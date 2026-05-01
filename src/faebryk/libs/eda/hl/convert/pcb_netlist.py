"""Convert PCB HL models to normalized netlists."""

from __future__ import annotations

from dataclasses import dataclass
from math import cos, isclose, radians, sin

from faebryk.libs.eda.hl.models.netlist import Net, Netlist, TerminalRef
from faebryk.libs.eda.hl.models.pcb import (
    PCB,
    Circle,
    Collection,
    ConductiveGeometry,
    Obround,
    Point2D,
    Polygon,
    Rectangle,
    RoundedRectangle,
    Segment,
    SourceID,
)

type PointKey = tuple[float, float]
type NodeKey = tuple[str, PointKey]

_EPSILON = 1e-9


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


@dataclass(frozen=True)
class _GeometryInstance:
    geometry: ConductiveGeometry
    path: tuple[Collection, ...]


def _point_key(point: Point2D) -> PointKey:
    return (point.x, point.y)


def _translate(point: Point2D, offset: Point2D) -> Point2D:
    return Point2D(x=point.x + offset.x, y=point.y + offset.y)


def _rotate(point: Point2D, angle_deg: float) -> Point2D:
    angle = radians(angle_deg)
    return Point2D(
        x=point.x * cos(angle) - point.y * sin(angle),
        y=point.x * sin(angle) + point.y * cos(angle),
    )


def _distance(left: Point2D, right: Point2D) -> float:
    dx = left.x - right.x
    dy = left.y - right.y
    return (dx * dx + dy * dy) ** 0.5


def _points_close(left: Point2D, right: Point2D) -> bool:
    return isclose(left.x, right.x, abs_tol=_EPSILON) and isclose(
        left.y,
        right.y,
        abs_tol=_EPSILON,
    )


def _point_on_segment(point: Point2D, start: Point2D, end: Point2D) -> bool:
    cross = (point.x - start.x) * (end.y - start.y) - (point.y - start.y) * (
        end.x - start.x
    )
    if not isclose(cross, 0.0, abs_tol=_EPSILON):
        return False
    return (
        min(start.x, end.x) - _EPSILON <= point.x <= max(start.x, end.x) + _EPSILON
        and min(start.y, end.y) - _EPSILON <= point.y <= max(start.y, end.y) + _EPSILON
    )


def _segment_param(point: Point2D, start: Point2D, end: Point2D) -> float:
    dx = end.x - start.x
    dy = end.y - start.y
    if abs(dx) >= abs(dy) and not isclose(dx, 0.0, abs_tol=_EPSILON):
        return (point.x - start.x) / dx
    if not isclose(dy, 0.0, abs_tol=_EPSILON):
        return (point.y - start.y) / dy
    return 0.0


def _point_in_polygon(point: Point2D, polygon: list[Point2D]) -> bool:
    if len(polygon) < 3:
        return False
    for start, end in zip(polygon, polygon[1:] + polygon[:1], strict=False):
        if _point_on_segment(point, start, end):
            return True
    inside = False
    prev = polygon[-1]
    for curr in polygon:
        intersects = ((curr.y > point.y) != (prev.y > point.y)) and (
            point.x
            < (prev.x - curr.x) * (point.y - curr.y) / (prev.y - curr.y) + curr.x
        )
        if intersects:
            inside = not inside
        prev = curr
    return inside


def _point_in_rotated_box(
    point: Point2D,
    center: Point2D,
    width: float,
    height: float,
    rotation_deg: float,
) -> tuple[bool, Point2D]:
    relative = Point2D(x=point.x - center.x, y=point.y - center.y)
    local = _rotate(relative, -rotation_deg)
    half_width = width / 2.0
    half_height = height / 2.0
    inside = (
        abs(local.x) <= half_width + _EPSILON and abs(local.y) <= half_height + _EPSILON
    )
    return inside, local


def _point_in_rounded_rectangle(point: Point2D, shape: RoundedRectangle) -> bool:
    inside, local = _point_in_rotated_box(
        point,
        shape.center,
        shape.width,
        shape.height,
        shape.rotation_deg,
    )
    if inside and shape.corner_radius <= _EPSILON:
        return True
    half_width = shape.width / 2.0
    half_height = shape.height / 2.0
    radius = min(shape.corner_radius, half_width, half_height)
    if radius <= _EPSILON:
        return inside
    clamped_x = max(abs(local.x) - (half_width - radius), 0.0)
    clamped_y = max(abs(local.y) - (half_height - radius), 0.0)
    return (clamped_x * clamped_x + clamped_y * clamped_y) <= (radius + _EPSILON) ** 2


