import * as vscode from "vscode";

export const CHANNEL_LOG_LEVELS = [
  "debug",
  "info",
  "warn",
  "error",
] as const;

export type ChannelLogLevel = typeof CHANNEL_LOG_LEVELS[number];
export type CoreLogLevel = "DEBUG" | "INFO" | "WARNING" | "ERROR" | "ALERT";

export interface WebviewLogRecord {
  level: ChannelLogLevel;
  text: string;
  panelId: string;
  scope: string;
}

export class ChannelLogger {
  constructor(
    private readonly _output: vscode.OutputChannel | vscode.LogOutputChannel,
    private readonly _scope?: string,
  ) {}

  get output(): vscode.OutputChannel | vscode.LogOutputChannel {
    return this._output;
  }

  scope(scope: string): ChannelLogger {
    const nextScope = this._scope ? `${this._scope}:${scope}` : scope;
    return new ChannelLogger(this._output, nextScope);
  }

  info(message: string): void {
    this._write("info", message);
  }

  warn(message: string): void {
    this._write("warn", message);
  }

  error(message: string): void {
    this._write("error", message);
  }

  debug(message: string): void {
    this._write("debug", message);
  }

  log(level: ChannelLogLevel, message: string): void {
    this._write(level, message);
  }

  private _format(message: string): string {
    if (!this._scope) {
      return message;
    }
    return `[${this._scope}] ${message}`;
  }

  private _write(level: ChannelLogLevel, message: string): void {
    const formatted = this._format(message);
    if (hasStructuredLogMethods(this._output)) {
      this._output[level](formatted);
      return;
    }
    this._output.appendLine(`${formatTimestamp()} [${level}] VSCE: ${formatted}`);
  }
}

function hasStructuredLogMethods(
  channel: vscode.OutputChannel | vscode.LogOutputChannel,
): channel is vscode.LogOutputChannel {
  return typeof (channel as Partial<vscode.LogOutputChannel>).info === "function";
}

function formatTimestamp(date: Date = new Date()): string {
  const pad = (value: number, width: number = 2) => String(value).padStart(width, "0");
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}.${pad(date.getMilliseconds(), 3)}`;
}

export function isChannelLogLevel(value: unknown): value is ChannelLogLevel {
  return typeof value === "string" &&
    CHANNEL_LOG_LEVELS.includes(value as ChannelLogLevel);
}

export function isWebviewLogRecord(value: unknown): value is WebviewLogRecord {
  return !!value
    && typeof value === "object"
    && isChannelLogLevel((value as { level?: unknown }).level)
    && typeof (value as { text?: unknown }).text === "string"
    && typeof (value as { panelId?: unknown }).panelId === "string"
    && typeof (value as { scope?: unknown }).scope === "string";
}

export function channelLogLevelFromCore(level: CoreLogLevel): ChannelLogLevel {
  switch (level) {
    case "DEBUG":
      return "debug";
    case "INFO":
      return "info";
    case "ALERT":
      return "warn";
    case "WARNING":
      return "warn";
    case "ERROR":
      return "error";
  }
}
