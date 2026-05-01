### ATTENTION LLMS: PLEASE READ THE DOCSSTRING

"""WebSocket transport for the UI-facing core server API.

This module owns connection lifecycle, subscriptions, and dispatch only.
UI-specific state and interaction helpers live in `src/atopile/server/ui`.
Domain logic lives in `src/atopile/model`.
"""

# TODO: Replace raw websocket action payload decoding with typed request models.

from __future__ import annotations

import asyncio
import base64
import json
import traceback
from pathlib import Path
from typing import Any, cast

import websockets
from pydantic import BaseModel, ConfigDict, Field
from websockets.asyncio.server import ServerConnection

from atopile.agent.providers.base import ModelId
from atopile.agent.session import (
    Agent,
    AgentSession,
    Done,
    ReasoningDelta,
    TextDelta,
    Thinking,
    TitleGenerated,
    ToolEnd,
    ToolStart,
)
from atopile.autolayout import AutolayoutService
from atopile.autolayout.deeppcb.models import JobType
from atopile.data_models import (
    AddBuildTargetRequest,
    AppContext,
    BuildRequest,
    CreateProjectRequest,
    DeleteBuildTargetRequest,
    Project,
    ResolvedBuildTarget,
    UiAutolayoutCandidateData,
    UiAutolayoutData,
    UiAutolayoutJobData,
    UiAutolayoutPreCheckItem,
    UiAutolayoutPreflightData,
    UiBuildLogRequest,
    UiLayoutData,
    UiLogEntry,
    UiLogsErrorMessage,
    UiLogsStreamMessage,
    UiPackageSyncProgress,
    UiPartData,
    UiPartsSearchData,
    UiPinoutData,
    UiProjectState,
    UiSidebarDetails,
    UpdateBuildTargetRequest,
)
from atopile.logging import get_logger
from atopile.model import (
    artifacts,
    builds,
    file_ops,
    migrations,
    packages,
    parts,
    projects,
    stdlib,
)
from atopile.model.build_queue import BuildQueue, _build_queue
from atopile.model.file_watcher import FileWatcher
from atopile.model.sqlite import Logs, delete_log_storage, initialize_log_storage
from atopile.pathutils import same_path
from atopile.server.domains.diff import diff_service
from atopile.server.domains.diff_rpc import DiffRpcSession
from atopile.server.domains.kicad import KicadService
from atopile.server.domains.kicad_rpc import KicadRpcSession
from atopile.server.domains.layout import layout_service
from atopile.server.domains.layout_models import WsMessage
from atopile.server.domains.layout_pcb_manager import PcbManager
from atopile.server.domains.layout_rpc import LayoutRpcSession
from atopile.server.domains.vscode_bridge import VscodeBridge
from atopile.server.ui import remote_assets, sidebar
from atopile.server.ui.store import Store

log = get_logger(__name__)

STREAM_POLL_INTERVAL = 0.25  # seconds


# ── Agent request models (used only by websocket dispatch) ────────────


class _AgentRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class AgentCreateSessionRequest(_AgentRequest):
    project_root: str = Field(alias="projectRoot")
    initial_message: str | None = Field(default=None, alias="initialMessage")
    error_context: dict[str, Any] | None = Field(default=None, alias="errorContext")


class AgentCreateRunRequest(_AgentRequest):
    message: str
    project_root: str = Field(alias="projectRoot")
    selected_targets: list[str] = Field(default_factory=list, alias="selectedTargets")
    error_context: dict[str, Any] | None = Field(default=None, alias="errorContext")


class AgentSetModelRequest(_AgentRequest):
    model_id: str = Field(alias="modelId")


EXTENSION_SESSION_ID = "extension"


def _render_model_from_pcb_text(text: str):
    manager = PcbManager()
    manager.load_text(text)
    return manager.get_render_model()


async def _get_package_layout_render_model(url: str):
    data = await remote_assets.fetch_remote_asset_bytes(url)
    return await asyncio.to_thread(
        _render_model_from_pcb_text, data.decode("utf-8-sig")
    )


async def _get_part_footprint_render_model(lcsc_id: str):
    data = await asyncio.to_thread(parts.PartAssets.get_footprint, lcsc_id)
    if not data:
        raise ValueError(f"Footprint not found: {lcsc_id}")
    return await asyncio.to_thread(
        _render_model_from_pcb_text, data.decode("utf-8-sig")
    )


# ---------------------------------------------------------------------------
# Autolayout: pure adapters from service models → wire models. Live at
# module scope (not on the dispatcher) so they're trivially testable.
# ---------------------------------------------------------------------------


def _candidate_routed_pct(metadata: dict[str, Any]) -> int | None:
    connected = metadata.get("airWiresConnected")
    total = metadata.get("totalAirWires")
    if connected is None or not total or total <= 0:
        return None
    return round(float(connected) / float(total) * 100)


def _candidate_to_ui(c: Any) -> UiAutolayoutCandidateData:
    md = c.metadata or {}
    via_added = md.get("viaAdded")
    return UiAutolayoutCandidateData(
        candidate_id=c.candidate_id,
        label=c.label,
        score=c.score,
        routed_pct=_candidate_routed_pct(md),
        via_count=int(via_added) if isinstance(via_added, (int, float)) else None,
        metadata=md,
        files=c.files,
    )


def _job_display_state(state: Any) -> str:
    """Collapse the lifecycle enum into the four-state display label
    the UI cares about: idle / running / done / failed."""
    s = getattr(state, "value", state)
    if s in ("submitting", "queued", "running", "building"):
        return "running"
    if s in ("completed", "awaiting_selection"):
        return "done"
    if s == "failed":
        return "failed"
    return "idle"


def _job_to_ui(svc: Any, j: Any) -> UiAutolayoutJobData:
    return UiAutolayoutJobData(
        job_id=j.job_id,
        project_root=j.project_root,
        build_target=j.build_target,
        provider=j.provider,
        job_type=j.job_type,
        state=j.state,
        display_state=_job_display_state(j.state),
        created_at=j.created_at,
        updated_at=j.updated_at,
        build_id=j.build_id,
        provider_job_ref=j.provider_job_ref,
        progress=j.progress,
        message=j.message,
        error=j.error,
        selected_candidate_id=j.selected_candidate_id,
        applied_candidate_id=j.applied_candidate_id,
        recommended_candidate_id=svc.recommended_candidate_id(j),
        layout_path=j.layout_path,
        candidates=[_candidate_to_ui(c) for c in j.candidates],
    )


def _preflight_to_ui(s: dict[str, Any]) -> UiAutolayoutPreflightData:
    return UiAutolayoutPreflightData(
        board_area_mm2=s.get("boardAreaMm2"),
        board_width_mm=s.get("boardWidthMm"),
        board_height_mm=s.get("boardHeightMm"),
        component_count=s.get("componentCount", 0),
        top_component_count=s.get("topComponentCount", 0),
        bottom_component_count=s.get("bottomComponentCount", 0),
        components_inside_board=s.get("componentsInsideBoard", 0),
        components_outside_board=s.get("componentsOutsideBoard", 0),
        component_area_mm2=s.get("componentAreaMm2"),
        pad_count=s.get("padCount", 0),
        net_count=s.get("netCount", 0),
        connection_count=s.get("connectionCount", 0),
        placement_utilization=s.get("placementUtilization"),
        top_only_utilization=s.get("topOnlyUtilization"),
        pad_density=s.get("padDensity"),
        connection_density=s.get("connectionDensity"),
        layer_count=s.get("layerCount"),
        sidedness=s.get("sidedness", ""),
        stackup_risk=s.get("stackupRisk", ""),
        recommendation=s.get("recommendation", ""),
    )


