"""Preflight metric helpers for autolayout feasibility hints.

Computes lightweight board health metrics from a KiCad PCB file
without running full autolayout. Used by the UI preflight section.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from faebryk.libs.eda import board_geometry as bg
from faebryk.libs.kicad.fileformats import kicad


def compute_preflight_summary(
    layout_path: Path | None = None,
    *,
    pcb: Any | None = None,
) -> dict[str, Any]:
    """Compute placement/routing demand metrics from a KiCad PCB.

    Either *layout_path* (reads from disk) or *pcb* (an already-loaded
    ``KicadPcb`` object) must be provided.  When *pcb* is given the
    path is ignored — this allows callers to pass the in-memory model
    from the layout editor so metrics reflect unsaved edits.
    """
    if pcb is None:
        if layout_path is None:
            raise ValueError("Either layout_path or pcb must be provided")
        pcb_file = kicad.loads(kicad.pcb.PcbFile, layout_path)
        pcb = pcb_file.kicad_pcb

    outer, holes = _edge_cuts_loops(pcb)
    board_area, board_width, board_height = _board_metrics_from_loops(outer, holes)
    (
        comp_area,
        top_comp_area,
        bottom_comp_area,
        comp_count,
        top_count,
        bottom_count,
        pad_count,
        net_count,
        conn_count,
    ) = _component_and_net_metrics(pcb)

    # Count components inside vs outside the board outline
    inside_count, outside_count = _components_inside_outside(pcb, outer, holes)

    utilization = _divide(comp_area, board_area)
    pad_density = _divide(float(pad_count), board_area)
    conn_density = _divide(float(conn_count), board_area)
    top_util = _divide(top_comp_area, board_area)

    layer_count = _layer_count(pcb)
    sidedness = _sidedness_label(top_count, bottom_count)
    risk = _stackup_risk(
        layer_count=layer_count,
        placement_utilization=utilization,
        pad_density=pad_density,
        connection_density=conn_density,
    )
    rec = _recommendation(
        layer_count=layer_count,
        sidedness=sidedness,
        stackup_risk=risk,
        placement_utilization=utilization,
        top_only_utilization=top_util,
        bottom_component_count=bottom_count,
    )

    return {
        "boardAreaMm2": _round(board_area),
        "boardWidthMm": _round(board_width),
        "boardHeightMm": _round(board_height),
        "componentAreaMm2": _round(comp_area),
        "componentCount": comp_count,
        "topComponentCount": top_count,
        "bottomComponentCount": bottom_count,
        "componentsInsideBoard": inside_count,
        "componentsOutsideBoard": outside_count,
        "padCount": pad_count,
        "netCount": net_count,
        "connectionCount": conn_count,
        "placementUtilization": _round(utilization, 3),
        "topOnlyUtilization": _round(top_util, 3),
        "padDensity": _round(pad_density, 3),
        "connectionDensity": _round(conn_density, 3),
        "layerCount": layer_count,
        "sidedness": sidedness,
        "stackupRisk": risk,
        "recommendation": rec,
    }


# ---------------------------------------------------------------------------
# Board outline
# ---------------------------------------------------------------------------


def _coord(obj: Any) -> bg.Point:
    return (
        float(getattr(obj, "x", 0.0) or 0.0),
        float(getattr(obj, "y", 0.0) or 0.0),
    )


def _edge_cuts_loops(pcb: Any) -> tuple[bg.Loop | None, list[bg.Loop]]:
    """Pull Edge.Cuts primitives off a KiCad PCB and assemble loops."""
    segments: list[bg.Segment] = []
    arcs: list[bg.Arc] = []
    circles: list[bg.Circle] = []

    for line in getattr(pcb, "gr_lines", []) or []:
        if str(getattr(line, "layer", "")) == "Edge.Cuts":
            segments.append((_coord(line.start), _coord(line.end)))
    for arc in getattr(pcb, "gr_arcs", []) or []:
        if str(getattr(arc, "layer", "")) == "Edge.Cuts":
            arcs.append(bg.Arc(_coord(arc.start), _coord(arc.mid), _coord(arc.end)))
    for rect in getattr(pcb, "gr_rects", []) or []:
        if str(getattr(rect, "layer", "")) == "Edge.Cuts":
            s, e = _coord(rect.start), _coord(rect.end)
            corners = [(s[0], s[1]), (e[0], s[1]), (e[0], e[1]), (s[0], e[1])]
            segments.extend((corners[i], corners[(i + 1) % 4]) for i in range(4))
    for circle in getattr(pcb, "gr_circles", []) or []:
        if str(getattr(circle, "layer", "")) == "Edge.Cuts":
            center = _coord(circle.center)
            edge = _coord(circle.end)
            radius = math.hypot(edge[0] - center[0], edge[1] - center[1])
            circles.append(bg.Circle(center, radius))

    return bg.build_outline(segments=segments, arcs=arcs, circles=circles)


def _board_metrics_from_loops(
    outer: bg.Loop | None, holes: list[bg.Loop]
) -> tuple[float | None, float | None, float | None]:
    area = bg.board_area(outer, holes)
    bbox = bg.loop_bbox(outer) if outer else None
    if area is None or bbox is None:
        return None, None, None
    min_x, min_y, max_x, max_y = bbox
    return area, max_x - min_x, max_y - min_y


# ---------------------------------------------------------------------------
# Component and net metrics
# ---------------------------------------------------------------------------


def _component_and_net_metrics(
    pcb: Any,
) -> tuple[float, float, float, int, int, int, int, int, int]:
    comp_area = top_area = bottom_area = 0.0
    comp_count = top_count = bottom_count = pad_count = 0
    net_pin_counts: dict[str, int] = {}

    for fp in getattr(pcb, "footprints", []) or []:
        pads = getattr(fp, "pads", []) or []
        if not pads:
            continue

        bbox = _footprint_bbox_mm(fp)
        if bbox is None:
            continue

        comp_count += 1
        area = max(0.0, (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
        comp_area += area

        layer = str(getattr(fp, "layer", "") or "")
        if layer.startswith("B."):
            bottom_count += 1
            bottom_area += area
        else:
            top_count += 1
            top_area += area

        for pad in pads:
            pad_count += 1
            pad_net = getattr(pad, "net", None)
            if pad_net is None:
                continue
            net_name = str(getattr(pad_net, "name", "") or "").strip()
            net_number = int(getattr(pad_net, "number", 0) or 0)
            if not net_name and net_number <= 0:
                continue
            key = net_name or str(net_number)
            net_pin_counts[key] = net_pin_counts.get(key, 0) + 1

    routable = [c for c in net_pin_counts.values() if c >= 2]
    net_count = len(routable)
    conn_count = sum(c - 1 for c in routable)

    return (
        comp_area,
        top_area,
        bottom_area,
        comp_count,
        top_count,
        bottom_count,
        pad_count,
        net_count,
        conn_count,
    )


def _components_inside_outside(
    pcb: Any, outer: bg.Loop | None, holes: list[bg.Loop]
) -> tuple[int, int]:
    """Count footprints fully on the board. A footprint is "inside" only
    when every pad center lies inside ``outer`` and outside every hole."""
    if outer is None or len(outer) < 3:
        count = sum(
            1
            for fp in (getattr(pcb, "footprints", []) or [])
            if getattr(fp, "pads", None)
        )
        return 0, count

    inside = outside = 0
    for fp in getattr(pcb, "footprints", []) or []:
        pads = getattr(fp, "pads", None)
        if not pads:
            continue
        fp_at = getattr(fp, "at", None)
        if fp_at is None:
            continue
        fp_x = float(getattr(fp_at, "x", 0.0) or 0.0)
        fp_y = float(getattr(fp_at, "y", 0.0) or 0.0)
        fp_r = float(getattr(fp_at, "r", 0.0) or 0.0)

        all_inside = True
        for pad in pads:
            pad_at = getattr(pad, "at", None)
            if pad_at is None:
                continue
            rel_x = float(getattr(pad_at, "x", 0.0) or 0.0)
            rel_y = float(getattr(pad_at, "y", 0.0) or 0.0)
            dx, dy = _rotate(rel_x, rel_y, fp_r)
            px, py = fp_x + dx, fp_y + dy
            if not bg.point_in_board(px, py, outer, holes):
                all_inside = False
                break

        if all_inside:
            inside += 1
        else:
            outside += 1
    return inside, outside


def _layer_count(pcb: Any) -> int | None:
    stackup = getattr(getattr(pcb, "setup", None), "stackup", None)
    if stackup and getattr(stackup, "layers", None):
        copper = [
            ly for ly in stackup.layers if str(getattr(ly, "type", "")) == "copper"
        ]
        if copper:
            return len(copper)

    count = sum(
        1
        for ly in (getattr(pcb, "layers", None) or [])
        if str(getattr(ly, "type", "")).lower() in {"signal", "power", "mixed"}
    )
    return count or None


def _footprint_bbox_mm(fp: Any) -> tuple[float, float, float, float] | None:
    fp_at = getattr(fp, "at", None)
    pads = getattr(fp, "pads", None)
    if fp_at is None or pads is None:
        return None

    fp_x = float(getattr(fp_at, "x", 0.0) or 0.0)
    fp_y = float(getattr(fp_at, "y", 0.0) or 0.0)
    fp_r = float(getattr(fp_at, "r", 0.0) or 0.0)

    points: list[tuple[float, float]] = []
    for pad in pads:
        pad_at = getattr(pad, "at", None)
        pad_size = getattr(pad, "size", None)
        if pad_at is None or pad_size is None:
            continue
        w = float(getattr(pad_size, "w", 0.0) or 0.0)
        h = float(getattr(pad_size, "h", w) or w)
        if w <= 0 or h <= 0:
            continue

        rel_x = float(getattr(pad_at, "x", 0.0) or 0.0)
        rel_y = float(getattr(pad_at, "y", 0.0) or 0.0)
        rel_r = float(getattr(pad_at, "r", 0.0) or 0.0)

        dx, dy = _rotate(rel_x, rel_y, fp_r)
        cx, cy = fp_x + dx, fp_y + dy
        abs_r = fp_r + rel_r

        hw, hh = w / 2, h / 2
        for ox, oy in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)):
            rx, ry = _rotate(ox, oy, abs_r)
            points.append((cx + rx, cy + ry))

    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


# ---------------------------------------------------------------------------
# Risk scoring
# ---------------------------------------------------------------------------


def _stackup_risk(
    *,
    layer_count: int | None,
    placement_utilization: float | None,
    pad_density: float | None,
    connection_density: float | None,
) -> str:
    score = 0
    if placement_utilization is not None:
        pu = placement_utilization
        score += 2 if pu >= 0.7 else (1 if pu >= 0.55 else 0)
    if pad_density is not None:
        score += 2 if pad_density >= 0.7 else (1 if pad_density >= 0.4 else 0)
    if connection_density is not None:
        cd = connection_density
        score += 2 if cd >= 0.18 else (1 if cd >= 0.1 else 0)
    if layer_count is None:
        score += 1
    elif layer_count <= 2:
        score += 2
    elif layer_count <= 4:
        score += 1

    if score >= 5:
        return "high"
    if score >= 3:
        return "medium"
    return "low"


def _recommendation(
    *,
    layer_count: int | None,
    sidedness: str,
    stackup_risk: str,
    placement_utilization: float | None,
    top_only_utilization: float | None,
    bottom_component_count: int,
) -> str:
    if stackup_risk == "high":
        if (layer_count or 0) <= 2:
            return "Current 2-layer stackup looks tight for this demand."
        return (
            "Placement and routing demand look high for the current board technology."
        )

    if (
        sidedness == "top-only"
        and bottom_component_count == 0
        and top_only_utilization is not None
        and top_only_utilization >= 0.55
    ):
        return (
            "Top-side placement looks dense; allowing bottom-side placement may help."
        )

    if placement_utilization is not None and placement_utilization < 0.35:
        return "Board looks relatively roomy for placement."

    if stackup_risk == "medium":
        return "Board looks feasible, but density is starting to get tight."

    return "Current stackup and placement demand look reasonable."


def _sidedness_label(top: int, bottom: int) -> str:
    if top > 0 and bottom == 0:
        return "top-only"
    if bottom > 0 and top == 0:
        return "bottom-only"
    if top > 0 and bottom > 0:
        return "dual-side"
    return "unknown"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rotate(x: float, y: float, deg: float) -> tuple[float, float]:
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return x * c - y * s, x * s + y * c


def _divide(n: float, d: float | None) -> float | None:
    return n / d if d and d > 0 else None


def _round(v: float | None, d: int = 1) -> float | None:
    return round(float(v), d) if v is not None else None
