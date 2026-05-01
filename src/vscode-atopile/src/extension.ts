import * as fs from "fs";
import * as vscode from "vscode";
import { ProcessManager } from "./processManager";
import { getConfiguredAtoPath, resolveAto } from "./atoResolver";
import type { ResolvedBinary } from "./atoResolver";
import { CoreClient } from "./coreClient";
import { RpcProxy } from "./rpcProxy";
import { findFreePort, waitForPortListening } from "./utils";

import { ExtensionRpcHandler, AtoDiffContentProvider, ATO_DIFF_SCHEME } from "./extensionRpcHandler";
import { ChannelLogger } from "./logger";
import { LspClientManager } from "./lspClientManager";
import { AuthManager } from "./auth";
import * as demo from "./demo";
import {
  AGENT_VIEW_ID,
  HostedWebviewViewProvider,
  LOGS_VIEW_ID,
  PanelHost,
  SIDEBAR_VIEW_ID,
} from "./webviewHost";
import type { ResolvedBuildTarget } from "../../ui/protocol/generated-types";

const CORE_SERVER_READY_MARKER = "ATOPILE_SERVER_READY";
const OPEN_AGENT_ONCE_KEY = "agentViewOpenedOnce";

const panels = [
  { id: "panel-settings", label: "Settings" },
  { id: "panel-developer", label: "Developer" },
  { id: "panel-tree", label: "Trees" },
  // Careful we are matching onto this specific string quite a bit
  { id: "panel-layout", label: "Layout" },
  { id: "panel-pinout", label: "Pinout" },
  { id: "panel-parameters", label: "Parameters" },
  { id: "panel-stackup", label: "Stackup" },
  { id: "panel-3d", label: "3D Model" },
  { id: "panel-manufacture", label: "Manufacture" },
  { id: "panel-autolayout", label: "Autolayout" },
  { id: "panel-welcome", label: "Welcome" },
  { id: "panel-pcb-diff", label: "PCB Diff" },
  { id: "panel-ibom", label: "Interactive BOM" },
];

let coreClient: CoreClient | undefined;
let lspClient: LspClientManager | undefined;