def _point_in_obround(point: Point2D, shape: Obround) -> bool:
    _, local = _point_in_rotated_box(
        point,
        shape.center,
        shape.width,
        shape.height,
        shape.rotation_deg,
    )
    if isclose(shape.width, shape.height, abs_tol=_EPSILON):
        radius = shape.width / 2.0
        return (local.x * local.x + local.y * local.y) <= (radius + _EPSILON) ** 2
    if shape.width > shape.height:
        radius = shape.height / 2.0
        half_straight = max(shape.width - shape.height, 0.0) / 2.0
        if (
            abs(local.x) <= half_straight + _EPSILON
            and abs(local.y) <= radius + _EPSILON
        ):
            return True
        dx = abs(local.x) - half_straight
        return (dx * dx + local.y * local.y) <= (radius + _EPSILON) ** 2
    radius = shape.width / 2.0
    half_straight = max(shape.height - shape.width, 0.0) / 2.0
    if abs(local.y) <= half_straight + _EPSILON and abs(local.x) <= radius + _EPSILON:
        return True
    dy = abs(local.y) - half_straight
    return (local.x * local.x + dy * dy) <= (radius + _EPSILON) ** 2


def _walk_geometries(
    collection: Collection,
    *,
    path: tuple[Collection, ...] = (),
) -> list[_GeometryInstance]:
    own_path = (*path, collection)
    instances = [
        _GeometryInstance(geometry=geometry, path=own_path)
        for geometry in collection.geometries
    ]
    for child in collection.collections:
        instances.extend(_walk_geometries(child, path=own_path))
    return instances


def _shape_reference_point(geometry: ConductiveGeometry) -> Point2D:
    shape = geometry.shape
    if isinstance(shape, Circle):
        return _translate(shape.center, geometry.location)
    if isinstance(shape, Rectangle | RoundedRectangle | Obround):
        return _translate(shape.center, geometry.location)
    return geometry.location


def _shape_points(geometry: ConductiveGeometry) -> list[Point2D]:
    shape = geometry.shape
    if isinstance(shape, Circle):
        return [_translate(shape.center, geometry.location)]
    if isinstance(shape, Rectangle | RoundedRectangle | Obround):
        return [_translate(shape.center, geometry.location)]
    if isinstance(shape, Segment):
        return [
            _translate(shape.start, geometry.location),
            _translate(shape.end, geometry.location),
        ]
    if isinstance(shape, Polygon):
        return [_translate(vertex, geometry.location) for vertex in shape.vertices]
    return [geometry.location]


def _point_on_geometry(point: Point2D, geometry: ConductiveGeometry) -> bool:
    shape = geometry.shape
    if isinstance(shape, Circle):
        center = _translate(shape.center, geometry.location)
        return _distance(point, center) <= shape.radius + _EPSILON
    if isinstance(shape, Rectangle):
        center = _translate(shape.center, geometry.location)
        inside, _ = _point_in_rotated_box(
            point,
            center,
            shape.width,
            shape.height,
            shape.rotation_deg,
        )
        return inside
    if isinstance(shape, RoundedRectangle):
        return _point_in_rounded_rectangle(
            point,
            RoundedRectangle(
                center=_translate(shape.center, geometry.location),
                width=shape.width,
                height=shape.height,
                corner_radius=shape.corner_radius,
                rotation_deg=shape.rotation_deg,
            ),
        )
    if isinstance(shape, Obround):
        return _point_in_obround(
            point,
            Obround(
                center=_translate(shape.center, geometry.location),
                width=shape.width,
                height=shape.height,
                rotation_deg=shape.rotation_deg,
            ),
        )
    if isinstance(shape, Segment):
        start = _translate(shape.start, geometry.location)
        end = _translate(shape.end, geometry.location)
        return _point_on_segment(point, start, end)
    if isinstance(shape, Polygon):
        return _point_in_polygon(point, _shape_points(geometry))
    return _points_close(point, geometry.location)


def _covered_points(
    geometry: ConductiveGeometry,
    layer_name: str,
    interesting_points_by_layer: dict[str, list[Point2D]],
) -> list[Point2D]:
    layer_points = interesting_points_by_layer.get(layer_name, [])
    covered = [point for point in layer_points if _point_on_geometry(point, geometry)]
    if covered:
        return covered
    return [_shape_reference_point(geometry)]


