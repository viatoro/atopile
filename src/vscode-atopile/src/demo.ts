import * as path from "path";
import * as vscode from "vscode";
import type { CoreClient } from "./coreClient";
import type { PanelHost } from "./webviewHost";
import type { ChannelLogger } from "./logger";
import type { Project } from "../../ui/protocol/generated-types";
import { isWebIdeUi } from "./utils";

async function runDemoMode(
  client: CoreClient,
  panelHost: PanelHost,
  logger: ChannelLogger,
): Promise<void> {
  logger.info("[demo] starting");

  // Focus the atopile sidebar
  vscode.commands.executeCommand("workbench.view.extension.atopile-sidebar");

  // Close editors (e.g. the "Setup VS Code Web" walkthrough tab)
  await vscode.commands.executeCommand("workbench.action.closeAllEditors");

  // Close chat/auxiliary sidebar if present
  try {
    await vscode.commands.executeCommand("workbench.action.closeAuxiliaryBar");
  } catch {
    // not available in all VS Code flavours
  }

  // Show welcome tab immediately
  panelHost.openPanel("panel-welcome");
  logger.info("[demo] welcome tab opened");

  // Wait for the core client to connect
  const connected = await client.waitForConnected();
  if (!connected) {
    logger.info("[demo] timed out waiting for core client connection");
    return;
  }
  logger.info("[demo] core client connected");

  // Subscribe to projects so we get notified when discovery finishes
  client.subscribe(["projects"]);

  // Wait for at least one project with targets to be discovered
  const projects = await client.waitForStoreState<Project[]>(
    "projects",
    (ps) => ps.length > 0 && ps.some((p) => p.targets.length > 0),
    30_000,
  );
  if (!projects || projects.length === 0) {
    logger.info("[demo] timed out waiting for projects");
    return;
  }
  logger.info(`[demo] discovered ${projects.length} project(s)`);

  // Auto-select the first project with targets
  const project = projects.find((p) => p.targets.length > 0);
  if (!project) {
    logger.info("[demo] no project with targets found");
    return;
  }
  const target = project.targets[0];
  logger.info(
    `[demo] auto-selecting project=${project.root} target=${target.entry}`,
  );

  client.sendAction("selectProject", { projectRoot: project.root });
  client.sendAction("selectTarget", {
    target: {
      name: target.name,
      entry: target.entry,
      root: target.root,
    } as Record<string, unknown>,
  });

  // Wait briefly for selection to propagate through the store
  await new Promise((r) => setTimeout(r, 500));

  // Open entry .ato file in a vertical split below the welcome tab
  try {
    const filePart = target.entry.split(":")[0];
    const entryPath = path.join(target.root, filePart);

    await vscode.commands.executeCommand("vscode.setEditorLayout", {
      orientation: 1, // vertical (top/bottom)
      groups: [{ size: 0.5 }, { size: 0.5 }],
    });
    await vscode.window.showTextDocument(vscode.Uri.file(entryPath), {
      preview: false,
      viewColumn: vscode.ViewColumn.Two,
      preserveFocus: true,
    });
    logger.info(`[demo] opened entry file: ${entryPath}`);
  } catch (e) {
    logger.info(`[demo] failed to open entry file: ${e}`);
  }

  // Open layout panel
  try {
    panelHost.openPanel("panel-layout");
    logger.info("[demo] layout panel opened");
  } catch (e) {
    logger.info(`[demo] failed to open layout panel: ${e}`);
  }

  // Clean up subscription
  client.unsubscribe(["projects"]);
  logger.info("[demo] done");
}

export function activate(
  context: vscode.ExtensionContext,
  client: CoreClient,
  panelHost: PanelHost,
  logger: ChannelLogger,
): void {
  context.subscriptions.push(
    vscode.commands.registerCommand("atopile.demo-mode", () =>
      runDemoMode(client, panelHost, logger),
    ),
  );

  // Web-ide sessions auto-trigger demo mode on activation
  if (isWebIdeUi()) {
    vscode.commands.executeCommand("atopile.demo-mode");
  }
}
