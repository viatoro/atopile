# This file is part of the faebryk project
# SPDX-License-Identifier: MIT

"""
Pinout exporter: generates per-component JSON pinout reports for real board
components with an associated footprint.

Traverses the graph to extract pin names, signal types, interface assignments,
external connections, and net names.
"""

import json
import logging
from itertools import pairwise
from pathlib import Path

import faebryk.core.node as fabll
import faebryk.library._F as F
from atopile.data_models import PinoutComponent, PinoutLead, PinSignalType
from faebryk.libs.util import sanitize_filepath_part

logger = logging.getLogger(__name__)

_PIN_TYPES = (F.ElectricLogic, F.ElectricSignal, F.ElectricPower, F.Electrical)
_TYPE_PRIORITY = {t: i for i, t in enumerate(_PIN_TYPES)}
_PASSIVE_ENDPOINTS = frozenset(
    {
        F.Pickable.is_pickable_by_type.Endpoint.RESISTORS,
        F.Pickable.is_pickable_by_type.Endpoint.CAPACITORS,
        F.Pickable.is_pickable_by_type.Endpoint.INDUCTORS,
    }
)


def _get_scope_root(component: fabll.Node) -> fabll.Node:
    return (
        component.get_parent_of_type(fabll.Node, direct_only=True, include_root=False)
        or component
    )


def _in_scope(node: fabll.Node, scope: fabll.Node) -> bool:
    return node.is_same(scope) or node.is_descendant_of(scope)


def _populate_interface(
    pin: fabll.Node,
    component: fabll.Node,
) -> list[str]:
    interfaces: list[str] = []
    scope_root = _get_scope_root(component)

    for (_child_node, child_name), (parent_node, _parent_name) in pairwise(
        reversed(pin.get_hierarchy())
    ):
        if not _in_scope(parent_node, scope_root):
            break
        if parent_node.has_trait(fabll.is_interface):
            label = (
                parent_node.get_type_name()
                or parent_node.get_name()
                or parent_node.get_full_name()
            )
            interfaces.append(f"{label} ({child_name.upper()})")

    if pin.isinstance(F.ElectricLogic) or pin.isinstance(F.ElectricSignal):
        connected = pin.line.get().get_trait(fabll.is_interface).get_connected()

        for conn_node, _ in connected.items():
            if not _in_scope(conn_node, scope_root):
                continue

            connected_pin = conn_node.get_parent_of_type(
                fabll.Node, direct_only=True, include_root=False
            )
            if connected_pin is None:
                continue

            path_parts: list[str] = []

            for (_child_node, child_name), (parent_node, _parent_name) in pairwise(
                reversed(connected_pin.get_hierarchy())
            ):
                if not _in_scope(parent_node, scope_root):
                    break
                path_parts.append(child_name)
                if not parent_node.has_trait(fabll.is_interface):
                    continue
                role = (
                    path_parts[0]
                    if len(path_parts) <= 1
                    else ".".join(reversed(path_parts[:-1]))
                )
                label = (
                    parent_node.get_type_name()
                    or parent_node.get_name()
                    or parent_node.get_full_name()
                )
                interfaces.append(f"{label} ({role.upper()})")

    return list(dict.fromkeys(interfaces))


def _find_typed_pin_for_lead(
    scoped_members: set[fabll.Node],
    component: fabll.Node,
    scope_root: fabll.Node,
) -> fabll.Node | None:
    best: fabll.Node | None = None
    best_score: tuple[int, int, int] = (99, 99, 99)

    for conn_node in scoped_members:
        if conn_node.try_get_trait(F.Lead.is_lead) is not None:
            continue
        if not conn_node.isinstance(F.Electrical):
            continue

        # Walk up from connected node to find the closest specific type.
        # Record Electrical as fallback, but stop as soon as we find
        # ElectricLogic/ElectricSignal/ElectricPower (the closest wins).
        node: fabll.Node | None = conn_node
        fallback_node: fabll.Node | None = None
        fallback_score: tuple[int, int, int] = (99, 99, 99)
        found_specific = False
        while node is not None and _in_scope(node, scope_root):
            for pin_type in _PIN_TYPES:
                if not node.isinstance(pin_type):
                    continue
                scope_penalty = 0 if _in_scope(node, component) else 1
                rel_root = component if scope_penalty == 0 else scope_root
                depth = max(0, len(node.get_path_from_ancestor(rel_root)) - 1)
                score = (_TYPE_PRIORITY[pin_type], scope_penalty, depth)
                if pin_type is F.Electrical:
                    if fallback_node is None or score < fallback_score:
                        fallback_node = node.cast(pin_type)
                        fallback_score = score
                else:
                    if score < best_score:
                        best = node.cast(pin_type)
                        best_score = score
                    found_specific = True
                break
            if found_specific:
                break
            node = node.get_parent_of_type(
                fabll.Node, direct_only=True, include_root=False
            )
        if best is None and fallback_node is not None and fallback_score < best_score:
            best = fallback_node
            best_score = fallback_score

    return best


