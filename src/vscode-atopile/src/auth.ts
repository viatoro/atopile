import * as vscode from "vscode";

export interface AuthUser {
  id: string;
  name: string;
  email?: string;
  imageUrl?: string;
}

/**
 * Thin auth state holder.
 *
 * Auth is owned by the backend (keyring/file via ``atopile.auth.session``).
 * The extension just tracks the current state for UI rendering and fires
 * ``onDidChangeAuth`` so other components can react.
 */
export class AuthManager implements vscode.Disposable {
  private _isAuthenticated = false;
  private _user: AuthUser | null = null;
  private readonly _onDidChangeAuth = new vscode.EventEmitter<boolean>();

  readonly onDidChangeAuth = this._onDidChangeAuth.event;

  get isAuthenticated(): boolean {
    return this._isAuthenticated;
  }

  get user(): AuthUser | null {
    return this._user;
  }

  /** Update local state from a server auth status response. */
  setAuthState(isAuthenticated: boolean, user: AuthUser | null): void {
    const changed = this._isAuthenticated !== isAuthenticated;
    this._isAuthenticated = isAuthenticated;
    this._user = user;
    if (changed) {
      this._onDidChangeAuth.fire(isAuthenticated);
    }
  }

  dispose(): void {
    this._onDidChangeAuth.dispose();
  }
}
