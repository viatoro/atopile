"""Tests for per-session model selection and mid-session provider switching."""

from __future__ import annotations

from pathlib import Path

import pytest

from atopile.agent.providers.anthropic import AnthropicProvider
from atopile.agent.providers.base import ModelId
from atopile.agent.providers.openai import OpenAIProvider
from atopile.agent.session import Agent, AgentSession


def _new_session(agent: Agent, project_root: Path, model: ModelId) -> AgentSession:
    return agent.create_session(project_root=project_root, model=model)


def test_default_model_is_claude_opus_4_7(tmp_path: Path):
    agent = Agent()
    session = agent.create_session(project_root=tmp_path)
    assert session.model == ModelId.CLAUDE_OPUS_4_7
    assert isinstance(session._provider, AnthropicProvider)


def test_create_session_with_openai_model(tmp_path: Path):
    agent = Agent()
    session = _new_session(agent, tmp_path, ModelId.GPT_5_4)
    assert session.model == ModelId.GPT_5_4
    assert isinstance(session._provider, OpenAIProvider)


def test_set_model_cross_provider_builds_new_provider(tmp_path: Path):
    agent = Agent()
    session = _new_session(agent, tmp_path, ModelId.GPT_5_4)
    original_messages = list(session._messages)
    session.set_model(ModelId.CLAUDE_OPUS_4_7)
    assert session.model == ModelId.CLAUDE_OPUS_4_7
    assert isinstance(session._provider, AnthropicProvider)
    # UI history must be preserved across the swap.
    assert session._messages == original_messages


def test_set_model_same_provider_mutates_in_place(tmp_path: Path):
    agent = Agent()
    session = _new_session(agent, tmp_path, ModelId.CLAUDE_OPUS_4_7)
    provider_before = session._provider
    session.set_model(ModelId.CLAUDE_SONNET_4_6)
    assert session._provider is provider_before
    assert session._provider.model == ModelId.CLAUDE_SONNET_4_6
    assert session.model == ModelId.CLAUDE_SONNET_4_6


def test_set_model_while_run_active_raises(tmp_path: Path):
    agent = Agent()
    session = _new_session(agent, tmp_path, ModelId.CLAUDE_OPUS_4_7)
    session._active_run_id = "run-fake"
    with pytest.raises(RuntimeError, match="Cannot change model"):
        session.set_model(ModelId.GPT_5_4)


def test_snapshot_round_trip_preserves_model(tmp_path: Path):
    agent = Agent()
    session = _new_session(agent, tmp_path, ModelId.CLAUDE_SONNET_4_6)
    snap = session.snapshot()
    assert snap["model"] == "claude-sonnet-4-6"
    restored = agent._restore_session(snap)
    assert restored.model == ModelId.CLAUDE_SONNET_4_6
    assert isinstance(restored._provider, AnthropicProvider)


def test_restore_session_unknown_model_falls_back_to_agent_default(tmp_path: Path):
    agent = Agent()
    row = {
        "session_id": "s1",
        "project_root": str(tmp_path),
        "model": "totally-not-a-model",
        "provider_state": {},
        "messages": [],
        "checklist": [],
        "active_skills": [],
        "created_at": 0.0,
        "updated_at": 0.0,
    }
    restored = agent._restore_session(row)
    assert restored.model == agent._model


def test_ui_state_includes_model(tmp_path: Path):
    agent = Agent()
    session = _new_session(agent, tmp_path, ModelId.CLAUDE_SONNET_4_6)
    ui = session.ui_state()
    assert ui["model"] == "claude-sonnet-4-6"


# ── abort_run (stop button cleanup) ───────────────────────────────────


def test_abort_run_clears_pending_state(tmp_path: Path):
    """After cancellation, abort_run should leave the session reusable."""
    agent = Agent()
    session = _new_session(agent, tmp_path, ModelId.CLAUDE_OPUS_4_7)

    # Simulate a mid-stream run: active_run_id set + pending assistant msg
    # with partial content.
    session._active_run_id = "run-abc"
    session._stop_requested = True
    session._messages.append(
        {"id": "u1", "role": "user", "content": "hi", "pending": False}
    )
    session._messages.append(
        {"id": "a1", "role": "assistant", "content": "partial", "pending": True}
    )

    session.abort_run(reason="Stopped")

    assert session._active_run_id is None
    assert session._stop_requested is False
    pending = [m for m in session._messages if m.get("pending")]
    assert pending == []
    assistant = session._messages[-1]
    assert assistant["content"] == "partial"  # partial kept if non-empty
    assert assistant.get("pending") is False


