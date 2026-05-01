"""
Minimal Altium 365 CLI — log in, enumerate components, download item-revision
zips, and list managed-project git repositories.

All real work lives in `api.Altium365Api`; this file is just Typer plumbing +
output rendering (Rich tables, JSON dumps, progress prints). `list` /
`download` / `repos` take no auth flags — they pull everything from
`.auth-token.json` (falling back to `ALTIUM_*` env vars). Run `login` first.
"""

from __future__ import annotations

import json as _json
import os
import sys
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.table import Table

from faebryk.libs.eda.altium.models.server.api import (
    AUTH_TOKEN_FILE,
    SEARCH_LIMIT_ALL,
    Altium365Api,
)

# ---------------------------------------------------------------------------
# Typer app
# ---------------------------------------------------------------------------

app = typer.Typer(
    help="Minimal Altium 365 CLI (list components, download item-revision zips).",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command("login")
def login_cmd(
    email: str = typer.Option(
        None,
        "--email",
        help=(
            "Login email. Required for --headless; in browser mode defaults "
            "to the `username` / `email` claim from the id_token."
        ),
    ),
    password: str = typer.Option(
        None,
        "--password",
        help=(
            "Password for headless login. Also reads $ALTIUM_PASSWORD. "
            "Setting this (or $ALTIUM_PASSWORD) implies --headless."
        ),
    ),
    headless: bool = typer.Option(
        False,
        "--headless",
        help=(
            "Skip the browser and log in with email+password via the "
            "IdentityServer 4 JSON API (README § 14.1.6). Prompts for "
            "missing credentials."
        ),
    ),
    workspace: str = typer.Option(
        None,
        "--workspace",
        help=(
            "Pick a specific workspace by display name. By default, the "
            "user's default workspace is auto-discovered via "
            "`GetUserWorkspaces` after the OIDC step."
        ),
    ),
    open_browser: bool = typer.Option(
        True,
        "--open-browser/--no-open-browser",
        help="Open the OAuth authorize URL in the default system browser.",
    ),
    timeout: int = typer.Option(
        300,
        "--timeout",
        help="Seconds to wait for the browser to deliver the OAuth code.",
    ),
    out: Path = typer.Option(
        AUTH_TOKEN_FILE,
        "--out",
        "-o",
        help=(
            "Where to write the auth-token JSON. `list`, `download` and "
            f"`repos` read this path by default ({AUTH_TOKEN_FILE})."
        ),
    ),
) -> None:
    """Log in via OAuth 2.0 (PKCE) and cache the session for later commands.

    Browser mode (default) drives the OIDC PKCE flow in the system browser;
    headless mode (`--headless`, `--password`, or `$ALTIUM_PASSWORD`) uses
    IdentityServer 4's `/api/account/signIn` JSON API and works over SSH /
    in CI. Both paths mint a short `AFSSessionID` via the workspace
    `servicediscovery` `Login` SOAP op and write the full session +
    endpoint directory to `.auth-token.json` (mode 0600).
    """
    # Resolve password from env, then prompt for whatever's still missing
    # if we're in headless mode.
    if password is None:
        password = os.environ.get("ALTIUM_PASSWORD")
    if headless or password is not None:
        if not email:
            email = typer.prompt("Altium email")
        if not password:
            password = typer.prompt("Altium password", hide_input=True)
        err_console.print(f"[dim]Headless sign-in as {email}...[/dim]")

    with Altium365Api() as api:
        token = api.login(
            email=email,
            password=password,
            workspace_name=workspace,
            open_browser=open_browser,
            timeout_s=timeout,
        )

    token.write(out)

    console.print(
        f"[green]✓[/green] Logged in as [bold]{token.email}[/bold]  "
        f"([dim]{len(token.endpoints)} endpoints discovered[/dim])"
    )
    console.print(f"  Session written to [cyan]{out}[/cyan]")
    console.print(f"  Workspace:     [dim]{token.workspace_host}[/dim]")
    if token.regional_host:
        console.print(f"  Regional host: [dim]{token.regional_host}[/dim]")
    if token.git_host:
        console.print(f"  Git host:      [dim]{token.git_host}[/dim]")


@app.command("list")
def list_components_cmd(
    component_type: str = typer.Option(
        None,
        "--type",
        "-t",
        help=(
            "Filter by ComponentType path, e.g. 'Resistors\\' or 'LED\\'. "
            "Include the trailing backslash — that's part of the value."
        ),
    ),
    limit: int = typer.Option(
        SEARCH_LIMIT_ALL,
        "--limit",
        "-n",
        help="Max rows to request (server side). Default is int32-max.",
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit JSON instead of a table."
    ),
) -> None:
    """List all components in the workspace via the regional search service."""
    with Altium365Api() as api:
        rows, total = api.search_components(component_type=component_type, limit=limit)
        regional_host = api.config.regional_host

    if json_out:
        typer.echo(_json.dumps(rows, indent=2))
        return

    columns = (
        "HRID",
        "ItemHRID",
        "ComponentType",
        "Description",
        "ItemGUID",
        "RevisionGUID",
    )
    table = Table(
        title=f"Altium 365 components ({regional_host})",
        caption=f"Total: {total} (showing {len(rows)})",
    )
    for col in columns:
        table.add_column(col, overflow="fold")
    for row in rows:
        table.add_row(*(row[c] or "" for c in columns))
    console.print(table)


@app.command("download")
def download_cmd(
    hrid: str = typer.Argument(
        ...,
        help=(
            "Item-revision HRID, e.g. PCC-015-0000-1, SYM-015-0000-1, or "
            "CMP-XXX-YYYYY-Z. For CMP-* the script walks the link table and "
            "downloads every non-template child (footprint(s) + symbol(s))."
        ),
    ),
    out: Path = typer.Option(
        Path("."),
        "--out",
        "-o",
        help="Output directory (created if missing).",
    ),
) -> None:
    """Download the zip(s) for an item-revision HRID from the Altium vault."""
    count = 0
    with Altium365Api() as api:
        for child_hrid, dest, size in api.download_item(hrid, out):
            console.print(
                f"[green]✓[/green] {child_hrid}  [dim]({size:,} bytes → {dest})[/dim]"
            )
            count += 1

    if count == 0:
        err_console.print(
            f"[yellow]warning:[/yellow] {hrid} has no downloadable children; "
            f"nothing written."
        )


@app.command("repos")
def list_repos_cmd(
    json_out: bool = typer.Option(
        False, "--json", help="Emit JSON instead of a table."
    ),
) -> None:
    """List managed projects (git repositories) in the workspace.

    Prints a clone-ready git URL for each project. Pipe one of them
    into `clone` to actually fetch the repo.
    """
    with Altium365Api() as api:
        rows = api.list_projects()
        cfg = api.config

    if json_out:
        typer.echo(_json.dumps(rows, indent=2))
        return

    columns = ("Name", "HRID", "Type", "Description", "GitURL")
    table = Table(
        title=f"Altium 365 managed projects ({cfg.regional_host})",
        caption=f"Total: {len(rows)}   Git host: {cfg.git_host}",
    )
    for col in columns:
        table.add_column(col, overflow="fold")
    for row in rows:
        table.add_row(*(row[c] or "" for c in columns))
    console.print(table)


@app.command("clone")
def clone_cmd(
    git_url: str = typer.Argument(
        ...,
        help=("Git URL from `repos`, e.g. https://<git-host>/git/<REPOSITORYPATH>.git"),
    ),
    target: Path = typer.Argument(
        None,
        help=(
            "Local directory to clone into. Defaults to the repo name "
            "(basename of the URL without `.git`)."
        ),
    ),
    email: str = typer.Option(
        None,
        "--email",
        help=(
            "Username for HTTP Basic auth. Defaults to the email stored "
            "in `.auth-token.json` / $ALTIUM_EMAIL. The server ignores "
            "this value — identity comes from the session."
        ),
    ),
) -> None:
    """Clone a managed-project git repo using the current AFSSessionID.

    Uses HTTP Basic auth with `<email>:<short AFSSessionID>` injected
    into git via transient `http.extraheader` config. Fails fast with a
    401 if the short session has expired — re-run `login` to mint a new
    one. See dumps/README.md § 16.
    """
    if target is None:
        name = git_url.rstrip("/").rsplit("/", 1)[-1]
        if name.endswith(".git"):
            name = name[:-4]
        if not name:
            raise typer.BadParameter(
                "could not derive a target directory from git_url; pass one explicitly."
            )
        target = Path(name)

    err_console.print(f"[dim]Cloning {git_url} → {target}...[/dim]")
    with Altium365Api() as api:
        repo = api.clone(git_url, target, email=email)

    head = repo.head.commit.hexsha[:12] if repo.head.is_valid() else "(empty)"
    console.print(
        f"[green]✓[/green] Cloned → [cyan]{target}[/cyan]  [dim]({head})[/dim]"
    )


def main() -> None:
    try:
        app()
    except httpx.HTTPStatusError as e:
        err_console.print(
            f"[red]HTTP {e.response.status_code}[/red] from "
            f"{e.request.method} {e.request.url}: "
            f"{e.response.text[:400]}"
        )
        sys.exit(1)
    except (RuntimeError, ValueError) as e:
        err_console.print(f"[red]error:[/red] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
