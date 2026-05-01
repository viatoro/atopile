import * as vscode from "vscode";
import { RpcClient } from "../../ui/protocol/baseRpcClient";
import type {
  StoreKey,
  UiExtensionSettings,
} from "../../ui/protocol/generated-types";
import { EXTENSION_SESSION_ID, RpcProxy } from "./rpcProxy";
import type { AuthManager, AuthUser } from "./auth";

export class CoreClient extends RpcClient implements vscode.Disposable {
  private _workspaceFolders: string[];
  private readonly _authManager: AuthManager;
  private _resolverInfo:
    | {
        uvPath: string;
        atoBinary: string;
        mode: "local" | "production";
        version: string;
        coreServerPort: number;
      }
    | null = null;
  private _activeFilePath: string | null = null;
  private _waitingForSettingsEcho = false;
  private readonly _workspaceState: vscode.Memento;

  constructor(
    proxy: RpcProxy,
    workspaceFolders: string[],
    workspaceState: vscode.Memento,
    authManager: AuthManager,
  ) {
    super(() => proxy.createTransport(EXTENSION_SESSION_ID));
    this._workspaceFolders = workspaceFolders;
    this._workspaceState = workspaceState;
    this._authManager = authManager;
  }

  start(): void {
    void this.connect();
  }

  setWorkspaceFolders(workspaceFolders: string[]): boolean {
    this._workspaceFolders = workspaceFolders;
    return this.sendDiscoverProjects();
  }

  sendResolverInfo(info: {
    uvPath: string;
    atoBinary: string;
    mode: "local" | "production";
    version: string;
    coreServerPort: number;
  }): boolean {
    this._resolverInfo = info;
    return this.sendAction("resolverInfo", info as Record<string, unknown>);
  }

  sendExtensionSettings(): boolean {
    return this.sendAction(
      "extensionSettings",
      this._getExtensionSettings() as unknown as Record<string, unknown>,
    );
  }

  sendActiveFile(filePath: string | null): boolean {
    this._activeFilePath = filePath;
    return this.sendAction("setActiveFile", { filePath });
  }

  /** Query backend auth status and update the local AuthManager. */
  async queryAuthStatus(): Promise<void> {
    try {
      const result = await this.requestAction("authStatus") as {
        isAuthenticated: boolean;
        user: AuthUser | null;
      } | undefined;
      if (result) {
        this._authManager.setAuthState(
          result.isAuthenticated,
          result.user,
        );
      }
    } catch {
      // Server not ready yet — leave auth state as-is
    }
  }

  dispose(): void {
    this.close();
  }

  private _getExtensionSettings(): UiExtensionSettings {
    const config = vscode.workspace.getConfiguration("atopile");
    return {
      enableChat: config.get<boolean>("enableChat")!,
    };
  }

  private sendDiscoverProjects(): boolean {
    return this.sendAction("discoverProjects", { paths: this._workspaceFolders });
  }

  protected override onConnected(): void {
    this._waitingForSettingsEcho = true;
    this.sendExtensionSettings();
    void this.queryAuthStatus();
    this.subscribe(["extensionSettings", "projectState"]);
    this.sendDiscoverProjects();
    if (this._resolverInfo) {
      this.sendAction("resolverInfo", this._resolverInfo);
    }
    this.sendActiveFile(this._activeFilePath);

    const savedRoot = this._workspaceState.get<string>("atopile.selectedProjectRoot");
    const savedTarget = this._workspaceState.get<Record<string, unknown>>(
      "atopile.selectedTarget",
    );
    if (savedRoot) {
      this.sendAction("selectProject", { projectRoot: savedRoot });
    }
    if (savedTarget) {
      this.sendAction("selectTarget", { target: savedTarget });
    }

    super.onConnected();
  }

  protected override onState(key: StoreKey, data: unknown): void {
    if (key === "projectState") {
      const state = data as {
        selectedProjectRoot?: string | null;
        selectedTarget?: unknown;
      };
      void this._workspaceState.update(
        "atopile.selectedProjectRoot",
        state.selectedProjectRoot ?? undefined,
      );
      void this._workspaceState.update(
        "atopile.selectedTarget",
        state.selectedTarget ?? undefined,
      );
      return;
    }

    if (key !== "extensionSettings") {
      return;
    }

    const settings = data as Partial<UiExtensionSettings>;
    const localSettings = this._getExtensionSettings();
    const matchesLocalSettings = settings.enableChat === localSettings.enableChat;

    if (this._waitingForSettingsEcho) {
      if (matchesLocalSettings) {
        this._waitingForSettingsEcho = false;
      }
      return;
    }
    if (matchesLocalSettings) {
      return;
    }

    const config = vscode.workspace.getConfiguration("atopile");

    if (settings.enableChat !== undefined) {
      void config.update(
        "enableChat",
        settings.enableChat,
        vscode.ConfigurationTarget.Global,
      );
    }
  }
}
