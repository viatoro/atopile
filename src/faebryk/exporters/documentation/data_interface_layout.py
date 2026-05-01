# This file is part of the faebryk project
# SPDX-License-Identifier: MIT

import json
import logging
from collections import defaultdict
from pathlib import Path

import faebryk.core.node as fabll
import faebryk.library._F as F
from faebryk.core.solver import Solver
from faebryk.exporters.utils import (
    assign_tree_group_metadata,
    build_tree_groups,
    compact_dict,
    compact_meta,
    get_node_type_name,
    strip_root_hex,
)
from faebryk.library.DataInterface import has_data_interface_role, is_data_interface

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_unresolved(value: str) -> bool:
    """Check if a parameter value is unresolved (contains ℝ or is '?')."""
    return not value or value == "?" or "\u211d" in value


def _try_as_hex(pretty: str) -> str:
    """Try to format a numeric value string as hex, return original on failure."""
    try:
        return hex(int(float(pretty.strip("{}"))))
    except ValueError, TypeError:
        return pretty


def _extract_param(
    param_trait: F.Parameters.is_parameter,
    solver: Solver,
    *,
    as_hex: bool = False,
) -> str | None:
    """Extract a parameter value as a pretty string."""
    # Strategy 1: solver (resolves constraints like assert addr is 0x48)
    try:
        lit = solver.extract_superset(param_trait)
        pretty = lit.pretty_str()
        if not _is_unresolved(pretty):
            if as_hex and lit.switch_cast().is_singleton():
                return _try_as_hex(pretty)
            return pretty
    except Exception:
        pass

    # Strategy 2: direct graph extraction
    try:
        lit = param_trait.switch_cast().try_extract_superset()
        if lit is not None:
            pretty = lit.pretty_str()
            if not _is_unresolved(pretty):
                if as_hex and lit.is_singleton():
                    return _try_as_hex(pretty)
                return pretty
    except Exception:
        pass

    return None


def _collect_interface_params(
    raw_member: fabll.Node,
    solver: Solver,
) -> tuple[dict[str, str | None], dict[str, str | None]]:
    """
    Scan an interface's children for parameters.
    Returns (member_params, bus_params) where bus_params are marked with
    is_alias_bus_parameter.
    """
    member_params: dict[str, str | None] = {}
    bus_params: dict[str, str | None] = {}

    children = raw_member.get_children(
        direct_only=True, types=fabll.Node, include_root=False
    )
    named_children = fabll.Node.with_names(children)

    for child_name, child in named_children.items():
        if child.has_trait(F.is_alias_bus_parameter):
            trait = child.get_trait(F.Parameters.is_parameter)
            bus_params[child_name] = _extract_param(trait, solver)
        elif child.has_trait(F.Parameters.is_parameter):
            trait = child.get_trait(F.Parameters.is_parameter)
            value = _extract_param(trait, solver, as_hex=(child_name == "address"))

            # Addressor special case: if parameter named "address" is
            # unresolved and parent has an Addressor child, mark it
            if value is None and child_name == "address":
                parent = raw_member.get_parent()
                if parent:
                    for sibling in parent[0].get_children(
                        direct_only=True,
                        types=fabll.Node,
                        include_root=False,
                    ):
                        sib_name = sibling.get_name(accept_no_parent=True) or ""
                        if "addressor" in sib_name.lower():
                            value = "via addressor"
                            break

            member_params[child_name] = value

    return member_params, bus_params


def _resolve_i2c_address(member: fabll.Node, solver: Solver) -> str | None:
    """Resolve I2C address using solver, returning hex string or None."""
    try:
        i2c = F.I2C.bind_instance(member.instance)
        addr_param = i2c.address.get().get_trait(F.Parameters.is_parameter)
        return _extract_param(addr_param, solver, as_hex=True)
    except Exception:
        return None


def _get_role_from_trait(member: fabll.Node) -> str | None:
    """Get role from has_data_interface_role trait, if present."""
    if member.has_trait(has_data_interface_role):
        roles = member.get_trait(has_data_interface_role).get_roles()
        if roles:
            return next(iter(roles)).name.lower()
    return None


def _get_member_role(
    raw_member: fabll.Node,
    bus_type: str,
    solver: Solver,
) -> tuple[str, dict[str, str | None]]:
    """
    Determine role and extra metadata for a bus member.
    Returns (role, extra_meta_dict).
    """
    role = _get_role_from_trait(raw_member)
    member_params, bus_params = _collect_interface_params(raw_member, solver)
    extra_meta = {**member_params, **bus_params}

    # I2C: resolve address via solver (typed access resolves constraints)
    if bus_type.upper() == "I2C":
        addr = _resolve_i2c_address(raw_member, solver)
        if addr is not None:
            extra_meta["address"] = addr

    return role or "node", extra_meta


