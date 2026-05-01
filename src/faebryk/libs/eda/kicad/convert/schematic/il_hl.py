"""Convert KiCad schematic IL objects into the shared HL schematic model."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from sexpdata import Symbol as SexpSymbol
from sexpdata import dumps as sexp_dumps
from sexpdata import loads as sexp_loads

from faebryk.libs.eda.hl.models.schematic import (
    Junction,
    Net,
    Pin,
    Schematic,
    Sheet,
    Symbol,
    WireSegment,
)
from faebryk.libs.eda.kicad.convert.schematic.raw import (
    RawKicadSchematic,
    RawSymbolInstance,
    read_raw_kicad_schematic,
)
from faebryk.libs.kicad.fileformats import kicad

type Point2D = tuple[float, float]

_SYMBOL_UNIT_RE = re.compile(r"_(\d+)_(\d+)$")
_BUS_EXPR_RE = re.compile(r".*((?<!~)\{[^{}]+\}|\[[^\[\]]+\])")
_PIN_NAMES_HIDE_RE = re.compile(r"\(pin_names\s+hide\)")
_SCHEMATIC_TOP_LEVEL_KEYS = {
    "version",
    "generator",
    "paper",
    "uuid",
    "lib_symbols",
    "title_block",
    "junction",
    "wire",
    "bus",
    "bus_entry",
    "label",
    "global_label",
    "symbol",
    "sheet",
}


def _point(obj: kicad.pcb.Xy | kicad.pcb.Xyr) -> Point2D:
    return (float(obj.x), float(obj.y))


def _rotation_deg(obj: kicad.pcb.Xyr) -> float:
    return float(obj.r or 0.0)


@dataclass(frozen=True, slots=True)
class _Transform:
    x1: int
    y1: int
    x2: int
    y2: int

    def apply(self, x: float, y: float) -> Point2D:
        return (
            self.x1 * x + self.y1 * y,
            self.x2 * x + self.y2 * y,
        )

    def compose_incremental(self, step: _Transform) -> _Transform:
        return _Transform(
            x1=self.x1 * step.x1 + self.x2 * step.y1,
            y1=self.y1 * step.x1 + self.y2 * step.y1,
            x2=self.x1 * step.x2 + self.x2 * step.y2,
            y2=self.y1 * step.x2 + self.y2 * step.y2,
        )


_IDENTITY = _Transform(1, 0, 0, 1)
_ROTATIONS = {
    0: _IDENTITY,
    90: _Transform(0, 1, -1, 0),
    180: _Transform(-1, 0, 0, -1),
    270: _Transform(0, -1, 1, 0),
}
_MIRRORS = {
    "x": _Transform(1, 0, 0, -1),
    "y": _Transform(-1, 0, 0, 1),
}


def _symbol_transform(angle_deg: float, mirror: str | None) -> _Transform:
    angle = int(round(angle_deg)) % 360
    try:
        transform = _ROTATIONS[angle]
    except KeyError as exc:
        raise ValueError(f"Unsupported KiCad schematic rotation: {angle_deg}") from exc
    if mirror is None:
        return transform
    try:
        return transform.compose_incremental(_MIRRORS[mirror])
    except KeyError as exc:
        raise ValueError(f"Unsupported KiCad schematic mirror axis: {mirror}") from exc


def _abs_point(
    origin: kicad.pcb.Xyr,
    local: kicad.pcb.Xyr,
    *,
    mirror: str | None = None,
) -> Point2D:
    transform = _symbol_transform(_rotation_deg(origin), mirror)
    rx, ry = transform.apply(float(local.x), -float(local.y))
    return (float(origin.x) + rx, float(origin.y) + ry)


def _property_value(
    properties: list[kicad.schematic.Property], name: str
) -> str | None:
    for prop in properties:
        if prop.name == name:
            return prop.value
    return None


def _reference_for_sheet(
    raw_instance: RawSymbolInstance | None,
    sheet_id: str,
    fallback: str | None,
) -> str | None:
    if raw_instance is None:
        return fallback
    references_by_path = getattr(raw_instance, "references_by_path", None)
    if not isinstance(references_by_path, dict):
        return fallback
    exact = references_by_path.get(sheet_id)
    if isinstance(exact, str):
        return exact
    for path, reference in references_by_path.items():
        if not isinstance(path, str) or not isinstance(reference, str):
            continue
        if sheet_id.endswith(path):
            return reference
    return fallback


def _lib_symbol_by_name(
    schematic: kicad.schematic.KicadSch,
) -> dict[str, kicad.schematic.Symbol]:
    return {symbol.name: symbol for symbol in schematic.lib_symbols.symbols}


def _select_symbol_units(
    symbol: kicad.schematic.Symbol, instance: kicad.schematic.SymbolInstance
) -> list[kicad.schematic.SymbolUnit]:
    selected: list[kicad.schematic.SymbolUnit] = []
    wanted_unit = int(instance.unit or 0)
    wanted_convert = int(instance.convert) if instance.convert is not None else None
    for unit in symbol.symbols:
        match = _SYMBOL_UNIT_RE.search(unit.name)
        if match is None:
            selected.append(unit)
            continue
        unit_id = int(match.group(1))
        convert_id = int(match.group(2))
        # KiCad schematic symbols often have a shared "unit 0" block with common
        # pins and a per-instance block with the remaining pins. An instance of
        # unit 1 therefore needs both `_0_0` and `_1_1`.
        if wanted_unit not in (0, unit_id) and unit_id != 0:
            continue
        if wanted_convert is not None and convert_id not in (0, wanted_convert):
            continue
        selected.append(unit)
    return selected or list(symbol.symbols)


def _symbol_pins(
    lib_symbol: kicad.schematic.Symbol,
    instance: kicad.schematic.SymbolInstance,
    *,
    mirror: str | None = None,
) -> list[Pin]:
    pins: list[Pin] = []
    for unit in _select_symbol_units(lib_symbol, instance):
        for pin in unit.pins:
            pin_name = pin.number.number or pin.name.name
            if not pin_name:
                continue
            pins.append(
                Pin(
                    name=str(pin_name),
                    location=_abs_point(instance.at, pin.at, mirror=mirror),
                    extra_properties={
                        "pin_name": pin.name.name,
                    },
                )
            )
    return pins


def _sheet_name(sheet: kicad.schematic.Sheet) -> str | None:
    props = list(sheet.propertys)
    return (
        _property_value(props, "Sheet name")
        or _property_value(props, "Sheetname")
        or _property_value(props, "Name")
    )


def _sheet_file(sheet: kicad.schematic.Sheet) -> str | None:
    props = list(sheet.propertys)
    return _property_value(props, "Sheet file") or _property_value(props, "Sheetfile")


def _is_bus_expression(name: str) -> bool:
    return bool(_BUS_EXPR_RE.fullmatch(name))


def _sheet_path(names: list[str]) -> str:
    if not names:
        return "/"
    return "/" + "/".join(names) + "/"


def _canonical_local_name(sheet_names: list[str], raw_name: str) -> str:
    return f"{_sheet_path(sheet_names)}{raw_name}"


def _wire_kind(extra_properties: dict[str, object]) -> str:
    value = extra_properties.get("kind")
    return str(value) if isinstance(value, str) else "wire"


def _bus_entry_endpoints(entry: kicad.schematic.BusEntry) -> tuple[Point2D, Point2D]:
    start = _point(entry.at)
    end = (
        float(entry.at.x) + float(entry.size.x),
        float(entry.at.y) + float(entry.size.y),
    )
    return start, end


def _sheet_id(parent_sheet_id: str, child_uuid: str) -> str:
    if parent_sheet_id == "/":
        return f"/{child_uuid}/"
    return f"{parent_sheet_id}{child_uuid}/"


def _sexp_symbol_name(value: object) -> str | None:
    if isinstance(value, SexpSymbol):
        return value.value()
    return None


def _simplify_lib_symbol_unit(expr: list[object]) -> list[object]:
    simplified = expr[:2]
    for child in expr[2:]:
        if not isinstance(child, list) or not child:
            continue
        if _sexp_symbol_name(child[0]) == "pin":
            simplified.append(child)
    return simplified


def _simplify_lib_symbol(expr: list[object]) -> list[object]:
    simplified = expr[:2]
    for child in expr[2:]:
        if not isinstance(child, list) or not child:
            continue
        head = _sexp_symbol_name(child[0])
        if head == "symbol":
            simplified.append(_simplify_lib_symbol_unit(child))
            continue
        if head in {
            "property",
            "in_bom",
            "on_board",
            "power",
            "convert",
        }:
            simplified.append(child)
    return simplified


def _normalize_schematic_text_for_connectivity(text: str) -> str:
    normalized = _PIN_NAMES_HIDE_RE.sub("(pin_names (offset 0) hide)", text)
    root = sexp_loads(normalized)
    if not isinstance(root, list):
        return normalized
    rewritten: list[object] = []
    for item in root:
        if not isinstance(item, list) or not item:
            rewritten.append(item)
            continue
        head = _sexp_symbol_name(item[0])
        if head not in _SCHEMATIC_TOP_LEVEL_KEYS:
            continue
        if head != "lib_symbols":
            rewritten.append(item)
            continue
        simplified = [item[0]]
        for child in item[1:]:
            if (
                isinstance(child, list)
                and child
                and _sexp_symbol_name(child[0]) == "symbol"
            ):
                simplified.append(_simplify_lib_symbol(child))
        rewritten.append(simplified)
    return sexp_dumps(rewritten)


def _load_kicad_schematic(path: Path) -> kicad.schematic.KicadSch:
    try:
        return kicad.loads(kicad.schematic.SchematicFile, path).kicad_sch
    except ValueError:
        pass
    text = path.read_text(encoding="utf-8")
    normalized = _normalize_schematic_text_for_connectivity(text)
    return kicad.loads(kicad.schematic.SchematicFile, normalized).kicad_sch


def _collect_project_bus_aliases(
    path: Path,
    *,
    seen: set[Path] | None = None,
) -> dict[str, tuple[str, ...]]:
    resolved_path = path.resolve()
    seen_paths = seen if seen is not None else set()
    if resolved_path in seen_paths or not resolved_path.exists():
        return {}
    seen_paths.add(resolved_path)

    raw = read_raw_kicad_schematic(resolved_path)
    aliases = dict(raw.bus_aliases)

    schematic = _load_kicad_schematic(resolved_path)
    for sheet in schematic.sheets:
        child_file = _sheet_file(sheet)
        if child_file is None:
            continue
        child_path = (resolved_path.parent / child_file).resolve()
        aliases.update(_collect_project_bus_aliases(child_path, seen=seen_paths))

    return aliases


def _convert_single_sheet(
    *,
    path: Path,
    schematic: kicad.schematic.KicadSch,
    sheet_id: str,
    sheet_names: list[str],
    display_name: str | None,
    inherited_bus_aliases: dict[str, tuple[str, ...]] | None = None,
) -> tuple[Sheet, list[tuple[Path, str, list[str], str | None]]]:
    raw = read_raw_kicad_schematic(path) if path.exists() else RawKicadSchematic()
    lib_symbols_by_name = _lib_symbol_by_name(schematic)
    depth = len(sheet_names)
    bus_aliases = {
        **(inherited_bus_aliases or {}),
        **raw.bus_aliases,
    }
    hl_sheet = Sheet(
        id=sheet_id,
        name=display_name,
        extra_properties={
            "sheet_path": _sheet_path(sheet_names),
            "sheet_depth": depth,
            "source_path": str(path),
            "bus_aliases": bus_aliases,
            "bus_entries": [],
        },
    )

    for wire in schematic.wires:
        points = [_point(vertex) for vertex in wire.pts.xys]
        if len(points) >= 2:
            hl_sheet.wires.append(WireSegment(id=wire.uuid, points=points))

    for bus in schematic.buss:
        points = [_point(vertex) for vertex in bus.pts.xys]
        if len(points) >= 2:
            hl_sheet.wires.append(
                WireSegment(
                    id=bus.uuid,
                    points=points,
                    extra_properties={"kind": "bus"},
                )
            )

    for polyline in raw.polylines:
        if len(polyline.points) >= 2:
            hl_sheet.wires.append(
                WireSegment(
                    id=polyline.uuid,
                    points=list(polyline.points),
                    extra_properties={"kind": "bus"},
                )
            )

    bus_entries: list[dict[str, object]] = []
    for entry in schematic.bus_entrys:
        bus_point, wire_point = _bus_entry_endpoints(entry)
        bus_entries.append(
            {
                "id": entry.uuid,
                "endpoints": (bus_point, wire_point),
            }
        )
    hl_sheet.extra_properties["bus_entries"] = bus_entries

    for junction in schematic.junctions:
        hl_sheet.junctions.append(
            Junction(id=junction.uuid, location=_point(junction.at))
        )

    for label in schematic.labels:
        if _is_bus_expression(str(label.text)):
            hl_sheet.nets.append(
                Net(
                    id=label.uuid,
                    name=str(label.text),
                    anchor=_point(label.at),
                    is_global=False,
                    extra_properties={
                        "kind": "bus_label",
                        "sheet_path": _sheet_path(sheet_names),
                        "sheet_depth": depth,
                        "raw_name": str(label.text),
                        "contributes_name": True,
                    },
                )
            )
            continue
        hl_sheet.nets.append(
            Net(
                id=label.uuid,
                name=_canonical_local_name(sheet_names, str(label.text)),
                anchor=_point(label.at),
                is_global=False,
                extra_properties={
                    "kind": "label",
                    "sheet_path": _sheet_path(sheet_names),
                    "sheet_depth": depth,
                    "raw_name": str(label.text),
                },
            )
        )

    for label in schematic.global_labels:
        if _is_bus_expression(str(label.text)):
            hl_sheet.nets.append(
                Net(
                    id=label.uuid,
                    name=str(label.text),
                    anchor=_point(label.at),
                    is_global=True,
                    extra_properties={
                        "kind": "bus_global_label",
                        "sheet_path": _sheet_path(sheet_names),
                        "sheet_depth": -1,
                        "raw_name": str(label.text),
                        "contributes_name": True,
                        "global": True,
                    },
                )
            )
            continue
        hl_sheet.nets.append(
            Net(
                id=label.uuid,
                name=str(label.text),
                anchor=_point(label.at),
                is_global=True,
                extra_properties={
                    "kind": "global_label",
                    "sheet_path": _sheet_path(sheet_names),
                    "sheet_depth": -1,
                    "raw_name": str(label.text),
                    "global": True,
                },
            )
        )

    for label in raw.hierarchical_labels:
        is_bus = _is_bus_expression(label.text)
        hl_sheet.pins.append(
            Pin(
                id=label.uuid,
                name=label.text,
                location=label.at,
                extra_properties={"kind": "bus_pin" if is_bus else "sheet_pin"},
            )
        )
        hl_sheet.nets.append(
            Net(
                id=label.uuid,
                name=label.text
                if is_bus
                else _canonical_local_name(sheet_names, label.text),
                anchor=label.at,
                is_global=False,
                extra_properties={
                    "kind": "hierarchical_bus_label"
                    if is_bus
                    else "hierarchical_label",
                    "sheet_path": _sheet_path(sheet_names),
                    "sheet_depth": depth,
                    "raw_name": label.text,
                    "contributes_name": is_bus,
                },
            )
        )

    child_specs: list[
        tuple[Path, str, list[str], str | None, dict[str, tuple[str, ...]]]
    ] = []
    for sheet in schematic.sheets:
        child_name = _sheet_name(sheet)
        child_file = _sheet_file(sheet)
        child_id = _sheet_id(sheet_id, str(sheet.uuid))
        sheet_symbol = Symbol(
            id=str(sheet.uuid or "sheet"),
            kind="sheet",
            name=child_name,
            child_sheet_id=child_id,
            pins=[
                Pin(
                    id=pin.uuid,
                    name=str(pin.name),
                    location=_point(pin.at),
                    extra_properties={
                        "kind": "bus_pin"
                        if _is_bus_expression(str(pin.name))
                        else "sheet_pin"
                    },
                )
                for pin in sheet.pins
            ],
        )
        hl_sheet.symbols.append(sheet_symbol)
        if child_file is None:
            continue
        child_path = (path.parent / child_file).resolve()
        child_specs.append(
            (
                child_path,
                child_id,
                [*sheet_names, child_name or str(sheet.uuid)],
                child_name,
                bus_aliases,
            )
        )

    for instance in schematic.symbols:
        raw_instance = raw.symbols_by_uuid.get(instance.uuid)
        lib_symbol = lib_symbols_by_name.get(instance.lib_id)
        properties = list(instance.propertys)
        refdes = _reference_for_sheet(
            raw_instance,
            sheet_id,
            _property_value(properties, "Reference"),
        )
        value = _property_value(properties, "Value")
        pins = (
            _symbol_pins(
                lib_symbol,
                instance,
                mirror=raw_instance.mirror if raw_instance is not None else None,
            )
            if lib_symbol is not None
            else []
        )
        hl_sheet.symbols.append(
            Symbol(
                id=str(instance.uuid or instance.lib_id or "symbol"),
                name=value or instance.lib_id,
                refdes=refdes,
                pins=pins,
                extra_properties={
                    "power_symbol": bool(lib_symbol and lib_symbol.power),
                    "on_board": bool(instance.on_board),
                },
            )
        )
        if lib_symbol is not None and lib_symbol.power:
            for pin in pins:
                pin_name = pin.extra_properties.get("pin_name")
                if not isinstance(pin_name, str):
                    continue
                power_name = pin_name.strip()
                if not power_name or power_name.lower() in {"pwr", "~"}:
                    continue
                hl_sheet.nets.append(
                    Net(
                        name=power_name,
                        anchor=pin.location,
                        is_power=True,
                        is_global=True,
                        extra_properties={
                            "kind": "power",
                            "sheet_path": _sheet_path(sheet_names),
                            "sheet_depth": -1,
                            "raw_name": power_name,
                            "global": True,
                        },
                    )
                )

    return hl_sheet, child_specs


def read_kicad_schematic_to_hl(path: Path) -> Schematic:
    sheets: list[Sheet] = []
    project_bus_aliases = _collect_project_bus_aliases(path.resolve())

    def visit(
        current_path: Path,
        *,
        current_sheet_id: str,
        current_sheet_names: list[str],
        current_display_name: str | None,
        current_bus_aliases: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        schematic = _load_kicad_schematic(current_path)
        sheet, children = _convert_single_sheet(
            path=current_path,
            schematic=schematic,
            sheet_id=current_sheet_id,
            sheet_names=current_sheet_names,
            display_name=current_display_name
            or (schematic.title_block.title if schematic.title_block else None),
            inherited_bus_aliases=current_bus_aliases,
        )
        sheets.append(sheet)
        for (
            child_path,
            child_sheet_id,
            child_sheet_names,
            child_display_name,
            child_bus_aliases,
        ) in children:
            visit(
                child_path,
                current_sheet_id=child_sheet_id,
                current_sheet_names=child_sheet_names,
                current_display_name=child_display_name,
                current_bus_aliases=child_bus_aliases,
            )

    visit(
        path.resolve(),
        current_sheet_id="/",
        current_sheet_names=[],
        current_display_name=None,
        current_bus_aliases=project_bus_aliases,
    )
    return Schematic(top_sheet_id="/", sheets=sheets)


def convert_schematic_il_to_hl(schematic: kicad.schematic.KicadSch) -> Schematic:
    top_sheet, _ = _convert_single_sheet(
        path=Path("<memory>.kicad_sch"),
        schematic=schematic,
        sheet_id="/",
        sheet_names=[],
        display_name=schematic.title_block.title if schematic.title_block else None,
        inherited_bus_aliases=None,
    )
    return Schematic(top_sheet_id=top_sheet.id, sheets=[top_sheet])


__all__ = ["convert_schematic_il_to_hl", "read_kicad_schematic_to_hl"]
