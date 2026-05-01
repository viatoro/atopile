import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  Clock3,
  Info,
  Settings2,
  Sparkles,
  X,
} from "lucide-react";
import { Spinner } from "../common/components";
import type {
  UiAutolayoutJobData,
  UiAutolayoutPreCheckItem,
  UiAutolayoutPreflightData,
} from "../../protocol/generated-types";
import { CandidateCard } from "./CandidateCard";
import { PreCheckList } from "./PreCheckList";
import { StepPipeline } from "./StepPipeline";
import {
  formatArea,
  formatDensity,
  formatPercent,
  formatSidedness,
  getCandidateLabel,
  getJobsForPhase,
  getRecommendedCandidate,
  jobDisplayState,
  runningLabel,
  timeAgo,
  type JobPhase,
} from "./helpers";

// PhaseSection — fully self-contained per phase.
//
// Local state is deliberately minimal and defensible:
// - processingMinutes (form input before submit)
// - showAdvanced / showHistory / showAllCandidates (disclosure toggles)
// - viewingHistoryJobId (which past job the user is inspecting)
// - pendingAction (which button was just clicked — cleared as soon as
//   the wire model confirms the intended outcome)
// - errorDismissed (user closed a failure banner — stays closed until
//   a different job produces a new error)
//
// All *domain* state (jobs, candidates, readiness, preview) arrives via
// props from the parent — which just passes through what the backend
// pushed in autolayoutData.

