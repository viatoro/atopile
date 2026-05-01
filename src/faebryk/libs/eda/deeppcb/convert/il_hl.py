"""Bidirectional conversion between DeepPCB LL models and the shared HL PCB model."""

from __future__ import annotations

import logging
import math
from collections import defaultdict

from faebryk.libs.eda import board_geometry as bg
from faebryk.libs.eda.deeppcb.models import ll
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

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Unit conversion
# ---------------------------------------------------------------------------

_UNIT_TO_MM = {
    "mm": 1.0,
    "cm": 10.0,
    "inch": 25.4,
    "mil": 0.0254,
    "um": 0.001,
}


def _scale(resolution: ll.Resolution) -> float:
    """Return mm-per-unit for the given resolution."""
    return _UNIT_TO_MM[resolution.unit] / resolution.value


def _to_mm(value: float | int, scale: float) -> float:
    return float(value) * scale


def _from_mm(value: float, scale: float) -> int:
    """Convert mm back to DeepPCB integer coordinates."""
    return round(value / scale)


def _point_to_hl(coords: list, scale: float) -> Point2D:
    """DeepPCB (Y-up) → HL/KiCad (Y-down)."""
    return Point2D(x=_to_mm(coords[0], scale), y=-_to_mm(coords[1], scale))


def _point_from_hl(p: Point2D, scale: float) -> list:
    """HL/KiCad (Y-down) → DeepPCB (Y-up)."""
    return [_from_mm(p.x, scale), _from_mm(-p.y, scale)]


# ---------------------------------------------------------------------------
# Rotation helpers
# ---------------------------------------------------------------------------


def _rotate(x: float, y: float, angle_deg: float) -> tuple[float, float]:
    angle = math.radians(angle_deg)
    return (
        x * math.cos(angle) - y * math.sin(angle),
        x * math.sin(angle) + y * math.cos(angle),
    )


# ---------------------------------------------------------------------------
# atopile address encoding
# ---------------------------------------------------------------------------

_ATO_SEPARATOR = "@"


def _encode_ato_id(refdes: str, ato_address: str | None) -> str:
    if ato_address:
        return f"{refdes}{_ATO_SEPARATOR}{ato_address}"
    return refdes


def _decode_ato_id(component_id: str) -> tuple[str, str | None]:
    if _ATO_SEPARATOR in component_id:
        refdes, ato_address = component_id.split(_ATO_SEPARATOR, 1)
        return refdes, ato_address
    return component_id, None


# ---------------------------------------------------------------------------
# DeepPCB LL → HL
# ---------------------------------------------------------------------------


def _shape_rotation(shape) -> float:
    """Extract rotation_deg from an HL shape, defaulting to 0."""
    return getattr(shape, "rotation_deg", 0.0)


def _ll_shape_to_hl(
    shape: ll.Shape,
    scale: float,
    rotation_deg: float = 0.0,
) -> tuple[
    Circle | Rectangle | RoundedRectangle | Obround | Polygon | Segment | None,
    Point2D,
]:
    """Convert a DeepPCB LL shape to an HL shape + offset point.

    Returns (hl_shape, offset) where offset is the shape's own center
    in board coordinates (used for pad shapes that define their own center).
    """
    origin = Point2D(x=0.0, y=0.0)

    if shape.type == "circle":
        center = _point_to_hl(shape.center, scale)  # type: ignore[arg-type]
        return (
            Circle(center=Point2D(x=0.0, y=0.0), radius=_to_mm(shape.radius, scale)),  # type: ignore[arg-type]
            center,
        )

    if shape.type == "rectangle":
        ll_pt = _point_to_hl(shape.lower_left, scale)  # type: ignore[arg-type]
        ur_pt = _point_to_hl(shape.upper_right, scale)  # type: ignore[arg-type]
        w = ur_pt.x - ll_pt.x
        h = ur_pt.y - ll_pt.y
        center = Point2D(x=(ll_pt.x + ur_pt.x) / 2.0, y=(ll_pt.y + ur_pt.y) / 2.0)
        if shape.corner_radius is not None:
            return (
                RoundedRectangle(
                    center=Point2D(x=0.0, y=0.0),
                    width=abs(w),
                    height=abs(h),
                    corner_radius=_to_mm(shape.corner_radius, scale),
                    rotation_deg=rotation_deg,
                ),
                center,
            )
        return (
            Rectangle(
                center=Point2D(x=0.0, y=0.0),
                width=abs(w),
                height=abs(h),
                rotation_deg=rotation_deg,
            ),
            center,
        )

    if shape.type == "path" and shape.points and len(shape.points) == 2:
        # Two-point path → Obround
        p1 = _point_to_hl(shape.points[0], scale)
        p2 = _point_to_hl(shape.points[1], scale)
        dx = p2.x - p1.x
        dy = p2.y - p1.y
        path_length = math.sqrt(dx * dx + dy * dy)
        path_width_mm = _to_mm(shape.width, scale)  # type: ignore[arg-type]
        center = Point2D(x=(p1.x + p2.x) / 2.0, y=(p1.y + p2.y) / 2.0)
        along = path_length + path_width_mm
        cross = path_width_mm

        # Determine width/height from path orientation in padstack-local frame.
        # At HL rotation_deg=0, width is X and height is Y.
        # For axis-aligned paths we preserve the natural w/h assignment;
        # for diagonal paths we fold the intrinsic angle into rotation.
        abs_dx = abs(dx)
        abs_dy = abs(dy)
        if abs_dx < 1e-9:
            # Vertical path: width = cross (X extent), height = along (Y extent)
            w, h, r = cross, along, rotation_deg
        elif abs_dy < 1e-9:
            # Horizontal path: width = along (X extent), height = cross (Y extent)
            w, h, r = along, cross, rotation_deg
        else:
            # Diagonal: use height >= width, fold intrinsic angle into rotation
            path_angle_deg = math.degrees(math.atan2(dy, dx))
            # Height axis at (r+90) should align with path direction:
            # r + 90 = rotation_deg + path_angle_deg → r = rotation_deg +
            # path_angle_deg - 90
            w, h, r = cross, along, rotation_deg + path_angle_deg - 90.0

        return (
            Obround(center=Point2D(x=0.0, y=0.0), width=w, height=h, rotation_deg=r),
            center,
        )

    if shape.type in ("polyline", "polygon"):
        points = shape.points or []
        if len(points) >= 3:
            vertices = [_point_to_hl(p, scale) for p in points]
            return Polygon(vertices=vertices), origin
        if len(points) == 2:
            return (
                Segment(
                    start=_point_to_hl(points[0], scale),
                    end=_point_to_hl(points[1], scale),
                ),
                origin,
            )

    if shape.type == "polygonWithHoles":
        outline_pts = shape.outline or []
        if len(outline_pts) >= 3:
            vertices = [_point_to_hl(p, scale) for p in outline_pts]
            return Polygon(vertices=vertices), origin

    return None, origin


