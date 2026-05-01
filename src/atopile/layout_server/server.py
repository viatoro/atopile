"""Shared layout-editor API router factory."""

from __future__ import annotations

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from atopile.server.domains.layout import LayoutService
from atopile.server.domains.layout_rpc import LayoutRpcSession


def create_layout_router(
    service: LayoutService,
    *,
    ws_path: str = "/ws",
) -> APIRouter:
    """Build a layout-editor RPC websocket router wired to *service*."""
    router = APIRouter(tags=["layout"])

    @router.websocket(ws_path)
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        session = LayoutRpcSession(service, websocket.send_json)

        try:
            while True:
                msg = json.loads(await websocket.receive_text())
                if msg.get("type") != "action":
                    continue

                action = str(msg.get("action") or "")
                try:
                    handled = await session.dispatch(msg)
                    if not handled:
                        raise ValueError(f"Unsupported layout action: {action}")
                except Exception as exc:
                    await session.send_error(
                        action,
                        str(msg.get("requestId") or ""),
                        str(exc),
                    )
        except WebSocketDisconnect:
            pass
        finally:
            await session.close()

    return router