async function showLogsView(logsProvider: HostedWebviewViewProvider): Promise<void> {
  await vscode.commands.executeCommand("workbench.view.extension.atopile-logs");
  logsProvider.reveal();
}

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const extensionHostOutput = vscode.window.createOutputChannel("atopile Extension Host", {
    log: true,
  });
  const webviewOutput = vscode.window.createOutputChannel("atopile Webviews", {
    log: true,
  });
  const coreOutput = vscode.window.createOutputChannel("atopile Core", {
    log: true,
  });
  const lspOutput = vscode.window.createOutputChannel("atopile LSP");
  const extensionLogger = new ChannelLogger(extensionHostOutput);
  const webviewLogger = new ChannelLogger(webviewOutput);
  const coreLogger = new ChannelLogger(coreOutput);
  const lspLogger = new ChannelLogger(lspOutput);
  extensionLogger.info("atopile extension activating");

  const isWebUi = vscode.env.uiKind === vscode.UIKind.Web;
  // In the web-ide playground container, entrypoint.sh pre-starts the core
  // server so the extension can skip the multi-second Python+wheel cold start.
  const playgroundBackendPortEnv = process.env.ATOPILE_BACKEND_PORT;
  const usePreStartedCoreServer =
    process.env.ATO_PLAYGROUND === "1" &&
    !!playgroundBackendPortEnv &&
    Number.isFinite(Number(playgroundBackendPortEnv));
  const coreServerPort = usePreStartedCoreServer
    ? Number(playgroundBackendPortEnv)
    : await findFreePort();
  const getWorkspaceFolders = () =>
    vscode.workspace.workspaceFolders?.map((folder) => folder.uri.fsPath) ?? [];

  const portEnv = {
    ATOPILE_CORE_SERVER_PORT: String(coreServerPort),
    ATO_VSCE_PID: String(process.pid),
  };

  let panelHost!: PanelHost;
  let logsProvider!: HostedWebviewViewProvider;
  const authManager = new AuthManager();
  const diffProvider = new AtoDiffContentProvider();
  context.subscriptions.push(
    vscode.workspace.registerTextDocumentContentProvider(ATO_DIFF_SCHEME, diffProvider),
  );
  const rpcHandler = new ExtensionRpcHandler(
    (panelId) => panelHost.openPanel(panelId),
    (panelId, title) => panelHost.setPanelTitle(panelId, title),
    () => showLogsView(logsProvider),
    (key, data) => proxy.pushToSession("sidebar", key, data),
    () => {
      void vscode.commands.executeCommand("workbench.view.extension.atopile-sidebar");
      sidebarProvider.reveal();
    },
    extensionLogger,
    diffProvider,
  );
  const proxy = new RpcProxy(
    coreServerPort,
    extensionLogger,
    webviewLogger,
    coreLogger,
    (webview, message) => rpcHandler.handle(webview, message),
  );
  proxy.setExtensionError(null, null);
  panelHost = new PanelHost(context.extensionUri, proxy, extensionLogger);
  const sidebarProvider = new HostedWebviewViewProvider(
    context.extensionUri,
    proxy,
    "sidebar",
  );
  logsProvider = new HostedWebviewViewProvider(
    context.extensionUri,
    proxy,
    "panel-logs",
  );
  const agentProvider = new HostedWebviewViewProvider(
    context.extensionUri,
    proxy,
    "panel-agent",
  );
  registerWebviews(context, sidebarProvider, logsProvider, agentProvider);
  registerCommands(context, panelHost, extensionLogger);
  if (hasWorkspace(vscode.workspace) && !context.workspaceState.get<boolean>(OPEN_AGENT_ONCE_KEY)) {
    void (async () => {
      try {
        await vscode.commands.executeCommand(`${AGENT_VIEW_ID}.open`, { preserveFocus: true });
        await context.workspaceState.update(OPEN_AGENT_ONCE_KEY, true);
      } catch (error) {
        extensionLogger.warn(
          `Failed to open agent view on first workspace use: ${error instanceof Error ? error.message : String(error)}`,
        );
      }
    })();
  }
  context.subscriptions.push(
    proxy,
    extensionHostOutput,
    webviewOutput,
    coreOutput,
    lspOutput,
    panelHost,
    sidebarProvider,
    logsProvider,
    agentProvider,
    authManager,
  );

  try {
    const version: string = context.extension.packageJSON.version;
    const resolved = await resolveAto(
      context,
      extensionLogger.scope("AtoResolver"),
      version,
    );

    if (usePreStartedCoreServer) {
      extensionLogger.info(
        `Reusing pre-started core server on port ${coreServerPort} (ATO_PLAYGROUND=1)`,
      );
      // Probe with a short window. If the pre-start hasn't appeared yet, fall
      // back to spawning so the user is never stuck on an indefinite wait.
      const ready = await waitForPortListening(coreServerPort, "127.0.0.1", 2000);
      if (!ready) {
        extensionLogger.warn(
          `Pre-started core server not responding on port ${coreServerPort}; falling back to spawning a fresh instance`,
        );
        const coreServer = await startCoreServer(resolved, portEnv, coreLogger, isWebUi);
        context.subscriptions.push(coreServer);
      }
    } else {
      const coreServer = await startCoreServer(resolved, portEnv, coreLogger, isWebUi);
      context.subscriptions.push(coreServer);
    }

    coreClient = new CoreClient(
      proxy,
      getWorkspaceFolders(),
      context.workspaceState,
      authManager,
    );
    coreClient.start();
    coreClient.sendResolverInfo({
      uvPath: resolved.isLocal ? "" : resolved.command,
      atoBinary: resolved.atoBinary ?? "",
      mode: resolved.isLocal ? "local" : "production",
      version,
      coreServerPort,
    });
    context.subscriptions.push(coreClient);

    lspClient = new LspClientManager(lspLogger, resolved);
    context.subscriptions.push(lspClient);
    void lspClient.start().catch((error: unknown) => {
      const detail = error instanceof Error ? error.message : String(error);
      extensionLogger.error(`LSP failed to start: ${detail}`);
    });

    context.subscriptions.push(
      vscode.workspace.onDidChangeConfiguration((event) => {
        if (event.affectsConfiguration("atopile.ato")) {
          const configuredAto = getConfiguredAtoPath();
          if (configuredAto && !fs.existsSync(configuredAto)) {
            extensionLogger.info(
              "atopile.ato changed but the configured binary does not exist yet; waiting to reload",
            );
            return;
          }

          extensionLogger.info(
            "atopile.ato changed, reloading window to apply the new ato binary",
          );
          void vscode.window.showInformationMessage(
            "Reloading VS Code window to apply the updated atopile binary.",
          );
          void vscode.commands.executeCommand("workbench.action.reloadWindow");
          return;
        }

        if (event.affectsConfiguration("atopile")) {
          coreClient?.sendExtensionSettings();
        }
      }),
      vscode.workspace.onDidChangeWorkspaceFolders(() => {
        coreClient?.setWorkspaceFolders(getWorkspaceFolders());
      }),

      vscode.window.onDidChangeActiveTextEditor((editor) => {
        coreClient?.sendActiveFile(editor?.document.uri.fsPath ?? null);
      }),
    );
    coreClient.sendActiveFile(vscode.window.activeTextEditor?.document.uri.fsPath ?? null);

    demo.activate(context, coreClient, panelHost, extensionLogger);

    extensionLogger.info("atopile extension activated");
  } catch (error) {
    reportGlobalException(extensionLogger, proxy, error);
  }
}

