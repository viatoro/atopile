"""Build tools — run, create, rename, search logs."""

from __future__ import annotations

import asyncio
from typing import Any, TypedDict

from atopile.agent.scope import Scope
from atopile.agent.tools.common import resolve_target
from atopile.agent.tools.registry import tool
from atopile.data_models import (
    AddBuildTargetRequest,
    Build,
    BuildRequest,
    Log,
    UpdateBuildTargetRequest,
)
from atopile.logging import read_build_logs
from atopile.model import builds as builds_domain
from atopile.model import projects as projects_domain
from atopile.model.sqlite import BuildHistory

# ── Helpers ──────────────────────────────────────────────────────────

_DEFAULT_LOG_LEVELS: tuple[Log.Level, ...] = (
    Log.Level.WARNING,
    Log.Level.ERROR,
    Log.Level.ALERT,
)

# Keys to strip from log entries before returning to the agent
_STRIP_KEYS = {"python_traceback", "objects"}


def _sanitize_logs(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Clean log entries for agent consumption."""
    return [{k: v for k, v in entry.items() if k not in _STRIP_KEYS} for entry in logs]


def _serialize_build(build: Build) -> dict[str, Any]:
    return {
        "build_id": build.build_id,
        "project_root": build.project_root,
        "target": build.target.model_dump(mode="json", by_alias=True),
        "status": build.status.value,
        "started_at": build.started_at,
        "elapsed_seconds": build.elapsed_seconds,
        "warnings": build.warnings,
        "errors": build.errors,
        "return_code": build.return_code,
        "error": build.error,
    }


# ── Tool handlers ────────────────────────────────────────────────────


class BuildRunArgs(TypedDict, total=False):
    targets: list[str]
    entry: str | None
    frozen: bool
    include_targets: list[str]
    exclude_targets: list[str]
    project_path: str | None
    wait: bool
    timeout_seconds: float


# Default: ~10 minutes. Builds rarely exceed this; exceeding timeout doesn't
# cancel the build — the tool just reports "timed_out" and the agent can
# call ``build_logs`` to check on it later.
_DEFAULT_BUILD_WAIT_SECONDS = 600.0


@tool(
    "Run a build for the specified targets. "
    "By default this BLOCKS until all queued builds finish and returns their "
    "final status (success/failure/warnings/errors). "
    "Pass wait=false to fire-and-forget (returns queued_build_ids only).",
    label="Built",
)
async def build_run(args: BuildRunArgs, scope: Scope) -> dict[str, Any]:
    targets = args.get("targets") or []
    if not isinstance(targets, list):
        raise ValueError("targets must be a list")
    include_targets = args.get("include_targets") or []
    if not isinstance(include_targets, list):
        raise ValueError("include_targets must be a list")
    exclude_targets = args.get("exclude_targets") or []
    if not isinstance(exclude_targets, list):
        raise ValueError("exclude_targets must be a list")
    build_root = scope.resolve_project_path(args.get("project_path"))
    wait = bool(args.get("wait", True))
    timeout_seconds = float(args.get("timeout_seconds") or _DEFAULT_BUILD_WAIT_SECONDS)

    request = BuildRequest(
        project_root=str(build_root),
        targets=[
            resolve_target(build_root, str(t).strip())
            for t in targets
            if str(t).strip()
        ],
        entry=(str(args["entry"]) if args.get("entry") else None),
        standalone=bool(args.get("standalone", False)),
        frozen=bool(args.get("frozen", False)),
        include_targets=[str(t) for t in include_targets],
        exclude_targets=[str(t) for t in exclude_targets],
    )

    # Register waiters BEFORE the queue can deliver its completion callback.
    # ``handle_start_build`` is synchronous on the queue thread — but the
    # build runs on a worker thread and completion can land at any moment,
    # so we install the futures first to avoid a race that leaves the tool
    # blocked forever.
    queued = await asyncio.to_thread(builds_domain.handle_start_build, request)
    build_ids = [b.build_id for b in queued if b.build_id]

    payload: dict[str, Any] = {
        "success": True,
        "builds": [b.model_dump(by_alias=True) for b in queued],
        "count": len(queued),
        "queued_build_ids": build_ids,
    }
    if build_root != scope.project_root:
        payload["project_path"] = str(build_root.relative_to(scope.project_root))

    if not wait or not build_ids or scope.wait_for_build is None:
        return payload

    futures = [scope.wait_for_build(bid) for bid in build_ids]
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*futures),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        payload["timed_out"] = True
        payload["timeout_seconds"] = timeout_seconds
        return payload

    results_by_id = {r.get("build_id"): r for r in results}
    payload["results"] = [results_by_id.get(bid, {}) for bid in build_ids]
    payload["overall_status"] = _summarize_status(results)
    return payload


def _summarize_status(results: list[dict[str, Any]]) -> str:
    """Collapse per-build statuses into an overall verdict."""
    statuses = {str(r.get("status") or "unknown") for r in results}
    if not statuses:
        return "unknown"
    if statuses <= {"success"}:
        return "success"
    if any(s in {"failed", "error"} for s in statuses):
        return "failed"
    if "cancelled" in statuses:
        return "cancelled"
    return "mixed"


class BuildCreateArgs(TypedDict):
    name: str
    entry: str


@tool("Create a new build target in ato.yaml.", label="Created build target")
async def build_create(args: BuildCreateArgs, scope: Scope) -> dict[str, Any]:
    request = AddBuildTargetRequest(
        project_root=str(scope.project_root),
        name=str(args.get("name", "")),
        entry=str(args.get("entry", "")),
    )
    result = await asyncio.to_thread(projects_domain.handle_add_build_target, request)
    return result.model_dump(by_alias=True)


class BuildRenameArgs(TypedDict, total=False):
    old_name: str
    new_name: str | None
    new_entry: str | None


@tool("Rename or update an existing build target.", label="Renamed build target")
async def build_rename(args: BuildRenameArgs, scope: Scope) -> dict[str, Any]:
    request = UpdateBuildTargetRequest(
        project_root=str(scope.project_root),
        old_name=str(args.get("old_name", "")),
        new_name=(str(args["new_name"]) if args.get("new_name") else None),
        new_entry=(str(args["new_entry"]) if args.get("new_entry") else None),
    )
    result = await asyncio.to_thread(
        projects_domain.handle_update_build_target,
        request,
    )
    return result.model_dump(by_alias=True)


class BuildListArgs(TypedDict, total=False):
    limit: int


@tool("List recent builds and their status.", label="Listed builds")
async def build_list(args: BuildListArgs, scope: Scope) -> dict[str, Any]:
    limit = max(1, min(120, int(args.get("limit", 20))))
    builds, active_ids = await asyncio.gather(
        asyncio.to_thread(builds_domain.get_recent_builds, limit),
        asyncio.to_thread(builds_domain.get_active_build_ids),
    )
    return {
        "builds": [_serialize_build(b) for b in builds],
        "total": len(builds),
        "active_ids": sorted(active_ids),
    }


class BuildLogsArgs(TypedDict):
    build_id: str


@tool(
    "Get logs for a build. Use the build_id returned by build_run or build_list.",
    label="Read build logs",
)
async def build_logs(args: BuildLogsArgs, scope: Scope) -> dict[str, Any]:
    build_id = str(args.get("build_id", ""))
    if not build_id:
        raise ValueError("build_id is required")

    history_build = await asyncio.to_thread(BuildHistory.get, build_id)
    raw_logs, _ = await asyncio.to_thread(
        read_build_logs,
        build_id=build_id,
        log_levels=[lv.value for lv in _DEFAULT_LOG_LEVELS],
        audience="developer",
        count=500,
    )

    return {
        "build_id": build_id,
        "logs": _sanitize_logs(raw_logs),
        "total": len(raw_logs),
        "status": history_build.status.value if history_build else None,
        "error": history_build.error if history_build else None,
        "return_code": history_build.return_code if history_build else None,
    }


class ManufacturingBuildArgs(TypedDict, total=False):
    target: str
    frozen: bool


@tool(
    "Queue a manufacturing build (includes mfg-data target by default).",
    label="Queued manufacturing build",
)
async def manufacturing_build(
    args: ManufacturingBuildArgs,
    scope: Scope,
) -> dict[str, Any]:
    target = str(args.get("target", "default")).strip() or "default"
    return await build_run(
        BuildRunArgs(
            targets=[target],
            frozen=args.get("frozen", False),
            include_targets=["mfg-data"],
        ),
        scope,
    )
