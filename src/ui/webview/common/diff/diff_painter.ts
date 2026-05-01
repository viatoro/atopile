import type { Renderer } from "../layout/webgl/renderer";
import type { RenderModel, FootprintModel, TrackModel, DrawingModel, ViaModel, ZoneModel } from "../layout/types";
import {
    buildLayerMap,
    paintBoardEdges,
    paintZones,
    paintObjects,
    arcToPoints,
} from "../layout/painter";
import type { DiffFilterMode, DiffStatus } from "./types";
import { STATUS_COLORS } from "./types";
import { Vec2 } from "../layout/math";
import { fpTransform } from "../layout/geometry";
import { convexHull, offsetHull } from "../layout/convex_hull";


/**
 * Paint a PCB model with diff status coloring.
 *
 * Elements whose UUID appears in `uuidStatusMap` are tinted with
 * the corresponding status color. Elements not in the map are
 * rendered as unchanged (gray tint).
 */
export function paintDiffBoard(
    renderer: Renderer,
    model: RenderModel,
    uuidStatusMap: Map<string, DiffStatus>,
    hiddenLayers?: Set<string>,
    filterMode?: DiffFilterMode,
    highlightUuids?: Set<string>,
): void {
    const hidden = hiddenLayers ?? new Set<string>();
    const layerById = buildLayerMap(model);
    renderer.dispose_layers();

    if (!hidden.has("Edge.Cuts")) {
        paintBoardEdges(renderer, model, layerById);
    }

    // Paint zones with tint
    paintZones(renderer, model, hidden, layerById, model.zones);

    // Group elements by status for tinted painting
    type StatusGroup = {
        footprints: FootprintModel[];
        tracks: TrackModel[];
        vias: ViaModel[];
        drawings: DrawingModel[];
        texts: typeof model.texts;
    };

    const statusGroups = new Map<DiffStatus, StatusGroup>();

    const initGroup = (): StatusGroup => ({
        footprints: [],
        tracks: [],
        vias: [],
        drawings: [],
        texts: [],
    });

    const classify = <T extends { uuid: string | null }>(
        items: T[],
        getList: (g: StatusGroup) => T[],
    ) => {
        for (const item of items) {
            const status = item.uuid ? (uuidStatusMap.get(item.uuid) ?? "unchanged") : "unchanged";
            if (!statusGroups.has(status)) statusGroups.set(status, initGroup());
            getList(statusGroups.get(status)!).push(item);
        }
    };

    classify(model.footprints, g => g.footprints);
    classify(model.tracks, g => g.tracks);
    classify(model.vias, g => g.vias);
    classify(model.drawings, g => g.drawings);
    classify(model.texts, g => g.texts);

    // Build footprint owner ref map (needed by paintObjects)
    const footprintOwnerByRef = new Map<FootprintModel, string>();
    for (let i = 0; i < model.footprints.length; i++) {
        const fp = model.footprints[i];
        if (fp) {
            footprintOwnerByRef.set(fp, fp.uuid ?? `fp:${i}`);
        }
    }

    // Paint each status group with its tint
    // Paint unchanged first (background), then changed on top
    const paintOrder: DiffStatus[] = ["unchanged", "moved", "modified", "added", "deleted"];
    for (const status of paintOrder) {
        const group = statusGroups.get(status);
        if (!group) continue;

        const hasContent =
            group.footprints.length > 0 ||
            group.tracks.length > 0 ||
            group.vias.length > 0 ||
            group.drawings.length > 0 ||
            group.texts.length > 0;

        if (!hasContent) continue;

        const tint = status === "unchanged" ? undefined : STATUS_COLORS[status];
        paintObjects(
            renderer,
            layerById,
            group.drawings,
            group.texts,
            group.tracks,
            group.vias,
            group.footprints,
            hidden,
            footprintOwnerByRef,
            tint,
        );
    }

    renderer.commit_all_layers();

    // Apply PCB layer visibility (non-__type: layers are toggled at the renderer level)
    for (const layer of model.layers) {
        renderer.set_layer_visible(layer.id, !hidden.has(layer.id));
    }
    renderer.set_layer_visible("Edge.Cuts", !hidden.has("Edge.Cuts"));
}

