"""Agent session — the public API for interacting with the agent.

Usage::

    agent = Agent()
    agent.load_sessions()  # restore from sqlite

    session = agent.create_session(project_root=Path("my-project"))

    async for event in session.send_message("Design a motor driver"):
        match event:
            case Thinking(): ...
            case ToolStart(name=n): ...
            case ToolEnd(name=n, ok=ok): ...
            case Done(text=t): ...

    title = await session.generate_title("Design a motor driver")
    agent.save_sessions()  # persist to sqlite
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

from atopile.agent.providers import make_provider
from atopile.agent.providers.base import (
    LLMProvider,
    LLMResponse,
    ModelId,
    StreamDone,
    StreamReasoningDelta,
    StreamTextDelta,
    TokenUsage,
)
from atopile.agent.providers.openai import OpenAIProvider
from atopile.agent.scope import Scope
from atopile.agent.skills import load_skill
from atopile.agent.tools import create_registry
from atopile.agent.tools.checklist import Checklist
from atopile.agent.tools.registry import ToolRegistry
from atopile.logging import get_logger

log = get_logger(__name__)


# ── Events ────────────────────────────────────────────────────────────


@dataclass
class ToolTrace:
    name: str
    label: str
    args: dict[str, Any]
    ok: bool
    result: dict[str, Any]


@dataclass
class Thinking:
    """Model is processing."""

    pass


@dataclass
class ToolStart:
    """A tool call is about to execute."""

    name: str
    label: str
    args: dict[str, Any]


@dataclass
class ToolEnd:
    """A tool call has completed."""

    name: str
    ok: bool
    trace: ToolTrace


@dataclass
class TitleGenerated:
    """Auto-generated session title from the first message."""

    title: str


@dataclass
class TextDelta:
    """Partial text chunk from the model."""

    delta: str


@dataclass
class ReasoningDelta:
    """Partial reasoning / thinking chunk from the model."""

    delta: str


@dataclass
class Done:
    """Turn is complete."""

    text: str
    traces: list[ToolTrace]
    usage: TokenUsage


TurnEvent = (
    Thinking | TextDelta | ReasoningDelta | ToolStart | ToolEnd | TitleGenerated | Done
)


# ── Background event helpers ─────────────────────────────────────────


class _BackgroundEvents:
    """Manage fire-and-forget async tasks that produce TurnEvents."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[TurnEvent | None]] = {}
        self._ready: list[TurnEvent] = []

    def spawn(self, coro: Any, key: str | None = None) -> None:
        """Run a coroutine in the background. Result collected via drain()."""
        k = key or f"_anon_{id(coro)}"
        self._tasks[k] = asyncio.create_task(self._wrap(k, coro))

    def debounce(self, key: str, coro: Any, delay: float) -> None:
        """Schedule a coroutine with a delay, cancelling any prior with same key."""
        prev = self._tasks.pop(key, None)
        if prev and not prev.done():
            prev.cancel()

        async def _delayed() -> TurnEvent | None:
            await asyncio.sleep(delay)
            return await coro

        self._tasks[key] = asyncio.create_task(self._wrap(key, _delayed()))

    def drain(self) -> list[TurnEvent]:
        """Collect any completed background events."""
        for key in list(self._tasks):
            if self._tasks[key].done():
                del self._tasks[key]
        events = list(self._ready)
        self._ready.clear()
        return events

    def cancel_all(self) -> None:
        """Cancel pending tasks, collect any that already completed."""
        for task in self._tasks.values():
            if not task.done():
                task.cancel()
        for task in self._tasks.values():
            if task.done() and not task.cancelled():
                try:
                    result = task.result()
                    if result is not None:
                        self._ready.append(result)
                except Exception:
                    pass
        self._tasks.clear()

    async def _wrap(self, key: str, coro: Any) -> TurnEvent | None:
        try:
            result = await coro
            if result is not None:
                self._ready.append(result)
            return result
        except asyncio.CancelledError:
            return None
        except Exception:
            log.debug("Background event %s failed", key, exc_info=True)
            return None