def convert_ll_to_hl(board: ll.DeepPCBBoard) -> PCB:
    """Convert a DeepPCB LL board model to the shared HL PCB model."""
    scale = _scale(board.resolution)
    root = PCB(id=SourceID(id=board.name or "deeppcb-board"))

    # Build lookup tables
    padstacks_by_id = {ps.id: ps for ps in board.padstacks}
    comp_defs_by_id = {cd.id: cd for cd in board.component_definitions}
    net_pins: dict[str, str] = {}  # "comp_id-pin_id" → net_id
    for net in board.nets:
        for pin_ref in net.pins:
            net_pins[pin_ref] = net.id

    # Layer index → name
    layer_names = {i: layer.id for i, layer in enumerate(board.layers)}

    # --- Components ---
    for comp in board.components:
        comp_def = comp_defs_by_id.get(comp.definition)
        if comp_def is None:
            continue

        refdes, ato_address = _decode_ato_id(comp.id)
        extra = {
            "refdes": refdes,
            "name": comp.definition,
        }
        if ato_address:
            extra["ato_address"] = ato_address
        if comp.part_number:
            extra["part_number"] = comp.part_number
        comp_rot = float(comp.rotation)

        comp_collection = Collection(
            id=SourceID(id=comp.id),
            extra_properties=extra,
            rotation_deg=comp_rot,
            side=comp.side or "FRONT",
        )
        root.collections.append(comp_collection)

        comp_pos = _point_to_hl(comp.position, scale)

        for pin in comp_def.pins:
            padstack = padstacks_by_id.get(pin.padstack)
            if padstack is None:
                continue

            # Pin absolute position = component pos + rotated pin local pos
            # Pin local Y is in DeepPCB's Y-up frame; negate to get HL Y-down.
            local_x = _to_mm(pin.position[0], scale)
            local_y = -_to_mm(pin.position[1], scale)
            rx, ry = _rotate(local_x, local_y, comp_rot)
            abs_pos = Point2D(x=comp_pos.x + rx, y=comp_pos.y + ry)

            # Total rotation for pad shape
            total_rotation = comp_rot + float(pin.rotation)

            # Build pad geometries from padstack
            geometries = _padstack_to_geometries(
                padstack,
                abs_pos,
                total_rotation,
                layer_names,
                scale,
                comp,
                pin,
                net_pins,
            )

            # Strip @N suffix for terminal_id — the HL model uses the
            # base pin name; the suffix is a DeepPCB encoding detail.
            base_terminal_id = pin.id.split("@")[0] if "@" in pin.id else pin.id

            comp_collection.collections.append(
                Collection(
                    id=SourceID(id=f"{comp.id}:{pin.id}"),
                    extra_properties={
                        "terminal_id": base_terminal_id,
                        "terminal_kind": "pcb_pad",
                    },
                    geometries=geometries,
                )
            )

    # --- Wires (traces) ---
    # HL Segment has no width field; preserve widths in PCB extra_properties
    # so the HL→DeepPCB export can recover them.
    wire_widths: list[int] = []
    for wire in board.wires:
        layer_name = layer_names.get(wire.layer, f"Layer{wire.layer}")
        root.geometries.append(
            ConductiveGeometry(
                shape=Segment(
                    start=_point_to_hl(wire.start, scale),
                    end=_point_to_hl(wire.end, scale),
                ),
                location=Point2D(x=0.0, y=0.0),
                layers=[LayerID(name=layer_name)],
                net=NetID(name=wire.net_id) if wire.net_id else None,
            )
        )
        wire_widths.append(wire.width)
    if wire_widths:
        root.extra_properties["_wire_widths"] = wire_widths

    # --- Vias ---
    for via in board.vias:
        padstack = padstacks_by_id.get(via.padstack)
        if padstack is None:
            continue
        via_pos = _point_to_hl(via.position, scale)
        layers = _padstack_layer_ids(padstack, layer_names)
        radius = _padstack_radius(padstack, scale)
        root.geometries.append(
            ConductiveGeometry(
                shape=Circle(center=Point2D(x=0.0, y=0.0), radius=radius),
                location=via_pos,
                layers=layers,
                net=NetID(name=via.net_id) if via.net_id else None,
            )
        )

    # --- Planes (zones) ---
    # Each DeepPCB plane maps to a single-layer HL geometry.
    # Filled shapes are layer-specific (copper fills differ per layer after
    # routing), so we must NOT merge planes across layers.
    for plane in board.planes:
        layer_name = layer_names.get(plane.layer, f"Layer{plane.layer}")
        hl_shape, offset = _ll_shape_to_hl(plane.shape, scale)
        if hl_shape is None:
            continue
        net = NetID(name=plane.net_id) if plane.net_id else None

        # Convert filled_shape data if present
        filled: list[Polygon] = []
        if plane.filled_shape:
            for fs in plane.filled_shape:
                fs_hl, _ = _ll_shape_to_hl(fs, scale)
                if isinstance(fs_hl, Polygon):
                    filled.append(fs_hl)

        geom = ConductiveGeometry(
            shape=hl_shape,
            location=offset,
            layers=[LayerID(name=layer_name)],
            net=net,
            filled_shapes=filled if filled else None,
        )
        root.geometries.append(geom)

    # --- Board outline from boundary ---
    root.outline = _boundary_to_outline(board.boundary, scale)

    return root


