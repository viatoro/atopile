import { useEffect, useState } from "react";
import "./main.css";
import { render } from "../common/render";
import { WebviewRpcClient, rpcClient } from "../common/webviewRpcClient";
import type { Build } from "../../protocol/generated-types";
import {
  createLogClient,
} from "./logRpcClient";
import { LogViewerScreen } from "./LogViewerScreen";
import { BuildCombobox } from "./BuildCombobox";
import { samePath } from "../../protocol/paths";

function LogViewer() {
  const [client] = useState(() => {
    if (!rpcClient) {
      throw new Error("VS Code RPC client is not connected");
    }
    return createLogClient({ mode: "vscode", rpcClient });
  });
  const projectState = WebviewRpcClient.useSubscribe("projectState");
  const selectedBuild = WebviewRpcClient.useSubscribe("selectedBuild");
  const buildsByProjectData = WebviewRpcClient.useSubscribe("buildsByProjectData");
  const queueBuilds: Build[] = WebviewRpcClient.useSubscribe("queueBuilds") ?? [];
  const [stage, setStage] = useState("");

  useEffect(() => {
    return () => {
      client.dispose();
    };
  }, [client]);

  useEffect(() => {
    if (!projectState.selectedProjectRoot) {
      return;
    }
    rpcClient?.sendAction("getBuildsByProject", {
      projectRoot: projectState.selectedProjectRoot,
      target: projectState.selectedTarget,
      limit: 100,
    });
  }, [projectState.selectedProjectRoot, projectState.selectedTarget]);

  const projectBuilds =
    !projectState.selectedProjectRoot
    || !samePath(buildsByProjectData.projectRoot, projectState.selectedProjectRoot)
      ? []
      : (() => {
          const builds = [...buildsByProjectData.builds];
          if (
            samePath(selectedBuild?.projectRoot, projectState.selectedProjectRoot)
            && selectedBuild?.buildId
          ) {
            builds.unshift(selectedBuild);
          }
          // Merge real-time queue builds (fresher stage data)
          for (const qb of queueBuilds) {
            if (qb.buildId && samePath(qb.projectRoot, projectState.selectedProjectRoot)) {
              builds.unshift(qb);
            }
          }
          return builds
            .filter((build, index, entries) =>
              index === entries.findIndex((entry) => entry.buildId === build.buildId),
            )
            .sort((left, right) => (right.startedAt ?? 0) - (left.startedAt ?? 0));
        })();

  const autoBuildId =
    samePath(selectedBuild?.projectRoot, projectState.selectedProjectRoot)
    && selectedBuild?.buildId
      ? selectedBuild.buildId
      : projectBuilds[0]?.buildId ?? "";

  useEffect(() => {
    if (projectState.logViewBuildId) {
      setStage(projectState.logViewStage ?? "");
      return;
    }
    setStage("");
  }, [autoBuildId, projectState.logViewBuildId, projectState.logViewStage]);

  const buildId = projectState.logViewBuildId ?? autoBuildId;

  const target = buildId ? { mode: "build" as const, buildId, stage: stage || null } : null;
  const currentBuild = projectBuilds.find((b) => b.buildId === buildId) ?? null;

  return (
    <LogViewerScreen
      client={client}
      target={target}
      build={currentBuild}
      scopeValue={stage}
      onScopeChange={setStage}
      targetControl={
        <BuildCombobox
          builds={projectBuilds}
          value={buildId || ""}
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

render(LogViewer);
