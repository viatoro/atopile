"""Bridge for backend actions that require VS Code extension capabilities."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable
from typing import Any, Callable

from websockets.asyncio.server import ServerConnection

VSCODE_ACTION_PREFIX = "vscode."


class VscodeBridge:
    """Track and route backend actions that must execute in the extension host."""

    def __init__(self) -> None:
        self._pending_requests: dict[ServerConnection, dict[str, dict[str, str]]] = {}
        self._awaitable_futures: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._future_to_ws: dict[str, ServerConnection] = {}

    def add_client(self, ws: ServerConnection) -> None:
        self._pending_requests[ws] = {}

    def remove_client(self, ws: ServerConnection) -> None:
        self._pending_requests.pop(ws, None)
        # Cancel any awaitable futures that belong to the disconnecting client
        stale = [rid for rid, conn in self._future_to_ws.items() if conn is ws]
        for rid in stale:
            future = self._awaitable_futures.pop(rid, None)
            if future is not None and not future.done():
                future.cancel()
            self._future_to_ws.pop(rid, None)

    def remove_session(self, ws: ServerConnection, session_id: str) -> None:
        pending = self._pending_requests.get(ws)
        if pending is None:
            return
        pending.pop(session_id, None)

    def handles(self, action: str) -> bool:
        return action.startswith(VSCODE_ACTION_PREFIX)

    def forward_request(
        self, ws: ServerConnection, session_id: str, msg: dict[str, Any]
    ) -> dict[str, Any]:
        action = str(msg.get("action", ""))
        request_id = msg.get("requestId")
        if not isinstance(request_id, str) or not request_id:
            request_id = uuid.uuid4().hex

        pending = self._pending_requests.setdefault(ws, {})
        pending.setdefault(session_id, {})[request_id] = action
        payload = {
            key: value for key, value in msg.items() if key not in {"type", "requestId"}
        }
        payload.update(
            {
                "type": "extension_request",
                "sessionId": session_id,
                "requestId": request_id,
                "action": action,
            }
        )
        return payload

    async def forward_and_await(
        self,
        ws: ServerConnection,
        session_id: str,
        msg: dict[str, Any],
        send: Callable[[ServerConnection, dict[str, Any]], Awaitable[None]],
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        """Forward a request to the extension host and await its response."""
        # Use a fresh internal requestId so that this backend-initiated
        # round-trip doesn't collide with the frontend's original requestId
        # that the caller may still be tracking.
        internal_msg = {**msg, "requestId": uuid.uuid4().hex}
        payload = self.forward_request(ws, session_id, internal_msg)
        request_id = payload["requestId"]
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._awaitable_futures[request_id] = future
        self._future_to_ws[request_id] = ws
        await send(ws, payload)
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._awaitable_futures.pop(request_id, None)
            self._future_to_ws.pop(request_id, None)

    def handle_response(
        self, ws: ServerConnection, session_id: str, msg: dict[str, Any]
    ) -> dict[str, Any] | None:
        request_id = msg.get("requestId")
        if not isinstance(request_id, str) or not request_id:
            return None

        pending_by_session = self._pending_requests.get(ws)
        if pending_by_session is None:
            return None

        pending = pending_by_session.get(session_id)
        if pending is None:
            return None

        action = pending.pop(request_id, msg.get("action", ""))
        ok = msg.get("ok") is not False
        response = {
            "type": "action_result",
            "sessionId": session_id,
            "requestId": request_id,
            "action": action,
            "ok": ok,
        }
        if ok:
            response["result"] = msg.get("result")
        else:
            response["error"] = msg.get("error") or f"{action} failed"

        # Resolve awaitable future if one exists for this request
        future = self._awaitable_futures.pop(request_id, None)
        if future is not None and not future.done():
            future.set_result(response)
            return None  # Don't forward to frontend — caller handles the response

        return response
