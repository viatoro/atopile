import type { RpcClient } from "../../../protocol/baseRpcClient";
import type { DiffConfig, DiffResult, GitCommitInfo } from "./types";

export interface DiffTransport {
    computeDiff(pathA: string, pathB: string, config?: DiffConfig, force?: boolean): Promise<DiffResult>;
    getDiffResult(): Promise<DiffResult>;
    getGitLog(filePath: string): Promise<GitCommitInfo[]>;
    getFileAtCommit(filePath: string, commitHash: string): Promise<string>;
    getAutolayoutPreviewPath(jobId: string, candidateId: string): Promise<string>;
}

export type DiffRpcPeer = Pick<RpcClient, "requestAction">;

export class RpcDiffClient implements DiffTransport {
    constructor(private readonly rpcClient: DiffRpcPeer) {}

    async computeDiff(
        pathA: string,
        pathB: string,
        config?: DiffConfig,
        force?: boolean,
    ): Promise<DiffResult> {
        return await this.rpcClient.requestAction<DiffResult>("computePcbDiff", {
            pathA,
            pathB,
            config,
            force: force ?? false,
        });
    }

    async getDiffResult(): Promise<DiffResult> {
        return await this.rpcClient.requestAction<DiffResult>("getDiffResult");
    }

    async getGitLog(filePath: string): Promise<GitCommitInfo[]> {
        const result = await this.rpcClient.requestAction<{ commits: GitCommitInfo[] }>("getGitLog", {
            filePath,
        });
        return result.commits;
    }

    async getFileAtCommit(filePath: string, commitHash: string): Promise<string> {
        const result = await this.rpcClient.requestAction<{ tempPath: string }>("getFileAtCommit", {
            filePath,
            commitHash,
        });
        return result.tempPath;
    }

    async getAutolayoutPreviewPath(jobId: string, candidateId: string): Promise<string> {
        const result = await this.rpcClient.requestAction<{ previewPath: string }>(
            "getAutolayoutPreviewPath",
            { jobId, candidateId },
        );
        return result.previewPath;
    }
}

export class StaticDiffClient implements DiffTransport {
    constructor(private readonly result: DiffResult) {}

    async computeDiff(): Promise<DiffResult> {
        return this.result;
    }

    async getDiffResult(): Promise<DiffResult> {
        return this.result;
    }

    async getGitLog(): Promise<GitCommitInfo[]> {
        return [];
    }

    async getFileAtCommit(): Promise<string> {
        return "";
    }

    async getAutolayoutPreviewPath(): Promise<string> {
        return "";
    }
}
