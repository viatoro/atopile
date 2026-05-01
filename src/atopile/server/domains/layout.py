"""Layout editor domain service."""

from __future__ import annotations

import asyncio
import inspect
import logging
from pathlib import Path
from typing import Awaitable, Callable

from atopile.model.file_watcher import FileWatcher
from atopile.server.domains.layout_models import (
    ActionRequest,
    RedoCommand,
    RenderDelta,
    RenderModel,
    StatusResponse,
    UndoCommand,
    WsMessage,
)
from atopile.server.domains.layout_pcb_manager import PcbManager

log = logging.getLogger(__name__)

LayoutListener = Callable[[WsMessage], Awaitable[None] | None]


def _reload_pcb_in_kicad(pcb_path: Path) -> None:
    try:
        from faebryk.libs.kicad.ipc import reload_pcb

        reload_pcb(pcb_path)
    except Exception:
        log.exception("Error reloading PCB in KiCad for layout editor: %s", pcb_path)


class LayoutService:
    """Manage the active PCB and broadcast updates to layout-editor clients."""

    def __init__(self) -> None:
        self._manager: PcbManager | None = None
        self._current_path: Path | None = None
        self._watcher: FileWatcher | None = None
        self._listeners: set[LayoutListener] = set()

    def load(self, path: Path) -> None:
        resolved = path.resolve()
        log.info("Loading PCB for layout editor: %s", resolved)
        manager = PcbManager()
        manager.load(resolved)
        self._manager = manager
        self._current_path = resolved

    @property
    def manager(self) -> PcbManager:
        if self._manager is None:
            raise RuntimeError("No PCB loaded in layout editor")
        return self._manager

    @property
    def current_path(self) -> Path | None:
        return self._current_path

    @property
    def is_loaded(self) -> bool:
        return self._manager is not None

    def add_listener(self, listener: LayoutListener) -> None:
        self._listeners.add(listener)

    def remove_listener(self, listener: LayoutListener) -> None:
        self._listeners.discard(listener)

    async def clear(self) -> None:
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher = None
        self._manager = None
        self._current_path = None

    async def open(self, path: Path) -> RenderModel:
        resolved = path.resolve()
        if self._current_path != resolved or self._manager is None:
            await asyncio.to_thread(self.load, resolved)
        await self.start_watcher()
        return await asyncio.to_thread(self.manager.get_render_model)

    async def get_render_model(self) -> RenderModel:
        if not self.is_loaded:
            raise RuntimeError("No PCB loaded in layout editor")
        return await asyncio.to_thread(self.manager.get_render_model)

    async def start_watcher(self) -> None:
        if self._watcher:
            self._watcher.stop()
            self._watcher = None

        if not self._current_path:
            return

        self._watcher = FileWatcher(
            "layout",
            paths=[self._current_path.parent],
            on_change=self._on_file_change,
            glob=self._current_path.name,
            debounce_s=1.0,
        )
        await self._watcher.watch()

    async def broadcast(self, message: WsMessage) -> None:
        stale_listeners: list[LayoutListener] = []
        for listener in list(self._listeners):
            try:
                result = listener(message)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                log.exception("Error broadcasting layout message to listener")
                stale_listeners.append(listener)

        for listener in stale_listeners:
            self._listeners.discard(listener)

    async def _on_file_change(self, _result: object) -> None:
        if not self._current_path or not self._manager:
            return

        try:
            log.info("PCB file changed on disk, reloading")
            await asyncio.to_thread(self.manager.load, self._current_path)
            model = await asyncio.to_thread(self.manager.get_render_model)
            await self.broadcast(WsMessage(type="layout_updated", model=model))
        except Exception:
            log.exception("Error reloading PCB after file change")

    async def save_and_broadcast(
        self,
        *,
        delta: RenderDelta | None = None,
        action_id: str | None = None,
    ) -> RenderModel | None:
        await asyncio.to_thread(self.manager.save)
        if self._watcher and self._current_path:
            self._watcher.notify_saved(self._current_path)
        if self._current_path:
            await asyncio.to_thread(_reload_pcb_in_kicad, self._current_path)

        if delta is not None:
            await self.broadcast(
                WsMessage(type="layout_delta", delta=delta, action_id=action_id)
            )
            return None

        model = await asyncio.to_thread(self.manager.get_render_model)
        await self.broadcast(
            WsMessage(type="layout_updated", model=model, action_id=action_id)
        )
        return model

    async def execute_action(self, req: ActionRequest) -> StatusResponse:
        if not self.is_loaded:
            return StatusResponse(
                status="error",
                code="not_loaded",
                message="No PCB loaded in layout editor",
                action_id=req.client_action_id,
            )

        action_id = req.client_action_id

        if isinstance(req, UndoCommand):
            ok = await asyncio.to_thread(self.manager.undo)
            if ok:
                await self.save_and_broadcast(action_id=action_id)
                return StatusResponse(status="ok", code="ok", action_id=action_id)
            return StatusResponse(
                status="error",
                code="nothing_to_undo",
                message="No action available to undo.",
                action_id=action_id,
            )

        if isinstance(req, RedoCommand):
            ok = await asyncio.to_thread(self.manager.redo)
            if ok:
                await self.save_and_broadcast(action_id=action_id)
                return StatusResponse(status="ok", code="ok", action_id=action_id)
            return StatusResponse(
                status="error",
                code="nothing_to_redo",
                message="No action available to redo.",
                action_id=action_id,
            )

        try:
            await asyncio.to_thread(self.manager.dispatch_action, req)
        except ValueError as exc:
            return StatusResponse(
                status="error",
                code="invalid_action_target",
                message=str(exc),
                action_id=action_id,
            )

        delta = await asyncio.to_thread(
            self.manager.get_render_delta_for_uuids, req.uuids
        )
        if delta is None:
            await self.save_and_broadcast(action_id=action_id)
            return StatusResponse(status="ok", code="ok", action_id=action_id)

        await self.save_and_broadcast(delta=delta, action_id=action_id)
        return StatusResponse(
            status="ok",
            code="ok",
            delta=delta,
            action_id=action_id,
        )


layout_service = LayoutService()
