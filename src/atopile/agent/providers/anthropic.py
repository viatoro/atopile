"""Anthropic provider — thin wrapper over the Claude Messages API.

Only vendor-specific logic lives here: client setup, message/tool shape
translation from the session's OpenAI-flavored inputs, streaming event
mapping, and internal conversation history. The runner never inspects
provider state.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

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

DEFAULT_MAX_TOKENS = 8192
# Budget for extended thinking. Keep modest so turns stay responsive —
# the summary UI truncates anyway.
THINKING_BUDGET_TOKENS = 2048


class AnthropicProvider:
    """Vendor-specific wrapper for the Anthropic Messages API.

    Anthropic's Messages API is stateless — every call ships the full
    conversation. This provider keeps ``_history`` as the canonical
    Anthropic-shape transcript and appends to it on each exchange.
    """

    def __init__(self, model: ModelId, *, timeout: float = 120.0) -> None:
        self._model = model
        self._timeout = timeout
        self._history: list[dict[str, Any]] = []

    # ── Properties ────────────────────────────────────────────────────

    @property
    def model(self) -> ModelId:
        return self._model

    @model.setter
    def model(self, value: ModelId) -> None:
        self._model = value

    # ── Core API ──────────────────────────────────────────────────────

    def _client(self):  # type: ignore[no-untyped-def]
        return gateway.anthropic_client(timeout=self._timeout)

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

        The session passes new user input / tool outputs in ``messages``;
        we translate them to Anthropic shape, append to history, and send
        the full history. On return we append the assistant turn too.
        """
        new_turn = _translate_incoming(messages)
        send_history = self._history + new_turn

        payload = _build_payload(
            model=(model or self._model),
            instructions=instructions,
            messages=send_history,
            tools=tools,
            max_output_tokens=max_output_tokens,
        )

        raw = await self._client().messages.create(**payload)
        response = _normalize(raw)
        self._history = send_history + [_assistant_message_from_blocks(raw.content)]
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
        """Stream a response, yielding events as they arrive."""
        new_turn = _translate_incoming(messages)
        send_history = self._history + new_turn

        payload = _build_payload(
            model=(model or self._model),
            instructions=instructions,
            messages=send_history,
            tools=tools,
            max_output_tokens=max_output_tokens,
        )

        # index → (call_id, name) for tool_use content blocks so we can
        # emit StreamToolCallDelta events as input_json_delta chunks arrive.
        tool_use_index: dict[int, tuple[str, str]] = {}

        async with self._client().messages.stream(**payload) as stream:
            async for event in stream:
                etype = event.type
                if etype == "content_block_start":
                    block = event.content_block
                    if block.type == "tool_use":
                        tool_use_index[event.index] = (block.id, block.name)
                        yield StreamToolCallStart(call_id=block.id, name=block.name)
                elif etype == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        yield StreamTextDelta(delta=delta.text)
                    elif delta.type == "thinking_delta":
                        yield StreamReasoningDelta(delta=delta.thinking)
                    elif delta.type == "input_json_delta":
                        rec = tool_use_index.get(event.index)
                        if rec is not None:
                            yield StreamToolCallDelta(
                                call_id=rec[0],
                                arguments_delta=delta.partial_json,
                            )

            final = await stream.get_final_message()

        response = _normalize(final)
        self._history = send_history + [_assistant_message_from_blocks(final.content)]
        yield StreamDone(response=response)

    def reset_chain(self) -> None:
        self._history.clear()

    def abort_pending(self, *, reason: str = "Interrupted") -> None:
        """Stub out unresolved tool_use blocks in the last assistant turn.

        Anthropic's Messages API requires every ``tool_use`` block to be
        followed immediately by a ``tool_result`` block in the next user
        message. If a run was cancelled after the model emitted tool calls
        but before their outputs were sent back, the internal history will
        fail validation on the next turn with "tool_use ids were found
        without tool_result blocks". Per the officially-supported pattern
        (`/docs/en/agents-and-tools/tool-use/handle-tool-calls`), we
        synthesize stub ``tool_result`` blocks with ``is_error: true`` and
        an instructive message so Claude understands the call didn't
        succeed and the conversation can continue.
        """
        if not self._history:
            return
        last = self._history[-1]
        if last.get("role") != "assistant":
            return
        content = last.get("content") or []
        pending_ids = [
            block["id"]
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "tool_use"
            and block.get("id")
        ]
        if not pending_ids:
            return
        message = (
            f"Tool call was cancelled before execution ({reason}). "
            "The user stopped the previous turn; acknowledge and ask how "
            "they'd like to proceed rather than retrying automatically."
        )
        stub_content = [
            {
                "type": "tool_result",
                "tool_use_id": tid,
                "content": message,
                "is_error": True,
            }
            for tid in pending_ids
        ]
        self._history.append({"role": "user", "content": stub_content})

    def snapshot(self) -> dict[str, Any]:
        return {
            "provider": "anthropic",
            "model": self._model.value,
            "history": list(self._history),
        }

    def restore(self, state: dict[str, Any]) -> None:
        self._history = list(state.get("history") or [])

    # ── Document search ─────────────────────────────────────────────

    async def add_searchable_document(
        self,
        filename: str,
        content: bytes,
    ) -> None:
        """Not supported on Anthropic. No-op with a warning."""
        log.warning(
            "add_searchable_document is not supported on Anthropic models; "
            "switch to an OpenAI model for file_search. filename=%s",
            filename,
        )


# ── Payload helpers ──────────────────────────────────────────────────