def _padstack_to_geometries(
    padstack: ll.Padstack,
    location: Point2D,
    rotation_deg: float,
    layer_names: dict[int, str],
    scale: float,
    comp: ll.Component,
    pin: ll.Pin,
    net_pins: dict[str, str],
) -> list[ConductiveGeometry]:
    layers = _padstack_layer_ids(padstack, layer_names)
    net_key = f"{comp.id}-{pin.id}"
    net_id = net_pins.get(net_key)
    net = NetID(name=net_id) if net_id else None

    if padstack.shape is not None:
        hl_shape, _offset = _ll_shape_to_hl(padstack.shape, scale, rotation_deg)
        if hl_shape is not None:
            return [
                ConductiveGeometry(
                    shape=hl_shape,
                    location=location,
                    layers=layers,
                    net=net,
                )
            ]

    if padstack.pads:
        geoms = []
        for pad_def in padstack.pads:
            pad_layers = [
                LayerID(name=layer_names.get(i, f"Layer{i}"))
                for i in range(pad_def.layer_from, pad_def.layer_to + 1)
                if i in layer_names
            ]
            hl_shape, _offset = _ll_shape_to_hl(pad_def.shape, scale, rotation_deg)
            if hl_shape is not None:
                geoms.append(
                    ConductiveGeometry(
                        shape=hl_shape,
                        location=location,
                        layers=pad_layers or layers,
                        net=net,
                    )
                )
        return geoms

    return []


def _padstack_layer_ids(
    padstack: ll.Padstack, layer_names: dict[int, str]
) -> list[LayerID]:
    if padstack.layers is not None:
        return [LayerID(name=layer_names.get(i, f"Layer{i}")) for i in padstack.layers]
    return []


def _padstack_radius(padstack: ll.Padstack, scale: float) -> float:
    if padstack.shape is not None:
        if padstack.shape.type == "circle" and padstack.shape.radius is not None:
            return _to_mm(padstack.shape.radius, scale)
        if padstack.shape.type == "rectangle":
            ll_pt = padstack.shape.lower_left or [0, 0]
            ur_pt = padstack.shape.upper_right or [0, 0]
            w = abs(_to_mm(ur_pt[0] - ll_pt[0], scale))
            h = abs(_to_mm(ur_pt[1] - ll_pt[1], scale))
            return max(w, h) / 2.0
    return 0.0


# ---------------------------------------------------------------------------
# HL → DeepPCB LL
# ---------------------------------------------------------------------------


