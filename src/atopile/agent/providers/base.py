"""Provider-agnostic types, enums, and protocols for LLM providers."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, AsyncIterator, Literal, Protocol, runtime_checkable

# ── Enums ─────────────────────────────────────────────────────────────────


class ModelId(enum.Enum):
    """Known model identifiers.

    The UI picker exposes ``GPT_5_4``, ``CLAUDE_OPUS_4_7``, and
    ``CLAUDE_SONNET_4_6``. ``GPT_5_4_NANO`` is reserved for internal
    utility calls (title generation) and is not shown to the user.
    """

    # OpenAI — user-facing
    GPT_5_4 = "gpt-5.4"
    # OpenAI — internal utility (title generation)
    GPT_5_4_NANO = "gpt-5.4-nano"

    # Anthropic — user-facing
    CLAUDE_OPUS_4_7 = "claude-opus-4-7"
    CLAUDE_SONNET_4_6 = "claude-sonnet-4-6"

    @property
    def api_name(self) -> str:
        return self.value

    @property
    def provider_kind(self) -> Literal["openai", "anthropic"]:
        if self.value.startswith("claude-"):
            return "anthropic"
        return "openai"


# ── Response types ────────────────────────────────────────────────────────


@dataclass
class ToolCall:
    """Normalized tool/function call from the LLM."""

    id: str
    name: str
    arguments_raw: str
    arguments: dict[str, Any]


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    cached_input_tokens: int = 0


@dataclass
class LLMResponse:
    """Normalized response from any LLM provider."""

    id: str | None
    text: str
    tool_calls: list[ToolCall]
    status: str | None = None
    usage: TokenUsage | None = None
    reasoning: str = ""


# ── Streaming event types ─────────────────────────────────────────────────


@dataclass
class StreamTextDelta:
    """A chunk of text from the model."""

    delta: str


@dataclass
class StreamReasoningDelta:
    """A chunk of the model's reasoning / thinking output.

    Anthropic extended-thinking content and OpenAI reasoning summaries
    surface through this event. Providers that don't expose reasoning
    never emit it.
    """

    delta: str


@dataclass
class StreamToolCallStart:
    """A tool call has been identified (name known, arguments still arriving)."""

    call_id: str
    name: str


@dataclass
class StreamToolCallDelta:
    """A chunk of tool call arguments."""

    call_id: str
    arguments_delta: str


@dataclass
class StreamDone:
    """Stream finished. Carries the fully assembled response."""

    response: LLMResponse


StreamEvent = (
    StreamTextDelta
    | StreamReasoningDelta
    | StreamToolCallStart
    | StreamToolCallDelta
    | StreamDone
)


# ── Provider protocol ─────────────────────────────────────────────────────


@runtime_checkable
class LLMProvider(Protocol):
    """Core capability: send messages to an LLM and get a response.

    The provider owns all internal state (response chains, vector stores,
    etc.). The runner never inspects or manages provider state.
    """

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        instructions: str,
        tools: list[dict[str, Any]] | None = None,
        model: ModelId | None = None,
        max_output_tokens: int | None = None,
    ) -> LLMResponse:
        """Send messages to the LLM and return a response.

        When tools is None, no tool calling is available and the response
        chain is not updated (single-shot mode). Pass model to override
        the provider's default.
        """
        ...

    async def stream(
        self,
        *,
        messages: list[dict[str, Any]],
        instructions: str,
        tools: list[dict[str, Any]] | None = None,
        model: ModelId | None = None,
        max_output_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a response from the LLM, yielding events as they arrive.

        The final event is always ``StreamDone`` carrying the complete
        ``LLMResponse`` (including usage). Callers must consume the
        iterator fully to update internal provider state (response chain).
        """
        ...

    async def add_searchable_document(
        self,
        filename: str,
        content: bytes,
    ) -> None:
        """Upload a document for provider-side search (e.g. datasheets).

        After upload, the provider automatically makes the document
        searchable on subsequent complete() calls. Providers that don't
        support document search should no-op.
        """
        ...

    def reset_chain(self) -> None:
        """Clear conversation chain state (e.g. after errors)."""
        ...

    def abort_pending(self, *, reason: str = "Interrupted") -> None:
        """Repair provider state after a run was cancelled mid-turn.

        If the last recorded assistant turn emitted tool calls whose results
        were never sent back (because the task was cancelled between tool
        dispatch and follow-up model call), provider-internal history can
        become invalid. Implementations should make the history self-
        consistent so the next turn can proceed. Safe to call when there is
        nothing to repair.
        """
        ...

    def snapshot(self) -> dict[str, Any]:
        """Serialize provider state for persistence across reloads."""
        ...

    def restore(self, state: dict[str, Any]) -> None:
        """Restore provider state from a previous snapshot."""
        ...
