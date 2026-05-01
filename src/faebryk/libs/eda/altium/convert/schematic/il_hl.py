"""Convert Altium schematic IL into the shared HL schematic model."""

from __future__ import annotations

import re
from math import isclose
from pathlib import Path

from faebryk.libs.eda.altium.convert.schematic.file_ll import SchDocCodec
from faebryk.libs.eda.altium.convert.schematic.il_ll import convert_ll_to_il
from faebryk.libs.eda.altium.models.schematic.il import (
    AltiumSchematic,
    SchematicBus,
    SchematicComponent,
    SchematicJunction,
    SchematicNetLabel,
    SchematicParameter,
    SchematicPin,
    SchematicPort,
    SchematicPowerObject,
    SchematicSheetEntry,
    SchematicSheetSymbol,
    SchematicWire,
)
from faebryk.libs.eda.hl.models.schematic import (
    Junction,
    Net,
    Pin,
    Schematic,
    Sheet,
    Symbol,
    WireSegment,
)

type Point2D = tuple[float, float]

_EPSILON = 1e-9
_REPEAT_SHEET_RE = re.compile(
    r"^Repeat\((?P<var>[^,]+),(?P<start>-?\d+),(?P<end>-?\d+)\)$",
    re.IGNORECASE,
)
_REPEAT_ENTRY_RE = re.compile(r"^Repeat\((?P<name>[^)]+)\)$", re.IGNORECASE)
_BUS_EXPR_RE = re.compile(r".*((?<!~)\{[^{}]+\}|\[[^\[\]]+\])")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_GENERIC_CONNECTION_NAMES = {
    "signal",
    "sig",
    "in",
    "out",
    "input",
    "output",
    "port",
    "cs",
    "clk",
    "clock",
    "txd",
    "rxd",
    "tx",
    "rx",
}
_NETLIST_NAMING_POLICY = {
    "numeric_suffix_canonicalization": "terminal_alpha_suffix",
    "single_pin_nc_placeholder": "question_mark",
}


def _point(location: tuple[int, int]) -> Point2D:
    return (float(location[0]), float(location[1]))


def _strip_control_chars(text: str) -> str:
    return _CONTROL_CHARS_RE.sub("", text).strip()


def _sheet_path(names: list[str]) -> str:
    if not names:
        return "/"
    return "/" + "/".join(names) + "/"


def _scoped_id(scope_id: str | None, item_id: str | None, *, fallback: str) -> str:
    base = item_id or fallback
    if not scope_id:
        return base
    return f"{scope_id}:{base}"


def _repeat_sheet_range(sheet_name: str) -> tuple[int, int] | None:
    match = _REPEAT_SHEET_RE.fullmatch(sheet_name.strip())
    if match is None:
        return None
    return (int(match.group("start")), int(match.group("end")))


def _repeat_entry_name(name: str) -> str | None:
    match = _REPEAT_ENTRY_RE.fullmatch(name.strip())
    if match is None:
        return None
    return match.group("name").strip()


def _repeat_values(start: int, end: int) -> list[int]:
    step = 1 if end >= start else -1
    return list(range(start, end + step, step))


def _alpha_suffix(value: int) -> str:
    if value <= 0:
        return str(value)
    result = ""
    current = value
    while current > 0:
        current -= 1
        result = chr(ord("A") + (current % 26)) + result
        current //= 26
    return result


def _refdes_with_suffix(refdes: str | None, suffix: str | None) -> str | None:
    if not refdes or not suffix:
        return refdes
    return f"{refdes}{suffix}"


def _local_net_name(
    raw_name: str,
    *,
    port_name_map: dict[str, str],
    local_net_suffix: str | None,
) -> str:
    if _is_bus_expression(raw_name):
        return raw_name
    if raw_name in port_name_map:
        return port_name_map[raw_name]
    if local_net_suffix:
        return f"{raw_name}{local_net_suffix}"
    return raw_name


def _port_name_map(
    sheet_symbol: SchematicSheetSymbol,
    *,
    repeat_value: int | None,
    resolved_names: dict[str, str] | None = None,
) -> dict[str, str]:
    mapping: dict[str, str] = dict(resolved_names or {})
    for entry in sheet_symbol.entries:
        repeat_name = _repeat_entry_name(entry.name)
        if repeat_name is not None and repeat_value is not None:
            mapping[repeat_name] = f"{repeat_name}{repeat_value}"
        elif entry.name:
            mapping.setdefault(entry.name, mapping.get(entry.name, entry.name))
    return mapping


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


def _is_bus_expression(name: str) -> bool:
    return bool(_BUS_EXPR_RE.fullmatch(name))


def _points_close(left: Point2D, right: Point2D) -> bool:
    return isclose(left[0], right[0], abs_tol=_EPSILON) and isclose(
        left[1], right[1], abs_tol=_EPSILON
    )


