import * as vscode from "vscode";
import WebSocket from "ws";
import type { RpcTransport } from "../../ui/protocol/rpcTransport";
import type {
  ExtensionRpcMessage,
  ExtensionRpcResult,
} from "./extensionRpcHandler";
import {
  ChannelLogger,
  isWebviewLogRecord,
  type WebviewLogRecord,
} from "./logger";

type SessionCallbacks = {
  onOpen?: () => void;
  onClose?: () => void;
  onMessage?: (data: string) => void;
  onExtensionRpc?: (message: ExtensionRpcMessage) => Promise<ExtensionRpcResult>;
};

export const EXTENSION_SESSION_ID = "extension";

type ExtensionErrorState = {
  error: string | null;
  traceback: string | null;
};

export class RpcProxy implements vscode.Disposable {
  private readonly _corePort: number;
  private readonly _logger: ChannelLogger;
  private readonly _webviewLogger: ChannelLogger;
  private readonly _coreLogger: ChannelLogger;
  private readonly _handleExtensionRpc: (
    webview: vscode.Webview,
    message: ExtensionRpcMessage,
  ) => Promise<ExtensionRpcResult>;
  private readonly _registrations = new Set<vscode.Disposable>();
  private readonly _sessions = new Map<string, SessionCallbacks>();
  private readonly _pendingMessages: string[] = [];
  private _extensionError: ExtensionErrorState = {
    error: null,
    traceback: null,
  };
  private readonly _bootstrapState = new Map<string, unknown>();
  private readonly _closingSessions = new Map<string, string>();
  private _disposed = false;
  private _closeRequestCounter = 0;
  private _reconnectDisabled = false;
  private _socket: WebSocket | null = null;
  private _reconnectTimer: NodeJS.Timeout | null = null;

  constructor(
    corePort: number,
    logger: ChannelLogger,
    webviewLogger: ChannelLogger,
    coreLogger: ChannelLogger,
    handleExtensionRpc: (
      webview: vscode.Webview,
      message: ExtensionRpcMessage,
    ) => Promise<ExtensionRpcResult>,
  ) {
    this._corePort = corePort;
    this._logger = logger.scope("RpcProxy");
    this._webviewLogger = webviewLogger;
    this._coreLogger = coreLogger;
    this._handleExtensionRpc = handleExtensionRpc;
  }

  connectWebviewSession(sessionId: string, webview: vscode.Webview): vscode.Disposable {
    let webviewReady = false;
    const openWebviewSession = () => {
      if (!webviewReady || !this._isSocketOpen()) {
        return;
      }
      this._postWebviewMessage(webview, { type: "rpc:open" }, `${sessionId}:open`);
      this._sendExtensionErrorState(sessionId);
      for (const [key, data] of this._bootstrapState) {
        this._sendBootstrapState(sessionId, key, data);
      }
    };

    const sessionDisposable = this._attachSession(sessionId, {
      onOpen: openWebviewSession,
      onClose: () => {
        this._postWebviewMessage(webview, { type: "rpc:close" }, `${sessionId}:close`);
      },
      onMessage: (data) => {
        this._postWebviewMessage(webview, { type: "rpc:recv", data }, `${sessionId}:recv`);
      },
      onExtensionRpc: (message) => this._handleExtensionRpc(webview, message),
    });

    const messageDisposable = webview.onDidReceiveMessage((message) => {
      if (isWebviewLogRecord(message)) {
        this._handleDirectWebviewLog(message);
        return;
      }
      if (message?.type === "extension:openSettings") {
        const query = typeof message.query === "string" ? message.query : "atopile";
        vscode.commands.executeCommand("workbench.action.openSettings", query);
        return;
      }
      if (message?.type !== "rpc:send" || typeof message.data !== "string") {
        return;
      }
      try {
        const payload = JSON.parse(message.data) as Record<string, unknown>;
        if (payload.type === "action" && payload.action === "webviewReady") {
          webviewReady = true;
          openWebviewSession();
          return;
        }
      } catch {
        this._logger.warn(`Dropping invalid JSON from session ${sessionId}`);
        return;
      }
      this._sendSessionRaw(sessionId, message.data);
    });

    this._connect();
    this._sendExtensionErrorState(sessionId);

    const disposable = new vscode.Disposable(() => {
      messageDisposable.dispose();
      sessionDisposable.dispose();
      this._registrations.delete(disposable);
    });

    this._registrations.add(disposable);
    return disposable;
  }

  createTransport(sessionId: string): RpcTransport {
    return new ProxySessionTransport(this, sessionId);
  }

  dispose(): void {
    this._disposed = true;
    for (const disposable of [...this._registrations]) {
      disposable.dispose();
    }
    this._registrations.clear();
    this._closingSessions.clear();
    this._sessions.clear();
    if (this._reconnectTimer) {
      clearTimeout(this._reconnectTimer);
      this._reconnectTimer = null;
    }
    if (this._socket) {
      this._socket.removeAllListeners();
      this._socket.close();
      this._socket = null;
    }
  }