def _hl_shape_to_ll(
    shape: Circle | Rectangle | RoundedRectangle | Obround | Polygon | Segment,
    scale: float,
) -> tuple[ll.Shape, float]:
    """Convert an HL shape to a DeepPCB LL shape and extract rotation.

    Returns (ll_shape, rotation_deg). The ll_shape is in canonical orientation
    (no rotation); rotation_deg should be applied via pin/component rotation.
    """
    if isinstance(shape, Circle):
        r = _from_mm(shape.radius, scale)
        return (
            ll.Shape(type=ll.ShapeType.CIRCLE, center=[0, 0], radius=r),
            0.0,
        )

    if isinstance(shape, RoundedRectangle):
        hw = _from_mm(shape.width / 2.0, scale)
        hh = _from_mm(shape.height / 2.0, scale)
        cr = _from_mm(shape.corner_radius, scale)
        return (
            ll.Shape(
                type=ll.ShapeType.RECTANGLE,
                lower_left=[-hw, -hh],
                upper_right=[hw, hh],
                corner_radius=cr,
            ),
            shape.rotation_deg,
        )

    if isinstance(shape, Rectangle):
        hw = _from_mm(shape.width / 2.0, scale)
        hh = _from_mm(shape.height / 2.0, scale)
        return (
            ll.Shape(
                type=ll.ShapeType.RECTANGLE, lower_left=[-hw, -hh], upper_right=[hw, hh]
            ),
            shape.rotation_deg,
        )

    if isinstance(shape, Obround):
        # Convert obround to two-point path.
        # The path orientation must match the forward conversion so that
        # width/height and rotation survive the round-trip exactly.
        cross = min(shape.width, shape.height)
        along = max(shape.width, shape.height)
        path_width = _from_mm(cross, scale)
        length = _from_mm(along - cross, scale)
        half_len = length / 2.0

        if shape.height >= shape.width:
            # Long axis is height (Y in local frame) → vertical path
            p1 = [0, half_len]
            p2 = [0, -half_len]
            returned_rotation = shape.rotation_deg
        else:
            # Long axis is width (X in local frame) → horizontal path
            p1 = [half_len, 0]
            p2 = [-half_len, 0]
            returned_rotation = shape.rotation_deg

        return (
            ll.Shape(type=ll.ShapeType.PATH, points=[p1, p2], width=path_width),
            returned_rotation,
        )

    if isinstance(shape, Polygon):
        points = [_point_from_hl(v, scale) for v in shape.vertices]
        return ll.Shape(type=ll.ShapeType.POLYGON, points=points), 0.0

    if isinstance(shape, Segment):
        p1 = _point_from_hl(shape.start, scale)
        p2 = _point_from_hl(shape.end, scale)
        return ll.Shape(type=ll.ShapeType.POLYLINE, points=[p1, p2]), 0.0

    raise ValueError(f"Unsupported HL shape type: {type(shape)}")


def _padstack_key(ll_shape: ll.Shape, layer_indices: list[int]) -> str:
    """Create a deduplication key for a padstack."""
    import json

    from faebryk.libs.eda.deeppcb.convert.file_ll import _serialize_shape

    shape_json = json.dumps(_serialize_shape(ll_shape), sort_keys=True)
    return f"{shape_json}|{sorted(layer_indices)}"


