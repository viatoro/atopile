import { useCallback, useSyncExternalStore } from "react";
import { RpcClient } from "../../protocol/baseRpcClient";
import type { StoreKey } from "../../protocol/generated-types";
import { createStoreState, type StoreState } from "../../protocol/types";
import { PostMessageTransport } from "./postMessageTransport";

const DEFAULT_STORE_STATE = createStoreState();

function isRemoteStoreKey(key: keyof StoreState): key is StoreKey {
  return key !== "connected" && key !== "extensionError";
}

export class WebviewRpcClient extends RpcClient {
  protected _state = createStoreState();
  protected _listeners = new Map<keyof StoreState, Set<() => void>>();

  constructor(createTransport?: () => import("../../protocol/rpcTransport").RpcTransport) {
    super(createTransport ?? (() => new PostMessageTransport()));
  }

  get<K extends keyof StoreState>(key: K): StoreState[K] {
    return this._state[key];
  }

  observe<K extends keyof StoreState>(key: K, listener: () => void): () => void {
    this._addListener(key, listener);
    if (isRemoteStoreKey(key)) {
      this.subscribe([key]);
    }
    return () => {
      this._removeListener(key, listener);
      if (isRemoteStoreKey(key)) {
        this.unsubscribe([key]);
      }
    };
  }

  static useSubscribe<K extends keyof StoreState>(key: K): StoreState[K] {
    const subscribe = useCallback(
      (listener: () => void) => rpcClient?.observe(key, listener) ?? (() => {}),
      [key],
    );
    const getSnapshot = useCallback(
      () => rpcClient?.get(key) ?? DEFAULT_STORE_STATE[key],
      [key],
    );
    const getServerSnapshot = useCallback(() => DEFAULT_STORE_STATE[key], [key]);
    return useSyncExternalStore(
      subscribe,
      getSnapshot,
      getServerSnapshot,
    );
  }

  protected _notify(key: keyof StoreState): void {
    for (const listener of this._listeners.get(key) ?? []) {
      listener();
    }
  }

  private _addListener(key: keyof StoreState, listener: () => void): void {
    const listeners = this._listeners.get(key);
    if (listeners) {
      listeners.add(listener);
      return;
    }
    this._listeners.set(key, new Set([listener]));
  }

  private _removeListener(key: keyof StoreState, listener: () => void): void {
    const listeners = this._listeners.get(key);
    if (!listeners) {
      return;
    }
    listeners.delete(listener);
    if (listeners.size === 0) {
      this._listeners.delete(key);
    }
  }

  private _setState(key: keyof StoreState, data: unknown): void {
    this._state = { ...this._state, [key]: data } as StoreState;
    this._notify(key);
  }

  protected override onConnected(): void {
    this._setState("connected", true);
    super.onConnected();
  }

  protected override onDisconnected(): void {
    this._setState("connected", false);
    super.onDisconnected();
  }

  protected override onState(key: StoreKey, data: unknown): void {
    this._setState(key as keyof StoreState, data);
  }
}

export let rpcClient: WebviewRpcClient | null = null;

export function connectWebview(
  createTransport?: () => import("../../protocol/rpcTransport").RpcTransport,
): void {
  rpcClient = new WebviewRpcClient(createTransport);
  void rpcClient.connect();
  rpcClient.sendAction("webviewReady");
}
