import type { Point2, RenderModel } from "../layout/types";

export type DiffStatus = "unchanged" | "added" | "deleted" | "moved" | "modified";

export interface DiffElementStatus {
    uuid_a: string | null;
    uuid_b: string | null;
    element_type: string;
    status: DiffStatus;
    reference: string | null;
    name: string | null;
    value: string | null;
    net: number | null;
    net_name: string | null;
    position_a: Point2 | null;
    position_b: Point2 | null;
}

export interface DiffConfig {
    position_tolerance?: number;
    angle_tolerance?: number;
}

export interface DiffResult {
    model_a: RenderModel;
    model_b: RenderModel;
    elements: DiffElementStatus[];
    net_names: Record<number, string>;
    summary: Record<string, number>;
}

export type DiffFilterMode = "components" | "traces" | "silkscreen" | "outline";

export interface GitCommitInfo {
    hash: string;
    shortHash: string;
    date: string;
    message: string;
    authorName: string;
}

export interface AutolayoutCandidateRef {
    jobId: string;
    candidateId: string;
    jobType: "Routing" | "Placement";
    label: string;
    score: number | null;
}

export interface VersionSource {
    filePath: string;
    commitHash: string | null;  // null = local working copy
    commitInfo: GitCommitInfo | null;
    autolayoutRef: AutolayoutCandidateRef | null;
}

export interface ViewerLabel {
    fileName: string;
    commitHash?: string;
    commitDate?: string;
    commitMessage?: string;
    authorName?: string;
}

export const STATUS_COLORS: Record<DiffStatus, [number, number, number]> = {
    unchanged: [0.53, 0.53, 0.53],
    added: [0.2, 0.8, 0.3],
    deleted: [0.9, 0.2, 0.2],
    moved: [0.3, 0.5, 0.9],
    modified: [0.9, 0.6, 0.1],
};

export const STATUS_CSS_COLORS: Record<DiffStatus, string> = {
    unchanged: "#7f849c",
    added: "#33cc4c",
    deleted: "#e63333",
    moved: "#4d80e6",
    modified: "#e6991a",
};
