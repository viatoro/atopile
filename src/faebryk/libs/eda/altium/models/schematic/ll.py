"""Low-level Altium schematic record models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

type Point = tuple[int, int]


class SchRecordType(IntEnum):
    COMPONENT = 1
    PIN = 2
    SYMBOL = 3
    LABEL = 4
    BEZIER = 5
    POLYLINE = 6
    POLYGON = 7
    ELLIPSE = 8
    PIE = 9
    ROUNDED_RECTANGLE = 10
    ELLIPTICAL_ARC = 11
    ARC = 12
    LINE = 13
    RECTANGLE = 14
    SHEET_SYMBOL = 15
    SHEET_ENTRY = 16
    POWER_OBJECT = 17
    PORT = 18
    NO_ERC = 22
    NET_LABEL = 25
    BUS = 26
    WIRE = 27
    TEXT_FRAME = 28
    JUNCTION = 29
    IMAGE = 30
    DOCUMENT_OPTIONS = 31
    DESIGNATOR = 34
    TEMPLATE = 39
    PARAMETER = 41
    PARAMETER_SET = 43
    IMPLEMENTATION_LIST = 44
    IMPLEMENTATION = 45
    MAP_DEFINER_LIST = 46
    MAP_DEFINER = 47
    IMPLEMENTATION_PARAMETERS = 48
    BLANKET = 225


@dataclass
class SchematicRecord:
    index: int
    record_type: int
    owner_index: int = -1
    owner_part_id: int = -1
    owner_part_display_mode: int = 0
    unique_id: str | None = None
    properties: dict[str, str] = field(default_factory=dict)


@dataclass
class ComponentRecord(SchematicRecord):
    record_type: int = field(init=False, default=SchRecordType.COMPONENT)
    lib_reference: str = ""
    design_item_id: str = ""
    description: str = ""
    location: Point = (0, 0)
    orientation: int = 0
    current_part_id: int = 1
    part_count: int = 1
    display_mode_count: int = 1


@dataclass
class PinRecord(SchematicRecord):
    record_type: int = field(init=False, default=SchRecordType.PIN)
    name: str = ""
    designator: str = ""
    location: Point = (0, 0)
    length: int = 0
    electrical: int = 0
    orientation: int = 0
    show_name: bool = True
    show_designator: bool = True
    description: str = ""


@dataclass
class ParameterRecord(SchematicRecord):
    record_type: int = SchRecordType.PARAMETER
    name: str = ""
    text: str = ""
    location: Point = (0, 0)
    color: int = 0
    font_id: int = 0
    is_hidden: bool = False
    is_designator: bool = False
    read_only_state: int = 0


@dataclass
class WireRecord(SchematicRecord):
    record_type: int = field(init=False, default=SchRecordType.WIRE)
    vertices: list[Point] = field(default_factory=list)
    color: int = 0
    line_width: int = 0


@dataclass
class BusRecord(SchematicRecord):
    record_type: int = field(init=False, default=SchRecordType.BUS)
    vertices: list[Point] = field(default_factory=list)
    color: int = 0
    line_width: int = 0


@dataclass
class NetLabelRecord(SchematicRecord):
    record_type: int = field(init=False, default=SchRecordType.NET_LABEL)
    text: str = ""
    location: Point = (0, 0)
    color: int = 0
    font_id: int = 0


@dataclass
class JunctionRecord(SchematicRecord):
    record_type: int = field(init=False, default=SchRecordType.JUNCTION)
    location: Point = (0, 0)
    color: int = 0


@dataclass
class PowerObjectRecord(SchematicRecord):
    record_type: int = field(init=False, default=SchRecordType.POWER_OBJECT)
    text: str = ""
    location: Point = (0, 0)
    orientation: int = 0
    style: int = 0
    color: int = 0
    font_id: int = 0
    show_net_name: bool = False


@dataclass
class SheetSymbolRecord(SchematicRecord):
    record_type: int = field(init=False, default=SchRecordType.SHEET_SYMBOL)
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


@dataclass
class SheetEntryRecord(SchematicRecord):
    record_type: int = field(init=False, default=SchRecordType.SHEET_ENTRY)
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


@dataclass
class PortRecord(SchematicRecord):
    record_type: int = field(init=False, default=SchRecordType.PORT)
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


@dataclass
class UnknownRecord(SchematicRecord):
    pass


@dataclass
class AltiumSchDoc:
    """Flat low-level SchDoc representation."""

    header_parameters: dict[str, str] = field(default_factory=dict)
    additional_parameters: dict[str, str] = field(default_factory=dict)
    records: list[SchematicRecord] = field(default_factory=list)
    storage_data: bytes | None = None
    raw_streams: dict[str, bytes] = field(default_factory=dict)

    @property
    def components(self) -> list[ComponentRecord]:
        return [r for r in self.records if isinstance(r, ComponentRecord)]

    @property
    def pins(self) -> list[PinRecord]:
        return [r for r in self.records if isinstance(r, PinRecord)]

    @property
    def parameters(self) -> list[ParameterRecord]:
        return [r for r in self.records if isinstance(r, ParameterRecord)]

    @property
    def wires(self) -> list[WireRecord]:
        return [r for r in self.records if isinstance(r, WireRecord)]

    @property
    def buses(self) -> list[BusRecord]:
        return [r for r in self.records if isinstance(r, BusRecord)]

    @property
    def net_labels(self) -> list[NetLabelRecord]:
        return [r for r in self.records if isinstance(r, NetLabelRecord)]

    @property
    def junctions(self) -> list[JunctionRecord]:
        return [r for r in self.records if isinstance(r, JunctionRecord)]

    @property
    def power_objects(self) -> list[PowerObjectRecord]:
        return [r for r in self.records if isinstance(r, PowerObjectRecord)]

    @property
    def sheet_symbols(self) -> list[SheetSymbolRecord]:
        return [r for r in self.records if isinstance(r, SheetSymbolRecord)]

    @property
    def sheet_entries(self) -> list[SheetEntryRecord]:
        return [r for r in self.records if isinstance(r, SheetEntryRecord)]

    @property
    def ports(self) -> list[PortRecord]:
        return [r for r in self.records if isinstance(r, PortRecord)]

    @property
    def unknown_records(self) -> list[UnknownRecord]:
        return [r for r in self.records if isinstance(r, UnknownRecord)]
