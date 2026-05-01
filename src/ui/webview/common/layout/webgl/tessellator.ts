import earcut from "earcut";
import { Vec2 } from "../math";

const VERTS_PER_QUAD = 6;

/** Convert quad corners to 2 triangles (6 vertices) */
function quad_to_triangles(a: Vec2, b: Vec2, c: Vec2, d: Vec2): number[] {
    return [a.x, a.y, c.x, c.y, b.x, b.y, b.x, b.y, c.x, c.y, d.x, d.y];
}

function fill_color(dest: Float32Array, r: number, g: number, b: number, a: number, offset: number, count: number) {
    for (let i = 0; i < count; i++) {
        dest[offset + i * 4] = r;
        dest[offset + i * 4 + 1] = g;
        dest[offset + i * 4 + 2] = b;
        dest[offset + i * 4 + 3] = a;
    }
}

export interface TessPolylineResult {
    positions: Float32Array;
    caps: Float32Array;
    colors: Float32Array;
    pathDists: Float32Array;
    vertexCount: number;
    totalPathDist: number;
}

/** Tessellate a polyline (array of points + width) into quads with round caps */
export function tessellate_polyline(
    points: Vec2[],
    width: number,
    r: number, g: number, b: number, a: number,
    pathDistOffset: number = 0,
): TessPolylineResult {
    const segCount = points.length - 1;
    const maxVerts = segCount * VERTS_PER_QUAD;
    const positions = new Float32Array(maxVerts * 2);
    const caps = new Float32Array(maxVerts);
    const colors = new Float32Array(maxVerts * 4);
    const pathDists = new Float32Array(maxVerts);
    let vi = 0;
    let cumulativeDist = pathDistOffset;

    for (let i = 1; i < points.length; i++) {
        const p1 = points[i - 1]!;
        const p2 = points[i]!;
        const line = p2.sub(p1);
        const len = line.magnitude;
        if (len === 0) continue;

        const norm = line.normal.normalize();
        const n = norm.multiply(width / 2);
        const n2 = n.normal;

        const qa = p1.add(n).add(n2);
        const qb = p1.sub(n).add(n2);
        const qc = p2.add(n).sub(n2);
        const qd = p2.sub(n).sub(n2);

        const cap_region = width / (len + width);

        positions.set(quad_to_triangles(qa, qb, qc, qd), vi * 2);
        for (let j = 0; j < VERTS_PER_QUAD; j++) caps[vi + j] = cap_region;
        fill_color(colors, r, g, b, a, vi * 4, VERTS_PER_QUAD);

        // Path distance: vertices a,b,b (indices 0,2,3) get startDist,
        // vertices c,c,d (indices 1,4,5) get endDist
        const startDist = cumulativeDist;
        const endDist = cumulativeDist + len + width;
        pathDists[vi + 0] = startDist; // a
        pathDists[vi + 1] = endDist;   // c
        pathDists[vi + 2] = startDist; // b
        pathDists[vi + 3] = startDist; // b
        pathDists[vi + 4] = endDist;   // c
        pathDists[vi + 5] = endDist;   // d

        cumulativeDist = endDist;
        vi += VERTS_PER_QUAD;
    }

    return {
        positions: positions.subarray(0, vi * 2),
        caps: caps.subarray(0, vi),
        colors: colors.subarray(0, vi * 4),
        pathDists: pathDists.subarray(0, vi),
        vertexCount: vi,
        totalPathDist: cumulativeDist,
    };
}

export interface TessCircleResult {
    positions: Float32Array;
    caps: Float32Array;
    colors: Float32Array;
    pathDists: Float32Array;
    vertexCount: number;
}

/** Tessellate a filled circle into a quad (rendered as SDF in the polyline shader) */
export function tessellate_circle(
    cx: number, cy: number, radius: number,
    r: number, g: number, b: number, a: number,
): TessCircleResult {
    const positions = new Float32Array(VERTS_PER_QUAD * 2);
    const caps = new Float32Array(VERTS_PER_QUAD);
    const colors = new Float32Array(VERTS_PER_QUAD * 4);

    const n = new Vec2(radius, 0);
    const n2 = n.normal;
    const c = new Vec2(cx, cy);

    const qa = c.add(n).add(n2);
    const qb = c.sub(n).add(n2);
    const qc = c.add(n).sub(n2);
    const qd = c.sub(n).sub(n2);

    positions.set(quad_to_triangles(qa, qb, qc, qd), 0);
    for (let i = 0; i < VERTS_PER_QUAD; i++) caps[i] = 1.0;
    fill_color(colors, r, g, b, a, 0, VERTS_PER_QUAD);
    const pathDists = new Float32Array(VERTS_PER_QUAD); // all zeros for circles

    return { positions, caps, colors, pathDists, vertexCount: VERTS_PER_QUAD };
}

export interface TessPolygonResult {
    positions: Float32Array;
    colors: Float32Array;
    vertexCount: number;
}

/** Triangulate polygon loops using earcut. First loop is outer, remaining loops are holes. */
export function triangulate_polygon(
    loops: Vec2[][],
    r: number, g: number, b: number, a: number,
): TessPolygonResult {
    const flat: number[] = [];
    const holeIndices: number[] = [];
    let pointOffset = 0;

    for (let loopIndex = 0; loopIndex < loops.length; loopIndex++) {
        const loop = loops[loopIndex]!;
        if (loop.length < 3) {
            continue;
        }
        if (pointOffset > 0) {
            holeIndices.push(pointOffset);
        }
        for (const point of loop) {
            flat.push(point.x, point.y);
        }
        pointOffset += loop.length;
    }

    const indices = earcut(flat, holeIndices);
    const positions = new Float32Array(indices.length * 2);
    for (let i = 0; i < indices.length; i++) {
        positions[i * 2] = flat[indices[i]! * 2]!;
        positions[i * 2 + 1] = flat[indices[i]! * 2 + 1]!;
    }

    const vertexCount = indices.length;
    const colors = new Float32Array(vertexCount * 4);
    fill_color(colors, r, g, b, a, 0, vertexCount);

    return { positions, colors, vertexCount };
}