// ── Tree-aware track painting ─────────────────────────────────────────

function pointKey(x: number, y: number): string {
    return `${x.toFixed(3)},${y.toFixed(3)}`;
}

/**
 * Paint tracks as a tree with continuous pathDist propagation.
 * BFS from degree-1 endpoints so that at junctions all branches
 * inherit the parent's accumulated distance — no phase discontinuities.
 */
function paintTracksAsTree(
    renderer: Renderer,
    tracks: TrackModel[],
    hidden: Set<string>,
): void {
    const r = 1.0, g = 1.0, b = 1.0, a = 1.0;

    // Group by layer:net
    const groups = new Map<string, TrackModel[]>();
    for (const t of tracks) {
        if (!t.layer) continue;
        const key = `${t.layer}:${t.net}`;
        let arr = groups.get(key);
        if (!arr) { arr = []; groups.set(key, arr); }
        arr.push(t);
    }

    for (const [groupKey, groupTracks] of groups) {
        const layer = groupKey.split(":")[0]!;
        if (hidden.has(layer)) continue;
        const renderLayer = renderer.get_layer(layer);

        // Build adjacency: pointKey → track indices
        const adj = new Map<string, number[]>();
        const trackEndpoints: [string, string][] = [];

        for (let i = 0; i < groupTracks.length; i++) {
            const t = groupTracks[i]!;
            const sk = pointKey(t.start.x, t.start.y);
            const ek = pointKey(t.end.x, t.end.y);
            trackEndpoints.push([sk, ek]);

            let arr = adj.get(sk);
            if (!arr) { arr = []; adj.set(sk, arr); }
            arr.push(i);

            arr = adj.get(ek);
            if (!arr) { arr = []; adj.set(ek, arr); }
            arr.push(i);
        }

        const visited = new Set<number>();

        // Find roots: degree-1 endpoints first (natural tree leaves/roots)
        const roots: string[] = [];
        for (const [key, indices] of adj) {
            if (indices.length === 1) roots.push(key);
        }

        // BFS from a root point, painting each track with continuous offset
        const bfsFrom = (rootKey: string, rootOffset: number) => {
            const queue: Array<{ point: string; offset: number }> = [{ point: rootKey, offset: rootOffset }];

            while (queue.length > 0) {
                const { point, offset } = queue.shift()!;
                const indices = adj.get(point);
                if (!indices) continue;

                for (const idx of indices) {
                    if (visited.has(idx)) continue;
                    visited.add(idx);

                    const t = groupTracks[idx]!;
                    const [sk, ek] = trackEndpoints[idx]!;

                    // Orient polyline so entry point is first
                    let pts: Vec2[];
                    let farPoint: string;
                    if (sk === point) {
                        pts = t.mid
                            ? arcToPoints(t.start, t.mid, t.end)
                            : [p2v(t.start), p2v(t.end)];
                        farPoint = ek;
                    } else {
                        pts = t.mid
                            ? arcToPoints(t.end, t.mid, t.start)
                            : [p2v(t.end), p2v(t.start)];
                        farPoint = sk;
                    }

                    const totalDist = renderLayer.geometry.add_polyline(
                        pts, t.width, r, g, b, a, null, offset);

                    queue.push({ point: farPoint, offset: totalDist });
                }
            }
        };

        // BFS from degree-1 roots
        for (const root of roots) {
            bfsFrom(root, 0);
        }

        // Handle remaining unvisited tracks (cycles / disconnected subgraphs)
        for (let i = 0; i < groupTracks.length; i++) {
            if (visited.has(i)) continue;
            visited.add(i);
            const t = groupTracks[i]!;
            const [, ek] = trackEndpoints[i]!;
            const pts = t.mid
                ? arcToPoints(t.start, t.mid, t.end)
                : [p2v(t.start), p2v(t.end)];
            const totalDist = renderLayer.geometry.add_polyline(
                pts, t.width, r, g, b, a, null, 0);
            bfsFrom(ek, totalDist);
        }
    }
}

