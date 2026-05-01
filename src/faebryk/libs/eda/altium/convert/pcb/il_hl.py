"""Convert Altium PCB IL into the shared HL PCB model."""

from __future__ import annotations

from math import cos, radians, sin

from faebryk.libs.eda.altium.models.pcb.il import (
    AltiumArc,
    AltiumFill,
    AltiumLayerType,
    AltiumPad,
    AltiumPadShape,
    AltiumPcb,
    AltiumRegion,
    AltiumTrack,
    AltiumVia,
    BoardLayer,
    LayerReference,
)
from faebryk.libs.eda.hl.models.pcb import (
    PCB,
    Circle,
    Collection,
    ConductiveGeometry,
    LayerID,
    NetID,
    Obround,
    Point2D,
    Polygon,
    Rectangle,
    RoundedRectangle,
    Segment,
    SourceID,
)


def _source_id(value: str | None) -> SourceID | None:
    if value is None:
        return None
    return SourceID(id=value)


def _point(x: float, y: float) -> Point2D:
    return Point2D(x=float(x), y=float(y))


def _net_id(name: str | None) -> NetID | None:
    if not name:
        return None
    return NetID(name=name)


def _layer_name(
    layer: int | LayerReference | None,
    board_layers_by_number: dict[int, BoardLayer],
    board_layers_by_id: dict[str, BoardLayer],
) -> str | None:
    if isinstance(layer, LayerReference):
        board_layer = board_layers_by_id.get(layer.layer_id)
        if board_layer is not None:
            return board_layer.name
        return layer.layer_id
    if isinstance(layer, int):
        board_layer = board_layers_by_number.get(layer)
        if board_layer is not None:
            return board_layer.name
        return f"Altium:{layer}"
    return None


def _layer_ids(
    names: list[str],
) -> list[LayerID]:
    return [LayerID(name=name) for name in names]


def _ordered_copper_layers(board: AltiumPcb) -> list[BoardLayer]:
    board_layers_by_id = {layer.id: layer for layer in board.board.layers}
    if board.board.copper_ordering.ordered_layer_ids:
        ordered = [
            board_layers_by_id[layer_id]
            for layer_id in board.board.copper_ordering.ordered_layer_ids
            if layer_id in board_layers_by_id
        ]
        if ordered:
            return ordered
    return sorted(
        (layer for layer in board.board.layers if layer.kind == AltiumLayerType.COPPER),
        key=lambda item: item.altium_layer_number,
    )


def _via_layer_names(
    primitive: AltiumVia,
    copper_layers: list[BoardLayer],
    board_layers_by_number: dict[int, BoardLayer],
) -> list[str]:
    if not copper_layers:
        start = board_layers_by_number.get(primitive.start_layer)
        end = board_layers_by_number.get(primitive.end_layer)
        return [layer.name for layer in (start, end) if layer is not None]

    index_by_number = {
        layer.altium_layer_number: index for index, layer in enumerate(copper_layers)
    }
    start_index = index_by_number.get(primitive.start_layer)
    end_index = index_by_number.get(primitive.end_layer)
    if start_index is None or end_index is None:
        fallback = [
            board_layers_by_number.get(primitive.start_layer),
            board_layers_by_number.get(primitive.end_layer),
        ]
        return [layer.name for layer in fallback if layer is not None]

    low = min(start_index, end_index)
    high = max(start_index, end_index)
    return [layer.name for layer in copper_layers[low : high + 1]]


def _pad_layer_names(
    primitive: AltiumPad,
    copper_layers: list[BoardLayer],
    board_layers_by_number: dict[int, BoardLayer],
    board_layers_by_id: dict[str, BoardLayer],
) -> list[str]:
    if primitive.is_tht and copper_layers:
        return [layer.name for layer in copper_layers]
    layer_name = _layer_name(
        primitive.layer, board_layers_by_number, board_layers_by_id
    )
    if layer_name is None:
        return []
    return [layer_name]


