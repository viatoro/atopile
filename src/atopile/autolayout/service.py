"""Autolayout service orchestration for DeepPCB-backed AI layout.

Owns job lifecycle, candidate management, apply/preview, and the
UI-state object the websocket layer reads from. Heavy lifting is
delegated to sibling modules:

* :mod:`.ui_state` — loading/error/preview/preflight flags
* :mod:`.job_store` — JSON persistence + resume-on-load
* :mod:`.client_factory` — DeepPCB client with token rotation
* :mod:`.job_runner` — background submit + poll
* :mod:`.eda_convert` — KiCad ↔ HL ↔ DeepPCB conversion
* :mod:`.readiness` — pre-check readiness computations
* :mod:`.status_mapping` — DeepPCB → internal state + revision parsing

State mutation rule: external callers MUST go through the public
``set_*``/``begin_*``/``end_*``/``clear_*`` methods. Direct attribute
writes from outside the service are not allowed — every mutation must
notify observers atomically.
"""

from __future__ import annotations

import copy
import logging
import shutil
import threading
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from atopile.autolayout import eda_convert, job_store, readiness, status_mapping
from atopile.autolayout.client_factory import DeepPCBClientProvider
from atopile.autolayout.deeppcb.exceptions import DeepPCBClientError
from atopile.autolayout.deeppcb.models import JobType
from atopile.autolayout.job_runner import JobRunner
from atopile.autolayout.models import (
    TERMINAL_STATES,
    AutolayoutCandidate,
    AutolayoutJob,
    AutolayoutState,
    PreCheckItem,
    PreviewResult,
    utc_now_iso,
)
from atopile.autolayout.preflight import compute_preflight_summary
from atopile.autolayout.ui_state import AutolayoutUIState

if TYPE_CHECKING:
    from atopile.server.domains.layout import LayoutService
    from atopile.server.domains.layout_models import WsMessage

log = logging.getLogger(__name__)

MAX_BACKUP_FILES = 20

# Public re-exports (preserve `from atopile.autolayout.service import …`)
__all__ = ["AutolayoutService", "PreCheckItem", "PreviewResult"]


