import { existsSync } from "node:fs";
import * as vscode from "vscode";
import { ChannelLogger } from "./logger";
import { RpcProxy } from "./rpcProxy";
import { isWebIdeUi } from "./utils";

export const SIDEBAR_VIEW_ID = "atopile.sidebarView";
export const LOGS_VIEW_ID = "atopile.logsView";
export const AGENT_VIEW_ID = "atopile.agentView";


class HostedPanel implements vscode.Disposable {
  private _panel: vscode.WebviewPanel | undefined;
  private readonly _sessionConnection: vscode.Disposable;

  constructor(
    extensionUri: vscode.Uri,
    proxy: RpcProxy,
    panelId: string,
    column: vscode.ViewColumn,
    onDispose: () => void,
  ) {
    const panel = vscode.window.createWebviewPanel(
      `atopile.${panelId}`,
      `atopile: ${panelId}`,
      column,
      buildPanelOptions(extensionUri),
    );
    this._panel = panel;
    configureWebview(extensionUri, panel.webview, panelId);
    this._sessionConnection = proxy.connectWebviewSession(panelId, panel.webview);
    panel.onDidDispose(() => {
      this._sessionConnection.dispose();
      this._panel = undefined;
      onDispose();
    });
  }

  isOpen(): boolean {
    return this._panel !== undefined;
  }

  /** Pass ``column`` only when first placing the panel; omit to keep
   * the user's current column (avoids moving an already-open panel). */
  reveal(column?: vscode.ViewColumn): void {
    this._panel?.reveal(column);
  }

  setTitle(title: string): void {
    if (this._panel) {
      this._panel.title = title;
    }
  }

  dispose(): void {
    this._panel?.dispose();
  }
}

export class HostedWebviewViewProvider
  implements vscode.WebviewViewProvider, vscode.Disposable
{
  private readonly _extensionUri: vscode.Uri;
  private readonly _proxy: RpcProxy;
  private readonly _panelId: string;
  private _view: vscode.WebviewView | undefined;
  private _sessionConnection: vscode.Disposable | undefined;

  constructor(extensionUri: vscode.Uri, proxy: RpcProxy, panelId: string) {
    this._extensionUri = extensionUri;
    this._proxy = proxy;
    this._panelId = panelId;
  }

  resolveWebviewView(
    webviewView: vscode.WebviewView,
    _context: vscode.WebviewViewResolveContext,
    _token: vscode.CancellationToken,
  ): void {
    this._view = webviewView;
    configureWebview(this._extensionUri, webviewView.webview, this._panelId);
    this._connectSession(webviewView.webview);

    webviewView.onDidDispose(() => {
      this._view = undefined;
      this._disconnectSession();
    });
  }

  reveal(preserveFocus = false): void {
    this._view?.show?.(preserveFocus);
  }

  dispose(): void {
    this._view = undefined;
    this._disconnectSession();
  }

  private _connectSession(webview: vscode.Webview): void {
    this._disconnectSession();
    this._sessionConnection = this._proxy.connectWebviewSession(this._panelId, webview);
  }

  private _disconnectSession(): void {
    this._sessionConnection?.dispose();
    this._sessionConnection = undefined;
  }
}

export class PanelHost implements vscode.Disposable {
  private readonly _extensionUri: vscode.Uri;
  private readonly _proxy: RpcProxy;
  private readonly _logger: ChannelLogger;
  private readonly _panels = new Map<string, HostedPanel>();

  constructor(
    extensionUri: vscode.Uri,
    proxy: RpcProxy,
    logger: ChannelLogger,
  ) {
    this._extensionUri = extensionUri;
    this._proxy = proxy;
    this._logger = logger.scope("PanelHost");
  }