def _arc_endpoints(primitive: AltiumArc) -> tuple[Point2D, Point2D]:
    start_rad = radians(primitive.start_angle)
    end_rad = radians(primitive.end_angle)
    start = _point(
        primitive.center_x + primitive.radius * cos(start_rad),
        primitive.center_y + primitive.radius * sin(start_rad),
    )
    end = _point(
        primitive.center_x + primitive.radius * cos(end_rad),
        primitive.center_y + primitive.radius * sin(end_rad),
    )
    return start, end


def _pad_dimensions(primitive: AltiumPad) -> tuple[float, float]:
    return (
        float(
            max(
                primitive.top_size_x,
                primitive.mid_size_x,
                primitive.bot_size_x,
                1,
            )
        ),
        float(
            max(
                primitive.top_size_y,
                primitive.mid_size_y,
                primitive.bot_size_y,
                1,
            )
        ),
    )


def _rotate_local(point: Point2D, rotation_deg: float) -> Point2D:
    angle = radians(rotation_deg)
    return _point(
        point.x * cos(angle) - point.y * sin(angle),
        point.x * sin(angle) + point.y * cos(angle),
    )


def _octagon_vertices(
    width: float,
    height: float,
    rotation_deg: float,
) -> list[Point2D]:
    inset = min(width, height) * (1.0 / (2.0 + 2.0**0.5))
    half_width = width / 2.0
    half_height = height / 2.0
    vertices = [
        _point(-half_width + inset, -half_height),
        _point(half_width - inset, -half_height),
        _point(half_width, -half_height + inset),
        _point(half_width, half_height - inset),
        _point(half_width - inset, half_height),
        _point(-half_width + inset, half_height),
        _point(-half_width, half_height - inset),
        _point(-half_width, -half_height + inset),
    ]
    if rotation_deg == 0.0:
        return vertices
    return [_rotate_local(vertex, rotation_deg) for vertex in vertices]


def _pad_shape(primitive: AltiumPad):
    width, height = _pad_dimensions(primitive)
    rotation_deg = float(primitive.rotation)
    if primitive.shape == AltiumPadShape.RECT:
        return Rectangle(
            center=_point(0.0, 0.0),
            width=width,
            height=height,
            rotation_deg=rotation_deg,
        )
    if primitive.shape == AltiumPadShape.ROUND_RECT:
        return RoundedRectangle(
            center=_point(0.0, 0.0),
            width=width,
            height=height,
            corner_radius=min(width, height) / 4.0,
            rotation_deg=rotation_deg,
        )
    if primitive.shape == AltiumPadShape.OCTAGONAL:
        return Polygon(vertices=_octagon_vertices(width, height, rotation_deg))
    if width != height:
        return Obround(
            center=_point(0.0, 0.0),
            width=width,
            height=height,
            rotation_deg=rotation_deg,
        )
    return Circle(center=_point(0.0, 0.0), radius=max(width, height) / 2.0)