class CoreSocket:
    """Manages WebSocket connections and dispatches actions."""

    def __init__(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._clients: set[ServerConnection] = set()
        self._subscriptions: dict[ServerConnection, dict[str, set[str]]] = {}
        self._layout_sessions: dict[ServerConnection, dict[str, LayoutRpcSession]] = {}
        self._diff_sessions: dict[ServerConnection, dict[str, DiffRpcSession]] = {}
        self._kicad_sessions: dict[ServerConnection, dict[str, KicadRpcSession]] = {}
        self._log_tasks: dict[ServerConnection, dict[str, asyncio.Task]] = {}
        self._parts_search_tasks: dict[ServerConnection, asyncio.Task[None]] = {}
        self._parts_search_tokens: dict[ServerConnection, int] = {}
        self._discovery_paths: list[Path] = []
        self._vscode_bridge = VscodeBridge()
        self._kicad_service = KicadService()
        self._store = Store()
        self._store.on_change = self._on_store_change
        layout_service.add_listener(self._handle_layout_message)
        self._agent = Agent()
        self._run_tasks: dict[str, asyncio.Task[None]] = {}
        self._workspace_metadata = FileWatcher(
            "workspace-metadata",
            paths_provider=lambda: list(self._discovery_paths),
            on_change=self._handle_workspace_metadata_change,
            glob="**/ato.yaml",
            debounce_s=0.2,
        )
        self._project_files = FileWatcher(
            "project-files",
            paths=[],
            on_change=lambda result: self._store.set(
                "project_files",
                result.model_copy(update={"loading": False}),
            ),
            glob="**/*",
            debounce_s=0.1,
            mode="tree",
        )
        self._project_discovery_lock = asyncio.Lock()
        self._project_selection_lock = asyncio.Lock()
        self._pending_auth_flows: dict[str, dict[str, Any]] = {}
        self._store.set(
            "current_builds",
            [build.model_dump() for build in builds.get_active_builds()],
        )
        self._store.set(
            "previous_builds",
            [build.model_dump() for build in builds.get_finished_builds()],
        )
        self._store.set(
            "queue_builds",
            [build.model_dump() for build in builds.get_queue_builds()],
        )
        # Construct the autolayout service once, with its layout
        # listener and observer callbacks wired up atomically. No
        # singleton, no lazy init — this class owns its lifetime.
        self._autolayout_service = AutolayoutService(
            layout_service=layout_service,
            on_state_changed=self._on_autolayout_state_changed,
            on_job_completed=self._on_autolayout_job_completed,
        )
        self._push_agent_state()
        self._push_auth_state()
        self.bind_build_queue(_build_queue)

    # -- Autolayout service -------------------------------------------------

    def _push_autolayout_store(self) -> None:
        """Build UiAutolayoutData from service state and push to the store."""
        svc = self._autolayout_service
        ui_jobs: list[UiAutolayoutJobData] = []
        preflight_data: UiAutolayoutPreflightData | None = None
        placement_readiness: list[UiAutolayoutPreCheckItem] = []
        routing_readiness: list[UiAutolayoutPreCheckItem] = []

        if svc.project_root:
            try:
                jobs = svc.list_jobs(svc.project_root)
                ui_jobs = [_job_to_ui(svc, j) for j in jobs]
            except Exception:
                log.exception("Failed to read jobs for autolayout store push")

            # Convert raw preflight dict to typed model
            if svc.preflight is not None:
                preflight_data = _preflight_to_ui(svc.preflight)

            placement_readiness = [
                UiAutolayoutPreCheckItem(
                    label=item.label, passed=item.passed, detail=item.detail
                )
                for item in svc.placement_readiness()
            ]
            routing_readiness = [
                UiAutolayoutPreCheckItem(
                    label=item.label, passed=item.passed, detail=item.detail
                )
                for item in svc.routing_readiness()
            ]

        self._store.set(
            "autolayout_data",
            UiAutolayoutData(
                loading=svc.loading,
                error=svc.error,
                submitting=svc.submitting,
                jobs=ui_jobs,
                preflight=preflight_data,
                preflight_loading=svc.preflight_loading,
                preflight_error=svc.preflight_error,
                placement_readiness=placement_readiness,
                routing_readiness=routing_readiness,
                preview_job_id=svc.preview_job_id,
                preview_candidate_id=svc.preview_candidate_id,
                preview_path=svc.preview_path,
            ),
        )

    def _on_autolayout_state_changed(self) -> None:
        """Called from service (possibly background thread) when state changes."""
        self._loop.call_soon_threadsafe(self._push_autolayout_store)

    def _on_autolayout_job_completed(self, job: Any) -> None:
        """Called when a job transitions to completed — auto-preview best candidate."""
        asyncio.run_coroutine_threadsafe(
            self._auto_preview_best_candidate(job.job_id), self._loop
        )

    async def _auto_preview_best_candidate(self, job_id: str) -> None:
        """Run the service's auto-preview policy and swap the layout
        viewer to the resulting file. The service owns selection +
        artifact preparation + state; the websocket only owns the
        layout-viewer side effect."""
        try:
            svc = self._autolayout_service
            result = await asyncio.to_thread(svc.auto_preview_best, job_id)
            if result is None:
                return
            await self._show_preview_in_layout_viewer(result.preview_path)
        except Exception:
            log.exception("Auto-preview of best candidate failed")

    async def _show_preview_in_layout_viewer(self, preview_path: Path) -> None:
        """Side effect that belongs to the websocket layer: open the
        preview file in the shared layout viewer and broadcast the
        update."""
        model = await layout_service.open(preview_path)
        await layout_service.broadcast(WsMessage(type="layout_updated", model=model))
        _, selected_target = self._selected_project_context()
        self._set_layout_data(
            self._store.get("project_state").selected_project_root,
            selected_target,
            path=str(preview_path),
            read_only=True,
        )

    def _cancel_parts_search(self, ws: ServerConnection) -> None:
        task = self._parts_search_tasks.pop(ws, None)
        if task is not None:
            task.cancel()

    def _next_parts_search_token(self, ws: ServerConnection) -> int:
        token = self._parts_search_tokens.get(ws, 0) + 1
        self._parts_search_tokens[ws] = token
        return token

    async def _start_parts_search(
        self,
        ws: ServerConnection,
        *,
        session_id: str,
        action: str,
        request_id: str,
        project_root: str | None,
        query: str,
        installed_only: bool,
        limit: int,
    ) -> None:
        self._cancel_parts_search(ws)
        current = cast(UiPartsSearchData, self._store.get("parts_search"))
        self._store.set(
            "parts_search",
            parts.PartUiState.search_loading_state(
                current,
                project_root=project_root,
                query=query,
                installed_only=installed_only,
            ),
        )
        if not project_root:
            if request_id:
                await self._send_action_result(
                    ws,
                    session_id,
                    action,
                    request_id,
                    ok=True,
                    result=self._store.dump("parts_search"),
                )
            return

        token = self._next_parts_search_token(ws)

        async def run_parts_search() -> None:
            try:
                if query.strip() and not installed_only:
                    await asyncio.sleep(parts.PARTS_SEARCH_DEBOUNCE_S)

                if token != self._parts_search_tokens.get(ws):
                    return

                await parts.PartUiState.refresh_search(
                    self._store,
                    project_root=project_root,
                    query=query,
                    installed_only=installed_only,
                    limit=limit,
                )

                if token != self._parts_search_tokens.get(ws):
                    return

                if request_id:
                    await self._send_action_result(
                        ws,
                        session_id,
                        action,
                        request_id,
                        ok=True,
                        result=self._store.dump("parts_search"),
                    )
            except asyncio.CancelledError:
                return
            except Exception as exc:
                if token != self._parts_search_tokens.get(ws):
                    return
                current_state = cast(UiPartsSearchData, self._store.get("parts_search"))
                self._store.set(
                    "parts_search",
                    parts.PartUiState.search_error_state(
                        current_state,
                        project_root=project_root,
                        query=query,
                        installed_only=installed_only,
                        error=str(exc),
                    ),
                )
                if request_id:
                    await self._send_action_result(
                        ws,
                        session_id,
                        action,
                        request_id,
                        ok=False,
                        error=str(exc),
                    )
            finally:
                if self._parts_search_tokens.get(ws) == token:
                    self._parts_search_tasks.pop(ws, None)

        self._parts_search_tasks[ws] = asyncio.create_task(run_parts_search())

    # -- Client lifecycle --------------------------------------------------

    async def handle_client(self, ws: ServerConnection) -> None:
        path = getattr(getattr(ws, "request", None), "path", None)
        if path not in {None, "/atopile-ui"}:
            await ws.close(code=4000, reason="unknown path")
            return

        self._clients.add(ws)
        self._subscriptions[ws] = {}
        self._layout_sessions[ws] = {}
        self._kicad_sessions[ws] = {}
        self._log_tasks[ws] = {}
        self._parts_search_tokens[ws] = 0
        self._vscode_bridge.add_client(ws)

        try:
            async for raw in ws:
                msg = json.loads(raw)
                keys = msg.get("keys")
                self._log_websocket_event(
                    "recv",
                    session_id=self._session_id(msg),
                    type=msg.get("type"),
                    action=msg.get("action"),
                    request_id=msg.get("requestId"),
                    ok=msg.get("ok"),
                    key_count=len(keys) if isinstance(keys, list) else None,
                )
                match msg.get("type"):
                    case "subscribe":
                        await self._handle_subscribe(ws, msg)
                    case "unsubscribe":
                        self._handle_unsubscribe(ws, msg)
                    case "action":
                        try:
                            await self._dispatch(ws, msg)
                        except Exception:
                            log.exception(
                                "dispatch failed for action %s",
                                msg.get("action"),
                            )
                    case "extension_response":
                        response = self._vscode_bridge.handle_response(
                            ws, self._session_id(msg), msg
                        )
                        if response is not None:
                            await self._send_message(ws, response)

        except websockets.ConnectionClosed:
            pass
        finally:
            await self._release_client(ws)

    async def _handle_subscribe(
        self, ws: ServerConnection, msg: dict[str, Any]
    ) -> None:
        session_id = self._session_id(msg)
        keys = {str(key) for key in msg.get("keys", [])}
        subscriptions = self._subscriptions.setdefault(ws, {})
        session_keys = subscriptions.setdefault(session_id, set())
        session_keys.update(keys)
        for key in keys:
            field_name = self._store.require_field_name(key)
            await self._send_state(
                ws, session_id, field_name, self._store.dump(field_name)
            )

    def _handle_unsubscribe(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        session_id = self._session_id(msg)
        keys = {str(key) for key in msg.get("keys", [])}
        session_keys = self._subscriptions.setdefault(ws, {}).get(session_id)
        if session_keys is None:
            return
        session_keys.difference_update(keys)
        if not session_keys:
            self._subscriptions[ws].pop(session_id, None)

    def _build_agent_context(self) -> AppContext:
        return AppContext(workspace_paths=list(self._discovery_paths))

    @staticmethod
    def _request_key(msg: dict[str, Any]) -> str:
        payload = {
            key: value
            for key, value in msg.items()
            if key not in {"type", "action", "requestId", "sessionId"}
        }
        return json.dumps(payload, separators=(",", ":"))

    def _log_websocket_event(self, event: str, **fields: Any) -> None:
        details = " ".join(
            f"{key}={value}" for key, value in fields.items() if value is not None
        )
        if details:
            log.debug("WebSocket %s %s", event, details)
            return
        log.debug("WebSocket %s", event)

    def _push_auth_state(self) -> None:
        """Push current auth state to all connected clients via the store."""
        from atopile.auth.session import get_valid_stored_session

        session = get_valid_stored_session()
        self._store.set(
            "auth_state",
            {
                "isAuthenticated": session is not None,
                "user": {
                    "id": session.user.id,
                    "name": session.user.name,
                    "email": session.user.email,
                    "imageUrl": session.user.image_url,
                }
                if session and session.user
                else None,
            },
        )

    def _start_auth_login(self) -> tuple[str, str]:
        """Begin the OAuth flow: build the URL and stash PKCE state.

        Returns (auth_url, pending_id). The webview is responsible for opening
        auth_url in the user's browser; `_complete_auth_login` then polls the
        gateway and exchanges the code.
        """
        import secrets as _secrets

        from atopile.auth.session import (
            build_authorization_url,
            create_oauth_state,
            create_pkce_code_challenge,
            create_pkce_code_verifier,
            create_session_id,
            fetch_discovery,
        )
        from faebryk.libs.http import http_client

        session_id = create_session_id()
        state = create_oauth_state(session_id)
        code_verifier = create_pkce_code_verifier()
        code_challenge = create_pkce_code_challenge(code_verifier)

        with http_client() as client:
            discovery = fetch_discovery(client)
            auth_url = build_authorization_url(discovery, code_challenge, state)

        pending_id = _secrets.token_urlsafe(16)
        self._pending_auth_flows[pending_id] = {
            "session_id": session_id,
            "state": state,
            "code_verifier": code_verifier,
            "token_endpoint": discovery.token_endpoint,
        }
        return auth_url, pending_id

    def _complete_auth_login(self, pending_id: str):
        """Finish the OAuth flow started by `_start_auth_login` (blocking)."""
        from atopile.auth.session import (
            AuthError,
            exchange_oauth_code,
            poll_for_oauth_result,
            store_session,
        )
        from atopile.telemetry.telemetry import capture_auth_event
        from faebryk.libs.http import http_client

        pending = self._pending_auth_flows.pop(pending_id, None)
        if pending is None:
            raise AuthError("Sign-in request expired or unknown.")

        session_id = pending["session_id"]
        expected_state = pending["state"]
        code_verifier = pending["code_verifier"]
        token_endpoint = pending["token_endpoint"]

        with http_client() as client:
            callback = poll_for_oauth_result(client, session_id)
            if callback is None:
                raise AuthError("Timed out waiting for sign-in.")

            if callback.error:
                message = callback.error_description or callback.error
                raise AuthError(f"OAuth sign-in failed: {message}")
            if callback.state != expected_state or not callback.code:
                raise AuthError("OAuth callback state mismatch.")

            session = exchange_oauth_code(
                client,
                token_endpoint,
                callback.code,
                code_verifier,
            )

        store_session(session)
        capture_auth_event(
            session.access_token,
            session.user.email if session.user else None,
        )
        return session

    def _push_agent_state(self) -> None:
        self._store.set("agent_data", self._agent.ui_state())

    async def _emit_agent_progress(
        self,
        session: AgentSession,
        payload: dict[str, Any],
        *,
        push_state: bool = True,
    ) -> None:
        payload["type"] = "agent_progress"
        payload["session_id"] = session.session_id
        payload["checklist"] = {"items": session._checklist.summary()}
        await self._broadcast_agent_message(payload)
        if push_state:
            # Update store and broadcast state immediately (not via on_change
            # which queues with run_coroutine_threadsafe and only flushes at
            # the next await point in the event loop).
            self._store.set("agent_data", self._agent.ui_state())
            await self._broadcast_state("agent_data", self._store.dump("agent_data"))

    async def _run_agent_turn(
        self,
        session: AgentSession,
        message: str,
        *,
        error_context: dict[str, Any] | None = None,
    ) -> None:
        """Run a turn in a background task, forwarding events to the frontend."""
        try:
            last_state_push = 0.0
            state_push_interval = 0.1  # seconds — throttle store broadcasts

            async for event in session.send_message(
                message,
                error_context=error_context,
            ):
                match event:
                    case Thinking():
                        await self._emit_agent_progress(
                            session,
                            {
                                "phase": "thinking",
                            },
                        )
                    case TextDelta(delta=delta):
                        # Send the delta event immediately (lightweight)
                        # but throttle the full state push
                        now = asyncio.get_event_loop().time()
                        should_push = (now - last_state_push) >= state_push_interval
                        await self._emit_agent_progress(
                            session,
                            {
                                "phase": "text_delta",
                                "delta": delta,
                            },
                            push_state=should_push,
                        )
                        if should_push:
                            last_state_push = now
                    case ToolStart(name=name, label=label, args=args):
                        await self._emit_agent_progress(
                            session,
                            {
                                "phase": "tool_start",
                                "name": name,
                                "label": label,
                                "args": args,
                            },
                        )
                    case ToolEnd(name=name, ok=ok, trace=trace):
                        await self._emit_agent_progress(
                            session,
                            {
                                "phase": "tool_end",
                                "name": name,
                                "ok": ok,
                                "trace": {
                                    "name": trace.name,
                                    "label": trace.label,
                                    "args": trace.args,
                                    "ok": trace.ok,
                                    "result": trace.result,
                                },
                            },
                        )
                    case ReasoningDelta(delta=delta):
                        now = asyncio.get_event_loop().time()
                        should_push = (now - last_state_push) >= state_push_interval
                        await self._emit_agent_progress(
                            session,
                            {
                                "phase": "reasoning_delta",
                                "delta": delta,
                            },
                            push_state=should_push,
                        )
                        if should_push:
                            last_state_push = now
                    case TitleGenerated(title=title):
                        await self._emit_agent_progress(
                            session,
                            {
                                "phase": "title",
                                "title": title,
                            },
                        )
                    case Done():
                        await self._emit_agent_progress(
                            session,
                            {
                                "phase": "done",
                            },
                        )
        except asyncio.CancelledError:
            # User hit the stop button (or the ws dropped). Clean up the
            # session's pending state so it's reusable on the next turn.
            session.abort_run(reason="Stopped")
            try:
                await self._emit_agent_progress(
                    session,
                    {
                        "phase": "stopped",
                    },
                )
            except Exception:  # noqa: BLE001 — best-effort notify
                log.debug(
                    "Failed to emit stopped progress for %s",
                    session.session_id,
                    exc_info=True,
                )
            raise
        except Exception as exc:
            log.exception("Agent turn failed for session %s", session.session_id)
            session._error = str(exc)
            session.abort_run(reason="Error")
            await self._emit_agent_progress(
                session,
                {
                    "phase": "error",
                    "error": str(exc),
                },
            )
        finally:
            self._run_tasks.pop(session.session_id, None)
            # Belt-and-suspenders: if anything left the session pending
            # (unexpected exit path), still tidy up before persisting.
            session.abort_run(reason="Interrupted")
            self._agent.save_sessions()
            self._push_agent_state()

    async def _apply_project_selection(
        self,
        *,
        selected_project_root: str | None,
        selected_target: ResolvedBuildTarget | None,
        project_list: list[Project] | None = None,
        project_state: UiProjectState | None = None,
    ) -> None:
        current_project_state = (
            cast(UiProjectState, self._store.get("project_state"))
            if project_state is None
            else project_state
        )
        # Load agent sessions for the newly selected project
        if selected_project_root:
            self._agent.load_sessions(Path(selected_project_root))
            self._push_agent_state()

        await sidebar.apply_project_selection(
            store=self._store,
            project_files=self._project_files,
            sync_selected_layout=self._sync_selected_layout,
            sync_selected_pinout=self._sync_selected_pinout,
            set_layout_data=self._set_layout_data,
            selection_lock=self._project_selection_lock,
            project_list=project_list,
            project_state=current_project_state.model_copy(
                update={
                    "selected_project_root": selected_project_root,
                    "selected_target": selected_target,
                    "log_view_build_id": None,
                    "log_view_stage": None,
                }
            ),
        )

    async def _set_discovery_paths(self, paths: list[Path]) -> list[Project]:
        self._discovery_paths = projects.normalize_discovery_paths(paths)
        if self._discovery_paths:
            await self._workspace_metadata.watch()
        else:
            await asyncio.to_thread(self._workspace_metadata.stop)
        return await self._refresh_discovered_projects()

    async def _refresh_discovered_projects(self) -> list[Project]:
        async with self._project_discovery_lock:
            project_list = (
                await asyncio.to_thread(
                    projects.handle_get_projects,
                    self._discovery_paths,
                )
            ).projects
            next_project_state, _ = projects.normalize_project_state(
                project_list,
                cast(UiProjectState, self._store.get("project_state")),
            )
            await sidebar.apply_project_selection(
                store=self._store,
                project_files=self._project_files,
                sync_selected_layout=self._sync_selected_layout,
                sync_selected_pinout=self._sync_selected_pinout,
                set_layout_data=self._set_layout_data,
                selection_lock=self._project_selection_lock,
                project_list=project_list,
                project_state=next_project_state,
            )
            return project_list

    async def _handle_workspace_metadata_change(self, _result: object) -> None:
        await self._refresh_discovered_projects()

    # -- Action dispatch ---------------------------------------------------

    async def _dispatch(self, ws: ServerConnection, msg: dict) -> None:
        session_id = self._session_id(msg)
        action = str(msg.get("action", ""))
        if action == "closeSession":
            request_id = str(msg.get("requestId") or "")
            await self._close_session(ws, session_id)
            if request_id:
                await self._send_action_result(
                    ws,
                    session_id,
                    action,
                    request_id,
                    ok=True,
                )
            return
        if action.startswith("agent."):
            await self._handle_agent_action(session_id, msg)
            return
        if self._vscode_bridge.handles(action):
            await self._send_message(
                ws, self._vscode_bridge.forward_request(ws, session_id, msg)
            )
            return
        layout_session = self._layout_session(ws, session_id)
        if LayoutRpcSession.handles(action):
            try:
                await layout_session.dispatch(msg)
            except Exception as exc:
                log.exception("Layout RPC action failed for session %s", session_id)
                await layout_session.send_error(
                    action,
                    str(msg.get("requestId") or ""),
                    str(exc),
                )
            return
        if KicadRpcSession.handles(action):
            kicad_session = self._kicad_session(ws, session_id)
            await kicad_session.dispatch(msg)
            return

        diff_session = self._diff_session(ws, session_id)
        if DiffRpcSession.handles(action):
            try:
                await diff_session.dispatch(msg)
            except Exception as exc:
                log.exception("Diff RPC action failed for session %s", session_id)
                await diff_session.send_error(
                    action,
                    str(msg.get("requestId") or ""),
                    str(exc),
                )
            return

        match action:
            case (
                "generateThreeDModel"
                | "getPackageLayoutRenderModel"
                | "getPartFootprintRenderModel"
            ):
                request_id = str(msg.get("requestId") or "")
                if not request_id:
                    raise ValueError(f"{action} requires requestId")
                try:
                    target = projects.parse_target(msg.get("target"))
                    if action == "generateThreeDModel" and target is None:
                        raise ValueError("target is required")
                    result = (
                        await asyncio.to_thread(
                            artifacts.generate_3d_model,
                            target.root,
                            target.name,
                        )
                        if action == "generateThreeDModel"
                        else (
                            await _get_package_layout_render_model(
                                str(msg.get("url") or "")
                            )
                            if action == "getPackageLayoutRenderModel"
                            else await _get_part_footprint_render_model(
                                str(msg.get("lcsc") or "")
                            )
                        )
                    )
                except Exception as exc:
                    await self._send_action_result(
                        ws,
                        session_id,
                        action,
                        request_id,
                        ok=False,
                        error=str(exc),
                    )
                    return
                await self._send_action_result(
                    ws,
                    session_id,
                    action,
                    request_id,
                    ok=True,
                    result=(
                        result
                        if action == "generateThreeDModel"
                        else result.model_dump(mode="json")
                    ),
                )
                return

            case "getRemoteAsset":
                request_key = self._request_key(msg)
                asset = await remote_assets.proxy_remote_asset(
                    str(msg.get("url", "")),
                    str(msg.get("filename")) if msg.get("filename") else None,
                )
                self._store.set(
                    "blob_asset",
                    {
                        "action": action,
                        "requestKey": request_key,
                        **asset,
                    },
                )
                return

            case "getPartModelData":
                lcsc_id = str(msg.get("lcsc", ""))
                request_key = self._request_key(msg)
                model = await asyncio.to_thread(
                    parts.PartAssets.get_model,
                    lcsc_id,
                )
                if not model:
                    raise ValueError(f"3D model not found: {lcsc_id}")
                data, name = model
                self._store.set(
                    "blob_asset",
                    {
                        "action": action,
                        "requestKey": request_key,
                        "contentType": "model/step",
                        "filename": name,
                        "data": base64.b64encode(data).decode("ascii"),
                    },
                )
                return

            case "selectProject":
                selected_project, selected_target = projects.resolve_project_selection(
                    cast(list[Project], self._store.get("projects")),
                    str(msg.get("projectRoot") or "") or None,
                )
                await self._apply_project_selection(
                    selected_project_root=(
                        selected_project.root if selected_project else None
                    ),
                    selected_target=selected_target,
                )
                return

            case "selectTarget":
                selected_project, resolved_target = projects.resolve_target_selection(
                    cast(list[Project], self._store.get("projects")),
                    cast(
                        UiProjectState, self._store.get("project_state")
                    ).selected_project_root,
                    projects.parse_target(msg.get("target")),
                )
                await self._apply_project_selection(
                    selected_project_root=(
                        selected_project.root if selected_project else None
                    ),
                    selected_target=resolved_target,
                )
                return

            case "setLogViewCurrentId":
                self._store.merge(
                    "project_state",
                    {
                        "logViewBuildId": str(msg.get("buildId") or "") or None,
                        "logViewStage": str(msg.get("stage") or "") or None,
                    },
                )
                return

            case "resolverInfo":
                self._store.merge(
                    "core_status",
                    {
                        "uvPath": msg.get("uvPath", ""),
                        "atoBinary": msg.get("atoBinary", ""),
                        "mode": msg.get("mode", "production"),
                        "version": msg.get("version", ""),
                        "coreServerPort": msg.get("coreServerPort", 0),
                    },
                )
                return

            case "extensionSettings":
                self._store.merge(
                    "extension_settings",
                    {
                        "enableChat": msg.get("enableChat", True),
                    },
                )
                return

            case "updateExtensionSetting":
                key = msg.get("key")
                if isinstance(key, str):
                    self._store.merge("extension_settings", {key: msg.get("value")})
                return

            case "agentRuntime":
                # Legacy: extension used to push the token here.
                # Now the server owns auth state via keyring/file.
                # Just refresh in-process cache from stored session.
                from atopile.auth.runtime import set_auth_token
                from atopile.auth.session import get_stored_access_token

                token = get_stored_access_token()
                set_auth_token(token)
                return

            case "authSignIn":
                request_id = str(msg.get("requestId") or "")
                try:
                    auth_url, pending_id = await asyncio.to_thread(
                        self._start_auth_login
                    )
                    await self._send_action_result(
                        ws,
                        session_id,
                        action,
                        request_id,
                        ok=True,
                        result={"url": auth_url, "pendingId": pending_id},
                    )
                except Exception as exc:
                    log.exception("authSignIn failed")
                    await self._send_action_result(
                        ws,
                        session_id,
                        action,
                        request_id,
                        ok=False,
                        error=str(exc),
                    )
                return

            case "authSignInComplete":
                request_id = str(msg.get("requestId") or "")
                pending_id = str(msg.get("pendingId") or "")
                try:
                    session = await asyncio.to_thread(
                        self._complete_auth_login, pending_id
                    )
                    from atopile.auth.runtime import set_auth_token

                    set_auth_token(session.access_token)
                    self._push_auth_state()
                    await self._send_action_result(
                        ws,
                        session_id,
                        action,
                        request_id,
                        ok=True,
                        result={
                            "user": {
                                "id": session.user.id,
                                "name": session.user.name,
                                "email": session.user.email,
                                "imageUrl": session.user.image_url,
                            }
                            if session.user
                            else None,
                        },
                    )
                except Exception as exc:
                    log.exception("authSignInComplete failed")
                    self._pending_auth_flows.pop(pending_id, None)
                    await self._send_action_result(
                        ws,
                        session_id,
                        action,
                        request_id,
                        ok=False,
                        error=str(exc),
                    )
                return

            case "authSignOut":
                request_id = str(msg.get("requestId") or "")
                from atopile.auth.runtime import set_auth_token
                from atopile.auth.session import clear_stored_session

                clear_stored_session()
                set_auth_token(None)
                self._push_auth_state()
                await self._send_action_result(
                    ws,
                    session_id,
                    action,
                    request_id,
                    ok=True,
                )
                return

            case "authStatus":
                request_id = str(msg.get("requestId") or "")
                from atopile.auth.session import get_valid_stored_session

                session = get_valid_stored_session()
                await self._send_action_result(
                    ws,
                    session_id,
                    action,
                    request_id,
                    ok=True,
                    result={
                        "isAuthenticated": session is not None,
                        "user": {
                            "id": session.user.id,
                            "name": session.user.name,
                            "email": session.user.email,
                            "imageUrl": session.user.image_url,
                        }
                        if session and session.user
                        else None,
                    },
                )
                return

            case "authGetBalance":
                request_id = str(msg.get("requestId") or "")
                from atopile.auth.runtime import GATEWAY_BASE_URL as _GATEWAY_URL
                from atopile.auth.runtime import get_auth_token as _get_token

                token = _get_token()
                if not token:
                    await self._send_action_result(
                        ws,
                        session_id,
                        action,
                        request_id,
                        ok=False,
                        error="Not signed in",
                    )
                    return
                try:
                    import httpx

                    resp = await asyncio.to_thread(
                        lambda: httpx.get(
                            f"{_GATEWAY_URL}/account/balance",
                            headers={"Authorization": f"Bearer {token}"},
                            timeout=10,
                        )
                    )
                    resp.raise_for_status()
                    await self._send_action_result(
                        ws,
                        session_id,
                        action,
                        request_id,
                        ok=True,
                        result=resp.json(),
                    )
                except Exception as exc:
                    await self._send_action_result(
                        ws,
                        session_id,
                        action,
                        request_id,
                        ok=False,
                        error=str(exc),
                    )
                return

            case "setActiveFile":
                self._store.merge(
                    "project_state",
                    {
                        "activeFilePath": msg.get("filePath"),
                    },
                )
                return

            case "discoverProjects":
                await self._set_discovery_paths(
                    [Path(p) for p in msg.get("paths", []) if p]
                )
                return

            case "createProject":
                request_id = str(msg.get("requestId") or "")
                try:
                    request = CreateProjectRequest(
                        parent_directory=str(msg.get("parentDirectory") or ""),
                        name=(
                            str(msg.get("name"))
                            if isinstance(msg.get("name"), str) and msg.get("name")
                            else None
                        ),
                    )
                    create_result = await asyncio.to_thread(
                        projects.handle_create_project, request
                    )
                    # Ensure new project is discoverable even if it's
                    # outside the current workspace folders.
                    new_project_path = Path(create_result.project_root)
                    if not any(
                        new_project_path.is_relative_to(dp)
                        for dp in self._discovery_paths
                    ):
                        self._discovery_paths.append(new_project_path)
                        await self._workspace_metadata.watch()

                    project_list = (
                        await asyncio.to_thread(
                            projects.handle_get_projects, self._discovery_paths
                        )
                    ).projects
                    selected_project, selected_target = (
                        projects.resolve_project_selection(
                            project_list,
                            create_result.project_root,
                        )
                    )
                    await self._apply_project_selection(
                        project_list=project_list,
                        selected_project_root=(
                            selected_project.root if selected_project else None
                        ),
                        selected_target=selected_target,
                    )
                except Exception as exc:
                    log.exception("createProject failed")
                    if request_id:
                        await self._send_action_result(
                            ws,
                            session_id,
                            action,
                            request_id,
                            ok=False,
                            error=str(exc),
                        )
                    return
                if request_id:
                    await self._send_action_result(
                        ws,
                        session_id,
                        action,
                        request_id,
                        ok=True,
                        result=create_result.project_root,
                    )
                return

            case "addBuildTarget":
                request = AddBuildTargetRequest(
                    project_root=str(msg.get("projectRoot") or ""),
                    name=str(msg.get("name") or ""),
                    entry=str(msg.get("entry") or ""),
                )
                add_result = await asyncio.to_thread(
                    projects.handle_add_build_target, request
                )
                project_list, project = await asyncio.to_thread(
                    projects.refresh_project,
                    cast(list[Project], self._store.get("projects")),
                    request.project_root,
                )
                selected_target = projects.find_target(
                    project,
                    add_result.target,
                    request.project_root,
                )
                await self._apply_project_selection(
                    project_list=project_list if project is not None else None,
                    selected_project_root=request.project_root,
                    selected_target=selected_target,
                )
                return

            case "updateBuildTarget":
                request = UpdateBuildTargetRequest(
                    project_root=str(msg.get("projectRoot") or ""),
                    old_name=str(msg.get("oldName") or ""),
                    new_name=(
                        str(msg.get("newName"))
                        if isinstance(msg.get("newName"), str) and msg.get("newName")
                        else None
                    ),
                    new_entry=(
                        str(msg.get("newEntry"))
                        if isinstance(msg.get("newEntry"), str)
                        else None
                    ),
                )
                update_result = await asyncio.to_thread(
                    projects.handle_update_build_target, request
                )
                current_project_state = cast(
                    UiProjectState, self._store.get("project_state")
                )
                was_selected = projects.is_selected_target(
                    current_project_state,
                    request.project_root,
                    request.old_name,
                )
                project_list, project = await asyncio.to_thread(
                    projects.refresh_project,
                    cast(list[Project], self._store.get("projects")),
                    request.project_root,
                )
                replacement = None
                if was_selected:
                    replacement = projects.find_target(
                        project,
                        update_result.target or request.old_name,
                        request.project_root,
                    )
                if was_selected:
                    await self._apply_project_selection(
                        project_list=project_list if project is not None else None,
                        project_state=current_project_state,
                        selected_project_root=current_project_state.selected_project_root,
                        selected_target=replacement,
                    )
                elif project is not None:
                    await sidebar.apply_project_selection(
                        store=self._store,
                        project_files=self._project_files,
                        sync_selected_layout=self._sync_selected_layout,
                        sync_selected_pinout=self._sync_selected_pinout,
                        set_layout_data=self._set_layout_data,
                        selection_lock=self._project_selection_lock,
                        project_list=project_list,
                    )
                return

            case "deleteBuildTarget":
                request = DeleteBuildTargetRequest(
                    project_root=str(msg.get("projectRoot") or ""),
                    name=str(msg.get("name") or ""),
                )
                await asyncio.to_thread(projects.handle_delete_build_target, request)
                project_list, project = await asyncio.to_thread(
                    projects.refresh_project,
                    cast(list[Project], self._store.get("projects")),
                    request.project_root,
                )
                current_project_state = cast(
                    UiProjectState, self._store.get("project_state")
                )
                if projects.is_selected_target(
                    current_project_state,
                    request.project_root,
                    request.name,
                ):
                    replacement = (
                        project.targets[0] if project and project.targets else None
                    )
                    await self._apply_project_selection(
                        project_list=project_list if project is not None else None,
                        project_state=current_project_state,
                        selected_project_root=current_project_state.selected_project_root,
                        selected_target=replacement,
                    )
                elif project is not None:
                    await sidebar.apply_project_selection(
                        store=self._store,
                        project_files=self._project_files,
                        sync_selected_layout=self._sync_selected_layout,
                        sync_selected_pinout=self._sync_selected_pinout,
                        set_layout_data=self._set_layout_data,
                        selection_lock=self._project_selection_lock,
                        project_list=project_list,
                    )
                return

            case "checkEntry":
                project_root = str(msg.get("projectRoot") or "")
                entry = str(msg.get("entry") or "").strip()
                project = projects.find_project(
                    cast(list[Project], self._store.get("projects")),
                    project_root,
                )
                result = await asyncio.to_thread(
                    projects.handle_check_entry,
                    project_root,
                    entry,
                    project.targets if project else None,
                )
                self._store.set(
                    "entry_check",
                    {
                        "projectRoot": project_root or None,
                        "entry": entry,
                        **result,
                    },
                )
                return

            case "createFile":
                project_root = str(msg.get("projectRoot") or "")
                parent_relative_path = str(msg.get("parentRelativePath") or "")
                name = str(msg.get("name") or "")
                created_path = await asyncio.to_thread(
                    file_ops.create_project_file,
                    project_root,
                    parent_relative_path,
                    name,
                )
                self._store.set(
                    "file_action",
                    {
                        "action": "create_file",
                        "path": created_path,
                        "isFolder": False,
                    },
                )
                return

            case "createFolder":
                project_root = str(msg.get("projectRoot") or "")
                parent_relative_path = str(msg.get("parentRelativePath") or "")
                name = str(msg.get("name") or "")
                created_path = await asyncio.to_thread(
                    file_ops.create_project_folder,
                    project_root,
                    parent_relative_path,
                    name,
                )
                self._store.set(
                    "file_action",
                    {
                        "action": "create_folder",
                        "path": created_path,
                        "isFolder": True,
                    },
                )
                return

            case "renamePath":
                project_root = str(msg.get("projectRoot") or "")
                relative_path = str(msg.get("relativePath") or "")
                new_name = str(msg.get("newName") or "")
                renamed_path = await asyncio.to_thread(
                    file_ops.rename_project_path,
                    project_root,
                    relative_path,
                    new_name,
                )
                self._store.set(
                    "file_action",
                    {
                        "action": "rename",
                        "path": renamed_path,
                        "isFolder": Path(renamed_path).is_dir(),
                    },
                )
                return

            case "deletePath":
                project_root = str(msg.get("projectRoot") or "")
                relative_path = str(msg.get("relativePath") or "")
                deleted_path = await asyncio.to_thread(
                    file_ops.delete_project_path,
                    project_root,
                    relative_path,
                )
                self._store.set(
                    "file_action",
                    {
                        "action": "delete",
                        "path": deleted_path,
                        "isFolder": False,
                    },
                )
                return

            case "duplicatePath":
                project_root = str(msg.get("projectRoot") or "")
                relative_path = str(msg.get("relativePath") or "")
                duplicated_path = await asyncio.to_thread(
                    file_ops.duplicate_project_path,
                    project_root,
                    relative_path,
                )
                self._store.set(
                    "file_action",
                    {
                        "action": "duplicate",
                        "path": duplicated_path,
                        "isFolder": Path(duplicated_path).is_dir(),
                    },
                )
                return

            case "startBuild":
                request = BuildRequest(
                    project_root=msg.get("projectRoot", ""),
                    targets=msg.get("targets", []),
                    frozen=msg.get("frozen", False),
                    include_targets=msg.get("includeTargets", []),
                    exclude_targets=msg.get("excludeTargets", []),
                )
                enqueued_builds = builds.handle_start_build(request)
                if enqueued_builds:
                    self._store.merge(
                        "project_state",
                        {
                            "logViewBuildId": None,
                            "logViewStage": None,
                        },
                    )
                await self._push_builds()
                return

            case "cancelBuild":
                build_id = str(msg.get("buildId") or "")
                await asyncio.to_thread(_build_queue.cancel_build, build_id)
                return

            case "clearLogs":
                await asyncio.to_thread(delete_log_storage)
                await asyncio.to_thread(initialize_log_storage)
                self._store.merge(
                    "project_state",
                    {
                        "logViewBuildId": None,
                        "logViewStage": None,
                    },
                )
                self._store.set("selected_build", None)
                self._store.set("selected_build_in_progress", False)
                self._store.set("recent_builds_data", {"builds": []})
                await self._push_builds()
                project_state = cast(UiProjectState, self._store.get("project_state"))
                await self._push_builds_by_project(
                    project_state.selected_project_root,
                    project_state.selected_target,
                )
                return

            case "getManufacturingArtifacts":
                request_id = str(msg.get("requestId") or "")
                if not request_id:
                    raise ValueError("getManufacturingArtifacts requires requestId")
                try:
                    project_root = str(msg.get("projectRoot", ""))
                    target_data = msg.get("target", {})
                    target_name = str(target_data.get("name", ""))
                    build_dir = Path(project_root) / "manufacturing" / target_name
                    mfg_files: list[dict[str, object]] = []
                    if build_dir.is_dir():
                        for entry in sorted(build_dir.iterdir()):
                            if entry.is_file():
                                mfg_files.append(
                                    {
                                        "name": entry.name,
                                        "path": str(entry),
                                        "sizeBytes": entry.stat().st_size,
                                    }
                                )
                    await self._send_action_result(
                        ws,
                        session_id,
                        action,
                        request_id,
                        ok=True,
                        result={"artifacts": mfg_files},
                    )
                except Exception as exc:
                    await self._send_action_result(
                        ws,
                        session_id,
                        action,
                        request_id,
                        ok=False,
                        error=str(exc),
                    )
                return

            case "getPackagesSummary":
                project_root = msg.get("projectRoot", "")
                root = Path(project_root) if project_root else None
                packages_result = await asyncio.to_thread(
                    packages.handle_packages_summary, root
                )
                self._store.set("packages_summary", packages_result)
                return

            case "showPackageDetails":
                await sidebar.show_package_details(
                    self._store,
                    msg.get("projectRoot") or None,
                    str(msg.get("packageId", "")),
                )
                return

            case "closeSidebarDetails":
                self._store.set("sidebar_details", sidebar.clear())
                return

            case "installPackage":
                project_root = Path(msg.get("projectRoot", ""))
                pkg_id = msg.get("packageId", "")
                version = msg.get("version")

                # Set initial progress state synchronously before the thread starts
                _install_current = cast(
                    UiSidebarDetails, self._store.get("sidebar_details")
                )
                if _install_current.view == "package":
                    self._store.set(
                        "sidebar_details",
                        _install_current.model_copy(
                            update={
                                "package": _install_current.package.model_copy(
                                    update={
                                        "sync_progress": UiPackageSyncProgress(
                                            stage="starting",
                                            message=f"Installing {pkg_id}...",
                                            completed=0,
                                            total=1,
                                        )
                                    }
                                )
                            }
                        ),
                    )

                loop = asyncio.get_running_loop()

                def _install_progress(
                    stage: str, message: str, completed: int, total: int
                ) -> None:
                    current = cast(UiSidebarDetails, self._store.get("sidebar_details"))
                    if current.view == "package":
                        asyncio.run_coroutine_threadsafe(
                            self._set_sync_progress(
                                pkg_id, stage, message, completed, total
                            ),
                            loop,
                        )

                try:
                    await asyncio.to_thread(
                        packages.install_package_to_project,
                        project_root,
                        pkg_id,
                        version,
                        on_progress=_install_progress,
                    )
                except Exception as exc:
                    await sidebar.show_package_details(
                        self._store,
                        str(project_root),
                        str(pkg_id),
                        action_error=str(exc),
                    )
                    return
                packages_result = await asyncio.to_thread(
                    packages.handle_packages_summary, project_root
                )
                self._store.set("packages_summary", packages_result)
                current = cast(UiSidebarDetails, self._store.get("sidebar_details"))
                package_state = current.package
                if (
                    current.view == "package"
                    and package_state.package_id == pkg_id
                    and same_path(package_state.project_root, str(project_root))
                ):
                    await sidebar.show_package_details(
                        self._store,
                        str(project_root),
                        str(pkg_id),
                    )
                return

            case "removePackage":
                project_root = Path(msg.get("projectRoot", ""))
                pkg_id = msg.get("packageId", "")

                # Set initial progress state synchronously before the thread starts
                _remove_current = cast(
                    UiSidebarDetails, self._store.get("sidebar_details")
                )
                if _remove_current.view == "package":
                    self._store.set(
                        "sidebar_details",
                        _remove_current.model_copy(
                            update={
                                "package": _remove_current.package.model_copy(
                                    update={
                                        "sync_progress": UiPackageSyncProgress(
                                            stage="starting",
                                            message=f"Removing {pkg_id}...",
                                            completed=0,
                                            total=1,
                                        )
                                    }
                                )
                            }
                        ),
                    )

                loop = asyncio.get_running_loop()

                def _remove_progress(
                    stage: str, message: str, completed: int, total: int
                ) -> None:
                    current = cast(UiSidebarDetails, self._store.get("sidebar_details"))
                    if current.view == "package":
                        asyncio.run_coroutine_threadsafe(
                            self._set_sync_progress(
                                pkg_id, stage, message, completed, total
                            ),
                            loop,
                        )

                try:
                    await asyncio.to_thread(
                        packages.remove_package_from_project,
                        project_root,
                        pkg_id,
                        on_progress=_remove_progress,
                    )
                except Exception as exc:
                    await sidebar.show_package_details(
                        self._store,
                        str(project_root),
                        str(pkg_id),
                        action_error=str(exc),
                    )
                    return
                packages_result = await asyncio.to_thread(
                    packages.handle_packages_summary, project_root
                )
                self._store.set("packages_summary", packages_result)
                current = cast(UiSidebarDetails, self._store.get("sidebar_details"))
                package_state = current.package
                if (
                    current.view == "package"
                    and package_state.package_id == pkg_id
                    and same_path(package_state.project_root, str(project_root))
                ):
                    await sidebar.show_package_details(
                        self._store,
                        str(project_root),
                        str(pkg_id),
                    )
                return

            case "syncPackages":
                project_root = Path(msg.get("projectRoot", ""))
                force = bool(msg.get("force", False))
                request_id = str(msg.get("requestId") or "")

                loop = asyncio.get_running_loop()

                def _sync_progress(
                    stage: str, message: str, completed: int, total: int
                ) -> None:
                    current = cast(UiSidebarDetails, self._store.get("sidebar_details"))
                    if current.view == "package":
                        pkg_id = current.package.package_id or ""
                        asyncio.run_coroutine_threadsafe(
                            self._set_sync_progress(
                                pkg_id, stage, message, completed, total
                            ),
                            loop,
                        )

                try:
                    await asyncio.to_thread(
                        packages.sync_packages_for_project,
                        project_root,
                        force=force,
                        on_progress=_sync_progress,
                    )
                except Exception as exc:
                    if request_id:
                        await self._send_action_result(
                            ws,
                            session_id,
                            action,
                            request_id,
                            ok=False,
                            error=str(exc),
                        )
                        return
                    raise
                packages_result = await asyncio.to_thread(
                    packages.handle_packages_summary, project_root
                )
                self._store.set("packages_summary", packages_result)
                if request_id:
                    await self._send_action_result(
                        ws,
                        session_id,
                        action,
                        request_id,
                        ok=True,
                    )
                return

            case "getStdlib":
                type_filter = msg.get("typeFilter")
                search = msg.get("search")
                stdlib_result = await asyncio.to_thread(
                    stdlib.handle_get_stdlib, type_filter, search
                )
                self._store.set("stdlib_data", stdlib_result)
                return

            case "getStructure":
                project_root = str(msg.get("projectRoot") or "") or None
                asyncio.create_task(
                    asyncio.to_thread(
                        sidebar.refresh_project_structure_data,
                        self._store,
                        project_root,
                    )
                )
                return

            case "searchParts":
                project_root = str(msg.get("projectRoot") or "") or None
                query = str(msg.get("query") or "")
                installed_only = bool(msg.get("installedOnly"))
                limit = int(msg.get("limit", 50))
                request_id = str(msg.get("requestId") or "")
                await self._start_parts_search(
                    ws,
                    session_id=session_id,
                    action=action,
                    request_id=request_id,
                    project_root=project_root,
                    query=query,
                    installed_only=installed_only,
                    limit=limit,
                )
                return

            case "showPartDetails":
                seed_payload = msg.get("seed")
                await parts.PartUiState.show_details(
                    self._store,
                    project_root=msg.get("projectRoot") or None,
                    identifier=msg.get("identifier"),
                    lcsc=msg.get("lcsc"),
                    installed=bool(msg.get("installed")),
                    seed=UiPartData.model_validate(seed_payload)
                    if seed_payload
                    else None,
                    action_error=msg.get("actionError"),
                )
                return

            case "generateChatTitle":
                request_id = str(msg.get("requestId") or "")
                if not request_id:
                    return
                message_text = str(msg.get("message") or "")
                if not message_text:
                    await self._send_action_result(
                        ws,
                        session_id,
                        action,
                        request_id,
                        ok=False,
                        error="No message provided",
                    )
                    return
                try:
                    title = await self._generate_chat_title(message_text)
                    await self._send_action_result(
                        ws,
                        session_id,
                        action,
                        request_id,
                        ok=True,
                        result={"title": title},
                    )
                except Exception as exc:
                    await self._send_action_result(
                        ws,
                        session_id,
                        action,
                        request_id,
                        ok=False,
                        error=str(exc),
                    )
                return

            case "lookupPart":
                request_id = str(msg.get("requestId") or "")
                lcsc = msg.get("lcsc", "")
                try:
                    part = await asyncio.to_thread(
                        parts.PartCatalog.get_details,
                        lcsc,
                    )
                    if part and request_id:
                        await self._send_action_result(
                            ws,
                            session_id,
                            action,
                            request_id,
                            ok=True,
                            result={
                                "lcsc": part.lcsc,
                                "mpn": part.mpn,
                                "manufacturer": part.manufacturer,
                                "description": part.description,
                                "stock": part.stock,
                                "unitCost": part.unit_cost,
                                "package": getattr(part, "package", None),
                            },
                        )
                    elif request_id:
                        await self._send_action_result(
                            ws,
                            session_id,
                            action,
                            request_id,
                            ok=False,
                            error=f"Part {lcsc} not found",
                        )
                except Exception as exc:
                    if request_id:
                        await self._send_action_result(
                            ws,
                            session_id,
                            action,
                            request_id,
                            ok=False,
                            error=str(exc),
                        )
                return

            case "installPart":
                project_root = str(msg.get("projectRoot") or "")
                lcsc = str(msg.get("lcsc") or "")
                request_id = str(msg.get("requestId") or "")
                try:
                    result = await parts.ProjectParts.install(
                        self._store,
                        project_root=project_root,
                        lcsc=lcsc,
                    )
                except Exception as exc:
                    if request_id:
                        await self._send_action_result(
                            ws,
                            session_id,
                            action,
                            request_id,
                            ok=False,
                            error=str(exc),
                        )
                        return
                    raise
                if request_id:
                    await self._send_action_result(
                        ws,
                        session_id,
                        action,
                        request_id,
                        ok=True,
                        result=result,
                    )
                return

            case "convertPartToPackage":
                project_root = str(msg.get("projectRoot") or "")
                lcsc = str(msg.get("lcsc") or "")
                request_id = str(msg.get("requestId") or "")
                try:
                    result = await parts.ProjectParts.convert_to_package(
                        self._store,
                        project_root=project_root,
                        lcsc=lcsc,
                    )
                except Exception as exc:
                    if request_id:
                        await self._send_action_result(
                            ws,
                            session_id,
                            action,
                            request_id,
                            ok=False,
                            error=str(exc),
                        )
                        return
                    raise
                if project_root:
                    packages_result = await asyncio.to_thread(
                        packages.handle_packages_summary, Path(project_root)
                    )
                    self._store.set("packages_summary", packages_result)
                if request_id:
                    await self._send_action_result(
                        ws,
                        session_id,
                        action,
                        request_id,
                        ok=True,
                        result=result,
                    )
                return

            case "uninstallPart":
                project_root = str(msg.get("projectRoot") or "")
                lcsc = str(msg.get("lcsc") or "")
                request_id = str(msg.get("requestId") or "")
                try:
                    result = await parts.ProjectParts.uninstall(
                        self._store,
                        project_root=project_root,
                        lcsc=lcsc,
                    )
                except Exception as exc:
                    if request_id:
                        await self._send_action_result(
                            ws,
                            session_id,
                            action,
                            request_id,
                            ok=False,
                            error=str(exc),
                        )
                        return
                    raise
                if request_id:
                    await self._send_action_result(
                        ws,
                        session_id,
                        action,
                        request_id,
                        ok=True,
                        result=result,
                    )
                return

            case "showMigrationDetails":
                project_root = str(msg.get("projectRoot", ""))
                if project_root:
                    await sidebar.show_migration_details(self._store, project_root)
                return

            case "runMigration" | "migrateProjectSteps":
                project_root = str(msg.get("projectRoot", ""))
                selected_steps = [str(step) for step in msg.get("steps", []) if step]
                if not project_root:
                    return
                self._store.set(
                    "sidebar_details",
                    sidebar.start_migration_run(
                        self._store.get("sidebar_details"),
                        selected_steps,
                    ),
                )
                loop = asyncio.get_running_loop()

                for step_id in selected_steps:

                    def _make_progress_cb(sid: str):  # noqa: E301
                        def _cb(
                            stage: str, message: str, completed: int, total: int
                        ) -> None:
                            asyncio.run_coroutine_threadsafe(
                                self._set_migration_step_progress(
                                    sid, stage, message, completed, total
                                ),
                                loop,
                            )

                        return _cb

                    try:
                        await migrations.get_step(step_id).run(
                            Path(project_root),
                            on_progress=_make_progress_cb(step_id),
                        )
                        error = None
                    except Exception as exc:
                        log.exception("Migration step %s failed", step_id)
                        error = str(exc)
                    self._store.set(
                        "sidebar_details",
                        sidebar.finish_migration_step(
                            self._store.get("sidebar_details"),
                            step_id,
                            error=error,
                        ),
                    )
                final_state, success = sidebar.complete_migration_run(
                    self._store.get("sidebar_details")
                )
                self._store.set("sidebar_details", final_state)
                project_list, project = await asyncio.to_thread(
                    projects.refresh_project,
                    cast(list[Project], self._store.get("projects")),
                    project_root,
                )
                if project is not None:
                    self._store.set("projects", project_list)
                self._store.set(
                    "sidebar_details",
                    sidebar.update_migration_project_state(
                        self._store.get("sidebar_details"),
                        cast(list[Project], self._store.get("projects")),
                        project_root,
                    ),
                )
                return

            case "getVariables":
                project_root = msg.get("projectRoot", "")
                try:
                    target = projects.parse_target(msg.get("target"))
                    variables = await asyncio.to_thread(
                        artifacts.read_artifact,
                        target.root if target else project_root,
                        target.name if target else "default",
                        ".variables.ato.json",
                    )
                    project_state = cast(
                        UiProjectState, self._store.get("project_state")
                    )
                    if not projects.same_selection(
                        project_root or None,
                        target,
                        project_state.selected_project_root,
                        project_state.selected_target,
                    ):
                        return
                    self._store.set("variables_data", variables or {"nodes": []})
                except Exception:
                    log.exception("getVariables failed")
                    self._store.set(
                        "variables_data",
                        {"nodes": [], "error": "Failed to load variables"},
                    )
                return

            case "getBom":
                project_root = msg.get("projectRoot", "")
                try:
                    target = projects.parse_target(msg.get("target"))
                    bom = await asyncio.to_thread(
                        artifacts.read_artifact,
                        target.root if target else project_root,
                        target.name if target else "default",
                        ".bom.json",
                    )
                    project_state = cast(
                        UiProjectState, self._store.get("project_state")
                    )
                    if not projects.same_selection(
                        project_root or None,
                        target,
                        project_state.selected_project_root,
                        project_state.selected_target,
                    ):
                        return
                    self._store.set(
                        "bom_data",
                        {
                            "projectRoot": project_root or None,
                            "target": target.model_dump() if target else None,
                            **(
                                bom
                                or {
                                    "components": [],
                                    "totalQuantity": 0,
                                    "uniqueParts": 0,
                                    "estimatedCost": None,
                                    "outOfStock": 0,
                                }
                            ),
                        },
                    )
                except Exception:
                    log.exception("getBom failed")
                    self._store.set(
                        "bom_data",
                        {
                            "projectRoot": project_root or None,
                            "target": None,
                            "components": [],
                            "totalQuantity": 0,
                            "uniqueParts": 0,
                            "estimatedCost": None,
                            "outOfStock": 0,
                            "error": "Failed to load BOM",
                            "errorTraceback": traceback.format_exc(),
                        },
                    )
                return

            case "getStackup":
                project_root = msg.get("projectRoot", "")
                try:
                    target = projects.parse_target(msg.get("target"))
                    stackup = await asyncio.to_thread(
                        artifacts.read_artifact,
                        target.root if target else project_root,
                        target.name if target else "default",
                        ".stackup.json",
                    )
                    project_state = cast(
                        UiProjectState, self._store.get("project_state")
                    )
                    if not projects.same_selection(
                        project_root or None,
                        target,
                        project_state.selected_project_root,
                        project_state.selected_target,
                    ):
                        return
                    self._store.set(
                        "stackup_data",
                        {
                            "projectRoot": project_root or None,
                            "target": target.model_dump() if target else None,
                            **(
                                stackup
                                or {
                                    "layers": [],
                                    "layerCount": 0,
                                }
                            ),
                        },
                    )
                except Exception:
                    log.exception("getStackup failed")
                    self._store.set(
                        "stackup_data",
                        {
                            "projectRoot": project_root or None,
                            "target": None,
                            "layers": [],
                            "layerCount": 0,
                            "error": "Failed to load stackup data",
                            "errorTraceback": traceback.format_exc(),
                        },
                    )
                return

            case "getBuildsByProject":
                project_root = msg.get("projectRoot") or None
                target = projects.parse_target(msg.get("target"))
                limit = int(msg.get("limit", 50))
                project_state = cast(UiProjectState, self._store.get("project_state"))
                if not projects.same_selection(
                    project_root,
                    target,
                    project_state.selected_project_root,
                    project_state.selected_target,
                ):
                    return
                await self._push_builds_by_project(project_root, target, limit)
                return

            case "getRecentBuilds":
                limit = int(msg.get("limit", 100))
                result = await asyncio.to_thread(
                    builds.get_recent_builds,
                    limit,
                )
                self._store.set(
                    "recent_builds_data",
                    {
                        "builds": [build.model_dump() for build in result],
                    },
                )
                return

            case "fetchLcscParts":
                lcsc_ids = [str(value) for value in msg.get("lcscIds", []) if value]
                project_root = msg.get("projectRoot") or None
                try:
                    target = projects.parse_target(msg.get("target"))
                    current_lcsc_parts = cast(
                        dict[str, Any], self._store.dump("lcsc_parts_data")
                    )
                    current_parts = current_lcsc_parts.get("parts", {})
                    current_target = projects.parse_target(
                        current_lcsc_parts.get("target")
                    )
                    if not projects.same_selection(
                        current_lcsc_parts.get("projectRoot"),
                        current_target,
                        project_root,
                        target,
                    ):
                        current_parts = {}
                    result = await asyncio.to_thread(
                        parts.PartCatalog.get_lcsc_parts,
                        lcsc_ids,
                    )
                    project_state = cast(
                        UiProjectState, self._store.get("project_state")
                    )
                    if not projects.same_selection(
                        project_root,
                        target,
                        project_state.selected_project_root,
                        project_state.selected_target,
                    ):
                        return
                    self._store.set(
                        "lcsc_parts_data",
                        {
                            "projectRoot": project_root,
                            "target": target.model_dump() if target else None,
                            "parts": {**current_parts, **result.get("parts", {})},
                            "loadingIds": [],
                        },
                    )
                except Exception:
                    log.exception("fetchLcscParts failed")
                    self._store.set(
                        "lcsc_parts_data",
                        {
                            "projectRoot": project_root,
                            "target": None,
                            "parts": {},
                            "loadingIds": [],
                            "error": "Failed to fetch LCSC parts",
                        },
                    )
                return

            case "subscribeLogs":
                request = UiBuildLogRequest.model_validate(msg)
                build_id = request.build_id.strip()
                if not build_id:
                    payload = UiLogsErrorMessage(
                        error="buildId is required"
                    ).model_dump(mode="json")
                    payload["sessionId"] = session_id
                    await self._send_message(ws, payload)
                    return
                old_task = self._log_tasks.setdefault(ws, {}).pop(session_id, None)
                if old_task:
                    old_task.cancel()
                query = {
                    "session_id": session_id,
                    "build_id": build_id,
                    "stage": request.stage,
                    "log_levels": request.log_levels,
                    "audience": request.audience,
                    "count": request.count or 1000,
                }
                task = asyncio.create_task(self._log_stream_loop(ws, query))
                self._log_tasks.setdefault(ws, {})[session_id] = task
                return

            case "unsubscribeLogs":
                old_task = self._log_tasks.setdefault(ws, {}).pop(session_id, None)
                if old_task:
                    old_task.cancel()
                return

            # -- Autolayout --------------------------------------------------
            # Thin dispatch: mutate service state, then push store.

            case "getAutolayoutData":
                project_root = str(msg.get("projectRoot") or "") or None
                svc = self._autolayout_service
                svc.set_project_root(project_root)
                # Always ensure the layout viewer shows the project
                # file — _sync_selected_layout is idempotent.
                await self._sync_selected_layout()
                if not project_root:
                    return
                svc.begin_loading()
                try:
                    await asyncio.to_thread(svc.list_jobs, project_root)
                    svc.end_loading()
                except Exception:
                    log.exception("getAutolayoutData failed")
                    svc.end_loading(error="Failed to load autolayout data")
                request_id = str(msg.get("requestId") or "")
                if request_id:
                    await self._send_action_result(
                        ws, session_id, action, request_id, ok=True
                    )
                return

            case "submitAutolayoutJob":
                project_root = str(msg.get("projectRoot") or "") or None
                build_target = str(msg.get("buildTarget") or "") or None
                job_type = JobType(msg.get("jobType") or "Placement")
                timeout = int(msg.get("timeoutMinutes") or 10)
                if not project_root or not build_target:
                    return
                _, selected_target = self._selected_project_context()
                layout_path = (
                    str(Path(selected_target.pcb_path).resolve())
                    if selected_target and selected_target.pcb_path
                    else None
                )
                svc = self._autolayout_service
                svc.begin_submitting()
                try:
                    # Persist any active preview first so the upload
                    # matches what the user sees in the viewer.
                    if svc.ui.is_previewing:
                        await asyncio.to_thread(svc.apply_active_preview)
                        await self._sync_selected_layout()
                    await asyncio.to_thread(
                        svc.start_job,
                        project_root,
                        build_target,
                        job_type=job_type,
                        timeout_minutes=timeout,
                        layout_path=layout_path,
                    )
                    svc.end_submitting()
                except Exception:
                    log.exception("submitAutolayoutJob failed")
                    svc.end_submitting(error="Failed to submit job")
                return

            case "refreshAutolayoutJob":
                job_id = str(msg.get("jobId") or "")
                if not job_id:
                    return
                svc = self._autolayout_service
                try:
                    await asyncio.to_thread(svc.refresh_job, job_id)
                except Exception:
                    log.exception("refreshAutolayoutJob failed")
                    svc.set_error("Failed to refresh job")
                return

            case "selectAutolayoutCandidate":
                job_id = str(msg.get("jobId") or "")
                candidate_id = str(msg.get("candidateId") or "")
                if not job_id or not candidate_id:
                    return
                svc = self._autolayout_service
                try:
                    await asyncio.to_thread(svc.select_candidate, job_id, candidate_id)
                except Exception:
                    log.exception("selectAutolayoutCandidate failed")
                    svc.set_error("Failed to select candidate")
                return

            case "applyAutolayoutCandidate":
                job_id = str(msg.get("jobId") or "")
                candidate_id = str(msg.get("candidateId") or "") or None
                if not job_id:
                    return
                svc = self._autolayout_service
                svc.begin_submitting()
                try:
                    await asyncio.to_thread(svc.apply_candidate, job_id, candidate_id)
                    svc.end_submitting()
                    svc.end_preview()
                    await self._sync_selected_layout()
                except Exception:
                    log.exception("applyAutolayoutCandidate failed")
                    svc.end_submitting(error="Failed to apply candidate")
                return

            case "previewAutolayoutCandidate":
                job_id = str(msg.get("jobId") or "")
                candidate_id = str(msg.get("candidateId") or "")
                if not job_id or not candidate_id:
                    return
                svc = self._autolayout_service
                try:
                    result = await asyncio.to_thread(
                        svc.begin_preview, job_id, candidate_id
                    )
                    await self._show_preview_in_layout_viewer(result.preview_path)
                except Exception:
                    log.exception("previewAutolayoutCandidate failed")
                    svc.set_error("Failed to preview candidate")
                return

            case "syncSelectedLayout":
                svc = self._autolayout_service
                svc.end_preview()
                await self._sync_selected_layout()
                return

            case "getAutolayoutPreflight":
                project_root = str(msg.get("projectRoot") or "") or None
                build_target = str(msg.get("buildTarget") or "") or None
                if not project_root or not build_target:
                    return
                svc = self._autolayout_service
                await asyncio.to_thread(
                    svc.compute_and_store_preflight, project_root, build_target
                )
                return

            case "cancelAutolayoutJob":
                job_id = str(msg.get("jobId") or "")
                if not job_id:
                    return
                svc = self._autolayout_service
                try:
                    await asyncio.to_thread(svc.cancel_job, job_id)
                except Exception:
                    log.exception("cancelAutolayoutJob failed")
                    svc.set_error("Failed to cancel job")
                return

            case _:
                raise ValueError(f"Unknown action: {action}")
        return

    # -- Log streaming -----------------------------------------------------

    async def _log_stream_loop(self, ws: ServerConnection, query: dict) -> None:
        """Poll SQLite for new logs and push to the client until cancelled."""
        last_id = 0
        try:
            # Send initial batch immediately
            last_id = await self._push_log_stream(ws, query, last_id)
            while True:
                await asyncio.sleep(STREAM_POLL_INTERVAL)
                last_id = await self._push_log_stream(ws, query, last_id)
        except asyncio.CancelledError:
            pass
        except websockets.ConnectionClosed:
            pass

    async def _push_log_stream(
        self, ws: ServerConnection, query: dict, after_id: int
    ) -> int:
        """Fetch new logs from SQLite and push to the client. Returns new cursor."""
        session_id = query.get("session_id", EXTENSION_SESSION_ID)
        build_id = query.get("build_id", "")
        stage = query.get("stage") or None
        log_levels = query.get("log_levels") or None
        audience = query.get("audience") or None
        count = query.get("count", 1000)

        logs, new_last_id = await asyncio.to_thread(
            Logs.fetch_chunk,
            build_id,
            stage=stage,
            levels=log_levels,
            audience=audience,
            after_id=after_id,
            count=count,
        )

        if logs:
            payload = UiLogsStreamMessage(
                build_id=build_id,
                stage=stage,
                logs=[UiLogEntry.model_validate(log) for log in logs],
                last_id=new_last_id,
            ).model_dump(mode="json")
            payload["sessionId"] = session_id
            await self._send_message(ws, payload)
            return new_last_id

        return after_id

    # -- Build queue integration -------------------------------------------

    def bind_build_queue(self, build_queue: BuildQueue) -> None:
        """Register as the listener for build queue changes."""
        loop = asyncio.get_running_loop()

        def _on_change(build_id: str, event_type: str) -> None:
            asyncio.run_coroutine_threadsafe(self._push_builds(), loop)

        def _on_completed(build: Any) -> None:
            build_target_name = (
                build.target.name
                if getattr(build, "target", None) is not None
                and getattr(build.target, "name", None)
                else str(getattr(build, "target", "") or "default")
            )
            self._agent.handle_build_completed(
                {
                    "project_root": build.project_root or "",
                    "build_id": build.build_id or "",
                    "target": build_target_name,
                    "status": (
                        build.status.value
                        if hasattr(build.status, "value")
                        else str(build.status)
                    ),
                    "warnings": build.warnings or 0,
                    "errors": build.errors or 0,
                    "error": build.error,
                    "elapsed_seconds": build.elapsed_seconds or 0.0,
                }
            )
            # Eagerly recompute preflight after PCB changes
            svc = self._autolayout_service
            svc.compute_and_store_preflight(build.project_root or "", build_target_name)

        build_queue.on_change = _on_change
        build_queue.on_completed = _on_completed
        build_queue.start()

    async def _push_builds_by_project(
        self,
        project_root: str | None,
        target: Any,
        limit: int = 50,
    ) -> None:
        """Fetch and push historical builds for a project/target selection."""
        result = await asyncio.to_thread(
            builds.get_builds_by_project,
            project_root,
            target,
            limit,
        )
        self._store.set(
            "builds_by_project_data",
            {
                "projectRoot": project_root,
                "target": target.model_dump() if target else None,
                "limit": limit,
                "builds": [build.model_dump() for build in result],
            },
        )

    async def _push_builds(self) -> None:
        self._store.set(
            "current_builds",
            [build.model_dump() for build in builds.get_active_builds()],
        )
        self._store.set(
            "previous_builds",
            [build.model_dump() for build in builds.get_finished_builds()],
        )
        self._store.set(
            "queue_builds",
            [build.model_dump() for build in builds.get_queue_builds()],
        )
        _, selected_target = projects.resolve_selection(
            cast(list[Project], self._store.get("projects")),
            cast(UiProjectState, self._store.get("project_state")),
        )
        self._set_selected_build(selected_target)
        await asyncio.gather(
            self._sync_selected_layout(),
            self._sync_selected_pinout(),
        )

    async def _set_sync_progress(
        self,
        pkg_id: str,
        stage: str,
        message: str,
        completed: int,
        total: int,
    ) -> None:
        """Update sidebar_details sync_progress from the main event loop."""
        current = cast(UiSidebarDetails, self._store.get("sidebar_details"))
        if current.view == "package":
            self._store.set(
                "sidebar_details",
                current.model_copy(
                    update={
                        "package": current.package.model_copy(
                            update={
                                "sync_progress": UiPackageSyncProgress(
                                    stage=stage,
                                    message=message,
                                    completed=completed,
                                    total=total,
                                )
                            }
                        )
                    }
                ),
            )

    async def _set_migration_step_progress(
        self,
        step_id: str,
        stage: str,
        message: str,
        completed: int,
        total: int,
    ) -> None:
        """Update sync_progress on a running migration step."""
        current = cast(UiSidebarDetails, self._store.get("sidebar_details"))
        if current.view == "migration":
            self._store.set(
                "sidebar_details",
                sidebar.update_migration_step_progress(
                    current, step_id, stage, message, completed, total
                ),
            )

    # -- Broadcasting ------------------------------------------------------

    def _on_store_change(self, field_name: str, value: Any, prev: Any) -> None:
        asyncio.run_coroutine_threadsafe(
            self._broadcast_state(field_name, value), self._loop
        )

    def _set_selected_build(self, target: ResolvedBuildTarget | None) -> None:
        selected_build = builds.get_selected_build(target)
        self._store.set("selected_build", selected_build)
        self._store.set(
            "selected_build_in_progress",
            builds.is_build_in_progress(selected_build),
        )

    async def _send_state(
        self,
        ws: ServerConnection,
        session_id: str,
        field_name: str,
        data: Any,
    ) -> None:
        await self._send_message(
            ws,
            {
                "type": "state",
                "sessionId": session_id,
                "key": self._store.wire_key(field_name),
                "data": data,
            },
        )

    async def _send_message(
        self,
        ws: ServerConnection,
        payload: dict[str, Any],
    ) -> None:
        self._log_websocket_event(
            "send",
            session_id=payload.get("sessionId"),
            type=payload.get("type"),
            action=payload.get("action"),
            request_id=payload.get("requestId"),
            ok=payload.get("ok"),
            key=payload.get("key"),
            step=payload.get("step"),
            success=payload.get("success"),
            last_id=payload.get("last_id"),
            log_count=(
                len(payload["logs"]) if isinstance(payload.get("logs"), list) else None
            ),
        )
        await ws.send(json.dumps(payload))

    async def _generate_chat_title(self, message: str) -> str:
        """Generate a short chat title from the first user message via LLM."""
        return await self._agent.generate_title(message)

    async def _send_action_result(
        self,
        ws: ServerConnection,
        session_id: str,
        action: str,
        request_id: str,
        *,
        ok: bool,
        result: Any = None,
        error: str | None = None,
    ) -> None:
        await self._send_message(
            ws,
            {
                "type": "action_result",
                "sessionId": session_id,
                "requestId": request_id,
                "action": action,
                "ok": ok,
                "result": result,
                "error": error,
            },
        )

    async def _release_client(self, ws: ServerConnection) -> None:
        self._clients.discard(ws)
        self._subscriptions.pop(ws, None)
        self._cancel_parts_search(ws)
        self._parts_search_tokens.pop(ws, None)
        for layout_session in self._layout_sessions.pop(ws, {}).values():
            await layout_session.close()
        for diff_session in self._diff_sessions.pop(ws, {}).values():
            await diff_session.close()
        for kicad_session in self._kicad_sessions.pop(ws, {}).values():
            await kicad_session.close()
        self._vscode_bridge.remove_client(ws)
        for task in self._log_tasks.pop(ws, {}).values():
            task.cancel()

    async def _close_session(
        self,
        ws: ServerConnection,
        session_id: str,
    ) -> None:
        self._subscriptions.setdefault(ws, {}).pop(session_id, None)
        if layout_session := self._layout_sessions.setdefault(ws, {}).pop(
            session_id, None
        ):
            await layout_session.close()
        if log_task := self._log_tasks.setdefault(ws, {}).pop(session_id, None):
            log_task.cancel()
        self._vscode_bridge.remove_session(ws, session_id)

    def _kicad_session(
        self,
        ws: ServerConnection,
        session_id: str,
    ) -> KicadRpcSession:
        sessions = self._kicad_sessions.setdefault(ws, {})
        session = sessions.get(session_id)
        if session is not None:
            return session

        async def send(payload: dict[str, Any]) -> None:
            await self._send_message(ws, {"sessionId": session_id, **payload})

        session = KicadRpcSession(self._kicad_service, send)
        sessions[session_id] = session
        return session

    def _layout_session(
        self,
        ws: ServerConnection,
        session_id: str,
    ) -> LayoutRpcSession:
        sessions = self._layout_sessions.setdefault(ws, {})
        session = sessions.get(session_id)
        if session is not None:
            return session

        async def send(payload: dict[str, Any]) -> None:
            await self._send_message(ws, {"sessionId": session_id, **payload})

        session = LayoutRpcSession(layout_service, send)
        sessions[session_id] = session
        return session

    def _diff_session(
        self,
        ws: ServerConnection,
        session_id: str,
    ) -> DiffRpcSession:
        sessions = self._diff_sessions.setdefault(ws, {})
        session = sessions.get(session_id)
        if session is not None:
            return session

        async def send(payload: dict[str, Any]) -> None:
            await self._send_message(ws, {"sessionId": session_id, **payload})

        session = DiffRpcSession(
            diff_service,
            send,
            autolayout_service=self._autolayout_service,
        )
        sessions[session_id] = session
        return session

    def _set_layout_data(
        self,
        project_root: str | None,
        target: ResolvedBuildTarget | None,
        *,
        path: str | None = None,
        loading: bool = False,
        error: str | None = None,
        bump_revision: bool = False,
        read_only: bool = False,
    ) -> None:
        current = cast(UiLayoutData, self._store.get("layout_data"))
        same_context = projects.same_selection(
            current.project_root,
            current.target,
            project_root,
            target,
        ) and same_path(current.path, path)

        if path is None:
            revision = 0
        elif bump_revision:
            revision = current.revision + 1 if same_context else 1
        else:
            revision = current.revision if same_context else 1

        self._store.set(
            "layout_data",
            UiLayoutData(
                project_root=project_root,
                target=target,
                path=path,
                revision=revision,
                loading=loading,
                error=error,
                read_only=read_only,
            ),
        )

    async def _handle_layout_message(self, _message: WsMessage) -> None:
        """Update layout_data revision on edits.

        Preflight recomputation is handled by the autolayout service's
        own layout listener — no need to trigger it here.
        """
        try:
            project_state = cast(UiProjectState, self._store.get("project_state"))
            selected_project_root = project_state.selected_project_root
            selected_target = project_state.selected_target
            current_path = layout_service.current_path
            if (
                selected_project_root is None
                or selected_target is None
                or current_path is None
                or not same_path(str(current_path), selected_target.pcb_path)
            ):
                return
            self._set_layout_data(
                selected_project_root,
                selected_target,
                path=str(current_path),
                error=None,
                bump_revision=True,
            )
        except Exception:
            log.exception("_handle_layout_message failed")

    def _selected_project_context(
        self,
    ) -> tuple[str | None, ResolvedBuildTarget | None]:
        project_list = cast(list[Project], self._store.get("projects"))
        project_state = cast(UiProjectState, self._store.get("project_state"))
        selected_project, selected_target = projects.resolve_selection(
            project_list,
            project_state,
        )
        return selected_project.root if selected_project else None, selected_target

    async def _sync_selected_layout(self) -> None:
        selected_project_root, selected_target = self._selected_project_context()

        if selected_project_root is None or selected_target is None:
            await layout_service.clear()
            self._set_layout_data(selected_project_root, selected_target)
            return

        layout_path = Path(selected_target.pcb_path).resolve()
        current_layout = cast(UiLayoutData, self._store.get("layout_data"))
        if not layout_path.exists():
            await layout_service.clear()
            self._set_layout_data(selected_project_root, selected_target)
            return

        if (
            current_layout.error is None
            and projects.same_selection(
                current_layout.project_root,
                current_layout.target,
                selected_project_root,
                selected_target,
            )
            and same_path(current_layout.path, str(layout_path))
            and same_path(
                str(layout_service.current_path)
                if layout_service.current_path
                else None,
                str(layout_path),
            )
        ):
            return

        # Set the path immediately so subscribers (e.g. the layout panel)
        # see the correct file before the async open completes.
        self._set_layout_data(
            selected_project_root,
            selected_target,
            path=str(layout_path),
            loading=True,
        )

        try:
            model = await layout_service.open(layout_path)
        except FileNotFoundError:
            await layout_service.clear()
            self._set_layout_data(selected_project_root, selected_target)
            return
        except Exception as exc:
            await layout_service.clear()
            self._set_layout_data(
                selected_project_root,
                selected_target,
                error=str(exc),
            )
            log.exception(
                "Failed to load selected layout for %s",
                selected_project_root,
            )
            return

        self._set_layout_data(
            selected_project_root,
            selected_target,
            path=str(layout_path),
        )
        await layout_service.broadcast(WsMessage(type="layout_updated", model=model))

    async def _sync_selected_pinout(self) -> None:
        selected_project_root, selected_target = self._selected_project_context()

        if selected_project_root is None or selected_target is None:
            self._store.set(
                "pinout_data",
                UiPinoutData(
                    project_root=selected_project_root,
                    target=selected_target,
                ),
            )
            return

        pinout = await asyncio.to_thread(
            artifacts.get_pinout,
            selected_project_root,
            selected_target.name if selected_target else "default",
        )
        self._store.set(
            "pinout_data",
            UiPinoutData(
                project_root=selected_project_root,
                target=selected_target,
                components=pinout or [],
            ),
        )

    async def _broadcast_state(self, field_name: str, data: Any) -> None:
        wire_key = self._store.wire_key(field_name)
        dead: list[ServerConnection] = []
        for ws, sessions in list(self._subscriptions.items()):
            for session_id, keys in sessions.items():
                try:
                    if wire_key in keys:
                        await self._send_state(ws, session_id, field_name, data)
                except websockets.ConnectionClosed:
                    dead.append(ws)
                    break
        for ws in dead:
            await self._release_client(ws)

    async def broadcast_state(self, field_name: str, data: Any) -> None:
        self._store.set(field_name, data)

    def _session_id(self, msg: dict[str, Any]) -> str:
        session_id = msg.get("sessionId")
        if isinstance(session_id, str) and session_id:
            return session_id
        return EXTENSION_SESSION_ID

    async def _broadcast_agent_message(self, payload: dict[str, object]) -> None:
        dead: list[ServerConnection] = []
        for ws, sessions in list(self._subscriptions.items()):
            for session_id in sessions:
                if session_id == EXTENSION_SESSION_ID:
                    continue
                try:
                    await self._send_message(
                        ws,
                        {
                            **payload,
                            "sessionId": session_id,
                        },
                    )
                except websockets.ConnectionClosed:
                    dead.append(ws)
                    break
        for ws in dead:
            await self._release_client(ws)

    async def _handle_agent_action(
        self,
        session_id: str,
        msg: dict[str, Any],
    ) -> None:
        action = str(msg.get("action", ""))
        agent_session_id = str(
            msg.get("agentSessionId") or msg.get("sessionIdValue") or ""
        )
        try:
            match action:
                case "agent.createSession":
                    req = AgentCreateSessionRequest.model_validate(msg)
                    session = self._agent.create_session(
                        project_root=Path(req.project_root),
                    )
                    if req.initial_message:
                        task = asyncio.create_task(
                            self._run_agent_turn(
                                session,
                                req.initial_message,
                                error_context=req.error_context,
                            ),
                        )
                        self._run_tasks[session.session_id] = task

                case "agent.createRun":
                    req = AgentCreateRunRequest.model_validate(msg)
                    session = self._agent.get_session(agent_session_id)
                    if session is None:
                        raise ValueError(f"Session not found: {agent_session_id}")
                    task = asyncio.create_task(
                        self._run_agent_turn(session, req.message),
                    )
                    self._run_tasks[session.session_id] = task

                case "agent.cancelRun":
                    session = self._agent.get_session(agent_session_id)
                    if session is not None:
                        session.request_stop()
                        task = self._run_tasks.get(agent_session_id)
                        if task is not None:
                            task.cancel()

                case "agent.steerRun":
                    session = self._agent.get_session(agent_session_id)
                    steer_msg = str(msg.get("message", "")).strip()
                    if session is not None and steer_msg:
                        session.steer(steer_msg)

                case "agent.setModel":
                    req = AgentSetModelRequest.model_validate(msg)
                    session = self._agent.get_session(agent_session_id)
                    if session is None:
                        raise ValueError(f"Session not found: {agent_session_id}")
                    try:
                        new_model = ModelId(req.model_id)
                    except ValueError as exc:
                        raise ValueError(f"Unknown model id: {req.model_id!r}") from exc
                    session.set_model(new_model)

                case _:
                    log.warning("Unknown agent action: %s", action)

            self._push_agent_state()

        except Exception as exc:
            log.warning(
                "Agent action failed action=%s session=%s error=%s",
                action,
                agent_session_id,
                exc,
            )
