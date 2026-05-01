import type { LayerModel } from "./types";
import { getLayerColor } from "./colors";

export interface LayerGroup {
    group: string;
    layers: LayerModel[];
}

export const OBJECT_ROOT_FILTERS = [
    { id: "__type:zones", label: "Zones", color: "#5a8a3a" },
    { id: "__type:tracks", label: "Tracks & Vias", color: "#c05030" },
    { id: "__type:pads", label: "Pads", color: "#a07020" },
] as const;

export const TEXT_SHAPES_FILTERS = [
    { id: "__type:text", label: "Text", color: "#4a8cad" },
    { id: "__type:shapes", label: "Shapes", color: "#356982" },
] as const;

export const TEXT_SHAPES_FILTER_IDS = TEXT_SHAPES_FILTERS.map((t) => t.id);

export const OBJECT_TYPE_IDS = [
    ...OBJECT_ROOT_FILTERS.map((t) => t.id),
    ...TEXT_SHAPES_FILTER_IDS,
];

export function groupLayers(layers: LayerModel[]): { groups: LayerGroup[]; topLevel: LayerModel[] } {
    const groupMap = new Map<string, LayerModel[]>();
    const topLevel: LayerModel[] = [];

    for (const layer of layers) {
        const group = layer.group?.trim() ?? "";
        if (!group) {
            topLevel.push(layer);
            continue;
        }
        if (!groupMap.has(group)) groupMap.set(group, []);
        groupMap.get(group)!.push(layer);
    }

    const groups = [...groupMap.entries()]
        .map(([group, groupedLayers]) => ({ group, layers: groupedLayers }))
        .sort((a, b) => {
            const aOrder = a.layers[0]?.panel_order ?? Number.MAX_SAFE_INTEGER;
            const bOrder = b.layers[0]?.panel_order ?? Number.MAX_SAFE_INTEGER;
            if (aOrder !== bOrder) return aOrder - bOrder;
            return a.group.localeCompare(b.group);
        });

    return { groups, topLevel };
}

export function colorToCSS(layerName: string, layerById: Map<string, LayerModel>): string {
    const [r, g, b] = getLayerColor(layerName, layerById);
    return `rgb(${Math.round(r * 255)},${Math.round(g * 255)},${Math.round(b * 255)})`;
}