def _source_id_value(source_id: SourceID | None) -> str | None:
    return source_id.id if source_id is not None else None


def _collection_display_name(collection: Collection) -> str | None:
    return (
        collection.extra_properties.get("refdes")
        or collection.extra_properties.get("name")
        or _source_id_value(collection.id)
    )


def _terminal_ref(path: tuple[Collection, ...]) -> TerminalRef | None:
    for index in range(len(path) - 1, -1, -1):
        terminal_collection = path[index]
        terminal_id = terminal_collection.extra_properties.get("terminal_id")
        if terminal_id is None:
            continue
        owner_collection = path[index - 1] if index > 0 else terminal_collection
        return TerminalRef(
            kind=str(
                terminal_collection.extra_properties.get("terminal_kind", "pcb_pad")
            ),
            owner_id=_source_id_value(owner_collection.id),
            owner_name=_collection_display_name(owner_collection),
            terminal_id=str(terminal_id),
        )
    return None


def _append_unique_point(
    interesting_points_by_layer: dict[str, list[Point2D]],
    layer_name: str,
    point: Point2D,
    dsu: _DisjointSet,
) -> None:
    points = interesting_points_by_layer.setdefault(layer_name, [])
    if not any(_points_close(point, existing) for existing in points):
        points.append(point)
    dsu.add((layer_name, _point_key(point)))


def convert_pcb_to_netlist(
    model: PCB,
    *,
    include_unconnected: bool = False,
) -> Netlist:
    dsu = _DisjointSet()
    interesting_points_by_layer: dict[str, list[Point2D]] = {}
    instances = _walk_geometries(model)

    for instance in instances:
        geometry = instance.geometry
        for layer in geometry.layers:
            for point in _shape_points(geometry):
                _append_unique_point(
                    interesting_points_by_layer,
                    layer.name,
                    point,
                    dsu,
                )
        reference_key = _point_key(_shape_reference_point(geometry))
        for left, right in zip(geometry.layers, geometry.layers[1:], strict=False):
            dsu.union((left.name, reference_key), (right.name, reference_key))

    for instance in instances:
        geometry = instance.geometry
        for layer in geometry.layers:
            covered = _covered_points(geometry, layer.name, interesting_points_by_layer)
            if not covered:
                continue
            anchor = _point_key(covered[0])
            for point in covered[1:]:
                dsu.union((layer.name, anchor), (layer.name, _point_key(point)))

    terminals_by_root: dict[NodeKey, set[TerminalRef]] = {}
    names_by_root: dict[NodeKey, set[str]] = {}
    unnamed_nets: list[Net] = []

    for instance in instances:
        geometry = instance.geometry
        terminal = _terminal_ref(instance.path)
        if terminal is not None:
            if geometry.layers:
                layer_name = geometry.layers[0].name
                root_point = _covered_points(
                    geometry,
                    layer_name,
                    interesting_points_by_layer,
                )[0]
                root = dsu.find((layer_name, _point_key(root_point)))
                terminals_by_root.setdefault(root, set()).add(terminal)
            elif include_unconnected:
                unnamed_nets.append(
                    Net(
                        id=(
                            f"unconnected:{terminal.owner_id or 'free'}:"
                            f"{terminal.terminal_id}"
                        ),
                        terminals=[terminal],
                    )
                )

        if geometry.net is None or not geometry.layers:
            continue
        for layer in geometry.layers:
            for point in _covered_points(
                geometry,
                layer.name,
                interesting_points_by_layer,
            ):
                root = dsu.find((layer.name, _point_key(point)))
                names_by_root.setdefault(root, set()).add(geometry.net.name)

    named_nets: dict[str, Net] = {}
    unnamed_counter = 1
    for root, terminals in terminals_by_root.items():
        aliases = sorted(names_by_root.get(root, set()))
        name = aliases[0] if aliases else None
        if name is not None:
            net = named_nets.setdefault(
                name,
                Net(id=name, name=name, aliases=[], terminals=[]),
            )
            net.aliases = sorted(set(net.aliases) | set(aliases[1:]))
            net.terminals = sorted(set(net.terminals) | terminals)
            continue
        if not include_unconnected:
            continue
        net_id = f"net-{unnamed_counter}"
        unnamed_counter += 1
        unnamed_nets.append(
            Net(
                id=net_id,
                terminals=sorted(terminals),
            )
        )

    nets = list(named_nets.values())
    nets.extend(unnamed_nets)
    return Netlist(nets=sorted(nets, key=lambda net: (net.name or "", net.id)))
