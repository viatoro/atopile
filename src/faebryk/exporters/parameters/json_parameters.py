# This file is part of the faebryk project
# SPDX-License-Identifier: MIT

"""
JSON Variables/Parameters exporter for the VSCode extension.

Generates a rich JSON output with hierarchical module/parameter data
for the extension's VariablesPanel.
"""

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

import faebryk.core.node as fabll
import faebryk.library._F as F
from faebryk.core.solver import Solver

logger = logging.getLogger(__name__)


# Source of the variable value
VariableSource = Literal["user", "derived", "picked", "datasheet"]


@dataclass
class Variable:
    """A single variable/parameter with its spec and actual values."""

    name: str
    spec: str | None = None
    actual: str | None = None
    meetsSpec: bool | None = None
    source: VariableSource = "derived"


@dataclass
class VariableNode:
    """A node in the hierarchical variable tree."""

    name: str
    type: Literal["module", "interface", "component"]
    path: str  # atopile address (hierarchical from app root)
    typeName: str | None = None  # The type name (e.g., "I2C", "SPI", "Resistor")
    variables: list[Variable] = field(default_factory=list)
    children: list["VariableNode"] = field(default_factory=list)


@dataclass
class JSONVariablesOutput:
    """The full JSON variables output."""

    version: str = "1.0"
    build_id: str | None = None  # Build ID from server (links to build history)
    nodes: list[VariableNode] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def _get_node_type(module: fabll.Node) -> Literal["module", "interface", "component"]:
    """Determine the type of node for display."""
    # Check if it's pickable (component)
    if module.has_trait(F.Pickable.is_pickable):
        return "component"

    # Check if it has the is_interface trait
    if module.has_trait(fabll.is_interface):
        return "interface"

    # Check type name for interface patterns
    try:
        type_node = module.get_type_node()
        if type_node:
            # Handle both Node and BoundNodeReference
            if hasattr(type_node, "get_name"):
                type_name = type_node.get_name()
            elif hasattr(type_node, "name"):
                type_name = type_node.name
            else:
                type_name = str(type_node)

            if type_name:
                type_lower = type_name.lower()
                if "interface" in type_lower or type_name.endswith("Power"):
                    return "interface"
                # Common interface names
                if type_name in (
                    "I2C",
                    "SPI",
                    "UART",
                    "GPIO",
                    "ElectricPower",
                    "Electrical",
                ):
                    return "interface"
    except Exception:
        pass

    return "module"


def _get_source_type(module: fabll.Node) -> VariableSource:
    """Determine how the parameter value was set for this module."""
    # Check if there's a has_part_picked trait (means part was picked)
    if module.has_trait(F.Pickable.has_part_picked):
        return "picked"

    # Check if the module was parametrically picked
    if module.has_trait(F.Pickable.is_pickable_by_type):
        return "picked"

    # Check if explicitly specified by supplier ID
    if module.has_trait(F.Pickable.is_pickable_by_supplier_id):
        return "datasheet"

    return "derived"