function p2v(p: { x: number; y: number }): Vec2 {
    return new Vec2(p.x, p.y);
}

// ── Glow geometry helpers ─────────────────────────────────────────────

const GLOW_RADIUS = 0.4;  // extra width for glow on tracks/drawings
const GLOW_MARGIN = 0.3;  // margin for footprint hull expansion
const SILK_LAYERS = new Set(["F.Silkscreen", "B.Silkscreen", "F.Silk", "B.Silk", "F.SilkS", "B.SilkS"]);

/**
 * Collect world-space points from a footprint's silkscreen drawings (excluding text)
 * and copper layer pad corners for convex hull computation.
 */
function collectHullPoints(fp: FootprintModel): Vec2[] {
    const points: Vec2[] = [];

    // Silkscreen drawings (excluding text)
    for (const d of fp.drawings) {
        if (!d.layer || !SILK_LAYERS.has(d.layer)) continue;
        switch (d.type) {
            case "line":
                points.push(fpTransform(fp.at, d.start.x, d.start.y));
                points.push(fpTransform(fp.at, d.end.x, d.end.y));
                break;
            case "arc": {
                const pts = arcToPoints(d.start, d.mid, d.end, 16);
                for (const p of pts) points.push(fpTransform(fp.at, p.x, p.y));
                break;
            }
            case "circle": {
                const cx = d.center.x, cy = d.center.y;
                const rad = Math.sqrt((d.end.x - cx) ** 2 + (d.end.y - cy) ** 2);
                for (let i = 0; i < 16; i++) {
                    const angle = (i / 16) * 2 * Math.PI;
                    points.push(fpTransform(fp.at, cx + rad * Math.cos(angle), cy + rad * Math.sin(angle)));
                }
                break;
            }
            case "rect":
                points.push(fpTransform(fp.at, d.start.x, d.start.y));
                points.push(fpTransform(fp.at, d.end.x, d.start.y));
                points.push(fpTransform(fp.at, d.end.x, d.end.y));
                points.push(fpTransform(fp.at, d.start.x, d.end.y));
                break;
            case "polygon":
                for (const p of d.points) points.push(fpTransform(fp.at, p.x, p.y));
                break;
            case "curve":
                for (const p of d.points) points.push(fpTransform(fp.at, p.x, p.y));
                break;
        }
    }

    // Copper layer pad corners (include pad extent, not just center)
    for (const pad of fp.pads) {
        const hasCopperLayer = pad.layers.some(l =>
            l.startsWith("F.Cu") || l.startsWith("B.Cu") || l.includes(".Cu")
        );
        if (!hasCopperLayer) continue;

        const hw = (pad.size?.w ?? 0) / 2;
        const hh = (pad.size?.h ?? 0) / 2;
        const px = pad.at.x;
        const py = pad.at.y;

        // Add four corners of the pad bounding box
        points.push(fpTransform(fp.at, px - hw, py - hh));
        points.push(fpTransform(fp.at, px + hw, py - hh));
        points.push(fpTransform(fp.at, px + hw, py + hh));
        points.push(fpTransform(fp.at, px - hw, py + hh));
    }

    return points;
}

/**
 * Build dynamic hatch overlay layers for the selected elements.
 * Uses the dynamic context system so paintObjects creates per-PCB-layer
 * dynamic layers. These are then committed with hatch shaders so
 * render_hatch() can draw them with diagonal stripes.
 */
