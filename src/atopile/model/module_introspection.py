"""
Module introspection using TypeGraph.

This module provides functions to introspect .ato modules and extract their
hierarchical structure (children, parameters, interfaces) using the TypeGraph,
similar to how the standard library is introspected.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from atopile.data_models import ModuleChild, ModuleDefinition
from atopile.lsp.lsp_server import clear_type_caches_for_file

if TYPE_CHECKING:
    import faebryk.core.faebrykpy as fbrk
    import faebryk.core.graph as graph

log = logging.getLogger(__name__)


def _get_item_type(
    tg: "fbrk.TypeGraph",
    type_node: "graph.BoundNode | None",
    type_name: str,
) -> Literal["interface", "module", "component", "parameter", "trait"] | None:
    """Determine the item type from the TypeGraph. Returns None for types we don't want to show."""  # noqa: E501
    import faebryk.core.node as fabll
    import faebryk.library._F as F
    from faebryk.library.Pickable import (
        is_pickable_by_part_number,
        is_pickable_by_supplier_id,
        is_pickable_by_type,
    )

    if type_node is not None:
        try:
            # Check trait FIRST using ImplementsTrait
            if fabll.TypeNodeBoundTG.has_instance_of_type_has_trait(
                type_node, fabll.ImplementsTrait
            ):
                return "trait"

            # Check if module is pickable (will appear in BOM) - mark as "component"
            # This includes Resistor, Capacitor, Inductor, and parts with LCSC IDs
            is_pickable = (
                fabll.TypeNodeBoundTG.has_instance_of_type_has_trait(
                    type_node, is_pickable_by_type
                )
                or fabll.TypeNodeBoundTG.has_instance_of_type_has_trait(
                    type_node, is_pickable_by_supplier_id
                )
                or fabll.TypeNodeBoundTG.has_instance_of_type_has_trait(
                    type_node, is_pickable_by_part_number
                )
            )
            if is_pickable:
                return "component"

            # Check module BEFORE interface (modules are also interfaces)
            if fabll.TypeNodeBoundTG.has_instance_of_type_has_trait(
                type_node, fabll.is_module
            ):
                return "module"
            if fabll.TypeNodeBoundTG.has_instance_of_type_has_trait(
                type_node, F.Parameters.is_parameter
            ):
                return "parameter"
            if fabll.TypeNodeBoundTG.has_instance_of_type_has_trait(
                type_node, fabll.is_interface
            ):
                return "interface"
        except Exception as exc:
            log.debug("Failed to determine item type for %s: %s", type_name, exc)

    # Return None for unknown types - we only want modules, interfaces, parameters
    return None


def _is_user_facing_child(attr_name: str) -> bool:
    """Determine if an attribute name is user-facing (vs internal)."""
    # Skip private attributes
    if attr_name.startswith("_"):
        return False
    # Skip anonymous fields
    if attr_name.startswith("anon"):
        return False
    # Skip trait-like attribute names (is_*, has_*, can_*, implements_*)
    # These are traits attached to the module, not user-facing children
    if attr_name.startswith(("is_", "has_", "can_", "implements_")):
        return False
    # Skip known internal field names (these are implementation details)
    internal_names = {
        "design_check",
        "net_names",
        "bus_parameters",
        "vcc",
        "gnd",  # Deprecated aliases for hv/lv
        "literals",  # Internal solver field
    }
    if attr_name in internal_names:
        return False

    return True


# Pattern to match array indices like "name[0]", "name[123]"
_ARRAY_PATTERN = re.compile(r"^(.+)\[(\d+)\]$")


