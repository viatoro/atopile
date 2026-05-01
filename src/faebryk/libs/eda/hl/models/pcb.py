"""Prototype HL PCB connectivity model."""

from dataclasses import dataclass, field


@dataclass(kw_only=True)
class Point2D:
    x: float
    y: float


@dataclass(kw_only=True)
class Shape2D:
    pass


@dataclass(kw_only=True)
class Circle(Shape2D):
    center: Point2D
    radius: float


@dataclass(kw_only=True)
class Rectangle(Shape2D):
    center: Point2D
    width: float
    height: float
    rotation_deg: float = 0.0


@dataclass(kw_only=True)
class RoundedRectangle(Shape2D):
    center: Point2D
    width: float
    height: float
    corner_radius: float
    rotation_deg: float = 0.0


@dataclass(kw_only=True)
class Obround(Shape2D):
    center: Point2D
    width: float
    height: float
    rotation_deg: float = 0.0


@dataclass(kw_only=True)
class Segment(Shape2D):
    start: Point2D
    end: Point2D


@dataclass(kw_only=True)
class Polygon(Shape2D):
    vertices: list[Point2D] = field(default_factory=list)


# -----------------------------------------------------------------------------
# Board outline primitives
# -----------------------------------------------------------------------------


@dataclass(kw_only=True)
class OutlineSegment:
    """A straight-line segment in the board outline."""

    start: Point2D
    end: Point2D


@dataclass(kw_only=True)
class OutlineArc:
    """A three-point arc in the board outline (KiCad style: start/mid/end)."""

    start: Point2D
    mid: Point2D
    end: Point2D


@dataclass(kw_only=True)
class OutlineCircle:
    """A full circle in the board outline (e.g. circular PCB)."""

    center: Point2D
    radius: float


@dataclass(kw_only=True)
class OutlineBezier:
    """A cubic bezier curve in the board outline (4 control points)."""

    p0: Point2D
    p1: Point2D
    p2: Point2D
    p3: Point2D


# -----------------------------------------------------------------------------


@dataclass(kw_only=True)
class LayerID:
    name: str


@dataclass(kw_only=True)
class NetID:
    name: str


@dataclass(kw_only=True)
class SourceID:
    id: str


@dataclass(kw_only=True)
class ConductiveGeometry:
    # TODO: model in 2.5D
    shape: Shape2D
    location: Point2D
    layers: list[LayerID] = field(default_factory=list)

    net: NetID | None = None

    # Filled polygon data (e.g. computed copper fills from zone filling).
    # Each entry is a Polygon representing a filled region.
    filled_shapes: list[Polygon] | None = None


@dataclass(kw_only=True)
class SourceMetadata:
    id: SourceID | None = None
    collection_id: SourceID | None = None
    extra_properties: dict[str, object] = field(default_factory=dict)


# -----------------------------------------------------------------------------


@dataclass(kw_only=True)
class Collection(SourceMetadata):
    """
    Collection of conductive elements.
    E.g footprint or group of footprints etc.
    """

    geometries: list[ConductiveGeometry] = field(default_factory=list)
    collections: list[Collection] = field(default_factory=list)
    rotation_deg: float = 0.0
    side: str = "FRONT"  # FRONT or BACK


@dataclass(kw_only=True)
class PCB(Collection):
    # Board outline as an ordered sequence of line segments and arcs.
    # Extracted from Edge.Cuts layer in KiCad, or equivalent in other EDA tools.
    outline: list[OutlineSegment | OutlineArc | OutlineCircle | OutlineBezier] = field(
        default_factory=list
    )