def _pin_endpoint(pin: SchematicPin) -> Point2D:
    x, y = pin.location
    match pin.orientation:
        case 0:  # Right
            endpoint = (x + pin.length, y)
        case 1:  # Up
            endpoint = (x, y + pin.length)
        case 2:  # Left
            endpoint = (x - pin.length, y)
        case 3:  # Down
            endpoint = (x, y - pin.length)
        case _:
            endpoint = pin.location
    return _point(endpoint)


def _pin_name(pin: SchematicPin) -> str:
    return _strip_control_chars(pin.designator or pin.name)


def _designator(parameters: list[SchematicParameter]) -> str | None:
    for parameter in parameters:
        if parameter.is_designator and parameter.text:
            return _strip_control_chars(parameter.text)
    for parameter in parameters:
        if parameter.name.lower() == "designator" and parameter.text:
            return _strip_control_chars(parameter.text)
    return None


def _component_symbol(
    component: SchematicComponent,
    *,
    scope_id: str | None,
    refdes_suffix: str | None,
    sheet_wires: list[SchematicWire],
    attachment_points: list[Point2D],
) -> Symbol:
    symbol_id = _scoped_id(scope_id, component.id, fallback="component")
    refdes = _refdes_with_suffix(
        _designator(component.parameters) or component.lib_reference or None,
        refdes_suffix,
    )
    visible_pins = [
        pin
        for pin in component.pins
        if pin.owner_part_id <= 0 or pin.owner_part_id == component.current_part_id
    ]
    pins_by_name: dict[str, list[SchematicPin]] = {}
    for pin in visible_pins:
        pins_by_name.setdefault(_pin_name(pin), []).append(pin)

    selected_pins: list[SchematicPin] = []
    all_wires = [*sheet_wires, *component.wires]
    component_attachment_points = [
        _point(item.location) for item in component.junctions
    ]
    component_attachment_points.extend(
        _point(item.location) for item in component.net_labels
    )
    component_attachment_points.extend(
        _point(item.location) for item in component.power_objects if item.text
    )
    all_attachment_points = [*attachment_points, *component_attachment_points]
    for pins in pins_by_name.values():
        if len(pins) == 1:
            selected_pins.append(pins[0])
            continue

        def _pin_score(pin: SchematicPin) -> tuple[int, int, int, int]:
            endpoint = _pin_endpoint(pin)
            touches_wire = int(_point_on_any_wire(endpoint, all_wires))
            touches_attachment = int(
                any(_points_close(endpoint, point) for point in all_attachment_points)
            )
            length = int(pin.length)
            source_order = pin.source_order if pin.source_order is not None else 0
            return (
                touches_wire,
                touches_attachment,
                -length,
                -source_order,
            )

        selected_pins.append(max(pins, key=_pin_score))

    return Symbol(
        id=symbol_id,
        name=component.design_item_id or component.lib_reference,
        refdes=refdes,
        extra_properties={
            "component_location": _point(component.location),
            "orientation": component.orientation,
        },
        pins=[
            Pin(
                id=_scoped_id(symbol_id, pin.id, fallback="pin"),
                name=_pin_name(pin),
                location=_pin_endpoint(pin),
                extra_properties={
                    "pin_name": pin.name,
                    "pin_origin": _point(pin.location),
                    "pin_length": pin.length,
                    "pin_orientation": pin.orientation,
                },
            )
            for pin in selected_pins
        ],
    )


def _wire_segments(
    wires: list[SchematicWire],
    *,
    scope_id: str | None,
) -> list[WireSegment]:
    return [
        WireSegment(
            id=_scoped_id(scope_id, wire.id, fallback="wire"),
            points=[_point(vertex) for vertex in wire.vertices],
        )
        for wire in wires
        if len(wire.vertices) >= 2
    ]


def _bus_segments(
    buses: list[SchematicBus],
    *,
    scope_id: str | None,
) -> list[WireSegment]:
    return [
        WireSegment(
            id=_scoped_id(scope_id, bus.id, fallback="bus"),
            points=[_point(vertex) for vertex in bus.vertices],
            extra_properties={"kind": "bus"},
        )
        for bus in buses
        if len(bus.vertices) >= 2
    ]


def _junctions(
    items: list[SchematicJunction],
    *,
    scope_id: str | None,
) -> list[Junction]:
    return [
        Junction(
            id=_scoped_id(scope_id, item.id, fallback="junction"),
            location=_point(item.location),
        )
        for item in items
    ]


