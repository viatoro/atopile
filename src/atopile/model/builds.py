"""Build domain logic - business logic for build operations."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from atopile import config
from atopile.buildutil import generate_build_id
from atopile.data_models import (
    Build,
    BuildRequest,
    BuildStatus,
    OpenLayoutRequest,
    ResolvedBuildTarget,
)
from atopile.logging import get_logger
from atopile.model.build_queue import _build_queue
from atopile.model.projects import _resolved_targets_for_project
from atopile.model.sqlite import BuildHistory
from atopile.pathutils import same_path

log = get_logger(__name__)


def resolve_build_target_config(
    project_root: str | Path,
    target_name: str,
) -> config.BuildTargetConfig:
    """Resolve a build target config from project root and target name."""
    project_path = Path(project_root)
    project_cfg = config.ProjectConfig.from_path(project_path)
    if project_cfg is None:
        raise ValueError(f"No ato.yaml found in: {project_path}")
    build_cfg = project_cfg.builds.get(target_name)
    if build_cfg is None:
        known = ", ".join(sorted(project_cfg.builds.keys()))
        raise ValueError(f"Unknown build target '{target_name}'. Available: {known}")
    return build_cfg


def get_build_output_dir_for_config(build_cfg: config.BuildTargetConfig) -> Path:
    """Return the artifact directory for a resolved build config."""
    return build_cfg.paths.output_base.parent


def get_build_output_dir(project_root: str | Path, target_name: str) -> Path:
    """Return the target's artifact directory."""
    return get_build_output_dir_for_config(
        resolve_build_target_config(project_root, target_name)
    )


def _target_identity(
    target: ResolvedBuildTarget | None,
    *,
    project_root: str | None = None,
    build_name: str | None = None,
) -> tuple[str, str, str]:
    return (
        str(target.root if target and target.root else project_root or ""),
        str(target.name if target and target.name else build_name or ""),
        str(target.entry if target and target.entry else ""),
    )


def _build_identity(build: Build) -> tuple[str, str, str]:
    return _target_identity(
        build.target,
        project_root=build.project_root,
        build_name=build.name,
    )


def _live_builds() -> list[Build]:
    return [*BuildHistory.get_building(), *BuildHistory.get_queued()]


def _finished_builds(*, limit: int | None = None) -> list[Build]:
    return BuildHistory.get_finished(limit=limit or 100)


def _matches_target(build: Build, target: ResolvedBuildTarget) -> bool:
    return _build_identity(build) == _target_identity(target)


def _recent_builds(*, limit: int | None = None) -> list[Build]:
    return [*_live_builds(), *_finished_builds(limit=limit)]


def _queue_builds(*, limit: int | None = None) -> list[Build]:
    live_builds = _live_builds()
    live_targets = {_build_identity(build) for build in live_builds}
    latest_finished = [
        build
        for build in BuildHistory.get_latest_finished_per_target(limit=limit or 100)
        if _build_identity(build) not in live_targets
    ]
    return [*live_builds, *latest_finished]


def _filter_builds(
    builds: list[Build],
    *,
    project_root: str | None = None,
    target: ResolvedBuildTarget | None = None,
    status: BuildStatus | None = None,
) -> list[Build]:
    if project_root is not None:
        builds = [
            build for build in builds if same_path(build.project_root, project_root)
        ]
    if target is not None:
        builds = [build for build in builds if _matches_target(build, target)]
    if status is not None:
        builds = [build for build in builds if build.status == status]
    return builds


def _query_builds(
    builds: list[Build],
    *,
    project_root: str | None = None,
    target: ResolvedBuildTarget | None = None,
    status: BuildStatus | None = None,
    limit: int | None = None,
    sort: bool = False,
) -> list[Build]:
    filtered = _filter_builds(
        builds,
        project_root=project_root,
        target=target,
        status=status,
    )
    if sort:
        filtered.sort(key=lambda build: build.started_at or 0, reverse=True)
    return filtered[:limit] if limit is not None else filtered


def is_build_in_progress(build: Build | None) -> bool:
    return build is not None and build.status in {
        BuildStatus.QUEUED,
        BuildStatus.BUILDING,
    }


def get_recent_builds(limit: int = 120) -> list[Build]:
    """Get the newest live and finished builds."""
    return _query_builds(
        _recent_builds(limit=limit),
        limit=limit,
        sort=True,
    )


def get_active_build_ids() -> set[str]:
    """Get currently queued or running build ids."""
    return {build.build_id for build in _query_builds(_live_builds()) if build.build_id}


