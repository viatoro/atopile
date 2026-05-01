"""KiCad RPC session — thin dispatch layer following the LayoutRpcSession pattern."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from atopile.logging import get_logger
from atopile.server.domains.kicad import KicadService

log = get_logger(__name__)

KicadRpcSend = Callable[[dict[str, Any]], Awaitable[None]]

KICAD_RPC_ACTIONS = frozenset({"openKicad"})


class KicadRpcSession:
    """Per-connection, per-session KiCad RPC handler."""

    def __init__(
        self,
        service: KicadService,
        send: KicadRpcSend,
    ) -> None:
        self._service = service
        self._send = send
        self._task: asyncio.Task[None] | None = None

    @staticmethod
    def handles(action: str) -> bool:
        return action in KICAD_RPC_ACTIONS

    async def dispatch(self, msg: Mapping[str, Any]) -> None:
        """Route the message to the appropriate service method.

        Runs the actual work in a background task so the WebSocket message
        loop stays free.
        """
        action = str(msg.get("action") or "")
        request_id = str(msg.get("requestId") or "")

        # Cancel any previous in-flight task for this session
        if self._task is not None and not self._task.done():
            self._task.cancel()

        self._task = asyncio.create_task(self._run(action, request_id, dict(msg)))

    async def _run(self, action: str, request_id: str, msg: dict[str, Any]) -> None:
        try:
            result = await self._service.open_kicad(msg)
            await self._send(
                {
                    "type": "action_result",
                    "requestId": request_id,
                    "action": action,
                    "ok": True,
                    "result": result,
                    "error": None,
                }
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("openKicad failed: %s", exc)
            await self._send(
                {
                    "type": "action_result",
                    "requestId": request_id,
                    "action": action,
                    "ok": False,
                    "result": None,
                    "error": str(exc),
                }
            )

    async def close(self) -> None:
        """Cancel any in-flight background task."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