def _nets_from_labels(
    items: list[SchematicNetLabel],
    *,
    scope_id: str | None,
    sheet_path: str,
    sheet_depth: int,
    port_name_map: dict[str, str],
    local_net_suffix: str | None,
) -> list[Net]:
    nets: list[Net] = []
    for item in items:
        if not item.text:
            continue
        raw_name = item.text
        if _is_bus_expression(raw_name):
            nets.append(
                Net(
                    id=_scoped_id(scope_id, item.id, fallback="bus-label"),
                    name=raw_name,
                    anchor=_point(item.location),
                    is_global=False,
                    extra_properties={
                        "kind": "bus_label",
                        "sheet_path": sheet_path,
                        "sheet_depth": sheet_depth,
                        "raw_name": raw_name,
                        "contributes_name": True,
                    },
                )
            )
            continue
        nets.append(
            Net(
                id=_scoped_id(scope_id, item.id, fallback="net-label"),
                name=_local_net_name(
                    raw_name,
                    port_name_map=port_name_map,
                    local_net_suffix=local_net_suffix,
                ),
                anchor=_point(item.location),
                is_global=False,
                extra_properties={
                    "kind": "label",
                    "sheet_path": sheet_path,
                    "sheet_depth": sheet_depth,
                    "raw_name": raw_name,
                },
            )
        )
    return nets


def _nets_from_power(
    items: list[SchematicPowerObject],
    *,
    scope_id: str | None,
    sheet_path: str,
) -> list[Net]:
    return [
        Net(
            id=_scoped_id(scope_id, item.id, fallback="power"),
            name=item.text,
            anchor=_point(item.location),
            is_power=True,
            is_global=True,
            extra_properties={
                "kind": "power",
                "sheet_path": sheet_path,
                "sheet_depth": -1,
                "raw_name": item.text,
                "global": True,
            },
        )
        for item in items
        if item.text
    ]


def _sheet_entry_anchor(
    sheet_symbol: SchematicSheetSymbol,
    entry: SchematicSheetEntry,
    *,
    wires: list[SchematicWire] | None = None,
) -> Point2D:
    x, y = sheet_symbol.location
    if entry.side == 0:
        anchor = (x, y - entry.distance_from_top * 10)
    elif entry.side == 1:
        anchor = (x + sheet_symbol.x_size, y - entry.distance_from_top * 10)
    elif entry.side == 2:
        anchor = (x + entry.distance_from_top * 10, y)
    elif entry.side == 3:
        anchor = (
            x + entry.distance_from_top * 10 + 500_000,
            y - sheet_symbol.y_size,
        )
    else:
        anchor = sheet_symbol.location
    anchor_point = _point(anchor)
    if wires is None:
        return anchor_point

    candidates = [anchor_point]
    if entry.side in {0, 1}:
        candidates.extend(
            [
                (anchor_point[0], anchor_point[1] - 500_000),
                (anchor_point[0], anchor_point[1] + 500_000),
            ]
        )
    elif entry.side in {2, 3}:
        candidates.extend(
            [
                (anchor_point[0] - 500_000, anchor_point[1]),
                (anchor_point[0] + 500_000, anchor_point[1]),
            ]
        )

    for candidate in candidates:
        if _point_on_any_wire(candidate, wires):
            return candidate
    return anchor_point


def _sheet_symbol_symbol(
    sheet_symbol: SchematicSheetSymbol,
    *,
    scope_id: str | None,
    wires: list[SchematicWire],
    child_sheet_id: str | None,
    repeat_range: tuple[int, int] | None = None,
    repeated_child_sheet_ids: list[str] | None = None,
) -> Symbol:
    symbol_id = _scoped_id(scope_id, sheet_symbol.id, fallback="sheet-symbol")
    return Symbol(
        id=symbol_id,
        name=sheet_symbol.sheet_name or sheet_symbol.file_name or sheet_symbol.id,
        refdes=sheet_symbol.sheet_name or None,
        kind="sheet",
        child_sheet_id=child_sheet_id,
        extra_properties={
            "sheet_name": sheet_symbol.sheet_name,
            "file_name": sheet_symbol.file_name,
            "location": _point(sheet_symbol.location),
            "x_size": sheet_symbol.x_size,
            "y_size": sheet_symbol.y_size,
            "symbol_type": sheet_symbol.symbol_type,
            "repeat_range": repeat_range,
            "repeated_child_sheet_ids": repeated_child_sheet_ids or [],
        },
        pins=[
            Pin(
                id=_scoped_id(symbol_id, entry.id, fallback="sheet-entry"),
                name=(
                    f"{repeat_name}[{repeat_range[0]}..{repeat_range[1]}]"
                    if repeat_range is not None
                    and (repeat_name := _repeat_entry_name(entry.name)) is not None
                    else entry.name
                ),
                location=_sheet_entry_anchor(sheet_symbol, entry, wires=wires),
                extra_properties={
                    "kind": (
                        "bus_pin"
                        if (
                            repeat_range is not None
                            and _repeat_entry_name(entry.name) is not None
                        )
                        or _is_bus_expression(entry.name)
                        else "sheet_entry"
                    ),
                    "side": entry.side,
                    "distance_from_top": entry.distance_from_top,
                    "io_type": entry.io_type,
                },
            )
            for entry in sheet_symbol.entries
            if entry.name
        ],
    )