def _get_pin_designator(
    lead: fabll.Node,
    component: fabll.Node,
    scoped_members: set[fabll.Node],
) -> str:
    try:
        conn_node = next(
            conn_node
            for conn_node in scoped_members
            if not conn_node.is_same(lead) and _in_scope(conn_node, component)
        )
    except StopIteration as exc:
        raise ValueError(
            f"Could not resolve pin designator for {lead.get_full_name()}"
        ) from exc

    return conn_node.relative_address(component).split(".", 1)[0]


def extract_pinout_component(
    component: fabll.Node,
    app: fabll.Node,
    leads: list[fabll.Node] | None = None,
    lead_bus_members: dict[fabll.Node, set[fabll.Node]] | None = None,
    lead_net_names: dict[fabll.Node, str | None] | None = None,
) -> PinoutComponent:
    component_full = component.get_full_name()
    ato_address = component.relative_address(app)
    designator = component.get_trait(F.has_designator).get_designator()
    component_type = component.get_trait(fabll.is_module).get_module_locator()
    footprint_uuid = None
    if footprint_trait := component.try_get_trait(
        F.Footprints.has_associated_footprint
    ):
        graph_fp = footprint_trait.get_footprint()
        if kicad_footprint := graph_fp.try_get_trait(
            F.KiCadFootprints.has_associated_kicad_pcb_footprint
        ):
            footprint = kicad_footprint.get_footprint()
            footprint_uuid = str(footprint.uuid) if footprint.uuid is not None else None

    display_name = ato_address.removesuffix(".package")
    pinout_component = PinoutComponent(
        name=display_name,
        ato_address=ato_address,
        designator=designator,
        descriptor=f"{designator} — {display_name}",
        type_name=component_type,
        footprint_uuid=footprint_uuid,
    )
    if leads is None:
        leads = component.get_children(
            direct_only=False,
            types=fabll.Node,
            required_trait=F.Lead.is_lead,
        )
    if not leads:
        raise ValueError(f"{component_full} has no leads for pinout export")

    scope_root = _get_scope_root(component)

    # Pre-filter bus members to scope once per unique bus for this component.
    # Multiple leads often share the same bus object, so we cache by identity.
    _scoped_cache: dict[int, set[fabll.Node]] = {}

    for lead in leads:
        lead_t = lead.get_trait(F.Lead.is_lead)
        pads_t = lead_t.get_trait(F.Lead.has_associated_pads)
        pads = sorted(pads_t.get_pads(), key=lambda pad: (pad.pad_name, pad.pad_number))

        # Use precomputed bus membership when available, otherwise fall back
        # to per-lead get_connected
        if lead_bus_members is not None and lead in lead_bus_members:
            bus_members = lead_bus_members[lead]
        else:
            bus_members = set(
                lead.get_trait(fabll.is_interface)
                .get_connected(include_self=False)
                .keys()
            )

        # Filter to scope once per unique bus
        bus_id = id(bus_members)
        if bus_id not in _scoped_cache:
            _scoped_cache[bus_id] = {n for n in bus_members if _in_scope(n, scope_root)}
        scoped_members = _scoped_cache[bus_id]

        typed_pin = _find_typed_pin_for_lead(scoped_members, component, scope_root)
        if typed_pin is None:
            raise ValueError(
                "Could not resolve typed pin for "
                f"{component_full}.{lead.relative_address(component)}"
            )
        lead_designator = _get_pin_designator(lead, component, scoped_members)

        if typed_pin.isinstance(F.ElectricPower):
            signal_type = PinSignalType.POWER
        elif typed_pin.isinstance(F.ElectricLogic):
            signal_type = PinSignalType.LOGIC
        elif typed_pin.isinstance(F.ElectricSignal) or typed_pin.isinstance(
            F.Electrical
        ):
            signal_type = PinSignalType.SIGNAL
        else:
            raise ValueError(
                f"Unsupported pinout signal type for {typed_pin.get_full_name()}"
            )
        interfaces = _populate_interface(typed_pin, component)

        is_connected = any(
            conn_node.try_get_trait(F.Lead.is_lead) is not None
            and not conn_node.is_same(lead)
            and not conn_node.is_descendant_of(component)
            for conn_node in scoped_members
        )

        if lead_net_names is not None and lead in lead_net_names:
            net_name = lead_net_names[lead]
        else:
            net_name = None

        pinout_component.leads.append(
            PinoutLead(
                lead_designator=lead_designator,
                pad_numbers=[pad.pad_number for pad in pads if pad.pad_number],
                net_name=net_name,
                signal_type=signal_type,
                interfaces=interfaces,
                is_connected=is_connected,
            )
        )

    def _lead_sort_key(lead: PinoutLead) -> tuple[int, str, str]:
        if lead.pad_numbers:
            return (0, lead.pad_numbers[0], lead.lead_designator)
        return (1, "", lead.lead_designator)

    pinout_component.leads.sort(key=_lead_sort_key)
    unconnected_count = sum(
        1 for lead in pinout_component.leads if not lead.is_connected
    )
    if unconnected_count > 0:
        pinout_component.warnings.append(f"{unconnected_count} unconnected lead(s)")

    return pinout_component


