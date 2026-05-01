"""Tests for the Anthropic provider -- translation layers + response normalization."""

from __future__ import annotations

from types import SimpleNamespace

from atopile.agent.providers.anthropic import (
    _assistant_message_from_blocks,
    _build_payload,
    _normalize,
    _translate_incoming,
    _translate_tools,
)
from atopile.agent.providers.base import ModelId

# ── Tool translation ───────────────────────────────────────────────────


def test_translate_tools_openai_to_anthropic():
    translated = _translate_tools(
        [
            {
                "type": "function",
                "name": "read_file",
                "description": "Read a file",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
            {"type": "file_search", "vector_store_ids": ["vs_1"]},
        ]
    )
    assert len(translated) == 1
    assert translated[0]["name"] == "read_file"
    assert translated[0]["description"] == "Read a file"
    assert "input_schema" in translated[0]
    assert translated[0]["input_schema"]["properties"]["path"]["type"] == "string"


def test_translate_tools_missing_parameters_defaults_empty_object():
    out = _translate_tools(
        [{"type": "function", "name": "noop", "description": "does nothing"}]
    )
    assert out[0]["input_schema"] == {"type": "object"}


# ── Message translation ───────────────────────────────────────────────


def test_translate_incoming_user_text():
    out = _translate_incoming([{"role": "user", "content": "hello"}])
    assert out == [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]


def test_translate_incoming_tool_outputs_batched():
    """Consecutive function_call_output items collapse to one user message."""
    out = _translate_incoming(
        [
            {"type": "function_call_output", "call_id": "t1", "output": "ok"},
            {"type": "function_call_output", "call_id": "t2", "output": "{}"},
        ]
    )
    assert len(out) == 1
    assert out[0]["role"] == "user"
    assert len(out[0]["content"]) == 2
    assert out[0]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "t1",
        "content": "ok",
    }
    assert out[0]["content"][1]["tool_use_id"] == "t2"


def test_translate_incoming_mixed_flushes_tool_results_in_order():
    out = _translate_incoming(
        [
            {"type": "function_call_output", "call_id": "t1", "output": "a"},
            {"role": "user", "content": "follow up"},
            {"type": "function_call_output", "call_id": "t2", "output": "b"},
        ]
    )
    assert len(out) == 3
    assert out[0]["content"][0]["tool_use_id"] == "t1"
    assert out[1]["content"] == [{"type": "text", "text": "follow up"}]
    assert out[2]["content"][0]["tool_use_id"] == "t2"


# ── Payload building ──────────────────────────────────────────────────