def _sheet_symbol_bus_nets(
    sheet_symbol: SchematicSheetSymbol,
    *,
    scope_id: str | None,
    wires: list[SchematicWire],
    sheet_path: str,
    sheet_depth: int,
    repeat_range: tuple[int, int] | None = None,
    resolved_names: dict[str, str] | None = None,
) -> list[Net]:
    nets: list[Net] = []
    symbol_id = _scoped_id(scope_id, sheet_symbol.id, fallback="sheet-symbol")
    for entry in sheet_symbol.entries:
        if (
            repeat_range is not None
            and (repeat_name := _repeat_entry_name(entry.name)) is not None
        ):
            name = f"{repeat_name}[{repeat_range[0]}..{repeat_range[1]}]"
        elif resolved_names and entry.name in resolved_names:
            name = resolved_names[entry.name]
        else:
            name = entry.name
        if not name:
            continue
        is_bus = _is_bus_expression(name)
        nets.append(
            Net(
                id=_scoped_id(symbol_id, entry.id, fallback="sheet-entry"),
                name=name,
                anchor=_sheet_entry_anchor(sheet_symbol, entry, wires=wires),
                is_global=False,
                extra_properties={
                    "kind": (
                        "hierarchical_bus_label" if is_bus else "hierarchical_label"
                    ),
                    "sheet_path": sheet_path,
                    "sheet_depth": sheet_depth,
                    "raw_name": name,
                    "contributes_name": is_bus,
                },
            )
        )
    return nets


def _wire_points(wires: list[SchematicWire]) -> list[tuple[Point2D, Point2D]]:
    segments: list[tuple[Point2D, Point2D]] = []
    for wire in wires:
        points = [_point(vertex) for vertex in wire.vertices]
        for start, end in zip(points, points[1:], strict=False):
            segments.append((start, end))
    return segments


def _point_on_any_wire(point: Point2D, wires: list[SchematicWire]) -> bool:
    for start, end in _wire_points(wires):
        if _point_on_segment(point, start, end):
            return True
    return False


class _PointDisjointSet:
    def __init__(self) -> None:
        self.parent: dict[Point2D, Point2D] = {}

    def add(self, point: Point2D) -> None:
        self.parent.setdefault(point, point)

    def find(self, point: Point2D) -> Point2D:
        self.add(point)
        root = self.parent[point]
        if root != point:
            root = self.find(root)
            self.parent[point] = root
        return root

    def union(self, left: Point2D, right: Point2D) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def _unique_points(points: list[Point2D]) -> list[Point2D]:
    unique: list[Point2D] = []
    for point in points:
        if any(_points_close(point, existing) for existing in unique):
            continue
        unique.append(point)
    return unique


def _find_point(points: list[Point2D], point: Point2D) -> Point2D:
    for candidate in points:
        if _points_close(point, candidate):
            return candidate
    return point


def _normalized_entry_name(name: str) -> str | None:
    stripped = _strip_control_chars(name)
    if not stripped:
        return None
    repeat_name = _repeat_entry_name(stripped)
    if repeat_name is not None:
        return repeat_name
    return stripped


def _normalized_connection_name(name: str) -> str:
    return _NON_ALNUM_RE.sub("", _strip_control_chars(name).lower())


def _is_generic_connection_name(name: str) -> bool:
    normalized = _normalized_connection_name(name)
    if not normalized:
        return True
    return normalized in _GENERIC_CONNECTION_NAMES


