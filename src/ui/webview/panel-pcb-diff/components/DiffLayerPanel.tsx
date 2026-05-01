import type { RenderModel } from "../../common/layout/types";
import type { LayerDimMode, DiffViewMode } from "../main";
import { Button } from "../../common/components/Button";
import { PanelTabs } from "../../common/components/PanelTabs";
import { LayerTreePanel } from "../../common/layout/LayerTreePanel";

export type DiffLayout = "horizontal" | "vertical";

interface DiffLayerPanelProps {
    model: RenderModel;
    hiddenLayers: Set<string>;
    onToggleLayer: (layer: string) => void;
    onToggleLayers: (layers: string[], visible: boolean) => void;
    layerDimMode: LayerDimMode;
    onLayerDimModeChange: (mode: LayerDimMode) => void;
    dimOpacity: number;
    onDimOpacityChange: (v: number) => void;
    onCenterView: () => void;
    followSelected: boolean;
    onToggleFollowSelected: () => void;
    layout: DiffLayout;
    onLayoutChange: (layout: DiffLayout) => void;
    viewMode: DiffViewMode;
    onViewModeChange: (mode: DiffViewMode) => void;
}


export function DiffLayerPanel({
    model,
    hiddenLayers,
    onToggleLayer,
    onToggleLayers,
    layerDimMode,
    onLayerDimModeChange,
    dimOpacity,
    onDimOpacityChange,
    onCenterView,
    followSelected,
    onToggleFollowSelected,
    layout,
    onLayoutChange,
    viewMode,
    onViewModeChange,
}: DiffLayerPanelProps) {
    const settings = (
        <div className="layer-panel-settings">
            <div className="layer-settings-label">View mode</div>
            <PanelTabs
                tabs={[
                    { key: "side-by-side", label: "Split" },
                    { key: "overlay-swap", label: "Swipe" },
                    { key: "overlay-alpha", label: "Blend" },
                ]}
                activeTab={viewMode}
                onTabChange={(key) => onViewModeChange(key as DiffViewMode)}
            />
            {viewMode === "side-by-side" && (
                <>
                    <div className="layer-settings-label">Layout</div>
                    <PanelTabs
                        tabs={[
                            { key: "horizontal", label: "H" },
                            { key: "vertical", label: "V" },
                        ]}
                        activeTab={layout}
                        onTabChange={(key) => onLayoutChange(key as DiffLayout)}
                    />
                </>
            )}
            <div className="layer-settings-label">Inactive layers</div>
            <PanelTabs
                tabs={[
                    { key: "normal", label: "Normal" },
                    { key: "dim", label: "Dim" },
                    { key: "hide", label: "Hide" },
                ]}
                activeTab={layerDimMode}
                onTabChange={(key) => onLayerDimModeChange(key as LayerDimMode)}
            />
            <div className={`layer-opacity-row${layerDimMode !== "dim" ? " disabled" : ""}`}>
                <span>Opacity</span>
                <input
                    type="range"
                    min={0}
                    max={100}
                    step={1}
                    value={Math.round(dimOpacity * 100)}
                    disabled={layerDimMode !== "dim"}
                    onChange={(e) => onDimOpacityChange(Number(e.target.value) / 100)}
                />
                <span>{Math.round(dimOpacity * 100)}%</span>
            </div>
            <div className="layer-view-buttons">
                <Button variant="outline" size="sm" onClick={onCenterView}>Center View</Button>
                <Button
                    variant={followSelected ? "default" : "outline"}
                    size="sm"
                    onClick={onToggleFollowSelected}
                >Follow Selected</Button>
            </div>
        </div>
    );

    return (
        <LayerTreePanel
            layers={model.layers}
            hiddenLayers={hiddenLayers}
            onToggleLayer={onToggleLayer}
            onToggleLayers={onToggleLayers}
            footer={settings}
        />
    );
}