def test_build_payload_embeds_cached_system_prompt():
    payload = _build_payload(
        model=ModelId.CLAUDE_OPUS_4_7,
        instructions="you are a helpful EDA assistant",
        messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        tools=None,
        max_output_tokens=512,
    )
    assert payload["model"] == "claude-opus-4-7"
    # max_tokens includes both the visible-output budget and the extended
    # thinking budget (they're billed/counted separately).
    assert payload["max_tokens"] >= 512
    # Opus 4.7 uses the newer adaptive thinking API.
    # display=summarized is required — the default omits thinking.
    assert payload["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert payload["output_config"] == {"effort": "medium"}
    assert payload["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "tools" not in payload


def test_build_payload_drops_unsupported_tools():
    payload = _build_payload(
        model=ModelId.CLAUDE_OPUS_4_7,
        instructions="",
        messages=[],
        tools=[{"type": "file_search", "vector_store_ids": []}],
        max_output_tokens=None,
    )
    assert "tools" not in payload


# ── Response normalization ────────────────────────────────────────────


def _fake_message(content_blocks, usage=None, **kwargs):
    return SimpleNamespace(
        id=kwargs.get("id", "msg_1"),
        content=content_blocks,
        stop_reason=kwargs.get("stop_reason", "end_turn"),
        usage=usage,
    )


def _text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def _tool_block(tool_id: str, name: str, inp: dict):
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=inp)


def test_normalize_extracts_text_and_tool_calls():
    usage = SimpleNamespace(
        input_tokens=80,
        output_tokens=20,
        cache_read_input_tokens=15,
        cache_creation_input_tokens=0,
    )
    raw = _fake_message(
        [
            _text_block("Thinking about it... "),
            _tool_block("tool_a1", "read_file", {"path": "/tmp/x"}),
            _text_block("here."),
        ],
        usage=usage,
    )
    resp = _normalize(raw)
    assert resp.id == "msg_1"
    assert resp.text == "Thinking about it... here."
    assert len(resp.tool_calls) == 1
    call = resp.tool_calls[0]
    assert call.id == "tool_a1"
    assert call.name == "read_file"
    assert call.arguments == {"path": "/tmp/x"}
    assert resp.usage is not None
    assert resp.usage.input_tokens == 80
    assert resp.usage.output_tokens == 20
    assert resp.usage.cached_input_tokens == 15
    assert resp.usage.total_tokens == 100
    assert resp.usage.reasoning_tokens == 0


def test_normalize_without_usage():
    raw = _fake_message([_text_block("x")], usage=None)
    resp = _normalize(raw)
    assert resp.usage is None
    assert resp.text == "x"
    assert resp.tool_calls == []


# ── Assistant round-trip ──────────────────────────────────────────────


def test_assistant_message_from_blocks_round_trip():
    msg = _assistant_message_from_blocks(
        [
            _text_block("hi"),
            _tool_block("t_1", "do_thing", {"x": 1}),
        ]
    )
    assert msg["role"] == "assistant"
    assert msg["content"][0] == {"type": "text", "text": "hi"}
    assert msg["content"][1] == {
        "type": "tool_use",
        "id": "t_1",
        "name": "do_thing",
        "input": {"x": 1},
    }


# ── Smoke: provider_kind dispatch ─────────────────────────────────────


def test_model_provider_kind():
    assert ModelId.CLAUDE_OPUS_4_7.provider_kind == "anthropic"
    assert ModelId.CLAUDE_SONNET_4_6.provider_kind == "anthropic"
    assert ModelId.GPT_5_4.provider_kind == "openai"
    assert ModelId.GPT_5_4_NANO.provider_kind == "openai"


# ── abort_pending (repair after cancelled turn) ──────────────────────


def test_abort_pending_stubs_unresolved_tool_use():
    """If the last turn emitted tool_use blocks that never got resolved,
    synthesize tool_result stubs (with is_error) so the next turn is a
    valid payload per Anthropic's handle-tool-calls docs.
    """
    from atopile.agent.providers.anthropic import AnthropicProvider

    p = AnthropicProvider(model=ModelId.CLAUDE_OPUS_4_7)
    p._history = [
        {"role": "user", "content": [{"type": "text", "text": "review"}]},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Thinking."},
                {"type": "tool_use", "id": "toolu_A", "name": "ls", "input": {}},
                {"type": "tool_use", "id": "toolu_B", "name": "read", "input": {}},
            ],
        },
    ]

    p.abort_pending(reason="Stopped")

    assert len(p._history) == 3
    repair = p._history[-1]
    assert repair["role"] == "user"
    assert [b["tool_use_id"] for b in repair["content"]] == ["toolu_A", "toolu_B"]
    assert all(b["type"] == "tool_result" for b in repair["content"])
    assert all(b.get("is_error") is True for b in repair["content"])
    assert "Stopped" in repair["content"][0]["content"]


def test_abort_pending_is_noop_without_tool_use():
    """If the last assistant turn had no tool_use blocks, do nothing."""
    from atopile.agent.providers.anthropic import AnthropicProvider

    p = AnthropicProvider(model=ModelId.CLAUDE_OPUS_4_7)
    p._history = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
    ]
    before = [dict(m) for m in p._history]

    p.abort_pending(reason="Stopped")

    assert p._history == before


def test_abort_pending_empty_history():
    """abort_pending on a fresh provider is a no-op."""
    from atopile.agent.providers.anthropic import AnthropicProvider

    p = AnthropicProvider(model=ModelId.CLAUDE_OPUS_4_7)
    p.abort_pending(reason="Stopped")
    assert p._history == []
