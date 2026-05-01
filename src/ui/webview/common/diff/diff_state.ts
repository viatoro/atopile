import type { DiffElementStatus, DiffFilterMode, DiffStatus } from "./types";

/** Stable identifier for a diff element, used for selection and navigation. */
export function elementId(el: DiffElementStatus): string {
    return el.uuid_a || el.uuid_b || `${el.element_type}:${el.reference ?? ""}`;
}

export function groupElementsByStatus(
    elements: DiffElementStatus[],
): Record<DiffStatus, DiffElementStatus[]> {
    const groups: Record<DiffStatus, DiffElementStatus[]> = {
        unchanged: [],
        added: [],
        deleted: [],
        moved: [],
        modified: [],
    };
    for (const el of elements) {
        groups[el.status].push(el);
    }
    return groups;
}

const FILTER_ELEMENT_TYPES: Record<DiffFilterMode, Set<string>> = {
    components: new Set(["footprint"]),
    traces: new Set(["track", "via"]),
    silkscreen: new Set(["text", "drawing"]),
    outline: new Set(["zone"]),
};

export function filterElementsByMode(
    elements: DiffElementStatus[],
    mode: DiffFilterMode,
): DiffElementStatus[] {
    const allowed = FILTER_ELEMENT_TYPES[mode];
    return elements.filter((el) => allowed.has(el.element_type));
}

export function buildUuidStatusMap(
    elements: DiffElementStatus[],
): { mapA: Map<string, DiffStatus>; mapB: Map<string, DiffStatus> } {
    const mapA = new Map<string, DiffStatus>();
    const mapB = new Map<string, DiffStatus>();
    for (const el of elements) {
        if (el.uuid_a) mapA.set(el.uuid_a, el.status);
        if (el.uuid_b) mapB.set(el.uuid_b, el.status);
    }
    return { mapA, mapB };
}