def _connected_entry_name_map(
    doc: AltiumSchematic,
    sheet_symbol: SchematicSheetSymbol,
) -> dict[str, str]:
    wires = doc.wires
    entry_points = {
        entry.name: _sheet_entry_anchor(sheet_symbol, entry, wires=wires)
        for entry in sheet_symbol.entries
        if entry.name and not _is_bus_expression(entry.name)
    }
    if not entry_points:
        return {}

    label_points = [
        _point(item.location)
        for item in doc.net_labels
        if item.text and not _is_bus_expression(item.text)
    ]
    power_points = [_point(item.location) for item in doc.power_objects if item.text]
    other_entry_points = [
        _sheet_entry_anchor(other_sheet_symbol, other_entry, wires=wires)
        for other_sheet_symbol in doc.sheet_symbols
        for other_entry in other_sheet_symbol.entries
        if other_entry.name and not _is_bus_expression(other_entry.name)
    ]
    wire_points = [
        point
        for wire in wires
        for point in [_point(vertex) for vertex in wire.vertices]
    ]
    all_points = _unique_points(
        [
            *entry_points.values(),
            *other_entry_points,
            *label_points,
            *power_points,
            *wire_points,
        ]
    )

    dsu = _PointDisjointSet()
    for point in all_points:
        dsu.add(point)
    for start, end in _wire_points(wires):
        segment_points = [
            point for point in all_points if _point_on_segment(point, start, end)
        ]
        for left, right in zip(segment_points, segment_points[1:], strict=False):
            dsu.union(left, right)

    names_by_root: dict[Point2D, str] = {}
    for item in doc.net_labels:
        if not item.text or _is_bus_expression(item.text):
            continue
        root = dsu.find(_find_point(all_points, _point(item.location)))
        names_by_root.setdefault(root, item.text)
    for item in doc.power_objects:
        if not item.text:
            continue
        root = dsu.find(_find_point(all_points, _point(item.location)))
        names_by_root.setdefault(root, item.text)

    entry_names_by_root: dict[Point2D, list[tuple[str, str]]] = {}
    for other_sheet_symbol in doc.sheet_symbols:
        for other_entry in other_sheet_symbol.entries:
            normalized_name = _normalized_entry_name(other_entry.name)
            if (
                normalized_name is None
                or _is_bus_expression(normalized_name)
                or not other_entry.name
            ):
                continue
            root = dsu.find(
                _find_point(
                    all_points,
                    _sheet_entry_anchor(other_sheet_symbol, other_entry, wires=wires),
                )
            )
            entry_names_by_root.setdefault(root, []).append(
                (other_sheet_symbol.id or "", normalized_name)
            )

    mapping: dict[str, str] = {}
    for name, point in entry_points.items():
        root = dsu.find(_find_point(all_points, point))
        resolved_name = names_by_root.get(root)
        if resolved_name:
            mapping[name] = resolved_name
            continue
        normalized_name = _normalized_entry_name(name)
        if normalized_name is None:
            continue
        candidates = {
            candidate_name
            for owner_id, candidate_name in entry_names_by_root.get(root, [])
            if owner_id != (sheet_symbol.id or "")
            and candidate_name != normalized_name
            and _is_generic_connection_name(normalized_name)
            and not _is_generic_connection_name(candidate_name)
        }
        if not candidates:
            continue
        mapping[name] = sorted(
            candidates,
            key=lambda candidate: (
                0
                if candidate.endswith(normalized_name)
                or candidate.startswith(normalized_name)
                else 1,
                -len(candidate),
                candidate,
            ),
        )[0]
    return mapping


def _point_on_any_bus(point: Point2D, buses: list[SchematicBus]) -> bool:
    for bus in buses:
        points = [_point(vertex) for vertex in bus.vertices]
        for start, end in zip(points, points[1:], strict=False):
            if _point_on_segment(point, start, end):
                return True
    return False


def _bus_entries(
    wires: list[SchematicWire],
    buses: list[SchematicBus],
    *,
    scope_id: str | None,
) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for wire in wires:
        points = [_point(vertex) for vertex in wire.vertices]
        if len(points) < 2:
            continue
        left = points[0]
        right = points[-1]
        left_on_bus = _point_on_any_bus(left, buses)
        right_on_bus = _point_on_any_bus(right, buses)
        if left_on_bus == right_on_bus:
            continue
        bus_point = left if left_on_bus else right
        wire_point = right if left_on_bus else left
        entries.append(
            {
                "id": _scoped_id(scope_id, wire.id, fallback="bus-entry"),
                "endpoints": (bus_point, wire_point),
            }
        )
    return entries


def _port_anchor(port: SchematicPort, wires: list[SchematicWire]) -> Point2D:
    left = _point(port.location)
    right = _point((port.location[0] + port.width, port.location[1]))
    if port.connected_end == 1:
        return left
    if port.connected_end == 2:
        return right
    if _point_on_any_wire(left, wires):
        return left
    if _point_on_any_wire(right, wires):
        return right
    return right if port.width else left


def _port_pin(
    port: SchematicPort,
    wires: list[SchematicWire],
    *,
    scope_id: str | None,
    port_name_map: dict[str, str],
    local_net_suffix: str | None,
) -> Pin:
    port_id = _scoped_id(scope_id, port.id, fallback="sheet-port")
    mapped_name = _local_net_name(
        port.name,
        port_name_map=port_name_map,
        local_net_suffix=local_net_suffix,
    )
    return Pin(
        id=port_id,
        name=mapped_name,
        location=_port_anchor(port, wires),
        extra_properties={
            "kind": "bus_pin" if _is_bus_expression(mapped_name) else "sheet_port",
            "location": _point(port.location),
            "width": port.width,
            "height": port.height,
            "connected_end": port.connected_end,
            "alignment": port.alignment,
        },
    )


