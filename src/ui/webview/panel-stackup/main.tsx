import { useCallback, useEffect, useMemo, useRef } from "react";
import { Layers } from "lucide-react";
import { NoDataMessage } from "../common/components";
import { render } from "../common/render";
import { WebviewRpcClient, rpcClient } from "../common/webviewRpcClient";
import type { UiStackupLayer } from "../../protocol/generated-types";
import "./StackupPanel.css";

/** Map a layer type string to a CSS color for the cross-section bar. */
function layerColor(layerType: string | null): string {
  if (!layerType) return "rgba(128,128,128,0.35)";
  const t = layerType.toLowerCase();
  if (t === "copper") return "rgba(232, 196, 140, 0.85)";
  if (t === "substrate" || t === "core") return "rgba(140, 190, 130, 0.75)";
  if (t === "prepreg") return "rgba(160, 200, 140, 0.65)";
  if (t.includes("solder") || t.includes("mask")) return "rgba(100, 180, 180, 0.7)";
  if (t.includes("silk")) return "rgba(180, 170, 210, 0.7)";
  if (t.includes("paste")) return "rgba(160, 160, 160, 0.5)";
  return "rgba(128, 128, 128, 0.35)";
}

/** CSS class for the type badge in the table. */
function typeBadgeClass(layerType: string | null): string {
  if (!layerType) return "type-badge type-default";
  const t = layerType.toLowerCase();
  if (t === "copper") return "type-badge type-copper";
  if (t === "substrate" || t === "core" || t === "prepreg") return "type-badge type-core";
  if (t.includes("solder") || t.includes("mask")) return "type-badge type-mask";
  if (t.includes("silk")) return "type-badge type-silk";
  return "type-badge type-default";
}

function formatThickness(mm: number | null): string {
  if (mm == null) return "-";
  if (mm >= 0.1) return `${mm.toFixed(3)} mm`;
  return `${(mm * 1000).toFixed(1)} \u00B5m`;
}

function formatFloat(value: number | null | undefined, decimals: number): string {
  if (value == null) return "-";
  return value.toFixed(decimals);
}