export function paintDiffSelection(
    renderer: Renderer,
    model: RenderModel,
    selectedUuids: Set<string>,
    hiddenLayers?: Set<string>,
    uuidStatusMap?: Map<string, DiffStatus>,
): void {
    renderer.dispose_dynamic_layers();
    if (selectedUuids.size === 0) return;

    const layerById = buildLayerMap(model);
    const hidden = hiddenLayers ?? new Set<string>();

    const fps = model.footprints.filter(fp => fp.uuid && selectedUuids.has(fp.uuid));
    const tracks = model.tracks.filter(t => t.uuid && selectedUuids.has(t.uuid));
    const vias = model.vias.filter(v => v.uuid && selectedUuids.has(v.uuid));
    const drawings = model.drawings.filter(d => d.uuid && selectedUuids.has(d.uuid));
    const texts = model.texts.filter(t => t.uuid && selectedUuids.has(t.uuid));
    const zones = model.zones.filter(z => z.uuid && selectedUuids.has(z.uuid));

    const footprintOwnerByRef = new Map<FootprintModel, string>();
    for (const fp of fps) {
        footprintOwnerByRef.set(fp, fp.uuid ?? "");
    }

    const tint: [number, number, number] = [1.0, 1.0, 1.0];

    // Pass 1: tracks + vias → path-line hatch with continuous chain animation
    renderer.isDynamicContext = true;
    paintTracksAsTree(renderer, tracks, hidden);
    // Vias still painted normally
    paintObjects(renderer, layerById, [], [], [], vias, [], hidden, footprintOwnerByRef, tint);
    renderer.isDynamicContext = false;
    renderer.commit_dynamic_hatch_layers(true);

    // Pass 2: footprints, drawings, texts, zones → diagonal hatch
    renderer.isDynamicContext = true;
    paintObjects(renderer, layerById, drawings, texts, [], [], fps, hidden, footprintOwnerByRef, tint);
    paintZones(renderer, model, hidden, layerById, zones);
    renderer.isDynamicContext = false;
    renderer.commit_dynamic_hatch_layers(false);

    // Pass 3: glow overlay for all selected items (colored by diff status)
    const statusMap = uuidStatusMap ?? new Map<string, DiffStatus>();
    renderer.isDynamicContext = true;
    paintGlowGeometry(renderer, fps, tracks, vias, drawings, zones, hidden, statusMap);
    renderer.isDynamicContext = false;
    renderer.commit_dynamic_glow_layers();
}


/** Look up the glow color for a uuid based on its diff status */
function glowColor(uuid: string | null, statusMap: Map<string, DiffStatus>): [number, number, number, number] {
    const status = uuid ? (statusMap.get(uuid) ?? "unchanged") : "unchanged";
    const [r, g, b] = STATUS_COLORS[status] ?? STATUS_COLORS.unchanged;
    return [r, g, b, 0.9];
}

/**
 * Paint glow geometry for all selected item types, colored by diff status.
 */
