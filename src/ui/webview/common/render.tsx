import React from "react";
import ReactDOM from "react-dom/client";
import { connectWebview } from "./webviewRpcClient";
import { createWebviewLogger } from "./logger";
import type { RpcTransport } from "../../protocol/rpcTransport";
import "./index.css";

declare global {
  interface Window {
    __ATOPILE_PANEL_ID__: string;
    __ATOPILE_LOGO_URL__: string;
    __ATOPILE_LOGO_DARK_URL__: string;
    __ATOPILE_LOGO_LIGHT_URL__: string;
    __ATOPILE_IS_WEB_IDE__?: boolean;
  }
}

export const panelId = window.__ATOPILE_PANEL_ID__;
export const logoUrl = window.__ATOPILE_LOGO_URL__;
export const logoDarkUrl = window.__ATOPILE_LOGO_DARK_URL__;
export const logoLightUrl = window.__ATOPILE_LOGO_LIGHT_URL__;
export const isWebIde = window.__ATOPILE_IS_WEB_IDE__ === true;

export function render(
  App: React.ComponentType,
  opts?: { createTransport?: () => RpcTransport },
) {
  createWebviewLogger("Bootstrap").info("render");
  connectWebview(opts?.createTransport);
  ReactDOM.createRoot(document.getElementById("root")!).render(<App />);
}
