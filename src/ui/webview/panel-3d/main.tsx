import { useEffect, useState } from "react";
import { Box } from "lucide-react";
import type { ResolvedBuildTarget } from "../../protocol/generated-types";
import { samePath } from "../../protocol/paths";
import { render } from "../common/render";
import { NoDataMessage } from "../common/components";
import { WebviewRpcClient, rpcClient } from "../common/webviewRpcClient";
import { createWebviewLogger } from "../common/logger";
import { ThreeDPreview } from "./threeDPreviewManager";
import "./panel-3d.css";

const logger = createWebviewLogger("Panel3D");

type ThreeDModel = {
  exists: boolean;
  modelPath: string;
  modelUri: string;
};


async function waitForModel(
  target: ResolvedBuildTarget,
  options?: {
    attempts?: number;
    delayMs?: number;
  },
): Promise<ThreeDModel> {
  const attempts = options?.attempts ?? 10;
  const delayMs = options?.delayMs ?? 150;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const model = await rpcClient!.requestAction<ThreeDModel>("vscode.resolveThreeDModel", {
      target,
    });
    if (model.exists && model.modelUri) {
      return model;
    }
    if (attempt < attempts - 1) {
      await new Promise((resolve) => setTimeout(resolve, delayMs));
    }
  }
  throw new Error(`3D model was not generated at ${target.modelPath}`);
}

function App() {
  const projectState = WebviewRpcClient.useSubscribe("projectState");
  const layoutData = WebviewRpcClient.useSubscribe("layoutData");
  const selectedBuildInProgress = WebviewRpcClient.useSubscribe("selectedBuildInProgress");
  const selectedTarget = projectState.selectedTarget;
  const hasSelection = Boolean(projectState.selectedProjectRoot && selectedTarget);
  const hasLayout = Boolean(
    selectedTarget
    && layoutData.path
    && samePath(layoutData.path, selectedTarget.pcbPath),
  );
  const layoutRevisionKey = selectedTarget && hasLayout
    ? `${selectedTarget.root}::${selectedTarget.name}::${selectedTarget.entry}::${selectedTarget.pcbPath}::${selectedTarget.modelPath}:${layoutData.revision}`
    : "";

  const [model, setModel] = useState<ThreeDModel | null>(null);
  const [isGenerating, setIsGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    setError(null);
    if (!layoutRevisionKey || !selectedTarget) {
      setIsGenerating(false);
      setModel(null);
      return;
    }

    setError(null);
    setIsGenerating(true);
    setModel(null);
    void rpcClient!.requestAction("generateThreeDModel", { target: selectedTarget })
      .then(() => waitForModel(selectedTarget))
      .then((nextModel) => {
        if (!cancelled) {
          setModel(nextModel);
        }
      })
      .catch((nextError) => {
        logger.error(
          `Failed to generate 3D model: ${nextError instanceof Error ? nextError.message : String(nextError)}`,
        );
        if (!cancelled) {
          setError(nextError instanceof Error ? nextError.message : String(nextError));
        }
      })
      .finally(() => {
        if (!cancelled) {
          setIsGenerating(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [layoutRevisionKey, selectedTarget]);

  const modelError = layoutData.error
    ? String(layoutData.error)
    : error;
  const modelReady = Boolean(model?.exists && model.modelUri);

  return (
    <NoDataMessage
      icon={<Box size={24} />}
      noun="3D model"
      hasSelection={hasSelection}
      isLoading={isGenerating && !modelReady}
      buildInProgress={selectedBuildInProgress}
      error={modelError}
      hasData={hasLayout && modelReady}
      noDataDescription="Run a build to generate the 3D model."
    >
      <div className="panel-3d-container">
        <ThreeDPreview
          key={layoutRevisionKey}
          modelUri={model?.modelUri ?? ""}
        />
        {isGenerating && (
          <div className="panel-3d-badge">
            <span className="panel-3d-badge-spinner" />
            <span>Generating...</span>
          </div>
        )}
      </div>
    </NoDataMessage>
  );
}

render(App);
