"""Background job submission and polling for autolayout jobs.

Owns the thread-based submit/poll machinery. Decoupled from the
service so the service can stay a plain orchestrator: the runner
takes explicit callables for persistence, emission, and job refresh
instead of reaching into service internals.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from pathlib import Path
from typing import Callable

from atopile.autolayout import eda_convert
from atopile.autolayout.client_factory import DeepPCBClientProvider
from atopile.autolayout.models import (
    TERMINAL_STATES,
    AutolayoutJob,
    AutolayoutState,
)

log = logging.getLogger(__name__)

NOOP_WEBHOOK_URL = "https://noop.atopile.io/webhook"
POLL_INTERVAL_S = 10
MAX_CONSECUTIVE_POLL_ERRORS = 30
BOARD_READY_TIMEOUT_S = 120
BOARD_READY_POLL_S = 2


class JobRunner:
    """Launches background threads that drive job submission + polling."""

    def __init__(
        self,
        *,
        lock: threading.RLock,
        jobs: dict[str, AutolayoutJob],
        client_provider: DeepPCBClientProvider,
        state_dir: Path,
        persist: Callable[[], None],
        emit: Callable[[AutolayoutJob], None],
        refresh_job: Callable[[str], AutolayoutJob | None],
    ) -> None:
        self._lock = lock
        self._jobs = jobs
        self._client_provider = client_provider
        self._state_dir = state_dir
        self._persist = persist
        self._emit = emit
        self._refresh_job = refresh_job

    # -- Public API ---------------------------------------------------------

    def start(self, job_id: str) -> None:
        """Spawn a daemon thread that submits + polls the given job."""
        thread = threading.Thread(
            target=self._submit_background,
            args=(job_id,),
            daemon=True,
        )
        thread.start()

    def resume(self, job_id: str) -> None:
        """Spawn a daemon thread that resumes polling an already-submitted job."""
        thread = threading.Thread(
            target=self._poll_until_done,
            args=(job_id,),
            daemon=True,
        )
        thread.start()

    # -- Submission ---------------------------------------------------------

    def _submit_background(self, job_id: str) -> None:
        """Submit job to DeepPCB on a background thread."""
        try:
            with self._lock:
                job = self._jobs.get(job_id)
                if job is None:
                    return

            log.info("[job %s] starting submission (type=%s)", job_id, job.job_type)
            client = self._client_provider.require()

            layout_path = Path(job.layout_path)
            if not layout_path.exists():
                raise FileNotFoundError(f"Layout not found: {layout_path}")

            self._set_message(job_id, AutolayoutState.SUBMITTING, "Loading layout")
            log.info("[job %s] loading HL from %s", job_id, layout_path)
            hl_pcb = eda_convert.load_hl(layout_path)

            self._set_message(job_id, AutolayoutState.SUBMITTING, "Converting layout")
            log.info("[job %s] converting HL → DeepPCB", job_id)
            work_dir = Path(job.work_dir) if job.work_dir else self._state_dir
            deeppcb_path = eda_convert.hl_to_deeppcb(hl_pcb, work_dir)

            self._set_message(
                job_id, AutolayoutState.SUBMITTING, "Uploading to DeepPCB"
            )
            log.info(
                "[job %s] uploading %s (%d bytes)",
                job_id,
                deeppcb_path.name,
                deeppcb_path.stat().st_size,
            )
            file_url = client.upload_board_file(deeppcb_path)
            log.info("[job %s] uploaded → %s", job_id, file_url)

            from atopile.autolayout.deeppcb.models import (
                ConfirmBoardRequest,
                CreateBoardRequest,
                RoutingType,
            )

            routing_type = RoutingType.EMPTY_BOARD
            timeout = int(job.options.get("timeoutMinutes", 10))
            webhook_token = secrets.token_urlsafe(16)

            self._set_message(job_id, AutolayoutState.SUBMITTING, "Creating board")
            log.info("[job %s] create_board request_id=%s", job_id, job_id)
            board_id = client.create_board(
                CreateBoardRequest(
                    request_id=job_id,
                    board_name=f"{job.build_target}-{job_id[:8]}",
                    routing_type=routing_type,
                    json_file_url=file_url,
                    webhook_url=NOOP_WEBHOOK_URL,
                    webhook_token=webhook_token,
                )
            )
            log.info("[job %s] create_board → board_id=%s", job_id, board_id)

            self._set_message(job_id, AutolayoutState.SUBMITTING, "Waiting for board")
            board_id = self._wait_for_board_ready(client, board_id, job_id)

            self._set_message(job_id, AutolayoutState.SUBMITTING, "Confirming board")
            log.info("[job %s] confirming board=%s", job_id, board_id)
            client.confirm_board(
                board_id,
                ConfirmBoardRequest(
                    job_type=job.job_type,
                    routing_type=routing_type,
                    timeout=timeout,
                    max_batch_timeout=60,
                    time_to_live=300,
                ),
            )
            log.info("[job %s] confirmed; entering RUNNING state", job_id)

            with self._lock:
                job = self._jobs[job_id]
                job.provider_job_ref = board_id
                job.state = AutolayoutState.RUNNING
                job.message = "Submitted"
                job.mark_updated()
                self._persist()

            self._emit(job)

            # Poll until terminal state
            self._poll_until_done(job_id)

        except Exception as exc:
            log.exception("[job %s] background submission failed", job_id)
            self.fail_job(job_id, str(exc))

    def _set_message(self, job_id: str, state: AutolayoutState, message: str) -> None:
        """Update the job's state/message and notify the UI."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.state = state
            job.message = message
            job.mark_updated()
            self._persist()
        self._emit(job)

    # -- Board readiness ----------------------------------------------------

    def _wait_for_board_ready(self, client, board_id: str, request_id: str) -> str:
        """Wait for a board to become confirmable. The final timeout error
        carries the last HTTP failure so auth/server errors don't look
        identical to "eventual consistency pending"."""
        deadline = time.time() + BOARD_READY_TIMEOUT_S
        last_error: str | None = None
        attempts = 0
        while time.time() < deadline:
            attempts += 1
            try:
                resolved_id = client.get_board_by_request_id(request_id)
                if resolved_id:
                    board_id = resolved_id
            except Exception as exc:
                last_error = f"get_board_by_request_id: {exc!r}"
                if attempts == 1 or attempts % 10 == 0:
                    log.warning(
                        "[board %s] lookup-by-request failed (attempt %d): %s",
                        request_id,
                        attempts,
                        exc,
                    )

            try:
                client.get_board(board_id)
                log.info("[board %s] ready after %d attempt(s)", board_id, attempts)
                return board_id
            except Exception as exc:
                last_error = f"get_board: {exc!r}"
                if attempts == 1 or attempts % 10 == 0:
                    log.warning(
                        "[board %s] not ready (attempt %d): %s",
                        board_id,
                        attempts,
                        exc,
                    )

            time.sleep(BOARD_READY_POLL_S)

        suffix = f" (last error: {last_error})" if last_error else ""
        raise RuntimeError(
            f"Board {board_id} not ready for confirmation after "
            f"{BOARD_READY_TIMEOUT_S}s{suffix}"
        )

    # -- Polling ------------------------------------------------------------

    def _poll_until_done(self, job_id: str) -> None:
        """Poll DeepPCB until the job reaches a terminal state."""
        consecutive_errors = 0
        while True:
            time.sleep(POLL_INTERVAL_S)

            with self._lock:
                job = self._jobs.get(job_id)
                if job is None or job.state in TERMINAL_STATES:
                    return

            try:
                self._refresh_job(job_id)
                consecutive_errors = 0
            except Exception:
                consecutive_errors += 1
                log.exception(
                    "Poll failed for job %s (%d/%d)",
                    job_id,
                    consecutive_errors,
                    MAX_CONSECUTIVE_POLL_ERRORS,
                )
                if consecutive_errors >= MAX_CONSECUTIVE_POLL_ERRORS:
                    self.fail_job(job_id, "Too many consecutive poll failures")
                    return

            with self._lock:
                job = self._jobs.get(job_id)
                if job is None or job.state in TERMINAL_STATES:
                    return

    # -- Failure ------------------------------------------------------------

    def fail_job(self, job_id: str, error: str) -> None:
        """Mark a job as failed and persist + emit."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.state = AutolayoutState.FAILED
            job.error = error
            job.message = "Failed"
            job.mark_updated()
            self._persist()
        self._emit(job)