function paintGlowGeometry(
    renderer: Renderer,
    fps: FootprintModel[],
    tracks: TrackModel[],
    vias: ViaModel[],
    drawings: DrawingModel[],
    zones: ZoneModel[],
    hidden: Set<string>,
    statusMap: Map<string, DiffStatus>,
): void {
    // Footprints: convex hull of silkscreen + copper pad points
    for (const fp of fps) {
        const hullPoints = collectHullPoints(fp);
        if (hullPoints.length < 3) continue;

        const hull = convexHull(hullPoints);
        if (hull.length < 3) continue;

        const expandedHull = offsetHull(hull, GLOW_MARGIN);
        const [r, g, b, a] = glowColor(fp.uuid, statusMap);

        // Determine which layer to render on (use first silk layer of this footprint)
        let glowLayerName = "F.Silkscreen";
        for (const d of fp.drawings) {
            if (d.layer && SILK_LAYERS.has(d.layer) && !hidden.has(d.layer)) {
                glowLayerName = d.layer;
                break;
            }
        }
        if (hidden.has(glowLayerName)) continue;

        const layer = renderer.get_layer(glowLayerName);

        // Inner fill (solid glow core)
        layer.geometry.add_polygon(expandedHull, r, g, b, a * 0.3);

        // Outer stroke (soft glow edge) — close the loop
        const strokePts = [...expandedHull, expandedHull[0]!];
        layer.geometry.add_polyline(strokePts, GLOW_RADIUS * 2, r, g, b, a);
    }

    // Tracks: expanded width glow
    for (const t of tracks) {
        if (!t.layer || hidden.has(t.layer)) continue;
        const [r, g, b, a] = glowColor(t.uuid, statusMap);
        const layer = renderer.get_layer(t.layer);
        const pts = t.mid
            ? arcToPoints(t.start, t.mid, t.end)
            : [p2v(t.start), p2v(t.end)];
        layer.geometry.add_polyline(pts, t.width + GLOW_RADIUS * 2, r, g, b, a);
    }

    // Vias: expanded radius glow
    for (const v of vias) {
        const [r, g, b, a] = glowColor(v.uuid, statusMap);
        for (const l of v.copper_layers) {
            if (hidden.has(l)) continue;
            const layer = renderer.get_layer(l);
            const glowRadius = v.size / 2 + GLOW_RADIUS;
            const segments = 24;
            const pts: Vec2[] = [];
            for (let i = 0; i <= segments; i++) {
                const angle = (i / segments) * 2 * Math.PI;
                pts.push(new Vec2(v.at.x + glowRadius * Math.cos(angle), v.at.y + glowRadius * Math.sin(angle)));
            }
            layer.geometry.add_polyline(pts, GLOW_RADIUS * 2, r, g, b, a);
        }
    }

    // Drawings: expanded width glow
    for (const d of drawings) {
        if (!d.layer || hidden.has(d.layer)) continue;
        const [r, g, b, a] = glowColor(d.uuid, statusMap);
        const layer = renderer.get_layer(d.layer);
        const rawWidth = Number.isFinite(d.width) ? d.width : 0;
        const strokeWidth = rawWidth > 0 ? rawWidth : (d.filled ? 0 : 0.12);
        const glowWidth = strokeWidth + GLOW_RADIUS * 2;

        switch (d.type) {
            case "line":
                layer.geometry.add_polyline([p2v(d.start), p2v(d.end)], glowWidth, r, g, b, a);
                break;
            case "arc": {
                const pts = arcToPoints(d.start, d.mid, d.end);
                layer.geometry.add_polyline(pts, glowWidth, r, g, b, a);
                break;
            }
            case "circle": {
                const cx = d.center.x, cy = d.center.y;
                const rad = Math.sqrt((d.end.x - cx) ** 2 + (d.end.y - cy) ** 2);
                const pts: Vec2[] = [];
                for (let i = 0; i <= 48; i++) {
                    const angle = (i / 48) * 2 * Math.PI;
                    pts.push(new Vec2(cx + rad * Math.cos(angle), cy + rad * Math.sin(angle)));
                }
                layer.geometry.add_polyline(pts, glowWidth, r, g, b, a);
                break;
            }
            case "rect": {
                const s = d.start, e = d.end;
                const corners = [p2v(s), new Vec2(e.x, s.y), p2v(e), new Vec2(s.x, e.y), p2v(s)];
                layer.geometry.add_polyline(corners, glowWidth, r, g, b, a);
                break;
            }
            case "polygon": {
                if (d.points.length >= 2) {
                    const pts = [...d.points.map(p2v), p2v(d.points[0]!)];
                    layer.geometry.add_polyline(pts, glowWidth, r, g, b, a);
                }
                break;
            }
            case "curve": {
                if (d.points.length >= 2) {
                    layer.geometry.add_polyline(d.points.map(p2v), glowWidth, r, g, b, a);
                }
                break;
            }
        }
    }

    // Zones: outline glow on each zone layer
    for (const z of zones) {
        if (z.outline.length < 2) continue;
        const [r, g, b, a] = glowColor(z.uuid, statusMap);
        for (const layerName of z.layers) {
            const renderLayerName = `zone:${layerName}`;
            if (hidden.has(layerName)) continue;
            const layer = renderer.get_layer(renderLayerName);
            const pts = [...z.outline.map(p2v), p2v(z.outline[0]!)];
            layer.geometry.add_polyline(pts, GLOW_RADIUS * 2, r, g, b, a);
        }
    }
}