def _extract_module_data(
    module: fabll.Node,
    solver: Solver,
    app_root: fabll.Node,
) -> tuple[
    str, str, str | None, Literal["module", "interface", "component"], list[Variable]
]:
    """
    Extract data from a single module.

    Returns: (name, path, typeName, nodeType, variables)
    """
    # Get the instance name (last component of hierarchy)
    name = module.get_name(accept_no_parent=True)

    # Get full hierarchical path from root using get_full_name
    # This gives paths like "app.ad1938_driver.i2c_ins[0]"
    full_path = module.get_full_name(types=False, include_root=False)

    # Make path relative to app root by removing the app root prefix
    app_prefix = app_root.get_full_name(types=False, include_root=False)
    if full_path.startswith(app_prefix + "."):
        path = full_path[len(app_prefix) + 1 :]
    elif full_path == app_prefix:
        # This is the app root itself
        path = name
    else:
        path = full_path

    # Get the type name (e.g., "I2C", "SPI", "Resistor", "AD1938_driver")
    type_name = module.get_type_name()
    # Clean up type name - remove file prefix if present
    if type_name and "::" in type_name:
        type_name = type_name.split("::")[-1]

    node_type = _get_node_type(module)
    module_source = _get_source_type(module)
    part_trait = module.try_get_trait(F.Pickable.has_part_picked)

    # Extract parameters
    variables: list[Variable] = []
    param_nodes = module.get_children(
        direct_only=True,
        types=fabll.Node,
        include_root=True,
        required_trait=F.Parameters.is_parameter,
    )

    for param in param_nodes:
        try:
            param_name = param.get_full_name().split(".")[-1]

            # Skip anonymous parameters
            if param_name.startswith("anon"):
                continue

            param_trait = param.get_trait(F.Parameters.is_parameter)

            # Use try_extract_superset (returns None for unconstrained)
            # instead of extract_superset (creates expensive domain_set nodes)
            value_set = solver.try_extract_superset(param_trait)
            if value_set is None:
                continue
            spec_value = value_set.pretty_str()

            actual_value = None
            if part_trait:
                try:
                    if attr_lit := part_trait.get_attribute(param_name):
                        actual_value = attr_lit.pretty_str()
                except Exception:
                    pass

            if module_source in ("picked", "datasheet"):
                variables.append(
                    Variable(
                        name=param_name,
                        spec=spec_value,
                        actual=actual_value,
                        meetsSpec=None,
                        source=module_source,
                    )
                )
            else:
                variables.append(
                    Variable(
                        name=param_name,
                        spec=spec_value,
                        source="derived",
                    )
                )

        except Exception as e:
            logger.debug(f"Could not extract parameter {param}: {e}")
            continue

    return name, path, type_name, node_type, variables


def _parse_module_locator(locator: str) -> list[str]:
    """
    Parse a module locator into its hierarchical path components.

    Handles formats like:
    - "adi-adxl375.ato::ADI_ADXL375.decoupling_capacitors[0]|Capacitor"
        -> ["ADI_ADXL375", "decoupling_capacitors", "[0]"]
    - "power" -> ["power"]
    - "i2c" -> ["i2c"]
    - "App.i2c_ins[0].scl" -> ["App", "i2c_ins", "[0]", "scl"]

    Array indices are split into separate path components so that
    array elements are nested under their container.
    """
    import re

    # Remove type suffix if present (after |)
    if "|" in locator:
        locator = locator.split("|")[0]

    # Extract the path after :: if present
    if "::" in locator:
        locator = locator.split("::")[1]

    # Split by . to get hierarchy
    parts = locator.split(".")

    # Further split array indices into separate components
    # e.g., "i2c_ins[0]" -> ["i2c_ins", "[0]"]
    expanded_parts = []
    for part in parts:
        # Check if part contains array indices
        if "[" in part and "]" in part:
            # Split "name[0][1]" into ["name", "[0]", "[1]"]
            match = re.match(r"^([^\[]+)((?:\[\d+\])+)$", part)
            if match:
                base_name = match.group(1)
                indices_str = match.group(2)
                # Extract all indices
                indices = re.findall(r"\[\d+\]", indices_str)
                expanded_parts.append(base_name)
                expanded_parts.extend(indices)
            else:
                # Fallback - just add the part as-is
                expanded_parts.append(part)
        else:
            expanded_parts.append(part)

    return expanded_parts


def _build_tree(flat_nodes: dict[str, VariableNode]) -> list[VariableNode]:
    """
    Build a tree structure from flat nodes based on path hierarchy.

    Creates intermediate placeholder nodes when a container (like i2c_ins)
    doesn't have its own entry but its children (like i2c_ins[0]) do.
    """
    # Parse all paths into their hierarchical components
    parsed_paths: dict[str, list[str]] = {}
    for path in flat_nodes:
        parsed_paths[path] = _parse_module_locator(path)

    # Collect all unique hierarchy prefixes that we need nodes for
    # This ensures containers exist even if they don't have parameters
    all_keys: set[str] = set()
    for parts in parsed_paths.values():
        for i in range(1, len(parts) + 1):
            all_keys.add(".".join(parts[:i]))

    # Create a mapping from hierarchy key to original path (if it exists)
    hierarchy_key_to_path: dict[str, str] = {}
    for path, parts in parsed_paths.items():
        key = ".".join(parts)
        hierarchy_key_to_path[key] = path

    # Sort keys by length (shorter = parent) then alphabetically
    sorted_keys = sorted(all_keys, key=lambda x: (len(x.split(".")), x))

    root_nodes: list[VariableNode] = []
    key_to_node: dict[str, VariableNode] = {}

    for key in sorted_keys:
        parts = key.split(".")

        # Check if we have an actual node for this key
        if key in hierarchy_key_to_path:
            path = hierarchy_key_to_path[key]
            node = flat_nodes[path]
        else:
            # Create a placeholder node for this intermediate container
            # The name is the last part of the key
            name = parts[-1]
            # Determine type: if name looks like [N], it's likely an array element
            if name.startswith("[") and name.endswith("]"):
                node_type = "module"  # Array elements inherit parent type conceptually
            else:
                node_type = "interface"  # Containers are typically interfaces
            node = VariableNode(
                name=name,
                type=node_type,
                path=key,  # Use hierarchy key as path for placeholders
                typeName=None,  # No type info for placeholders
                variables=[],
                children=[],
            )

        key_to_node[key] = node

        # Try to find a parent by removing the last component
        if len(parts) > 1:
            parent_key = ".".join(parts[:-1])
            if parent_key in key_to_node:
                key_to_node[parent_key].children.append(node)
            else:
                # Shouldn't happen since we process in order, but fallback to root
                root_nodes.append(node)
        else:
            root_nodes.append(node)

    return root_nodes


