import * as vscode from "vscode";
import {
  CloseAction,
  ErrorAction,
  LanguageClient,
  LanguageClientOptions,
  RevealOutputChannelOn,
  ServerOptions,
  State,
} from "vscode-languageclient/node";
import type { ResolvedBinary } from "./atoResolver";
import { ChannelLogger } from "./logger";

export class LspClientManager implements vscode.Disposable {
  private _client: LanguageClient | null = null;
  private _disposed = false;
  private _startPromise: Promise<void> | null = null;

  constructor(
    private readonly _logger: ChannelLogger,
    private readonly _resolved: ResolvedBinary,
  ) {}

  async start(): Promise<void> {
    if (this._startPromise) {
      return this._startPromise;
    }

    this._startPromise = this._startInternal();
    try {
      await this._startPromise;
    } finally {
      this._startPromise = null;
    }
  }

  private async _startInternal(): Promise<void> {
    if (this._disposed) {
      throw new Error("Cannot start LSP after disposal");
    }

    if (!this._client) {
      this._client = this._createClient();
    }

    if (!this._client.needsStart()) {
      return;
    }

    this._logger.info("Starting language client");
    try {
      await this._client.start();
    } catch (error) {
      const client = this._client;
      this._client = null;
      if (client) {
        void client.dispose().catch(() => {
          // Ignore shutdown errors from partially-started clients.
        });
      }
      throw error;
    }
  }

  async stop(): Promise<void> {
    if (!this._client || !this._client.needsStop()) {
      return;
    }

    this._logger.info("Stopping language client");
    await this._client.stop();
  }

  async restart(): Promise<void> {
    const client = this._client;
    if (!client) {
      await this.start();
      return;
    }

    this._logger.info("Restarting language client");
    await client.restart();
  }

  dispose(): void {
    this._disposed = true;
    const client = this._client;
    this._client = null;
    if (!client) {
      return;
    }

    void client.dispose().catch((error: unknown) => {
      if (isAlreadyStoppedError(error)) {
        return;
      }
      const message = error instanceof Error ? error.message : String(error);
      this._logger.error(`Failed to dispose language client cleanly: ${message}`);
    });
  }

  private _createClient(): LanguageClient {
    const serverOptions: ServerOptions = {
      command: this._resolved.command,
      args: [...this._resolved.prefixArgs, "lsp", "start"],
      options: {
        env: { ...process.env, ...this._resolved.env },
      },
    };

    const clientOptions: LanguageClientOptions = {
      documentSelector: [{ language: "ato", scheme: "file" }],
      diagnosticCollectionName: "atopile",
      outputChannel: this._logger.output,
      revealOutputChannelOn: RevealOutputChannelOn.Never,
      errorHandler: {
        error: (error, message, count) => {
          const detail = error instanceof Error ? error.message : String(error);
          this._logger.error(
            `LSP transport error after ${count} errors: ${detail} (message: ${JSON.stringify(message)})`,
          );
          return { action: ErrorAction.Continue, handled: true };
        },
        closed: () => {
          this._logger.warn("LSP connection closed unexpectedly; restarting");
          return { action: CloseAction.Restart, handled: true };
        },
      },
    };

    const client = new LanguageClient(
      "atopile-lsp",
      "atopile Language Server",
      serverOptions,
      clientOptions,
    );

    client.onDidChangeState(({ oldState, newState }) => {
      this._logger.info(
        `Language client state ${stateToString(oldState)} -> ${stateToString(newState)}`,
      );
    });

    return client;
  }
}

function isAlreadyStoppedError(error: unknown): boolean {
  return error instanceof Error
    && error.message.includes("Client is not running and can't be stopped");
}

function stateToString(state: State): string {
  switch (state) {
    case State.Starting:
      return "starting";
    case State.Running:
      return "running";
    case State.Stopped:
      return "stopped";
  }

  return "unknown";
}