class AutolayoutService:
    """Backend-owned autolayout job lifecycle manager.

    Owns *all* autolayout state — jobs, preview, preflight, credits,
    and transient UI flags (loading / submitting / error).  The websocket
    handler never stores ``_al_*`` fields; it reads from here via
    :meth:`get_ui_state`.
    """

    def __init__(
        self,
        *,
        layout_service: LayoutService | None = None,
        on_state_changed: Callable[[], None] | None = None,
        on_job_completed: Callable[[AutolayoutJob], None] | None = None,
        state_dir: Path | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._state_dir = state_dir or Path.home() / ".atopile" / "autolayout"
        self._state_path = self._state_dir / "jobs.json"
        self._on_state_changed = on_state_changed
        self._on_job_completed = on_job_completed

        self._jobs: dict[str, AutolayoutJob] = job_store.load(self._state_path)
        self._client_provider = DeepPCBClientProvider()

        # UI state — a separate object so the concerns don't tangle.
        self.ui = AutolayoutUIState(on_change=self._notify)

        # Seed with already-completed jobs so only NEW completions
        # trigger the on_job_completed callback.
        self._completed_job_ids: set[str] = {
            j.job_id
            for j in self._jobs.values()
            if j.state
            in (AutolayoutState.COMPLETED, AutolayoutState.AWAITING_SELECTION)
        }

        # Background runner.
        self._runner = JobRunner(
            lock=self._lock,
            jobs=self._jobs,
            client_provider=self._client_provider,
            state_dir=self._state_dir,
            persist=self._persist,
            emit=self._emit,
            refresh_job=self.refresh_job,
        )

        # Resume polling for jobs that were still active at shutdown.
        for job in self._jobs.values():
            if job.state not in TERMINAL_STATES and job.provider_job_ref:
                self._runner.resume(job.job_id)

        # Layout service reference — wired via attach_layout_service()
        self._layout_service: LayoutService | None = None
        if layout_service is not None:
            self.attach_layout_service(layout_service)

    # -- UI state pass-through ---------------------------------------------
    # Kept on the service for backwards-compat with the websocket layer,
    # which reads ``svc.loading`` etc. directly.

    @property
    def loading(self) -> bool:
        return self.ui.loading

    @property
    def submitting(self) -> bool:
        return self.ui.submitting

    @property
    def error(self) -> str | None:
        return self.ui.error

    @property
    def preview_job_id(self) -> str | None:
        return self.ui.preview_job_id

    @property
    def preview_candidate_id(self) -> str | None:
        return self.ui.preview_candidate_id

    @property
    def preview_path(self) -> str | None:
        return self.ui.preview_path

    @property
    def is_previewing(self) -> bool:
        return self.ui.is_previewing

    @property
    def preflight(self) -> dict[str, Any] | None:
        return self.ui.preflight

    @property
    def preflight_loading(self) -> bool:
        return self.ui.preflight_loading

    @property
    def preflight_error(self) -> str | None:
        return self.ui.preflight_error

    @property
    def project_root(self) -> str | None:
        return self.ui.project_root

    def begin_loading(self) -> None:
        self.ui.begin_loading()

    def end_loading(self, error: str | None = None) -> None:
        self.ui.end_loading(error)

    def begin_submitting(self) -> None:
        self.ui.begin_submitting()

    def end_submitting(self, error: str | None = None) -> None:
        self.ui.end_submitting(error)

    def set_error(self, message: str | None) -> None:
        self.ui.set_error(message)

    def clear_error(self) -> None:
        self.ui.clear_error()

    def end_preview(self) -> None:
        """Clear preview state. Called after applying a candidate or
        when returning to the project layout."""
        self.ui.end_preview()

    # -- Layout service integration ----------------------------------------

    def attach_layout_service(self, layout_service: LayoutService) -> None:
        """Wire up to the layout service for preview lifecycle and
        automatic preflight recomputation on layout edits.

        Idempotent — re-attaching the same service is a no-op; attaching
        a different one is rejected (the singleton is shared across all
        callers and only ever sees one real LayoutService)."""
        if self._layout_service is layout_service:
            return
        if self._layout_service is not None:
            raise RuntimeError(
                "AutolayoutService is already attached to a different "
                "LayoutService — refusing to re-attach"
            )
        self._layout_service = layout_service
        layout_service.add_listener(self._on_layout_changed)

    async def _on_layout_changed(self, _message: WsMessage) -> None:
        """Recompute preflight when the layout changes (listener callback).

        Skips recomputation while previewing a candidate — the preview
        file is not the real project layout.
        """
        if self.is_previewing:
            return
        ls = self._layout_service
        if ls is None or not ls.is_loaded:
            return
        if self.project_root is None:
            return

        current_path = ls.current_path
        if current_path is None:
            return

        try:
            pcb = ls.manager.pcb
            summary = compute_preflight_summary(pcb=pcb)
            self.ui.set_preflight_silent(summary)
        except Exception:
            log.exception("Auto-preflight recomputation failed")

    def _notify(self) -> None:
        """Tell the websocket layer to push a fresh store snapshot."""
        if self._on_state_changed:
            self._on_state_changed()

    # -- Public API ---------------------------------------------------------

    def set_project_root(self, project_root: str | None) -> None:
        """Update the active project root and clear stale preview state."""
        self.ui.set_project_root(project_root)

    def start_job(
        self,
        project_root: str,
        build_target: str,
        *,
        job_type: JobType = JobType.PLACEMENT,
        timeout_minutes: int = 10,
        layout_path: str | None = None,
    ) -> AutolayoutJob:
        """Create and submit a new autolayout job."""
        now = utc_now_iso()
        job_id = str(uuid.uuid4())
        work_dir = self._state_dir / "jobs" / job_id
        work_dir.mkdir(parents=True, exist_ok=True)

        # Resolve layout path: use provided path or fall back to search
        if layout_path is None:
            resolved = self._resolve_layout_path(project_root, build_target)
            layout_path = str(resolved)

        job = AutolayoutJob(
            job_id=job_id,
            project_root=project_root,
            build_target=build_target,
            provider="deeppcb",
            job_type=job_type,
            state=AutolayoutState.SUBMITTING,
            message="Submitting",
            created_at=now,
            updated_at=now,
            layout_path=layout_path,
            work_dir=str(work_dir),
            options={
                "timeoutMinutes": timeout_minutes,
            },
        )

        with self._lock:
            self._jobs[job_id] = job
            self._persist()

        self._emit(job)
        self._runner.start(job_id)

        return copy.deepcopy(job)

    def get_job(self, job_id: str) -> AutolayoutJob | None:
        """Return a copy of a job by ID."""
        with self._lock:
            job = self._jobs.get(job_id)
            return copy.deepcopy(job) if job else None

    def list_jobs(self, project_root: str | None = None) -> list[AutolayoutJob]:
        """Return jobs, optionally filtered by project root."""
        with self._lock:
            jobs = list(self._jobs.values())
        if project_root:
            jobs = [j for j in jobs if j.project_root == project_root]
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return [copy.deepcopy(j) for j in jobs]

    def refresh_job(self, job_id: str) -> AutolayoutJob | None:
        """Poll DeepPCB for updated job status."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.state in TERMINAL_STATES:
                return copy.deepcopy(job) if job else None
            if not job.provider_job_ref:
                return copy.deepcopy(job)

        client = self._client_provider.require()

        try:
            data = client.get_board_raw(job.provider_job_ref)
        except DeepPCBClientError as exc:
            log.warning("Failed to poll board %s: %s", job.provider_job_ref, exc)
            return self.get_job(job_id)

        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None

            # Map DeepPCB board status → our state
            board_status = data.get("boardStatus", "")
            job.state = status_mapping.map_board_status(board_status)
            job.message = status_mapping.friendly_status(board_status)

            # Extract progress from result
            result = data.get("result", {})
            if result:
                total = result.get("totalAirWires", 0)
                connected = result.get("airWiresConnected", 0)
                if total > 0:
                    job.progress = connected / total

            # Extract candidates from workflow revisions
            workflows = data.get("workflows", [])
            for wf in workflows:
                revisions = wf.get("revisions", [])
                if revisions:
                    candidates = status_mapping.revisions_to_candidates(revisions)
                    self._merge_candidates(job, candidates)

            # Promote completed → awaiting_selection
            if (
                job.state == AutolayoutState.COMPLETED
                and job.candidates
                and not job.applied_candidate_id
            ):
                job.state = AutolayoutState.AWAITING_SELECTION
                job.message = "Ready"

            job.mark_updated()
            self._persist()

        self._emit(job)
        return copy.deepcopy(job)

    def select_candidate(self, job_id: str, candidate_id: str) -> AutolayoutJob:
        """Mark a candidate as selected (for preview)."""
        with self._lock:
            job = self._jobs[job_id]
            if not any(c.candidate_id == candidate_id for c in job.candidates):
                raise KeyError(f"Candidate {candidate_id} not found in job {job_id}")
            job.selected_candidate_id = candidate_id
            job.mark_updated()
            self._persist()
        self._emit(job)
        return copy.deepcopy(job)

    def preview_candidate(self, job_id: str, candidate_id: str) -> Path:
        """Download and convert a candidate for preview."""
        job, out_dir = self._resolve_candidate(job_id, candidate_id)
        deeppcb_path = self._download_candidate(job, candidate_id, out_dir)
        return self._convert_candidate(
            deeppcb_path, Path(job.layout_path), out_dir, job
        )

    def apply_active_preview(self) -> AutolayoutJob | None:
        """Persist the currently-previewed candidate to the project layout
        so the next submit uploads what the user sees. No-op if no preview
        is active."""
        job_id = self.ui.preview_job_id
        candidate_id = self.ui.preview_candidate_id
        if not job_id or not candidate_id:
            return None
        try:
            applied = self.apply_candidate(job_id, candidate_id)
        finally:
            self.ui.end_preview()
        return applied

    def apply_candidate(
        self, job_id: str, candidate_id: str | None = None
    ) -> AutolayoutJob:
        """Apply a candidate layout to the real PCB file."""
        with self._lock:
            job = self._jobs[job_id]
            cid = candidate_id or job.selected_candidate_id
            if not cid:
                raise ValueError("No candidate specified or selected")

        job, out_dir = self._resolve_candidate(job_id, cid)
        deeppcb_path = self._download_candidate(job, cid, out_dir)
        result_path = self._convert_candidate(
            deeppcb_path, Path(job.layout_path), out_dir, job
        )
        self._apply_candidate_to_layout(result_path, Path(job.layout_path), job, cid)

        with self._lock:
            return copy.deepcopy(self._jobs[job_id])

    def cancel_job(self, job_id: str) -> AutolayoutJob:
        """Cancel a running job."""
        with self._lock:
            job = self._jobs[job_id]
            if job.state in TERMINAL_STATES:
                return copy.deepcopy(job)

        if job.provider_job_ref:
            client = self._client_provider.require()
            try:
                client.stop_board(job.provider_job_ref)
            except DeepPCBClientError as exc:
                # 409 means the job already finished/stopped on the provider
                # side — treat as a successful cancel.
                if exc.status_code != 409:
                    raise

        with self._lock:
            job = self._jobs[job_id]
            job.state = AutolayoutState.CANCELLED
            job.message = "Cancelled"
            job.mark_updated()
            self._persist()

        self._emit(job)
        return copy.deepcopy(job)

    def get_preflight(
        self,
        project_root: str,
        build_target: str,
        *,
        pcb: Any | None = None,
    ) -> dict[str, Any]:
        """Compute preflight metrics for a build target.

        When *pcb* is provided the in-memory model is used instead of
        reading the layout file from disk.
        """
        if pcb is not None:
            return compute_preflight_summary(pcb=pcb)
        layout_path = self._resolve_layout_path(project_root, build_target)
        return compute_preflight_summary(layout_path)

    def compute_and_store_preflight(
        self,
        project_root: str,
        build_target: str,
        *,
        pcb: Any | None = None,
    ) -> None:
        """Compute preflight and store the result on this service instance."""
        self.ui.begin_preflight()
        try:
            summary = self.get_preflight(project_root, build_target, pcb=pcb)
            self.ui.set_preflight(summary)
        except Exception:
            log.exception("preflight computation failed")
            self.ui.fail_preflight("Failed to compute preflight metrics")

    # -- Best-candidate selection / auto-preview policy --------------------

    def recommended_candidate_id(self, job: AutolayoutJob) -> str | None:
        """Return the recommended candidate for a job — the one with
        the highest routed-air-wires percentage. Falls back to the
        first candidate if no routing stats are available."""
        if not job.candidates:
            return None
        best: AutolayoutCandidate | None = None
        best_pct = -1.0
        for c in job.candidates:
            connected = c.metadata.get("airWiresConnected")
            total = c.metadata.get("totalAirWires")
            if connected is not None and total and total > 0:
                pct = float(connected) / float(total)
                if pct > best_pct:
                    best_pct = pct
                    best = c
        if best is None:
            best = job.candidates[0]
        return best.candidate_id

    def auto_preview_best(self, job_id: str) -> PreviewResult | None:
        """Convert + cache the best candidate, mark it as previewing,
        and return the artifact path. The websocket layer is responsible
        for actually swapping the layout viewer to that path.

        Returns None if the job has no candidates.
        """
        job = self.get_job(job_id)
        if job is None:
            return None
        candidate_id = self.recommended_candidate_id(job)
        if candidate_id is None:
            return None
        preview_path = self.preview_candidate(job_id, candidate_id)
        return self.ui.set_preview(job_id, candidate_id, preview_path)

    def begin_preview(self, job_id: str, candidate_id: str) -> PreviewResult:
        """Convert the candidate (cached on disk) and mark it as the
        active preview. Returns the preview path so the caller can
        swap the layout viewer."""
        preview_path = self.preview_candidate(job_id, candidate_id)
        return self.ui.set_preview(job_id, candidate_id, preview_path)

    # -- Readiness pre-checks -----------------------------------------------

    def placement_readiness(self) -> list[PreCheckItem]:
        """Pre-checks that must pass before submitting a placement job."""
        return readiness.placement_readiness(self.preflight)

    def routing_readiness(self) -> list[PreCheckItem]:
        """Pre-checks that must pass before submitting a routing job."""
        return readiness.routing_readiness(self.preflight)

    # -- Candidate pipeline -------------------------------------------------

    def _resolve_candidate(
        self, job_id: str, candidate_id: str
    ) -> tuple[AutolayoutJob, Path]:
        """Validate candidate exists and return (job, out_dir)."""
        with self._lock:
            job = self._jobs[job_id]
            if not any(c.candidate_id == candidate_id for c in job.candidates):
                raise KeyError(f"Candidate {candidate_id} not found")
            if not job.provider_job_ref:
                raise RuntimeError("No provider job ref for download")

        work_dir = Path(job.work_dir) if job.work_dir else self._state_dir / "tmp"
        out_dir = work_dir / "artifacts" / candidate_id
        out_dir.mkdir(parents=True, exist_ok=True)
        return job, out_dir

    def _download_candidate(
        self, job: AutolayoutJob, candidate_id: str, out_dir: Path
    ) -> Path:
        """Download candidate artifact from DeepPCB if not cached."""
        deeppcb_path = out_dir / "artifact.deeppcb"
        if not deeppcb_path.exists():
            client = self._client_provider.require()
            candidate = next(
                c for c in job.candidates if c.candidate_id == candidate_id
            )
            revision_num = candidate.metadata.get("revision_number")
            artifact_content = client.download_revision_artifact(
                job.provider_job_ref,
                revision=revision_num,
                artifact_type="JsonFile",
            )
            deeppcb_path.write_text(artifact_content)
            log.info("Downloaded artifact for candidate %s", candidate_id)
        return deeppcb_path

    def _convert_candidate(
        self, deeppcb_path: Path, layout_path: Path, out_dir: Path, job: AutolayoutJob
    ) -> Path:
        """Convert DeepPCB artifact to KiCad format."""
        result_path = out_dir / f"result{layout_path.suffix}"
        artifact_content = deeppcb_path.read_text()
        hl_result = eda_convert.deeppcb_to_hl(artifact_content)
        eda_convert.save_hl(hl_result, layout_path, result_path, job_type=job.job_type)
        log.info("Converted candidate to KiCad format")
        return result_path

    def _apply_candidate_to_layout(
        self,
        result_path: Path,
        layout_path: Path,
        job: AutolayoutJob,
        candidate_id: str,
    ) -> None:
        """Backup original layout and replace with converted result."""
        if not layout_path.exists():
            raise FileNotFoundError(f"Layout path not found: {layout_path}")
        if not result_path.exists():
            raise RuntimeError("Cannot apply: converted KiCad file not available")

        backup_path = layout_path.with_name(f"{layout_path.name}.bak.{job.job_id[:8]}")
        shutil.copy2(layout_path, backup_path)
        self._cleanup_backups(layout_path)
        shutil.copy2(result_path, layout_path)

        with self._lock:
            job = self._jobs[job.job_id]
            job.applied_candidate_id = candidate_id
            job.applied_layout_path = str(layout_path)
            job.backup_layout_path = str(backup_path)
            job.state = AutolayoutState.COMPLETED
            job.message = "Applied"
            job.mark_updated()
            self._persist()

        self._emit(job)

    def _cleanup_backups(self, layout_path: Path) -> None:
        """Keep at most MAX_BACKUP_FILES backups for a layout, deleting the oldest."""
        pattern = f"{layout_path.name}.bak.*"
        backups = sorted(
            layout_path.parent.glob(pattern),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in backups[MAX_BACKUP_FILES:]:
            old.unlink()
            log.debug("Removed old backup: %s", old)

    # -- Helpers ------------------------------------------------------------

    def _merge_candidates(
        self, job: AutolayoutJob, new_candidates: list[AutolayoutCandidate]
    ) -> None:
        """Merge new candidates into job, deduplicating by ID."""
        existing = {c.candidate_id: c for c in job.candidates}
        for c in new_candidates:
            existing[c.candidate_id] = c
        job.candidates = list(existing.values())

    def _emit(self, job: AutolayoutJob) -> None:
        """Notify the websocket layer that state changed.

        If the job just completed (first time), also fire the
        on_job_completed callback so the websocket handler can
        auto-preview the best candidate.
        """
        self._notify()
        if (
            job.state in (AutolayoutState.COMPLETED, AutolayoutState.AWAITING_SELECTION)
            and self._on_job_completed
            and job.candidates
            and job.job_id not in self._completed_job_ids
        ):
            self._completed_job_ids.add(job.job_id)
            self._on_job_completed(copy.deepcopy(job))

    def _resolve_layout_path(self, project_root: str, build_target: str) -> Path:
        """Find the KiCad PCB layout file for a build target."""
        from atopile.model.builds import resolve_build_target_config

        build_cfg = resolve_build_target_config(project_root, build_target)
        layout = build_cfg.paths.layout
        if not layout.exists():
            raise FileNotFoundError(
                f"No layout found for target '{build_target}' in {project_root}"
            )
        return layout

    # -- Persistence --------------------------------------------------------

    def _persist(self) -> None:
        """Write jobs to disk. Must be called with lock held."""
        job_store.persist(self._state_path, self._jobs)
