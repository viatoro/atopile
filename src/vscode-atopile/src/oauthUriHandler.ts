import * as vscode from "vscode";
import { isWebIdeUi } from "./utils";

const CALLBACK_TIMEOUT_MS = 120_000;
const WEB_POLL_INTERVAL_MS = 1_500;

export interface OAuthCallbackResult {
  code: string | null;
  state: string | null;
  error: string | null;
  errorDescription: string | null;
}

/**
 * Handles OAuth callback URIs from the OAuth redirect.
 *
 * Desktop editors: Clerk -> gateway /oauth/callback page -> vscode:// URI -> handleUri().
 * Web editors (openvscode-server): Clerk -> gateway stores result -> extension polls for it.
 */
export class OAuthUriHandler implements vscode.UriHandler {
  private _pending: {
    expectedState: string;
    resolve: (result: OAuthCallbackResult | null) => void;
    timer: NodeJS.Timeout;
  } | null = null;

  private readonly _gatewayBaseUrl: string;
  private readonly _isWeb: boolean;

  constructor(gatewayBaseUrl: string) {
    this._gatewayBaseUrl = gatewayBaseUrl.replace(/\/+$/, "");
    this._isWeb = isWebIdeUi();
  }

  /** Whether this is a web-based editor that needs poll-based auth. */
  get isWeb(): boolean {
    return this._isWeb;
  }

  /**
   * The redirect URI registered with Clerk. Points to the gateway which
   * shows a completion page and then redirects to the editor URI scheme
   * (or stores the result for web editors to poll).
   */
  get redirectUri(): string {
    return `${this._gatewayBaseUrl}/oauth/callback/${vscode.env.uriScheme}`;
  }

  handleUri(uri: vscode.Uri): void {
    if (uri.path !== "/callback") {
      return;
    }

    const params = new URLSearchParams(uri.query);
    const result: OAuthCallbackResult = {
      code: params.get("code"),
      state: params.get("state"),
      error: params.get("error"),
      errorDescription: params.get("error_description"),
    };

    if (this._pending) {
      clearTimeout(this._pending.timer);
      const { resolve } = this._pending;
      this._pending = null;
      resolve(result);
    }
  }

  /**
   * Wait for callback via URI handler (desktop editors).
   */
  waitForCallback(
    expectedState: string,
    timeoutMs = CALLBACK_TIMEOUT_MS,
  ): Promise<OAuthCallbackResult | null> {
    this.cancelPending();

    return new Promise<OAuthCallbackResult | null>((resolve) => {
      const timer = setTimeout(() => {
        this._pending = null;
        resolve(null);
      }, timeoutMs);

      this._pending = { expectedState, resolve, timer };
    });
  }

  /**
   * Poll the gateway for the OAuth result (web editors).
   * The gateway stores the result when Clerk redirects to the callback page.
   */
  async pollForResult(
    sessionId: string,
    timeoutMs = CALLBACK_TIMEOUT_MS,
  ): Promise<OAuthCallbackResult | null> {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      try {
        const resp = await fetch(
          `${this._gatewayBaseUrl}/oauth/web-result/${sessionId}`,
        );
        if (resp.ok) {
          const data = await resp.json();
          if (data.status === "ready") {
            return {
              code: data.code ?? null,
              state: data.state ?? null,
              error: data.error ?? null,
              errorDescription: data.error_description ?? null,
            };
          }
        }
      } catch {
        // Network error, retry
      }
      await new Promise((r) => setTimeout(r, WEB_POLL_INTERVAL_MS));
    }
    return null;
  }

  cancelPending(): void {
    if (this._pending) {
      clearTimeout(this._pending.timer);
      const { resolve } = this._pending;
      this._pending = null;
      resolve(null);
    }
  }
}
