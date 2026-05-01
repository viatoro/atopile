"""Semantic Altium schematic IL models."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

type Point = tuple[int, int]


@dataclass(kw_only=True)
class SourceMetadata:
    id: str | None = None
    source_index: int | None = None
    source_order: int | None = None
    extra_properties: dict[str, object] = field(default_factory=dict)


@dataclass(kw_only=True)
class SchematicPin(SourceMetadata):
    name: str = ""
    designator: str = ""
    location: Point = (0, 0)
    length: int = 0
    electrical: int = 0
    orientation: int = 0
    show_name: bool = True
    show_designator: bool = True
    description: str = ""
    owner_part_id: int = 1
    owner_part_display_mode: int = 0


@dataclass(kw_only=True)
class SchematicParameter(SourceMetadata):
    name: str = ""
    text: str = ""
    location: Point = (0, 0)
    color: int = 0
    font_id: int = 0
    is_hidden: bool = False
    is_designator: bool = False
    read_only_state: int = 0
    owner_part_id: int = -1
    owner_part_display_mode: int = 0


@dataclass(kw_only=True)
class SchematicWire(SourceMetadata):
    vertices: list[Point] = field(default_factory=list)
    color: int = 0
    line_width: int = 0
    owner_part_id: int = -1
    owner_part_display_mode: int = 0


@dataclass(kw_only=True)
class SchematicBus(SourceMetadata):
    vertices: list[Point] = field(default_factory=list)
    color: int = 0
    line_width: int = 0
    owner_part_id: int = -1
    owner_part_display_mode: int = 0


@dataclass(kw_only=True)
class SchematicNetLabel(SourceMetadata):
    text: str = ""
    location: Point = (0, 0)
    color: int = 0
    font_id: int = 0
    owner_part_id: int = -1
    owner_part_display_mode: int = 0


@dataclass(kw_only=True)
class SchematicJunction(SourceMetadata):
    location: Point = (0, 0)
    color: int = 0
    owner_part_id: int = -1
    owner_part_display_mode: int = 0


@dataclass(kw_only=True)
class SchematicPowerObject(SourceMetadata):
    text: str = ""
    location: Point = (0, 0)
    orientation: int = 0
    style: int = 0
    color: int = 0
    font_id: int = 0
    show_net_name: bool = False
    owner_part_id: int = -1
    owner_part_display_mode: int = 0


@dataclass(kw_only=True)
class SchematicSheetEntry(SourceMetadata):
    side: int = 0
    distance_from_top: int = 0
    name: str = ""
    io_type: int = 0
    style: int = 0
    arrow_kind: str = ""
    harness_type: str = ""
    harness_color: int = 0
    font_id: int = 0
    color: int = 0
    area_color: int = 0
    text_color: int = 0
    text_style: str = ""
    owner_part_id: int = -1
    owner_part_display_mode: int = 0


@dataclass(kw_only=True)
class SchematicSheetSymbol(SourceMetadata):
    location: Point = (0, 0)
    x_size: int = 0
    y_size: int = 0
    is_mirrored: bool = False
    file_name: str = ""
    sheet_name: str = ""
    line_width: int = 0
    color: int = 0
    area_color: int = 0
    is_solid: bool = False
    show_hidden_fields: bool = False
    symbol_type: str = ""
    entries: list[SchematicSheetEntry] = field(default_factory=list)


@dataclass(kw_only=True)
class SchematicPort(SourceMetadata):
    location: Point = (0, 0)
    name: str = ""
    io_type: int = 0
    style: int = 0
    alignment: int = 0
    width: int = 0
    height: int = 0
    border_width: int = 0
    auto_size: bool = False
    connected_end: int = 0
    cross_reference: str = ""
    show_net_name: bool = False
    harness_type: str = ""
    harness_color: int = 0
    is_custom_style: bool = False
    font_id: int = 0
    color: int = 0
    area_color: int = 0
    text_color: int = 0
    owner_part_id: int = -1
    owner_part_display_mode: int = 0


@dataclass(kw_only=True)
class SchematicComponent(SourceMetadata):
    lib_reference: str = ""
    design_item_id: str = ""
    description: str = ""
    location: Point = (0, 0)
    orientation: int = 0
    current_part_id: int = 1
    part_count: int = 1
    display_mode_count: int = 1
    pins: list[SchematicPin] = field(default_factory=list)
    parameters: list[SchematicParameter] = field(default_factory=list)
    wires: list[SchematicWire] = field(default_factory=list)
    net_labels: list[SchematicNetLabel] = field(default_factory=list)
    junctions: list[SchematicJunction] = field(default_factory=list)
    power_objects: list[SchematicPowerObject] = field(default_factory=list)


@dataclass(kw_only=True)
class AltiumSchematic(SourceMetadata):
    header_parameters: dict[str, str] = field(default_factory=dict)
    additional_parameters: dict[str, str] = field(default_factory=dict)
    storage_data: bytes | None = None
    components: list[SchematicComponent] = field(default_factory=list)
    sheet_symbols: list[SchematicSheetSymbol] = field(default_factory=list)
    ports: list[SchematicPort] = field(default_factory=list)
    parameters: list[SchematicParameter] = field(default_factory=list)
    wires: list[SchematicWire] = field(default_factory=list)
    buses: list[SchematicBus] = field(default_factory=list)
    net_labels: list[SchematicNetLabel] = field(default_factory=list)
    junctions: list[SchematicJunction] = field(default_factory=list)
    power_objects: list[SchematicPowerObject] = field(default_factory=list)

    @property
    def all_pins(self) -> list[SchematicPin]:
        return [pin for component in self.components for pin in component.pins]

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        storage_data = payload.get("storage_data")
        if isinstance(storage_data, bytes):
            payload["storage_data"] = f"<{len(storage_data)} bytes>"
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)
