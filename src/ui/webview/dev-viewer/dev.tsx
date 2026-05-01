/**
 * Standalone browser dev harness for the sidebar + log viewer.
 *
 * Connects directly to the atopile core server via WebSocket,
 * bypassing the VS Code extension host.
 *
 * Quick start:  ./devctl up
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { WebSocketTransport } from "../../protocol/webSocketTransport";
import { render } from "../common/render";
import { WebviewRpcClient, rpcClient } from "../common/webviewRpcClient";
import { SidebarApp } from "../sidebar/SidebarApp";
import type { Build } from "../../protocol/generated-types";
import { createLogClient, type LogTarget } from "../panel-logs/logRpcClient";
import { LogViewerScreen } from "../panel-logs/LogViewerScreen";
import { BuildCombobox } from "../panel-logs/BuildCombobox";
import { samePath } from "../../protocol/paths";

const params = new URLSearchParams(window.location.search);
const wsUrl = `ws://${window.location.host}/atopile-ui`;

const SIDEBAR_MIN = 280;
const SIDEBAR_MAX = 600;
const PANEL_MIN = 120;
const PANEL_DEFAULT_RATIO = 0.4; // bottom panel takes 40% of height

function DevLogViewer() {
  const [client] = useState(() => {
    if (!rpcClient) {
      throw new Error("RPC client is not connected");
    }
    return createLogClient({ mode: "vscode", rpcClient });
  });
  const projectState = WebviewRpcClient.useSubscribe("projectState");
  const selectedBuild = WebviewRpcClient.useSubscribe("selectedBuild");
  const recentBuildsData = WebviewRpcClient.useSubscribe("recentBuildsData");
  const queueBuilds: Build[] = WebviewRpcClient.useSubscribe("queueBuilds") ?? [];
  const [stage, setStage] = useState("");

  useEffect(() => {
    return () => {
      client.dispose();
    };
  }, [client]);

  // Fetch recent builds on mount and whenever queue changes (builds complete)
  const queueLen = queueBuilds.length;
  useEffect(() => {
    rpcClient?.sendAction("getRecentBuilds", { limit: 100 });
  }, [queueLen]);

  const allBuilds = (() => {
    const builds = [...(recentBuildsData.builds || [])];
    if (selectedBuild?.buildId) {
      builds.unshift(selectedBuild);
    }
    // Merge real-time queue builds (fresher stage data)
    for (const qb of queueBuilds) {
      if (qb.buildId) builds.unshift(qb);
    }
    return builds
      .filter((build, index, entries) =>
        index === entries.findIndex((entry) => entry.buildId === build.buildId),
      )
      .sort((left, right) => {
        // Active project builds first
        const leftMatch = samePath(left.projectRoot, projectState.selectedProjectRoot) ? 0 : 1;
        const rightMatch = samePath(right.projectRoot, projectState.selectedProjectRoot) ? 0 : 1;
        if (leftMatch !== rightMatch) return leftMatch - rightMatch;
        return (right.startedAt ?? 0) - (left.startedAt ?? 0);
      });
  })();

  const autoBuildId = selectedBuild?.buildId
    ? selectedBuild.buildId
    : allBuilds[0]?.buildId ?? "";

  useEffect(() => {
    if (projectState.logViewBuildId) {
      setStage(projectState.logViewStage ?? "");
      return;
    }
    setStage("");
  }, [autoBuildId, projectState.logViewBuildId, projectState.logViewStage]);

  const buildId = projectState.logViewBuildId ?? autoBuildId;

  const target: LogTarget | null = buildId
    ? { mode: "build", buildId, stage: stage || null }
    : null;

  const currentBuild = allBuilds.find((b) => b.buildId === buildId) ?? null;

  return (
    <LogViewerScreen
      client={client}
      target={target}
      build={currentBuild}
      scopeValue={stage}
      onScopeChange={setStage}
      targetControl={
        <BuildCombobox
          builds={allBuilds}
          value={buildId}
          onSelect={(value) => {
            setStage("");
            rpcClient?.sendAction("setLogViewCurrentId", {
              buildId: value || null,
              stage: null,
            });
          }}
        />
      }
    />
  );
}

function useDrag(
  axis: "x" | "y",
  onDrag: (pos: number) => void,
) {
  const dragging = useRef(false);

  const onPointerDown = useCallback((e: React.PointerEvent) => {
    dragging.current = true;
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  }, []);

  const onPointerMove = useCallback(
    (e: React.PointerEvent) => {
      if (!dragging.current) return;
      onDrag(axis === "x" ? e.clientX : e.clientY);
    },
    [axis, onDrag],
  );

  const onPointerUp = useCallback(() => {
    dragging.current = false;
  }, []);

  return { onPointerDown, onPointerMove, onPointerUp };
}

const dividerStyle = {
  flexShrink: 0,
  background: "var(--border-subtle)",
} as const;

function DevApp() {
  const [sidebarWidth, setSidebarWidth] = useState(360);
  const [panelHeight, setPanelHeight] = useState(() =>
    Math.round(window.innerHeight * PANEL_DEFAULT_RATIO),
  );
  const mainRef = useRef<HTMLDivElement>(null);

  const sidebarDrag = useDrag("x", useCallback((x: number) => {
    setSidebarWidth(Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, x)));
  }, []));

  const panelDrag = useDrag("y", useCallback((y: number) => {
    const mainEl = mainRef.current;
    if (!mainEl) return;
    const rect = mainEl.getBoundingClientRect();
    const fromBottom = rect.bottom - y;
    setPanelHeight(Math.max(PANEL_MIN, Math.min(rect.height - PANEL_MIN, fromBottom)));
  }, []));

  return (
    <div style={{ display: "flex", height: "100vh", overflow: "hidden" }}>
      {/* Sidebar */}
      <div style={{ width: sidebarWidth, minWidth: SIDEBAR_MIN, flexShrink: 0, overflow: "auto" }}>
        <SidebarApp />
      </div>
      <div
        {...sidebarDrag}
        style={{ ...dividerStyle, width: 4, cursor: "col-resize" }}
      />
      {/* Main area: empty top + log viewer bottom */}
      <div ref={mainRef} style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <div style={{ flex: 1, overflow: "auto" }} />
        <div
          {...panelDrag}
          style={{ ...dividerStyle, height: 4, cursor: "row-resize" }}
        />
        <div className="dev-log-panel" style={{ height: panelHeight, minHeight: PANEL_MIN, flexShrink: 0, overflow: "hidden" }}>
          <DevLogViewer />
        </div>
      </div>
    </div>
  );
}

render(DevApp, {
  createTransport: () => new WebSocketTransport(() => new WebSocket(wsUrl)),
});

// Send the bootstrap messages the VS Code extension normally sends.
rpcClient?.addConnectionListener((connected) => {
  if (!connected) return;
  rpcClient?.sendAction("resolverInfo", {
    uvPath: "",
    atoBinary: "",
    mode: "local",
    version: "dev",
    coreServerPort: 18730,
  });
  rpcClient?.sendAction("extensionSettings", { enableChat: true });
  const projectPath = params.get("project") || import.meta.env.VITE_PROJECT_PATH;
  if (projectPath) {
    rpcClient?.sendAction("discoverProjects", { paths: [projectPath] });
  }
});
