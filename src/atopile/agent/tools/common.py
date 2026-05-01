"""Shared helpers used across multiple tool modules."""

from __future__ import annotations

from pathlib import Path

from atopile.data_models import ResolvedBuildTarget
from atopile.model import projects as projects_domain


def resolve_target(project_root: Path, target_name: str) -> ResolvedBuildTarget:
    """Resolve a build target name within a project."""
    project = projects_domain.handle_get_project(str(project_root))
    if project is None:
        raise ValueError(f"Project not found: {project_root}")
    resolved = next(
        (t for t in project.targets if t.name == target_name),
        None,
    )
    if resolved is not None:
        return resolved
    known = ", ".join(sorted(t.name for t in project.targets))
    raise ValueError(f"Unknown build target '{target_name}'. Available: {known}")