def _group_array_children(children: list[ModuleChild]) -> list[ModuleChild]:
    """
    Group array elements like i2c[0], i2c[1] under a parent i2c node.

    Takes flat children list and returns grouped list where array elements
    are nested under their parent array node.
    """
    # Separate array elements from regular children
    array_groups: dict[str, list[tuple[int, ModuleChild]]] = {}
    regular_children: list[ModuleChild] = []

    for child in children:
        match = _ARRAY_PATTERN.match(child.name)
        if match:
            base_name = match.group(1)
            index = int(match.group(2))
            if base_name not in array_groups:
                array_groups[base_name] = []
            array_groups[base_name].append((index, child))
        else:
            regular_children.append(child)

    # Create grouped array nodes
    result: list[ModuleChild] = regular_children.copy()

    for base_name, indexed_children in array_groups.items():
        # Sort by index
        indexed_children.sort(key=lambda x: x[0])

        # Get type info from first element
        first_child = indexed_children[0][1]
        element_type = first_child.type_name

        # Create array children with simplified names like [0], [1]
        array_elements = [
            ModuleChild(
                name=f"[{idx}]",
                type_name=child.type_name,
                item_type=child.item_type,
                children=child.children,
                spec=child.spec,  # Preserve spec for array elements
                src_loc=child.src_loc,
            )
            for idx, child in indexed_children
        ]

        # Create parent array node
        array_node = ModuleChild(
            name=base_name,
            type_name=f"{element_type}[{len(indexed_children)}]",
            item_type=first_child.item_type,
            children=array_elements,
            spec=first_child.spec,  # Use first element's spec for parent
            src_loc=first_child.src_loc,
        )
        result.append(array_node)

    return result


def _extract_children_from_typegraph(
    tg: "fbrk.TypeGraph",
    type_node: "graph.BoundNode",
    project_root: Path,
    depth: int = 0,
    max_depth: int = 2,
) -> list[ModuleChild]:
    """Extract children from the TypeGraph for a given type node."""
    import faebryk.core.faebrykpy as fbrk

    children: list[ModuleChild] = []

    if depth >= max_depth:
        return children

    try:
        make_children = tg.collect_make_children(type_node=type_node)
    except Exception as exc:
        log.debug("Failed to collect make children: %s", exc)
        return children

    for identifier, make_child in make_children:
        if not identifier:
            continue
        # Check base name for arrays (e.g., "i2c" from "i2c[0]")
        base_name = identifier
        match = _ARRAY_PATTERN.match(identifier)
        if match:
            base_name = match.group(1)
        if not _is_user_facing_child(base_name):
            continue

        try:
            type_ref = tg.get_make_child_type_reference(make_child=make_child)
            resolved_type = fbrk.Linker.get_resolved_type(type_reference=type_ref)
            if resolved_type is not None:
                type_name = fbrk.TypeGraph.get_type_name(type_node=resolved_type)
            else:
                type_name = fbrk.TypeGraph.get_type_reference_identifier(
                    type_reference=type_ref
                )
        except Exception as exc:
            log.debug("Failed to resolve type for %s: %s", identifier, exc)
            type_name = "Unknown"
            resolved_type = None

        # Skip internal types by name
        type_name_lower = type_name.lower()
        if "pointer" in type_name_lower or "sequence" in type_name_lower:
            continue

        # Determine item type from the graph
        item_type = _get_item_type(tg, resolved_type, type_name)

        # Skip traits - but keep unresolved types as modules
        if item_type == "trait":
            continue

        # If type couldn't be determined (unresolved import), treat as module
        if item_type is None:
            # Check if it looks like a module/component based on naming convention
            # (types from imports are usually PascalCase modules/components)
            if resolved_type is None and type_name and type_name[0].isupper():
                item_type = "module"
            else:
                continue

        # Extract parameter spec if this is a parameter
        # TODO: Implement spec extraction from TypeGraph operand/constraint nodes
        spec: str | None = None

        # Extract source location for this child
        src_loc: str | None = None
        try:
            from atopile.compiler.ast_visitor import ASTVisitor

            source_chunk = ASTVisitor.get_source_chunk(make_child)
            if source_chunk is not None:
                src_loc = source_chunk.loc.get().get_full_location()
                # Relativize path if within project
                prefix = str(project_root) + "/"
                if src_loc.startswith(prefix):
                    src_loc = src_loc[len(prefix) :]
        except Exception:
            pass  # Source info is best-effort

        # Recursively extract nested children
        nested_children: list[ModuleChild] = []
        if resolved_type is not None and depth < max_depth:
            nested_children = _extract_children_from_typegraph(
                tg=tg,
                type_node=resolved_type,
                project_root=project_root,
                depth=depth + 1,
                max_depth=max_depth,
            )

        children.append(
            ModuleChild(
                name=identifier,
                type_name=type_name,
                item_type=item_type,
                children=nested_children,
                spec=spec,
                src_loc=src_loc,
            )
        )

    # Group array elements under parent nodes
    return _group_array_children(children)


