"""Tests for the async build-completion callback plumbing on Agent."""

from __future__ import annotations

import asyncio

from atopile.agent.session import Agent


def test_wait_then_complete_resolves_future():
    """Normal path: waiter installed before completion, gets resolved."""

    async def _run() -> dict:
        agent = Agent()
        future = agent.wait_for_build("b-1")
        assert not future.done()

        agent.handle_build_completed({"build_id": "b-1", "status": "success"})

        result = await asyncio.wait_for(future, timeout=0.5)
        # Waiter cleared on delivery.
        assert "b-1" not in agent._build_waiters
        return result

    result = asyncio.run(_run())
    assert result["build_id"] == "b-1"
    assert result["status"] == "success"


def test_complete_then_wait_uses_cache():
    """Race fix: completion can arrive before the waiter. Late
    ``wait_for_build`` should return a pre-resolved future from the cache.
    """

    async def _run() -> dict:
        agent = Agent()
        agent.handle_build_completed({"build_id": "b-early", "status": "success"})
        future = agent.wait_for_build("b-early")
        assert future.done()
        # Cache entry is consumed on read.
        assert "b-early" not in agent._recent_build_results
        return await future

    result = asyncio.run(_run())
    assert result["status"] == "success"


def test_double_wait_shares_future():
    """Concurrent waiters for the same build_id should observe the same result."""

    async def _run() -> tuple[dict, dict]:
        agent = Agent()
        f1 = agent.wait_for_build("b-2")
        f2 = agent.wait_for_build("b-2")
        assert f1 is f2

        agent.handle_build_completed({"build_id": "b-2", "status": "failed"})

        return await asyncio.gather(f1, f2)

    r1, r2 = asyncio.run(_run())
    assert r1 == r2
    assert r1["status"] == "failed"


def test_handle_completed_ignores_missing_build_id():
    agent = Agent()
    agent.handle_build_completed({"status": "success"})
    assert agent._recent_build_results == {}
    assert agent._build_waiters == {}


def test_recent_cache_evicts_past_ttl():
    """Stale completions should be dropped on subsequent writes."""
    agent = Agent()
    # Insert while grace is long so the entry lands in the cache.
    agent.handle_build_completed({"build_id": "b-old", "status": "success"})
    assert "b-old" in agent._recent_build_results
    # Then tighten the TTL so it looks stale on the next eviction pass.
    agent._recent_build_grace_seconds = 0.0

    agent.handle_build_completed({"build_id": "b-new", "status": "success"})

    assert "b-old" not in agent._recent_build_results


def test_recent_cache_hard_cap():
    """Over-budget cache drops the oldest entries."""
    agent = Agent()
    agent._recent_build_max_entries = 3
    for i in range(5):
        agent.handle_build_completed({"build_id": f"b-{i}", "status": "success"})
    agent._evict_stale_build_results()
    assert len(agent._recent_build_results) <= 3
    assert "b-4" in agent._recent_build_results
    assert "b-3" in agent._recent_build_results


def test_session_scope_carries_wait_for_build(tmp_path):
    """AgentSession's send_message must wire wait_for_build into Scope."""
    agent = Agent()
    session = agent.create_session(project_root=tmp_path)
    # Back-reference wired.
    assert session._agent is agent
    # Callable is the agent's method.
    assert agent.wait_for_build is not None


def test_sessions_restored_from_sqlite_get_agent_reference(tmp_path):
    agent = Agent()
    row = {
        "session_id": "s1",
        "project_root": str(tmp_path),
        "model": "claude-opus-4-7",
        "provider_state": {},
        "messages": [],
        "checklist": [],
        "active_skills": [],
        "created_at": 0.0,
        "updated_at": 0.0,
    }
    restored = agent._restore_session(row)
    assert restored._agent is agent