# ── Session ───────────────────────────────────────────────────────────


class AgentSession:
    """One conversation with the agent.

    Wraps a provider (LLM state) and registry (tools). Use
    send_message() to run a turn, generate_title() for chat titles.
    Tracks UI messages for display and persistence.
    """

    def __init__(
        self,
        *,
        session_id: str,
        project_root: Path,
        provider: LLMProvider,
        model: ModelId,
        registry: ToolRegistry,
        skill_ids: list[str],
        max_tool_loops: int = 240,
        max_turn_seconds: float = 7_200.0,
        messages: list[dict[str, Any]] | None = None,
        checklist: Checklist | None = None,
        active_skills: list[str] | None = None,
        created_at: float | None = None,
        agent: "Agent | None" = None,
    ) -> None:
        self.session_id = session_id
        self.project_root = project_root
        self._provider = provider
        self._model = model
        self._registry = registry
        self._skill_ids = skill_ids
        self._active_skills = active_skills or list(skill_ids)
        self._max_tool_loops = max_tool_loops
        self._max_turn_seconds = max_turn_seconds
        self._messages: list[dict[str, Any]] = messages or []
        self._checklist = checklist or Checklist()
        self._created_at = created_at or time.time()
        self._updated_at = self._created_at
        # Back-reference to the owning Agent so tools can route callbacks
        # (e.g. build completion) back through shared Agent state.
        self._agent = agent

        self._active_run_id: str | None = None
        self._error: str | None = None
        self._stop_requested: bool = False
        self._steering_messages: list[str] = []

    # ── Model selection ───────────────────────────────────────────────

    @property
    def model(self) -> ModelId:
        return self._model

    def set_model(self, new: ModelId) -> None:
        """Swap the session's model.

        Within a provider kind, mutate in place so response-chain state
        (OpenAI) or history (Anthropic) carries forward. Across kinds,
        build a fresh provider with a clean chain — ``_messages`` (UI
        history) is preserved either way.
        """
        if self._active_run_id is not None:
            raise RuntimeError("Cannot change model while a run is active.")

        if new.provider_kind == self._model.provider_kind:
            self._provider.model = new  # type: ignore[attr-defined]
        else:
            self._provider = make_provider(new)
        self._model = new
        self._updated_at = time.time()

    # ── Public API ────────────────────────────────────────────────────

    async def send_message(
        self,
        message: str,
        *,
        error_context: dict[str, Any] | None = None,
    ) -> AsyncIterator[TurnEvent]:
        """Run one user turn, yielding events as they happen."""
        msg_id = self._start_run(message, error_context=error_context)
        scope = Scope(
            allowed_roots=[self.project_root],
            checklist=self._checklist,
            active_skills=list(self._active_skills),
            wait_for_build=(
                self._agent.wait_for_build if self._agent is not None else None
            ),
        )
        bg = _BackgroundEvents()

        # Generate title for new sessions (first message)
        is_first_message = (
            sum(1 for m in self._messages if m.get("role") == "user") <= 1
        )
        if is_first_message:
            bg.spawn(self._generate_title_event(message))

        yield Thinking()
        response: LLMResponse | None = None
        accumulated_text = ""
        accumulated_reasoning = ""
        async for event in self._stream_model(
            [{"role": "user", "content": message}], scope
        ):
            if isinstance(event, StreamTextDelta):
                accumulated_text += event.delta
                self._update_pending_message(msg_id, content=accumulated_text)
                yield TextDelta(delta=event.delta)
            elif isinstance(event, StreamReasoningDelta):
                accumulated_reasoning = _append_reasoning(
                    accumulated_reasoning, event.delta
                )
                self._update_pending_message(msg_id, reasoning=accumulated_reasoning)
                yield ReasoningDelta(delta=event.delta)
            elif isinstance(event, StreamDone):
                response = event.response
        assert response is not None
        usage = TokenUsage()
        _accumulate(usage, response.usage)

        traces: list[ToolTrace] = []
        started_at = time.monotonic()
        loops = 0

        while self._should_continue(response, loops, started_at):
            loops += 1
            accumulated_text = ""

            outputs: list[dict[str, Any]] = []
            for call in response.tool_calls:
                yield ToolStart(
                    name=call.name,
                    label=self._registry.label(call.name),
                    args=call.arguments,
                )
                trace = await self._execute(call.name, call.arguments, scope)
                traces.append(trace)
                outputs.append(_tool_output(call.id, trace.result))
                if scope.checklist is not None:
                    self._checklist = scope.checklist
                self._update_pending_message(msg_id, traces=traces)
                yield ToolEnd(name=call.name, ok=trace.ok, trace=trace)

            # Yield any ready background events (title)
            for event in bg.drain():
                yield event

            await self._upload_pending_documents(scope)
            for steering_msg in self._drain_steering():
                outputs.append({"role": "user", "content": steering_msg})
            yield Thinking()
            # Start a fresh reasoning segment for the next model turn.
            accumulated_reasoning = _start_new_reasoning_segment(accumulated_reasoning)
            response = None
            async for event in self._stream_model(outputs, scope):
                if isinstance(event, StreamTextDelta):
                    accumulated_text += event.delta
                    self._update_pending_message(msg_id, content=accumulated_text)
                    yield TextDelta(delta=event.delta)
                elif isinstance(event, StreamReasoningDelta):
                    accumulated_reasoning = _append_reasoning(
                        accumulated_reasoning, event.delta
                    )
                    self._update_pending_message(
                        msg_id, reasoning=accumulated_reasoning
                    )
                    yield ReasoningDelta(delta=event.delta)
                elif isinstance(event, StreamDone):
                    response = event.response
            assert response is not None
            _accumulate(usage, response.usage)

            # Drain background events that completed during the model call
            for event in bg.drain():
                yield event

        # Drain any remaining background events
        for event in bg.drain():
            yield event

        self._finish_run(msg_id, response.text, traces, scope)
        yield Done(text=response.text, traces=traces, usage=usage)

    def request_stop(self) -> None:
        """Signal the current run to stop at the next safe boundary."""
        self._stop_requested = True

    @property
    def is_running(self) -> bool:
        """True while a turn is in flight."""
        return self._active_run_id is not None

    def abort_run(self, *, reason: str = "Interrupted") -> None:
        """Clean up after a run that was cancelled mid-flight.

        Called from the task wrapper's ``finally`` block when ``send_message``
        doesn't reach ``_finish_run`` on its own (e.g. ``task.cancel()``
        interrupts the stream). Marks any pending assistant message as done,
        clears the active-run flag, resets the stop request, and repairs
        provider-internal history so the next turn is valid. Safe to call
        even if the run had already finished normally.
        """
        had_work = self._active_run_id is not None or any(
            m.get("pending") for m in self._messages
        )

        for msg in reversed(self._messages):
            if msg.get("pending"):
                msg["pending"] = False
                if not (msg.get("content") or "").strip():
                    msg["content"] = f"{reason} before completion."
                break

        # Always give the provider a chance to repair its internal state —
        # even a completed turn can leave orphan tool_use blocks if the
        # session was cancelled between tool-result batches.
        try:
            self._provider.abort_pending(reason=reason)
        except Exception:
            log.debug(
                "provider.abort_pending failed for session %s",
                self.session_id,
                exc_info=True,
            )

        self._active_run_id = None
        self._stop_requested = False
        if had_work:
            self._updated_at = time.time()

    def steer(self, message: str) -> None:
        """Inject guidance into the current turn.

        Takes effect on the next model call.
        """
        self._steering_messages.append(message)

    def _utility_provider(self) -> Any:
        """Create a throwaway provider for stateless utility calls."""
        from atopile.agent.providers.openai import OpenAIProvider

        return OpenAIProvider(model=ModelId.GPT_5_4_NANO)

    async def generate_title(self, message: str) -> str:
        """Generate a short chat title from a user message."""
        resp = await self._utility_provider().complete(
            instructions=load_skill("title"),
            messages=[{"role": "user", "content": message[:500]}],
            max_output_tokens=24,
        )
        return resp.text

    # ── State ─────────────────────────────────────────────────────────

    def ui_state(self) -> dict[str, Any]:
        """Build session state for the store.

        Snake_case keys — the store's pydantic model handles
        camelCase conversion when serializing to the frontend.
        """
        messages = self._messages
        return {
            "session_id": self.session_id,
            "project_root": str(self.project_root),
            "model": self._model.value,
            "messages": messages,
            "checklist": {"items": self._checklist.summary()}
            if self._checklist.items
            else None,
            "active_run_id": self._active_run_id,
            "active_run_status": "running" if self._active_run_id else None,
            "active_run_stop_requested": self._stop_requested,
            "error": self._error,
            "created_at": self._created_at,
            "updated_at": self._updated_at,
        }

    def snapshot(self) -> dict[str, Any]:
        """Serialize session state for persistence to sqlite."""
        return {
            "session_id": self.session_id,
            "project_root": str(self.project_root),
            "model": self._model.value,
            "provider_state": self._provider.snapshot(),
            "messages": self._messages,
            "checklist": self._checklist.snapshot(),
            "active_skills": self._active_skills,
            "created_at": self._created_at,
            "updated_at": self._updated_at,
        }

    # ── Private ───────────────────────────────────────────────────────

    def _start_run(
        self,
        message: str,
        *,
        error_context: dict[str, Any] | None = None,
    ) -> str:
        """Record user message, create assistant placeholder, return its ID."""
        self._active_run_id = uuid.uuid4().hex
        self._stop_requested = False

        user_msg: dict[str, Any] = {
            "id": uuid.uuid4().hex,
            "role": "user",
            "content": message,
            "pending": False,
        }
        if error_context:
            user_msg["error_context"] = error_context
        self._messages.append(user_msg)

        msg_id = uuid.uuid4().hex
        self._messages.append(
            {
                "id": msg_id,
                "role": "assistant",
                "content": "",
                "pending": True,
            }
        )
        return msg_id

    def _finish_run(
        self,
        msg_id: str,
        text: str,
        traces: list[ToolTrace],
        scope: Scope,
    ) -> None:
        """Finalize assistant message, persist state, clear run."""
        if scope.checklist is not None:
            self._checklist = scope.checklist
        self._active_skills = list(scope.active_skills)

        for msg in reversed(self._messages):
            if msg.get("id") == msg_id:
                msg["content"] = text
                msg["pending"] = False
                msg["toolTraces"] = [
                    {
                        "name": t.name,
                        "label": t.label,
                        "ok": t.ok,
                        "args": t.args,
                        "result": t.result,
                    }
                    for t in traces
                ]
                break

        self._active_run_id = None
        self._updated_at = time.time()

    def _update_pending_message(
        self,
        msg_id: str,
        *,
        content: str | None = None,
        reasoning: str | None = None,
        traces: list[ToolTrace] | None = None,
    ) -> None:
        """Update the pending assistant message with live state."""
        for msg in reversed(self._messages):
            if msg.get("id") == msg_id:
                if content is not None:
                    msg["content"] = content
                if reasoning is not None:
                    msg["reasoning"] = reasoning
                if traces is not None:
                    msg["toolTraces"] = [
                        {
                            "name": t.name,
                            "label": t.label,
                            "ok": t.ok,
                            "args": t.args,
                            "result": t.result,
                        }
                        for t in traces
                    ]
                break

    def _should_continue(
        self,
        response: Any,
        loops: int,
        started_at: float,
    ) -> bool:
        if not response.tool_calls:
            return False
        if self._stop_requested:
            return False
        if loops >= self._max_tool_loops:
            return False
        if time.monotonic() - started_at >= self._max_turn_seconds:
            return False
        return True

    async def _stream_model(
        self,
        messages: list[dict[str, Any]],
        scope: Scope,
    ) -> AsyncIterator[StreamTextDelta | StreamReasoningDelta | StreamDone]:
        """Stream an LLM call, yielding text/reasoning deltas and a final StreamDone.

        Instructions are built from scope.active_skills and sent on every
        call because the OpenAI Responses API does not carry them over
        when using previous_response_id.
        """
        async for event in self._provider.stream(
            messages=messages,
            instructions=scope.build_instructions(),
            tools=self._registry.definitions(),
        ):
            # Forward text, reasoning, and completion to the session layer.
            # Tool call deltas are handled internally by the provider.
            if isinstance(event, (StreamTextDelta, StreamReasoningDelta, StreamDone)):
                yield event

    async def _upload_pending_documents(self, scope: Scope) -> None:
        """Upload any files tools marked as searchable."""
        for path in scope.drain_pending_documents():
            if not path.exists():
                continue
            try:
                await self._provider.add_searchable_document(
                    path.name,
                    path.read_bytes(),
                )
                log.info("Uploaded searchable document %s", path.name)
            except Exception:
                log.debug("Failed to upload %s", path, exc_info=True)

    def _drain_steering(self) -> list[str]:
        msgs = list(self._steering_messages)
        self._steering_messages.clear()
        return msgs

    async def _generate_title_event(self, message: str) -> TurnEvent | None:
        """Generate a title and return it as an event."""
        try:
            title = await self.generate_title(message)
            return TitleGenerated(title=title)
        except Exception:
            log.debug("Title generation failed", exc_info=True)
            return None

    async def _execute(
        self,
        name: str,
        args: dict[str, Any],
        scope: Scope,
    ) -> ToolTrace:
        label = self._registry.label(name)
        try:
            result = await self._registry.execute(name, args, scope)
            return ToolTrace(name=name, label=label, args=args, ok=True, result=result)
        except Exception as exc:
            return ToolTrace(
                name=name,
                label=label,
                args=args,
                ok=False,
                result={"error": str(exc), "error_type": type(exc).__name__},
            )


