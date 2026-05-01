"""Board-outline geometry — segments/arcs/circles → outer ring + holes.

Format-agnostic: adapters feed ``Arc``/``Circle``/segment tuples into
:func:`build_outline`. Largest-area closed loop is the outer ring; the
rest are treated as (flat) cutouts. Nested cutouts are not modeled.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

Point = tuple[float, float]
Segment = tuple[Point, Point]
Loop = list[Point]

_CHAIN_EPS_MM = 0.01
_ARC_STEP_DEG = 5.0
_CIRCLE_SEGMENTS = 64


@dataclass(frozen=True)
class Arc:
    start: Point
    mid: Point
    end: Point


@dataclass(frozen=True)
class Circle:
    center: Point
    radius: float


def build_outline(
    *,
    segments: Iterable[Segment] = (),
    arcs: Iterable[Arc] = (),
    circles: Iterable[Circle] = (),
) -> tuple[Loop | None, list[Loop]]:
    """Assemble primitives into ``(outer_loop, hole_loops)``. Returns
    ``(None, [])`` if no closed loop of >=3 points can be built."""
    raw_segments: list[Segment] = list(segments)
    for arc in arcs:
        pts = tessellate_arc(arc)
        raw_segments.extend((pts[i], pts[i + 1]) for i in range(len(pts) - 1))

    loops = _chain_into_loops(raw_segments)
    for circle in circles:
        if circle.radius > 0:
            loops.append(_circle_loop(circle.center, circle.radius))

    if not loops:
        return None, []

    loops.sort(key=lambda lp: abs(loop_area(lp)), reverse=True)
    return loops[0], loops[1:]


def loop_area(loop: Loop) -> float:
    """Signed shoelace area; sign carries winding direction."""
    n = len(loop)
    if n < 3:
        return 0.0
    total = 0.0
    for i in range(n):
        x1, y1 = loop[i]
        x2, y2 = loop[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return total / 2


def loop_bbox(loop: Loop) -> tuple[float, float, float, float] | None:
    if not loop:
        return None
    xs = [p[0] for p in loop]
    ys = [p[1] for p in loop]
    return min(xs), min(ys), max(xs), max(ys)


def board_area(outer: Loop | None, holes: Iterable[Loop]) -> float | None:
    """Net board area: |outer| − Σ|holes|. ``None`` if no valid outer."""
    if outer is None or len(outer) < 3:
        return None
    area = abs(loop_area(outer)) - sum(abs(loop_area(h)) for h in holes)
    return area if area > 0 else None


def point_in_board(
    x: float, y: float, outer: Loop | None, holes: Iterable[Loop]
) -> bool:
    """True iff (x, y) lies inside ``outer`` and outside every hole."""
    if outer is None or not _point_in_polygon(x, y, outer):
        return False
    return not any(_point_in_polygon(x, y, h) for h in holes)


def tessellate_arc(arc: Arc) -> list[Point]:
    """Sample an arc at ~5° steps. Falls back to ``[start, mid, end]``
    for collinear inputs."""
    (ax, ay), (bx, by), (cx, cy) = arc.start, arc.mid, arc.end
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return [arc.start, arc.mid, arc.end]
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
    radius = math.hypot(ax - ux, ay - uy)
    if radius <= 0:
        return [arc.start, arc.mid, arc.end]

    a_s = math.atan2(ay - uy, ax - ux)
    a_m = math.atan2(by - uy, bx - ux)
    a_e = math.atan2(cy - uy, cx - ux)

    sweep_ccw = _norm_angle(a_e - a_s)
    mid_ccw = _norm_angle(a_m - a_s)
    if 0 < mid_ccw < sweep_ccw:
        sweep, direction = sweep_ccw, 1.0
    else:
        sweep, direction = _norm_angle(a_s - a_e), -1.0

    n = max(2, math.ceil(sweep / math.radians(_ARC_STEP_DEG)))
    return [
        (
            ux + radius * math.cos(a_s + direction * sweep * (i / n)),
            uy + radius * math.sin(a_s + direction * sweep * (i / n)),
        )
        for i in range(n + 1)
    ]


def _chain_into_loops(segments: list[Segment]) -> list[Loop]:
    """Walk segments end-to-end into closed loops. Open chains are dropped."""
    if not segments:
        return []

    def close(a: Point, b: Point) -> bool:
        return abs(a[0] - b[0]) < _CHAIN_EPS_MM and abs(a[1] - b[1]) < _CHAIN_EPS_MM

    remaining = list(segments)
    loops: list[Loop] = []

    while remaining:
        first = remaining.pop(0)
        chain: Loop = [first[0], first[1]]
        closed = False
        while remaining:
            if close(chain[-1], chain[0]):
                closed = True
                break
            tail = chain[-1]
            found = False
            for i, (s, e) in enumerate(remaining):
                if close(tail, s):
                    chain.append(e)
                    remaining.pop(i)
                    found = True
                    break
                if close(tail, e):
                    chain.append(s)
                    remaining.pop(i)
                    found = True
                    break
            if not found:
                break
        if not closed and len(chain) >= 3 and close(chain[-1], chain[0]):
            closed = True
        if closed and len(chain) >= 3:
            loops.append(chain)

    return loops


def _circle_loop(center: Point, radius: float, n: int = _CIRCLE_SEGMENTS) -> Loop:
    cx, cy = center
    return [
        (
            cx + radius * math.cos(2 * math.pi * i / n),
            cy + radius * math.sin(2 * math.pi * i / n),
        )
        for i in range(n)
    ]


def _norm_angle(a: float) -> float:
    two_pi = 2 * math.pi
    a = a % two_pi
    return a + two_pi if a < 0 else a


def _point_in_polygon(x: float, y: float, polygon: Loop) -> bool:
    n = len(polygon)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside
