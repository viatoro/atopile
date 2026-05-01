"""Core PCB diff engine: matching + classification."""

from __future__ import annotations

import hashlib
from collections import Counter

from atopile.server.domains.diff_models import (
    DiffConfig,
    DiffElementStatus,
    DiffResult,
    DiffStatus,
    PointXY,
)
from atopile.server.domains.layout_models import (
    DrawingModel,
    FootprintModel,
    RenderModel,
    TextModel,
    TrackModel,
    ViaModel,
    ZoneModel,
)


def _pos_equal(ax: float, ay: float, bx: float, by: float, tol: float) -> bool:
    return abs(ax - bx) <= tol and abs(ay - by) <= tol


def _angle_equal(a: float, b: float, tol: float) -> bool:
    diff = abs(a - b) % 360
    return min(diff, 360 - diff) <= tol


def _geometry_hash(drawing: DrawingModel) -> str:
    """Hash drawing geometry for fallback matching."""
    h = hashlib.md5(usedforsecurity=False)
    h.update(drawing.type.encode())
    h.update((drawing.layer or "").encode())
    h.update(f"{drawing.width:.4f}".encode())
    if drawing.type in ("line", "arc", "rect"):
        h.update(f"{drawing.start.x:.4f},{drawing.start.y:.4f}".encode())  # type: ignore[union-attr]
        h.update(f"{drawing.end.x:.4f},{drawing.end.y:.4f}".encode())  # type: ignore[union-attr]
    if drawing.type == "circle":
        h.update(f"{drawing.center.x:.4f},{drawing.center.y:.4f}".encode())  # type: ignore[union-attr]
        h.update(f"{drawing.end.x:.4f},{drawing.end.y:.4f}".encode())  # type: ignore[union-attr]
    if drawing.type == "arc":
        h.update(f"{drawing.mid.x:.4f},{drawing.mid.y:.4f}".encode())  # type: ignore[union-attr]
    if drawing.type in ("polygon", "curve"):
        for pt in drawing.points:  # type: ignore[union-attr]
            h.update(f"{pt.x:.4f},{pt.y:.4f}".encode())
    return h.hexdigest()