# ── Agent ─────────────────────────────────────────────────────────────


class Agent:
    """Top-level agent. Creates sessions, manages persistence."""

    def __init__(
        self,
        *,
        model: ModelId = ModelId.CLAUDE_OPUS_4_7,
        skill_ids: list[str] | None = None,
        max_tool_loops: int = 240,
        max_turn_seconds: float = 7_200.0,
    ) -> None:
        self._model = model
        self._skill_ids = skill_ids or ["agent", "ato-language"]
        self._max_tool_loops = max_tool_loops
        self._max_turn_seconds = max_turn_seconds
        self._registry = create_registry()
        self._sessions: dict[str, AgentSession] = {}
        # Futures keyed by build_id. Tools call ``wait_for_build`` to await
        # a build started via the build queue; ``handle_build_completed``
        # (invoked by the websocket layer when the queue callback fires)
        # resolves them with the serialized result.
        self._build_waiters: dict[str, asyncio.Future[dict[str, Any]]] = {}
        # Short-lived cache of recent build completions. Protects against
        # the race where ``handle_build_completed`` fires before the tool
        # has installed its waiter (very fast builds, or callback-thread
        # vs event-loop ordering). Entries are served to late waiters and
        # evicted on a size/TTL budget.
        self._recent_build_results: dict[str, tuple[float, dict[str, Any]]] = {}
        self._recent_build_grace_seconds: float = 60.0
        self._recent_build_max_entries: int = 128

    # ── Async build callbacks ─────────────────────────────────────────

    def wait_for_build(self, build_id: str) -> asyncio.Future[dict[str, Any]]:
        """Return a Future that resolves when ``build_id`` completes.

        Called by the ``build_run`` tool after queueing a build. If the
        build already completed within the grace window, returns a
        pre-resolved future so the tool doesn't hang. Otherwise installs
        (or returns) a pending future that ``handle_build_completed``
        resolves when the queue callback fires.
        """
        loop = asyncio.get_event_loop()

        # Race fix: if completion landed before the waiter was installed,
        # serve from the recent-results cache.
        cached = self._recent_build_results.pop(build_id, None)
        if cached is not None:
            _, payload = cached
            future: asyncio.Future[dict[str, Any]] = loop.create_future()
            future.set_result(payload)
            return future

        existing = self._build_waiters.get(build_id)
        if existing is not None and not existing.done():
            return existing

        future = loop.create_future()
        self._build_waiters[build_id] = future
        return future

    def handle_build_completed(self, payload: dict[str, Any]) -> None:
        """Resolve any futures waiting on this build.

        Invoked from the websocket's build-queue ``on_completed`` callback.
        ``payload`` is the shape emitted there: ``build_id``, ``status``,
        ``warnings``, ``errors``, ``error``, ``elapsed_seconds``,
        ``project_root``, ``target``. If no waiter is installed yet, the
        payload is cached so a subsequent ``wait_for_build`` can serve it.
        """
        build_id = str(payload.get("build_id") or "")
        if not build_id:
            return
        snapshot = dict(payload)
        future = self._build_waiters.pop(build_id, None)
        if future is not None and not future.done():
            future.set_result(snapshot)
            return
        # No waiter registered yet — stash in the grace cache.
        self._recent_build_results[build_id] = (time.time(), snapshot)
        self._evict_stale_build_results()

    def _evict_stale_build_results(self) -> None:
        """Drop grace-cache entries past their TTL or exceeding the cap."""
        if not self._recent_build_results:
            return
        cutoff = time.time() - self._recent_build_grace_seconds
        stale = [
            bid for bid, (ts, _) in self._recent_build_results.items() if ts < cutoff
        ]
        for bid in stale:
            self._recent_build_results.pop(bid, None)
        # Hard cap — drop oldest to stay under budget.
        if len(self._recent_build_results) > self._recent_build_max_entries:
            ordered = sorted(
                self._recent_build_results.items(),
                key=lambda kv: kv[1][0],
            )
            overflow = len(ordered) - self._recent_build_max_entries
            for bid, _ in ordered[:overflow]:
                self._recent_build_results.pop(bid, None)

    # ── Session management ────────────────────────────────────────────

    @property
    def sessions(self) -> dict[str, AgentSession]:
        return self._sessions

    def create_session(
        self,
        project_root: Path,
        *,
        session_id: str | None = None,
        model: ModelId | None = None,
    ) -> AgentSession:
        """Create a new session for a project."""
        chosen = model or self._model
        session = AgentSession(
            session_id=session_id or uuid.uuid4().hex,
            project_root=project_root,
            provider=make_provider(chosen),
            model=chosen,
            registry=self._registry,
            skill_ids=self._skill_ids,
            max_tool_loops=self._max_tool_loops,
            max_turn_seconds=self._max_turn_seconds,
            agent=self,
        )
        self._sessions[session.session_id] = session
        return session

    def get_session(self, session_id: str) -> AgentSession | None:
        """Look up a session by ID."""
        return self._sessions.get(session_id)

    # ── UI state ──────────────────────────────────────────────────────

    async def generate_title(self, message: str) -> str:
        """Generate a chat title using a throwaway provider."""
        provider = OpenAIProvider(model=ModelId.GPT_5_4_NANO)
        resp = await provider.complete(
            model=ModelId.GPT_5_4_NANO,
            instructions=load_skill("title"),
            messages=[{"role": "user", "content": message[:500]}],
            max_output_tokens=24,
        )
        return resp.text

    def ui_state(self, *, project_root: Path | None = None) -> dict[str, Any]:
        """Build the full agent state dict for the frontend."""
        sessions = self._sessions.values()
        if project_root is not None:
            sessions = [s for s in sessions if s.project_root == project_root]
        sessions = sorted(sessions, key=lambda s: s._updated_at, reverse=True)
        return {
            "loaded": True,
            "sessions": [s.ui_state() for s in sessions],
            "default_model": self._model.value,
        }

    # ── Persistence ───────────────────────────────────────────────────

    def load_sessions(self, project_root: Path) -> None:
        """Restore sessions for a project from sqlite."""
        try:
            from atopile.model.sqlite import AgentSessions

            AgentSessions.init_db()
            rows = AgentSessions.load_all(project_root=str(project_root))
        except Exception:
            log.exception("Failed to load persisted agent sessions")
            return

        loaded = 0
        for row in rows:
            if row["session_id"] in self._sessions:
                continue  # already loaded
            try:
                session = self._restore_session(row)
                # Mark any interrupted runs
                for msg in session._messages:
                    if msg.get("pending"):
                        msg["pending"] = False
                        if not msg.get("content"):
                            msg["content"] = "Run interrupted before completion."
                self._sessions[session.session_id] = session
                loaded += 1
            except Exception:
                log.warning(
                    "Failed to restore session %s",
                    row.get("session_id"),
                    exc_info=True,
                )

        if loaded:
            log.info("Restored %d agent sessions for %s", loaded, project_root)

    def save_sessions(self) -> None:
        """Persist all sessions to sqlite."""
        snapshots = [s.snapshot() for s in self._sessions.values()]
        if not snapshots:
            return
        try:
            from atopile.model.sqlite import AgentSessions

            AgentSessions.init_db()
            AgentSessions.upsert_many(snapshots)
        except Exception:
            log.exception("Failed to persist agent sessions")

    def _restore_session(self, row: dict[str, Any]) -> AgentSession:
        """Restore a single session from a sqlite row."""
        raw_model = row.get("model")
        try:
            model = ModelId(raw_model) if raw_model else self._model
        except ValueError:
            model = self._model
        provider = make_provider(model)
        provider.restore(row.get("provider_state", {}))
        checklist_data = row.get("checklist")
        checklist = (
            Checklist.restore(checklist_data)
            if isinstance(checklist_data, list)
            else None
        )
        return AgentSession(
            session_id=row["session_id"],
            project_root=Path(row["project_root"]),
            provider=provider,
            model=model,
            registry=self._registry,
            skill_ids=self._skill_ids,
            max_tool_loops=self._max_tool_loops,
            max_turn_seconds=self._max_turn_seconds,
            messages=row.get("messages", []),
            checklist=checklist,
            active_skills=row.get("active_skills"),
            created_at=row.get("created_at"),
            agent=self,
        )


# ── Helpers ───────────────────────────────────────────────────────────


# Separator inserted between reasoning segments from successive model
# calls within one turn. Rendered as a subtle divider in the UI.
REASONING_SEGMENT_SEP = "\n\n⸻\n\n"


def _append_reasoning(existing: str, delta: str) -> str:
    if not existing:
        return delta
    return existing + delta


def _start_new_reasoning_segment(existing: str) -> str:
    if not existing.strip():
        return existing
    if existing.endswith(REASONING_SEGMENT_SEP):
        return existing
    return existing + REASONING_SEGMENT_SEP


def _tool_output(call_id: str, result: dict[str, Any]) -> dict[str, Any]:
    output = json.dumps(result, ensure_ascii=False, default=str)
    if len(output) > 10_000:
        output = output[:9_997] + "..."
    return {"type": "function_call_output", "call_id": call_id, "output": output}


def _accumulate(total: TokenUsage, increment: TokenUsage | None) -> None:
    if increment is None:
        return
    total.input_tokens += increment.input_tokens
    total.output_tokens += increment.output_tokens
    total.total_tokens += increment.total_tokens
    total.reasoning_tokens += increment.reasoning_tokens
    total.cached_input_tokens += increment.cached_input_tokens
