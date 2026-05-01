import {
  STORE_KEYS as REMOTE_STORE_KEYS,
  createUiStore,
  type UiActionMessage,
  type UiActionResultMessage,
  type UiLogEntry,
  type UiLogLevel,
  type UiStateMessage,
  type UiStore,
  type UiSubscribeMessage,
} from "./generated-types";

export interface UiExtensionErrorState {
  error: string | null;
  traceback: string | null;
}

export interface UiAuthUser {
  id: string;
  name: string;
  imageUrl?: string;
}

export interface UiAuthState {
  isAuthenticated: boolean;
  user: UiAuthUser | null;
}

export type StoreState = UiStore & {
  authState: UiAuthState;
  connected: boolean;
  extensionError: UiExtensionErrorState;
};

export function createStoreState(): StoreState {
  return {
    authState: {
      isAuthenticated: false,
      user: null,
    },
    connected: false,
    extensionError: {
      error: null,
      traceback: null,
    },
    ...createUiStore(),
  };
}

export const STORE_KEYS = ["connected", "extensionError", "authState", ...REMOTE_STORE_KEYS] as const;

export interface AtoYaml {
  paths?: {
    layout?: string;
  };
  builds: Record<
    string,
    {
      entry: string;
      paths?: {
        layout?: string;
      };
    }
  >;
}

// -- Log viewer UI-only helpers --------------------------------------------

export type TimeMode = "delta" | "wall";
export type SourceMode = "source" | "logger";
export type LogConnectionState = "disconnected" | "connecting" | "connected";

export const LEVEL_SHORT: Record<UiLogLevel, string> = {
  DEBUG: "D",
  INFO: "I",
  WARNING: "W",
  ERROR: "E",
  ALERT: "A",
};

export const SOURCE_COLORS = [
  "#cba6f7", "#f38ba8", "#fab387", "#f9e2af",
  "#a6e3a1", "#94e2d5", "#89dceb", "#74c7ec",
  "#89b4fa", "#b4befe", "#f5c2e7", "#eba0ac",
];

export interface TreeNode {
  entry: UiLogEntry;
  depth: number;
  content: string;
  children: TreeNode[];
}

export interface LogTreeGroup {
  type: "standalone" | "tree";
  root: TreeNode;
}

export interface LogErrorContext {
  level: string;
  message: string;
  stage?: string | null;
  sourceFile?: string | null;
  sourceLine?: number | null;
  atoTraceback?: string | null;
  pythonTraceback?: string | null;
  buildId?: string | null;
}

// -- Logical RPC protocol messages -----------------------------------------

export const MSG_TYPE = {
  SUBSCRIBE: "subscribe",
  UNSUBSCRIBE: "unsubscribe",
  STATE: "state",
  ACTION: "action",
  ACTION_RESULT: "action_result",
} as const;

export type RpcMessage =
  | UiSubscribeMessage
  | UiStateMessage
  | UiActionMessage
  | UiActionResultMessage;