def _port_nets(
    items: list[SchematicPort],
    wires: list[SchematicWire],
    *,
    scope_id: str | None,
    sheet_path: str,
    sheet_depth: int,
    port_name_map: dict[str, str],
    local_net_suffix: str | None,
) -> list[Net]:
    nets: list[Net] = []
    for item in items:
        mapped_name = _local_net_name(
            item.name,
            port_name_map=port_name_map,
            local_net_suffix=local_net_suffix,
        )
        if not mapped_name:
            continue
        is_bus = _is_bus_expression(mapped_name)
        nets.append(
            Net(
                id=_scoped_id(scope_id, item.id, fallback="sheet-port"),
                name=mapped_name,
                anchor=_port_anchor(item, wires),
                is_global=False,
                extra_properties={
                    "kind": (
                        "hierarchical_bus_label" if is_bus else "hierarchical_label"
                    ),
                    "sheet_path": sheet_path,
                    "sheet_depth": sheet_depth,
                    "raw_name": mapped_name,
                    "contributes_name": is_bus,
                },
            )
        )
    return nets


def _sheet_name(doc: AltiumSchematic, *, fallback: str) -> str:
    return (
        doc.header_parameters.get("SHEETNAME")
        or doc.additional_parameters.get("SHEETNAME")
        or fallback
    )


def convert_schematic_il_to_sheet(
    doc: AltiumSchematic,
    *,
    sheet_id: str | None = None,
    sheet_name: str | None = None,
    sheet_names: list[str] | None = None,
    source_path: str | None = None,
    sheet_depth: int = 0,
    child_sheet_ids_by_symbol_index: dict[int, str] | None = None,
    symbol_overrides_by_source_index: dict[int, list[Symbol]] | None = None,
    symbol_net_name_overrides_by_source_index: dict[int, dict[str, str]] | None = None,
    refdes_suffix: str | None = None,
    local_net_suffix: str | None = None,
    port_name_map: dict[str, str] | None = None,
    scope_ids: bool = False,
) -> Sheet:
    child_sheet_ids_by_symbol_index = child_sheet_ids_by_symbol_index or {}
    symbol_overrides_by_source_index = symbol_overrides_by_source_index or {}
    symbol_net_name_overrides_by_source_index = (
        symbol_net_name_overrides_by_source_index or {}
    )
    port_name_map = port_name_map or {}
    resolved_sheet_id = sheet_id or doc.id or "altium-sheet"
    resolved_sheet_name = sheet_name or _sheet_name(
        doc, fallback=doc.id or "altium-sheet"
    )
    resolved_sheet_names = sheet_names or [resolved_sheet_name]
    resolved_sheet_path = _sheet_path(resolved_sheet_names)
    scope_prefix = resolved_sheet_id if scope_ids else None
    top_sheet = Sheet(
        id=resolved_sheet_id,
        name=resolved_sheet_name,
        extra_properties={
            "sheet_path": resolved_sheet_path,
            "sheet_depth": sheet_depth,
            "bus_aliases": {},
            "bus_entries": [],
            **({"source_path": source_path} if source_path else {}),
        },
    )
    sheet_attachment_points = [
        *(_point(item.location) for item in doc.junctions),
        *(_point(item.location) for item in doc.net_labels),
        *(_point(item.location) for item in doc.power_objects if item.text),
    ]

    top_sheet.symbols.extend(
        _component_symbol(
            component,
            scope_id=scope_prefix,
            refdes_suffix=refdes_suffix,
            sheet_wires=doc.wires,
            attachment_points=sheet_attachment_points,
        )
        for component in doc.components
    )

    for sheet_symbol in doc.sheet_symbols:
        source_index = sheet_symbol.source_index or -1
        repeat_range = (
            _repeat_sheet_range(sheet_symbol.sheet_name)
            if sheet_symbol.sheet_name
            else None
        )
        overrides = symbol_overrides_by_source_index.get(source_index)
        if overrides is not None:
            top_sheet.symbols.extend(overrides)
        else:
            top_sheet.symbols.append(
                _sheet_symbol_symbol(
                    sheet_symbol,
                    scope_id=scope_prefix,
                    wires=doc.wires,
                    child_sheet_id=child_sheet_ids_by_symbol_index.get(source_index),
                )
            )
        top_sheet.nets.extend(
            _sheet_symbol_bus_nets(
                sheet_symbol,
                scope_id=scope_prefix,
                wires=doc.wires,
                sheet_path=resolved_sheet_path,
                sheet_depth=sheet_depth,
                repeat_range=repeat_range,
                resolved_names=symbol_net_name_overrides_by_source_index.get(
                    source_index
                ),
            )
        )

    top_sheet.pins.extend(
        _port_pin(
            port,
            doc.wires,
            scope_id=scope_prefix,
            port_name_map=port_name_map,
            local_net_suffix=local_net_suffix,
        )
        for port in doc.ports
        if port.name
    )
    top_sheet.wires.extend(_wire_segments(doc.wires, scope_id=scope_prefix))
    top_sheet.wires.extend(_bus_segments(doc.buses, scope_id=scope_prefix))
    top_sheet.extra_properties["bus_entries"] = _bus_entries(
        doc.wires,
        doc.buses,
        scope_id=scope_prefix,
    )
    top_sheet.junctions.extend(_junctions(doc.junctions, scope_id=scope_prefix))
    top_sheet.nets.extend(
        _nets_from_labels(
            doc.net_labels,
            scope_id=scope_prefix,
            sheet_path=resolved_sheet_path,
            sheet_depth=sheet_depth,
            port_name_map=port_name_map,
            local_net_suffix=local_net_suffix,
        )
    )
    top_sheet.nets.extend(
        _port_nets(
            doc.ports,
            doc.wires,
            scope_id=scope_prefix,
            sheet_path=resolved_sheet_path,
            sheet_depth=sheet_depth,
            port_name_map=port_name_map,
            local_net_suffix=local_net_suffix,
        )
    )
    top_sheet.nets.extend(
        _nets_from_power(
            doc.power_objects,
            scope_id=scope_prefix,
            sheet_path=resolved_sheet_path,
        )
    )

    for component in doc.components:
        component_scope = (
            _scoped_id(resolved_sheet_id, component.id, fallback="component")
            if scope_ids
            else None
        )
        top_sheet.wires.extend(
            _wire_segments(component.wires, scope_id=component_scope)
        )
        top_sheet.junctions.extend(
            _junctions(component.junctions, scope_id=component_scope)
        )
        top_sheet.nets.extend(
            _nets_from_labels(
                component.net_labels,
                scope_id=component_scope,
                sheet_path=resolved_sheet_path,
                sheet_depth=sheet_depth,
                port_name_map=port_name_map,
                local_net_suffix=local_net_suffix,
            )
        )
        top_sheet.nets.extend(
            _nets_from_power(
                component.power_objects,
                scope_id=component_scope,
                sheet_path=resolved_sheet_path,
            )
        )

    return top_sheet


