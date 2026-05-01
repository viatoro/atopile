"""Altium-specific intermediate layer (IL) data model."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum, StrEnum

type Point = tuple[int, int]


class AltiumLayerType(StrEnum):
    """Semantic category for Altium layers."""

    COPPER = "copper"
    OVERLAY = "overlay"
    PASTE = "paste"
    MASK = "mask"
    MECHANICAL = "mechanical"
    UNKNOWN = "unknown"


class AltiumClassKind(StrEnum):
    """Class categories supported by `Classes6`."""

    NET = "net"
    COMPONENT = "component"
    PAD = "pad"
    LAYER = "layer"
    DIFF_PAIR = "diff_pair"
    POLYGON = "polygon"
    OTHER = "other"


class AltiumRuleKind(StrEnum):
    """Rule families tracked as typed variants in phase 1."""

    CLEARANCE = "clearance"
    WIDTH = "width"
    HOLE_SIZE = "hole_size"
    ROUTING_VIAS = "routing_vias"
    SOLDER_MASK_EXPANSION = "solder_mask_expansion"
    PASTE_MASK_EXPANSION = "paste_mask_expansion"
    POLYGON_CONNECT_STYLE = "polygon_connect_style"


class AltiumPolygonConnectStyle(StrEnum):
    DIRECT = "direct"
    RELIEF = "relief"
    NONE = "none"


class AltiumPrimitiveKind(StrEnum):
    PAD = "pad"
    TRACK = "track"
    VIA = "via"
    ARC = "arc"
    TEXT = "text"
    FILL = "fill"
    REGION = "region"


class AltiumPadShape(StrEnum):
    ROUND = "round"
    RECT = "rect"
    OCTAGONAL = "octagonal"
    ROUND_RECT = "round_rect"


@dataclass(kw_only=True)
class SourceMetadata:
    """Common metadata shared across IL entities for preservation and order."""

    id: str | None = None
    source_id: str | None = None
    source_order: int | None = None
    extra_properties: dict[str, object] = field(default_factory=dict)


@dataclass(kw_only=True)
class LayerReference:
    """Reference to a layer by ID or Altium name."""

    layer_id: str
    altium_layer_number: int | None = None
    altium_kind: AltiumLayerType = AltiumLayerType.UNKNOWN


@dataclass(kw_only=True)
class BoardLayer(SourceMetadata):
    """Typed layer definition used by the board configuration."""

    id: str
    name: str
    kind: AltiumLayerType
    altium_layer_number: int
    is_enabled: bool = True
    source_name: str | None = None


@dataclass(kw_only=True)
class BoardCopperOrdering:
    """Deterministic copper layer traversal order."""

    ordered_layer_ids: list[str] = field(default_factory=list)


@dataclass(kw_only=True)
class BoardOutlineSegment:
    """Board edge segment as straight edge or arc."""

    start: Point
    end: Point
    kind: str = "line"
    center: Point | None = None
    radius: float | None = None
    start_angle_deg: float | None = None
    end_angle_deg: float | None = None


@dataclass(kw_only=True)
class BoardConfig(SourceMetadata):
    """Top-level board-level configuration and stack definitions."""

    name: str | None = None
    board_thickness: int = 0
    layers: list[BoardLayer] = field(default_factory=list)
    copper_ordering: BoardCopperOrdering = field(
        default_factory=BoardCopperOrdering,
    )
    outline: list[BoardOutlineSegment] = field(default_factory=list)
    origin_x: int = 0
    origin_y: int = 0


@dataclass(kw_only=True)
class AltiumNet(SourceMetadata):
    """Typed net with stable, deterministic identity."""

    name: str


@dataclass(kw_only=True)
class AltiumComponent(SourceMetadata):
    """Typed component instance."""

    designator: str
    footprint: str
    x: int
    y: int
    rotation: float
    layer: int
    side: str = "top"
    name_on: bool = True
    comment_on: bool = False


@dataclass(kw_only=True)
class BasePrimitive(SourceMetadata):
    """Common fields for typed primitives."""

    primitive_kind: AltiumPrimitiveKind = AltiumPrimitiveKind.PAD
    layer: int | LayerReference | None = None
    net_id: str | None = None
    component_id: str | None = None


@dataclass(kw_only=True)
class AltiumPad(BasePrimitive):
    """Typed pad primitive."""

    name: str
    component_id: str | None = None
    net_id: str | None = None
    x: int = 0
    y: int = 0
    top_size_x: int = 0
    top_size_y: int = 0
    mid_size_x: int = 0
    mid_size_y: int = 0
    bot_size_x: int = 0
    bot_size_y: int = 0
    hole_size: int = 0
    shape: AltiumPadShape = AltiumPadShape.ROUND
    rotation: float = 0.0
    is_tht: bool = False
    plated: bool = True
    pad_mode: int = 0
    slot_size: int = 0
    slot_rotation: float = 0.0

    def __post_init__(self) -> None:
        self.primitive_kind = AltiumPrimitiveKind.PAD


@dataclass(kw_only=True)
class AltiumTrack(BasePrimitive):
    """Typed track primitive."""

    x1: int
    y1: int
    x2: int
    y2: int
    width: int
    net_id: str | None = None
    layer: int | LayerReference | None = None
    component_id: str | None = None

    def __post_init__(self) -> None:
        self.primitive_kind = AltiumPrimitiveKind.TRACK


@dataclass(kw_only=True)
class AltiumVia(BasePrimitive):
    """Typed via primitive."""

    x: int
    y: int
    net_id: str | None = None
    diameter: int = 0
    hole_size: int = 0
    start_layer: int = 1
    end_layer: int = 32

    def __post_init__(self) -> None:
        self.primitive_kind = AltiumPrimitiveKind.VIA


@dataclass(kw_only=True)
class AltiumArc(BasePrimitive):
    """Typed arc primitive."""

    center_x: int
    center_y: int
    radius: int
    start_angle: float
    end_angle: float
    width: int
    net_id: str | None = None
    layer: int | LayerReference | None = None
    component_id: str | None = None

    def __post_init__(self) -> None:
        self.primitive_kind = AltiumPrimitiveKind.ARC


@dataclass(kw_only=True)
class AltiumText(BasePrimitive):
    """Typed text primitive."""

    text: str
    x: int
    y: int
    height: int
    layer: int | LayerReference | None = None
    component_id: str | None = None
    rotation: float = 0.0
    net_id: str | None = None
    is_mirrored: bool = False
    stroke_width: int = 0
    stroke_font_type: int = 1
    is_comment: bool = False
    is_designator: bool = False
    font_type: int = 0
    is_bold: bool = False
    is_italic: bool = False
    font_name: str = ""
    is_inverted: bool = False
    is_inverted_rect: bool = False
    is_frame: bool = False
    is_offset_border: bool = False
    is_justification_valid: bool = False
    margin_border_width: int = 0
    textbox_rect_width: int = 0
    textbox_rect_height: int = 0
    text_offset_width: int = 0
    text_justification: int = 3

    def __post_init__(self) -> None:
        self.primitive_kind = AltiumPrimitiveKind.TEXT


@dataclass(kw_only=True)
class AltiumFill(BasePrimitive):
    """Typed fill primitive."""

    x1: int
    y1: int
    x2: int
    y2: int
    layer: int | LayerReference | None = None
    net_id: str | None = None
    component_id: str | None = None
    rotation: float = 0.0

    def __post_init__(self) -> None:
        self.primitive_kind = AltiumPrimitiveKind.FILL


@dataclass(kw_only=True)
class AltiumRegion(BasePrimitive):
    """Typed polygon region/copper-pour-like primitive."""

    outline: list[Point] = field(default_factory=list)
    net_id: str | None = None
    layer: int | LayerReference | None = None
    component_id: str | None = None
    holes: list[list[Point]] = field(default_factory=list)
    is_keepout: bool = False
    keepout_restrictions: int = 0

    def __post_init__(self) -> None:
        self.primitive_kind = AltiumPrimitiveKind.REGION


@dataclass(kw_only=True)
class AltiumClass(SourceMetadata):
    """Typed class membership entry."""

    kind: AltiumClassKind
    name: str
    members: list[str] = field(default_factory=list)


@dataclass(kw_only=True)
class AltiumClassNet(AltiumClass):
    kind: AltiumClassKind = field(default=AltiumClassKind.NET, init=False)


@dataclass(kw_only=True)
class AltiumClassComponent(AltiumClass):
    kind: AltiumClassKind = field(default=AltiumClassKind.COMPONENT, init=False)


@dataclass(kw_only=True)
class AltiumClassPad(AltiumClass):
    kind: AltiumClassKind = field(default=AltiumClassKind.PAD, init=False)


@dataclass(kw_only=True)
class AltiumClassLayer(AltiumClass):
    kind: AltiumClassKind = field(default=AltiumClassKind.LAYER, init=False)


@dataclass(kw_only=True)
class AltiumRule(SourceMetadata):
    """Base class for typed rule declarations."""

    name: str
    scope1: str | None = None
    scope2: str | None = None


@dataclass(kw_only=True)
class AltiumRuleClearance(AltiumRule):
    """Typed clearance rule."""

    kind: AltiumRuleKind = AltiumRuleKind.CLEARANCE
    gap: int = 0


@dataclass(kw_only=True)
class AltiumRuleWidth(AltiumRule):
    """Typed width rule."""

    kind: AltiumRuleKind = AltiumRuleKind.WIDTH
    min_limit: int = 0
    max_limit: int = 0
    preferred: int = 0


@dataclass(kw_only=True)
class AltiumRuleHoleSize(AltiumRule):
    """Typed hole-size rule."""

    kind: AltiumRuleKind = AltiumRuleKind.HOLE_SIZE
    min_limit: int = 0
    max_limit: int = 0


@dataclass(kw_only=True)
class AltiumRuleRoutingVias(AltiumRule):
    """Typed routing vias rule."""

    kind: AltiumRuleKind = AltiumRuleKind.ROUTING_VIAS
    width: int = 0
    min_width: int = 0
    max_width: int = 0
    hole_width: int = 0
    min_hole_width: int = 0
    max_hole_width: int = 0


@dataclass(kw_only=True)
class AltiumRuleSolderMaskExpansion(AltiumRule):
    """Typed solder-mask expansion rule."""

    kind: AltiumRuleKind = AltiumRuleKind.SOLDER_MASK_EXPANSION
    expansion: int = 0


@dataclass(kw_only=True)
class AltiumRulePasteMaskExpansion(AltiumRule):
    """Typed paste-mask expansion rule."""

    kind: AltiumRuleKind = AltiumRuleKind.PASTE_MASK_EXPANSION
    expansion: int = 0


@dataclass(kw_only=True)
class AltiumRulePolygonConnectStyle(AltiumRule):
    """Typed polygon connect style rule."""

    kind: AltiumRuleKind = AltiumRuleKind.POLYGON_CONNECT_STYLE
    connect_style: AltiumPolygonConnectStyle = AltiumPolygonConnectStyle.DIRECT
    air_gap_width: int = 0
    relief_conductor_width: int = 0
    relief_entries: int = 0


@dataclass
class AltiumPcb:
    """Aggregate root for an Altium PCB IL document."""

    board: BoardConfig = field(default_factory=BoardConfig)
    nets: list[AltiumNet] = field(default_factory=list)
    components: list[AltiumComponent] = field(default_factory=list)
    classes: list[AltiumClass] = field(default_factory=list)
    rules: list[AltiumRule] = field(default_factory=list)
    source_raw_streams: dict[str, bytes] = field(default_factory=dict, repr=False)
    source_semantic_fingerprint: str | None = field(default=None, repr=False)
    source_ll_semantic_fingerprint: str | None = field(default=None, repr=False)
    source_stream_fingerprints: dict[str, str] = field(default_factory=dict, repr=False)
    primitives: list[
        AltiumPad
        | AltiumTrack
        | AltiumVia
        | AltiumArc
        | AltiumText
        | AltiumFill
        | AltiumRegion
    ] = field(default_factory=list)

    def __post_init__(self) -> None:
        # ensure deterministic list-based identity order
        if not self.primitives:
            self.primitives = []
        if not self.nets:
            self.nets = []
        if not self.components:
            self.components = []
        if not self.classes:
            self.classes = []
        if not self.rules:
            self.rules = []

    @property
    def pad_primitives(self) -> list[AltiumPad]:
        """Primitives restricted to pads."""
        return [item for item in self.primitives if isinstance(item, AltiumPad)]

    @property
    def track_primitives(self) -> list[AltiumTrack]:
        """Primitives restricted to tracks."""
        return [item for item in self.primitives if isinstance(item, AltiumTrack)]

    @property
    def via_primitives(self) -> list[AltiumVia]:
        """Primitives restricted to vias."""
        return [item for item in self.primitives if isinstance(item, AltiumVia)]

    @property
    def class_ids(self) -> list[str]:
        """Stable class identifier list in order."""
        return [entry.id or "" for entry in self.classes if entry.id is not None]

    def to_dict(self) -> dict[str, object]:
        """Return a debug-friendly dictionary without serializer dependencies."""
        return _as_debug_dict(self)

    def to_json(self, *, indent: int | None = 2, sort_keys: bool = True) -> str:
        """Return deterministic JSON debug output."""
        return json.dumps(self.to_dict(), indent=indent, sort_keys=sort_keys)


def _as_debug_dict(value: object) -> object:
    """Convert dataclasses and enums to plain Python structures."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bytes):
        return {"__bytes_len__": len(value)}
    if isinstance(value, dict):
        return {key: _as_debug_dict(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_as_debug_dict(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_as_debug_dict(item) for item in value)
    if isinstance(value, set):
        return [_as_debug_dict(item) for item in sorted(value, key=repr)]
    if hasattr(value, "__dataclass_fields__"):
        data = {}
        for key, field_def in value.__class__.__dataclass_fields__.items():  # type: ignore[attr-defined]
            data[key] = _as_debug_dict(getattr(value, key))
        return data
    return value


__all__ = [
    "AltiumArc",
    "AltiumClass",
    "AltiumClassComponent",
    "AltiumClassLayer",
    "AltiumClassNet",
    "AltiumClassPad",
    "AltiumFill",
    "BoardCopperOrdering",
    "AltiumLayerType",
    "AltiumPcb",
    "AltiumPolygonConnectStyle",
    "AltiumPad",
    "AltiumPadShape",
    "AltiumPrimitiveKind",
    "AltiumRegion",
    "AltiumRule",
    "AltiumRuleClearance",
    "AltiumRuleHoleSize",
    "AltiumRuleKind",
    "AltiumRulePasteMaskExpansion",
    "AltiumRulePolygonConnectStyle",
    "AltiumRuleRoutingVias",
    "AltiumRuleSolderMaskExpansion",
    "AltiumRuleWidth",
    "AltiumTrack",
    "AltiumVia",
    "BoardConfig",
    "BoardLayer",
    "BoardOutlineSegment",
    "SourceMetadata",
    "LayerReference",
    "AltiumClassKind",
    "AltiumNet",
    "AltiumText",
    "AltiumComponent",
    "AltiumPcb",
]
