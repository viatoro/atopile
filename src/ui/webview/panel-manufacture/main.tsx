import { useCallback, useEffect, useState } from "react";
import { render } from "../common/render";
import { WebviewRpcClient, rpcClient } from "../common/webviewRpcClient";
import {
  Alert,
  AlertDescription,
  AlertTitle,
  Button,
  Input,
  Separator,
  Spinner,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../common/components";
import type { Build } from "../../protocol/generated-types";

type PanelState = "idle" | "building" | "done" | "error";

interface Artifact {
  name: string;
  path: string;
  sizeBytes: number;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function App() {
  const projectState = WebviewRpcClient.useSubscribe("projectState");
  const currentBuilds = WebviewRpcClient.useSubscribe("currentBuilds");
  const selectedBuild = WebviewRpcClient.useSubscribe("selectedBuild");

  const [state, setState] = useState<PanelState>("idle");
  const [outputDir, setOutputDir] = useState("");
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [errorMessage, setErrorMessage] = useState("");
  const [buildId, setBuildId] = useState<string | null>(null);

  // Set default output dir when project changes
  useEffect(() => {
    if (projectState.selectedProjectRoot) {
      setOutputDir(
        `${projectState.selectedProjectRoot}/manufacturing/${projectState.selectedTarget?.name ?? "default"}`,
      );
    }
  }, [projectState.selectedProjectRoot, projectState.selectedTarget?.name]);

  // Pick up the build ID once the build appears in currentBuilds
  useEffect(() => {
    if (state !== "building" || buildId) return;
    const match = currentBuilds.find(
      (b: Build) =>
        b.projectRoot === projectState.selectedProjectRoot &&
        b.target?.name === projectState.selectedTarget?.name,
    );
    if (match?.buildId) setBuildId(match.buildId);
  }, [
    state,
    buildId,
    currentBuilds,
    projectState.selectedProjectRoot,
    projectState.selectedTarget?.name,
  ]);

  // Track build completion via selectedBuild
  useEffect(() => {
    if (state !== "building" || !buildId) return;

    // Check if our build is still in currentBuilds
    const activeBuild = currentBuilds.find(
      (b: Build) => b.buildId === buildId,
    );
    if (activeBuild) return; // still running

    // Build finished — check selectedBuild
    const finishedBuild =
      selectedBuild?.buildId === buildId ? selectedBuild : null;
    if (finishedBuild) {
      if (
        finishedBuild.status === "success" ||
        finishedBuild.status === "warning"
      ) {
        handleBuildSuccess();
      } else if (finishedBuild.status === "failed") {
        setState("error");
        setErrorMessage(finishedBuild.error || "Build failed");
      }
    }
  }, [state, buildId, currentBuilds, selectedBuild]);

  const handleBuildSuccess = useCallback(async () => {
    if (!projectState.selectedProjectRoot || !projectState.selectedTarget)
      return;
    try {
      const result = await rpcClient!.requestAction<{
        artifacts: Artifact[];
      }>("getManufacturingArtifacts", {
        projectRoot: projectState.selectedProjectRoot,
        target: projectState.selectedTarget,
      });
      setArtifacts(result.artifacts);
      setState("done");
    } catch (err) {
      setState("error");
      setErrorMessage(String(err));
    }
  }, [projectState.selectedProjectRoot, projectState.selectedTarget]);

  const handleExport = useCallback(() => {
    if (!projectState.selectedProjectRoot || !projectState.selectedTarget)
      return;
    setState("building");
    setErrorMessage("");
    setArtifacts([]);
    setBuildId(null);

    rpcClient?.sendAction("startBuild", {
      projectRoot: projectState.selectedProjectRoot,
      targets: [projectState.selectedTarget],
      frozen: true,
      includeTargets: ["collect-manufacturing"],
    });
  }, [projectState.selectedProjectRoot, projectState.selectedTarget]);

  const handleBrowseFolder = useCallback(async () => {
    const result =
      await rpcClient?.requestAction<{ result?: string }>(
        "vscode.browseFolder",
      );
    if (result?.result) {
      setOutputDir(result.result);
    }
  }, []);

  const handleReset = useCallback(() => {
    setState("idle");
    setArtifacts([]);
    setErrorMessage("");
    setBuildId(null);
  }, []);

  const noTarget = !projectState.selectedTarget;

  return (
    <div className="panel-centered">
      <div>
        <h2 style={{ marginBottom: "var(--spacing-sm)" }}>
          Export for Manufacturing
        </h2>
        <p
          style={{
            color: "var(--text-muted)",
            fontSize: "var(--font-size-sm)",
          }}
        >
          Build your project and generate all manufacturing artifacts (Gerbers,
          BOM, pick &amp; place, etc.).
        </p>
      </div>

      <Separator />

      {/* Output path */}
      <div>
        <label
          style={{
            display: "block",
            fontSize: "var(--font-size-sm)",
            marginBottom: "var(--spacing-xs)",
            color: "var(--text-muted)",
          }}
        >
          Output directory
        </label>
        <div style={{ display: "flex", gap: "var(--spacing-sm)" }}>
          <Input
            value={outputDir}
            onChange={(e) => setOutputDir(e.target.value)}
            style={{ flex: 1 }}
          />
          <Button variant="outline" size="sm" onClick={handleBrowseFolder}>
            ...
          </Button>
        </div>
      </div>

      {/* Error state */}
      {state === "error" && (
        <Alert variant="destructive">
          <AlertTitle>Build Failed</AlertTitle>
          <AlertDescription>{errorMessage}</AlertDescription>
        </Alert>
      )}

      {/* Export button */}
      {(state === "idle" || state === "error") && (
        <Button onClick={handleExport} disabled={noTarget}>
          {noTarget
            ? "Select a build target first"
            : "Export for Manufacturing"}
        </Button>
      )}

      {/* Building state */}
      {state === "building" && (() => {
        const activeBuild = buildId
          ? currentBuilds.find((b: Build) => b.buildId === buildId)
          : null;
        const runningStage = activeBuild?.stages.slice().reverse().find((s: Build["stages"][number]) => s.status === "running");
        const doneCount = activeBuild?.stages.filter(
          (s) => s.status === "success" || s.status === "warning",
        ).length ?? 0;
        const total = activeBuild?.totalStages ?? activeBuild?.stages.length;
        const statusText = runningStage
          ? `${runningStage.name}${total ? ` (${doneCount}/${total})` : ""}`
          : activeBuild?.status === "queued"
            ? "Queued..."
            : "Building...";
        return (
          <>
            <Button disabled>
              <Spinner
                size={16}
                style={{ marginRight: "var(--spacing-sm)" }}
              />
              Building...
            </Button>
            <p
              style={{
                color: "var(--text-muted)",
                fontSize: "var(--font-size-sm)",
                margin: 0,
              }}
            >
              {statusText}
            </p>
          </>
        );
      })()}

      {/* Done state — artifacts table */}
      {state === "done" && (
        <>
          <div>
            <h3 style={{ marginBottom: "var(--spacing-sm)" }}>
              Build Artifacts ({artifacts.length})
            </h3>
            {artifacts.length > 0 ? (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>File</TableHead>
                    <TableHead style={{ textAlign: "right" }}>Size</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {artifacts.map((a) => (
                    <TableRow key={a.path}>
                      <TableCell>{a.name}</TableCell>
                      <TableCell style={{ textAlign: "right" }}>
                        {formatBytes(a.sizeBytes)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : (
              <p
                style={{
                  color: "var(--text-muted)",
                  fontSize: "var(--font-size-sm)",
                }}
              >
                No artifacts found in the build output directory.
              </p>
            )}
          </div>

          <Separator />

          <div
            style={{
              display: "flex",
              gap: "var(--spacing-sm)",
              flexWrap: "wrap",
            }}
          >
            <Button
              variant="default"
              onClick={() =>
                rpcClient?.sendAction("vscode.openExternal", {
                  url: "https://cart.jlcpcb.com/quote",
                })
              }
            >
              Order PCBs
            </Button>
            <Button
              variant="outline"
              onClick={() =>
                rpcClient?.sendAction("vscode.revealInOs", {
                  path: outputDir,
                })
              }
            >
              Show in Folder
            </Button>
            <Button
              variant="outline"
              onClick={() =>
                rpcClient?.sendAction("vscode.revealInExplorer", {
                  path: outputDir,
                })
              }
            >
              Show in Workspace
            </Button>
            <Button variant="ghost" onClick={handleReset}>
              Export Again
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

render(App);
