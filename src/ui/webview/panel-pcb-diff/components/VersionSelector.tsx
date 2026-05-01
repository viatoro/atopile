import type { AutolayoutCandidateRef, GitCommitInfo, VersionSource } from "../../common/diff/types";
import type { DiffTransport } from "../../common/diff/client";
import type { UiAutolayoutJobData } from "../../../protocol/generated-types";

interface VersionSelectorProps {
    label: string;
    version: VersionSource;
    gitLog: GitCommitInfo[];
    pcbPath: string;
    diffClient: DiffTransport;
    onVersionChange: (version: VersionSource) => void;
    onBrowseFile?: () => Promise<string | undefined>;
    autolayoutJobs: UiAutolayoutJobData[];
}

const BROWSE_VALUE = "__browse__";

function formatCommitOption(c: GitCommitInfo): string {
    const date = new Date(c.date).toLocaleDateString();
    const msg = c.message.length > 40 ? c.message.slice(0, 40) + "..." : c.message;
    return `${c.shortHash} — ${date} — ${msg}`;
}

function formatCandidateOption(
    job: UiAutolayoutJobData,
    c: { candidateId: string; label: string | null; score: number | null },
): string {
    const label = c.label ?? `Candidate ${c.candidateId.slice(0, 8)}`;
    const score = c.score != null ? ` (${c.score.toFixed(1)}%)` : "";
    return `${label}${score}`;
}

function autolayoutValue(jobId: string, candidateId: string): string {
    return `autolayout:${jobId}:${candidateId}`;
}

function parseAutolayoutValue(value: string): { jobId: string; candidateId: string } | null {
    if (!value.startsWith("autolayout:")) return null;
    const parts = value.split(":");
    if (parts.length < 3) return null;
    return { jobId: parts[1]!, candidateId: parts.slice(2).join(":") };
}

/** Jobs that have candidates, grouped by job type */
function groupJobsByType(jobs: UiAutolayoutJobData[]): {
    routing: UiAutolayoutJobData[];
    placement: UiAutolayoutJobData[];
} {
    const routing: UiAutolayoutJobData[] = [];
    const placement: UiAutolayoutJobData[] = [];
    for (const job of jobs) {
        if (job.candidates.length === 0) continue;
        if (job.jobType === "Routing") routing.push(job);
        else if (job.jobType === "Placement") placement.push(job);
    }
    // Sort by most recently updated first
    routing.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
    placement.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
    return { routing, placement };
}

export function VersionSelector({
    label,
    version,
    gitLog,
    pcbPath,
    diffClient,
    onVersionChange,
    onBrowseFile,
    autolayoutJobs,
}: VersionSelectorProps) {
    const handleChange = async (e: React.ChangeEvent<HTMLSelectElement>) => {
        const value = e.target.value;

        if (value === "local") {
            onVersionChange({
                filePath: pcbPath,
                commitHash: null,
                commitInfo: null,
                autolayoutRef: null,
            });
            return;
        }

        if (value === BROWSE_VALUE) {
            try {
                const path = await onBrowseFile?.();
                if (path) {
                    onVersionChange({
                        filePath: path,
                        commitHash: null,
                        commitInfo: null,
                        autolayoutRef: null,
                    });
                }
            } catch {
                // User cancelled or browse failed
            }
            return;
        }

        // Check for autolayout candidate
        const alRef = parseAutolayoutValue(value);
        if (alRef) {
            const job = autolayoutJobs.find((j) => j.jobId === alRef.jobId);
            const candidate = job?.candidates.find((c) => c.candidateId === alRef.candidateId);
            if (!job || !candidate) return;

            try {
                const previewPath = await diffClient.getAutolayoutPreviewPath(
                    alRef.jobId,
                    alRef.candidateId,
                );
                const ref: AutolayoutCandidateRef = {
                    jobId: alRef.jobId,
                    candidateId: alRef.candidateId,
                    jobType: job.jobType as "Routing" | "Placement",
                    label: candidate.label ?? `Candidate ${alRef.candidateId.slice(0, 8)}`,
                    score: candidate.score,
                };
                onVersionChange({
                    filePath: previewPath,
                    commitHash: null,
                    commitInfo: null,
                    autolayoutRef: ref,
                });
            } catch {
                // Preview fetch failed — leave version unchanged
            }
            return;
        }

        // It's a commit hash
        const commit = gitLog.find((c) => c.hash === value);
        if (!commit) return;

        try {
            const tempPath = await diffClient.getFileAtCommit(pcbPath, commit.hash);
            onVersionChange({
                filePath: tempPath,
                commitHash: commit.hash,
                commitInfo: commit,
                autolayoutRef: null,
            });
        } catch {
            // getFileAtCommit failed — leave version unchanged
        }
    };

    // Compute selected value for the <select>
    let selectValue: string;
    if (version.autolayoutRef) {
        selectValue = autolayoutValue(version.autolayoutRef.jobId, version.autolayoutRef.candidateId);
    } else {
        selectValue = version.commitHash ?? "local";
    }

    // Filter jobs to those matching current PCB (if layout_path available)
    const relevantJobs = autolayoutJobs.filter((j) => {
        if (!j.layoutPath) return true; // show if no path info
        return pcbPath.endsWith(j.layoutPath.split("/").pop() ?? "");
    });

    const { routing, placement } = groupJobsByType(relevantJobs);

    return (
        <div className="pcb-diff-version-selector">
            <span className="pcb-diff-version-label">{label}</span>
            <select
                className="pcb-diff-version-select"
                value={selectValue}
                onChange={handleChange}
            >
                <option value="local">Local (working copy)</option>
                {routing.length > 0 && (
                    <optgroup label="Autorouting">
                        {routing.flatMap((job) =>
                            [...job.candidates].reverse().map((c) => (
                                <option
                                    key={autolayoutValue(job.jobId, c.candidateId)}
                                    value={autolayoutValue(job.jobId, c.candidateId)}
                                >
                                    {formatCandidateOption(job, c)}
                                </option>
                            )),
                        )}
                    </optgroup>
                )}
                {placement.length > 0 && (
                    <optgroup label="Autoplacement">
                        {placement.flatMap((job) =>
                            [...job.candidates].reverse().map((c) => (
                                <option
                                    key={autolayoutValue(job.jobId, c.candidateId)}
                                    value={autolayoutValue(job.jobId, c.candidateId)}
                                >
                                    {formatCandidateOption(job, c)}
                                </option>
                            )),
                        )}
                    </optgroup>
                )}
                {gitLog.length > 0 && (
                    <optgroup label="Git History">
                        {gitLog.map((c) => (
                            <option key={c.hash} value={c.hash}>
                                {formatCommitOption(c)}
                            </option>
                        ))}
                    </optgroup>
                )}
                {onBrowseFile && <option value={BROWSE_VALUE}>Browse file...</option>}
            </select>
        </div>
    );
}