export function deactivate(): void {
  lspClient?.dispose();
  lspClient = undefined;
  coreClient?.dispose();
  coreClient = undefined;
}

function reportGlobalException(
  logger: ChannelLogger,
  proxy: RpcProxy,
  error: unknown,
): void {
  const { summary, traceback } = formatException(error);
  logger.error(`Unhandled exception: ${summary}`);
  if (traceback && traceback !== summary) {
    logger.error(traceback);
  }
  proxy.setExtensionError(summary, traceback);
}

function formatException(error: unknown): {
  summary: string;
  traceback: string | null;
} {
  if (error instanceof Error) {
    const summary =
      error.name && error.name !== "Error"
        ? `${error.name}: ${error.message}`
        : error.message || error.name;
    return {
      summary,
      traceback: error.stack?.trim() || summary,
    };
  }

  const summary = String(error);
  return { summary, traceback: summary };
}

function registerCommands(
  context: vscode.ExtensionContext,
  panelHost: PanelHost,
  logger: ChannelLogger,
): void {
  context.subscriptions.push(
    vscode.commands.registerCommand("atopile.openPanel", async () => {
      const pick = await vscode.window.showQuickPick(
        panels.map((p) => ({ label: p.label, id: p.id })),
        { placeHolder: "Select a panel to open" },
      );
      if (pick) {
        panelHost.openPanel(pick.id);
      }
    }),

    vscode.commands.registerCommand("atopile.startBuild", async () => {
      if (!coreClient) {
        vscode.window.showErrorMessage("atopile core server is not running.");
        return;
      }

      const selectedProjectRoot = context.workspaceState.get<string>("atopile.selectedProjectRoot");
      const selectedTarget = context.workspaceState.get<ResolvedBuildTarget>("atopile.selectedTarget");

      if (!selectedProjectRoot || !selectedTarget) {
        vscode.window.showErrorMessage("Select a project and build target first.");
        return;
      }

      logger.info(
        `startBuild command projectRoot=${selectedProjectRoot} target=${selectedTarget.name}`,
      );

      const started = coreClient.sendAction("startBuild", {
        projectRoot: selectedProjectRoot,
        targets: [selectedTarget],
      });

      if (!started) {
        vscode.window.showErrorMessage("Failed to send build request to the atopile core server.");
      }
    }),

    vscode.commands.registerCommand(
      "atopile.openKicad",
      async ({
        target,
      }: {
        target?: ResolvedBuildTarget;
      } = {}) => {
        if (!target) {
          vscode.window.showErrorMessage("Select a project and target first.");
          return;
        }

        try {
          if (!coreClient) {
            throw new Error("Core server is not running");
          }
          await coreClient.requestAction("openKicad", { target });
        } catch (err) {
          vscode.window.showErrorMessage(
            `Failed to open KiCad: ${err instanceof Error ? err.message : err}`,
          );
        }
      },
    ),

    vscode.commands.registerCommand(
      "atopile.openFile",
      async ({ path }: { path?: string } = {}) => {
        if (!path) {
          return;
        }

        await vscode.window.showTextDocument(vscode.Uri.file(path));
      },
    ),
  );
}

function hasWorkspace(workspace: typeof vscode.workspace): boolean {
  return Boolean(workspace.workspaceFile) || (workspace.workspaceFolders?.length ?? 0) > 0;
}

function registerWebviews(
  context: vscode.ExtensionContext,
  sidebarProvider: HostedWebviewViewProvider,
  logsProvider: HostedWebviewViewProvider,
  agentProvider: HostedWebviewViewProvider,
): void {
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(SIDEBAR_VIEW_ID, sidebarProvider, {
      webviewOptions: { retainContextWhenHidden: true },
    }),

    vscode.window.registerWebviewViewProvider(LOGS_VIEW_ID, logsProvider, {
      webviewOptions: { retainContextWhenHidden: true },
    }),

    vscode.window.registerWebviewViewProvider(AGENT_VIEW_ID, agentProvider, {
      webviewOptions: { retainContextWhenHidden: true },
    }),
  );
}

async function startCoreServer(
  resolved: ResolvedBinary,
  portEnv: Record<string, string>,
  coreLogger: ChannelLogger,
  isWebUi: boolean,
): Promise<ProcessManager> {
  const pm = new ProcessManager(coreLogger, {
    name: "CoreServer",
    command: resolved.command,
    args: [...resolved.prefixArgs, "serve", "core", ...(isWebUi ? ["--force"] : [])],
    readyMarker: CORE_SERVER_READY_MARKER,
    env: { ...resolved.env, ...portEnv },
  });

  try {
    await pm.start();
    return pm;
  } catch (error) {
    coreLogger.output.show(true);
    pm.dispose();
    throw error;
  }
}
