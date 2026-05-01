"""Tests for the OpenAI provider response normalization and abort behaviour."""

from openai.types.responses import Response
from openai.types.responses.response_usage import (
    InputTokensDetails,
    OutputTokensDetails,
    ResponseUsage,
)

from atopile.agent.providers.base import ModelId
from atopile.agent.providers.openai import OpenAIProvider, _normalize


class TestNormalize:
    def test_extracts_usage_tokens(self) -> None:
        raw = Response.model_construct(
            id="resp_123",
            output=[],
            output_text="",
            status="completed",
            model="gpt-5.4",
            object="response",
            tools=[],
            parallel_tool_calls=True,
            created_at=0.0,
            usage=ResponseUsage(
                input_tokens=1200,
                output_tokens=300,
                total_tokens=1500,
                input_tokens_details=InputTokensDetails(cached_tokens=900),
                output_tokens_details=OutputTokensDetails(reasoning_tokens=42),
            ),
        )

        response = _normalize(raw)

        assert response.id == "resp_123"
        assert response.usage is not None
        assert response.usage.input_tokens == 1200
        assert response.usage.output_tokens == 300
        assert response.usage.total_tokens == 1500
        assert response.usage.cached_input_tokens == 900
        assert response.usage.reasoning_tokens == 42

    def test_no_usage(self) -> None:
        raw = Response.model_construct(
            id="resp_456",
            output=[],
            output_text="hello",
            status="completed",
            model="gpt-5.4",
            object="response",
            tools=[],
            parallel_tool_calls=True,
            created_at=0.0,
            usage=None,
        )

        response = _normalize(raw)

        assert response.text == ""
        assert response.usage is None
        assert response.tool_calls == []


class TestAbortPending:
    def test_abort_queues_stubs_for_every_pending_call(self) -> None:
        p = OpenAIProvider(model=ModelId.GPT_5_4)
        p._last_response_id = "resp_pending"
        p._pending_call_ids = ["call_1", "call_2"]

        p.abort_pending(reason="Stopped")

        assert p._last_response_id == "resp_pending"  # chain preserved
        assert p._pending_call_ids == []
        assert [s["call_id"] for s in p._queued_tool_stubs] == ["call_1", "call_2"]
        assert all(s["type"] == "function_call_output" for s in p._queued_tool_stubs)

    def test_queued_stubs_prepended_to_next_messages(self) -> None:
        p = OpenAIProvider(model=ModelId.GPT_5_4)
        p._queued_tool_stubs = [
            {"type": "function_call_output", "call_id": "x", "output": "stub"},
        ]
        user_msg = [{"role": "user", "content": "continue"}]

        out = p._prepend_queued_stubs(user_msg)

        assert out[0]["call_id"] == "x"
        assert out[1] == user_msg[0]
        # Drained after draining.
        assert p._queued_tool_stubs == []

    def test_abort_noop_without_pending(self) -> None:
        p = OpenAIProvider(model=ModelId.GPT_5_4)
        p._last_response_id = "resp_clean"
        p.abort_pending(reason="Stopped")
        assert p._queued_tool_stubs == []
        assert p._last_response_id == "resp_clean"

    def test_reset_chain_clears_pending_and_stubs(self) -> None:
        p = OpenAIProvider(model=ModelId.GPT_5_4)
        p._last_response_id = "resp_x"
        p._pending_call_ids = ["call_1"]
        p._queued_tool_stubs = [
            {"type": "function_call_output", "call_id": "call_1", "output": "x"},
        ]

        p.reset_chain()

        assert p._last_response_id is None
        assert p._pending_call_ids == []
        assert p._queued_tool_stubs == []
