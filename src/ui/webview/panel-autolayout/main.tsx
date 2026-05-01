import { useCallback, useEffect } from "react";
import { AlertCircle, Grid2x2, Route, Sparkles } from "lucide-react";
import { render } from "../common/render";
import { WebviewRpcClient, rpcClient } from "../common/webviewRpcClient";
import type { UiAutolayoutData } from "../../protocol/generated-types";
import { PhaseSection } from "./PhaseSection";
import type { JobPhase } from "./helpers";
import "./autolayout.css";

// Top-level autolayout panel. Subscribes to one store key
// (autolayoutData) and dispatches actions back to the backend.
// All derivation, state, and policy lives on the backend; this file
// is wiring + a top-level render only.
function App() {
  const { selectedProjectRoot: projectRoot, selectedTarget } =
    WebviewRpcClient.useSubscribe("projectState");
  const data: UiAutolayoutData = WebviewRpcClient.useSubscribe("autolayoutData");

  const selectedTargetName = selectedTarget?.name ?? null;

  const openLayoutPanel = useCallback(() => {
    rpcClient?.requestAction("vscode.openPanel", { panelId: "panel-layout" });
  }, []);

  const setLayoutTitle = useCallback((title: string) => {
    rpcClient?.requestAction("vscode.setPanelTitle", {
      panelId: "panel-layout",
      title,
    });
  }, []);

  // Sync layout and open viewer alongside this panel
  useEffect(() => {
    if (!projectRoot || !selectedTargetName) return;
    // getAutolayoutData triggers _sync_selected_layout on the backend,
    // which sets layoutData.path to the project file. We must wait for
    // that before opening the layout panel so it doesn't mount with a
    // stale candidate preview path.
    rpcClient?.requestAction("getAutolayoutData", { projectRoot }).then(() => {
      openLayoutPanel();
      setLayoutTitle(selectedTargetName);
    });
  }, [projectRoot, selectedTargetName, openLayoutPanel, setLayoutTitle]);

  // Fetch preflight on target change
  useEffect(() => {
    if (projectRoot && selectedTargetName) {
      rpcClient?.sendAction("getAutolayoutPreflight", { projectRoot, buildTarget: selectedTargetName });
    }
  }, [projectRoot, selectedTargetName]);

  // Preflight is automatically recomputed by the backend on layout changes
  // and pushed via the autolayoutData store subscription.

  // Auto-preview of best candidate on job completion is handled by the
  // backend (AutolayoutService._on_job_completed).

  // Actions
  const handleRun = useCallback((phase: JobPhase, processingMinutes: number) => {
    if (!projectRoot || !selectedTargetName) return;
    rpcClient?.sendAction("submitAutolayoutJob", {
      projectRoot,
      buildTarget: selectedTargetName,
      jobType: phase,
      // Wire field is still `timeoutMinutes` — the rename is UI-only.
      timeoutMinutes: processingMinutes,
    });
  }, [projectRoot, selectedTargetName]);

  const handlePreviewCandidate = useCallback((jobId: string, candidateId: string, label?: string) => {
    rpcClient?.sendAction("previewAutolayoutCandidate", { jobId, candidateId });
    setLayoutTitle(label || "Candidate");
  }, [setLayoutTitle]);

  const handleCancel = useCallback((jobId: string) => {
    rpcClient?.sendAction("cancelAutolayoutJob", { jobId });
  }, []);

  const handleApplyCandidate = useCallback((jobId: string, candidateId: string) => {
    rpcClient?.sendAction("applyAutolayoutCandidate", { jobId, candidateId });
    if (selectedTargetName) setLayoutTitle(selectedTargetName);
  }, [selectedTargetName, setLayoutTitle]);

  // Empty state
  if (!projectRoot || !selectedTargetName) {
    return (
      <div className="al-panel al-empty">
        <Sparkles size={24} />
        <h2>Autolayout</h2>
        <p>Select a project and build target to get started.</p>
      </div>
    );
  }

  return (
    <div className="al-panel">
      <div className="al-panel-header">
        <Sparkles size={18} />
        <h2>Autolayout</h2>
      </div>

      {data.error && (
        <div className="al-error">
          <AlertCircle size={14} />
          <span>{data.error}</span>
        </div>
      )}

      <PhaseSection
        phase="Placement"
        icon={<Grid2x2 size={16} />}
        checks={data.placementReadiness}
        allJobs={data.jobs}
        projectRoot={projectRoot}
        targetName={selectedTargetName}
        submitting={data.submitting}
        preflight={data.preflight}
        preflightLoading={data.preflightLoading}
        previewJobId={data.previewJobId}
        previewCandidateId={data.previewCandidateId}
        onRun={(minutes) => handleRun("Placement", minutes)}
        onCancel={handleCancel}
        onPreviewCandidate={handlePreviewCandidate}
        onApplyCandidate={handleApplyCandidate}
      />

      <PhaseSection
        phase="Routing"
        icon={<Route size={16} />}
        checks={data.routingReadiness}
        allJobs={data.jobs}
        projectRoot={projectRoot}
        targetName={selectedTargetName}
        submitting={data.submitting}
        preflight={data.preflight}
        preflightLoading={data.preflightLoading}
        previewJobId={data.previewJobId}
        previewCandidateId={data.previewCandidateId}
        onRun={(minutes) => handleRun("Routing", minutes)}
        onCancel={handleCancel}
        onPreviewCandidate={handlePreviewCandidate}
        onApplyCandidate={handleApplyCandidate}
      />
    </div>
  );
}

render(App);