export function PhaseSection({
  phase,
  icon,
  checks,
  allJobs,
  projectRoot,
  targetName,
  submitting,
  preflight,
  preflightLoading,
  previewJobId,
  previewCandidateId,
  onRun,
  onCancel,
  onPreviewCandidate,
  onApplyCandidate,
}: {
  phase: JobPhase;
  icon: React.ReactNode;
  checks: UiAutolayoutPreCheckItem[];
  allJobs: UiAutolayoutJobData[];
  projectRoot: string | null;
  targetName: string | null;
  submitting: boolean;
  preflight: UiAutolayoutPreflightData | null;
  preflightLoading: boolean;
  previewJobId: string | null;
  previewCandidateId: string | null;
  onRun: (processingMinutes: number) => void;
  onCancel: (jobId: string) => void;
  onPreviewCandidate: (jobId: string, candidateId: string, label?: string) => void;
  onApplyCandidate: (jobId: string, candidateId: string) => void;
}) {
  const [processingMinutes, setProcessingMinutes] = useState(1);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [showAllCandidates, setShowAllCandidates] = useState(false);
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [viewingHistoryJobId, setViewingHistoryJobId] = useState<string | null>(null);
  const [errorDismissedJobId, setErrorDismissedJobId] = useState<string | null>(null);

  // All jobs for this phase, sorted newest first
  const phaseJobs = useMemo(
    () => getJobsForPhase(allJobs, projectRoot, targetName, phase),
    [allJobs, projectRoot, targetName, phase],
  );

  const latestJob = phaseJobs[0] ?? null;
  const state = jobDisplayState(latestJob);

  // The job whose candidates we're currently viewing
  // Either the latest job (default) or a history job the user clicked
  const activeJob = useMemo(() => {
    if (viewingHistoryJobId) {
      return phaseJobs.find((j) => j.jobId === viewingHistoryJobId) ?? latestJob;
    }
    return latestJob;
  }, [viewingHistoryJobId, phaseJobs, latestJob]);

  const activeJobDone = jobDisplayState(activeJob) === "done";
  const bestCandidate = getRecommendedCandidate(activeJob);

  // Sort by routedPct descending, with nulls last
  const sortedCandidates = useMemo(() => {
    if (!activeJob) return [];
    return [...activeJob.candidates].sort((a, b) => {
      const aPct = a.routedPct ?? -1;
      const bPct = b.routedPct ?? -1;
      return bPct - aPct;
    });
  }, [activeJob]);

  // Clear pendingAction as soon as the wire model confirms the
  // intended outcome. This prevents the spinner from lingering after
  // the action completed — and, more importantly, prevents a stale
  // "pending" marker from outlasting the action that set it.
  useEffect(() => {
    if (pendingAction == null) return;
    if (pendingAction === "run") {
      // "run" is satisfied once a job exists in running/done/failed.
      if (latestJob && latestJob.displayState !== "idle") {
        setPendingAction(null);
      }
      return;
    }
    const [kind, candidateId] = pendingAction.split("-", 2);
    if (!candidateId) return;
    if (kind === "preview") {
      const previewConfirmed =
        previewJobId === activeJob?.jobId && previewCandidateId === candidateId;
      if (previewConfirmed) setPendingAction(null);
    } else if (kind === "apply") {
      if (activeJob?.appliedCandidateId === candidateId) setPendingAction(null);
    }
  }, [pendingAction, latestJob, activeJob, previewJobId, previewCandidateId]);

  // Reset history view when a new job appears
  useEffect(() => { setViewingHistoryJobId(null); }, [latestJob?.jobId]);

  // Show the failure banner when the latest job errored — unless the
  // user explicitly dismissed *that* job's error.
  const showError = state === "failed" && errorDismissedJobId !== latestJob?.jobId;

  const allChecksPassed = checks.every((c) => c.passed);
  const canRun = allChecksPassed && state !== "running" && !submitting;
  const isViewingHistory = viewingHistoryJobId != null && viewingHistoryJobId !== latestJob?.jobId;

  // History jobs = all except the latest (which is shown inline)
  const historyJobs = phaseJobs.slice(1);

  return (
    <section className="al-section">
      {/* Header */}
      <div className="al-section-header">
        <span className="al-section-icon">{icon}</span>
        <h3 className="al-section-title">{phase}</h3>
        {state === "running" && <Spinner size={14} />}
        {activeJob?.appliedCandidateId && (
          <span className="al-section-status applied">
            <CheckCircle2 size={12} /> Applied
          </span>
        )}
      </div>

      {/* Pre-checks */}
      <PreCheckList checks={checks} />

      {/* Run / Cancel buttons */}
      <div className="al-action-row">
        <button
          className="al-run-button"
          onClick={() => { setPendingAction("run"); onRun(processingMinutes); }}
          disabled={!canRun}
        >
          {state === "running"
            ? <Spinner size={14} />
            : <Sparkles size={14} />
          }
          <span>
            {state === "running"
              ? runningLabel(latestJob!)
              : phase === "Placement" ? "Place" : "Route"
            }
          </span>
        </button>
        {state === "running" && latestJob && (
          <button
            className="al-cancel-button"
            onClick={() => onCancel(latestJob.jobId)}
          >
            Cancel
          </button>
        )}
      </div>

      {/* Step pipeline */}
      {state === "running" && latestJob && (
        <StepPipeline state={latestJob.state} progress={latestJob.progress} />
      )}

      {/* Error — sticky until the user closes it or a new job appears */}
      {showError && latestJob && (
        <div className="al-error">
          <AlertCircle size={14} />
          <span>{latestJob.error || "Job failed"}</span>
          <button
            className="al-error-dismiss"
            aria-label="Dismiss error"
            onClick={() => setErrorDismissedJobId(latestJob.jobId)}
          >
            <X size={12} />
          </button>
        </div>
      )}

      {/* Viewing history banner */}
      {isViewingHistory && (
        <div className="al-history-viewing">
          <span>Viewing past job from {timeAgo(activeJob!.createdAt)}</span>
          <button onClick={() => setViewingHistoryJobId(null)}>Back to latest</button>
        </div>
      )}

      {/* Candidates */}
      {activeJobDone && activeJob && activeJob.candidates.length > 0 && (
        <div className="al-candidates">
          {bestCandidate && (
            <CandidateCard
              candidate={bestCandidate}
              index={0}
              isRecommended
              isApplied={activeJob.appliedCandidateId === bestCandidate.candidateId}
              isActive={pendingAction === `preview-${bestCandidate.candidateId}`}

              onPreview={() => {
                setPendingAction(`preview-${bestCandidate.candidateId}`);
                onPreviewCandidate(activeJob.jobId, bestCandidate.candidateId, getCandidateLabel(bestCandidate, 0));
              }}
              onApply={() => {
                setPendingAction(`apply-${bestCandidate.candidateId}`);
                onApplyCandidate(activeJob.jobId, bestCandidate.candidateId);
              }}
              disabled={submitting}
            />
          )}

          {sortedCandidates.length > 1 && (
            <>
              <button
                className="al-show-all-toggle"
                onClick={() => setShowAllCandidates((v) => !v)}
              >
                <span>
                  {showAllCandidates ? "Hide" : "Show"} all candidates ({sortedCandidates.length})
                </span>
                <ChevronDown size={14} className={showAllCandidates ? "al-chevron-open" : ""} />
              </button>
              {showAllCandidates && (
                <div className="al-candidates-list">
                  {sortedCandidates
                    .filter((c) => c.candidateId !== bestCandidate?.candidateId)
                    .map((c, i) => (
                      <CandidateCard
                        key={c.candidateId}
                        candidate={c}
                        index={i + 1}
                        isRecommended={false}
                        isApplied={activeJob.appliedCandidateId === c.candidateId}
                        isActive={pendingAction === `preview-${c.candidateId}`}
                        onPreview={() => {
                          setPendingAction(`preview-${c.candidateId}`);
                          onPreviewCandidate(activeJob.jobId, c.candidateId, getCandidateLabel(c, i + 1));
                        }}
                        onApply={() => {
                          setPendingAction(`apply-${c.candidateId}`);
                          onApplyCandidate(activeJob.jobId, c.candidateId);
                        }}
                        disabled={submitting}
                      />
                    ))}
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* Advanced — per phase */}
      <button className="al-collapse-toggle" onClick={() => setShowAdvanced((v) => !v)}>
        <Settings2 size={13} />
        <span>Advanced</span>
        <ChevronDown size={13} className={showAdvanced ? "al-chevron-open" : ""} />
      </button>
      {showAdvanced && (
        <div className="al-collapse-content">
          <div className="al-advanced-field">
            <label htmlFor={`al-processing-${phase}`}>
              Processing time (minutes)
              <span
                className="al-timeout-info"
                title="How long the autolayout provider will spend searching for a result. Billed per minute. 1 minute is usually fine for small boards (<30 parts); more time generally produces better results on larger or denser boards."
              >
                <Info size={12} />
              </span>
            </label>
            <input
              id={`al-processing-${phase}`}
              type="number"
              min={1}
              max={120}
              value={processingMinutes}
              onChange={(e) => setProcessingMinutes(Math.max(1, Number(e.target.value) || 1))}
            />
          </div>
          {preflight && (
            <div className="al-preflight-grid">
              <div className="al-preflight-item">
                <span className="al-preflight-label">Utilization</span>
                <span className="al-preflight-value">{formatPercent(preflight.placementUtilization)}</span>
                <span className="al-preflight-detail">
                  {formatArea(preflight.componentAreaMm2)} / {formatArea(preflight.boardAreaMm2)}
                </span>
              </div>
              <div className="al-preflight-item">
                <span className="al-preflight-label">Conn. Density</span>
                <span className="al-preflight-value">{formatDensity(preflight.connectionDensity)}</span>
                <span className="al-preflight-detail">
                  {preflight.connectionCount} conn / {preflight.netCount} nets
                </span>
              </div>
              <div className="al-preflight-item">
                <span className="al-preflight-label">Pad Density</span>
                <span className="al-preflight-value">{formatDensity(preflight.padDensity)}</span>
                <span className="al-preflight-detail">{preflight.padCount} pads</span>
              </div>
              <div className="al-preflight-item">
                <span className="al-preflight-label">Sides</span>
                <span className="al-preflight-value">{formatSidedness(preflight.sidedness)}</span>
                <span className="al-preflight-detail">
                  {preflight.topComponentCount} top / {preflight.bottomComponentCount} bottom
                </span>
              </div>
              {phase === "Routing" && (
                <div className="al-preflight-item">
                  <span className="al-preflight-label">Stackup</span>
                  <span className="al-preflight-value">
                    {preflight.layerCount != null ? `${preflight.layerCount}L` : ""} {preflight.stackupRisk} risk
                  </span>
                  <span className="al-preflight-detail">
                    {preflight.boardWidthMm != null && preflight.boardHeightMm != null
                      ? `${preflight.boardWidthMm.toFixed(1)} x ${preflight.boardHeightMm.toFixed(1)} mm`
                      : ""}
                  </span>
                </div>
              )}
            </div>
          )}
          {!preflight && (
            <p className="al-preflight-empty">
              {preflightLoading ? "Loading board analysis..." : "No board analysis available yet."}
            </p>
          )}
        </div>
      )}

      {/* History — per phase */}
      {historyJobs.length > 0 && (
        <>
          <button className="al-collapse-toggle" onClick={() => setShowHistory((v) => !v)}>
            <Clock3 size={13} />
            <span>History ({historyJobs.length})</span>
            <ChevronDown size={13} className={showHistory ? "al-chevron-open" : ""} />
          </button>
          {showHistory && (
            <div className="al-collapse-content">
              <div className="al-history-list">
                {historyJobs.map((job) => {
                  const isViewing = viewingHistoryJobId === job.jobId;
                  return (
                    <button
                      key={job.jobId}
                      className={`al-history-item ${isViewing ? "viewing" : ""}`}
                      onClick={() => setViewingHistoryJobId(isViewing ? null : job.jobId)}
                    >
                      <span className={`al-history-state ${job.state}`}>
                        {job.appliedCandidateId ? "applied" : job.state}
                      </span>
                      <span className="al-history-time">{timeAgo(job.createdAt)}</span>
                      <span className="al-history-candidates">
                        {job.candidates.length} candidate{job.candidates.length !== 1 ? "s" : ""}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}