def introspect_module(
    project_root: Path,
    entry_point: str,
    max_depth: int = 5,
) -> list[ModuleChild] | None:
    """
    Introspect a module and extract its children using TypeGraph.

    Args:
        project_root: Path to the project root (containing ato.yaml)
        entry_point: Entry point in format "file.ato:ModuleName"
        max_depth: Maximum depth for nested children (default: 5)

    Returns:
        List of ModuleChild objects, or None if introspection fails.
    """
    import faebryk.core.faebrykpy as fbrk
    import faebryk.core.graph as graph
    from atopile.compiler.build import build_file

    # Parse entry point
    if ":" not in entry_point:
        log.warning("Invalid entry point format: %s", entry_point)
        return None

    file_part, module_name = entry_point.rsplit(":", 1)
    file_path = project_root / file_part

    if not file_path.exists():
        log.warning("File not found: %s", file_path)
        return None

    clear_type_caches_for_file(file_path, file_part)

    try:
        # Create TypeGraph
        g = graph.GraphView.create()
        tg = fbrk.TypeGraph.create(g=g)

        # Build the file
        result = build_file(
            g=g,
            tg=tg,
            import_path=file_part,
            path=file_path,
        )

        # Find the type node for the requested module
        type_node = result.state.type_roots.get(module_name)
        if type_node is None:
            log.warning("Module %s not found in %s", module_name, file_path)
            return None

        # Try to link imports for multi-file projects
        try:
            from atopile.compiler.build import Linker, StdlibRegistry
            from atopile.config import config

            # Apply config for the project
            config.apply_options(entry=None, working_dir=project_root)

            stdlib = StdlibRegistry(tg)
            linker = Linker(
                config_obj=config,
                stdlib=stdlib,
                tg=tg,
            )
            # Use _link_recursive to avoid raising on unresolved refs
            # (packages may not be installed)
            linker._link_recursive(g, result.state)
        except Exception as link_exc:
            # Linking is optional - continue without it
            log.debug("Import linking skipped: %s", link_exc)

        # Extract children
        return _extract_children_from_typegraph(
            tg, type_node, project_root=project_root, max_depth=max_depth
        )

    except Exception as exc:
        log.warning("Failed to introspect module %s: %s", entry_point, exc)
        return None


_INTROSPECT_TIMEOUT = 60  # seconds


def introspect_module_definition(
    project_root: Path,
    module_def: ModuleDefinition,
    max_depth: int = 5,
) -> ModuleDefinition:
    """Introspect a module in a fresh subprocess, killing any previous one."""
    prev = introspect_module_definition._proc
    if prev is not None and prev.poll() is None:
        prev.kill()
        prev.wait()

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "atopile.model.module_introspection",
            "--project-root",
            str(project_root),
            "--entry-point",
            module_def.entry,
            "--max-depth",
            str(max_depth),
        ],
        stdout=subprocess.PIPE,
        cwd=str(project_root),
    )
    introspect_module_definition._proc = proc
    stdout, _ = proc.communicate(timeout=_INTROSPECT_TIMEOUT)
    introspect_module_definition._proc = None

    children_data = json.loads(stdout)
    if children_data is None:
        raise RuntimeError(f"Failed to introspect module {module_def.entry}")

    return module_def.model_copy(
        update={"children": [ModuleChild.model_validate(c) for c in children_data]}
    )


introspect_module_definition._proc = None  # type: ignore[attr-defined]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--entry-point", required=True)
    parser.add_argument("--max-depth", type=int, default=5)
    args = parser.parse_args()

    children = introspect_module(
        Path(args.project_root), args.entry_point, args.max_depth
    )
    json.dump(
        [c.model_dump() for c in children] if children is not None else None, sys.stdout
    )