def convert_schematic_il_to_hl(doc: AltiumSchematic) -> Schematic:
    top_sheet = convert_schematic_il_to_sheet(doc)
    return Schematic(
        top_sheet_id=top_sheet.id,
        sheets=[top_sheet],
        extra_properties={"netlist_naming_policy": dict(_NETLIST_NAMING_POLICY)},
    )


def _sheet_id_for_path(path: Path, root_dir: Path) -> str:
    try:
        return path.resolve().relative_to(root_dir.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _resolve_child_sheet_path(parent_path: Path, file_name: str) -> Path | None:
    if not file_name:
        return None
    candidate = (parent_path.parent / file_name).resolve()
    if candidate.exists():
        return candidate
    if candidate.suffix:
        lowered = candidate.name.lower()
        for sibling in parent_path.parent.iterdir():
            if sibling.is_file() and sibling.name.lower() == lowered:
                return sibling.resolve()
        return None
    with_suffix = candidate.with_suffix(".SchDoc")
    if with_suffix.exists():
        return with_suffix
    lowered = with_suffix.name.lower()
    for sibling in parent_path.parent.iterdir():
        if sibling.is_file() and sibling.name.lower() == lowered:
            return sibling.resolve()
    return None


def read_altium_schematic_to_hl(path: Path) -> Schematic:
    root_path = path.resolve()
    docs_by_path: dict[Path, AltiumSchematic] = {}
    sheets: list[Sheet] = []
    unresolved_child_sheets: set[str] = set()

    def _load_doc(current_path: Path) -> AltiumSchematic:
        resolved_path = current_path.resolve()
        doc = docs_by_path.get(resolved_path)
        if doc is None:
            doc = convert_ll_to_il(SchDocCodec.read(resolved_path))
            docs_by_path[resolved_path] = doc
        return doc

    def _visit(
        current_path: Path,
        *,
        current_sheet_id: str,
        current_sheet_name: str,
        current_sheet_names: list[str],
        current_sheet_depth: int,
        refdes_suffix: str | None = None,
        local_net_suffix: str | None = None,
        port_name_map: dict[str, str] | None = None,
        repeat_value: int | None = None,
    ) -> None:
        current_path = current_path.resolve()
        doc = _load_doc(current_path)
        child_sheet_ids_by_symbol_index: dict[int, str] = {}
        symbol_overrides_by_source_index: dict[int, list[Symbol]] = {}
        symbol_net_name_overrides_by_source_index: dict[int, dict[str, str]] = {}
        child_specs: list[
            tuple[
                Path,
                str,
                str,
                list[str],
                int,
                str | None,
                str | None,
                dict[str, str],
                int | None,
            ]
        ] = []

        for sheet_symbol in doc.sheet_symbols:
            source_index = sheet_symbol.source_index or -1
            child_path = _resolve_child_sheet_path(current_path, sheet_symbol.file_name)
            if child_path is None and sheet_symbol.file_name:
                unresolved_child_sheets.add(
                    (
                        f"{current_path.name}:{sheet_symbol.sheet_name or source_index}"
                        f" -> {sheet_symbol.file_name}"
                    )
                )
            resolved_name_map = _connected_entry_name_map(doc, sheet_symbol)
            symbol_net_name_overrides_by_source_index[source_index] = resolved_name_map
            repeat_range = (
                _repeat_sheet_range(sheet_symbol.sheet_name)
                if sheet_symbol.sheet_name
                else None
            )
            if repeat_range is not None and child_path is not None:
                start, end = repeat_range
                repeated_child_ids: list[str] = []
                for repeat_value in _repeat_values(start, end):
                    child_sheet_id = (
                        f"{current_sheet_id}/"
                        f"{source_index}:{child_path.stem}:{repeat_value}"
                    )
                    repeated_child_ids.append(child_sheet_id)
                    child_specs.append(
                        (
                            child_path,
                            child_sheet_id,
                            f"{child_path.stem} {_alpha_suffix(repeat_value)}",
                            [*current_sheet_names, child_path.stem],
                            current_sheet_depth + 1,
                            _alpha_suffix(repeat_value),
                            _alpha_suffix(repeat_value),
                            _port_name_map(
                                sheet_symbol,
                                repeat_value=repeat_value,
                                resolved_names=resolved_name_map,
                            ),
                            repeat_value,
                        )
                    )
                symbol_overrides_by_source_index[source_index] = [
                    _sheet_symbol_symbol(
                        sheet_symbol,
                        scope_id=current_sheet_id,
                        wires=doc.wires,
                        child_sheet_id=None,
                        repeat_range=repeat_range,
                        repeated_child_sheet_ids=repeated_child_ids,
                    )
                ]
                continue

            child_sheet_id: str | None = None
            if child_path is not None:
                child_sheet_id = f"{current_sheet_id}/{source_index}:{child_path.stem}"
                child_sheet_ids_by_symbol_index[source_index] = child_sheet_id
                child_specs.append(
                    (
                        child_path,
                        child_sheet_id,
                        child_path.stem,
                        [*current_sheet_names, child_path.stem],
                        current_sheet_depth + 1,
                        None,
                        None,
                        _port_name_map(
                            sheet_symbol,
                            repeat_value=None,
                            resolved_names=resolved_name_map,
                        ),
                        None,
                    )
                )

            symbol_overrides_by_source_index[source_index] = [
                _sheet_symbol_symbol(
                    sheet_symbol,
                    scope_id=current_sheet_id,
                    wires=doc.wires,
                    child_sheet_id=child_sheet_id,
                )
            ]

        sheets.append(
            convert_schematic_il_to_sheet(
                doc,
                sheet_id=current_sheet_id,
                sheet_name=current_sheet_name,
                sheet_names=current_sheet_names,
                source_path=str(current_path),
                sheet_depth=current_sheet_depth,
                child_sheet_ids_by_symbol_index=child_sheet_ids_by_symbol_index,
                symbol_overrides_by_source_index=symbol_overrides_by_source_index,
                symbol_net_name_overrides_by_source_index=(
                    symbol_net_name_overrides_by_source_index
                ),
                refdes_suffix=refdes_suffix,
                local_net_suffix=local_net_suffix,
                port_name_map=port_name_map,
                scope_ids=True,
            )
        )

        for (
            child_path,
            child_sheet_id,
            child_sheet_name,
            child_sheet_names,
            child_sheet_depth,
            child_refdes_suffix,
            child_local_net_suffix,
            child_port_name_map,
            child_repeat_value,
        ) in child_specs:
            _visit(
                child_path,
                current_sheet_id=child_sheet_id,
                current_sheet_name=child_sheet_name,
                current_sheet_names=child_sheet_names,
                current_sheet_depth=child_sheet_depth,
                refdes_suffix=child_refdes_suffix,
                local_net_suffix=child_local_net_suffix,
                port_name_map=child_port_name_map,
                repeat_value=child_repeat_value,
            )

    _visit(
        root_path,
        current_sheet_id=root_path.name,
        current_sheet_name=root_path.name,
        current_sheet_names=[root_path.name],
        current_sheet_depth=0,
        repeat_value=None,
    )
    return Schematic(
        top_sheet_id=root_path.name,
        sheets=sheets,
        extra_properties={
            "netlist_naming_policy": dict(_NETLIST_NAMING_POLICY),
            "unresolved_child_sheets": sorted(unresolved_child_sheets),
        },
    )


__all__ = [
    "convert_schematic_il_to_hl",
    "convert_schematic_il_to_sheet",
    "read_altium_schematic_to_hl",
]
