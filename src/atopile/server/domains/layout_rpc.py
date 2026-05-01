"""Shared layout RPC session handling."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from pydantic import TypeAdapter

from atopile.server.domains.layout import LayoutService
from atopile.server.domains.layout_models import ActionRequest, WsMessage

LayoutRpcSend = Callable[[dict[str, Any]], Awaitable[None]]

ACTION_REQUEST_ADAPTER = TypeAdapter(ActionRequest)
LAYOUT_RPC_ACTIONS = frozenset(
    {
        "subscribeLayout",
        "unsubscribeLayout",
        "getLayoutRenderModel",
        "executeLayoutAction",
    }
)


class LayoutRpcSession:
    def __init__(self, service: LayoutService, send: LayoutRpcSend) -> None:
        self._service = service
        self._send = send
        self._subscribed = False

    @staticmethod
    def handles(action: str) -> bool:
        return action in LAYOUT_RPC_ACTIONS

    async def dispatch(self, msg: Mapping[str, Any]) -> bool:
        action = str(msg.get("action") or "")
        if not self.handles(action):
            return False

        if action == "subscribeLayout":
            if not self._subscribed:
                self._service.add_listener(self._emit_update)
                self._subscribed = True
            return True

        if action == "unsubscribeLayout":
            await self.close()
            return True

        request_id = str(msg.get("requestId") or "")
        if not request_id:
            raise ValueError(f"{action} requires requestId")

        if action == "getLayoutRenderModel":
            result = await self._service.get_render_model()
        else:
            payload = {
                key: value
                for key, value in msg.items()
                if key not in {"type", "action", "requestId", "sessionId"}
            }
            request = ACTION_REQUEST_ADAPTER.validate_python(payload)
            result = await self._service.execute_action(request)

        await self._send(
            {
                "type": "action_result",
                "requestId": request_id,
                "action": action,
                "ok": True,
                "result": result.model_dump(mode="json"),
                "error": None,
            }
        )
        return True

    async def send_error(
        self,
        action: str,
        request_id: str,
        error: str,
    ) -> None:
        await self._send(
            {
                "type": "action_result",
                "requestId": request_id,
                "action": action,
                "ok": False,
                "result": None,
                "error": error,
            }
        )

    async def close(self) -> None:
        if not self._subscribed:
            return
        self._service.remove_listener(self._emit_update)
        self._subscribed = False

    async def _emit_update(self, message: WsMessage) -> None:
        await self._send(message.model_dump(mode="json"))
