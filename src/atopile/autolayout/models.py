"""Typed models for autolayout jobs and provider contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from atopile.autolayout.deeppcb.models import JobType


class AutolayoutState(str, Enum):
    """Lifecycle state of an autolayout job."""

    BUILDING = "building"
    SUBMITTING = "submitting"
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_SELECTION = "awaiting_selection"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES = {
    AutolayoutState.COMPLETED,
    # DeepPCB is done as soon as candidates land; ``AWAITING_SELECTION``
    # means "waiting for the user to pick one" — there's nothing left
    # to poll for. Treating it as terminal stops the ~10 s/board API
    # chatter that keeps firing for every un-applied finished job.
    AutolayoutState.AWAITING_SELECTION,
    AutolayoutState.FAILED,
    AutolayoutState.CANCELLED,
}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


class AutolayoutCandidate(_Model):
    """A selectable layout candidate produced by a provider."""

    candidate_id: str
    label: str | None = None
    score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    files: dict[str, str] = Field(default_factory=dict)


class AutolayoutJob(_Model):
    """Persistent in-memory representation of an autolayout job."""

    job_id: str
    project_root: str
    build_target: str
    provider: str
    job_type: JobType = JobType.PLACEMENT
    state: AutolayoutState
    created_at: str
    updated_at: str
    build_id: str | None = None
    provider_job_ref: str | None = None
    progress: float | None = None
    message: str = ""
    error: str | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)
    work_dir: str | None = None
    layout_path: str
    selected_candidate_id: str | None = None
    applied_candidate_id: str | None = None
    applied_layout_path: str | None = None
    backup_layout_path: str | None = None
    candidates: list[AutolayoutCandidate] = Field(default_factory=list)

    def mark_updated(self) -> None:
        self.updated_at = utc_now_iso()


def utc_now_iso() -> str:
    """Return a compact UTC timestamp string."""
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class PreCheckItem:
    """A single readiness check displayed in the UI before running a job."""

    label: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class PreviewResult:
    """Result of starting a candidate preview — the layout viewer side
    is the websocket layer's responsibility."""

    job_id: str
    candidate_id: str
    preview_path: Path
