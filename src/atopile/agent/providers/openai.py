"""OpenAI provider — thin wrapper over the OpenAI Responses API.

Only vendor-specific logic lives here: client setup, API calls,
response normalization, and internal state (response chains, vector
stores). The runner never inspects provider state.
"""

from __future__ import annotations

import json
from io import BytesIO
from typing import Any, AsyncIterator

from openai import AsyncOpenAI
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseFunctionCallArgumentsDeltaEvent,
    ResponseFunctionToolCall,
    ResponseOutputItemAddedEvent,
    ResponseReasoningItem,
    ResponseReasoningSummaryTextDeltaEvent,
    ResponseTextDeltaEvent,
)

from atopile.agent import gateway
from atopile.agent.providers.base import (
    LLMResponse,
    ModelId,
    StreamDone,
    StreamEvent,
    StreamReasoningDelta,
    StreamTextDelta,
    StreamToolCallDelta,
    StreamToolCallStart,
    TokenUsage,
    ToolCall,
)
from atopile.logging import get_logger

log = get_logger(__name__)


class OpenAIProvider:
    """Vendor-specific wrapper for the OpenAI Responses API.

    One instance per session. Owns response chain, vector stores, and
    uploaded file cache. Use snapshot()/restore() to persist across
    reloads. For stateless utility calls (titles, summaries), create
    a separate throwaway instance.
    """

    def __init__(self, model: ModelId, *, timeout: float = 120.0) -> None:
        self._model = model
        self._timeout = timeout
        self._last_response_id: str | None = None
        self._vector_store_ids: list[str] = []
        self._uploaded_files: dict[str, dict[str, str]] = {}
        # Tool calls emitted by the most recent response. The session is
        # expected to send ``function_call_output`` back for each of these
        # on the next turn; if not (because the run was cancelled), we use
        # this list to synthesize stubs and keep the chain valid.
        self._pending_call_ids: list[str] = []
        # Synthesized ``function_call_output`` items queued up to prepend
        # to the next outgoing payload. Populated by ``abort_pending`` and
        # drained by ``_build_payload``.
        self._queued_tool_stubs: list[dict[str, Any]] = []

    # ── Properties ────────────────────────────────────────────────────

    @property
    def model(self) -> ModelId:
        return self._model

    @model.setter
    def model(self, value: ModelId) -> None:
        self._model = value

    # ── Core API ──────────────────────────────────────────────────────

    def _client(self) -> AsyncOpenAI:
        return gateway.openai_client(timeout=self._timeout)

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        instructions: str,
        tools: list[dict[str, Any]] | None = None,
        model: ModelId | None = None,
        max_output_tokens: int | None = None,
    ) -> LLMResponse:
        """Send messages to the model and return a normalized response.

        Each provider instance is one session's conversation. Every call
        chains from the previous response and updates the chain ID.
        """
        payload = _build_payload(
            model=model or self._model,
            instructions=instructions,
            messages=self._prepend_queued_stubs(messages),
            tools=tools,
            vector_store_ids=self._vector_store_ids,
            previous_response_id=self._last_response_id,
            max_output_tokens=max_output_tokens,
        )

        raw = await self._client().responses.create(**payload)
        response = _normalize(raw)

        if response.id:
            self._last_response_id = response.id
        self._pending_call_ids = [c.id for c in response.tool_calls]

        return response

    async def stream(
        self,
        *,
        messages: list[dict[str, Any]],
        instructions: str,
        tools: list[dict[str, Any]] | None = None,
        model: ModelId | None = None,
        max_output_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a response, yielding events as they arrive.

        Yields ``StreamTextDelta`` for text chunks,
        ``StreamToolCallStart`` / ``StreamToolCallDelta`` for tool calls,
        and ``StreamDone`` with the fully assembled response at the end.
        """
        payload = _build_payload(
            model=model or self._model,
            instructions=instructions,
            messages=self._prepend_queued_stubs(messages),
            tools=tools,
            vector_store_ids=self._vector_store_ids,
            previous_response_id=self._last_response_id,
            max_output_tokens=max_output_tokens,
        )
        # responses.stream() handles stream=True internally
        payload.pop("stream", None)

        # Track tool call arguments as they stream in
        tool_call_args: dict[str, str] = {}  # call_id -> accumulated args
        tool_call_names: dict[str, str] = {}  # call_id -> function name

        async with self._client().responses.stream(**payload) as stream:
            async for event in stream:
                match event:
                    case ResponseTextDeltaEvent():
                        yield StreamTextDelta(delta=event.delta)

                    case ResponseReasoningSummaryTextDeltaEvent():
                        yield StreamReasoningDelta(delta=event.delta)

                    case ResponseOutputItemAddedEvent():
                        item = event.item
                        if isinstance(item, ResponseFunctionToolCall):
                            tool_call_args[item.call_id] = ""
                            tool_call_names[item.call_id] = item.name
                            yield StreamToolCallStart(
                                call_id=item.call_id,
                                name=item.name,
                            )

                    case ResponseFunctionCallArgumentsDeltaEvent():
                        call_id = event.item_id
                        tool_call_args.setdefault(call_id, "")
                        tool_call_args[call_id] += event.delta
                        yield StreamToolCallDelta(
                            call_id=call_id,
                            arguments_delta=event.delta,
                        )

                    case ResponseCompletedEvent():
                        response = _normalize(event.response)
                        if response.id:
                            self._last_response_id = response.id
                        self._pending_call_ids = [c.id for c in response.tool_calls]
                        yield StreamDone(response=response)

    def reset_chain(self) -> None:
        self._last_response_id = None
        self._pending_call_ids = []
        self._queued_tool_stubs = []

    def abort_pending(self, *, reason: str = "Interrupted") -> None:
        """Queue ``function_call_output`` stubs for any unresolved tool calls.

        The Responses API's ``previous_response_id`` pins the server-side
        conversation state. If the last response carried pending
        ``function_call`` items whose ``function_call_output`` companions
        were never sent back, chaining to it on the next turn leaves the
        server waiting for outputs we never produced — it will 400.

        Rather than break the chain (and lose the server-side conversation),
        we synthesize stub outputs for every pending call id. They get
        prepended to the next outgoing message batch so the server sees
        "every call was answered" and the turn can proceed. The stub text
        tells the model the user cancelled so it doesn't retry blindly.
        """
        if not self._pending_call_ids:
            return
        message = (
            f"Tool call was cancelled before execution ({reason}). "
            "The user stopped the previous turn; acknowledge and ask how "
            "they'd like to proceed rather than retrying automatically."
        )
        self._queued_tool_stubs.extend(
            {
                "type": "function_call_output",
                "call_id": call_id,
                "output": message,
            }
            for call_id in self._pending_call_ids
        )
        self._pending_call_ids = []

    def _prepend_queued_stubs(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Drain any queued abort stubs in front of the outgoing messages."""
        if not self._queued_tool_stubs:
            return messages
        stubs = self._queued_tool_stubs
        self._queued_tool_stubs = []
        return [*stubs, *messages]

    def snapshot(self) -> dict[str, Any]:
        state: dict[str, Any] = {
            "provider": "openai",
            "model": self._model.value,
        }
        if self._last_response_id:
            state["last_response_id"] = self._last_response_id
        if self._vector_store_ids:
            state["vector_store_ids"] = list(self._vector_store_ids)
        if self._uploaded_files:
            state["uploaded_files"] = dict(self._uploaded_files)
        if self._pending_call_ids:
            state["pending_call_ids"] = list(self._pending_call_ids)
        if self._queued_tool_stubs:
            state["queued_tool_stubs"] = list(self._queued_tool_stubs)
        return state

    def restore(self, state: dict[str, Any]) -> None:
        self._last_response_id = state.get("last_response_id") or None
        self._vector_store_ids = state.get("vector_store_ids", [])
        self._uploaded_files = state.get("uploaded_files", {})
        self._pending_call_ids = list(state.get("pending_call_ids") or [])
        self._queued_tool_stubs = list(state.get("queued_tool_stubs") or [])

    # ── Document search ─────────────────────────────────────────────

    async def add_searchable_document(
        self,
        filename: str,
        content: bytes,
    ) -> None:
        """Upload a document to an OpenAI vector store for file_search.

        Deduplicates by filename. Creates the vector store on first use.
        The file_search tool is injected automatically on the next
        complete() call.
        """
        if filename in self._uploaded_files:
            return

        client = self._client()

        uploaded = await client.files.create(
            file=(filename, BytesIO(content), "application/pdf"),
            purpose="assistants",
        )

        vs_id = self._vector_store_ids[-1] if self._vector_store_ids else None
        if not vs_id:
            vector_store = await client.vector_stores.create(
                name="atopile-datasheets",
                expires_after={"anchor": "last_active_at", "days": 7},
            )
            vs_id = vector_store.id
            self._vector_store_ids.append(vs_id)

        await client.vector_stores.files.create(
            vector_store_id=vs_id,
            file_id=uploaded.id,
        )
        self._uploaded_files[filename] = {
            "vector_store_id": vs_id,
            "file_id": uploaded.id,
        }


# ── Payload helpers ──────────────────────────────────────────────────


def _build_payload(
    *,
    model: ModelId,
    instructions: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    vector_store_ids: list[str],
    previous_response_id: str | None,
    max_output_tokens: int | None,
) -> dict[str, Any]:
    """Build the common request payload for create() and stream()."""
    payload: dict[str, Any] = {
        "model": model.api_name,
        "instructions": instructions,
        "input": messages,
    }

    # Ask reasoning-capable models to stream a human-readable summary of
    # their chain of thought. ``auto`` picks the right detail level per
    # prompt; utility models (nano) don't support reasoning.
    if _supports_reasoning(model):
        payload["reasoning"] = {"summary": "auto"}

    if tools is not None:
        all_tools = [t for t in tools if t.get("type") != "file_search"]
        if vector_store_ids:
            all_tools.append(
                {
                    "type": "file_search",
                    "vector_store_ids": list(vector_store_ids),
                }
            )
        payload["tools"] = all_tools

    if previous_response_id:
        payload["previous_response_id"] = previous_response_id

    if max_output_tokens is not None:
        payload["max_output_tokens"] = max_output_tokens
        payload["truncation"] = "disabled"

    return payload


def _supports_reasoning(model: ModelId) -> bool:
    """Only the user-facing GPT-5 model is reasoning-capable today."""
    return model is ModelId.GPT_5_4


# ── Response normalization ────────────────────────────────────────────


def _normalize(raw: Response) -> LLMResponse:
    """Convert an OpenAI Response into our LLMResponse."""
    tool_calls = [
        ToolCall(
            id=item.call_id,
            name=item.name,
            arguments_raw=item.arguments,
            arguments=_parse_args(item.arguments),
        )
        for item in raw.output
        if isinstance(item, ResponseFunctionToolCall)
    ]

    # Concatenate reasoning summaries across all reasoning items in this
    # response. Blank when the model didn't produce any (non-reasoning
    # models, or summary not available for this prompt).
    reasoning_parts: list[str] = []
    for item in raw.output:
        if isinstance(item, ResponseReasoningItem):
            for part in item.summary:
                if part.text:
                    reasoning_parts.append(part.text)

    usage = None
    if raw.usage:
        u = raw.usage
        usage = TokenUsage(
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            total_tokens=u.total_tokens,
            reasoning_tokens=u.output_tokens_details.reasoning_tokens,
            cached_input_tokens=u.input_tokens_details.cached_tokens,
        )

    return LLMResponse(
        id=raw.id,
        text=raw.output_text,
        tool_calls=tool_calls,
        status=raw.status,
        usage=usage,
        reasoning="\n\n".join(reasoning_parts),
    )


def _parse_args(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw) if raw else {}
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}