def convert_hl_to_ll(
    pcb: PCB,
    resolution: ll.Resolution | None = None,
) -> ll.DeepPCBBoard:
    """Convert an HL PCB model to a DeepPCB LL board model.

    If *resolution* is not provided, defaults to mm with value=1000 (1 μm precision).
    """
    if resolution is None:
        resolution = ll.Resolution(unit=ll.ResolutionUnit.MM, value=1000)

    scale = _scale(resolution)
    board_name = pcb.id.id if pcb.id else ""

    # Accumulators
    padstack_registry: dict[str, ll.Padstack] = {}
    comp_defs: dict[str, ll.ComponentDefinition] = {}
    components: list[ll.Component] = []
    net_pin_map: dict[str, list[str]] = defaultdict(list)  # net_id → pin refs
    all_layers: dict[str, int] = {}  # layer_name → index
    wires: list[ll.Wire] = []
    vias: list[ll.Via] = []
    planes: list[ll.Plane] = []
    via_padstack_ids: set[str] = set()

    def _is_copper_layer(name: str) -> bool:
        """DeepPCB only models conductive layers — filter out mask/paste/silk."""
        return (
            name.endswith(".Cu") or name.startswith("In") or name in ("*.Cu", "F&B.Cu")
        )

    # Wildcard copper layers from KiCad (through-hole pads) must be resolved
    # to actual copper layer names.  Collect real layers first, then resolve.
    _WILDCARD_LAYERS = {"*.Cu", "F&B.Cu", "*.Mask", "*.Paste"}

    def _is_real_copper(name: str) -> bool:
        return _is_copper_layer(name) and name not in _WILDCARD_LAYERS

    def _resolve_copper_layers(names: list[str]) -> list[str]:
        """Expand wildcard layers to the real copper layers on the board."""
        real = sorted(all_layers.keys())  # all registered real copper layers
        if not real:
            real = ["F.Cu"]
        result = []
        for n in names:
            if n in ("*.Cu", "F&B.Cu"):
                result.extend(real)
            elif _is_real_copper(n):
                result.append(n)
        return list(dict.fromkeys(result))  # dedupe, preserve order

    def _get_layer_index(name: str) -> int:
        if name not in all_layers:
            all_layers[name] = len(all_layers)
        return all_layers[name]

    def _register_padstack(ll_shape: ll.Shape, layer_indices: list[int]) -> str:
        key = _padstack_key(ll_shape, layer_indices)
        if key not in padstack_registry:
            ps_id = f"Padstack_{len(padstack_registry)}"
            padstack_registry[key] = ll.Padstack(
                id=ps_id,
                shape=ll_shape,
                layers=sorted(layer_indices),
                allow_via=False,
            )
        return padstack_registry[key].id

    # Pre-register real (non-wildcard) copper layers so wildcard resolution
    # knows what layers exist on the board.
    for col in pcb.collections:
        for pad_col in col.collections:
            for geom in pad_col.geometries:
                for lid in geom.layers:
                    if _is_real_copper(lid.name):
                        _get_layer_index(lid.name)
    for geom in pcb.geometries:
        for lid in geom.layers:
            if _is_real_copper(lid.name):
                _get_layer_index(lid.name)

    # --- Process component collections ---
    for comp_col in pcb.collections:
        extra = comp_col.extra_properties
        refdes = str(extra.get("refdes", ""))
        ato_address = extra.get("ato_address")
        comp_id = (
            _encode_ato_id(refdes, ato_address)
            if refdes
            else (comp_col.id.id if comp_col.id else f"comp_{len(components)}")
        )
        comp_name = str(extra.get("name", ""))
        comp_side = comp_col.side

        # Collect pad data to determine component position and rotation
        pad_data: list[
            tuple[Point2D, float, list[ConductiveGeometry], str]
        ] = []  # (location, rotation, geoms, pin_id)

        unnamed_pad_idx = 0
        for pad_col in comp_col.collections:
            pad_extra = pad_col.extra_properties
            pin_id = str(pad_extra.get("terminal_id", ""))
            if not pin_id:
                # Unnamed pads (mounting, shield) — assign simple sequential IDs.
                # UUIDs from KiCad would break the "{comp}-{pin}" net ref format.
                pin_id = f"pad{unnamed_pad_idx}"
                unnamed_pad_idx += 1

            if not pad_col.geometries:
                continue

            geom = pad_col.geometries[0]
            rotation = _shape_rotation(geom.shape)
            pad_data.append((geom.location, rotation, pad_col.geometries, pin_id))

        # Deduplicate pin IDs: multi-pad pins (e.g. GND thermal array)
        # get @N suffixes matching the DeepPCB convention.
        pin_id_counts: dict[str, int] = {}
        for i, (loc, rot, geoms, pid) in enumerate(pad_data):
            count = pin_id_counts.get(pid, 0)
            if count > 0:
                pad_data[i] = (loc, rot, geoms, f"{pid}@{count}")
            pin_id_counts[pid] = count + 1

        if not pad_data:
            continue

        # Component position = centroid of pad locations
        cx = sum(p[0].x for p in pad_data) / len(pad_data)
        cy = sum(p[0].y for p in pad_data) / len(pad_data)
        comp_pos = Point2D(x=cx, y=cy)

        # Component rotation from HL model
        comp_rot = comp_col.rotation_deg

        # Build pins and padstacks
        pins: list[ll.Pin] = []
        keepouts: list[ll.Keepout] = []

        for loc, _rot, geoms, pin_id in pad_data:
            geom = geoms[0]

            # Pin local position: un-rotate relative to component
            rel_x = loc.x - comp_pos.x
            rel_y = loc.y - comp_pos.y
            local_x, local_y = _rotate(rel_x, rel_y, -comp_rot)
            pin_pos = [_from_mm(local_x, scale), _from_mm(-local_y, scale)]

            # Shape → padstack (canonical, no rotation)
            ll_shape, shape_rot = _hl_shape_to_ll(geom.shape, scale)
            copper_layers = _resolve_copper_layers(
                [lid.name for lid in geom.layers if _is_copper_layer(lid.name)]
            )
            if not copper_layers:
                copper_layers = ["F.Cu"]
            layer_indices = [_get_layer_index(n) for n in copper_layers]
            ps_id = _register_padstack(ll_shape, layer_indices)

            # Pin rotation = local pad rotation within the footprint.
            # HL stores absolute shape rotation; subtract component rotation
            # to recover the local rotation. This matches KiCad's
            # pad.at.r - footprint.at.r.
            pin_rot = round(shape_rot - comp_rot) % 360

            pins.append(
                ll.Pin(
                    id=pin_id,
                    padstack=ps_id,
                    position=pin_pos,
                    rotation=pin_rot,
                )
            )

            # Register net (skip unnamed pads — they're mounting/shield)
            if geom.net is not None and not pin_id.startswith("pad"):
                net_pin_map[geom.net.name].append(f"{comp_id}-{pin_id}")

        # Build component outline from pad bounding box.
        # DeepPCB uses this for placement collision detection.
        outline: ll.Shape | None = None
        if pins:
            all_pin_x = [p.position[0] for p in pins]
            all_pin_y = [p.position[1] for p in pins]
            # Add margin based on padstack size (estimate from first pad shape)
            margin = _from_mm(0.5, scale)  # 0.5mm default margin
            min_x = min(all_pin_x) - margin
            max_x = max(all_pin_x) + margin
            min_y = min(all_pin_y) - margin
            max_y = max(all_pin_y) + margin
            outline = ll.Shape(
                type=ll.ShapeType.POLYLINE,
                points=[
                    [min_x, min_y],
                    [max_x, min_y],
                    [max_x, max_y],
                    [min_x, max_y],
                    [min_x, min_y],
                ],
            )

        # Each component gets its own definition because pin local positions
        # are derived from absolute positions and vary per placement/rotation.
        def_id = comp_id
        comp_defs[def_id] = ll.ComponentDefinition(
            id=def_id,
            pins=pins,
            keepouts=keepouts,
            outline=outline,
        )

        # part_number: use footprint name or refdes:name combo
        part_number = (
            str(extra.get("part_number", ""))
            or (f"{refdes}:{comp_name}" if comp_name else None)
            or None
        )

        components.append(
            ll.Component(
                id=comp_id,
                definition=def_id,
                position=_point_from_hl(comp_pos, scale),
                rotation=round(comp_rot),
                side=comp_side,
                part_number=part_number,
                protected=False,
            )
        )

    # --- Process top-level geometries (traces, vias, zones) ---
    # Recover wire widths stored during LL→HL import (if available).
    stored_wire_widths: list[int] = pcb.extra_properties.get("_wire_widths", [])
    wire_idx = 0

    _DEFAULT_TRACK_WIDTH = _from_mm(0.2, scale)  # 200 µm fallback

    for geom in pcb.geometries:
        copper = _resolve_copper_layers(
            [lid.name for lid in geom.layers if _is_copper_layer(lid.name)]
        )
        if not copper:
            copper = [geom.layers[0].name] if geom.layers else ["F.Cu"]
        layer_indices = [_get_layer_index(n) for n in copper]
        net_name = geom.net.name if geom.net else ""

        if isinstance(geom.shape, Segment):
            # Trace segment → wire
            layer_idx = layer_indices[0] if layer_indices else 0
            # Use stored width if available, else default track width
            width = (
                stored_wire_widths[wire_idx]
                if wire_idx < len(stored_wire_widths)
                else _DEFAULT_TRACK_WIDTH
            )
            wire_idx += 1
            wires.append(
                ll.Wire(
                    net_id=net_name,
                    layer=layer_idx,
                    start=_point_from_hl(
                        Point2D(
                            x=geom.location.x + geom.shape.start.x,
                            y=geom.location.y + geom.shape.start.y,
                        ),
                        scale,
                    ),
                    end=_point_from_hl(
                        Point2D(
                            x=geom.location.x + geom.shape.end.x,
                            y=geom.location.y + geom.shape.end.y,
                        ),
                        scale,
                    ),
                    width=width,
                    type="segment",
                )
            )

        elif isinstance(geom.shape, Circle) and len(layer_indices) > 1:
            # Multi-layer circle → via
            ll_shape, _ = _hl_shape_to_ll(geom.shape, scale)
            ps_id = _register_padstack(ll_shape, layer_indices)
            via_padstack_ids.add(ps_id)
            # Mark padstack as via-capable
            for key, ps in padstack_registry.items():
                if ps.id == ps_id:
                    ps.allow_via = True
                    break
            vias.append(
                ll.Via(
                    net_id=net_name,
                    position=_point_from_hl(geom.location, scale),
                    padstack=ps_id,
                )
            )

        elif isinstance(geom.shape, Polygon):
            # Polygon → one plane per layer (DeepPCB planes are single-layer).
            # Planes require polygonWithHoles shape type.
            ll_shape, _ = _hl_shape_to_ll(geom.shape, scale)
            if ll_shape.type == "polygon":
                ll_shape = ll.Shape(
                    type=ll.ShapeType.POLYGON_WITH_HOLES,
                    outline=ll_shape.points,
                    holes=[],
                )
            for layer_idx in layer_indices or [0]:
                planes.append(
                    ll.Plane(
                        net_id=net_name,
                        layer=layer_idx,
                        shape=ll_shape,
                    )
                )

    # --- Build layers ---
    layers = [
        ll.Layer(id=name, keepouts=[], type=ll.LayerType.SIGNAL)
        for name, _idx in sorted(all_layers.items(), key=lambda x: x[1])
    ]

    # --- Boundary (CCW polyline) + Edge.Cuts cutouts as per-layer keepouts ---
    boundary, cutout_polygons = _compute_boundary_and_cutouts(pcb, scale)
    for cutout in cutout_polygons:
        for layer_idx in range(len(layers)):
            layers[layer_idx].keepouts.append(
                ll.Keepout(
                    shape=ll.Shape(type=ll.ShapeType.POLYLINE, points=cutout),
                    layer=layer_idx,
                )
            )

    # --- Build nets ---
    nets = [
        ll.Net(id=net_id, pins=pin_refs) for net_id, pin_refs in net_pin_map.items()
    ]

    # --- Ensure at least one via definition exists ---
    # DeepPCB requires a valid via definition for net classes.
    if not via_padstack_ids:
        # Create a default via padstack spanning all copper layers
        all_layer_indices = list(range(len(all_layers)))
        if len(all_layer_indices) < 2:
            all_layer_indices = [0]
        default_via_radius = _from_mm(0.3, scale)  # 0.3mm radius
        default_via_shape = ll.Shape(
            type=ll.ShapeType.CIRCLE, center=[0, 0], radius=default_via_radius
        )
        default_via_id = _register_padstack(default_via_shape, all_layer_indices)
        via_padstack_ids.add(default_via_id)
        for key, ps in padstack_registry.items():
            if ps.id == default_via_id:
                ps.allow_via = True
                break

    default_via = sorted(via_padstack_ids)[0]

    # --- Build net classes (single default) ---
    net_classes = [
        ll.NetClass(
            id="__default__",
            nets=[],
            clearance=200,
            track_width=200,
            via_definition=default_via,
            via_priority=[[default_via]],
        )
    ]

    # --- Build via definitions ---
    via_definitions = sorted(via_padstack_ids)

    return ll.DeepPCBBoard(
        name=board_name,
        resolution=resolution,
        boundary=boundary,
        padstacks=list(padstack_registry.values()),
        component_definitions=list(comp_defs.values()),
        components=components,
        layers=layers,
        nets=nets,
        net_classes=net_classes,
        planes=planes,
        wires=wires,
        vias=vias,
        via_definitions=via_definitions,
        rules=[
            ll.Rule(type=ll.RuleType.ROTATE_FIRST, value=False),
            ll.Rule(type=ll.RuleType.ALLOW_VIA_AT_SMD, value=False),
            ll.Rule(type=ll.RuleType.ALLOW_90_DEGREES, value=False),
            ll.Rule(
                type=ll.RuleType.PIN_CONNECTION_POINT,
                value=ll.PinConnectionPointValue.CENTROID,
            ),
        ],
    )