class DiffEngine:
    def __init__(self, config: DiffConfig | None = None) -> None:
        self._config = config or DiffConfig()

    def compute(self, model_a: RenderModel, model_b: RenderModel) -> DiffResult:
        elements: list[DiffElementStatus] = []
        tol = self._config.position_tolerance
        atol = self._config.angle_tolerance

        # --- Footprints ---
        elements.extend(self._diff_footprints(model_a, model_b, tol, atol))

        # --- Tracks ---
        elements.extend(self._diff_tracks(model_a, model_b, tol))

        # --- Vias ---
        elements.extend(self._diff_vias(model_a, model_b, tol))

        # --- Zones ---
        elements.extend(self._diff_zones(model_a, model_b))

        # --- Drawings ---
        elements.extend(self._diff_drawings(model_a, model_b))

        # --- Texts ---
        elements.extend(self._diff_texts(model_a, model_b, tol))

        # Build net_names
        net_names: dict[int, str] = {}
        for zone in (*model_a.zones, *model_b.zones):
            if zone.net and zone.net_name:
                net_names[zone.net] = zone.net_name

        # Summary
        summary: dict[str, int] = dict(Counter(el.status.value for el in elements))

        return DiffResult(
            model_a=model_a,
            model_b=model_b,
            elements=elements,
            net_names=net_names,
            summary=summary,
        )

    # ------------------------------------------------------------------
    # Footprints
    # ------------------------------------------------------------------

    def _diff_footprints(
        self,
        model_a: RenderModel,
        model_b: RenderModel,
        tol: float,
        atol: float,
    ) -> list[DiffElementStatus]:
        results: list[DiffElementStatus] = []

        # Build lookup maps
        by_uuid_b = {fp.uuid: fp for fp in model_b.footprints if fp.uuid}

        # Fallback: reference + name
        by_ref_a: dict[str, FootprintModel] = {}
        for fp in model_a.footprints:
            key = f"{fp.reference}:{fp.name}"
            by_ref_a.setdefault(key, fp)
        by_ref_b: dict[str, FootprintModel] = {}
        for fp in model_b.footprints:
            key = f"{fp.reference}:{fp.name}"
            by_ref_b.setdefault(key, fp)

        matched_b: set[str] = set()

        for fp_a in model_a.footprints:
            fp_b: FootprintModel | None = None

            # UUID match
            if fp_a.uuid and fp_a.uuid in by_uuid_b:
                fp_b = by_uuid_b[fp_a.uuid]
            else:
                # Fallback
                key = f"{fp_a.reference}:{fp_a.name}"
                fp_b = by_ref_b.get(key)

            if fp_b is None:
                results.append(
                    DiffElementStatus(
                        uuid_a=fp_a.uuid,
                        element_type="footprint",
                        status=DiffStatus.deleted,
                        reference=fp_a.reference,
                        name=fp_a.name,
                        value=fp_a.value,
                        position_a=PointXY(x=fp_a.at.x, y=fp_a.at.y),
                    )
                )
                continue

            if fp_b.uuid:
                matched_b.add(fp_b.uuid)
            elif fp_b.reference:
                matched_b.add(f"{fp_b.reference}:{fp_b.name}")

            status = self._classify_footprint(fp_a, fp_b, tol, atol)
            results.append(
                DiffElementStatus(
                    uuid_a=fp_a.uuid,
                    uuid_b=fp_b.uuid,
                    element_type="footprint",
                    status=status,
                    reference=fp_a.reference or fp_b.reference,
                    name=fp_a.name,
                    value=fp_a.value or fp_b.value,
                    position_a=PointXY(x=fp_a.at.x, y=fp_a.at.y),
                    position_b=PointXY(x=fp_b.at.x, y=fp_b.at.y),
                )
            )

        # Added footprints (in B but not matched)
        for fp_b in model_b.footprints:
            is_matched = False
            if fp_b.uuid and fp_b.uuid in matched_b:
                is_matched = True
            elif f"{fp_b.reference}:{fp_b.name}" in matched_b:
                is_matched = True

            if not is_matched:
                results.append(
                    DiffElementStatus(
                        uuid_b=fp_b.uuid,
                        element_type="footprint",
                        status=DiffStatus.added,
                        reference=fp_b.reference,
                        name=fp_b.name,
                        value=fp_b.value,
                        position_b=PointXY(x=fp_b.at.x, y=fp_b.at.y),
                    )
                )

        return results

    def _classify_footprint(
        self,
        a: FootprintModel,
        b: FootprintModel,
        tol: float,
        atol: float,
    ) -> DiffStatus:
        pos_same = _pos_equal(a.at.x, a.at.y, b.at.x, b.at.y, tol)
        angle_same = _angle_equal(a.at.r, b.at.r, atol)
        layer_same = a.layer == b.layer
        value_same = a.value == b.value

        if pos_same and angle_same and layer_same and value_same:
            # Check pads count as well
            if len(a.pads) == len(b.pads):
                return DiffStatus.unchanged
            return DiffStatus.modified

        if not pos_same or not angle_same:
            if layer_same and value_same:
                return DiffStatus.moved
        return DiffStatus.modified

    # ------------------------------------------------------------------
    # Tracks
    # ------------------------------------------------------------------

    def _diff_tracks(
        self,
        model_a: RenderModel,
        model_b: RenderModel,
        tol: float,
    ) -> list[DiffElementStatus]:
        results: list[DiffElementStatus] = []
        by_uuid_b = {t.uuid: t for t in model_b.tracks if t.uuid}

        # Fallback key
        def track_key(t: TrackModel) -> str:
            return (
                f"{t.start.x:.4f},{t.start.y:.4f},{t.end.x:.4f},{t.end.y:.4f},"
                f"{t.layer},{t.width:.4f},{t.net}"
            )

        by_key_b: dict[str, TrackModel] = {}
        for t in model_b.tracks:
            by_key_b.setdefault(track_key(t), t)

        matched_b_ids: set[int] = set()

        for t_a in model_a.tracks:
            t_b: TrackModel | None = None
            if t_a.uuid and t_a.uuid in by_uuid_b:
                t_b = by_uuid_b[t_a.uuid]
            else:
                t_b = by_key_b.get(track_key(t_a))

            if t_b is None:
                results.append(
                    DiffElementStatus(
                        uuid_a=t_a.uuid,
                        element_type="track",
                        status=DiffStatus.deleted,
                        net=t_a.net,
                        position_a=PointXY(x=t_a.start.x, y=t_a.start.y),
                    )
                )
                continue

            matched_b_ids.add(id(t_b))

            start_same = _pos_equal(
                t_a.start.x, t_a.start.y, t_b.start.x, t_b.start.y, tol
            )
            end_same = _pos_equal(t_a.end.x, t_a.end.y, t_b.end.x, t_b.end.y, tol)

            if (
                start_same
                and end_same
                and t_a.layer == t_b.layer
                and t_a.width == t_b.width
            ):
                status = DiffStatus.unchanged
            elif not start_same or not end_same:
                status = DiffStatus.moved
            else:
                status = DiffStatus.modified

            results.append(
                DiffElementStatus(
                    uuid_a=t_a.uuid,
                    uuid_b=t_b.uuid,
                    element_type="track",
                    status=status,
                    net=t_a.net,
                    position_a=PointXY(x=t_a.start.x, y=t_a.start.y),
                    position_b=PointXY(x=t_b.start.x, y=t_b.start.y),
                )
            )

        for t_b in model_b.tracks:
            if id(t_b) not in matched_b_ids:
                results.append(
                    DiffElementStatus(
                        uuid_b=t_b.uuid,
                        element_type="track",
                        status=DiffStatus.added,
                        net=t_b.net,
                        position_b=PointXY(x=t_b.start.x, y=t_b.start.y),
                    )
                )

        return results

    # ------------------------------------------------------------------
    # Vias
    # ------------------------------------------------------------------

    def _diff_vias(
        self,
        model_a: RenderModel,
        model_b: RenderModel,
        tol: float,
    ) -> list[DiffElementStatus]:
        results: list[DiffElementStatus] = []
        by_uuid_b = {v.uuid: v for v in model_b.vias if v.uuid}

        def via_key(v: ViaModel) -> str:
            return f"{v.at.x:.4f},{v.at.y:.4f},{v.size:.4f},{v.drill:.4f}"

        by_key_b: dict[str, ViaModel] = {}
        for v in model_b.vias:
            by_key_b.setdefault(via_key(v), v)

        matched_b_ids: set[int] = set()

        for v_a in model_a.vias:
            v_b: ViaModel | None = None
            if v_a.uuid and v_a.uuid in by_uuid_b:
                v_b = by_uuid_b[v_a.uuid]
            else:
                v_b = by_key_b.get(via_key(v_a))

            if v_b is None:
                results.append(
                    DiffElementStatus(
                        uuid_a=v_a.uuid,
                        element_type="via",
                        status=DiffStatus.deleted,
                        position_a=PointXY(x=v_a.at.x, y=v_a.at.y),
                    )
                )
                continue

            matched_b_ids.add(id(v_b))

            pos_same = _pos_equal(v_a.at.x, v_a.at.y, v_b.at.x, v_b.at.y, tol)
            size_same = (
                abs(v_a.size - v_b.size) < tol and abs(v_a.drill - v_b.drill) < tol
            )

            if pos_same and size_same:
                status = DiffStatus.unchanged
            elif not pos_same:
                status = DiffStatus.moved
            else:
                status = DiffStatus.modified

            results.append(
                DiffElementStatus(
                    uuid_a=v_a.uuid,
                    uuid_b=v_b.uuid,
                    element_type="via",
                    status=status,
                    position_a=PointXY(x=v_a.at.x, y=v_a.at.y),
                    position_b=PointXY(x=v_b.at.x, y=v_b.at.y),
                )
            )

        for v_b in model_b.vias:
            if id(v_b) not in matched_b_ids:
                results.append(
                    DiffElementStatus(
                        uuid_b=v_b.uuid,
                        element_type="via",
                        status=DiffStatus.added,
                        position_b=PointXY(x=v_b.at.x, y=v_b.at.y),
                    )
                )

        return results

    # ------------------------------------------------------------------
    # Zones
    # ------------------------------------------------------------------

    def _diff_zones(
        self,
        model_a: RenderModel,
        model_b: RenderModel,
    ) -> list[DiffElementStatus]:
        results: list[DiffElementStatus] = []
        by_uuid_b = {z.uuid: z for z in model_b.zones if z.uuid}

        def zone_key(z: ZoneModel) -> str:
            return f"{z.net}:{','.join(z.layers)}"

        by_key_b: dict[str, ZoneModel] = {}
        for z in model_b.zones:
            by_key_b.setdefault(zone_key(z), z)

        matched_b_ids: set[int] = set()

        for z_a in model_a.zones:
            z_b: ZoneModel | None = None
            if z_a.uuid and z_a.uuid in by_uuid_b:
                z_b = by_uuid_b[z_a.uuid]
            else:
                z_b = by_key_b.get(zone_key(z_a))

            if z_b is None:
                results.append(
                    DiffElementStatus(
                        uuid_a=z_a.uuid,
                        element_type="zone",
                        status=DiffStatus.deleted,
                        net=z_a.net,
                        net_name=z_a.net_name,
                    )
                )
                continue

            matched_b_ids.add(id(z_b))

            # Compare outlines
            outline_same = len(z_a.outline) == len(z_b.outline) and all(
                abs(a.x - b.x) < 0.01 and abs(a.y - b.y) < 0.01
                for a, b in zip(z_a.outline, z_b.outline)
            )
            status = DiffStatus.unchanged if outline_same else DiffStatus.modified

            results.append(
                DiffElementStatus(
                    uuid_a=z_a.uuid,
                    uuid_b=z_b.uuid,
                    element_type="zone",
                    status=status,
                    net=z_a.net,
                    net_name=z_a.net_name or z_b.net_name,
                )
            )

        for z_b in model_b.zones:
            if id(z_b) not in matched_b_ids:
                results.append(
                    DiffElementStatus(
                        uuid_b=z_b.uuid,
                        element_type="zone",
                        status=DiffStatus.added,
                        net=z_b.net,
                        net_name=z_b.net_name,
                    )
                )

        return results

    # ------------------------------------------------------------------
    # Drawings
    # ------------------------------------------------------------------

    def _diff_drawings(
        self,
        model_a: RenderModel,
        model_b: RenderModel,
    ) -> list[DiffElementStatus]:
        results: list[DiffElementStatus] = []
        by_uuid_b = {d.uuid: d for d in model_b.drawings if d.uuid}
        by_hash_b: dict[str, DrawingModel] = {}
        for d in model_b.drawings:
            by_hash_b.setdefault(_geometry_hash(d), d)

        matched_b_ids: set[int] = set()

        for d_a in model_a.drawings:
            d_b: DrawingModel | None = None
            if d_a.uuid and d_a.uuid in by_uuid_b:
                d_b = by_uuid_b[d_a.uuid]
            else:
                d_b = by_hash_b.get(_geometry_hash(d_a))

            if d_b is None:
                results.append(
                    DiffElementStatus(
                        uuid_a=d_a.uuid,
                        element_type="drawing",
                        status=DiffStatus.deleted,
                    )
                )
                continue

            matched_b_ids.add(id(d_b))

            same_hash = _geometry_hash(d_a) == _geometry_hash(d_b)
            status = DiffStatus.unchanged if same_hash else DiffStatus.modified

            results.append(
                DiffElementStatus(
                    uuid_a=d_a.uuid,
                    uuid_b=d_b.uuid,
                    element_type="drawing",
                    status=status,
                )
            )

        for d_b in model_b.drawings:
            if id(d_b) not in matched_b_ids:
                results.append(
                    DiffElementStatus(
                        uuid_b=d_b.uuid,
                        element_type="drawing",
                        status=DiffStatus.added,
                    )
                )

        return results

    # ------------------------------------------------------------------
    # Texts
    # ------------------------------------------------------------------

    def _diff_texts(
        self,
        model_a: RenderModel,
        model_b: RenderModel,
        tol: float,
    ) -> list[DiffElementStatus]:
        results: list[DiffElementStatus] = []
        by_uuid_b = {t.uuid: t for t in model_b.texts if t.uuid}

        def text_key(t: TextModel) -> str:
            return f"{t.text}:{t.layer}"

        by_key_b: dict[str, TextModel] = {}
        for t in model_b.texts:
            by_key_b.setdefault(text_key(t), t)

        matched_b_ids: set[int] = set()

        for t_a in model_a.texts:
            t_b: TextModel | None = None
            if t_a.uuid and t_a.uuid in by_uuid_b:
                t_b = by_uuid_b[t_a.uuid]
            else:
                t_b = by_key_b.get(text_key(t_a))

            if t_b is None:
                results.append(
                    DiffElementStatus(
                        uuid_a=t_a.uuid,
                        element_type="text",
                        status=DiffStatus.deleted,
                    )
                )
                continue

            matched_b_ids.add(id(t_b))

            pos_same = _pos_equal(t_a.at.x, t_a.at.y, t_b.at.x, t_b.at.y, tol)
            text_same = t_a.text == t_b.text

            if pos_same and text_same:
                status = DiffStatus.unchanged
            elif not pos_same and text_same:
                status = DiffStatus.moved
            else:
                status = DiffStatus.modified

            results.append(
                DiffElementStatus(
                    uuid_a=t_a.uuid,
                    uuid_b=t_b.uuid,
                    element_type="text",
                    status=status,
                    position_a=PointXY(x=t_a.at.x, y=t_a.at.y),
                    position_b=PointXY(x=t_b.at.x, y=t_b.at.y),
                )
            )

        for t_b in model_b.texts:
            if id(t_b) not in matched_b_ids:
                results.append(
                    DiffElementStatus(
                        uuid_b=t_b.uuid,
                        element_type="text",
                        status=DiffStatus.added,
                        position_b=PointXY(x=t_b.at.x, y=t_b.at.y),
                    )
                )

        return results
