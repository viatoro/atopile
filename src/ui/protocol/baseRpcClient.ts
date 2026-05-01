import type { StoreKey } from "./generated-types";
import { MSG_TYPE, type RpcMessage } from "./types";
import type { RpcTransport } from "./rpcTransport";

class ReconnectScheduler {
  private _delay: number;
  private readonly _initialDelay: number;
  private readonly _maxDelay: number;
  private _timer: ReturnType<typeof setTimeout> | null = null;
  private _stopped = true;

  constructor(initialDelay = 1000, maxDelay = 10000) {
    this._delay = initialDelay;
    this._initialDelay = initialDelay;
    this._maxDelay = maxDelay;
  }

  resetDelay(): void {
    this._delay = this._initialDelay;
  }

  schedule(fn: () => void): void {
    if (this._stopped) {
      return;
    }

    this._timer = setTimeout(() => {
      this._timer = null;
      fn();
    }, this._delay);
    this._delay = Math.min(this._delay * 2, this._maxDelay);
  }

  start(): void {
    this._stopped = false;
  }

  stop(): void {
    this._stopped = true;
    if (this._timer) {
      clearTimeout(this._timer);
      this._timer = null;
    }
  }
}

export function parseMessage(data: unknown): RpcMessage | null {
  try {
    const str = typeof data === "string" ? data : String(data);
    const msg = JSON.parse(str);
    if (
      msg?.type === MSG_TYPE.SUBSCRIBE ||
      msg?.type === MSG_TYPE.STATE ||
      msg?.type === MSG_TYPE.ACTION ||
      msg?.type === MSG_TYPE.ACTION_RESULT
    ) {
      return msg as RpcMessage;
    }
    return null;
  } catch {
    return null;
  }
}

export interface RpcClientOptions {
  reconnect?: boolean;
}

export type RawMessageListener = (data: string) => void;
export type ConnectionListener = (connected: boolean) => void;

export class RpcClient {
  private readonly _createTransport: () => RpcTransport;
  private readonly _reconnect: ReconnectScheduler | null;
  // The client owns the authoritative desired subscription set and reconciles it
  // against what the server has acknowledged for the current transport session.
  private readonly _subscriptionRefs = new Map<StoreKey, number>();
  private readonly _desiredSubscriptions = new Set<StoreKey>();
  private readonly _remoteSubscriptions = new Set<StoreKey>();
  private readonly _pendingRequests = new Map<
    string,
    {
      action: string;
      resolve: (value: any) => void;
      reject: (error: Error) => void;
    }
  >();
  private readonly _rawListeners = new Set<RawMessageListener>();
  private readonly _connectionListeners = new Set<ConnectionListener>();
  private _transport: RpcTransport | null = null;
  private _connected = false;
  private _requestCounter = 0;

  constructor(createTransport: () => RpcTransport, opts?: RpcClientOptions) {
    this._createTransport = createTransport;
    this._reconnect = opts?.reconnect === false ? null : new ReconnectScheduler();
  }

  get isConnected(): boolean {
    return this._connected;
  }

  addRawListener(listener: RawMessageListener): void {
    this._rawListeners.add(listener);
  }

  removeRawListener(listener: RawMessageListener): void {
    this._rawListeners.delete(listener);
  }

  addConnectionListener(listener: ConnectionListener): void {
    this._connectionListeners.add(listener);
  }

  removeConnectionListener(listener: ConnectionListener): void {
    this._connectionListeners.delete(listener);
  }

  /** Resolves when the client is connected. Resolves immediately if already connected. */
  waitForConnected(timeoutMs = 30_000): Promise<boolean> {
    if (this._connected) {
      return Promise.resolve(true);
    }
    return new Promise<boolean>((resolve) => {
      const timer = setTimeout(() => {
        this.removeConnectionListener(listener);
        resolve(false);
      }, timeoutMs);

      const listener: ConnectionListener = (connected) => {
        if (connected) {
          clearTimeout(timer);
          this.removeConnectionListener(listener);
          resolve(true);
        }
      };

      this.addConnectionListener(listener);
    });
  }

  /** Resolves when a store state message matching `key` satisfies `predicate`. */
  waitForStoreState<T>(
    key: string,
    predicate: (data: T) => boolean,
    timeoutMs = 30_000,
  ): Promise<T | undefined> {
    return new Promise<T | undefined>((resolve) => {
      const timer = setTimeout(() => {
        this.removeRawListener(listener);
        resolve(undefined);
      }, timeoutMs);

      const listener: RawMessageListener = (data) => {
        const msg = parseMessage(data);
        if (msg?.type === MSG_TYPE.STATE && msg.key === key) {
          const state = msg.data as T;
          if (predicate(state)) {
            clearTimeout(timer);
            this.removeRawListener(listener);
            resolve(state);
          }
        }
      };

      this.addRawListener(listener);
    });
  }

