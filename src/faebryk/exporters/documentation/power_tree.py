# This file is part of the faebryk project
# SPDX-License-Identifier: MIT

import logging
from dataclasses import dataclass
from enum import StrEnum, auto
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
    write_json,
)

logger = logging.getLogger(__name__)


TREE_METADATA = {
    "title": "Power Tree",
}


def _param_value(param_node: fabll.Node, solver: Solver) -> str:
    """Extract a human-readable display string for a solved parameter."""
    try:
        param_trait = param_node.get_trait(F.Parameters.is_parameter)
        lit = solver.extract_superset(param_trait)
        if lit is not None:
            s = lit.pretty_str()
            if s and "\u211d" not in s and s != "?":
                return s
    except Exception:
        pass
    try:
        param = param_node.try_cast(F.Parameters.NumericParameter)
        if param:
            direct = param.try_extract_superset()
            if direct is not None:
                s = direct.is_literal.get().pretty_str()
                if s and "\u211d" not in s and s != "?":
                    return s
    except Exception:
        pass
    return "?"


def export_power_tree(
    app: fabll.Node,
    solver: Solver,
    *,
    mermaid_path: Path,
) -> None:
    """Generate Mermaid power tree (placeholder, kept for compatibility)."""
    mermaid_path.parent.mkdir(parents=True, exist_ok=True)
    mermaid_path.write_text("```mermaid\ngraph TD\n```\n", encoding="utf-8")


