"""EDA format conversion: KiCad ↔ HL ↔ DeepPCB.

Pure functions (no ``self``, no service state). The service calls
these to shuttle layouts between the on-disk KiCad format, our
intermediate HL model, and DeepPCB's JSON wire format, plus to apply
a DeepPCB result back onto the original KiCad file via an HL diff.

.. warning::

    This module will be overhauled when the stage/swap-to-HL branch
    merges — at that point the HL model becomes the primary in-memory
    representation of the PCB and much of the load/save plumbing here
    (HL ↔ KiCad round-tripping, fingerprint-based diff application)
    moves into the HL layer itself. Treat the current shape as
    transitional; avoid adding new responsibilities here.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from atopile.autolayout.deeppcb.models import JobType

log = logging.getLogger(__name__)


def load_hl(layout_path: Path) -> Any:
    """Load any supported EDA file into an HL PCB model."""
    suffix = layout_path.suffix.lower()
    if suffix == ".kicad_pcb":
        from faebryk.libs.eda.kicad.convert.pcb.il_hl import convert_pcb_il_to_hl
        from faebryk.libs.kicad.fileformats import kicad

        pcb_file = kicad.loads(kicad.pcb.PcbFile, layout_path)
        return convert_pcb_il_to_hl(pcb_file.kicad_pcb)
    raise ValueError(f"Unsupported layout format: {suffix}")


def save_hl(
    hl_pcb: Any,
    original_path: Path,
    out_path: Path,
    job_type: JobType = JobType.ROUTING,
) -> Path:
    """Write an HL PCB model back to the original EDA format.

    Uses the original file as a base and applies HL changes on top,
    preserving format-specific metadata the HL model doesn't carry.
    job_type filters the diff: Placement only moves components,
    Routing only changes traces/vias.
    """
    suffix = original_path.suffix.lower()
    if suffix == ".kicad_pcb":
        return _save_hl_to_kicad(hl_pcb, original_path, out_path, job_type)
    raise ValueError(f"Unsupported layout format: {suffix}")


def hl_to_deeppcb(hl_pcb: Any, work_dir: Path) -> Path:
    """Convert an HL PCB model to DeepPCB JSON."""
    from faebryk.libs.eda.deeppcb import convert_hl_to_ll, dump

    ll_board = convert_hl_to_ll(hl_pcb)
    json_content = dump(ll_board)
    out_path = work_dir / "board.deeppcb"
    out_path.write_text(json_content)
    return out_path


def deeppcb_to_hl(deeppcb_content: str) -> Any:
    """Convert DeepPCB JSON to an HL PCB model."""
    from faebryk.libs.eda.deeppcb import convert_ll_to_hl, load

    return convert_ll_to_hl(load(deeppcb_content))


def _zone_layers(zone: object) -> list[str]:
    """Extract layer names from a KiCad Zone object."""
    layers = list(getattr(zone, "layers", []) or [])
    if layers:
        return [str(ly) for ly in layers]
    layer = getattr(zone, "layer", None)
    return [str(layer)] if layer else []


def _save_hl_to_kicad(
    hl_result: Any,
    original_kicad_path: Path,
    out_path: Path,
    job_type: JobType = JobType.ROUTING,
) -> Path:
    """Apply HL diff onto the original KiCad PCB file.

    Computes what changed between the original HL and the result HL,
    then applies only the changes appropriate for the job type.
    Placement: only component moves. Routing: only traces/vias.
    Warns if the provider made unexpected changes.
    """

    from atopile.autolayout.hl_diff import diff_hl
    from faebryk.libs.eda.hl.models.pcb import Circle, Segment
    from faebryk.libs.eda.hl.models.pcb import Polygon as HLPolygon
    from faebryk.libs.kicad.fileformats import kicad

    # Load original as HL for diff
    hl_original = load_hl(original_kicad_path)

    # Compute what changed
    d = diff_hl(hl_original, hl_result)

    log.info(
        "HL diff: %d moved, +%d/-%d segments, +%d/-%d vias, %d zones",
        len(d.moved_components),
        len(d.added_segments),
        len(d.removed_segments),
        len(d.added_vias),
        len(d.removed_vias),
        len(d.result_zones),
    )

    is_placement = job_type == JobType.PLACEMENT
    is_routing = job_type == JobType.ROUTING

    if is_placement:
        # Moved components invalidate all existing routing. Drop per-segment
        # diff bookkeeping in favour of the unconditional wipe below.
        d.added_segments = []
        d.removed_segments = []
        d.added_vias = []
        d.removed_vias = []
        d.result_zones = []

    if is_routing and d.moved_components:
        log.warning(
            "Routing job produced unexpected component moves: "
            "%d components — ignoring moves",
            len(d.moved_components),
        )
        d.moved_components = []

    if not d.has_changes and not is_placement:
        log.info("No changes to apply")
        shutil.copy2(original_kicad_path, out_path)
        return out_path

    # Load original KiCad file
    pcb_file = kicad.loads(kicad.pcb.PcbFile, original_kicad_path)
    pcb = pcb_file.kicad_pcb

    if is_placement:
        # Strip stale routing — components are about to move.
        n_segs = len(getattr(pcb, "segments", []) or [])
        n_vias = len(getattr(pcb, "vias", []) or [])
        n_zones = len(getattr(pcb, "zones", []) or [])
        pcb.segments = []
        pcb.vias = []
        pcb.zones = []
        if n_segs or n_vias or n_zones:
            log.info(
                "Stripped existing routing for placement: "
                "%d segments, %d vias, %d zones",
                n_segs,
                n_vias,
                n_zones,
            )

    # Build net name → number mapping
    net_map: dict[str, int] = {}
    for net in getattr(pcb, "nets", []) or []:
        net_map[str(getattr(net, "name", ""))] = int(getattr(net, "number", 0))

    # KiCad's footprint property list is named ``propertys`` (sic) — the
    # obvious ``properties`` attribute doesn't exist.
    if d.moved_components:
        moves = {m.refdes: m for m in d.moved_components}
        applied = 0
        for fp in getattr(pcb, "footprints", []) or []:
            ref = ""
            for prop in getattr(fp, "propertys", []) or []:
                if getattr(prop, "name", "") == "Reference":
                    ref = getattr(prop, "value", "")
                    break
            if ref and ref in moves:
                m = moves[ref]
                fp_at = getattr(fp, "at", None)
                if fp_at:
                    fp_at.x = m.to_x
                    fp_at.y = m.to_y
                    if hasattr(fp_at, "r"):
                        fp_at.r = m.to_rotation
                    applied += 1
        if applied != len(moves):
            log.warning(
                "Applied %d/%d component moves (rest had no matching footprint)",
                applied,
                len(moves),
            )

    # Remove segments that were in the original but not in the result
    if d.removed_segments:
        from atopile.autolayout.hl_diff import _segment_fingerprint

        remove_fps = {_segment_fingerprint(g) for g in d.removed_segments}
        if hasattr(pcb, "segments") and pcb.segments:
            kept = []
            for seg in pcb.segments:
                # Build fingerprint from KiCad segment
                net_name = ""
                seg_net = getattr(seg, "net", None)
                if seg_net is not None:
                    # Reverse lookup: net number → name
                    for name, num in net_map.items():
                        if num == int(seg_net):
                            net_name = name
                            break
                fp = (
                    round(seg.start.x * 1000),
                    round(seg.start.y * 1000),
                    round(seg.end.x * 1000),
                    round(seg.end.y * 1000),
                    net_name,
                    str(getattr(seg, "layer", "")),
                )
                if fp not in remove_fps:
                    kept.append(seg)
            pcb.segments = kept

    # Remove vias
    if d.removed_vias:
        from atopile.autolayout.hl_diff import _via_fingerprint

        remove_fps = {_via_fingerprint(g) for g in d.removed_vias}
        if hasattr(pcb, "vias") and pcb.vias:
            kept = []
            for via in pcb.vias:
                net_name = ""
                via_net = getattr(via, "net", None)
                if via_net is not None:
                    for name, num in net_map.items():
                        if num == int(via_net):
                            net_name = name
                            break
                via_at = getattr(via, "at", None)
                via_layers = tuple(
                    sorted(str(ly) for ly in (getattr(via, "layers", []) or []))
                )
                fp = (
                    round(via_at.x * 1000) if via_at else 0,
                    round(via_at.y * 1000) if via_at else 0,
                    net_name,
                    via_layers,
                )
                if fp not in remove_fps:
                    kept.append(via)
            pcb.vias = kept

    # Add new segments
    for geom in d.added_segments:
        if not isinstance(geom.shape, Segment):
            continue
        net_name = geom.net.name if geom.net else ""
        net_num = net_map.get(net_name, 0)
        layers = [lid.name for lid in geom.layers]
        seg = kicad.pcb.Segment(
            start=kicad.pcb.Xy(
                x=geom.shape.start.x + geom.location.x,
                y=geom.shape.start.y + geom.location.y,
            ),
            end=kicad.pcb.Xy(
                x=geom.shape.end.x + geom.location.x,
                y=geom.shape.end.y + geom.location.y,
            ),
            width=0.25,
            layer=layers[0] if layers else "F.Cu",
            net=net_num,
        )
        if pcb.segments is None:
            pcb.segments = []
        pcb.segments.append(seg)

    # Add new vias
    for geom in d.added_vias:
        if not isinstance(geom.shape, Circle):
            continue
        net_name = geom.net.name if geom.net else ""
        net_num = net_map.get(net_name, 0)
        layers = [lid.name for lid in geom.layers]
        via = kicad.pcb.Via(
            at=kicad.pcb.Xy(
                x=geom.location.x,
                y=geom.location.y,
            ),
            size=geom.shape.radius * 2,
            drill=geom.shape.radius,
            layers=([layers[0], layers[-1]] if len(layers) >= 2 else layers),
            net=net_num,
        )
        if pcb.vias is None:
            pcb.vias = []
        pcb.vias.append(via)

    # Replace zones with result zones from DeepPCB
    if d.result_zones:
        # Build settings lookup from original zones by (net_name, layer)
        zone_settings: dict[tuple[str, str], kicad.pcb.Zone] = {}
        for z in getattr(pcb, "zones", []) or []:
            z_net = z.net_name or ""
            for layer in _zone_layers(z):
                key = (z_net, layer)
                if key not in zone_settings:
                    zone_settings[key] = z

        new_zones = []
        for geom in d.result_zones:
            if not isinstance(geom.shape, HLPolygon):
                continue
            zone_net_name = geom.net.name if geom.net else ""
            zone_net_num = net_map.get(zone_net_name, 0)
            layers = [lid.name for lid in geom.layers]

            # Find original zone to copy settings from
            donor = None
            for layer in layers:
                donor = zone_settings.get((zone_net_name, layer))
                if donor:
                    break

            # Build polygon outline
            outline_pts = kicad.pcb.Pts(
                xys=[kicad.pcb.Xy(x=v.x, y=v.y) for v in geom.shape.vertices]
            )

            # Build filled polygons from filled_shapes
            filled_polys: list[kicad.pcb.FilledPolygon] = []
            if geom.filled_shapes:
                for fp_shape in geom.filled_shapes:
                    if not isinstance(fp_shape, HLPolygon):
                        continue
                    fp_pts = kicad.pcb.Pts(
                        xys=[kicad.pcb.Xy(x=v.x, y=v.y) for v in fp_shape.vertices]
                    )
                    # Assign to each layer of the zone
                    for layer in layers:
                        filled_polys.append(
                            kicad.pcb.FilledPolygon(layer=layer, pts=fp_pts)
                        )

            zone = kicad.pcb.Zone(
                net=zone_net_num,
                net_name=zone_net_name,
                layers=layers if len(layers) > 1 else [],
                layer=layers[0] if len(layers) == 1 else None,
                uuid=kicad.gen_uuid(),
                name=donor.name if donor else None,
                polygon=kicad.pcb.Polygon(
                    pts=outline_pts,
                    layers=[],
                    layer=None,
                    solder_mask_margin=None,
                    stroke=None,
                    fill=None,
                    locked=None,
                    uuid=None,
                ),
                min_thickness=(donor.min_thickness if donor else 0.2),
                filled_areas_thickness=(
                    donor.filled_areas_thickness if donor else False
                ),
                fill=donor.fill
                if donor
                else kicad.pcb.ZoneFill(
                    enable=kicad.pcb.E_zone_fill_enable.YES,
                    mode=None,
                    hatch_thickness=0.0,
                    hatch_gap=0.5,
                    hatch_orientation=0,
                    hatch_smoothing_level=0,
                    hatch_smoothing_value=0,
                    hatch_border_algorithm=(
                        kicad.pcb.E_zone_hatch_border_algorithm.HATCH_THICKNESS
                    ),
                    hatch_min_hole_area=0.3,
                    thermal_gap=0.2,
                    thermal_bridge_width=0.2,
                    smoothing=None,
                    radius=1,
                    island_area_min=10.0,
                    arc_segments=None,
                    island_removal_mode=None,
                ),
                hatch=(
                    donor.hatch
                    if donor
                    else kicad.pcb.Hatch(
                        mode=kicad.pcb.E_zone_hatch_mode.EDGE, pitch=0.5
                    )
                ),
                priority=donor.priority if donor else 0,
                keepout=donor.keepout if donor else None,
                connect_pads=(
                    donor.connect_pads
                    if donor
                    else kicad.pcb.ConnectPads(mode=None, clearance=0.2)
                ),
                filled_polygon=filled_polys,
                placement=donor.placement if donor else None,
                attr=donor.attr if donor else None,
            )
            new_zones.append(zone)

        orig_zone_count = len(getattr(pcb, "zones", []) or [])
        pcb.zones = new_zones
        log.info(
            "Replaced %d original zones with %d from result",
            orig_zone_count,
            len(new_zones),
        )

    # Write — everything not in the diff is preserved from the original
    kicad.dumps(pcb_file, out_path)
    return out_path
