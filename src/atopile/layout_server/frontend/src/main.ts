import "../../../../ui/webview/common/layout/layout-shell.css";
import { RpcClient } from "../../../../ui/protocol/baseRpcClient";
import { WebSocketTransport } from "../../../../ui/protocol/webSocketTransport";
import type { SocketLike } from "../../../../ui/protocol/rpcTransport";
import {
    ensureLayoutViewerShell,
    mountLayoutViewer,
    RpcLayoutClient,
} from "../../../../ui/webview/common/layout";

class StandaloneLayoutRpcBridge {
    private readonly rawListeners = new Set<(data: string) => void>();
    private readonly connectionListeners = new Set<(connected: boolean) => void>();

    constructor(private readonly client: RpcClient) {
        this.client.onConnected = () => {
            for (const listener of this.connectionListeners) {
                listener(true);
            }
        };
        this.client.onDisconnected = () => {
            for (const listener of this.connectionListeners) {
                listener(false);
            }
        };
        this.client.onRawMessage = (data) => {
            for (const listener of this.rawListeners) {
                listener(data);
            }
        };
    }

    connect(): Promise<void> {
        return this.client.connect();
    }

    disconnect(): void {
        this.client.close();
    }

    isConnected(): boolean {
        return this.client.isConnected;
    }

    requestAction<T>(action: string, payload?: Record<string, unknown>): Promise<T> {
        return this.client.requestAction<T>(action, payload);
    }

    sendAction(action: string, payload?: Record<string, unknown>): boolean {
        return this.client.sendAction(action, payload);
    }

    addRawListener(listener: (data: string) => void): void {
        this.rawListeners.add(listener);
    }

    removeRawListener(listener: (data: string) => void): void {
        this.rawListeners.delete(listener);
    }

    addConnectionListener(listener: (connected: boolean) => void): void {
        this.connectionListeners.add(listener);
    }

    removeConnectionListener(listener: (connected: boolean) => void): void {
        this.connectionListeners.delete(listener);
    }
}

const host = document.getElementById("layout-viewer-root");
if (!(host instanceof HTMLElement)) {
    throw new Error("Layout viewer host #layout-viewer-root not found");
}

const w = window as Record<string, string | undefined> & {
    __layoutEditor?: unknown;
    __layoutRpcClient?: RpcLayoutClient;
};
const rpcPath = w.__LAYOUT_RPC_PATH__ || "/ws";
const rpcUrl = new URL(rpcPath, window.location.origin).toString().replace(/^http/, "ws");
const bridge = new StandaloneLayoutRpcBridge(new RpcClient(
    () => new WebSocketTransport(() => new WebSocket(rpcUrl) as unknown as SocketLike),
));
const layoutClient = new RpcLayoutClient(bridge);
const shell = ensureLayoutViewerShell(host);
w.__layoutRpcClient = layoutClient;

await bridge.connect();

const viewer = mountLayoutViewer({
    canvas: shell.canvas,
    client: layoutClient,
    initialLoadingEl: shell.initialLoadingEl,
    layerPanelEl: shell.layerPanelEl,
    statusEl: shell.statusEl,
    coordsEl: shell.coordsEl,
    busyEl: shell.busyEl,
    fpsEl: shell.fpsEl,
    helpEl: shell.helpEl,
});

window.addEventListener("beforeunload", () => {
    bridge.disconnect();
});

w.__layoutEditor = viewer.editor;