  connect(): Promise<void> {
    this._reconnect?.start();
    return new Promise<void>((resolve, reject) => {
      let settled = false;
      const settle = (fn: () => void) => {
        if (!settled) {
          settled = true;
          fn();
        }
      };

      const open = () => {
        this._closeTransport(this._transport);

        const transport = this._createTransport();
        this._transport = transport;

        transport.onOpen = () => {
          this._setConnected(true);
          this._reconnect?.resetDelay();
          this._remoteSubscriptions.clear();
          this._syncSubscriptions();
          settle(() => resolve());
        };

        transport.onMessage = (data) => {
          this.onRawMessage(data);
          const msg = parseMessage(data);
          if (msg?.type === MSG_TYPE.STATE) {
            this.onState(msg.key, msg.data);
            return;
          }
          if (msg?.type === MSG_TYPE.ACTION_RESULT) {
            this._handleActionResult(msg);
          }
        };

        transport.onClose = () => {
          this._remoteSubscriptions.clear();
          this._closeTransport(transport);
          this._setConnected(false);
          this._reconnect?.schedule(open);
          if (!this._reconnect) {
            settle(() => reject(new Error("RPC transport closed before open")));
          }
        };

        transport.connect();
      };

      open();
    });
  }

  subscribe(keys: StoreKey[]): void {
    for (const key of keys) {
      const nextCount = (this._subscriptionRefs.get(key) ?? 0) + 1;
      this._subscriptionRefs.set(key, nextCount);
      this._desiredSubscriptions.add(key);
    }
    this._syncSubscriptions();
  }

  unsubscribe(keys: StoreKey[]): void {
    for (const key of keys) {
      const count = this._subscriptionRefs.get(key);
      if (!count) {
        continue;
      }
      if (count === 1) {
        this._subscriptionRefs.delete(key);
        this._desiredSubscriptions.delete(key);
        continue;
      }
      this._subscriptionRefs.set(key, count - 1);
    }
    this._syncSubscriptions();
  }

  sendRaw(data: string): boolean {
    if (!this._transport) {
      return false;
    }
    try {
      this._transport.send(data);
      return true;
    } catch {
      return false;
    }
  }

  sendAction(action: string, payload?: Record<string, unknown>): boolean {
    return this.sendRaw(JSON.stringify({ type: MSG_TYPE.ACTION, action, ...payload }));
  }

  requestAction<T>(
    action: string,
    payload?: Record<string, unknown>,
  ): Promise<T> {
    const requestId = `rpc-${this._requestCounter++}`;

    return new Promise<T>((resolve, reject) => {
      this._pendingRequests.set(requestId, {
        action,
        resolve: (value: unknown) => resolve(value as T),
        reject,
      });
      const ok = this.sendRaw(
        JSON.stringify({
          type: MSG_TYPE.ACTION,
          action,
          requestId,
          ...payload,
        }),
      );
      if (!ok) {
        this._pendingRequests.delete(requestId);
        reject(new Error("RPC transport is not available"));
      }
    });
  }

  close(): void {
    this._reconnect?.stop();
    this._remoteSubscriptions.clear();
    this._rejectPendingRequests(new Error("RPC transport closed"));
    this._closeTransport(this._transport);
    this._setConnected(false);
  }

  private _sendSubscribe(keys: string[]): boolean {
    return this.sendRaw(JSON.stringify({ type: MSG_TYPE.SUBSCRIBE, keys }));
  }

  private _sendUnsubscribe(keys: string[]): boolean {
    return this.sendRaw(JSON.stringify({ type: MSG_TYPE.UNSUBSCRIBE, keys }));
  }

  private _syncSubscriptions(): void {
    if (!this._connected) {
      return;
    }

    const addedKeys = [...this._desiredSubscriptions].filter(
      (key) => !this._remoteSubscriptions.has(key),
    );
    if (addedKeys.length > 0 && this._sendSubscribe(addedKeys)) {
      for (const key of addedKeys) {
        this._remoteSubscriptions.add(key);
      }
    }

    const removedKeys = [...this._remoteSubscriptions].filter(
      (key) => !this._desiredSubscriptions.has(key),
    );
    if (removedKeys.length > 0 && this._sendUnsubscribe(removedKeys)) {
      for (const key of removedKeys) {
        this._remoteSubscriptions.delete(key);
      }
    }
  }

  private _handleActionResult(msg: Extract<RpcMessage, { type: typeof MSG_TYPE.ACTION_RESULT }>): void {
    if (!msg.requestId) {
      return;
    }

    const pending = this._pendingRequests.get(msg.requestId);
    if (!pending) {
      return;
    }
    this._pendingRequests.delete(msg.requestId);

    if (msg.ok === false) {
      pending.reject(new Error(msg.error || `${pending.action} failed`));
      return;
    }

    pending.resolve(msg.result as unknown);
  }

  private _rejectPendingRequests(error: Error): void {
    for (const pending of this._pendingRequests.values()) {
      pending.reject(error);
    }
    this._pendingRequests.clear();
  }

  private _setConnected(connected: boolean): void {
    if (this._connected === connected) {
      return;
    }
    this._connected = connected;
    if (connected) {
      this.onConnected();
      return;
    }
    this.onDisconnected();
  }

  private _closeTransport(transport: RpcTransport | null): void {
    if (!transport) {
      return;
    }
    transport.onOpen = null;
    transport.onMessage = null;
    transport.onClose = null;
    transport.close();
    if (this._transport === transport) {
      this._transport = null;
    }
  }

  protected onConnected(): void {
    for (const listener of this._connectionListeners) {
      listener(true);
    }
  }

  protected onDisconnected(): void {
    for (const listener of this._connectionListeners) {
      listener(false);
    }
  }

  protected onState(_key: StoreKey, _data: unknown): void {}

  protected onRawMessage(data: string): void {
    for (const listener of this._rawListeners) {
      listener(data);
    }
  }
}
