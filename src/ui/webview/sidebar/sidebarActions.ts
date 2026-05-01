import { createWebviewLogger } from "../common/logger";
import { rpcClient } from "../common/webviewRpcClient";

const logger = createWebviewLogger("Sidebar");

export async function requestPanel(panelId: string): Promise<void> {
  logger.info(`openPanel click panelId=${panelId}`);
  try {
    // When opening the layout panel from the sidebar, sync to the
    // project file first (clears any active preview).
    if (panelId === "panel-layout") {
      rpcClient?.sendAction("syncSelectedLayout");
    }
    await rpcClient?.requestAction("vscode.openPanel", { panelId });
    logger.info(`openPanel resolved panelId=${panelId}`);
  } catch (error) {
    logger.error(
      `openPanel failed panelId=${panelId} error=${error instanceof Error ? error.message : String(error)}`,
    );
  }
}
