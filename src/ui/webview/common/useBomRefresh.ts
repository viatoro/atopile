import { useCallback, useEffect } from "react";
import { WebviewRpcClient, rpcClient } from "./webviewRpcClient";

/**
 * Subscribe to BOM data and auto-refresh when:
 *  1. The selected project/target changes
 *  2. All active builds finish (new artifact may be available)
 */
export function useBomRefresh() {
  const projectState = WebviewRpcClient.useSubscribe("projectState");
  const bomData = WebviewRpcClient.useSubscribe("bomData");
  const currentBuilds = WebviewRpcClient.useSubscribe("currentBuilds");

  const refreshBom = useCallback(() => {
    if (!projectState.selectedProjectRoot) return;
    rpcClient?.sendAction("getBom", {
      projectRoot: projectState.selectedProjectRoot,
      target: projectState.selectedTarget,
    });
  }, [projectState.selectedProjectRoot, projectState.selectedTarget]);

  // Fetch when project/target changes
  useEffect(() => {
    refreshBom();
  }, [refreshBom]);

  // Re-fetch when all builds finish
  useEffect(() => {
    if (
      projectState.selectedProjectRoot &&
      currentBuilds.every((build) => build.status !== "building" && build.status !== "queued")
    ) {
      refreshBom();
    }
  }, [currentBuilds, projectState.selectedProjectRoot, refreshBom]);

  return { projectState, bomData, currentBuilds, refreshBom };
}