def _write_json_list(data: list, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        temp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


# ---------------------------------------------------------------------------
# Main exporter
# ---------------------------------------------------------------------------


def export_data_interface_tree(
    app: fabll.Node,
    path: Path,
    solver: Solver,
) -> None:
    """
    Export all data interface trees as a single JSON file.

    Output is a JSON array of TreeViewerDocument objects, one per bus type.
    Each entry includes id/label fields for tab identification.
    """
    bus_groups = is_data_interface.get_data_interface_groups(app.g, app.tg)

    if not bus_groups:
        logger.info("No data interfaces found, writing empty tree file")
        _write_json_list([], path)
        return

    # Group bus groups by type, tracking which are disconnected (singleton)
    typed_groups: dict[str, list[tuple[set[fabll.Node], bool]]] = defaultdict(list)

    for bus_members in bus_groups:
        is_disconnected = len(bus_members) < 2

        # Determine bus type from the first member
        bus_type = None
        for member in bus_members:
            bus_type = member.get_type_name() or type(member).__name__
            if bus_type:
                break
        if bus_type is None:
            bus_type = "Unknown"

        typed_groups[bus_type].append((bus_members, is_disconnected))

    result: list[dict] = []

    for bus_type, groups in typed_groups.items():
        type_lower = bus_type.lower()
        type_id = type_lower.replace(" ", "_")

        json_nodes: list[dict] = []
        json_edges: list[dict] = []
        owners_by_node_id: dict[str, fabll.Node] = {}

        for bus_idx, (bus_members, is_disconnected) in enumerate(groups):
            bus_id = (
                f"{type_id}_disconnected_{bus_idx}"
                if is_disconnected
                else f"{type_id}_bus_{bus_idx}"
            )

            controllers: list[dict] = []
            targets: list[dict] = []
            nodes_in_bus: list[dict] = []

            # Pass 1: collect members and their raw roles
            collected: list[tuple[str, str, dict, fabll.Node | None]] = []
            known_roles: set[str] = set()

            for raw_member in bus_members:
                member_name_raw = raw_member.get_full_name()
                clean_name = strip_root_hex(member_name_raw)

                # Skip bus wires and standalone interfaces — only include
                # interfaces nested inside a component (depth 3+: App.Component.iface)
                if clean_name.count(".") < 2:
                    continue

                parent = raw_member.get_parent()
                parent_module_node = parent[0] if parent else None
                block_type = get_node_type_name(parent_module_node or raw_member)

                role, extra_meta = _get_member_role(raw_member, bus_type, solver)
                if is_disconnected:
                    role = "disconnected"

                # Build metadata
                meta_dict: dict[str, str | None] = {"block_type": block_type}
                meta_dict.update(extra_meta)

                if role not in ("node", "disconnected"):
                    known_roles.add(role)

                collected.append((role, clean_name, meta_dict, parent_module_node))

            # Pass 2: infer unknown roles and build node dicts
            for role, clean_name, meta_dict, parent_module_node in collected:
                if role == "node":
                    # Infer edge-direction from known roles in this bus:
                    # connected to a controller → act as target, and vice versa
                    if "controller" in known_roles:
                        edge_bucket = "target"
                    elif "target" in known_roles:
                        edge_bucket = "controller"
                    else:
                        edge_bucket = "node"
                    role = "unknown_role"
                else:
                    edge_bucket = role

                node_id = f"{bus_id}_{role}_{len(json_nodes)}"
                node_dict = compact_dict(
                    {
                        "id": node_id,
                        "type": role,
                        "label": clean_name,
                        "meta": compact_meta(meta_dict),
                    }
                )

                if edge_bucket == "controller":
                    controllers.append(node_dict)
                elif edge_bucket == "target":
                    targets.append(node_dict)
                else:
                    nodes_in_bus.append(node_dict)

                json_nodes.append(node_dict)

                if parent_module_node is not None:
                    owners_by_node_id[node_id] = parent_module_node

            # Directed edges: controller → target
            for ctrl in controllers:
                for tgt in targets:
                    json_edges.append(
                        {
                            "id": f"{type_id}:{ctrl['id']}->{tgt['id']}",
                            "source": ctrl["id"],
                            "target": tgt["id"],
                            "kind": "edge",
                        }
                    )

            # Peer edges: mesh connect all nodes_in_bus to each other
            for i, node_a in enumerate(nodes_in_bus):
                for node_b in nodes_in_bus[i + 1 :]:
                    json_edges.append(
                        {
                            "id": f"{type_id}:{node_a['id']}->{node_b['id']}",
                            "source": node_a["id"],
                            "target": node_b["id"],
                            "kind": "edge",
                        }
                    )

            # Connect peers to directed members
            all_directed = controllers + targets
            for peer in nodes_in_bus:
                for directed in all_directed:
                    json_edges.append(
                        {
                            "id": f"{type_id}:{peer['id']}->{directed['id']}",
                            "source": peer["id"],
                            "target": directed["id"],
                            "kind": "edge",
                        }
                    )

            # Multi-master: mesh connect controllers when no targets
            if len(controllers) > 1 and not targets:
                for i, ctrl_a in enumerate(controllers):
                    for ctrl_b in controllers[i + 1 :]:
                        json_edges.append(
                            {
                                "id": f"{type_id}:{ctrl_a['id']}->{ctrl_b['id']}",
                                "source": ctrl_a["id"],
                                "target": ctrl_b["id"],
                                "kind": "edge",
                            }
                        )

        if not json_nodes:
            continue

        assign_tree_group_metadata(app, json_nodes, owners_by_node_id)

        result.append(
            {
                "id": type_id,
                "label": bus_type.upper(),
                "title": f"{bus_type} Tree",
                "nodes": json_nodes,
                "edges": json_edges,
                "groups": build_tree_groups(json_nodes),
            }
        )

    _write_json_list(result, path)
    logger.info("Wrote data interface tree JSON to %s", path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDataInterfaceTreeExporter:
    def test_empty_graph(self, tmp_path):
        import faebryk.core.faebrykpy as fbrk
        import faebryk.core.graph as graph

        g = graph.GraphView.create()
        tg = fbrk.TypeGraph.create(g=g)

        class _EmptyApp(fabll.Node):
            pass

        app = _EmptyApp.bind_typegraph(tg=tg).create_instance(g=g)
        solver = Solver()

        out = tmp_path / "data_interface_tree.json"
        export_data_interface_tree(app, path=out, solver=solver)

        data = json.loads(out.read_text())
        assert data == []

    def test_connected_i2c(self, tmp_path):
        import faebryk.core.faebrykpy as fbrk
        import faebryk.core.graph as graph

        g = graph.GraphView.create()
        tg = fbrk.TypeGraph.create(g=g)

        class _I2CComponent(fabll.Node):
            i2c = F.I2C.MakeChild()

        class _I2CModule(fabll.Node):
            comp = _I2CComponent.MakeChild()

        class _I2CApp(fabll.Node):
            a = _I2CModule.MakeChild()
            b = _I2CModule.MakeChild()

        app = _I2CApp.bind_typegraph(tg=tg).create_instance(g=g)
        solver = Solver()

        a_i2c = app.a.get().comp.get().i2c.get()
        b_i2c = app.b.get().comp.get().i2c.get()
        a_i2c._is_interface.get().connect_to(b_i2c)

        out = tmp_path / "data_interface_tree.json"
        export_data_interface_tree(app, path=out, solver=solver)

        data = json.loads(out.read_text())
        assert len(data) >= 1

        tree = data[0]
        assert tree["id"] is not None
        assert tree["label"] is not None
        assert len(tree["nodes"]) >= 2
        assert "edges" in tree

    def test_single_interface_shown_as_disconnected(self, tmp_path):
        import faebryk.core.faebrykpy as fbrk
        import faebryk.core.graph as graph

        g = graph.GraphView.create()
        tg = fbrk.TypeGraph.create(g=g)

        class _I2CComponent(fabll.Node):
            i2c = F.I2C.MakeChild()

        class _I2CModule(fabll.Node):
            comp = _I2CComponent.MakeChild()

        class _SingleI2CApp(fabll.Node):
            a = _I2CModule.MakeChild()

        app = _SingleI2CApp.bind_typegraph(tg=tg).create_instance(g=g)
        solver = Solver()

        out = tmp_path / "data_interface_tree.json"
        export_data_interface_tree(app, path=out, solver=solver)

        data = json.loads(out.read_text())
        assert len(data) >= 1
        tree = data[0]
        assert len(tree["nodes"]) == 1
        assert tree["nodes"][0]["type"] == "disconnected"
        assert tree["edges"] == []
