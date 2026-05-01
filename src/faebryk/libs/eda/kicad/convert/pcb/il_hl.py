"""Convert KiCad PCB IL objects into the shared HL PCB model."""

from __future__ import annotations

from math import cos, hypot, radians, sin

from faebryk.libs.eda.hl.models.pcb import (
    PCB,
    Circle,
    Collection,
    ConductiveGeometry,
    LayerID,
    NetID,
    Obround,
    OutlineArc,
    OutlineBezier,
    OutlineCircle,
    OutlineSegment,
    Point2D,
    Polygon,
    Rectangle,
    RoundedRectangle,
    Segment,
    SourceID,
)
from faebryk.libs.kicad.fileformats import kicad


def _point(x: float, y: float) -> Point2D:
    return Point2D(x=float(x), y=float(y))


def _rotation_deg(obj: kicad.pcb.Xyr) -> float:
    return float(obj.r or 0.0)


def _rotate(x: float, y: float, angle_deg: float) -> tuple[float, float]:
    angle = radians(angle_deg)
    return (
        x * cos(angle) - y * sin(angle),
        x * sin(angle) + y * cos(angle),
    )


def _abs_point(origin: kicad.pcb.Xyr, local: kicad.pcb.Xyr) -> Point2D:
    rx, ry = _rotate(float(local.x), float(local.y), _rotation_deg(origin))
    return _point(float(origin.x) + rx, float(origin.y) + ry)


def _absolute_rotation_deg(
    footprint_at: kicad.pcb.Xyr,
    local_at: kicad.pcb.Xyr,
) -> float:
    return _rotation_deg(footprint_at) + _rotation_deg(local_at)


def _net_name(
    board: kicad.pcb.KicadPcb, net_value: int | kicad.pcb.Net | None
) -> str | None:
    if net_value is None:
        return None
    if isinstance(net_value, int):
        for candidate in board.nets:
            if candidate.number == net_value:
                return candidate.name or None
        return None
    name = net_value.name
    if name:
        return str(name)
    return _net_name(board, net_value.number)


def _property_value(properties: list[kicad.pcb.Property], name: str) -> str | None:
    for prop in properties:
        if prop.name == name:
            return prop.value
    return None


def _layers_for_zone(zone: kicad.pcb.Zone) -> list[str]:
    layers = list(zone.layers)
    if layers:
        return layers
    layer = zone.layer
    return [layer] if layer else []


def _rotate_local(point: Point2D, rotation_deg: float) -> Point2D:
    x, y = _rotate(point.x, point.y, rotation_deg)
    return _point(x, y)


def _pad_geometries(
    board: kicad.pcb.KicadPcb,
    footprint: kicad.pcb.Footprint,
    pad: kicad.pcb.Pad,
) -> list[ConductiveGeometry]:
    absolute = _abs_point(footprint.at, pad.at)
    layers = [LayerID(name=str(layer)) for layer in pad.layers]
    width = float(pad.size.w)
    height = float(pad.size.h or pad.size.w)
    rotation_deg = _absolute_rotation_deg(footprint.at, pad.at)
    net = NetID(name=name) if (name := _net_name(board, pad.net)) else None

    if pad.shape == "rect":
        shapes = [
            Rectangle(
                center=_point(0.0, 0.0),
                width=width,
                height=height,
                rotation_deg=rotation_deg,
            )
        ]
    elif pad.shape == "oval":
        shapes = [
            Obround(
                center=_point(0.0, 0.0),
                width=width,
                height=height,
                rotation_deg=rotation_deg,
            )
        ]
    elif pad.shape == "roundrect":
        corner_radius = min(width, height) * float(pad.roundrect_rratio or 0.0)
        shapes = [
            RoundedRectangle(
                center=_point(0.0, 0.0),
                width=width,
                height=height,
                corner_radius=corner_radius,
                rotation_deg=rotation_deg,
            )
        ]
    elif pad.shape == "custom" and pad.primitives is not None:
        shapes = [
            Polygon(
                vertices=[
                    _rotate_local(_point(vertex.x, vertex.y), rotation_deg)
                    for vertex in poly.pts.xys
                ]
            )
            for poly in pad.primitives.gr_polys
            if len(poly.pts.xys) >= 3
        ]
        if not shapes:
            shapes = [
                Rectangle(
                    center=_point(0.0, 0.0),
                    width=width,
                    height=height,
                    rotation_deg=rotation_deg,
                )
            ]
    elif pad.shape == "trapezoid":
        shapes = [
            Rectangle(
                center=_point(0.0, 0.0),
                width=width,
                height=height,
                rotation_deg=rotation_deg,
            )
        ]
    else:
        if width != height:
            shapes = [
                Obround(
                    center=_point(0.0, 0.0),
                    width=width,
                    height=height,
                    rotation_deg=rotation_deg,
                )
            ]
        else:
            shapes = [Circle(center=_point(0.0, 0.0), radius=width / 2.0)]

    return [
        ConductiveGeometry(
            shape=shape,
            location=absolute,
            layers=layers,
            net=net,
        )
        for shape in shapes
    ]