  attachSession(sessionId: string, callbacks: SessionCallbacks): vscode.Disposable {
    return this._attachSession(sessionId, callbacks);
  }

  sendSessionPayload(sessionId: string, payload: Record<string, unknown>): boolean {
    return this._sendSerialized({
      ...payload,
      sessionId,
    });
  }

  setExtensionError(error: string | null, traceback: string | null): void {
    this._extensionError = { error, traceback };
    if (error !== null) {
      this._reconnectDisabled = true;
      if (this._reconnectTimer) {
        clearTimeout(this._reconnectTimer);
        this._reconnectTimer = null;
      }
    } else {
      this._reconnectDisabled = false;
    }
    for (const sessionId of this._sessions.keys()) {
      this._sendExtensionErrorState(sessionId);
    }
  }

  setBootstrapState(key: string, data: unknown): void {
    this._bootstrapState.set(key, data);
    for (const sessionId of this._sessions.keys()) {
      this._sendBootstrapState(sessionId, key, data);
    }
  }

  private _attachSession(sessionId: string, callbacks: SessionCallbacks): vscode.Disposable {
    const existing = this._sessions.get(sessionId);
    if (existing && existing !== callbacks) {
      try {
        existing.onClose?.();
      } catch (error) {
        this._logger.warn(
          `Ignoring stale session close failure for ${sessionId}: ${error instanceof Error ? error.message : String(error)}`,
        );
      }
    }
    this._closingSessions.delete(sessionId);
    this._sessions.set(sessionId, callbacks);
    this._connect();
    if (this._isSocketOpen()) {
      callbacks.onOpen?.();
    }

    return new vscode.Disposable(() => {
      const current = this._sessions.get(sessionId);
      if (current !== callbacks) {
        return;
      }
      callbacks.onClose?.();
      if (!this._requestSessionClose(sessionId)) {
        this._sessions.delete(sessionId);
      }
    });
  }

  private _sendSessionRaw(sessionId: string, raw: string): void {
    try {
      const payload = JSON.parse(raw) as Record<string, unknown>;
      this._sendSerialized({
        ...payload,
        sessionId,
      });
    } catch {
      this._logger.warn(`Dropping invalid JSON from session ${sessionId}`);
    }
  }

  private _sendSerialized(payload: Record<string, unknown>): boolean {
    const raw = JSON.stringify(payload);
    if (!this._isSocketOpen()) {
      this._pendingMessages.push(raw);
      this._connect();
      return true;
    }

    this._socket!.send(raw);
    return true;
  }

  private _connect(): void {
    if (this._disposed || this._reconnectDisabled) {
      return;
    }
    if (this._socket && this._socket.readyState !== WebSocket.CLOSED) {
      return;
    }
    if (this._reconnectTimer) {
      return;
    }

    const socket = new WebSocket(`ws://localhost:${this._corePort}/atopile-ui`);
    this._socket = socket;

    socket.on("open", () => {
      for (const callbacks of this._sessions.values()) {
        callbacks.onOpen?.();
      }
      this._flushPending();
    });

    socket.on("message", (data) => {
      void this._handleSocketMessage(data.toString());
    });

    socket.on("close", () => {
      if (this._socket === socket) {
        this._socket = null;
      }
      this._closingSessions.clear();
      for (const callbacks of this._sessions.values()) {
        callbacks.onClose?.();
      }
      this._scheduleReconnect();
    });

    socket.on("error", (error) => {
      this._logger.error(error.message || (error as NodeJS.ErrnoException).code || "Unknown error");
    });
  }

  private _flushPending(): void {
    if (!this._isSocketOpen()) {
      return;
    }
    while (this._pendingMessages.length > 0) {
      this._socket!.send(this._pendingMessages.shift()!);
    }
  }

  private _scheduleReconnect(): void {
    if (this._disposed || this._reconnectDisabled || this._reconnectTimer) {
      return;
    }
    this._reconnectTimer = setTimeout(() => {
      this._reconnectTimer = null;
      this._connect();
    }, 1000);
  }

  private async _handleSocketMessage(raw: string): Promise<void> {
    let message: Record<string, unknown>;
    try {
      message = JSON.parse(raw) as Record<string, unknown>;
    } catch {
      this._logger.warn("Dropping invalid backend JSON");
      return;
    }

    const sessionId =
      typeof message.sessionId === "string" && message.sessionId
        ? message.sessionId
        : EXTENSION_SESSION_ID;
    const callbacks = this._sessions.get(sessionId);
    if (!callbacks) {
      this._logger.warn(`No registered session for ${sessionId}`);
      return;
    }

    if (this._shouldCompleteSessionClose(sessionId, message)) {
      return;
    }

    await this._dispatchSessionMessage(sessionId, callbacks, message);
  }

