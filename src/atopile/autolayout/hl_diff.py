"""Diff two HL PCB models and apply changes to an EDA file.

Compares an original HL model against a result HL model (from DeepPCB)
and produces a structured diff. The diff can then be applied to the
original EDA file, modifying only what changed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from faebryk.libs.eda.hl.models.pcb import (
    PCB,
    Circle,
    ConductiveGeometry,
    Polygon,
    Segment,
)

POSITION_TOLERANCE = 0.01  # mm


@dataclass
class ComponentMove:
    """A component that was moved/rotated by the autolayout result."""

    refdes: str
    # Original position (from HL₁)
    from_x: float
    from_y: float
    from_rotation: float
    # New position (from HL₂)
    to_x: float
    to_y: float
    to_rotation: float


@dataclass
class HLDiff:
    """Structured diff between two HL PCB models.

    Only contains what changed — applying this to the original EDA file
    should not modify anything the autolayout provider didn't touch.
    """

    # Components that moved
    moved_components: list[ComponentMove] = field(default_factory=list)

    # New routing from the result (segments and vias to ADD)
    added_segments: list[ConductiveGeometry] = field(default_factory=list)
    added_vias: list[ConductiveGeometry] = field(default_factory=list)

    # Routing that was in the original but not in the result (to REMOVE)
    removed_segments: list[ConductiveGeometry] = field(default_factory=list)
    removed_vias: list[ConductiveGeometry] = field(default_factory=list)

    # Zones from the result (replace all original zones with these)
    result_zones: list[ConductiveGeometry] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(
            self.moved_components
            or self.added_segments
            or self.added_vias
            or self.removed_segments
            or self.removed_vias
            or self.result_zones
        )


def diff_hl(original: PCB, result: PCB) -> HLDiff:
    """Compare two HL PCB models and return what changed.

    The original is from the user's EDA file. The result is from DeepPCB.
    """
    d = HLDiff()

    # --- Component moves ---
    orig_comps = _component_map(original)
    result_comps = _component_map(result)

    for refdes, (ox, oy, orot) in orig_comps.items():
        if refdes in result_comps:
            rx, ry, rrot = result_comps[refdes]
            if (
                abs(ox - rx) > POSITION_TOLERANCE
                or abs(oy - ry) > POSITION_TOLERANCE
                or abs(orot - rrot) > POSITION_TOLERANCE
            ):
                d.moved_components.append(
                    ComponentMove(
                        refdes=refdes,
                        from_x=ox,
                        from_y=oy,
                        from_rotation=orot,
                        to_x=rx,
                        to_y=ry,
                        to_rotation=rrot,
                    )
                )

    # --- Routing changes ---
    # Classify board-level geometries by type
    orig_segments, orig_vias, _orig_zones, _orig_other = _classify_geometries(
        original.geometries
    )
    result_segments, result_vias, result_zones, _result_other = _classify_geometries(
        result.geometries
    )

    # Build fingerprint sets for matching
    orig_seg_fps = {_segment_fingerprint(g): g for g in orig_segments}
    result_seg_fps = {_segment_fingerprint(g): g for g in result_segments}
    orig_via_fps = {_via_fingerprint(g): g for g in orig_vias}
    result_via_fps = {_via_fingerprint(g): g for g in result_vias}

    # Segments added (in result but not original)
    for fp, geom in result_seg_fps.items():
        if fp not in orig_seg_fps:
            d.added_segments.append(geom)

    # Segments removed (in original but not result)
    for fp, geom in orig_seg_fps.items():
        if fp not in result_seg_fps:
            d.removed_segments.append(geom)

    # Vias added
    for fp, geom in result_via_fps.items():
        if fp not in orig_via_fps:
            d.added_vias.append(geom)

    # Vias removed
    for fp, geom in orig_via_fps.items():
        if fp not in result_via_fps:
            d.removed_vias.append(geom)

    # --- Zones: pass through result zones for full replacement ---
    d.result_zones = result_zones

    return d


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _component_map(pcb: PCB) -> dict[str, tuple[float, float, float]]:
    """Extract refdes → (x, y, rotation) from HL collections."""
    result: dict[str, tuple[float, float, float]] = {}
    for col in pcb.collections:
        refdes = col.extra_properties.get("refdes", "")
        if not refdes:
            continue
        # Compute centroid from pad locations
        pad_locs = [
            g.location for pad_col in col.collections for g in pad_col.geometries
        ]
        if pad_locs:
            cx = sum(p.x for p in pad_locs) / len(pad_locs)
            cy = sum(p.y for p in pad_locs) / len(pad_locs)
            result[refdes] = (cx, cy, col.rotation_deg)
    return result


def _classify_geometries(
    geometries: list[ConductiveGeometry],
) -> tuple[
    list[ConductiveGeometry],
    list[ConductiveGeometry],
    list[ConductiveGeometry],
    list[ConductiveGeometry],
]:
    """Split geometries into segments, vias, zones, and other."""
    segments: list[ConductiveGeometry] = []
    vias: list[ConductiveGeometry] = []
    zones: list[ConductiveGeometry] = []
    other: list[ConductiveGeometry] = []

    for g in geometries:
        if isinstance(g.shape, Segment):
            segments.append(g)
        elif isinstance(g.shape, Circle) and len(g.layers) > 1:
            vias.append(g)
        elif isinstance(g.shape, Polygon):
            zones.append(g)
        else:
            other.append(g)

    return segments, vias, zones, other


def _round(v: float) -> int:
    """Round to µm for fingerprinting."""
    return round(v * 1000)


def _segment_fingerprint(g: ConductiveGeometry) -> tuple:
    """Position-based fingerprint for a segment geometry."""
    s = g.shape
    if not isinstance(s, Segment):
        return ()
    net = g.net.name if g.net else ""
    layer = g.layers[0].name if g.layers else ""
    return (
        _round(s.start.x + g.location.x),
        _round(s.start.y + g.location.y),
        _round(s.end.x + g.location.x),
        _round(s.end.y + g.location.y),
        net,
        layer,
    )


def _via_fingerprint(g: ConductiveGeometry) -> tuple:
    """Position-based fingerprint for a via geometry."""
    net = g.net.name if g.net else ""
    layers = tuple(sorted(ly.name for ly in g.layers))
    return (
        _round(g.location.x),
        _round(g.location.y),
        net,
        layers,
    )
