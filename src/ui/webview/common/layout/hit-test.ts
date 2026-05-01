import { Vec2, BBox } from "./math";
import type { FootprintModel } from "./types";
import { fpTransform, padTransform } from "./geometry";

type FootprintBBoxCacheEntry = {
    x: number;
    y: number;
    r: number;
    bbox: BBox;
};

const bboxCache = new WeakMap<FootprintModel, FootprintBBoxCacheEntry>();

/** Compute bounding box for a footprint in world coords */
export function footprintBBox(fp: FootprintModel): BBox {
    const cached = bboxCache.get(fp);
    if (cached && cached.x === fp.at.x && cached.y === fp.at.y && cached.r === fp.at.r) {
        return cached.bbox;
    }

    const points: Vec2[] = [];
    for (const pad of fp.pads) {
        const hw = pad.size.w / 2;
        const hh = pad.size.h / 2;
        // Transform all four corners through pad + footprint rotation
        points.push(padTransform(fp.at, pad.at, -hw, -hh));
        points.push(padTransform(fp.at, pad.at, hw, -hh));
        points.push(padTransform(fp.at, pad.at, hw, hh));
        points.push(padTransform(fp.at, pad.at, -hw, hh));
    }
    for (const drawing of fp.drawings) {
        switch (drawing.type) {
            case "line":
                points.push(fpTransform(fp.at, drawing.start.x, drawing.start.y));
                points.push(fpTransform(fp.at, drawing.end.x, drawing.end.y));
                break;
            case "arc":
                points.push(fpTransform(fp.at, drawing.start.x, drawing.start.y));
                points.push(fpTransform(fp.at, drawing.mid.x, drawing.mid.y));
                points.push(fpTransform(fp.at, drawing.end.x, drawing.end.y));
                break;
            case "circle":
                points.push(fpTransform(fp.at, drawing.center.x, drawing.center.y));
                points.push(fpTransform(fp.at, drawing.end.x, drawing.end.y));
                break;
            case "rect":
                points.push(fpTransform(fp.at, drawing.start.x, drawing.start.y));
                points.push(fpTransform(fp.at, drawing.end.x, drawing.end.y));
                break;
            case "polygon":
            case "curve":
                for (const p of drawing.points) {
                    points.push(fpTransform(fp.at, p.x, p.y));
                }
                break;
        }
    }
    const bbox = points.length === 0
        ? new BBox(fp.at.x - 1, fp.at.y - 1, 2, 2)
        : BBox.from_points(points).grow(0.2);
    bboxCache.set(fp, { x: fp.at.x, y: fp.at.y, r: fp.at.r, bbox });
    return bbox;
}

/** Find the footprint under a world-space point, returns index or -1 */
export function hitTestFootprints(worldPos: Vec2, footprints: FootprintModel[]): number {
    for (let i = footprints.length - 1; i >= 0; i--) {
        const bbox = footprintBBox(footprints[i]!);
        if (bbox.contains_point(worldPos)) {
            return i;
        }
    }
    return -1;
}

/** Find which pad within a footprint is under a world-space point. */
export function hitTestPads(worldPos: Vec2, footprint: FootprintModel): number {
    const fpAt = footprint.at;
    const rad = -(fpAt.r || 0) * Math.PI / 180;
    const cos = Math.cos(rad);
    const sin = Math.sin(rad);
    const dx = worldPos.x - fpAt.x;
    const dy = worldPos.y - fpAt.y;
    const localX = dx * cos + dy * sin;
    const localY = -dx * sin + dy * cos;

    for (let i = footprint.pads.length - 1; i >= 0; i--) {
        const pad = footprint.pads[i]!;
        const padRad = -(pad.at.r || 0) * Math.PI / 180;
        const pc = Math.cos(padRad);
        const ps = Math.sin(padRad);
        const pdx = localX - pad.at.x;
        const pdy = localY - pad.at.y;
        const plx = pdx * pc + pdy * ps;
        const ply = -pdx * ps + pdy * pc;
        const hw = pad.size.w / 2;
        const hh = pad.size.h / 2;

        if (pad.shape === "circle") {
            if (plx * plx + ply * ply <= hw * hw) {
                return i;
            }
            continue;
        }

        if (plx >= -hw && plx <= hw && ply >= -hh && ply <= hh) {
            return i;
        }
    }

    return -1;
}

function bboxIntersects(a: BBox, b: BBox): boolean {
    return !(a.x2 < b.x || b.x2 < a.x || a.y2 < b.y || b.y2 < a.y);
}

/** Find all footprints intersecting a world-space selection box. */
export function hitTestFootprintsInBox(selectionBox: BBox, footprints: FootprintModel[]): number[] {
    const hits: number[] = [];
    for (let i = 0; i < footprints.length; i++) {
        const bbox = footprintBBox(footprints[i]!);
        if (bboxIntersects(selectionBox, bbox)) {
            hits.push(i);
        }
    }
    return hits;
}