def convert_pcb_il_to_hl(doc: AltiumPcb) -> PCB:
    net_name_by_id = {net.id: net.name for net in doc.nets if net.id is not None}
    board_layers_by_number = {
        layer.altium_layer_number: layer for layer in doc.board.layers
    }
    board_layers_by_id = {layer.id: layer for layer in doc.board.layers}
    copper_layers = _ordered_copper_layers(doc)

    component_collections: dict[str, Collection] = {}
    root = PCB(
        id=_source_id(doc.board.id or doc.board.name or "altium-pcb"),
        extra_properties={"name": doc.board.name or ""},
    )

    for component in doc.components:
        if component.id is None:
            continue
        component_collection = Collection(
            id=_source_id(component.id),
            extra_properties={
                "refdes": component.designator,
                "name": component.footprint,
            },
        )
        component_collections[component.id] = component_collection
        root.collections.append(component_collection)

    for primitive in doc.primitives:
        if isinstance(primitive, AltiumPad):
            pad_owner = component_collections.get(primitive.component_id or "")
            if pad_owner is None:
                pad_owner = Collection(
                    id=_source_id(primitive.id or f"pad:{primitive.name}"),
                    extra_properties={"name": primitive.id or primitive.name},
                )
                root.collections.append(pad_owner)
            pad_terminal = Collection(
                id=_source_id(primitive.id or f"pad:{primitive.name}"),
                extra_properties={
                    "terminal_id": primitive.name,
                    "terminal_kind": "pcb_pad",
                },
                geometries=[
                    ConductiveGeometry(
                        shape=_pad_shape(primitive),
                        location=_point(primitive.x, primitive.y),
                        layers=_layer_ids(
                            _pad_layer_names(
                                primitive,
                                copper_layers,
                                board_layers_by_number,
                                board_layers_by_id,
                            )
                        ),
                        net=_net_id(net_name_by_id.get(primitive.net_id)),
                    )
                ],
            )
            pad_owner.collections.append(pad_terminal)
            continue

        if isinstance(primitive, AltiumTrack):
            layer_name = _layer_name(
                primitive.layer,
                board_layers_by_number,
                board_layers_by_id,
            )
            if layer_name is None:
                continue
            root.geometries.append(
                ConductiveGeometry(
                    shape=Segment(
                        start=_point(primitive.x1, primitive.y1),
                        end=_point(primitive.x2, primitive.y2),
                    ),
                    location=_point(0.0, 0.0),
                    layers=[LayerID(name=layer_name)],
                    net=_net_id(net_name_by_id.get(primitive.net_id)),
                )
            )
            continue

        if isinstance(primitive, AltiumVia):
            layer_names = _via_layer_names(
                primitive,
                copper_layers,
                board_layers_by_number,
            )
            if not layer_names:
                continue
            root.geometries.append(
                ConductiveGeometry(
                    shape=Circle(
                        center=_point(0.0, 0.0),
                        radius=float(max(primitive.diameter, 1)) / 2.0,
                    ),
                    location=_point(primitive.x, primitive.y),
                    layers=_layer_ids(layer_names),
                    net=_net_id(net_name_by_id.get(primitive.net_id)),
                )
            )
            continue

        if isinstance(primitive, AltiumRegion):
            layer_name = _layer_name(
                primitive.layer,
                board_layers_by_number,
                board_layers_by_id,
            )
            if layer_name is None or len(primitive.outline) < 3:
                continue
            root.geometries.append(
                ConductiveGeometry(
                    shape=Polygon(
                        vertices=[_point(x, y) for x, y in primitive.outline]
                    ),
                    location=_point(0.0, 0.0),
                    layers=[LayerID(name=layer_name)],
                    net=_net_id(net_name_by_id.get(primitive.net_id)),
                )
            )
            continue

        if isinstance(primitive, AltiumFill):
            layer_name = _layer_name(
                primitive.layer,
                board_layers_by_number,
                board_layers_by_id,
            )
            if layer_name is None:
                continue
            x1, x2 = sorted((primitive.x1, primitive.x2))
            y1, y2 = sorted((primitive.y1, primitive.y2))
            root.geometries.append(
                ConductiveGeometry(
                    shape=Polygon(
                        vertices=[
                            _point(x1, y1),
                            _point(x2, y1),
                            _point(x2, y2),
                            _point(x1, y2),
                        ]
                    ),
                    location=_point(0.0, 0.0),
                    layers=[LayerID(name=layer_name)],
                    net=_net_id(net_name_by_id.get(primitive.net_id)),
                )
            )
            continue

        if isinstance(primitive, AltiumArc):
            layer_name = _layer_name(
                primitive.layer,
                board_layers_by_number,
                board_layers_by_id,
            )
            if layer_name is None:
                continue
            start, end = _arc_endpoints(primitive)
            root.geometries.append(
                ConductiveGeometry(
                    shape=Segment(start=start, end=end),
                    location=_point(0.0, 0.0),
                    layers=[LayerID(name=layer_name)],
                    net=_net_id(net_name_by_id.get(primitive.net_id)),
                )
            )

    return root


__all__ = ["convert_pcb_il_to_hl"]
