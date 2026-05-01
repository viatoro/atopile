import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  ChevronDown,
  Clock3,
  Loader2,
  Sparkles,
  Square,
} from "lucide-react";
import {
  EmptyState,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../common/components";
import { WebviewRpcClient, rpcClient } from "../common/webviewRpcClient";
import type {
  AutolayoutState,
  UiAutolayoutData,
  UiAutolayoutJobData,
  UiAutolayoutCandidateData,
  UiAutolayoutPreflightData,
} from "../../protocol/generated-types";
import "./AutolayoutPanel.css";

// ---------------------------------------------------------------------------
// Local UI-only types (not stored in backend)
// ---------------------------------------------------------------------------
type CandidateSortKey = "candidate" | "routed" | "vias" | "length";
type PreflightTone = "low" | "medium" | "high";

interface PreflightRow {
  label: string;
  value: string;
  detail: string;
  tone: PreflightTone;
  scale: number;
}

const MODE_OPTIONS = [
  { label: "Routing", value: "Routing" },
  { label: "Placement", value: "Placement" },
] as const;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function formatTimestamp(value: string): string {
  if (!value) return "Unknown";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function timeAgo(value: string): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const diffMs = Date.now() - date.getTime();
  const mins = Math.floor(diffMs / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function jobTypeShort(job: UiAutolayoutJobData): string {
  const jt = job.jobType;
  if (jt === "Placement") return "Place";
  if (jt === "Routing") return "Route";
  return jt || "—";
}

function formatScore(score: number | null): string {
  return score == null ? "No score" : score.toFixed(3);
}

function formatMoney(amount: number): string {
  return `$${amount.toFixed(2)}`;
}

function formatPercent(value: number | null): string {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}

function formatArea(value: number | null): string {
  return value == null ? "—" : `${value.toFixed(value >= 100 ? 0 : 1)} mm²`;
}

function formatDensity(value: number | null): string {
  return value == null ? "—" : `${value.toFixed(2)}/mm²`;
}

function formatFraction(done: number | null, total: number | null): string {
  if (done != null && total != null) return `${done}/${total}`;
  if (done != null) return String(done);
  if (total != null) return String(total);
  return "—";
}

function formatSidedness(value: string): string {
  switch (value) {
    case "top-only": return "Top only";
    case "bottom-only": return "Bottom only";
    case "dual-side": return "Dual side";
    default: return "Unknown";
  }
}

function clampScale(value: number): number {
  return Math.max(0.08, Math.min(1, value));
}

function estimateJobCost(timeoutMinutes: number): number {
  return Math.round(timeoutMinutes * 0.5 * 100) / 100;
}

function stateTone(state: AutolayoutState): "neutral" | "pending" | "running" | "ready" | "success" | "error" {
  if (state === "completed") return "success";
  if (state === "failed" || state === "cancelled") return "error";
  if (state === "awaiting_selection") return "ready";
  if (state === "running") return "running";
  if (["building", "submitting", "queued"].includes(state)) return "pending";
  return "neutral";
}

function stateLabel(state: AutolayoutState): string {
  if (state === "building") return "Building";
  if (state === "submitting") return "Submitting";
  if (state === "awaiting_selection") return "Ready";
  const s = state.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function jobStatusTone(job: Pick<UiAutolayoutJobData, "state" | "appliedCandidateId">) {
  return job.appliedCandidateId ? ("success" as const) : stateTone(job.state);
}

function jobStatusLabel(job: Pick<UiAutolayoutJobData, "state" | "appliedCandidateId" | "message" | "error">) {
  if (job.appliedCandidateId) return "applied";
  if (job.state === "failed") return "Failed";
  if (job.state === "cancelled") return "Cancelled";
  if (job.message) return job.message;
  return stateLabel(job.state);
}

function readNumber(source: Record<string, unknown>, keys: string[]): number | null {
  for (const key of keys) {
    const value = source[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return null;
}

function readString(source: Record<string, unknown>, keys: string[]): string | null {
  for (const key of keys) {
    const value = source[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return null;
}

function readBoolean(source: Record<string, unknown>, keys: string[]): boolean | null {
  for (const key of keys) {
    const value = source[key];
    if (typeof value === "boolean") return value;
  }
  return null;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : null;
}

// ---------------------------------------------------------------------------
// Preflight row derivation
// ---------------------------------------------------------------------------
function buildPreflightRows(pf: UiAutolayoutPreflightData): PreflightRow[] {
  const utilizationTone = (v: number | null): PreflightTone =>
    v == null ? "medium" : v >= 0.55 ? "high" : v >= 0.35 ? "medium" : "low";
  const densityTone = (v: number | null, med: number, hi: number): PreflightTone =>
    v == null ? "medium" : v >= hi ? "high" : v >= med ? "medium" : "low";

  return [
    {
      label: "Utilization",
      value: formatPercent(pf.placementUtilization),
      detail: `${formatArea(pf.componentAreaMm2)} / ${formatArea(pf.boardAreaMm2)}`,
      tone: utilizationTone(pf.placementUtilization),
      scale: clampScale(pf.placementUtilization ?? 0),
    },
    {
      label: "Conn. Density",
      value: formatDensity(pf.connectionDensity),
      detail: `${pf.connectionCount} est. conn / ${pf.netCount} nets`,
      tone: densityTone(pf.connectionDensity, 0.08, 0.14),
      scale: clampScale((pf.connectionDensity ?? 0) / 0.18),
    },
    {
      label: "Pad Density",
      value: formatDensity(pf.padDensity),
      detail: `${pf.padCount} pads`,
      tone: densityTone(pf.padDensity, 0.3, 0.55),
      scale: clampScale((pf.padDensity ?? 0) / 0.8),
    },
    {
      label: "Sides",
      value: formatSidedness(pf.sidedness),
      detail: `${pf.topComponentCount} top / ${pf.bottomComponentCount} bottom`,
      tone: pf.sidedness === "dual-side" ? "low" : (pf.topOnlyUtilization ?? 0) >= 0.55 ? "high" : (pf.topOnlyUtilization ?? 0) >= 0.4 ? "medium" : "low",
      scale: clampScale(pf.sidedness === "dual-side" ? 0.28 : (pf.topOnlyUtilization ?? 0.25)),
    },
    {
      label: "Stackup",
      value: pf.layerCount == null ? `${pf.stackupRisk} risk` : `${pf.layerCount}L ${pf.stackupRisk} risk`,
      detail: pf.boardWidthMm != null && pf.boardHeightMm != null
        ? `${pf.boardWidthMm.toFixed(1)} x ${pf.boardHeightMm.toFixed(1)} mm`
        : "Board size unavailable",
      tone: pf.stackupRisk === "high" ? "high" : pf.stackupRisk === "low" ? "low" : "medium",
      scale: pf.stackupRisk === "high" ? 1 : pf.stackupRisk === "low" ? 0.34 : 0.67,
    },
  ];
}

// ---------------------------------------------------------------------------
// Candidate row derivation
// ---------------------------------------------------------------------------
interface CandidateRowView {
  candidate: UiAutolayoutCandidateData;
  candidateName: string;
  revisionLabel: string;
  isApplied: boolean;
  isChosen: boolean;
  routedPct: number | null;
  viaCount: number | null;
  trackLength: number | null;
  committed: boolean | null;
  detailRows: string[][];
}

function formatTrackLength(um: number): string {
  const mm = um / 1000;
  return mm >= 1000 ? `${(mm / 1000).toFixed(1)}m` : `${mm.toFixed(0)}mm`;
}

function buildCandidateRow(
  candidate: UiAutolayoutCandidateData,
  index: number,
  job: UiAutolayoutJobData,
): CandidateRowView {
  const m = candidate.metadata as Record<string, unknown>;
  const revisionLabel = String(readNumber(m, ["revision_number"]) ?? candidate.candidateId);
  const candidateName = /^\d+$/.test(revisionLabel)
    ? `Rev ${revisionLabel}`
    : candidate.label?.trim() || `Candidate ${index + 1}`;

  const connected = readNumber(m, ["airWiresConnected"]);
  const total = readNumber(m, ["totalAirWires"]);
  const routedPct = connected != null && total != null && total > 0
    ? Math.round(connected / total * 100) : null;
  const viaCount = readNumber(m, ["viaAdded"]);
  const trackLength = readNumber(m, ["totalWireLength"]);
  const committed = readBoolean(m, ["committed"]);
  const netsConnected = readNumber(m, ["netsConnected"]);
  const creditsBurned = readNumber(m, ["creditsBurned"]);

  const detailRows = [
    ...(connected != null && total != null ? [[`Routed`, `${connected}/${total} airwires`]] : []),
    ...(netsConnected != null ? [["Nets", String(netsConnected)]] : []),
    ...(creditsBurned != null ? [["Credits", formatMoney(creditsBurned)]] : []),
    ...(committed != null ? [["Best so far", committed ? "Yes" : "No"]] : []),
  ];

  return {
    candidate, candidateName, revisionLabel,
    isApplied: job.appliedCandidateId === candidate.candidateId,
    isChosen: job.selectedCandidateId === candidate.candidateId,
    routedPct, viaCount, trackLength, committed,
    detailRows,
  };
}

// ---------------------------------------------------------------------------
// Panel
// ---------------------------------------------------------------------------
export function AutolayoutPanel() {
  const { selectedProjectRoot: projectRoot, selectedTarget } =
    WebviewRpcClient.useSubscribe("projectState");
  const data = WebviewRpcClient.useSubscribe("autolayoutData");

  // UI-only local state
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [expandedCandidateId, setExpandedCandidateId] = useState<string | null>(null);
  const [jobType, setJobType] = useState<"Placement" | "Routing">("Placement");
  const [timeoutMinutes, setTimeoutMinutes] = useState(1);
  const [preflightExpanded, setPreflightExpanded] = useState(false);

  const [candidateSort, setCandidateSort] = useState<{ key: CandidateSortKey; direction: "asc" | "desc" }>({ key: "routed", direction: "desc" });
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [copiedProviderId, setCopiedProviderId] = useState(false);

  const selectedTargetName = selectedTarget?.name ?? null;

  // Refresh job list on target change (fast — reads from memory)
  useEffect(() => {
    if (projectRoot && selectedTargetName) {
      rpcClient?.sendAction("getAutolayoutData", { projectRoot });
    }
  }, [projectRoot, selectedTargetName]);

  // Preflight is slow (file parse) — only fetch once per target.
  const [preflightTarget, setPreflightTarget] = useState<string | null>(null);
  useEffect(() => {
    if (projectRoot && selectedTargetName && selectedTargetName !== preflightTarget) {
      setPreflightTarget(selectedTargetName);
      rpcClient?.sendAction("getAutolayoutPreflight", { projectRoot, buildTarget: selectedTargetName });
    }
  }, [projectRoot, selectedTargetName, preflightTarget]);

  // Reset expanded candidate on job change
  useEffect(() => { setExpandedCandidateId(null); }, [selectedJobId]);

  // Clear pending action once jobs list updates (new job appeared) or submitting clears
  useEffect(() => { setPendingAction(null); }, [data.jobs.length, data.submitting]);

  // Derived state
  const filteredJobs = useMemo(() => {
    const pJobs = projectRoot ? data.jobs.filter((j) => j.projectRoot === projectRoot) : data.jobs;
    return selectedTargetName ? pJobs.filter((j) => j.buildTarget === selectedTargetName) : pJobs;
  }, [data.jobs, projectRoot, selectedTargetName]);

  const selectedJob = useMemo(
    () => filteredJobs.find((j) => j.jobId === selectedJobId) ?? null,
    [filteredJobs, selectedJobId],
  );

  const estimatedCost = useMemo(() => estimateJobCost(timeoutMinutes), [timeoutMinutes]);

  const preflightRows = useMemo(
    () => (data.preflight ? buildPreflightRows(data.preflight) : []),
    [data.preflight],
  );

  const candidateRows = useMemo(() => {
    if (!selectedJob) return [];
    const rows = selectedJob.candidates.map((c, i) => buildCandidateRow(c, i, selectedJob));
    const dir = candidateSort.direction === "asc" ? 1 : -1;
    const cmp = (a: number | null, b: number | null) => {
      if (a == null && b == null) return 0;
      if (a == null) return 1;
      if (b == null) return -1;
      return (a - b) * dir;
    };
    return [...rows].sort((a, b) => {
      switch (candidateSort.key) {
        case "routed": return cmp(a.routedPct, b.routedPct);
        case "vias": return cmp(a.viaCount, b.viaCount);
        case "length": return cmp(a.trackLength, b.trackLength);
        default: return a.candidateName.localeCompare(b.candidateName) * dir;
      }
    });
  }, [selectedJob, candidateSort]);

  // Actions
  const handleStart = useCallback(() => {
    if (!projectRoot || !selectedTargetName) return;
    setPendingAction("start");
    rpcClient?.sendAction("submitAutolayoutJob", {
      projectRoot,
      buildTarget: selectedTargetName,
      jobType: jobType,
      timeoutMinutes: timeoutMinutes,
    });
  }, [projectRoot, selectedTargetName, jobType, timeoutMinutes]);

  const handleCancel = useCallback(() => {
    if (!selectedJob) return;
    rpcClient?.sendAction("cancelAutolayoutJob", { jobId: selectedJob.jobId });
  }, [selectedJob]);

  const handleActivateCandidate = useCallback(async (candidateId: string) => {
    setExpandedCandidateId((c) => (c === candidateId ? null : candidateId));
    if (selectedJob) {
      setPendingAction(`diff-${candidateId}`);
      rpcClient?.sendAction("previewAutolayoutCandidate", { jobId: selectedJob.jobId, candidateId });
      await rpcClient?.requestAction("vscode.openPanel", { panelId: "panel-pcb-diff" });
      setPendingAction(null);
    }
  }, [selectedJob]);

  const handleApply = useCallback((candidateId: string) => {
    if (!selectedJob) return;
    setPendingAction(`apply-${candidateId}`);
    rpcClient?.sendAction("applyAutolayoutCandidate", { jobId: selectedJob.jobId, candidateId });
  }, [selectedJob]);

  const toggleSort = useCallback((key: CandidateSortKey) => {
    setCandidateSort((c) =>
      c.key === key
        ? { key, direction: c.direction === "asc" ? "desc" : "asc" }
        : { key, direction: key === "candidate" ? "asc" : "desc" },
    );
  }, []);

  // Empty state
  if (!projectRoot) {
    return (
      <EmptyState
        title="Select a project"
        description="Choose a project to review autolayout jobs."
        icon={<Sparkles size={20} />}
      />
    );
  }

  return (
    <div className="autolayout-panel">
      {/* ── Request bar ── */}
      <div className="autolayout-request-bar">
        <div className="autolayout-inline-field autolayout-inline-field-type autolayout-inline-field-mode" aria-label="Autolayout mode">
          <div className="autolayout-type-select">
            <Select
              items={[...MODE_OPTIONS]}
              value={jobType}
              onValueChange={(v) => { if (v === "Routing" || v === "Placement") setJobType(v); }}
            >
              <SelectTrigger className="autolayout-type-trigger">
                <SelectValue />
              </SelectTrigger>
              <SelectContent className="autolayout-type-content">
                {MODE_OPTIONS.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
        </div>
        <label className="autolayout-inline-field autolayout-inline-field-number">
          <span>
            <span className="autolayout-label-full">Minutes</span>
            <span className="autolayout-label-short">Mins.</span>
          </span>
          <input
            type="number" min={1} max={120} value={timeoutMinutes}
            onChange={(e) => setTimeoutMinutes(Math.max(1, Number(e.target.value) || 1))}
          />
        </label>
        <div className="autolayout-inline-metric autolayout-summary-start">
          <span className="autolayout-info-label">
            <span className="autolayout-label-full">Est. Cost</span>
            <span className="autolayout-label-short">Est. $</span>
          </span>
          <strong>{formatMoney(estimatedCost)}</strong>
        </div>
        <button
          className="autolayout-primary-button autolayout-send-job-button autolayout-inline-action"
          onClick={handleStart}
          disabled={!selectedTargetName || data.submitting}
        >
          {pendingAction === "start" ? <Loader2 size={12} className="spinning" /> : <Sparkles size={12} />}
          <span className="autolayout-send-label">Send</span>
        </button>
      </div>

      {/* ── Preflight ── */}
      {selectedTargetName && (
        <section className="autolayout-preflight-section">
          <button className="autolayout-section-toggle" onClick={() => setPreflightExpanded((v) => !v)}>
            <span className="autolayout-section-toggle-title">
              Pre-Flight
            </span>
            {data.preflightLoading && <Loader2 size={14} className="spinning" />}
            <span style={{ flex: 1 }} />
            <span className={`autolayout-section-chevron${preflightExpanded ? " expanded" : ""}`}>
              <ChevronDown size={14} />
            </span>
          </button>
          {preflightExpanded && (
            data.preflight ? (
              <div className="autolayout-preflight-table">
                {preflightRows.map((row) => (
                  <div key={row.label} className={`autolayout-preflight-row tone-${row.tone}`}>
                    <span>{row.label}</span>
                    <strong>{row.value}</strong>
                    <div className="autolayout-preflight-scale" aria-hidden="true">
                      <span className="autolayout-preflight-scale-fill" style={{ width: `${Math.round(row.scale * 100)}%` }} />
                    </div>
                    <small>{row.detail}</small>
                  </div>
                ))}
              </div>
            ) : (
              <div className="autolayout-preflight-empty">
                {data.preflightError ?? "No preflight metrics available for this target yet."}
              </div>
            )
          )}
        </section>
      )}

      {/* ── Error banner ── */}
      {data.error && (
        <div className="autolayout-error-banner">
          <AlertCircle size={14} />
          <span>{data.error}</span>
        </div>
      )}

      {/* ── Main section ── */}
      <section className="autolayout-section autolayout-main-section">
        {!selectedJob ? (
          <>
            <div className="autolayout-section-header">
              <span>Job Queue</span>
              {data.loading && <Loader2 size={14} className="spinning" />}
            </div>
            <div className="autolayout-job-list">
              {filteredJobs.length === 0 ? (
                <EmptyState
                  title="No autolayout jobs"
                  description="Send a job for the selected target to start building up queue history."
                  icon={<Clock3 size={18} />}
                />
              ) : (
                <div className="autolayout-job-list-grid">
                  <div className="autolayout-job-header">
                    <span>Type</span>
                    <span>Status</span>
                    <span>Started</span>
                    <span>Candidates</span>
                  </div>
                  {filteredJobs.map((job) => (
                    <button
                      key={job.jobId}
                      className={`autolayout-job-row ${job.state}`}
                      onClick={() => setSelectedJobId(job.jobId)}
                    >
                      <span className="autolayout-job-cell-type">{jobTypeShort(job)}</span>
                      <span className={`autolayout-state-chip tone-${jobStatusTone(job)}`} title={job.error || undefined}>{jobStatusLabel(job)}</span>
                      <span className="autolayout-job-cell-time" title={formatTimestamp(job.createdAt)}>{timeAgo(job.createdAt)}</span>
                      <span className="autolayout-job-cell-count">{job.candidates.length}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </>
        ) : (
          <>
            {/* Detail header */}
            <div className="autolayout-section-header">
              <button className="autolayout-back-button" onClick={() => setSelectedJobId(null)}>
                <ArrowLeft size={12} /> Jobs
              </button>
              <span style={{ flex: 1 }} />
              {!selectedJob.appliedCandidateId && !["completed", "failed", "cancelled", "awaiting_selection"].includes(selectedJob.state) && (
                <button className="autolayout-secondary-button" onClick={handleCancel} disabled={data.submitting}>
                  <Square size={12} /> Cancel
                </button>
              )}
            </div>

            {/* Status card */}
            <div className="autolayout-detail">
              <div className="autolayout-detail-card">
                <div className="autolayout-detail-row">
                  <span>Status</span>
                  <span className={`autolayout-state-chip tone-${jobStatusTone(selectedJob)}`}>{jobStatusLabel(selectedJob)}</span>
                </div>
                <div className="autolayout-detail-row">
                  <span>Type</span>
                  <span>{selectedJob.jobType || "—"}</span>
                </div>
                <div className="autolayout-detail-row">
                  <span>Started</span>
                  <span title={formatTimestamp(selectedJob.createdAt)}>{timeAgo(selectedJob.createdAt)}</span>
                </div>
                {selectedJob.providerJobRef && (
                  <div className="autolayout-detail-row">
                    <span>Provider ID</span>
                    <span
                      className="autolayout-monospace autolayout-copyable"
                      title={copiedProviderId ? "Copied!" : selectedJob.providerJobRef}
                      onClick={() => {
                        void navigator.clipboard.writeText(selectedJob.providerJobRef!);
                        setCopiedProviderId(true);
                        setTimeout(() => setCopiedProviderId(false), 2000);
                      }}
                    >
                      {copiedProviderId
                        ? "Copied!"
                        : selectedJob.providerJobRef.length <= 20
                          ? selectedJob.providerJobRef
                          : `${selectedJob.providerJobRef.slice(0, 8)}…${selectedJob.providerJobRef.slice(-6)}`}
                    </span>
                  </div>
                )}
                {selectedJob.progress != null && (
                  <div className="autolayout-detail-row">
                    <span>Progress</span>
                    <span>{Math.round(selectedJob.progress * 100)}%</span>
                  </div>
                )}
                {selectedJob.error && (
                  <div className="autolayout-error-banner">
                    <AlertCircle size={14} />
                    <span>{selectedJob.error}</span>
                  </div>
                )}
              </div>

              {/* Candidate list */}
              <div className="autolayout-candidate-list">
                {selectedJob.candidates.length === 0 ? (
                  <EmptyState
                    title="No candidates yet"
                    description={
                      ["submitting", "queued", "running"].includes(selectedJob.state)
                        ? "Waiting for the provider to accept the job and return candidate layouts."
                        : "Waiting for candidate layouts."
                    }
                    icon={<Clock3 size={18} />}
                  />
                ) : (
                  <div className="autolayout-job-list-grid">
                    <div className="autolayout-candidate-header">
                      {([["candidate", "Candidate"], ["routed", "Routed"], ["vias", "Vias"], ["length", "Length"]] as const).map(([key, label]) => (
                        <button key={key} className={`autolayout-candidate-sort ${candidateSort.key === key ? "active" : ""}`} onClick={() => toggleSort(key)}>
                          <span>{label}</span>
                          <strong>{candidateSort.key === key ? (candidateSort.direction === "asc" ? "↑" : "↓") : "↕"}</strong>
                        </button>
                      ))}
                    </div>
                    {candidateRows.map((row) => {
                      const { candidate, candidateName, isApplied, isChosen, routedPct, viaCount, trackLength, detailRows } = row;
                      const isExpanded = expandedCandidateId === candidate.candidateId;
                      const isApplyPending = pendingAction === `apply-${candidate.candidateId}`;
                      const isDiffPending = pendingAction === `diff-${candidate.candidateId}`;

                      return (
                        <div key={candidate.candidateId}>
                          <button
                            className={`autolayout-candidate-row ${isApplied ? "applied" : ""} ${isExpanded ? "expanded" : ""}`}
                            onClick={() => handleActivateCandidate(candidate.candidateId)}
                          >
                            <span className="autolayout-candidate-col-primary">
                              {candidateName}{isDiffPending && <Loader2 size={12} className="spinning" style={{ marginLeft: 6, verticalAlign: "middle" }} />}
                              {isApplied && <span className="autolayout-applied-badge"><CheckCircle2 size={10} /></span>}
                            </span>
                            <span>{routedPct != null ? `${routedPct}%` : "—"}</span>
                            <span>{viaCount != null ? viaCount : "—"}</span>
                            <span>{trackLength != null ? formatTrackLength(trackLength) : "—"}</span>
                          </button>
                          {isExpanded && (
                            <div className="autolayout-candidate-detail">
                              <div className="autolayout-candidate-detail-panel">
                                {detailRows.length > 0 && (
                                  <div className="autolayout-candidate-detail-block">
                                    <div className="autolayout-candidate-detail-table">
                                      {detailRows.map(([label, value]) => (
                                        <div key={label} className="autolayout-candidate-detail-row">
                                          <span>{label}</span><strong title={value}>{value}</strong>
                                        </div>
                                      ))}
                                    </div>
                                  </div>
                                )}
                              </div>
                              <div className="autolayout-candidate-action-rail">
                                <button
                                  className="autolayout-primary-button"
                                  onClick={(e) => { e.stopPropagation(); handleApply(candidate.candidateId); }}
                                  disabled={data.submitting || isApplied}
                                >
                                  {isApplyPending && <Loader2 size={13} className="spinning" />}
                                  {isApplied ? "Applied" : "Apply"}
                                </button>
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
