"""Convert schematic HL models to normalized netlists."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from math import isclose

from faebryk.libs.eda.hl.models.netlist import (
    Net as NetlistNet,
)
from faebryk.libs.eda.hl.models.netlist import (
    Netlist,
    TerminalRef,
)
from faebryk.libs.eda.hl.models.schematic import (
    Net,
    Pin,
    Point2D,
    Schematic,
    Sheet,
    WireSegment,
)

type NodeKey = tuple[str, Point2D]

_EPSILON = 1e-9
_BUS_EXPR_RE = re.compile(r".*((?<!~)\{[^{}]+\}|\[[^\[\]]+\])")
_BUS_GROUP_RE = re.compile(r"^(?P<prefix>.*)\{(?P<inner>[^{}]+)\}$")
_BUS_VECTOR_RE = re.compile(r"^(?P<base>.+)\[(?P<start>-?\d+)\.\.(?P<end>-?\d+)\]$")
_NATURAL_PART_RE = re.compile(r"(\d+)")
_NUMERIC_SUFFIX_RE = re.compile(r"^(?P<base>.*?)(?P<index>\d+)$")
_OWNER_SUFFIX_RE = re.compile(r"(?:\s|[0-9?])(?P<suffix>[A-Z]+)$")


class _DisjointSet:
    def __init__(self) -> None:
        self.parent: dict[NodeKey, NodeKey] = {}

    def add(self, node: NodeKey) -> None:
        self.parent.setdefault(node, node)

    def find(self, node: NodeKey) -> NodeKey:
        self.add(node)
        root = self.parent[node]
        if root != node:
            root = self.find(root)
            self.parent[node] = root
        return root

    def union(self, left: NodeKey, right: NodeKey) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


class _IndexDisjointSet:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, index: int) -> int:
        root = self.parent[index]
        if root != index:
            root = self.find(root)
            self.parent[index] = root
        return root

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


@dataclass
class _NameRecord:
    name: str
    priority: int


@dataclass
class _BusEndpoint:
    location: Point2D
    expression: str
    priority: int
    contributes_name: bool
    global_scope: bool = False


@dataclass
class _BusMemberGroup:
    match_names: dict[str, int] = field(default_factory=dict)
    alias_names: dict[str, int] = field(default_factory=dict)
    scalar_roots: set[NodeKey] = field(default_factory=set)


@dataclass
class _HierarchyContext:
    linked_sheet_pins: set[tuple[str, str]]


@dataclass(frozen=True)
class _NamingPolicy:
    numeric_suffix_canonicalization: str | None = None
    single_pin_nc_placeholder: str | None = None


def _points_close(left: Point2D, right: Point2D) -> bool:
    return isclose(left[0], right[0], abs_tol=_EPSILON) and isclose(
        left[1], right[1], abs_tol=_EPSILON
    )


def _point_on_segment(point: Point2D, start: Point2D, end: Point2D) -> bool:
    px, py = point
    x1, y1 = start
    x2, y2 = end
    cross = (px - x1) * (y2 - y1) - (py - y1) * (x2 - x1)
    if not isclose(cross, 0.0, abs_tol=_EPSILON):
        return False
    return (
        min(x1, x2) - _EPSILON <= px <= max(x1, x2) + _EPSILON
        and min(y1, y2) - _EPSILON <= py <= max(y1, y2) + _EPSILON
    )


def _segment_param(point: Point2D, start: Point2D, end: Point2D) -> float:
    x1, y1 = start
    x2, y2 = end
    px, py = point
    dx = x2 - x1
    dy = y2 - y1
    if abs(dx) >= abs(dy) and not isclose(dx, 0.0, abs_tol=_EPSILON):
        return (px - x1) / dx
    if not isclose(dy, 0.0, abs_tol=_EPSILON):
        return (py - y1) / dy
    return 0.0


def _is_bus_expression(name: str) -> bool:
    return bool(_BUS_EXPR_RE.fullmatch(name))


def _wire_kind(wire: WireSegment) -> str:
    kind = wire.extra_properties.get("kind")
    return kind if isinstance(kind, str) else "wire"


def _net_kind(net: Net) -> str:
    kind = net.extra_properties.get("kind")
    return kind if isinstance(kind, str) else "label"


def _pin_kind(pin: Pin) -> str:
    kind = pin.extra_properties.get("kind")
    return kind if isinstance(kind, str) else "pin"


def _sheet_depth(sheet: Sheet) -> int:
    value = sheet.extra_properties.get("sheet_depth")
    return value if isinstance(value, int) else 0


def _sheet_path(sheet: Sheet) -> str:
    value = sheet.extra_properties.get("sheet_path")
    return value if isinstance(value, str) else "/"


def _naming_policy(model: Schematic) -> _NamingPolicy:
    value = model.extra_properties.get("netlist_naming_policy")
    if not isinstance(value, dict):
        return _NamingPolicy()
    numeric_suffix_canonicalization = value.get("numeric_suffix_canonicalization")
    single_pin_nc_placeholder = value.get("single_pin_nc_placeholder")
    return _NamingPolicy(
        numeric_suffix_canonicalization=(
            numeric_suffix_canonicalization
            if isinstance(numeric_suffix_canonicalization, str)
            else None
        ),
        single_pin_nc_placeholder=(
            single_pin_nc_placeholder
            if isinstance(single_pin_nc_placeholder, str)
            else None
        ),
    )


def _sheet_bus_aliases(sheet: Sheet) -> dict[str, tuple[str, ...]]:
    value = sheet.extra_properties.get("bus_aliases")
    if not isinstance(value, dict):
        return {}
    aliases: dict[str, tuple[str, ...]] = {}
    for name, members in value.items():
        if isinstance(name, str) and isinstance(members, tuple | list):
            aliases[name] = tuple(
                member for member in members if isinstance(member, str)
            )
    return aliases


def _sheet_bus_entries(sheet: Sheet) -> list[tuple[Point2D, Point2D]]:
    value = sheet.extra_properties.get("bus_entries")
    if not isinstance(value, list):
        return []
    endpoints: list[tuple[Point2D, Point2D]] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        entry_points = entry.get("endpoints")
        if (
            isinstance(entry_points, tuple | list)
            and len(entry_points) == 2
            and isinstance(entry_points[0], tuple | list)
            and isinstance(entry_points[1], tuple | list)
            and len(entry_points[0]) == 2
            and len(entry_points[1]) == 2
        ):
            left = (float(entry_points[0][0]), float(entry_points[0][1]))
            right = (float(entry_points[1][0]), float(entry_points[1][1]))
            endpoints.append((left, right))
    return endpoints


def _net_priority(net: Net, sheet: Sheet) -> int:
    if net.is_global or net.is_power or bool(net.extra_properties.get("global")):
        return -1
    return _sheet_depth(sheet)


def _net_contributes_name(net: Net) -> bool:
    return net.extra_properties.get("contributes_name") is not False


def _net_raw_name(net: Net) -> str:
    raw_name = net.extra_properties.get("raw_name")
    return raw_name if isinstance(raw_name, str) else net.name


def _scalar_net(net: Net) -> bool:
    return "bus" not in _net_kind(net)


def _bus_net(net: Net) -> bool:
    return "bus" in _net_kind(net)


def _scalar_pin(pin: Pin) -> bool:
    return _pin_kind(pin) != "bus_pin"


def _bus_pin(pin: Pin) -> bool:
    return _pin_kind(pin) == "bus_pin" or _is_bus_expression(pin.name)


def _sheet_points(sheet: Sheet, *, bus: bool) -> list[Point2D]:
    points: list[Point2D] = []
    if not bus:
        points.extend(junction.location for junction in sheet.junctions)
    points.extend(
        net.anchor for net in sheet.nets if (_bus_net(net) if bus else _scalar_net(net))
    )
    points.extend(
        pin.location
        for pin in sheet.pins
        if (_bus_pin(pin) if bus else _scalar_pin(pin))
    )
    for symbol in sheet.symbols:
        if symbol.kind == "sheet":
            points.extend(
                pin.location
                for pin in symbol.pins
                if (_bus_pin(pin) if bus else _scalar_pin(pin))
            )
        elif not bus:
            points.extend(pin.location for pin in symbol.pins)
    for wire in sheet.wires:
        if (_wire_kind(wire) == "bus") != bus:
            continue
        points.extend(wire.points)
    for left, right in _sheet_bus_entries(sheet):
        points.append(left)
        points.append(right)
    unique_points: list[Point2D] = []
    for point in points:
        if any(_points_close(point, existing) for existing in unique_points):
            continue
        unique_points.append(point)
    return unique_points


def _find_sheet_point(sheet_points: list[Point2D], point: Point2D) -> Point2D:
    for candidate in sheet_points:
        if _points_close(candidate, point):
            return candidate
    return point


def _wire_points(sheet: Sheet, *, bus: bool) -> list[WireSegment]:
    return [wire for wire in sheet.wires if (_wire_kind(wire) == "bus") == bus]


def _point_on_any_wire(sheet: Sheet, point: Point2D, *, bus: bool) -> bool:
    for wire in _wire_points(sheet, bus=bus):
        for start, end in zip(wire.points, wire.points[1:], strict=False):
            if _point_on_segment(point, start, end):
                return True
    return False


def _resolve_bus_entry(
    sheet: Sheet,
    left: Point2D,
    right: Point2D,
) -> tuple[Point2D, Point2D] | None:
    left_on_bus = _point_on_any_wire(sheet, left, bus=True)
    right_on_bus = _point_on_any_wire(sheet, right, bus=True)
    left_on_wire = _point_on_any_wire(sheet, left, bus=False)
    right_on_wire = _point_on_any_wire(sheet, right, bus=False)
    if left_on_bus and right_on_wire:
        return (left, right)
    if right_on_bus and left_on_wire:
        return (right, left)
    if left_on_bus and not right_on_bus:
        return (left, right)
    if right_on_bus and not left_on_bus:
        return (right, left)
    return None


def _apply_bus_prefix(prefix: str, name: str) -> str:
    if not prefix:
        return name
    if prefix.endswith((".", "/")):
        return f"{prefix}{name}"
    return f"{prefix}.{name}"


def _expand_bus_raw(expr: str, aliases: dict[str, tuple[str, ...]]) -> dict[str, str]:
    match = _BUS_GROUP_RE.fullmatch(expr)
    if match is not None:
        prefix = match.group("prefix")
        inner = match.group("inner").strip()
        base = _expand_bus_raw(inner, aliases)
        return {key: _apply_bus_prefix(prefix, value) for key, value in base.items()}

    if expr in aliases:
        expanded: dict[str, str] = {}
        for member in aliases[expr]:
            expanded.update(_expand_bus_raw(member, aliases))
        return expanded

    vector_match = _BUS_VECTOR_RE.fullmatch(expr)
    if vector_match is not None:
        base = vector_match.group("base")
        start = int(vector_match.group("start"))
        end = int(vector_match.group("end"))
        step = 1 if end >= start else -1
        return {
            f"{base}{index}": f"{base}{index}"
            for index in range(start, end + step, step)
        }

    if " " in expr:
        expanded: dict[str, str] = {}
        for token in expr.split():
            expanded.update(_expand_bus_raw(token, aliases))
        return expanded

    return {expr: expr}


def _natural_key(text: str) -> tuple[int | str, ...]:
    parts = _NATURAL_PART_RE.split(text)
    return tuple(int(part) if part.isdigit() else part for part in parts)


def _synthetic_net_name(terminals: list[TerminalRef]) -> str | None:
    if not terminals:
        return None
    anchor = min(
        terminals,
        key=lambda terminal: (
            _natural_key(terminal.owner_name or terminal.owner_id or ""),
            _natural_key(terminal.terminal_id),
        ),
    )
    owner_name = anchor.owner_name or anchor.owner_id
    if not owner_name:
        return None
    return f"Net{owner_name}_{anchor.terminal_id}"


def _name_specificity(name: str, candidates: set[str]) -> int:
    score = 0
    for other in candidates:
        if other == name:
            continue
        if name.startswith(f"{other}_") or name.endswith(f"_{other}"):
            score += 2
        if other.startswith(f"{name}_") or other.endswith(f"_{name}"):
            score -= 2
        if name.startswith(other) and len(name) > len(other):
            score += 1
        if other.startswith(name) and len(other) > len(name):
            score -= 1
    return score


def _name_sort_key(
    name: str,
    *,
    alias_map: dict[str, int],
    candidate_names: set[str],
) -> tuple[int, int, tuple[int | str, ...]]:
    return (
        -_name_specificity(name, candidate_names),
        alias_map[name],
        _natural_key(name),
    )


def _drop_alias(canonical_name: str, alias_name: str) -> bool:
    if canonical_name == alias_name:
        return True
    return (
        canonical_name.startswith(f"{alias_name}_")
        or canonical_name.endswith(f"_{alias_name}")
        or alias_name.startswith(f"{canonical_name}_")
        or alias_name.endswith(f"_{canonical_name}")
    )


def _owner_suffix(owner_name: str | None) -> str | None:
    if not owner_name:
        return None
    match = _OWNER_SUFFIX_RE.search(owner_name)
    if match is None:
        return None
    return match.group("suffix")


def _non_sheet_pin_alpha_suffixes(terminals: list[TerminalRef]) -> set[str]:
    return {
        suffix
        for terminal in terminals
        if terminal.kind != "schematic_sheet_pin"
        and (suffix := _owner_suffix(terminal.owner_name)) is not None
    }


def _all_non_sheet_terminals_have_alpha_suffix(terminals: list[TerminalRef]) -> bool:
    non_sheet = [
        terminal for terminal in terminals if terminal.kind != "schematic_sheet_pin"
    ]
    return bool(non_sheet) and all(
        _owner_suffix(terminal.owner_name) is not None for terminal in non_sheet
    )


def _sheet_pin_suffixes(terminals: list[TerminalRef]) -> set[str]:
    return {
        suffix
        for terminal in terminals
        if terminal.kind == "schematic_sheet_pin"
        and (suffix := _owner_suffix(terminal.owner_name)) is not None
    }


def _canonicalize_numeric_suffix_name(name: str, terminals: list[TerminalRef]) -> str:
    match = _NUMERIC_SUFFIX_RE.fullmatch(name)
    if match is None:
        return name

    base = match.group("base")
    alpha_suffixes = _non_sheet_pin_alpha_suffixes(terminals)
    if len(alpha_suffixes) == 1 and _all_non_sheet_terminals_have_alpha_suffix(
        terminals
    ):
        return f"{base}{next(iter(alpha_suffixes))}"

    sheet_suffixes = _sheet_pin_suffixes(terminals)
    if len(sheet_suffixes) > 1 and alpha_suffixes and alpha_suffixes <= sheet_suffixes:
        return f"{base}{min(sheet_suffixes, key=_natural_key)}"

    return name


def _should_use_single_pin_nc_placeholder(
    terminals: list[TerminalRef],
    pin_names: set[str],
) -> bool:
    return len(terminals) == 1 and pin_names == {"NC"}


def _terminal_sort_key(
    terminals: list[TerminalRef],
) -> tuple[tuple[int | str, ...], ...]:
    return tuple(
        sorted(
            (
                _natural_key(terminal.owner_name or terminal.owner_id or ""),
                _natural_key(terminal.terminal_id),
            )
            for terminal in terminals
        )
    )


def _expand_bus_expression(
    *,
    sheet: Sheet,
    expression: str,
    global_scope: bool,
) -> dict[str, str]:
    raw_members = _expand_bus_raw(expression, _sheet_bus_aliases(sheet))
    if global_scope:
        return raw_members
    prefix = _sheet_path(sheet)
    return {key: f"{prefix}{value}" for key, value in raw_members.items()}


def _register_name(
    names_by_root: dict[NodeKey, dict[str, int]],
    root: NodeKey,
    *,
    name: str,
    priority: int,
) -> None:
    names_by_root.setdefault(root, {})
    existing = names_by_root[root].get(name)
    if existing is None or priority < existing:
        names_by_root[root][name] = priority


def convert_schematic_to_netlist(model: Schematic) -> Netlist:
    naming_policy = _naming_policy(model)
    scalar_dsu = _DisjointSet()
    scalar_sheet_points = {
        sheet.id: _sheet_points(sheet, bus=False)
        for sheet in model.sheets
        if sheet.id is not None
    }

    for sheet in model.sheets:
        if sheet.id is None:
            continue
        for point in scalar_sheet_points[sheet.id]:
            scalar_dsu.add((sheet.id, point))

    for sheet in model.sheets:
        if sheet.id is None:
            continue
        interesting_points = scalar_sheet_points[sheet.id]
        for wire in _wire_points(sheet, bus=False):
            for start, end in zip(wire.points, wire.points[1:], strict=False):
                segment_points = [
                    point
                    for point in interesting_points
                    if _point_on_segment(point, start, end)
                ]
                segment_points = sorted(
                    segment_points,
                    key=lambda point: _segment_param(point, start, end),
                )
                for left, right in zip(
                    segment_points, segment_points[1:], strict=False
                ):
                    scalar_dsu.union((sheet.id, left), (sheet.id, right))

    scalar_global_nodes: dict[str, list[NodeKey]] = {}
    linked_sheet_pins: set[tuple[str, str]] = set()
    bus_hierarchy_links: list[tuple[NodeKey, NodeKey]] = []
    for sheet in model.sheets:
        if sheet.id is None:
            continue
        for net in sheet.nets:
            if not _scalar_net(net):
                continue
            if (
                net.is_global
                or net.is_power
                or bool(net.extra_properties.get("global"))
            ):
                anchor = _find_sheet_point(scalar_sheet_points[sheet.id], net.anchor)
                scalar_global_nodes.setdefault(net.name, []).append((sheet.id, anchor))
        for symbol in sheet.symbols:
            if symbol.kind != "sheet" or symbol.child_sheet_id is None:
                continue
            child_sheet = model.sheet_by_id.get(symbol.child_sheet_id)
            if child_sheet is None or child_sheet.id is None:
                continue
            child_pins_by_name = {pin.name: pin for pin in child_sheet.pins}
            for pin in symbol.pins:
                child_pin = child_pins_by_name.get(pin.name)
                if child_pin is None:
                    continue
                parent_node = (
                    sheet.id,
                    _find_sheet_point(scalar_sheet_points[sheet.id], pin.location),
                )
                child_node = (
                    child_sheet.id,
                    _find_sheet_point(
                        scalar_sheet_points[child_sheet.id],
                        child_pin.location,
                    ),
                )
                if _bus_pin(pin) or _bus_pin(child_pin):
                    bus_hierarchy_links.append((parent_node, child_node))
                    linked_sheet_pins.add((child_sheet.id, child_pin.name))
                    continue
                scalar_dsu.union(parent_node, child_node)
                linked_sheet_pins.add((child_sheet.id, child_pin.name))

    for nodes in scalar_global_nodes.values():
        anchor = nodes[0]
        for node in nodes[1:]:
            scalar_dsu.union(anchor, node)

    bus_dsu = _DisjointSet()
    bus_sheet_points = {
        sheet.id: _sheet_points(sheet, bus=True)
        for sheet in model.sheets
        if sheet.id is not None
    }
    for sheet in model.sheets:
        if sheet.id is None:
            continue
        for point in bus_sheet_points[sheet.id]:
            bus_dsu.add((sheet.id, point))

    for sheet in model.sheets:
        if sheet.id is None:
            continue
        interesting_points = bus_sheet_points[sheet.id]
        for wire in _wire_points(sheet, bus=True):
            for start, end in zip(wire.points, wire.points[1:], strict=False):
                segment_points = [
                    point
                    for point in interesting_points
                    if _point_on_segment(point, start, end)
                ]
                segment_points = sorted(
                    segment_points,
                    key=lambda point: _segment_param(point, start, end),
                )
                for left, right in zip(
                    segment_points, segment_points[1:], strict=False
                ):
                    bus_dsu.union((sheet.id, left), (sheet.id, right))

    for parent_node, child_node in bus_hierarchy_links:
        bus_dsu.union(parent_node, child_node)

    bus_global_nodes: dict[str, list[NodeKey]] = {}
    for sheet in model.sheets:
        if sheet.id is None:
            continue
        for net in sheet.nets:
            if _net_kind(net) != "bus_global_label":
                continue
            anchor = _find_sheet_point(bus_sheet_points[sheet.id], net.anchor)
            raw_name = net.extra_properties.get("raw_name")
            if isinstance(raw_name, str):
                bus_global_nodes.setdefault(raw_name, []).append((sheet.id, anchor))
    for nodes in bus_global_nodes.values():
        anchor = nodes[0]
        for node in nodes[1:]:
            bus_dsu.union(anchor, node)

    bus_member_groups: dict[NodeKey, dict[str, _BusMemberGroup]] = {}
    for sheet in model.sheets:
        if sheet.id is None:
            continue
        endpoints: list[_BusEndpoint] = []
        for net in sheet.nets:
            if not _bus_net(net):
                continue
            raw_name = net.extra_properties.get("raw_name")
            expression = raw_name if isinstance(raw_name, str) else net.name
            endpoints.append(
                _BusEndpoint(
                    location=net.anchor,
                    expression=expression,
                    priority=_net_priority(net, sheet),
                    contributes_name=bool(net.extra_properties.get("contributes_name")),
                    global_scope=bool(net.extra_properties.get("global")),
                )
            )

        for endpoint in endpoints:
            anchor = _find_sheet_point(bus_sheet_points[sheet.id], endpoint.location)
            bus_root = bus_dsu.find((sheet.id, anchor))
            member_groups = bus_member_groups.setdefault(bus_root, {})
            for member_key, concrete_name in _expand_bus_expression(
                sheet=sheet,
                expression=endpoint.expression,
                global_scope=endpoint.global_scope,
            ).items():
                group = member_groups.setdefault(member_key, _BusMemberGroup())
                existing = group.match_names.get(concrete_name)
                if existing is None or endpoint.priority < existing:
                    group.match_names[concrete_name] = endpoint.priority
                if endpoint.contributes_name:
                    existing = group.alias_names.get(concrete_name)
                    if existing is None or endpoint.priority < existing:
                        group.alias_names[concrete_name] = endpoint.priority

    # Some net names, especially hierarchical labels, must participate in root
    # matching without automatically surfacing as the canonical/display name.
    match_names_by_root: dict[NodeKey, dict[str, int]] = {}
    display_names_by_root: dict[NodeKey, dict[str, int]] = {}
    scalar_names: dict[NodeKey, set[str]] = {}
    raw_roots_by_sheet: dict[str, dict[str, set[NodeKey]]] = {}
    global_raw_names_by_sheet: dict[str, set[str]] = {}
    for sheet in model.sheets:
        if sheet.id is None:
            continue
        for net in sheet.nets:
            if not _scalar_net(net):
                continue
            root = scalar_dsu.find(
                (
                    sheet.id,
                    _find_sheet_point(scalar_sheet_points[sheet.id], net.anchor),
                )
            )
            priority = _net_priority(net, sheet)
            _register_name(match_names_by_root, root, name=net.name, priority=priority)
            if _net_contributes_name(net):
                _register_name(
                    display_names_by_root,
                    root,
                    name=net.name,
                    priority=priority,
                )
            scalar_names.setdefault(root, set()).add(net.name)
            raw_name = _net_raw_name(net)
            raw_roots_by_sheet.setdefault(sheet.id, {}).setdefault(raw_name, set()).add(
                root
            )
            if (
                net.is_global
                or net.is_power
                or bool(net.extra_properties.get("global"))
            ):
                global_raw_names_by_sheet.setdefault(sheet.id, set()).add(raw_name)

    bus_group_list: list[_BusMemberGroup] = []
    for sheet in model.sheets:
        if sheet.id is None:
            continue
        for left, right in _sheet_bus_entries(sheet):
            resolved = _resolve_bus_entry(sheet, left, right)
            if resolved is None:
                continue
            bus_point, wire_point = resolved
            bus_root = bus_dsu.find(
                (sheet.id, _find_sheet_point(bus_sheet_points[sheet.id], bus_point))
            )
            scalar_root = scalar_dsu.find(
                (
                    sheet.id,
                    _find_sheet_point(scalar_sheet_points[sheet.id], wire_point),
                )
            )
            groups = bus_member_groups.get(bus_root, {})
            if not groups:
                continue
            for scalar_name in scalar_names.get(scalar_root, set()):
                for member_key, group in groups.items():
                    if scalar_name in group.match_names:
                        group.scalar_roots.add(scalar_root)
                        group.alias_names.setdefault(
                            scalar_name,
                            min(
                                match_names_by_root.get(scalar_root, {}).get(
                                    scalar_name, _sheet_depth(sheet)
                                ),
                                group.match_names[scalar_name],
                            ),
                        )

    for groups in bus_member_groups.values():
        bus_group_list.extend(groups.values())

    merged_bus_groups = bus_group_list
    if len(bus_group_list) >= 2:
        group_dsu = _IndexDisjointSet(len(bus_group_list))
        groups_by_name: dict[str, list[int]] = {}
        for index, group in enumerate(bus_group_list):
            for name in group.match_names:
                groups_by_name.setdefault(name, []).append(index)
        for indices in groups_by_name.values():
            anchor = indices[0]
            for index in indices[1:]:
                group_dsu.union(anchor, index)

        merged_groups: dict[int, _BusMemberGroup] = {}
        for index, group in enumerate(bus_group_list):
            root_index = group_dsu.find(index)
            merged_group = merged_groups.setdefault(root_index, _BusMemberGroup())
            merged_group.scalar_roots.update(group.scalar_roots)
            for name, priority in group.match_names.items():
                existing = merged_group.match_names.get(name)
                if existing is None or priority < existing:
                    merged_group.match_names[name] = priority
            for name, priority in group.alias_names.items():
                existing = merged_group.alias_names.get(name)
                if existing is None or priority < existing:
                    merged_group.alias_names[name] = priority
        merged_bus_groups = list(merged_groups.values())

    for group in merged_bus_groups:
        scalar_roots = sorted(group.scalar_roots)
        if len(scalar_roots) < 2:
            continue
        anchor = scalar_roots[0]
        for node in scalar_roots[1:]:
            scalar_dsu.union(anchor, node)

    for sheet_id, global_raw_names in global_raw_names_by_sheet.items():
        raw_roots = raw_roots_by_sheet.get(sheet_id, {})
        for raw_name in global_raw_names:
            roots = sorted(
                {scalar_dsu.find(root) for root in raw_roots.get(raw_name, set())}
            )
            if len(roots) < 2:
                continue
            anchor = roots[0]
            for root in roots[1:]:
                scalar_dsu.union(anchor, root)

    scalar_roots_by_name: dict[str, list[NodeKey]] = {}
    for root, root_names in match_names_by_root.items():
        for name in root_names:
            scalar_roots_by_name.setdefault(name, []).append(root)
    for roots in scalar_roots_by_name.values():
        if len(roots) < 2:
            continue
        anchor = roots[0]
        for root in roots[1:]:
            scalar_dsu.union(anchor, root)

    merged_display_names_by_root: dict[NodeKey, dict[str, int]] = {}
    for root, root_names in display_names_by_root.items():
        final_root = scalar_dsu.find(root)
        for name, priority in root_names.items():
            _register_name(
                merged_display_names_by_root, final_root, name=name, priority=priority
            )

    merged_match_names_by_root: dict[NodeKey, dict[str, int]] = {}
    for root, root_names in match_names_by_root.items():
        final_root = scalar_dsu.find(root)
        for name, priority in root_names.items():
            _register_name(
                merged_match_names_by_root, final_root, name=name, priority=priority
            )

    for group in merged_bus_groups:
        if not group.scalar_roots:
            continue
        final_root = scalar_dsu.find(next(iter(group.scalar_roots)))
        for name, priority in group.alias_names.items():
            _register_name(
                merged_display_names_by_root, final_root, name=name, priority=priority
            )

    ctx = _HierarchyContext(linked_sheet_pins=linked_sheet_pins)
    terminals_by_root: dict[NodeKey, list[TerminalRef]] = {}
    pin_names_by_root: dict[NodeKey, set[str]] = {}

    for sheet in model.sheets:
        if sheet.id is None:
            continue
        for symbol in sheet.symbols:
            if symbol.kind == "sheet":
                continue
            if bool(symbol.extra_properties.get("power_symbol")):
                continue
            if symbol.extra_properties.get("on_board") is False:
                continue
            owner_name = symbol.refdes or symbol.name or symbol.id
            for pin in symbol.pins:
                root = scalar_dsu.find(
                    (
                        sheet.id,
                        _find_sheet_point(scalar_sheet_points[sheet.id], pin.location),
                    )
                )
                pin_name = pin.extra_properties.get("pin_name")
                if isinstance(pin_name, str):
                    pin_names_by_root.setdefault(root, set()).add(pin_name)
                terminals_by_root.setdefault(root, []).append(
                    TerminalRef(
                        kind="schematic_pin",
                        owner_id=symbol.id,
                        owner_name=owner_name,
                        terminal_id=pin.name,
                    )
                )
        for pin in sheet.pins:
            if (sheet.id, pin.name) in ctx.linked_sheet_pins:
                continue
            if _bus_pin(pin):
                continue
            root = scalar_dsu.find(
                (
                    sheet.id,
                    _find_sheet_point(scalar_sheet_points[sheet.id], pin.location),
                )
            )
            terminals_by_root.setdefault(root, []).append(
                TerminalRef(
                    kind="schematic_sheet_pin",
                    owner_id=sheet.id,
                    owner_name=sheet.name or sheet.id,
                    terminal_id=pin.name,
                )
            )

    nets: list[NetlistNet] = []
    unnamed_counter = 1
    for root, terminals in sorted(
        terminals_by_root.items(),
        key=lambda item: _terminal_sort_key(item[1]),
    ):
        alias_map = merged_display_names_by_root.get(root)
        if not alias_map:
            alias_map = merged_match_names_by_root.get(root, {})
        candidate_names = set(alias_map)
        aliases = sorted(
            candidate_names,
            key=lambda name: _name_sort_key(
                name,
                alias_map=alias_map,
                candidate_names=candidate_names,
            ),
        )
        if aliases:
            name = aliases[0]
            aliases = [alias for alias in aliases[1:] if not _drop_alias(name, alias)]
            if naming_policy.numeric_suffix_canonicalization == "terminal_alpha_suffix":
                name = _canonicalize_numeric_suffix_name(name, terminals)
        else:
            if (
                naming_policy.single_pin_nc_placeholder == "question_mark"
                and _should_use_single_pin_nc_placeholder(
                    terminals,
                    pin_names_by_root.get(root, set()),
                )
            ):
                name = f"?{unnamed_counter}"
                unnamed_counter += 1
            else:
                name = _synthetic_net_name(terminals)
            aliases = []
        net_id = name or f"net-{unnamed_counter}"
        if name is None:
            unnamed_counter += 1
        nets.append(
            NetlistNet(
                id=net_id,
                name=name,
                aliases=aliases,
                terminals=sorted(terminals),
            )
        )

    return Netlist(nets=sorted(nets, key=lambda net: (net.name or "", net.id)))