  openPanel(panelId: string): void {
    if (panelId === "panel-logs") {
      throw new Error("panel-logs must be shown in the bottom logs view, not opened as a panel");
    }
    try {
      const existing = this._panels.get(panelId);
      this._logger.debug(`openPanel panelId=${panelId} hasExisting=${Boolean(existing)}`);
      if (existing?.isOpen()) {
        try {
          // Reveal without a column so the panel stays in its current
          // group. Passing a column (e.g. ViewColumn.Beside) would be
          // re-evaluated against the now-active column and move the
          // webview, blanking its splitter and wiping WebGL canvases.
          // The autolayout webview re-requests the layout panel on
          // every mount, so this path is hot.
          existing.reveal();
          this._logger.debug(`revealed existing panelId=${panelId}`);
          return;
        } catch (error) {
          this._logger.warn(
            `disposing stale panelId=${panelId} error=${error instanceof Error ? error.message : String(error)}`,
          );
          existing.dispose();
          this._panels.delete(panelId);
        }
      } else if (existing) {
        this._panels.delete(panelId);
      }

      this._logger.debug(
        `creating panelId=${panelId} targetColumn=${this._panelColumn(panelId)}`,
      );
      const panel = new HostedPanel(
        this._extensionUri,
        this._proxy,
        panelId,
        this._panelColumn(panelId),
        () => {
          this._logger.debug(`onDidDispose panelId=${panelId}`);
          this._panels.delete(panelId);
        },
      );
      this._panels.set(panelId, panel);
      this._logger.debug(`created panelId=${panelId}`);

      // Applied only on first creation so a later manual resize sticks.
      const narrowConfig = this._narrowSplitConfig(panelId);
      if (narrowConfig !== null) {
        void this._applyNarrowSplit(panelId, narrowConfig);
      }
    } catch (error) {
      const detail = error instanceof Error ? error.stack ?? error.message : String(error);
      this._logger.error(`openPanel exception panelId=${panelId}\n${detail}`);
      throw error;
    }
  }

  /** Ratio + companion panel for a narrow Beside split, or null. The
   * companion must also be open before we resize so we can identify
   * which tab group is which. */
  private _narrowSplitConfig(
    panelId: string,
  ): { ratio: number; companionPanelId: string } | null {
    if (panelId === "panel-autolayout") {
      return { ratio: 0.3, companionPanelId: "panel-layout" };
    }
    return null;
  }

  private _findGroupForPanel(panelId: string): vscode.TabGroup | undefined {
    const viewType = `atopile.${panelId}`;
    return vscode.window.tabGroups.all.find((g) =>
      g.tabs.some(
        (t) =>
          t.input instanceof vscode.TabInputWebview && t.input.viewType === viewType,
      ),
    );
  }

  /** Give ``panelId``'s group ``ratio`` and its companion the rest.
   * Reads actual tab-group positions and waits for both panels to be
   * present before applying. */
  private async _applyNarrowSplit(
    panelId: string,
    config: { ratio: number; companionPanelId: string },
  ): Promise<void> {
    const tryApply = async (): Promise<boolean> => {
      const targetGroup = this._findGroupForPanel(panelId);
      const companionGroup = this._findGroupForPanel(config.companionPanelId);
      if (!targetGroup || !companionGroup || targetGroup === companionGroup) {
        return false;
      }
      // Bail if the user has a more complex layout — don't collapse it.
      if (vscode.window.tabGroups.all.length !== 2) {
        return false;
      }
      const isLeftmost = targetGroup.viewColumn === vscode.ViewColumn.One;
      const sizes = isLeftmost
        ? [config.ratio, 1 - config.ratio]
        : [1 - config.ratio, config.ratio];
      try {
        await vscode.commands.executeCommand("vscode.setEditorLayout", {
          orientation: 0,
          groups: [{ size: sizes[0] }, { size: sizes[1] }],
        });
        this._logger.debug(
          `narrow split applied for ${panelId}: targetCol=${targetGroup.viewColumn} sizes=${JSON.stringify(sizes)}`,
        );
        return true;
      } catch (error) {
        this._logger.warn(
          `setEditorLayout failed for ${panelId}: ${error instanceof Error ? error.message : String(error)}`,
        );
        return true; // don't keep retrying on a hard failure
      }
    };

    if (await tryApply()) return;

    // Companion hasn't opened yet; wait for tab-group changes. 5s timeout
    // in case it never appears.
    await new Promise<void>((resolve) => {
      let resolved = false;
      const finish = () => {
        if (resolved) return;
        resolved = true;
        sub.dispose();
        clearTimeout(timer);
        resolve();
      };
      const sub = vscode.window.tabGroups.onDidChangeTabGroups(async () => {
        if (await tryApply()) finish();
      });
      const timer = setTimeout(() => {
        this._logger.debug(
          `narrow split timed out for ${panelId}: companion ${config.companionPanelId} did not open`,
        );
        finish();
      }, 5000);
    });
  }

  setPanelTitle(panelId: string, title: string): void {
    const panel = this._panels.get(panelId);
    if (panel?.isOpen()) {
      panel.setTitle(title);
    }
  }