def _linearize_arc(
    start: Point2D, mid: Point2D, end: Point2D, max_seg_mm: float = 0.5
) -> list[Point2D]:
    """Approximate a three-point arc as a sequence of points.

    Returns intermediate points (excluding *start*, including *end*) so that
    callers can concatenate arcs and lines without duplicating junction points.
    """
    # Compute circle centre from three points.
    ax, ay = start.x, start.y
    bx, by = mid.x, mid.y
    cx, cy = end.x, end.y

    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        # Degenerate (collinear) — just return the endpoint.
        return [end]

    ux = (
        (ax * ax + ay * ay) * (by - cy)
        + (bx * bx + by * by) * (cy - ay)
        + (cx * cx + cy * cy) * (ay - by)
    ) / d
    uy = (
        (ax * ax + ay * ay) * (cx - bx)
        + (bx * bx + by * by) * (ax - cx)
        + (cx * cx + cy * cy) * (bx - ax)
    ) / d
    r = math.hypot(ax - ux, ay - uy)

    # Angles for start, mid, end relative to centre.
    a_start = math.atan2(ay - uy, ax - ux)
    a_mid = math.atan2(by - uy, bx - ux)
    a_end = math.atan2(cy - uy, cx - ux)

    # Determine sweep direction so the arc passes through mid.
    def _norm(a: float) -> float:
        return a % (2.0 * math.pi)

    sweep_cw = (_norm(a_start - a_mid) + _norm(a_mid - a_end)) < (
        _norm(a_mid - a_start) + _norm(a_end - a_mid)
    )

    if sweep_cw:
        total = _norm(a_start - a_end)
    else:
        total = _norm(a_end - a_start)

    arc_len = r * total
    n_segs = max(2, int(math.ceil(arc_len / max_seg_mm)))

    pts: list[Point2D] = []
    for i in range(1, n_segs + 1):
        t = i / n_segs
        if sweep_cw:
            angle = a_start - t * total
        else:
            angle = a_start + t * total
        pts.append(Point2D(x=ux + r * math.cos(angle), y=uy + r * math.sin(angle)))
    return pts