def convert_pcb_il_to_hl(board: kicad.pcb.KicadPcb) -> PCB:
    root = PCB(id=SourceID(id="kicad-pcb"))

    for footprint in board.footprints:
        footprint_props = list(footprint.propertys)
        component_collection = Collection(
            id=SourceID(
                id=str(
                    footprint.uuid or footprint.path or footprint.name or "footprint"
                )
            ),
            extra_properties={
                "refdes": _property_value(footprint_props, "Reference")
                or footprint.name
                or "",
                "name": _property_value(footprint_props, "Value")
                or footprint.name
                or "",
            },
            rotation_deg=_rotation_deg(footprint.at),
            side="BACK" if str(footprint.layer) == "B.Cu" else "FRONT",
        )
        root.collections.append(component_collection)

        for pad in footprint.pads:
            component_collection.collections.append(
                Collection(
                    id=SourceID(
                        id=str(
                            pad.uuid
                            or f"{component_collection.id.id}:{pad.name or 'pad'}"
                        )
                    ),
                    extra_properties={
                        "terminal_id": str(pad.name),
                        "terminal_kind": "pcb_pad",
                    },
                    geometries=_pad_geometries(board, footprint, pad),
                )
            )

    for segment in board.segments:
        layer = segment.layer
        if layer is None:
            continue
        root.geometries.append(
            ConductiveGeometry(
                shape=Segment(
                    start=_point(segment.start.x, segment.start.y),
                    end=_point(segment.end.x, segment.end.y),
                ),
                location=_point(0.0, 0.0),
                layers=[LayerID(name=str(layer))],
                net=(
                    NetID(name=name)
                    if (name := _net_name(board, segment.net))
                    else None
                ),
            )
        )

    for arc in board.arcs:
        layer = arc.layer
        if layer is None:
            continue
        root.geometries.append(
            ConductiveGeometry(
                shape=Segment(
                    start=_point(arc.start.x, arc.start.y),
                    end=_point(arc.end.x, arc.end.y),
                ),
                location=_point(0.0, 0.0),
                layers=[LayerID(name=str(layer))],
                net=(NetID(name=name) if (name := _net_name(board, arc.net)) else None),
            )
        )

    for via in board.vias:
        layers = [LayerID(name=str(layer)) for layer in via.layers]
        root.geometries.append(
            ConductiveGeometry(
                shape=Circle(
                    center=_point(0.0, 0.0),
                    radius=float(via.size) / 2.0,
                ),
                location=_point(via.at.x, via.at.y),
                layers=layers,
                net=(NetID(name=name) if (name := _net_name(board, via.net)) else None),
            )
        )

    for zone in board.zones:
        layer_names = _layers_for_zone(zone)
        if not layer_names:
            continue
        polygon = zone.polygon
        if not polygon.pts.xys:
            continue
        vertices = [_point(vertex.x, vertex.y) for vertex in polygon.pts.xys]
        if len(vertices) < 3:
            continue
        net = (
            NetID(name=name)
            if (name := zone.net_name or _net_name(board, zone.net))
            else None
        )
        # Emit one geometry per layer so round-tripping through
        # DeepPCB (which stores planes per-layer) is lossless.
        for ln in layer_names:
            root.geometries.append(
                ConductiveGeometry(
                    shape=Polygon(vertices=vertices),
                    location=_point(0.0, 0.0),
                    layers=[LayerID(name=ln)],
                    net=net,
                )
            )

    # --- Extract board outline from Edge.Cuts layer ---
    raw_outline: list[OutlineSegment | OutlineArc | OutlineCircle | OutlineBezier] = []
    for line in board.gr_lines:
        if str(line.layer) == "Edge.Cuts":
            raw_outline.append(
                OutlineSegment(
                    start=_point(line.start.x, line.start.y),
                    end=_point(line.end.x, line.end.y),
                )
            )
    for arc in board.gr_arcs:
        if str(arc.layer) == "Edge.Cuts":
            raw_outline.append(
                OutlineArc(
                    start=_point(arc.start.x, arc.start.y),
                    mid=_point(arc.mid.x, arc.mid.y),
                    end=_point(arc.end.x, arc.end.y),
                )
            )
    for rect in board.gr_rects:
        if str(rect.layer) == "Edge.Cuts":
            sx, sy = float(rect.start.x), float(rect.start.y)
            ex, ey = float(rect.end.x), float(rect.end.y)
            raw_outline.extend(
                [
                    OutlineSegment(start=_point(sx, sy), end=_point(ex, sy)),
                    OutlineSegment(start=_point(ex, sy), end=_point(ex, ey)),
                    OutlineSegment(start=_point(ex, ey), end=_point(sx, ey)),
                    OutlineSegment(start=_point(sx, ey), end=_point(sx, sy)),
                ]
            )
    for poly in board.gr_polys:
        if str(poly.layer) == "Edge.Cuts":
            pts = poly.pts.xys
            for i in range(len(pts)):
                raw_outline.append(
                    OutlineSegment(
                        start=_point(pts[i].x, pts[i].y),
                        end=_point(
                            pts[(i + 1) % len(pts)].x,
                            pts[(i + 1) % len(pts)].y,
                        ),
                    )
                )
    for circle in board.gr_circles:
        if str(circle.layer) == "Edge.Cuts":
            cx, cy = float(circle.center.x), float(circle.center.y)
            ex, ey = float(circle.end.x), float(circle.end.y)
            r = hypot(ex - cx, ey - cy)
            raw_outline.append(OutlineCircle(center=_point(cx, cy), radius=r))
    for curve in board.gr_curves:
        if str(curve.layer) == "Edge.Cuts":
            pts = curve.pts.xys
            if len(pts) == 4:
                raw_outline.append(
                    OutlineBezier(
                        p0=_point(pts[0].x, pts[0].y),
                        p1=_point(pts[1].x, pts[1].y),
                        p2=_point(pts[2].x, pts[2].y),
                        p3=_point(pts[3].x, pts[3].y),
                    )
                )

    root.outline = raw_outline

    return root


__all__ = ["convert_pcb_il_to_hl"]
