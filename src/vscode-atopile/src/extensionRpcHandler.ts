import * as vscode from "vscode";
import * as path from "path";
import { ChannelLogger } from "./logger";
import { CLERK_USER_URL } from "./authConfig";
import type { ResolvedBuildTarget } from "../../ui/protocol/generated-types";
import { AGENT_VIEW_ID } from "./webviewHost";
import { isWebIdeUi } from "./utils";

/**
 * Read-only virtual document provider for agent diff views.
 * Documents use the `ato-diff` URI scheme and are never marked dirty,
 * so VS Code won't prompt to save when closing them.
 */
export const ATO_DIFF_SCHEME = "ato-diff";

export class AtoDiffContentProvider implements vscode.TextDocumentContentProvider {
  private _docs = new Map<string, string>();

  set(uri: vscode.Uri, content: string): void {
    this._docs.set(uri.toString(), content);
  }

  provideTextDocumentContent(uri: vscode.Uri): string {
    return this._docs.get(uri.toString()) ?? "";
  }
}

export type ExtensionRpcMessage = {
  type: "extension_request";
  requestId: string;
  action: string;
  [key: string]: unknown;
};

export type ExtensionRpcResult =
  | {
    ok: true;
    result?: unknown;
  }
  | {
    ok: false;
    error: string;
  };

type TreeType = "power";

export class ExtensionRpcHandler {
  private readonly _openPanel: (panelId: string) => void;
  private readonly _setPanelTitle: (panelId: string, title: string) => void;
  private readonly _showLogsView: () => Promise<void> | void;
  private readonly _pushToSidebar: (key: string, data: unknown) => void;
  private readonly _revealSidebar: () => void;
  private readonly _logger: ChannelLogger;
  private readonly _diffProvider: AtoDiffContentProvider;

  constructor(
    openPanel: (panelId: string) => void,
    setPanelTitle: (panelId: string, title: string) => void,
    showLogsView: () => Promise<void> | void,
    pushToSidebar: (key: string, data: unknown) => void,
    revealSidebar: () => void,
    logger: ChannelLogger,
    diffProvider: AtoDiffContentProvider,
  ) {
    this._openPanel = openPanel;
    this._setPanelTitle = setPanelTitle;
    this._showLogsView = showLogsView;
    this._pushToSidebar = pushToSidebar;
    this._revealSidebar = revealSidebar;
    this._logger = logger.scope("ExtensionRpc");
    this._diffProvider = diffProvider;
  }