def _linearize_circle(
    center: Point2D, radius: float, max_seg_mm: float = 0.5
) -> list[Point2D]:
    """Approximate a full circle as a closed list of points."""
    circumference = 2.0 * math.pi * radius
    n_segs = max(8, int(math.ceil(circumference / max_seg_mm)))
    pts: list[Point2D] = []
    for i in range(n_segs + 1):  # +1 to close the loop
        angle = 2.0 * math.pi * i / n_segs
        pts.append(
            Point2D(
                x=center.x + radius * math.cos(angle),
                y=center.y + radius * math.sin(angle),
            )
        )
    return pts


def _linearize_bezier(
    p0: Point2D, p1: Point2D, p2: Point2D, p3: Point2D, max_seg_mm: float = 0.5
) -> list[Point2D]:
    """Approximate a cubic bezier as a sequence of points.

    Returns intermediate points (excluding *p0*, including *p3*).
    """
    # Estimate arc length from control polygon for segment count.
    chord = math.hypot(p3.x - p0.x, p3.y - p0.y)
    poly_len = (
        math.hypot(p1.x - p0.x, p1.y - p0.y)
        + math.hypot(p2.x - p1.x, p2.y - p1.y)
        + math.hypot(p3.x - p2.x, p3.y - p2.y)
    )
    est_len = (chord + poly_len) / 2.0
    n_segs = max(2, int(math.ceil(est_len / max_seg_mm)))

    pts: list[Point2D] = []
    for i in range(1, n_segs + 1):
        t = i / n_segs
        u = 1.0 - t
        x = u**3 * p0.x + 3 * u**2 * t * p1.x + 3 * u * t**2 * p2.x + t**3 * p3.x
        y = u**3 * p0.y + 3 * u**2 * t * p1.y + 3 * u * t**2 * p2.y + t**3 * p3.y
        pts.append(Point2D(x=x, y=y))
    return pts


