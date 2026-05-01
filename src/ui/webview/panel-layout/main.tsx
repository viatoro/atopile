import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Grid3X3 } from "lucide-react";
import { createPortal } from "react-dom";
import { render } from "../common/render";
import { NoDataMessage } from "../common/components";
import { WebviewRpcClient, rpcClient } from "../common/webviewRpcClient";
import { createWebviewLogger } from "../common/logger";
import {
  ensureLayoutViewerShell,
  mountLayoutViewer,
  setOverlayState,
  RpcLayoutClient,
  type LayoutViewerHandle,
  type LayoutViewerShellElements,
} from "../common/layout";
import { LayerTreePanel } from "../common/layout/LayerTreePanel";
import type { LayerModel } from "../common/layout/types";
import "../common/layout/layout-shell.css";

const logger = createWebviewLogger("PanelLayout");

function App() {
  const projectState = WebviewRpcClient.useSubscribe("projectState");
  const layoutData = WebviewRpcClient.useSubscribe("layoutData");
  const selectedBuildInProgress = WebviewRpcClient.useSubscribe("selectedBuildInProgress");
  const hostRef = useRef<HTMLDivElement | null>(null);
  const panelClient = useMemo(
    () => (rpcClient ? new RpcLayoutClient(rpcClient, logger) : null),
    [],
  );
  const viewerRef = useRef<LayoutViewerHandle | null>(null);
  const [shell, setShell] = useState<LayoutViewerShellElements | null>(null);
  const [layers, setLayers] = useState<LayerModel[]>([]);
  const [hiddenLayers, setHiddenLayers] = useState<Set<string>>(new Set());
  const [mountError, setMountError] = useState<string | null>(null);

  function resetViewerShell(): LayoutViewerShellElements | null {
    viewerRef.current?.dispose();
    viewerRef.current = null;
    setLayers([]);
    setHiddenLayers(new Set());
    if (!hostRef.current) {
      return null;
    }
    const nextShell = ensureLayoutViewerShell(hostRef.current);
    setShell(nextShell);
    return nextShell;
  }

  function syncLayerState() {
    const editor = viewerRef.current?.editor;
    if (!editor) return;
    setLayers(editor.getLayerModels());
    setHiddenLayers(editor.getHiddenLayers());
  }

  const handleToggleLayer = useCallback((layerId: string) => {
    const editor = viewerRef.current?.editor;
    if (!editor) return;
    editor.setLayerVisible(layerId, !editor.isLayerVisible(layerId));
    setHiddenLayers(editor.getHiddenLayers());
  }, []);

  const handleToggleLayers = useCallback((layerIds: string[], visible: boolean) => {
    const editor = viewerRef.current?.editor;
    if (!editor) return;
    editor.setLayersVisible(layerIds, visible);
    setHiddenLayers(editor.getHiddenLayers());
  }, []);
  useEffect(() => {
    if (!hostRef.current) {
      return;
    }

    setShell(ensureLayoutViewerShell(hostRef.current));

    return () => {
      hostRef.current?.replaceChildren();
      setShell(null);
    };
  }, []);

  const hasSelection = Boolean(projectState.selectedProjectRoot && projectState.selectedTarget);
  const hasLayout = Boolean(layoutData.path);

  useEffect(() => {
    if (!shell) {
      return;
    }

    setMountError(null);

    if (!hasSelection || layoutData.error || !hasLayout) {
      if (viewerRef.current) {
        resetViewerShell();
      }
      // Hide the shell overlay — React NoDataMessage handles messaging
      setOverlayState(shell, "", "", { showSpinner: false });
      return;
    }

    if (!viewerRef.current) {
      if (!panelClient) {
        logger.error("Panel layout RPC client is unavailable.");
        setMountError("RPC client is unavailable.");
        return;
      }

      try {
        logger.info(
          `Mounting layout viewer over RPC pcbPath=${layoutData.path}`,
        );
        viewerRef.current = mountLayoutViewer({
          canvas: shell.canvas,
          client: panelClient,
          initialLoadingEl: shell.initialLoadingEl,
          layerPanelEl: null,
          statusEl: shell.statusEl,
          coordsEl: shell.coordsEl,
          busyEl: shell.busyEl,
          fpsEl: shell.fpsEl,
          helpEl: shell.helpEl,
          logger,
        });

        viewerRef.current.editor.setOnLayersChanged(syncLayerState);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        const detail = error instanceof Error && error.stack
          ? error.stack
          : message;
        logger.error(`Failed to mount layout viewer: ${detail}`);
        setMountError(message || "Failed to initialize layout viewer.");
        return;
      }
    }
  }, [
    panelClient,
    hasSelection,
    selectedBuildInProgress,
    layoutData.error,
    hasLayout,
    shell,
  ]);

  // Sync read-only state (e.g. when previewing autolayout candidates)
  useEffect(() => {
    viewerRef.current?.editor.setReadOnly(layoutData.readOnly);
  }, [layoutData.readOnly]);

  useEffect(() => {
    return () => {
      viewerRef.current?.dispose();
      viewerRef.current = null;
    };
  }, []);

  const layoutError = layoutData.error ? String(layoutData.error) : mountError;
  const viewerMounted = Boolean(viewerRef.current);
  const showNoData = !hasSelection || Boolean(layoutError) || (!hasLayout && !viewerMounted);

  return (
    <>
      {showNoData && (
        <NoDataMessage
          icon={<Grid3X3 size={24} />}
          noun="layout"
          hasSelection={hasSelection}
          buildInProgress={selectedBuildInProgress}
          error={layoutError}
          hasData={false}
        >
          {null}
        </NoDataMessage>
      )}
      <div id="layout-viewer-root" ref={hostRef} style={showNoData ? { display: "none" } : undefined} />
      {shell?.layerPanelEl && layers.length > 0 && createPortal(
        <LayerTreePanel
          layers={layers}
          hiddenLayers={hiddenLayers}
          onToggleLayer={handleToggleLayer}
          onToggleLayers={handleToggleLayers}
          defaultCollapsed
        />,
        shell.layerPanelEl,
      )}
    </>
  );
}

render(App);