  async handle(
    webview: vscode.Webview,
    message: ExtensionRpcMessage,
  ): Promise<ExtensionRpcResult> {
    switch (message.action) {
      case "vscode.openPanel": {
        const panelId = this._requireString(message.panelId, "panelId");
        if (panelId === "panel-logs") {
          return {
            ok: false,
            error: "panel-logs is not a panel; use vscode.showLogsView",
          };
        }
        this._logger.debug(`openPanel requested panelId=${panelId}`);
        try {
          this._openPanel(panelId);
        } catch (error) {
          const detail = error instanceof Error ? error.stack ?? error.message : String(error);
          this._logger.error(`openPanel failed panelId=${panelId}\n${detail}`);
          throw error;
        }
        this._logger.debug(`openPanel completed panelId=${panelId}`);
        return { ok: true };
      }

      case "vscode.setPanelTitle": {
        const panelId = this._requireString(message.panelId, "panelId");
        const title = this._requireString(message.title, "title");
        this._setPanelTitle(panelId, title);
        return { ok: true };
      }

      case "vscode.showLogsView": {
        await this._showLogsView();
        return { ok: true };
      }

      case "vscode.authOpenProfile": {
        await vscode.env.openExternal(vscode.Uri.parse(CLERK_USER_URL));
        return { ok: true, result: CLERK_USER_URL };
      }

      case "vscode.openFile": {
        const filePath = this._requireString(message.path, "path");
        const line = this._optionalNumber(message.line);
        const column = this._optionalNumber(message.column);
        const beside = message.beside === true;
        const document = await vscode.workspace.openTextDocument(vscode.Uri.file(filePath));
        const options: vscode.TextDocumentShowOptions = {};
        if (beside) {
          options.viewColumn = vscode.ViewColumn.Beside;
          options.preserveFocus = true;
        }
        if (line != null) {
          const position = new vscode.Position(Math.max(0, line - 1), Math.max(0, column ?? 0));
          options.selection = new vscode.Range(position, position);
        }
        await vscode.window.showTextDocument(document, options);
        return { ok: true };
      }

      case "vscode.openDiff": {
        const filePath = this._requireString(message.path, "path");
        const beforeContent = typeof message.beforeContent === "string" ? message.beforeContent : "";
        const afterContent = typeof message.afterContent === "string" ? message.afterContent : "";
        const title = typeof message.title === "string" && message.title
          ? message.title
          : `Agent diff: ${path.basename(filePath)}`;

        // Use the original file extension so VS Code applies the correct
        // syntax highlighting (e.g. .ato gets ato language support).
        // Virtual ato-diff: documents are read-only — no save prompt on close.
        const ext = path.extname(filePath);
        const base = path.basename(filePath, ext);
        const ts = Date.now();
        const left = vscode.Uri.parse(`${ATO_DIFF_SCHEME}:before-${base}-${ts}${ext}`);
        const right = vscode.Uri.parse(`${ATO_DIFF_SCHEME}:after-${base}-${ts}${ext}`);
        this._diffProvider.set(left, beforeContent);
        this._diffProvider.set(right, afterContent);
        await vscode.commands.executeCommand("vscode.diff", left, right, title, {
          preview: true,
        });
        return { ok: true };
      }

      case "vscode.browseFolder": {
        const result = await vscode.window.showOpenDialog({
          canSelectFiles: false,
          canSelectFolders: true,
          canSelectMany: false,
          openLabel: "Select folder",
        });
        return {
          ok: true,
          result: result?.[0]?.fsPath,
        };
      }

      case "vscode.browseFile": {
        const filters = message.filters as Record<string, string[]> | undefined;
        const title = typeof message.title === "string" ? message.title : undefined;
        const result = await vscode.window.showOpenDialog({
          canSelectFiles: true,
          canSelectFolders: false,
          canSelectMany: false,
          openLabel: "Select file",
          title,
          filters: filters ?? undefined,
        });
        return {
          ok: true,
          result: result?.[0]?.fsPath,
        };
      }

      case "vscode.revealInOs": {
        const filePath = this._requireString(message.path, "path");
        const uri = vscode.Uri.file(filePath);
        if (isWebIdeUi()) {
          await vscode.commands.executeCommand("revealInExplorer", uri);
        } else {
          await vscode.commands.executeCommand("revealFileInOS", uri);
        }
        return { ok: true };
      }

      case "vscode.openInTerminal": {
        const filePath = this._requireString(message.path, "path");
        const cwd = await this._terminalCwd(vscode.Uri.file(filePath));
        const terminal = vscode.window.createTerminal({
          cwd,
          name: `Terminal: ${path.basename(cwd.fsPath) || cwd.fsPath}`,
        });
        terminal.show();
        return { ok: true };
      }

      case "vscode.resolveThreeDModel": {
        const target = message.target as ResolvedBuildTarget;
        const modelFile = vscode.Uri.file(target.modelPath);
        const exists = await this._pathExists(modelFile);

        return {
          ok: true,
          result: {
            exists,
            modelPath: target.modelPath,
            modelUri: webview.asWebviewUri(modelFile).toString(),
          },
        };
      }

      case "vscode.resolveTreeData": {
        const target = message.target as ResolvedBuildTarget;
        const treeType = this._requireTreeType(message.treeType);
        const treeFile = vscode.Uri.file(
          path.join(
            target.root,
            "build",
            "builds",
            target.name,
            `${target.name}.${treeType}_tree.ato.json`,
          ),
        );
        const exists = await this._pathExists(treeFile);

        return {
          ok: true,
          result: {
            exists,
            treePath: treeFile.fsPath,
            dataUrl: webview.asWebviewUri(treeFile).toString(),
          },
        };
      }

      case "vscode.resolveDataInterfaceTree": {
        const target = message.target as ResolvedBuildTarget;
        const treeFile = vscode.Uri.file(
          path.join(
            target.root,
            "build",
            "builds",
            target.name,
            `${target.name}.data_interface_tree.ato.json`,
          ),
        );
        const exists = await this._pathExists(treeFile);

        return {
          ok: true,
          result: {
            exists,
            treePath: treeFile.fsPath,
            dataUrl: webview.asWebviewUri(treeFile).toString(),
          },
        };
      }

      case "vscode.restartExtensionHost": {
        void vscode.commands.executeCommand("workbench.action.restartExtensionHost");
        return { ok: true };
      }

      case "vscode.openExternal": {
        const url = this._requireString(message.url, "url");
        await vscode.env.openExternal(vscode.Uri.parse(url));
        return { ok: true };
      }

      case "vscode.openInPanel": {
        const url = this._requireString(message.url, "url");
        await vscode.commands.executeCommand("simpleBrowser.show", url, {
          viewColumn: vscode.ViewColumn.Beside,
        });
        return { ok: true };
      }

      case "vscode.revealInExplorer": {
        const filePath = this._requireString(message.path, "path");
        await vscode.commands.executeCommand("revealInExplorer", vscode.Uri.file(filePath));
        return { ok: true };
      }

      case "vscode.revealSidebar": {
        this._revealSidebar();
        return { ok: true };
      }

      case "vscode.revealAgent": {
        await vscode.commands.executeCommand(`${AGENT_VIEW_ID}.open`);
        return { ok: true };
      }

      default:
        return {
          ok: false,
          error: `Unsupported extension action: ${message.action}`,
        };
    }
  }

  private _requireString(value: unknown, name: string): string {
    if (typeof value !== "string" || !value) {
      throw new Error(`Missing required field: ${name}`);
    }
    return value;
  }

  private _optionalNumber(value: unknown): number | undefined {
    return typeof value === "number" && Number.isFinite(value) ? value : undefined;
  }

  private _requireTreeType(value: unknown): TreeType {
    if (value === "power") {
      return value;
    }
    throw new Error("Missing required field: treeType");
  }

  private async _pathExists(uri: vscode.Uri): Promise<boolean> {
    try {
      await vscode.workspace.fs.stat(uri);
      return true;
    } catch {
      return false;
    }
  }

  private async _terminalCwd(uri: vscode.Uri): Promise<vscode.Uri> {
    const stat = await vscode.workspace.fs.stat(uri);
    if (this._isDirectory(stat.type)) {
      return uri;
    }
    return vscode.Uri.file(path.dirname(uri.fsPath));
  }

  private _isDirectory(fileType: vscode.FileType): boolean {
    return (fileType & vscode.FileType.Directory) !== 0;
  }

}