_OutlineElem = OutlineSegment | OutlineArc | OutlineCircle | OutlineBezier


def _hl_outline_to_primitives(
    outline: list[_OutlineElem],
) -> tuple[list[bg.Segment], list[bg.Arc], list[bg.Circle]]:
    """Adapt HL outline elements into ``board_geometry`` primitives.
    Beziers are pre-linearized since ``board_geometry`` doesn't model them."""
    segments: list[bg.Segment] = []
    arcs: list[bg.Arc] = []
    circles: list[bg.Circle] = []
    for e in outline:
        if isinstance(e, OutlineSegment):
            segments.append(((e.start.x, e.start.y), (e.end.x, e.end.y)))
        elif isinstance(e, OutlineArc):
            arcs.append(
                bg.Arc(
                    start=(e.start.x, e.start.y),
                    mid=(e.mid.x, e.mid.y),
                    end=(e.end.x, e.end.y),
                )
            )
        elif isinstance(e, OutlineCircle):
            circles.append(bg.Circle(center=(e.center.x, e.center.y), radius=e.radius))
        elif isinstance(e, OutlineBezier):
            pts = [e.p0, *_linearize_bezier(e.p0, e.p1, e.p2, e.p3)]
            for i in range(len(pts) - 1):
                segments.append(((pts[i].x, pts[i].y), (pts[i + 1].x, pts[i + 1].y)))
    return segments, arcs, circles


def _loop_to_ll_polygon(
    loop: bg.Loop, scale: float, *, ccw: bool
) -> list[list[float | int]]:
    """Project an HL loop into closed DeepPCB integer coords. Winding is
    forced to the requested direction post Y-flip."""
    pts = [_point_from_hl(Point2D(x=x, y=y), scale) for x, y in loop]
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    area = _signed_area_xy(pts)
    if (area < 0 and ccw) or (area > 0 and not ccw):
        pts.reverse()
    return pts


def _signed_area_xy(pts: list[list]) -> float:
    a = 0.0
    for i in range(len(pts) - 1):
        a += pts[i][0] * pts[i + 1][1] - pts[i + 1][0] * pts[i][1]
    return a / 2.0


def _boundary_to_outline(boundary: ll.Boundary, scale: float) -> list[OutlineSegment]:
    """Reconstruct an HL outline (outer + holes) from a DeepPCB boundary."""
    shape = boundary.shape
    if shape.type == ll.ShapeType.POLYGON_WITH_HOLES:
        outer = shape.outline or []
        holes = shape.holes or []
        out: list[OutlineSegment] = []
        for poly in (outer, *holes):
            hl_pts = [_point_to_hl(p, scale) for p in poly]
            if not hl_pts:
                continue
            for i in range(len(hl_pts) - 1):
                out.append(OutlineSegment(start=hl_pts[i], end=hl_pts[i + 1]))
            if hl_pts[0].x != hl_pts[-1].x or hl_pts[0].y != hl_pts[-1].y:
                out.append(OutlineSegment(start=hl_pts[-1], end=hl_pts[0]))
        return out

    pts = shape.points
    if not pts or len(pts) < 3:
        return []
    hl_pts = [_point_to_hl(p, scale) for p in pts]
    segments = [
        OutlineSegment(start=hl_pts[i], end=hl_pts[i + 1])
        for i in range(len(hl_pts) - 1)
    ]
    if hl_pts[0].x != hl_pts[-1].x or hl_pts[0].y != hl_pts[-1].y:
        segments.append(OutlineSegment(start=hl_pts[-1], end=hl_pts[0]))
    return segments


def _compute_boundary_and_cutouts(
    pcb: PCB, scale: float
) -> tuple[ll.Boundary, list[list[list[float | int]]]]:
    """Boundary (CCW polyline) + interior cutouts as closed CCW polylines.

    DeepPCB's schema pins ``boundary.shape.type`` to ``polyline``;
    cutouts live in ``layer.keepouts[]``. Caller wraps each cutout
    in a ``Keepout`` for every conductive layer.
    """
    if not pcb.outline:
        raise ValueError("PCB has no board outline (Edge.Cuts)")

    segments, arcs, circles = _hl_outline_to_primitives(pcb.outline)
    outer, holes = bg.build_outline(segments=segments, arcs=arcs, circles=circles)
    if outer is None:
        raise ValueError(
            "PCB Edge.Cuts has no closed outer loop "
            f"({len(segments)} segments, {len(arcs)} arcs, {len(circles)} circles)"
        )

    boundary = ll.Boundary(
        shape=ll.Shape(
            type=ll.ShapeType.POLYLINE,
            points=_loop_to_ll_polygon(outer, scale, ccw=True),
        ),
        clearance=_from_mm(0.2, scale),  # type: ignore[arg-type]
    )
    cutouts = [_loop_to_ll_polygon(h, scale, ccw=True) for h in holes]
    return boundary, cutouts


__all__ = ["convert_ll_to_hl", "convert_hl_to_ll"]