  private async _dispatchSessionMessage(
    sessionId: string,
    callbacks: SessionCallbacks,
    message: Record<string, unknown>,
  ): Promise<void> {
    const payload = { ...message };

    if (payload.type === "extension_request") {
      await this._handleBackendExtensionRpc(sessionId, callbacks, payload);
      return;
    }

    delete payload.sessionId;
    callbacks.onMessage?.(JSON.stringify(payload));
  }

  private _requestSessionClose(sessionId: string): boolean {
    if (!this._isSocketOpen()) {
      this._closingSessions.delete(sessionId);
      return false;
    }

    const requestId = `rpc-close-${this._closeRequestCounter++}`;
    this._closingSessions.set(sessionId, requestId);
    this._sessions.set(sessionId, {});
    const ok = this.sendSessionPayload(sessionId, {
      type: "action",
      action: "closeSession",
      requestId,
    });
    if (!ok) {
      this._closingSessions.delete(sessionId);
      return false;
    }
    return true;
  }

  private _shouldCompleteSessionClose(
    sessionId: string,
    message: Record<string, unknown>,
  ): boolean {
    const requestId = this._closingSessions.get(sessionId);
    if (!requestId) {
      return false;
    }
    if (
      message.type !== "action_result"
      || message.action !== "closeSession"
      || message.requestId !== requestId
    ) {
      return false;
    }
    this._closingSessions.delete(sessionId);
    if (this._sessions.get(sessionId)?.onMessage === undefined) {
      this._sessions.delete(sessionId);
    }
    return true;
  }

  private async _handleBackendExtensionRpc(
    sessionId: string,
    callbacks: SessionCallbacks,
    message: Record<string, unknown>,
  ): Promise<void> {
    if (!callbacks.onExtensionRpc) {
      this._logger.warn(`Session ${sessionId} cannot handle extension_request`);
      return;
    }

    const request = message as ExtensionRpcMessage;
    if (typeof request.requestId !== "string" || !request.requestId) {
      this._logger.warn("Dropping extension_request without requestId");
      return;
    }

    this._logger.debug(
      `extension_request session=${sessionId} action=${request.action} requestId=${request.requestId}`,
    );

    let response: ExtensionRpcResult;
    try {
      response = await callbacks.onExtensionRpc(request);
    } catch (error) {
      response = {
        ok: false,
        error: error instanceof Error ? error.message : String(error),
      };
    }

    this._logger.debug(
      `extension_response session=${sessionId} action=${request.action} requestId=${request.requestId} ok=${response.ok}`,
    );

    this.sendSessionPayload(sessionId, {
      type: "extension_response",
      requestId: request.requestId,
      action: request.action,
      ...response,
    });
  }

  private _isSocketOpen(): boolean {
    return this._socket?.readyState === WebSocket.OPEN;
  }

  private _sendExtensionErrorState(sessionId: string): void {
    this._sessions.get(sessionId)?.onMessage?.(
      JSON.stringify({
        type: "state",
        key: "extensionError",
        data: this._extensionError,
      }),
    );
  }

  private _sendBootstrapState(sessionId: string, key: string, data: unknown): void {
    this._sessions.get(sessionId)?.onMessage?.(
      JSON.stringify({ type: "state", key, data }),
    );
  }

  private _postWebviewMessage(
    webview: vscode.Webview,
    message: unknown,
    context: string,
  ): void {
    try {
      const result = webview.postMessage(message);
      void result.then(
        undefined,
        (error) => {
          this._logger.warn(
            `Ignoring webview postMessage failure for ${context}: ${error instanceof Error ? error.message : String(error)}`,
          );
        },
      );
    } catch (error) {
      this._logger.warn(
        `Ignoring webview postMessage failure for ${context}: ${error instanceof Error ? error.message : String(error)}`,
      );
    }
  }

  private _handleDirectWebviewLog(record: WebviewLogRecord): void {
    const logger = record.scope
      ? this._webviewLogger.scope(record.panelId).scope(record.scope)
      : this._webviewLogger.scope(record.panelId);
    logger.log(record.level, record.text);
  }
}

class ProxySessionTransport implements RpcTransport {
  onMessage: ((data: string) => void) | null = null;
  onOpen: (() => void) | null = null;
  onClose: (() => void) | null = null;

  private readonly _proxy: RpcProxy;
  private readonly _sessionId: string;
  private _disposable: vscode.Disposable | null = null;

  constructor(proxy: RpcProxy, sessionId: string) {
    this._proxy = proxy;
    this._sessionId = sessionId;
  }

  connect(): void {
    if (this._disposable) {
      return;
    }
    this._disposable = this._proxy.attachSession(this._sessionId, {
      onOpen: () => this.onOpen?.(),
      onClose: () => this.onClose?.(),
      onMessage: (data) => this.onMessage?.(data),
    });
  }

  send(data: string): void {
    const ok = this._proxy.sendSessionPayload(this._sessionId, JSON.parse(data) as Record<string, unknown>);
    if (!ok) {
      throw new Error("Transport is not connected");
    }
  }

  close(): void {
    this._disposable?.dispose();
    this._disposable = null;
  }
}
