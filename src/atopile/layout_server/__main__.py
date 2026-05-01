"""Standalone layout-editor server.

CLI entry point: ``python -m atopile.layout_server <path.kicad_pcb> [--port 8100]``.
"""

from __future__ import annotations

import copy
import logging
import shutil
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

import typer
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from atopile.layout_server.server import create_layout_router
from atopile.server.domains.layout import LayoutService

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
FRONTEND_DIR = Path(__file__).parent / "frontend"
TEMPLATE_PATH = STATIC_DIR / "layout-editor.hbs"


def _render_layout_template(
    *,
    ws_path: str,
    editor_uri: str,
    editor_css_uri: str,
    nonce: str = "",
    csp: str,
) -> str:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    values = {
        "wsPath": ws_path,
        "editorUri": editor_uri,
        "editorCssUri": editor_css_uri,
        "nonce": nonce,
        "csp": csp,
    }
    for key, value in values.items():
        template = template.replace(f"{{{{{key}}}}}", value)
    return template


def _normalize_base_path(base_path: str) -> str:
    if not base_path or base_path == "/":
        return ""
    return "/" + base_path.strip("/")


def create_app_for_service(service: LayoutService, *, base_path: str = "") -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await service.start_watcher()
        yield

    base_path = _normalize_base_path(base_path)
    ws_path = f"{base_path}/ws" if base_path else "/ws"
    editor_uri = f"{base_path}/static/editor.js" if base_path else "/static/editor.js"
    editor_css_uri = (
        f"{base_path}/static/editor.css" if base_path else "/static/editor.css"
    )
    index_path = f"{base_path}/" if base_path else "/"
    static_path = f"{base_path}/static" if base_path else "/static"

    app = FastAPI(title="PCB Layout Editor", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(create_layout_router(service, ws_path=ws_path))

    @app.get(index_path)
    async def index() -> HTMLResponse:
        html = _render_layout_template(
            ws_path=ws_path,
            editor_uri=editor_uri,
            editor_css_uri=editor_css_uri,
            csp=(
                "default-src 'self'; "
                "style-src 'self'; "
                "script-src 'self' 'unsafe-inline'; "
                "connect-src 'self' ws: wss:;"
            ),
        )
        return HTMLResponse(html)

    app.mount(static_path, StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app


def create_app(pcb_path: Path) -> FastAPI:
    service = LayoutService()
    service.load(pcb_path)
    return create_app_for_service(service)


def _ensure_editor_bundle() -> None:
    bun = shutil.which("bun")
    if not bun:
        home_bun = Path.home() / ".bun" / "bin" / "bun"
        if home_bun.is_file():
            bun = str(home_bun)
    if not bun:
        raise RuntimeError(
            "bun is not installed; cannot build layout editor bundle. "
            "Run `bun --cwd src/atopile/layout_server/frontend run build`."
        )
    if not FRONTEND_DIR.is_dir():
        raise RuntimeError(
            f"Layout frontend source directory not found: {FRONTEND_DIR}"
        )

    typer.echo("Building layout editor frontend assets...", err=True)
    result = subprocess.run([bun, "run", "build"], cwd=str(FRONTEND_DIR), check=False)
    if result.returncode != 0:
        raise RuntimeError(
            "Failed to build layout editor bundle. "
            "Run `bun --cwd src/atopile/layout_server/frontend run build`."
        )
    editor_js = STATIC_DIR / "editor.js"
    editor_css = STATIC_DIR / "editor.css"
    if not editor_js.is_file() or not editor_css.is_file():
        raise RuntimeError(
            "Layout editor build reported success but required editor assets "
            "are still missing."
        )


def main(
    pcb_path: Path = typer.Argument(..., help="Path to .kicad_pcb file"),
    port: int = typer.Option(8100, help="Server port"),
    host: str = typer.Option("127.0.0.1", help="Server host"),
) -> None:
    if not pcb_path.is_file():
        raise typer.BadParameter(f"File not found: {pcb_path}", param_hint="pcb_path")
    try:
        _ensure_editor_bundle()
    except RuntimeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    import uvicorn
    from uvicorn.config import LOGGING_CONFIG as UVICORN_LOGGING_CONFIG

    log_config = copy.deepcopy(UVICORN_LOGGING_CONFIG)
    default_formatter = log_config.get("formatters", {}).get("default")
    if isinstance(default_formatter, dict):
        default_formatter["fmt"] = "%(asctime)s %(levelprefix)s %(message)s"
        default_formatter["datefmt"] = "%Y-%m-%d %H:%M:%S"
    access_formatter = log_config.get("formatters", {}).get("access")
    if isinstance(access_formatter, dict):
        access_formatter["fmt"] = (
            '%(asctime)s %(levelprefix)s %(client_addr)s - "%(request_line)s" '
            "%(status_code)s"
        )
        access_formatter["datefmt"] = "%Y-%m-%d %H:%M:%S"

    uvicorn.run(create_app(pcb_path), host=host, port=port, log_config=log_config)


if __name__ == "__main__":
    typer.run(main)