function StackupPanel() {
  const projectState = WebviewRpcClient.useSubscribe("projectState");
  const stackupData = WebviewRpcClient.useSubscribe("stackupData");
  const currentBuilds = WebviewRpcClient.useSubscribe("currentBuilds");
  const selectedBuildInProgress = WebviewRpcClient.useSubscribe("selectedBuildInProgress");
  const bodyRef = useRef<HTMLDivElement>(null);
  const hoveredRef = useRef<number | null>(null);

  const setHighlight = useCallback((index: number | null) => {
    if (hoveredRef.current === index) return;
    const container = bodyRef.current;
    if (!container) return;
    if (hoveredRef.current != null) {
      for (const el of container.querySelectorAll(`[data-layer="${hoveredRef.current}"]`)) {
        el.classList.remove("highlighted");
      }
    }
    hoveredRef.current = index;
    if (index != null) {
      for (const el of container.querySelectorAll(`[data-layer="${index}"]`)) {
        el.classList.add("highlighted");
      }
    }
  }, []);

  const refreshStackup = useCallback(() => {
    if (!projectState.selectedProjectRoot) return;
    rpcClient?.sendAction("getStackup", {
      projectRoot: projectState.selectedProjectRoot,
      target: projectState.selectedTarget,
    });
  }, [projectState.selectedProjectRoot, projectState.selectedTarget]);

  // Refresh on mount and project/target change
  useEffect(() => {
    refreshStackup();
  }, [refreshStackup]);

  // Refresh when builds complete
  useEffect(() => {
    if (
      projectState.selectedProjectRoot &&
      currentBuilds.every((build) => build.status !== "building" && build.status !== "queued")
    ) {
      refreshStackup();
    }
  }, [currentBuilds, projectState.selectedProjectRoot, refreshStackup]);

  const layers = stackupData.layers;
  const hasData = layers.length > 0;

  // Calculate proportional heights for cross-section
  const layerHeights = useMemo(() => {
    if (!hasData) return [];
    const totalThickness = layers.reduce(
      (sum, l) => sum + (l.thicknessMm ?? 0),
      0,
    );
    if (totalThickness <= 0) {
      return layers.map(() => 20); // equal height fallback
    }
    const TARGET_TOTAL_PX = 200;
    const MIN_PX = 4;
    return layers.map((l) => {
      const fraction = (l.thicknessMm ?? 0) / totalThickness;
      return Math.max(Math.round(fraction * TARGET_TOTAL_PX), MIN_PX);
    });
  }, [layers, hasData]);

  return (
    <NoDataMessage
      icon={<Layers size={24} />}
      noun="stackup"
      hasSelection={Boolean(projectState.selectedProjectRoot)}
      isLoading={stackupData.loading}
      buildInProgress={selectedBuildInProgress}
      error={stackupData.error}
      hasData={hasData}
    >
    <div className="stackup-panel">
      {/* Summary header */}
      <div className="stackup-summary">
        {stackupData.stackupName && (
          <div className="stackup-summary-item">
            <span className="stackup-summary-label">Stackup:</span>
            <span className="stackup-summary-value">{stackupData.stackupName}</span>
          </div>
        )}
        <div className="stackup-summary-item">
          <span className="stackup-summary-label">Layers:</span>
          <span className="stackup-summary-value">{stackupData.layerCount} Cu</span>
        </div>
        {stackupData.totalThicknessMm != null && (
          <div className="stackup-summary-item">
            <span className="stackup-summary-label">Thickness:</span>
            <span className="stackup-summary-value">
              {stackupData.totalThicknessMm.toFixed(2)} mm
            </span>
          </div>
        )}
        {stackupData.manufacturer && (
          <div className="stackup-summary-item">
            <span className="stackup-summary-label">Manufacturer:</span>
            <span className="stackup-summary-value">
              {stackupData.manufacturer.name}
              {stackupData.manufacturer.country && ` (${stackupData.manufacturer.country})`}
            </span>
          </div>
        )}
      </div>

      <div className="stackup-body" ref={bodyRef}>
        {/* Cross-section visualization */}
        <div className="stackup-cross-section">
          {layers.map((layer, i) => (
            <div
              key={i}
              data-layer={i}
              className="stackup-layer-bar"
              style={{
                height: layerHeights[i],
                background: layerColor(layer.layerType),
              }}
              onMouseEnter={() => setHighlight(i)}
              onMouseLeave={() => setHighlight(null)}
            >
              {(layerHeights[i] ?? 0) >= 14 && (
                <span className="stackup-layer-bar-label">{layer.layerType || "-"}</span>
              )}
            </div>
          ))}
        </div>

        {/* Layer detail table */}
        <div className="stackup-table-wrapper">
          <table className="stackup-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Type</th>
                <th>Material</th>
                <th>Thickness</th>
                <th>&epsilon;r</th>
                <th>tan &delta;</th>
              </tr>
            </thead>
            <tbody>
              {layers.map((layer, i) => (
                <tr
                  key={i}
                  data-layer={i}
                  onMouseEnter={() => setHighlight(i)}
                  onMouseLeave={() => setHighlight(null)}
                >
                  <td>{layer.index + 1}</td>
                  <td>
                    <span className={typeBadgeClass(layer.layerType)}>
                      {layer.layerType || "-"}
                    </span>
                  </td>
                  <td>{layer.material || "-"}</td>
                  <td>{formatThickness(layer.thicknessMm)}</td>
                  <td>{formatFloat(layer.relativePermittivity, 2)}</td>
                  <td>{formatFloat(layer.lossTangent, 4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
    </NoDataMessage>
  );
}

function App() {
  return <StackupPanel />;
}

render(App);