def make_json_variables(
    app: fabll.Node,
    solver: Solver,
    build_id: str | None = None,
) -> JSONVariablesOutput:
    """
    Generate a JSON variables report from the application module tree.

    Walks the module hierarchy and extracts parameters with their
    spec values, actual values (from picked parts), units, and sources.

    Args:
        app: The application root node
        solver: The solver used for parameter resolution
        build_id: Build ID from server (links to build history)
    """
    # Get all modules
    modules = list(
        app.get_children(
            direct_only=False,
            types=fabll.Node,
            required_trait=fabll.is_module,
            include_root=True,
        )
    )

    # Also get interfaces (which can have parameters like voltage, current, etc.)
    interfaces = list(
        app.get_children(
            direct_only=False,
            types=fabll.Node,
            required_trait=fabll.is_interface,
            include_root=False,
        )
    )

    # Combine and deduplicate (some nodes might have both traits)
    all_nodes = {id(n): n for n in modules + interfaces}

    logger.info(
        f"JSON Variables: Found {len(modules)} modules, {len(interfaces)} interfaces"
    )

    # Extract data from each node (module or interface)
    # We need ALL nodes for tree building, even those without parameters
    flat_nodes: dict[str, VariableNode] = {}
    nodes_with_params: set[str] = set()
    total_params = 0

    for module in all_nodes.values():
        try:
            name, path, type_name, node_type, variables = _extract_module_data(
                module, solver, app
            )
            total_params += len(variables)

            # Store all nodes for tree building
            flat_nodes[path] = VariableNode(
                name=name,
                type=node_type,
                path=path,
                typeName=type_name,
                variables=variables,
                children=[],
            )

            if variables:
                nodes_with_params.add(path)
        except Exception as e:
            logger.debug(f"Could not process module {module}: {e}")
            continue

    logger.info(
        f"JSON Variables: {len(flat_nodes)} total nodes, "
        f"{len(nodes_with_params)} with params, {total_params} total params"
    )

    # Build tree structure
    root_nodes = _build_tree(flat_nodes)

    # Prune nodes without parameters (unless they have children with params)
    def prune_empty_nodes(nodes: list[VariableNode]) -> list[VariableNode]:
        result = []
        for node in nodes:
            # Recursively prune children first
            node.children = prune_empty_nodes(node.children)

            # Keep node if it has parameters or has children
            if node.variables or node.children:
                result.append(node)

        return result

    pruned_roots = prune_empty_nodes(root_nodes)

    logger.info(f"JSON Variables: Built tree with {len(pruned_roots)} root nodes")

    return JSONVariablesOutput(build_id=build_id, nodes=pruned_roots)


def write_variables_to_file(
    app: fabll.Node,
    solver: Solver,
    path: Path,
    build_id: str | None = None,
) -> None:
    """Write a variables report as JSON.

    Args:
        app: The application root node
        solver: The solver used for parameter resolution
        path: Output file path (.json)
        build_id: Build ID from server (links to build history)
    """
    if not path.parent.exists():
        os.makedirs(path.parent)

    output = make_json_variables(app, solver, build_id=build_id)
    output_path = path.with_suffix(".json")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(output.to_json())

    logger.info(f"Wrote variables to {output_path}")
