// Pure display helpers for the autolayout panel.
// No React, no JSX — trivially unit-testable.
//
// All real derivation (best candidate, routed %, readiness checks)
// lives on the backend. These helpers are presentation-only: collapse
// the wire-level enum into display labels, format numbers for humans,
// filter/sort jobs that already arrived in the store.

import type {
  UiAutolayoutCandidateData,
  UiAutolayoutJobData,
} from "../../protocol/generated-types";

export type JobPhase = "Placement" | "Routing";

export type SectionState = "idle" | "running" | "done" | "failed";

export function timeAgo(value: string): string {
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

export function formatPercent(value: number | null): string {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}

export function formatArea(value: number | null): string {
  return value == null ? "—" : `${value.toFixed(value >= 100 ? 0 : 1)} mm²`;
}

export function formatDensity(value: number | null): string {
  return value == null ? "—" : `${value.toFixed(2)}/mm²`;
}

export function formatSidedness(value: string): string {
  switch (value) {
    case "top-only": return "Top only";
    case "bottom-only": return "Bottom only";
    case "dual-side": return "Dual side";
    default: return "Unknown";
  }
}

// Display state derived by the backend (`displayState` on UiAutolayoutJobData).
// This thin wrapper just narrows the field to SectionState so call sites
// don't carry around a free-form string.
export function jobDisplayState(job: UiAutolayoutJobData | null): SectionState {
  return (job?.displayState as SectionState | undefined) ?? "idle";
}

export function runningLabel(job: UiAutolayoutJobData): string {
  if (job.message) return job.message;
  return "Running...";
}

export function getJobsForPhase(
  jobs: UiAutolayoutJobData[],
  projectRoot: string | null,
  targetName: string | null,
  phase: JobPhase,
): UiAutolayoutJobData[] {
  if (!projectRoot || !targetName) return [];
  return jobs
    .filter((j) => j.projectRoot === projectRoot && j.buildTarget === targetName && j.jobType === phase)
    .sort((a, b) => (a.createdAt > b.createdAt ? -1 : 1));
}

export function getRecommendedCandidate(
  job: UiAutolayoutJobData | null,
): UiAutolayoutCandidateData | null {
  if (!job) return null;
  const id = job.recommendedCandidateId;
  if (!id) return null;
  return job.candidates.find((c) => c.candidateId === id) ?? null;
}

export function getCandidateLabel(c: UiAutolayoutCandidateData, index: number): string {
  const rev = c.metadata?.revision_number;
  if (typeof rev === "number" && Number.isFinite(rev)) return `Rev ${rev}`;
  return c.label?.trim() || `Candidate ${index + 1}`;
}