def summarize_build_stages(
    build: Build | None,
    *,
    limit: int = 40,
) -> dict[str, Any] | None:
    """Summarize build stage progress for UI/tool consumers."""
    if build is None:
        return None

    counts: dict[str, int] = {}
    stages: list[dict[str, Any]] = []
    for stage in build.stages:
        status = stage.status.value
        counts[status] = counts.get(status, 0) + 1
        stages.append(
            {
                "name": stage.name or stage.stage_id,
                "status": status,
                "elapsed_seconds": stage.elapsed_seconds,
            }
        )

    return {
        "total_reported": build.total_stages,
        "observed": len(stages),
        "counts": counts,
        "stages": stages[:limit],
    }


def get_active_builds() -> list[Build]:
    """Get currently active (queued/building) builds."""
    return _query_builds(_live_builds())


def get_finished_builds() -> list[Build]:
    """Get finished (succeeded/failed/cancelled) builds."""
    return _query_builds(_finished_builds())


def get_queue_builds() -> list[Build]:
    """Get live builds plus the latest completed build for every other target."""
    return _query_builds(_queue_builds())


def get_builds_by_project(
    project_root: str | None = None,
    target: ResolvedBuildTarget | None = None,
    limit: int = 50,
) -> list[Build]:
    """Get finished builds filtered by project root and/or target."""
    return _query_builds(
        _finished_builds(limit=limit),
        project_root=project_root,
        target=target,
        limit=limit,
    )


def get_selected_build(
    target: ResolvedBuildTarget | None,
) -> Build | None:
    """Get the live build for the selected target, else the latest completed build."""
    if target is None:
        return None
    active_matches = _query_builds(_live_builds(), target=target, limit=1, sort=True)
    if active_matches:
        return active_matches[0]
    return BuildHistory.get_latest_finished_for_target(target)


def validate_build_request(request: BuildRequest) -> str | None:
    """Validate a build request. Returns error message or None if valid."""
    if request.standalone:
        project_path = Path(request.project_root)
        if not project_path.exists():
            return f"Project path does not exist: {project_path}"
        if not request.entry:
            return "Standalone builds require an entry point"
        entry_file = (
            request.entry.split(":")[0] if ":" in request.entry else request.entry
        )
        entry_path = project_path / entry_file
        if not entry_path.exists():
            return f"Entry file not found: {entry_path}"
        return None

    project_roots = (
        {Path(target.root) for target in request.targets}
        if request.targets
        else {Path(request.project_root)}
    )
    for project_path in project_roots:
        if not project_path.exists():
            return f"Project path does not exist: {project_path}"
        if not (project_path / "ato.yaml").exists():
            return f"No ato.yaml found in: {project_path}"

    return None


def _resolve_request_targets(request: BuildRequest) -> list[ResolvedBuildTarget]:
    """Resolve targets for a build request (empty list means all targets)."""
    if request.targets:
        return request.targets

    if request.standalone:
        return [
            ResolvedBuildTarget(root=request.project_root, entry=request.entry or "")
        ]

    project_path = Path(request.project_root)
    try:
        targets = _resolved_targets_for_project(project_path)
        return targets or [ResolvedBuildTarget(root=request.project_root)]
    except Exception as exc:
        log.warning(
            f"Failed to read targets from ato.yaml at {project_path}: {exc}; "
            "falling back to 'default'"
        )
        return [ResolvedBuildTarget(root=request.project_root)]


def resolve_layout_path(request: OpenLayoutRequest) -> Path:
    layout_path = Path(request.target.pcb_path)
    if not layout_path.exists():
        raise FileNotFoundError(f"Layout not found: {layout_path}")

    return layout_path


def handle_start_build(request: BuildRequest) -> list[Build]:
    """Validate and enqueue builds. Raises ValueError on invalid request."""
    error = validate_build_request(request)
    if error:
        raise ValueError(error)

    targets = _resolve_request_targets(request)
    if request.standalone and len(targets) > 1:
        log.warning(
            "Standalone build requested with multiple targets; "
            "using the first target only"
        )
        targets = targets[:1]

    if not targets:
        raise ValueError("No build targets resolved")

    queued_builds: list[Build] = []
    for target in targets:
        started_at = time.time()
        build_id = generate_build_id(target.root, target.name, started_at)
        queued_builds.append(
            Build(
                build_id=build_id,
                project_root=target.root or request.project_root,
                name=target.name,
                target=target,
                standalone=request.standalone,
                frozen=request.frozen,
                include_targets=request.include_targets,
                exclude_targets=request.exclude_targets,
                status=BuildStatus.QUEUED,
                started_at=started_at,
            )
        )

    return _build_queue.submit_builds(queued_builds)