def test_abort_run_fills_empty_content(tmp_path: Path):
    """If the run was cancelled before any text streamed, fill a placeholder."""
    agent = Agent()
    session = _new_session(agent, tmp_path, ModelId.CLAUDE_OPUS_4_7)
    session._active_run_id = "run-abc"
    session._messages.append(
        {"id": "a1", "role": "assistant", "content": "", "pending": True}
    )

    session.abort_run(reason="Stopped")

    assert session._messages[-1]["content"] == "Stopped before completion."
    assert session._messages[-1]["pending"] is False


def test_abort_run_is_idempotent(tmp_path: Path):
    """Calling abort_run on a clean session is a no-op."""
    agent = Agent()
    session = _new_session(agent, tmp_path, ModelId.CLAUDE_OPUS_4_7)
    before = list(session._messages)

    session.abort_run(reason="Stopped")

    assert session._active_run_id is None
    assert session._messages == before


def test_abort_run_repairs_anthropic_tool_use(tmp_path: Path):
    """abort_run must repair provider state, not just the UI message list."""
    agent = Agent()
    session = _new_session(agent, tmp_path, ModelId.CLAUDE_OPUS_4_7)

    # Simulate: user asked, model emitted tool_use, run got cancelled.
    session._provider._history = [  # type: ignore[attr-defined]
        {"role": "user", "content": [{"type": "text", "text": "review"}]},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "toolu_X", "name": "ls", "input": {}},
            ],
        },
    ]
    session._active_run_id = "run-xyz"
    session._messages.append(
        {"id": "a1", "role": "assistant", "content": "partial", "pending": True}
    )

    session.abort_run(reason="Stopped")

    history = session._provider._history  # type: ignore[attr-defined]
    assert len(history) == 3
    assert history[-1]["role"] == "user"
    assert history[-1]["content"][0]["type"] == "tool_result"
    assert history[-1]["content"][0]["tool_use_id"] == "toolu_X"


def test_abort_run_queues_openai_stubs_without_breaking_chain(tmp_path: Path):
    """abort_run must preserve previous_response_id and queue stub outputs
    for any unresolved tool calls, so the next turn's Responses API request
    stays valid without losing server-side conversation state.
    """
    agent = Agent()
    session = _new_session(agent, tmp_path, ModelId.GPT_5_4)

    session._provider._last_response_id = "resp_pending"  # type: ignore[attr-defined]
    session._provider._pending_call_ids = ["call_A", "call_B"]  # type: ignore[attr-defined]
    session._active_run_id = "run-xyz"

    session.abort_run(reason="Stopped")

    # Chain NOT broken — server-side context is preserved.
    assert session._provider._last_response_id == "resp_pending"  # type: ignore[attr-defined]
    # Stubs queued for both unresolved calls, pending list cleared.
    stubs = session._provider._queued_tool_stubs  # type: ignore[attr-defined]
    assert [s["call_id"] for s in stubs] == ["call_A", "call_B"]
    assert all(s["type"] == "function_call_output" for s in stubs)
    assert all("Stopped" in s["output"] for s in stubs)
    assert session._provider._pending_call_ids == []  # type: ignore[attr-defined]


def test_openai_abort_is_noop_when_no_pending_calls(tmp_path: Path):
    """If the last response had no tool_calls, abort_pending does nothing."""
    agent = Agent()
    session = _new_session(agent, tmp_path, ModelId.GPT_5_4)
    session._provider._last_response_id = "resp_clean"  # type: ignore[attr-defined]

    session.abort_run(reason="Stopped")

    assert session._provider._last_response_id == "resp_clean"  # type: ignore[attr-defined]
    assert session._provider._queued_tool_stubs == []  # type: ignore[attr-defined]