def _build_payload(
    *,
    model: ModelId,
    instructions: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    max_output_tokens: int | None,
) -> dict[str, Any]:
    """Build the common request payload for create() and stream()."""
    max_tokens = max_output_tokens or DEFAULT_MAX_TOKENS
    payload: dict[str, Any] = {
        "model": model.api_name,
        "max_tokens": max_tokens + THINKING_BUDGET_TOKENS,
        "messages": messages,
    }
    # Thinking config shape differs by model generation.
    #   Opus 4.7 → adaptive + output_config.effort (the newer API; it
    #     handles interleaved thinking natively, so no beta header).
    #   Sonnet 4.6 and earlier → enabled + budget_tokens, plus the
    #     interleaved-thinking beta so the model can reason between
    #     tool calls, not just before the first one.
    if model is ModelId.CLAUDE_OPUS_4_7:
        # display="summarized" surfaces a user-readable thinking stream;
        # the default omits thinking content from the response entirely.
        payload["thinking"] = {"type": "adaptive", "display": "summarized"}
        payload["output_config"] = {"effort": "medium"}
    else:
        payload["thinking"] = {
            "type": "enabled",
            "budget_tokens": THINKING_BUDGET_TOKENS,
        }
        payload["extra_headers"] = {
            "anthropic-beta": "interleaved-thinking-2025-05-14",
        }

    if instructions:
        # Cache the system prompt — it's large and stable across turns.
        payload["system"] = [
            {
                "type": "text",
                "text": instructions,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    if tools is not None:
        translated = _translate_tools(tools)
        if translated:
            payload["tools"] = translated

    return payload


def _translate_tools(openai_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate OpenAI-format tool definitions to Anthropic shape.

    Drops OpenAI-only tools (``file_search``) since Anthropic has no
    equivalent in this provider.
    """
    out: list[dict[str, Any]] = []
    for t in openai_tools:
        if t.get("type") != "function":
            # Skip unknown/vendor-specific (file_search, code_interpreter, etc.)
            continue
        out.append(
            {
                "name": t["name"],
                "description": t.get("description", ""),
                "input_schema": t.get("parameters") or {"type": "object"},
            }
        )
    return out


def _translate_incoming(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate the session's OpenAI-flavored inputs to Anthropic messages.

    The session sends either:
    - ``{"role":"user","content":str}`` for fresh user input or steering.
    - ``{"type":"function_call_output","call_id":...,"output":str}`` for
      tool results (a batch may contain several for a single turn).

    Consecutive tool-result items collapse into a single user message with
    multiple ``tool_result`` content blocks (Anthropic requires one
    user→assistant exchange per turn).
    """
    out: list[dict[str, Any]] = []
    pending_tool_results: list[dict[str, Any]] = []

    def flush_tool_results() -> None:
        if pending_tool_results:
            out.append({"role": "user", "content": list(pending_tool_results)})
            pending_tool_results.clear()

    for m in messages:
        if m.get("type") == "function_call_output":
            pending_tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": m["call_id"],
                    "content": m.get("output", ""),
                }
            )
            continue

        flush_tool_results()

        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, str):
            out.append({"role": role, "content": [{"type": "text", "text": content}]})
        elif isinstance(content, list):
            out.append({"role": role, "content": content})
        else:
            out.append(
                {
                    "role": role,
                    "content": [{"type": "text", "text": str(content)}],
                }
            )

    flush_tool_results()
    return out


def _assistant_message_from_blocks(blocks: list[Any]) -> dict[str, Any]:
    """Build an Anthropic-shape assistant message from response blocks.

    Thinking blocks must be round-tripped verbatim (signature-verified)
    when extended thinking is enabled alongside tool use, or the next
    turn 400s.
    """
    content: list[dict[str, Any]] = []
    for b in blocks:
        if b.type == "text":
            content.append({"type": "text", "text": b.text})
        elif b.type == "tool_use":
            content.append(
                {
                    "type": "tool_use",
                    "id": b.id,
                    "name": b.name,
                    "input": b.input,
                }
            )
        elif b.type == "thinking":
            content.append(
                {
                    "type": "thinking",
                    "thinking": b.thinking,
                    "signature": b.signature,
                }
            )
        elif b.type == "redacted_thinking":
            content.append({"type": "redacted_thinking", "data": b.data})
    return {"role": "assistant", "content": content}


# ── Response normalization ────────────────────────────────────────────


def _normalize(raw: Any) -> LLMResponse:
    """Convert an Anthropic Message into our LLMResponse."""
    text_parts: list[str] = []
    thinking_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for b in raw.content:
        if b.type == "text":
            text_parts.append(b.text)
        elif b.type == "thinking":
            thinking_parts.append(b.thinking)
        elif b.type == "tool_use":
            args = b.input if isinstance(b.input, dict) else {}
            tool_calls.append(
                ToolCall(
                    id=b.id,
                    name=b.name,
                    arguments_raw=json.dumps(args, ensure_ascii=False),
                    arguments=args,
                )
            )

    usage = None
    if getattr(raw, "usage", None) is not None:
        u = raw.usage
        input_tokens = int(u.input_tokens)
        cached = int(getattr(u, "cache_read_input_tokens", 0) or 0)
        output_tokens = int(u.output_tokens)
        usage = TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            reasoning_tokens=0,
            cached_input_tokens=cached,
        )

    return LLMResponse(
        id=getattr(raw, "id", None),
        text="".join(text_parts),
        tool_calls=tool_calls,
        status=getattr(raw, "stop_reason", None),
        usage=usage,
        reasoning="\n\n".join(thinking_parts),
    )
