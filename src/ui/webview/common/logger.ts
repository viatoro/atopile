import { getVscodeApi } from "./vscodeApi";

type WebviewLogLevel = "debug" | "info" | "warn" | "error";

type WebviewLogMessage = {
  level: WebviewLogLevel;
  text: string;
  panelId: string;
  scope: string;
};

function getPanelId(): string {
  return (window as Window & { __ATOPILE_PANEL_ID__?: string }).__ATOPILE_PANEL_ID__ || "unknown";
}

export class WebviewLogger {
  private readonly _panelId: string;
  private readonly _scope: string;

  constructor(scope: string) {
    this._panelId = getPanelId();
    this._scope = scope;
  }

  info(message: string): void {
    this._emit("info", message);
  }

  warn(message: string): void {
    this._emit("warn", message);
  }

  error(message: string): void {
    this._emit("error", message);
  }

  debug(message: string): void {
    this._emit("debug", message);
  }

  private _emit(level: WebviewLogLevel, text: string): void {
    const scopedMessage = this._format(text);
    console[level](scopedMessage);
    const message: WebviewLogMessage = {
      level,
      text,
      panelId: this._panelId,
      scope: this._scope,
    };
    getVscodeApi()?.postMessage(message);
  }

  private _format(message: string): string {
    return `[${this._panelId}:${this._scope}] ${message}`;
  }
}

export function createWebviewLogger(scope: string): WebviewLogger {
  return new WebviewLogger(scope);
}