  dispose(): void {
    for (const panel of this._panels.values()) {
      panel.dispose();
    }
    this._panels.clear();
  }

  private _panelColumn(panelId: string): vscode.ViewColumn {
    if (panelId === "panel-pcb-diff") {
      return vscode.ViewColumn.Active;
    }
    if (panelId === "panel-autolayout") {
      return vscode.ViewColumn.Beside;
    }
    return vscode.ViewColumn.Beside;
  }
}

function configureWebview(
  extensionUri: vscode.Uri,
  webview: vscode.Webview,
  panelId: string,
): void {
  webview.options = {
    enableScripts: true,
    localResourceRoots: getLocalResourceRoots(extensionUri),
  };
  webview.html = getHtml(extensionUri, webview, panelId);
}

function getHtml(
  extensionUri: vscode.Uri,
  webview: vscode.Webview,
  panelId: string,
): string {
  const distUri = vscode.Uri.joinPath(extensionUri, "webview-dist");
  const scriptUri = webview.asWebviewUri(requireWebviewAsset(distUri, panelId, "index.js"));
  const styleUri = webview.asWebviewUri(requireWebviewAsset(distUri, panelId, "index.css"));
  const logoUri = webview.asWebviewUri(vscode.Uri.joinPath(distUri, "logo.png"));
  const logoDarkUri = webview.asWebviewUri(vscode.Uri.joinPath(distUri, "logo-dark.svg"));
  const logoLightUri = webview.asWebviewUri(vscode.Uri.joinPath(distUri, "logo-light.svg"));
  const stepViewerWasmUri = webview.asWebviewUri(vscode.Uri.joinPath(distUri, "occt-import-js.wasm"));
  const glbViewerScriptUri = webview.asWebviewUri(vscode.Uri.joinPath(distUri, "model-viewer.min.js"));
  const csp = webview.cspSource;
  const connectSrc = `${csp} https: blob:`;

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta http-equiv="Content-Security-Policy"
    content="default-src 'none';
      script-src 'unsafe-inline' 'unsafe-eval' 'wasm-unsafe-eval' ${csp} https:;
      style-src 'unsafe-inline' ${csp} https:;
      img-src ${csp} https: data: blob:;
      font-src ${csp} https: data:;
      connect-src ${connectSrc};
      worker-src ${csp} https: blob:;" />
  <link rel="stylesheet" crossorigin="anonymous" href="${styleUri}" />
  <title>atopile</title>
</head>
<body>
  <div id="root"></div>
  <script>
    window.__ATOPILE_PANEL_ID__ = "${panelId}";
    window.__ATOPILE_LOGO_URL__ = "${logoUri}";
    window.__ATOPILE_LOGO_DARK_URL__ = "${logoDarkUri}";
    window.__ATOPILE_LOGO_LIGHT_URL__ = "${logoLightUri}";
    window.__ATOPILE_STEP_VIEWER_WASM_URL__ = "${stepViewerWasmUri}";
    window.__ATOPILE_GLB_VIEWER_SCRIPT_URL__ = "${glbViewerScriptUri}";
    window.__ATOPILE_IS_WEB_IDE__ = ${isWebIdeUi() ? "true" : "false"};
  </script>
  <script type="module" src="${scriptUri}"></script>
</body>
</html>`;
}

function requireWebviewAsset(
  distUri: vscode.Uri,
  panelId: string,
  filename: string,
): vscode.Uri {
  const uri = vscode.Uri.joinPath(distUri, panelId, filename);
  if (!existsSync(uri.fsPath)) {
    throw new Error(
      `Missing webview asset for ${panelId}: ${uri.fsPath}. Run bun run build in src/vscode-atopile.`,
    );
  }
  return uri;
}

function buildPanelOptions(
  extensionUri: vscode.Uri,
): vscode.WebviewPanelOptions & {
  enableScripts: true;
  localResourceRoots: vscode.Uri[];
  portMapping?: { webviewPort: number; extensionHostPort: number }[];
} {
  return {
    enableScripts: true,
    retainContextWhenHidden: true,
    localResourceRoots: getLocalResourceRoots(extensionUri),
  };
}

function getLocalResourceRoots(extensionUri: vscode.Uri): vscode.Uri[] {
  return [
    extensionUri,
    ...(vscode.workspace.workspaceFolders?.map((folder) => folder.uri) ?? []),
  ];
}
