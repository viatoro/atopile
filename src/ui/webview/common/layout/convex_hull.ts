import { Vec2 } from "./math";

/**
 * Compute the convex hull of a set of 2D points using Andrew's monotone chain algorithm.
 * Returns points in counter-clockwise order.
 */
export function convexHull(points: Vec2[]): Vec2[] {
    if (points.length < 3) return [...points];

    const sorted = [...points].sort((a, b) => a.x !== b.x ? a.x - b.x : a.y - b.y);

    const cross = (o: Vec2, a: Vec2, b: Vec2) =>
        (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);

    // Lower hull
    const lower: Vec2[] = [];
    for (const p of sorted) {
        while (lower.length >= 2 && cross(lower[lower.length - 2]!, lower[lower.length - 1]!, p) <= 0) {
            lower.pop();
        }
        lower.push(p);
    }

    // Upper hull
    const upper: Vec2[] = [];
    for (let i = sorted.length - 1; i >= 0; i--) {
        const p = sorted[i]!;
        while (upper.length >= 2 && cross(upper[upper.length - 2]!, upper[upper.length - 1]!, p) <= 0) {
            upper.pop();
        }
        upper.push(p);
    }

    // Remove last point of each half because it's repeated
    lower.pop();
    upper.pop();

    return lower.concat(upper);
}

/**
 * Expand a convex hull outward by a margin.
 * Moves each edge outward along its normal, then re-intersects adjacent edges.
 */
export function offsetHull(hull: Vec2[], margin: number): Vec2[] {
    const n = hull.length;
    if (n < 3) return hull;

    // Compute outward normals for each edge
    const normals: Vec2[] = [];
    for (let i = 0; i < n; i++) {
        const a = hull[i]!;
        const b = hull[(i + 1) % n]!;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const len = Math.sqrt(dx * dx + dy * dy);
        if (len < 1e-10) {
            normals.push(new Vec2(0, 0));
        } else {
            // CCW hull: outward normal is (-dy, dx) / len
            normals.push(new Vec2(-dy / len, dx / len));
        }
    }

    // Offset each edge and intersect consecutive offset edges
    const result: Vec2[] = [];
    for (let i = 0; i < n; i++) {
        const j = (i + 1) % n;
        // Edge i: hull[i] -> hull[(i+1)%n], offset by normals[i] * margin
        // Edge j: hull[j] -> hull[(j+1)%n], offset by normals[j] * margin
        const ni = normals[i]!;
        const nj = normals[j]!;

        const a1 = hull[i]!.add(ni.multiply(margin));
        const b1 = hull[(i + 1) % n]!.add(ni.multiply(margin));
        const a2 = hull[j]!.add(nj.multiply(margin));
        const b2 = hull[(j + 1) % n]!.add(nj.multiply(margin));

        // Intersect the two offset lines
        const pt = lineIntersect(a1, b1, a2, b2);
        result.push(pt ?? b1);
    }

    return result;
}

function lineIntersect(a1: Vec2, b1: Vec2, a2: Vec2, b2: Vec2): Vec2 | null {
    const d1x = b1.x - a1.x, d1y = b1.y - a1.y;
    const d2x = b2.x - a2.x, d2y = b2.y - a2.y;
    const denom = d1x * d2y - d1y * d2x;
    if (Math.abs(denom) < 1e-10) return null;
    const t = ((a2.x - a1.x) * d2y - (a2.y - a1.y) * d2x) / denom;
    return new Vec2(a1.x + t * d1x, a1.y + t * d1y);
}
