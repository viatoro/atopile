"""Low-level models mirroring the DeepPCB JSON board format.

Every field corresponds directly to the DeepPCB JSON schema.
Field names use snake_case; the file_ll codec handles camelCase mapping.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ShapeType(StrEnum):
    CIRCLE = "circle"
    RECTANGLE = "rectangle"
    POLYLINE = "polyline"
    POLYGON = "polygon"
    PATH = "path"
    POLYGON_WITH_HOLES = "polygonWithHoles"
    MULTI = "multi"


class ResolutionUnit(StrEnum):
    INCH = "inch"
    MIL = "mil"
    CM = "cm"
    MM = "mm"
    UM = "um"


class ComponentSide(StrEnum):
    FRONT = "FRONT"
    BACK = "BACK"


class LayerType(StrEnum):
    SIGNAL = "signal"
    POWER = "power"
    PCB = "pcb"
    MIXED = "mixed"
    JUMPER = "jumper"


class RuleSubjectType(StrEnum):
    NET_CLASS = "netClass"
    NET = "net"
    LAYER = "layer"
    PIN = "pin"


class RuleType(StrEnum):
    ALLOW_VIA_AT_SMD = "allowViaAtSmd"
    ALLOW_90_DEGREES = "allow90Degrees"
    ROTATE_FIRST = "rotateFirst"
    CLEARANCE = "clearance"
    ROUTING_DIRECTION = "routingDirection"
    PIN_CONNECTION_POINT = "pinConnectionPoint"
    DIRECT_CONNECTION = "directConnection"


class RoutingDirection(StrEnum):
    ANY = "any"
    VERTICAL = "vertical"
    HORIZONTAL = "horizontal"
    DIAGONAL = "diagonal"
    ANTI_DIAGONAL = "antiDiagonal"


class PinConnectionPointValue(StrEnum):
    CENTROID = "centroid"
    POSITION = "position"


class RuleDescription(StrEnum):
    DECOUPLING_CAPACITOR = "decouplingCapacitor"


class KeepoutItemType(StrEnum):
    WIRE = "wire"
    VIA = "via"
    PLACEMENT = "placement"


class PlaneKeepoutRule(StrEnum):
    WIRE = "wire"
    VIA = "via"
    PLACEMENT = "placement"
    NO_PROTECTION = "noProtection"


class WireType(StrEnum):
    SEGMENT = "segment"


class ConstraintType(StrEnum):
    DECOUPLED_BY = "decoupled_by"
    SUPPORTED_BY = "supported_by"
    HIGH_SPEED = "high_speed"
    MEDIUM_SPEED = "medium_speed"
    LOW_SPEED = "low_speed"
    ANALOG = "analog"
    POWER = "power"
    GROUND = "ground"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

Point = list[float | int]  # [x, y]
Polygon = list[Point]  # [[x1, y1], [x2, y2], ...]

# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


@dataclass
class Shape:
    """Union type for DeepPCB shapes, discriminated by ``type``.

    Only the fields relevant to the given ``type`` are populated;
    the rest remain ``None``.
    """

    type: ShapeType
    # circle
    center: Point | None = None
    radius: float | int | None = None
    # rectangle
    lower_left: Point | None = None
    upper_right: Point | None = None
    # polyline, polygon, path
    points: Polygon | None = None
    # path
    width: float | int | None = None
    # polygonWithHoles
    outline: Polygon | None = None
    holes: list[Polygon] | None = None
    # multi
    shapes: list[Shape] | None = None
    # Round-trip extension: corner radius for rounded rectangles
    corner_radius: float | int | None = None


# ---------------------------------------------------------------------------
# Resolution / Boundary
# ---------------------------------------------------------------------------


@dataclass
class Resolution:
    unit: ResolutionUnit
    value: int


@dataclass
class Boundary:
    shape: Shape
    clearance: int
    user_data: str | None = None


# ---------------------------------------------------------------------------
# Padstacks
# ---------------------------------------------------------------------------


@dataclass
class PadstackPad:
    shape: Shape
    layer_from: int
    layer_to: int


@dataclass
class PadstackHole:
    shape: Shape


@dataclass
class Padstack:
    id: str
    # Basic variant
    shape: Shape | None = None
    layers: list[int] | None = None
    allow_via: bool = False
    # Advanced variant
    pads: list[PadstackPad] | None = None
    hole: PadstackHole | None = None


# ---------------------------------------------------------------------------
# Component Definitions (footprints)
# ---------------------------------------------------------------------------


@dataclass
class Pin:
    id: str
    padstack: str  # references Padstack.id
    position: Point
    rotation: int


@dataclass
class Keepout:
    shape: Shape
    layer: int
    type: list[KeepoutItemType] | KeepoutItemType | None = None
    user_data: str | None = None


@dataclass
class ComponentDefinition:
    id: str
    pins: list[Pin]
    keepouts: list[Keepout]
    outline: Shape | None = None


# ---------------------------------------------------------------------------
# Components (placed instances)
# ---------------------------------------------------------------------------


@dataclass
class Component:
    id: str
    definition: str  # references ComponentDefinition.id
    position: Point
    rotation: int
    side: ComponentSide
    part_number: str | None = None
    protected: bool = False
    user_data: str | None = None


# ---------------------------------------------------------------------------
# Layers
# ---------------------------------------------------------------------------


@dataclass
class Layer:
    id: str
    keepouts: list[Keepout]
    display_name: str | None = None
    type: LayerType | None = None


# ---------------------------------------------------------------------------
# Nets
# ---------------------------------------------------------------------------


@dataclass
class Net:
    id: str
    pins: list[str]  # "{component-id}-{pin-id}"
    track_width: int | list[int] | None = None
    routing_priority: int | None = None
    forbidden_layers: list[int] | None = None


# ---------------------------------------------------------------------------
# Net Classes
# ---------------------------------------------------------------------------


@dataclass
class NetClass:
    id: str
    nets: list[str]
    clearance: int
    track_width: int | list[int]
    via_definition: str | None = None
    via_priority: list[list[str]] | None = None


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


@dataclass
class Wire:
    net_id: str
    layer: int
    start: Point
    end: Point
    width: int
    type: WireType = WireType.SEGMENT
    protected: bool = False
    user_data: str | None = None


@dataclass
class Via:
    net_id: str
    position: Point
    padstack: str  # references Padstack.id
    protected: bool = False
    user_data: str | None = None


# ---------------------------------------------------------------------------
# Planes
# ---------------------------------------------------------------------------


@dataclass
class Plane:
    net_id: str
    layer: int
    shape: Shape
    protected: bool = False
    keepout_rule: list[PlaneKeepoutRule] | PlaneKeepoutRule | None = None
    user_data: str | None = None
    # Not in schema but present in real data
    filled_shape: list[Shape] | None = None


# ---------------------------------------------------------------------------
# Net Preferences
# ---------------------------------------------------------------------------


@dataclass
class NetPreference:
    id: str
    nets: list[str]
    reduce_via_count_prio_coef: int = 1
    reduce_wire_length_prio_coef: int = 1
    reduce_acute_angle_prio_coef: int = 1


# ---------------------------------------------------------------------------
# Differential Pairs
# ---------------------------------------------------------------------------


@dataclass
class DifferentialPair:
    net_id1: str
    net_id2: str
    track_width: int | list[int] | None = None
    gap: int | None = None


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


@dataclass
class RuleSubject:
    id: str
    type: RuleSubjectType


@dataclass
class Rule:
    type: RuleType
    value: object  # varies by rule type
    subjects: list[RuleSubject] = field(default_factory=list)
    description: RuleDescription | str | None = None


# ---------------------------------------------------------------------------
# Board (top-level)
# ---------------------------------------------------------------------------


@dataclass
class DeepPCBBoard:
    name: str
    resolution: Resolution
    boundary: Boundary
    padstacks: list[Padstack]
    component_definitions: list[ComponentDefinition]
    components: list[Component]
    layers: list[Layer]
    nets: list[Net]
    net_classes: list[NetClass]
    planes: list[Plane]
    wires: list[Wire]
    vias: list[Via]
    via_definitions: list[str]  # padstack IDs
    net_preferences: list[NetPreference] = field(default_factory=list)
    differential_pairs: list[DifferentialPair] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Constraints (separate file)
# ---------------------------------------------------------------------------


@dataclass
class DecouplingTarget:
    type: ConstraintType
    targets: list[str]


@dataclass
class NetTypeConstraint:
    type: ConstraintType
    targets: list[str]


@dataclass
class DeepPCBConstraints:
    decoupling_constraints: dict[str, list[DecouplingTarget]]
    net_type_constraints: list[NetTypeConstraint]
