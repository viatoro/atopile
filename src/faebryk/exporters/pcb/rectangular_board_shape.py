# This file is part of the faebryk project
# SPDX-License-Identifier: MIT

import logging
import math

import faebryk.core.node as fabll
import faebryk.library._F as F
from atopile.errors import UserBadParameterError
from faebryk.exporters.pcb.kicad.transformer import (
    PCB_Transformer,
    get_all_geo_containers,
)
from faebryk.libs.kicad.fileformats import kicad

logger = logging.getLogger(__name__)

_EDGE_CUTS = "Edge.Cuts"
_STROKE_W = 0.05
_HALF_SQRT2 = math.sqrt(2.0) / 2.0


def _read_mm(
    shape: F.RectangularBoardShape, name: str, *, default: float | None = None
) -> float:
    param = getattr(shape, name).get()
    value = param.try_extract_singleton()
    if value is None:
        s = param.try_extract_superset()
        if s is not None and s.is_singleton():
            value = s.get_single()
    if value is None:
        if default is None:
            raise UserBadParameterError(
                f"`{shape.get_full_name()}.{name}` must be set to a single value."
            )
        value = default
    return value * 1_000.0  # m -> mm


def _stroke(transformer: PCB_Transformer) -> dict:
    return dict(
        stroke=kicad.pcb.Stroke(width=_STROKE_W, type=kicad.pcb.E_stroke_type.SOLID),
        layer=_EDGE_CUTS,
        uuid=transformer.gen_uuid(mark=True),
        solder_mask_margin=None,
        fill=None,
        locked=None,
        layers=[],
    )


def _line(
    t: PCB_Transformer, s: tuple[float, float], e: tuple[float, float]
) -> kicad.pcb.Line:
    return kicad.pcb.Line(
        start=kicad.pcb.Xy(x=s[0], y=s[1]),
        end=kicad.pcb.Xy(x=e[0], y=e[1]),
        **_stroke(t),
    )


def _arc(
    t: PCB_Transformer,
    s: tuple[float, float],
    m: tuple[float, float],
    e: tuple[float, float],
) -> kicad.pcb.Arc:
    return kicad.pcb.Arc(
        start=kicad.pcb.Xy(x=s[0], y=s[1]),
        mid=kicad.pcb.Xy(x=m[0], y=m[1]),
        end=kicad.pcb.Xy(x=e[0], y=e[1]),
        **_stroke(t),
    )


def _build_outline(transformer: PCB_Transformer, w: float, h: float, r: float) -> list:
    hw, hh = w / 2.0, h / 2.0
    if r <= 0:
        return [
            _line(transformer, (-hw, -hh), (hw, -hh)),
            _line(transformer, (hw, -hh), (hw, hh)),
            _line(transformer, (hw, hh), (-hw, hh)),
            _line(transformer, (-hw, hh), (-hw, -hh)),
        ]
    o = r * _HALF_SQRT2
    return [
        _line(transformer, (-hw + r, -hh), (hw - r, -hh)),
        _arc(transformer, (hw - r, -hh), (hw - r + o, -hh + r - o), (hw, -hh + r)),
        _line(transformer, (hw, -hh + r), (hw, hh - r)),
        _arc(transformer, (hw, hh - r), (hw - r + o, hh - r + o), (hw - r, hh)),
        _line(transformer, (hw - r, hh), (-hw + r, hh)),
        _arc(transformer, (-hw + r, hh), (-hw + r - o, hh - r + o), (-hw, hh - r)),
        _line(transformer, (-hw, hh - r), (-hw, -hh + r)),
        _arc(transformer, (-hw, -hh + r), (-hw + r - o, -hh + r - o), (-hw + r, -hh)),
    ]


def apply_board_outlines(transformer: PCB_Transformer, app: fabll.Node) -> None:
    """Emit Edge.Cuts geometry for any RectangularBoardShape in the design."""
    shapes = F.RectangularBoardShape.bind_typegraph(app.tg).get_instances(app.g)
    if not shapes:
        return
    if len(shapes) > 1:
        raise UserBadParameterError(
            "Only one RectangularBoardShape is supported per design; found "
            + ", ".join(f"`{s.get_full_name(include_uuid=False)}`" for s in shapes)
        )
    shape = shapes[0]
    x = _read_mm(shape, "x")
    y = _read_mm(shape, "y")
    r = _read_mm(shape, "corner_radius", default=0.0)

    if x <= 0 or y <= 0:
        raise UserBadParameterError("`x` and `y` must be > 0.")
    if r < 0:
        raise UserBadParameterError("`corner_radius` must be >= 0.")
    if r > min(x, y) / 2.0:
        raise UserBadParameterError(
            f"`corner_radius` ({r:.3f} mm) exceeds half the smallest board dimension."
        )

    _clear_existing_outline(transformer.pcb)

    for geo in _build_outline(transformer, x, y, r):
        transformer.insert_geo(geo)


def _clear_existing_outline(pcb: kicad.pcb.KicadPcb) -> None:
    """Remove any existing Edge.Cuts geometry so the new outline is the only one."""
    removed = 0
    for _, field in get_all_geo_containers(pcb):
        container = getattr(pcb, field)
        before = len(container)
        kicad.filter(
            pcb, field, container, lambda g: _EDGE_CUTS not in kicad.geo.get_layers(g)
        )
        removed += before - len(container)

    before_fps = len(pcb.footprints)
    kicad.filter(
        pcb, "footprints", pcb.footprints, lambda fp: "board_only" not in fp.attr
    )
    removed_fps = before_fps - len(pcb.footprints)

    if removed or removed_fps:
        logger.warning(
            f"Replacing existing board outline "
            f"({removed} Edge.Cuts primitive(s), "
            f"{removed_fps} board_only footprint(s))."
        )