def export_power_tree_json(
    app: fabll.Node,
    solver: Solver,
    *,
    json_path: Path,
) -> None:
    """
    Export the power tree as a hierarchical JSON showing power flow.

    The tree shows:
    - Source nodes (power origins like USB connectors)
    - Converter nodes (regulators/LDOs that bridge two rails)
    - Sink nodes (power consumers like MCUs, sensors)

    A converter has a source on one bus and a sink on another.
    The tree flows: source -> converter -> sinks.
    """
    all_power = list(F.ElectricPower.bind_typegraph(tg=app.tg).get_instances())

    if not all_power:
        write_json(
            {
                **TREE_METADATA,
                "nodes": [],
                "edges": [],
                "groups": [],
            },
            json_path,
        )
        return

    # Group ElectricPower interfaces by bus connectivity
    buses = fabll.is_interface.group_into_buses(all_power)

    # Track which buses are disconnected (singleton)
    disconnected_buses: set[int] = set()

    # Cast and filter members — include component-level power (depth <= 3)
    # but skip deeply nested internal wiring (e.g. cap.power at depth 4+)
    bus_typed: dict[int, list[F.ElectricPower]] = {}
    ep_to_bus: dict[int, int] = {}  # ep id -> bus index

    for bus_idx, bus_members in enumerate(buses.values()):
        is_disconnected = len(bus_members) < 2
        members = []
        for raw in bus_members:
            try:
                ep = F.ElectricPower.bind_instance(raw.instance)
                name = strip_root_hex(ep.get_full_name())
                if len(name.split(".")) <= 3:
                    members.append(ep)
                    ep_to_bus[id(ep)] = bus_idx
            except Exception:
                continue
        if members:
            bus_typed[bus_idx] = members
            if is_disconnected:
                disconnected_buses.add(bus_idx)

    # --- Types ---

    class ModuleType(StrEnum):
        SOURCE = auto()
        CONVERTER = auto()
        BIDIRECTIONAL_CONVERTER = auto()
        SINK = auto()
        DISCONNECTED = auto()

    # --- Per-EP property tagging and per-module grouping ---

    @dataclass
    class EPInfo:
        ep: F.ElectricPower
        bus_idx: int
        is_source: bool
        is_sink: bool
        is_disconnected: bool

    def get_parent_module(ep: F.ElectricPower) -> fabll.Node | None:
        parent = ep.get_parent()
        if not parent:
            return None
        return parent[0]

    def get_parent_module_id(ep: F.ElectricPower) -> str | None:
        parent_module = get_parent_module(ep)
        if parent_module is None:
            return None
        return strip_root_hex(parent_module.get_full_name())

    # Build EPInfo for every relevant EP and group by parent module
    eps_by_parent: dict[str, list[EPInfo]] = {}

    for members in bus_typed.values():
        for ep in members:
            is_src = ep.has_trait(F.is_source)
            is_snk = ep.has_trait(F.is_sink)
            if not is_src and not is_snk:
                continue  # skip rail aliases / intermediaries
            pid = get_parent_module_id(ep)
            if not pid:
                continue  # skip EPs without a parent module
            info = EPInfo(
                ep=ep,
                bus_idx=ep_to_bus[id(ep)],
                is_source=is_src,
                is_sink=is_snk,
                is_disconnected=ep_to_bus.get(id(ep)) in disconnected_buses,
            )
            eps_by_parent.setdefault(pid, []).append(info)

    # --- Module-level classification ---

    def classify_module(eps: list[EPInfo]) -> ModuleType | None:
        has_source = any(e.is_source for e in eps)
        has_sink = any(e.is_sink for e in eps)
        has_bidirectional = any(e.is_source and e.is_sink for e in eps)
        any_disconnected = any(e.is_disconnected for e in eps)

        if any_disconnected:
            return ModuleType.DISCONNECTED

        if has_bidirectional:
            return ModuleType.BIDIRECTIONAL_CONVERTER

        if has_source and has_sink:
            src_buses = {e.bus_idx for e in eps if e.is_source}
            snk_buses = {e.bus_idx for e in eps if e.is_sink}
            if src_buses != snk_buses:
                return ModuleType.CONVERTER

        if has_source:
            return ModuleType.SOURCE
        if has_sink:
            return ModuleType.SINK
        return None

    # --- Single node generation loop ---

    json_nodes: list[dict] = []
    json_edges: list[dict] = []
    owners_by_node_id: dict[str, fabll.Node] = {}
    node_id_map: dict[int, str] = {}  # python id(ep) -> json node id
    counter = 0

    def make_node_id() -> str:
        nonlocal counter
        nid = f"n{counter}"
        counter += 1
        return nid

    for pid, ep_infos in eps_by_parent.items():
        module_type = classify_module(ep_infos)
        if module_type is None:
            continue

        nid = make_node_id()

        # Map ALL module EPs to this single node
        for e in ep_infos:
            node_id_map[id(e.ep)] = nid

        parent_module = get_parent_module(ep_infos[0].ep)
        block_type = get_node_type_name(parent_module or ep_infos[0].ep)

        if module_type is ModuleType.CONVERTER:
            sink_eps = [e for e in ep_infos if e.is_sink]
            source_eps = [e for e in ep_infos if e.is_source]
            input_ep = sink_eps[0].ep if sink_eps else None
            output_ep = source_eps[0].ep if source_eps else None
            voltage_in = (
                _param_value(input_ep.voltage.get(), solver) if input_ep else "?"
            )
            voltage_out = (
                _param_value(output_ep.voltage.get(), solver) if output_ep else "?"
            )
            max_current_out = (
                _param_value(output_ep.max_current.get(), solver) if output_ep else "?"
            )
            meta = compact_meta(
                {
                    "block_type": block_type,
                    "voltage_in": voltage_in,
                    "voltage_out": voltage_out,
                    "max_current": max_current_out,
                },
                preview_keys=("voltage_in", "voltage_out", "max_current"),
                labels={
                    "block_type": "Block Type",
                    "voltage_in": "Input Voltage",
                    "voltage_out": "Output Voltage",
                    "max_current": "Max Output Current",
                },
            )
            label = pid

        elif module_type is ModuleType.BIDIRECTIONAL_CONVERTER:
            bidir_ep = next(
                (e.ep for e in ep_infos if e.is_source and e.is_sink),
                ep_infos[0].ep,
            )
            voltage = _param_value(bidir_ep.voltage.get(), solver)
            max_current = _param_value(bidir_ep.max_current.get(), solver)
            meta = compact_meta(
                {
                    "block_type": block_type,
                    "voltage": voltage,
                    "max_current": max_current,
                },
                preview_keys=("voltage", "max_current"),
                labels={
                    "block_type": "Block Type",
                    "voltage": "Voltage",
                    "max_current": "Max Current",
                },
            )
            label = pid

        elif module_type is ModuleType.SOURCE:
            ep = ep_infos[0].ep
            voltage = _param_value(ep.voltage.get(), solver)
            max_current = _param_value(ep.max_current.get(), solver)
            meta = compact_meta(
                {
                    "block_type": block_type,
                    "voltage": voltage,
                    "max_current": max_current,
                },
                preview_keys=("voltage", "max_current"),
                labels={
                    "block_type": "Block Type",
                    "voltage": "Voltage",
                    "max_current": "Max Output Current",
                },
            )
            label = strip_root_hex(ep.get_full_name())

        elif module_type is ModuleType.SINK:
            ep = ep_infos[0].ep
            max_current = _param_value(ep.max_current.get(), solver)
            max_power = _param_value(ep.max_power.get(), solver)
            meta = compact_meta(
                {
                    "block_type": block_type,
                    "max_current": max_current,
                    "max_power": max_power,
                },
                preview_keys=("max_current", "max_power"),
                labels={
                    "block_type": "Block Type",
                    "max_current": "Max Input Current",
                    "max_power": "Max Input Power",
                },
            )
            label = strip_root_hex(ep_infos[0].ep.get_full_name())

        else:  # disconnected
            sources = [e for e in ep_infos if e.is_source]
            sinks = [e for e in ep_infos if e.is_sink]
            if sources and sinks:
                # Disconnected converter — show voltage in/out
                input_ep = sinks[0].ep
                output_ep = sources[0].ep
                meta = compact_meta(
                    {
                        "block_type": block_type,
                        "voltage_in": _param_value(input_ep.voltage.get(), solver),
                        "voltage_out": _param_value(output_ep.voltage.get(), solver),
                        "max_current": _param_value(
                            output_ep.max_current.get(), solver
                        ),
                    },
                    preview_keys=("voltage_in", "voltage_out", "max_current"),
                    labels={
                        "block_type": "Block Type",
                        "voltage_in": "Input Voltage",
                        "voltage_out": "Output Voltage",
                        "max_current": "Max Output Current",
                    },
                )
                label = pid
            else:
                ep = ep_infos[0].ep
                meta = compact_meta(
                    {
                        "block_type": block_type,
                        "voltage": _param_value(ep.voltage.get(), solver),
                        "max_current": _param_value(ep.max_current.get(), solver),
                    },
                    preview_keys=("voltage", "max_current"),
                    labels={
                        "block_type": "Block Type",
                        "voltage": "Voltage",
                        "max_current": "Max Current",
                    },
                )
                label = strip_root_hex(ep.get_full_name())

        json_nodes.append(
            compact_dict(
                {
                    "id": nid,
                    "type": module_type,
                    "label": label,
                    "meta": meta,
                }
            )
        )
        if parent_module is not None:
            owners_by_node_id[nid] = parent_module

    assign_tree_group_metadata(app, json_nodes, owners_by_node_id)

    # Build edges based on bus membership
    # For each bus, find the source(s) and connect to sinks + converter inputs
    for members in bus_typed.values():
        bus_source_nids = set()
        bus_sink_nids = set()

        for ep in members:
            ep_nid = node_id_map.get(id(ep))
            if ep_nid is None:
                continue

            if ep.has_trait(F.is_source):
                bus_source_nids.add(ep_nid)
            if ep.has_trait(F.is_sink):
                bus_sink_nids.add(ep_nid)

        # Connect sources to sinks on the same bus
        for src_nid in bus_source_nids:
            for snk_nid in bus_sink_nids:
                if src_nid != snk_nid:
                    json_edges.append(
                        {
                            "id": f"power:{src_nid}->{snk_nid}",
                            "source": src_nid,
                            "target": snk_nid,
                            "kind": "edge",
                        }
                    )

    write_json(
        {
            **TREE_METADATA,
            "nodes": json_nodes,
            "edges": json_edges,
            "groups": build_tree_groups(json_nodes),
        },
        json_path,
    )
    logger.info("Wrote power tree JSON to %s", json_path)
