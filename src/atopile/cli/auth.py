from __future__ import annotations

import webbrowser
from typing import Annotated

import typer

from atopile.auth.runtime import set_auth_token
from atopile.auth.session import (
    DEFAULT_LOGIN_TIMEOUT_SECONDS,
    AuthError,
    build_authorization_url,
    clear_stored_session,
    create_oauth_state,
    create_pkce_code_challenge,
    create_pkce_code_verifier,
    create_session_id,
    exchange_oauth_code,
    fetch_discovery,
    get_valid_stored_session,
    poll_for_oauth_result,
    storage_backend_name,
    store_session,
)
from atopile.telemetry.telemetry import capture_auth_event
from faebryk.libs.http import http_client

auth_app = typer.Typer(rich_markup_mode="rich")


@auth_app.command("login")
def login(
    timeout: Annotated[
        int,
        typer.Option(
            "--timeout",
            min=1,
            help="Maximum time to wait for browser sign-in to complete.",
        ),
    ] = DEFAULT_LOGIN_TIMEOUT_SECONDS,
):
    try:
        session_id = create_session_id()
        state = create_oauth_state(session_id)
        code_verifier = create_pkce_code_verifier()
        code_challenge = create_pkce_code_challenge(code_verifier)

        with http_client() as client:
            discovery = fetch_discovery(client)
            auth_url = build_authorization_url(discovery, code_challenge, state)

            typer.echo("Opening your browser for atopile sign-in...")
            if not webbrowser.open(auth_url):
                typer.echo("Open this URL to continue sign-in:")
                typer.echo(auth_url)

            callback = poll_for_oauth_result(client, session_id, timeout)
            if callback is None:
                typer.echo("Timed out waiting for sign-in to complete.")
                raise typer.Exit(1)

            if callback.error:
                message = callback.error_description or callback.error
                typer.echo(f"OAuth sign-in failed: {message}")
                raise typer.Exit(1)
            if callback.state != state or not callback.code:
                typer.echo("OAuth callback state mismatch.")
                raise typer.Exit(1)

            session = exchange_oauth_code(
                client,
                discovery.token_endpoint,
                callback.code,
                code_verifier,
            )

        store_session(session)
        set_auth_token(session.access_token)
        capture_auth_event(
            session.access_token, session.user.email if session.user else None
        )

        if storage_backend_name() == "file":
            typer.echo(
                "System keychain unavailable, using local user-only token storage."
            )

        if session.user:
            typer.echo(f"Signed in as {session.user.display_name()}.")
        else:
            typer.echo("Signed in.")
    except AuthError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc


@auth_app.command("logout")
def logout():
    clear_stored_session()
    set_auth_token(None)
    typer.echo("Signed out.")


@auth_app.command("status")
def status():
    session = get_valid_stored_session()
    if session is None:
        typer.echo("Signed out.")
        return

    if session.user:
        typer.echo(f"Signed in as {session.user.display_name()}.")
        return

    typer.echo("Signed in.")
