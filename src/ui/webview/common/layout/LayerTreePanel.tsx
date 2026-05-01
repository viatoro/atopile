import { useState, useMemo, useCallback, useRef, useEffect, type ReactNode } from "react";
import type { LayerModel } from "./types";
import {
    groupLayers,
    colorToCSS,
    OBJECT_ROOT_FILTERS,
    TEXT_SHAPES_FILTERS,
    TEXT_SHAPES_FILTER_IDS,
    OBJECT_TYPE_IDS,
} from "./layer-panel-utils";
// CSS is loaded by the consumer (e.g. pcb-diff.css imports layer-panel.css)

const STORAGE_KEY = "layer-panel-expanded";

function loadExpanded(): Set<string> {
    try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (raw) return new Set(JSON.parse(raw));
    } catch { /* ignore */ }
    return new Set();
}

function saveExpanded(expanded: Set<string>) {
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify([...expanded]));
    } catch { /* ignore */ }
}

interface LayerTreePanelProps {
    layers: LayerModel[];
    hiddenLayers: Set<string>;
    onToggleLayer: (layerId: string) => void;
    onToggleLayers: (layerIds: string[], visible: boolean) => void;
    footer?: ReactNode;
    defaultCollapsed?: boolean;
}

export function LayerTreePanel({
    layers,
    hiddenLayers,
    onToggleLayer,
    onToggleLayers,
    footer,
    defaultCollapsed = true,
}: LayerTreePanelProps) {
    const [collapsed, setCollapsed] = useState(defaultCollapsed);
    const [expandedGroups, setExpandedGroups] = useState<Set<string>>(loadExpanded);

    const objectTypesExpanded = expandedGroups.has("__objects");
    const textShapesExpanded = expandedGroups.has("__textshapes");

    useEffect(() => { saveExpanded(expandedGroups); }, [expandedGroups]);

    const objChildRef = useRef<HTMLDivElement>(null);
    const tsChildRef = useRef<HTMLDivElement>(null);
    const groupChildRefs = useRef<Map<string, HTMLDivElement>>(new Map());

    const isVisible = useCallback((id: string) => !hiddenLayers.has(id), [hiddenLayers]);

    const layerById = useMemo(
        () => new Map(layers.map((l) => [l.id, l])),
        [layers],
    );

    const { groups, topLevel } = useMemo(
        () => groupLayers(layers),
        [layers],
    );

    const rowOpacity = (vis: boolean) => (vis ? 1 : 0.3);

    const groupOpacity = (childIds: string[]) => {
        const allVis = childIds.every(isVisible);
        const allHid = childIds.every((id) => !isVisible(id));
        return allVis ? 1 : allHid ? 0.3 : 0.6;
    };

    const animateToggle = (
        container: HTMLDivElement | null,
        expanding: boolean,
    ) => {
        if (!container) return;
        if (expanding) {
            container.style.maxHeight = container.scrollHeight + "px";
            const onEnd = () => {
                container.style.maxHeight = "";
                container.removeEventListener("transitionend", onEnd);
            };
            container.addEventListener("transitionend", onEnd);
        } else {
            container.style.maxHeight = container.scrollHeight + "px";
            requestAnimationFrame(() => {
                container.style.maxHeight = "0";
            });
        }
    };

    const toggleGroupExpanded = useCallback((key: string, container: HTMLDivElement | null) => {
        setExpandedGroups((prev) => {
            const next = new Set(prev);
            const expanding = !next.has(key);
            if (expanding) next.add(key); else next.delete(key);
            animateToggle(container, expanding);
            return next;
        });
    }, []);

    const handleToggleObjectTypes = () => {
        const allVis = OBJECT_TYPE_IDS.every(isVisible);
        onToggleLayers([...OBJECT_TYPE_IDS], !allVis);
    };

    const handleToggleTextShapes = () => {
        const allVis = TEXT_SHAPES_FILTER_IDS.every(isVisible);
        onToggleLayers([...TEXT_SHAPES_FILTER_IDS], !allVis);
    };

    const handleToggleGroup = (childNames: string[]) => {
        const allVis = childNames.every(isVisible);
        onToggleLayers(childNames, !allVis);
    };

    return (
            <div
                className={`layer-panel${collapsed ? " collapsed" : ""}`}
            >
                <div className="layer-panel-header">
                    <span>Layers</span>
                </div>
                <div className="layer-panel-content">
                    {/* Objects group */}
                    <div
                        className="layer-group-header"
                        style={{ opacity: groupOpacity(OBJECT_TYPE_IDS) }}
                        onClick={handleToggleObjectTypes}
                    >
                        <span
                            className="layer-chevron"
                            onClick={(e) => {
                                e.stopPropagation();
                                toggleGroupExpanded("__objects", objChildRef.current);
                            }}
                        >
                            {objectTypesExpanded ? "\u25BE" : "\u25B8"}
                        </span>
                        <span
                            className="layer-swatch"
                            style={{ background: "linear-gradient(135deg, #5a8a3a 50%, #c05030 50%)" }}
                        />
                        <span className="layer-group-name">Objects</span>
                    </div>
                    <div
                        ref={objChildRef}
                        className="layer-group-children"
                        style={objectTypesExpanded ? undefined : { maxHeight: 0 }}
                    >
                        {OBJECT_ROOT_FILTERS.map((f) => (
                            <div
                                key={f.id}
                                className="layer-row"
                                style={{ opacity: rowOpacity(isVisible(f.id)) }}
                                onClick={() => onToggleLayer(f.id)}
                            >
                                <span className="layer-swatch" style={{ background: f.color }} />
                                <span>{f.label}</span>
                            </div>
                        ))}

                        {/* Text & Shapes sub-group */}
                        <div
                            className="layer-group-header"
                            style={{ opacity: groupOpacity(TEXT_SHAPES_FILTER_IDS) }}
                            onClick={handleToggleTextShapes}
                        >
                            <span
                                className="layer-chevron"
                                onClick={(e) => {
                                    e.stopPropagation();
                                    toggleGroupExpanded("__textshapes", tsChildRef.current);
                                }}
                            >
                                {textShapesExpanded ? "\u25BE" : "\u25B8"}
                            </span>
                            <span
                                className="layer-swatch"
                                style={{ background: "linear-gradient(135deg, #4a8cad 50%, #356982 50%)" }}
                            />
                            <span className="layer-group-name">Text & Shapes</span>
                        </div>
                        <div
                            ref={tsChildRef}
                            className="layer-group-children"
                            style={textShapesExpanded ? undefined : { maxHeight: 0 }}
                        >
                            {TEXT_SHAPES_FILTERS.map((f) => (
                                <div
                                    key={f.id}
                                    className="layer-row"
                                    style={{ opacity: rowOpacity(isVisible(f.id)) }}
                                    onClick={() => onToggleLayer(f.id)}
                                >
                                    <span className="layer-swatch" style={{ background: f.color }} />
                                    <span>{f.label}</span>
                                </div>
                            ))}
                        </div>
                    </div>

                    {/* PCB layer groups */}
                    {groups.map((group) => {
                        const childNames = group.layers.map((l) => l.id);
                        const isExpanded = expandedGroups.has(group.group);
                        const primaryColor = colorToCSS(childNames[0]!, layerById);

                        return (
                            <div key={group.group}>
                                <div
                                    className="layer-group-header"
                                    style={{ opacity: groupOpacity(childNames) }}
                                    onClick={() => handleToggleGroup(childNames)}
                                >
                                    <span
                                        className="layer-chevron"
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            toggleGroupExpanded(
                                                group.group,
                                                groupChildRefs.current.get(group.group) ?? null,
                                            );
                                        }}
                                    >
                                        {isExpanded ? "\u25BE" : "\u25B8"}
                                    </span>
                                    <span className="layer-swatch" style={{ background: primaryColor }} />
                                    <span className="layer-group-name">{group.group}</span>
                                </div>
                                <div
                                    ref={(el) => {
                                        if (el) groupChildRefs.current.set(group.group, el);
                                    }}
                                    className="layer-group-children"
                                    style={isExpanded ? undefined : { maxHeight: 0 }}
                                >
                                    {group.layers.map((layer) => (
                                        <div
                                            key={layer.id}
                                            className="layer-row"
                                            style={{ opacity: rowOpacity(isVisible(layer.id)) }}
                                            onClick={() => onToggleLayer(layer.id)}
                                        >
                                            <span
                                                className="layer-swatch"
                                                style={{ background: colorToCSS(layer.id, layerById) }}
                                            />
                                            <span>{layer.label ?? layer.id}</span>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        );
                    })}

                    {/* Top-level layers (ungrouped) */}
                    {topLevel.map((layer) => (
                        <div
                            key={layer.id}
                            className="layer-row layer-top-level"
                            style={{ opacity: rowOpacity(isVisible(layer.id)) }}
                            onClick={() => onToggleLayer(layer.id)}
                        >
                            <span
                                className="layer-swatch"
                                style={{ background: colorToCSS(layer.id, layerById) }}
                            />
                            <span>{layer.label ?? layer.id}</span>
                        </div>
                    ))}
                </div>
                {footer}

                {/* Toggle tab — inside the panel so it moves with the transform */}
                <div
                    className="layer-expand-tab visible"
                    onClick={() => setCollapsed((v) => !v)}
                >
                    Layers
                </div>
            </div>
    );
}
