"""UI-state holder for the autolayout panel.

All transient store-shaped state — loading/submitting/error flags,
the active preview, preflight metrics, and the current project root
— lives here. The service composes one and routes mutations through
its public ``begin_*``/``end_*``/``set_*``/``clear_*`` methods so
every change fans out to observers atomically.

This class owns its own RLock. The service's lock protects the
jobs dict; the two are intentionally independent because UI-state
mutations and job mutations never need to be atomic together.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from atopile.autolayout.models import PreviewResult


class AutolayoutUIState:
    """Mutable UI state, guarded by an RLock, with change notification."""

    def __init__(self, *, on_change: Callable[[], None] | None = None) -> None:
        self._lock = threading.RLock()
        self._on_change = on_change

        # Transient flags
        self._loading: bool = False
        self._submitting: bool = False
        self._error: str | None = None

        # Preview
        self._preview_job_id: str | None = None
        self._preview_candidate_id: str | None = None
        self._preview_path: str | None = None

        # Preflight
        self._preflight: dict[str, Any] | None = None
        self._preflight_loading: bool = False
        self._preflight_error: str | None = None

        # Current project context
        self._project_root: str | None = None

    # -- Read-only state accessors ------------------------------------------

    @property
    def loading(self) -> bool:
        return self._loading

    @property
    def submitting(self) -> bool:
        return self._submitting

    @property
    def error(self) -> str | None:
        return self._error

    @property
    def preview_job_id(self) -> str | None:
        return self._preview_job_id

    @property
    def preview_candidate_id(self) -> str | None:
        return self._preview_candidate_id

    @property
    def preview_path(self) -> str | None:
        return self._preview_path

    @property
    def is_previewing(self) -> bool:
        return self._preview_path is not None

    @property
    def preflight(self) -> dict[str, Any] | None:
        return self._preflight

    @property
    def preflight_loading(self) -> bool:
        return self._preflight_loading

    @property
    def preflight_error(self) -> str | None:
        return self._preflight_error

    @property
    def project_root(self) -> str | None:
        return self._project_root

    # -- Mutators (each notifies observers) ---------------------------------

    def begin_loading(self) -> None:
        with self._lock:
            self._loading = True
            self._error = None
        self.notify()

    def end_loading(self, error: str | None = None) -> None:
        with self._lock:
            self._loading = False
            if error is not None:
                self._error = error
        self.notify()

    def begin_submitting(self) -> None:
        with self._lock:
            self._submitting = True
            self._error = None
        self.notify()

    def end_submitting(self, error: str | None = None) -> None:
        with self._lock:
            self._submitting = False
            if error is not None:
                self._error = error
        self.notify()

    def set_error(self, message: str | None) -> None:
        with self._lock:
            self._error = message
        self.notify()

    def clear_error(self) -> None:
        self.set_error(None)

    def set_preview(
        self, job_id: str, candidate_id: str, path: Path | str
    ) -> PreviewResult:
        with self._lock:
            self._preview_job_id = job_id
            self._preview_candidate_id = candidate_id
            self._preview_path = str(path)
        self.notify()
        return PreviewResult(
            job_id=job_id, candidate_id=candidate_id, preview_path=Path(path)
        )

    def end_preview(self) -> None:
        with self._lock:
            self._preview_job_id = None
            self._preview_candidate_id = None
            self._preview_path = None
        self.notify()

    def set_project_root(self, project_root: str | None) -> None:
        with self._lock:
            self._project_root = project_root
        if self.is_previewing:
            self.end_preview()  # also notifies
        else:
            self.notify()

    # -- Preflight ----------------------------------------------------------

    def begin_preflight(self) -> None:
        with self._lock:
            self._preflight_loading = True
            self._preflight_error = None
        self.notify()

    def set_preflight(self, summary: dict[str, Any]) -> None:
        with self._lock:
            self._preflight = summary
            self._preflight_loading = False
        self.notify()

    def set_preflight_silent(self, summary: dict[str, Any]) -> None:
        """Replace the preflight snapshot without toggling loading state.

        Used by the layout-changed listener, which recomputes in-line.
        """
        with self._lock:
            self._preflight = summary
        self.notify()

    def fail_preflight(self, error: str) -> None:
        with self._lock:
            self._preflight_loading = False
            self._preflight_error = error
        self.notify()

    # -- Notification -------------------------------------------------------

    def notify(self) -> None:
        """Tell observers the state changed."""
        if self._on_change:
            self._on_change()