def _is_passive_component(component: fabll.Node) -> bool:
    pickable = component.try_get_trait(F.Pickable.is_pickable_by_type)
    if pickable is None:
        return False
    try:
        endpoint = F.Pickable.is_pickable_by_type.Endpoint(pickable.endpoint)
    except Exception:
        return False
    return endpoint in _PASSIVE_ENDPOINTS


def export_pinout_json(
    app: fabll.Node,
    output_dir: Path,
) -> list[PinoutComponent]:
    components_by_name: dict[str, fabll.Node] = {}
    for footprint_t in fabll.Traits.get_implementors(
        F.Footprints.has_associated_footprint.bind_typegraph(tg=app.tg), g=app.g
    ):
        component = fabll.Traits.bind(footprint_t).get_obj_raw()
        if _is_passive_component(component):
            continue
        components_by_name.setdefault(component.get_full_name(), component)

    if not components_by_name:
        logger.info("No components with has_associated_footprint trait found")
        return []

    components_sorted = sorted(
        components_by_name.values(),
        key=lambda c: c.get_full_name(include_root=False),
    )

    # Collect leads per component, filtering to eligible components
    component_leads: dict[str, list[fabll.Node]] = {}
    all_leads: list[fabll.Node] = []
    filtered_components: list[fabll.Node] = []
    for component in components_sorted:
        leads = component.get_children(
            direct_only=False,
            types=fabll.Node,
            required_trait=F.Lead.is_lead,
        )
        component_leads[component.get_full_name()] = leads
        filtered_components.append(component)
        all_leads.extend(leads)

    if not filtered_components:
        logger.info("No pinout-eligible components found")
        return []

    # Group leads into buses: one get_connected() call per unique bus instead
    # of per lead. Each bus maps a representative → set of all connected nodes.
    lead_set = set(all_leads)
    buses = fabll.is_interface.group_into_buses(all_leads)

    # Build per-lead lookups from bus results.
    # Derive net name directly from bus members instead of calling
    # get_named_net (which does another get_connected traversal per bus).
    lead_bus_members: dict[fabll.Node, set[fabll.Node]] = {}
    lead_net_names: dict[fabll.Node, str | None] = {}
    for representative, bus_members in buses.items():
        net_name: str | None = None
        for node in bus_members:
            parent_info = node.get_parent()
            if parent_info is not None:
                if net_trait := parent_info[0].try_get_trait(F.has_net_name):
                    net = fabll.Traits.bind(net_trait).get_obj_raw().cast(F.Net)
                    net_name = net.get_name()
                    break
        for member in bus_members:
            if member in lead_set:
                lead_bus_members[member] = bus_members
                lead_net_names[member] = net_name

    components: list[PinoutComponent] = []
    for component in filtered_components:
        try:
            components.append(
                extract_pinout_component(
                    component,
                    app,
                    leads=component_leads[component.get_full_name()],
                    lead_bus_members=lead_bus_members,
                    lead_net_names=lead_net_names,
                )
            )
        except ValueError:
            logger.info(
                "Skipping pinout for %s",
                component.get_full_name(include_root=False),
                exc_info=True,
            )

    output_dir.mkdir(parents=True, exist_ok=True)
    for output_path in output_dir.glob("*"):
        if output_path.is_file():
            output_path.unlink()

    for index, component in enumerate(components, start=1):
        artifact_stem = sanitize_filepath_part(component.name) or "component"
        output_path = output_dir / f"{index:03d}_{artifact_stem}.json"
        output_path.write_text(
            json.dumps(component.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )
        logger.info("Wrote pinout JSON to %s", output_path)

    return components
