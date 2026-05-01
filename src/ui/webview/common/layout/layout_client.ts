import type { RpcClient } from "../../../protocol/baseRpcClient";
import type { ActionCommand, LayoutWsMessage, RenderModel, StatusResponse } from "./types";

export type UpdateHandler = (message: LayoutWsMessage) => void;
export type LayoutClientLogger = {
    info?(message: string): void;
    warn?(message: string): void;
    error?(message: string): void;
};

export interface LayoutTransport {
    fetchRenderModel(): Promise<RenderModel>;
    executeAction(action: ActionCommand): Promise<StatusResponse>;
    connect(onUpdate: UpdateHandler): void;
    disconnect(): void;
}

export type LayoutRpcPeer = Pick<
    RpcClient,
    | "addRawListener"
    | "removeRawListener"
    | "addConnectionListener"
    | "removeConnectionListener"
    | "requestAction"
    | "sendAction"
    | "isConnected"
>;

export class StaticLayoutClient implements LayoutTransport {
    constructor(private model: RenderModel) {}

    async fetchRenderModel(): Promise<RenderModel> {
        return this.model;
    }

    setRenderModel(model: RenderModel): void {
        this.model = model;
    }

    async executeAction(action: ActionCommand): Promise<StatusResponse> {
        return {
            status: "error",
            code: "read_only",
            message: `Layout preview is read-only; cannot execute ${action.command}.`,
            delta: null,
            action_id: action.client_action_id ?? null,
        };
    }

    connect(_onUpdate: UpdateHandler): void {}

    disconnect(): void {}
}

function isLayoutMessage(message: unknown): message is LayoutWsMessage {
    if (!message || typeof message !== "object") {
        return false;
    }
    const record = message as Record<string, unknown>;
    return record.type === "layout_updated" || record.type === "layout_delta";
}

export class RpcLayoutClient implements LayoutTransport {
    private onUpdate: UpdateHandler | null = null;

    constructor(
        private readonly rpcClient: LayoutRpcPeer,
        private readonly logger?: LayoutClientLogger,
    ) {}

    async fetchRenderModel(): Promise<RenderModel> {
        this.logger?.info?.("Fetching layout render model over RPC");
        return await this.rpcClient.requestAction<RenderModel>("getLayoutRenderModel");
    }

    async executeAction(action: ActionCommand): Promise<StatusResponse> {
        this.logger?.info?.(`Executing layout action over RPC: ${action.command}`);
        return await this.rpcClient.requestAction<StatusResponse>(
            "executeLayoutAction",
            action as unknown as Record<string, unknown>,
        );
    }

    connect(onUpdate: UpdateHandler): void {
        this.onUpdate = onUpdate;
        this.rpcClient.addRawListener(this.handleRawMessage);
        this.rpcClient.addConnectionListener(this.handleConnectionChange);
        this.subscribe();
    }

    disconnect(): void {
        this.rpcClient.removeRawListener(this.handleRawMessage);
        this.rpcClient.removeConnectionListener(this.handleConnectionChange);
        this.unsubscribe();
        this.onUpdate = null;
    }

    private readonly handleConnectionChange = (connected: boolean): void => {
        if (connected) {
            this.subscribe();
        }
    };

    private readonly handleRawMessage = (data: string): void => {
        let message: unknown;
        try {
            message = JSON.parse(data);
        } catch {
            return;
        }
        if (!isLayoutMessage(message) || !this.onUpdate) {
            return;
        }
        this.onUpdate(message);
    };

    private subscribe(): void {
        if (!this.rpcClient.isConnected) {
            return;
        }
        this.logger?.info?.("Subscribing to layout RPC updates");
        this.rpcClient.sendAction("subscribeLayout");
    }

    private unsubscribe(): void {
        if (!this.rpcClient.isConnected) {
            return;
        }
        this.logger?.info?.("Unsubscribing from layout RPC updates");
        this.rpcClient.sendAction("unsubscribeLayout");
    }
}
