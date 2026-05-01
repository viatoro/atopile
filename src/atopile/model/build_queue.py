"""
Build queue and active build tracking.
"""

from __future__ import annotations

import multiprocessing.context
import os
import queue
import signal
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from atopile.data_models import (
    Build,
    BuildStage,
    BuildStatus,
    StageStatus,
)
from atopile.logging import get_logger, read_build_logs
from atopile.model.sqlite import BUILD_HISTORY_DB, BuildHistory

# ---------------------------------------------------------------------------
# Typed messages from build worker threads
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BuildStageMsg:
    build_id: str
    stages: list[BuildStage]


@dataclass(frozen=True)
class BuildCompletedMsg:
    build_id: str
    return_code: int
    error: str | None
    stages: list[BuildStage]


@dataclass(frozen=True)
class BuildCancelledMsg:
    build_id: str


BuildResultMsg = BuildStageMsg | BuildCompletedMsg | BuildCancelledMsg

log = get_logger(__name__)

# Build queue configuration
MAX_CONCURRENT_BUILDS = 4


def _mp_build_worker(
    build_name: str,
    build_id: str,
    db_path: str,
    build_started_at: float,
    project_root: str,
    verbose: bool = False,
    include_targets: list[str] | None = None,
    exclude_targets: list[str] | None = None,
    frozen: bool | None = None,
    keep_picked_parts: bool | None = None,
    keep_net_names: bool | None = None,
    keep_designators: bool | None = None,
    standardize_designators: bool | None = None,
    standalone: bool = False,
    entry: str | None = None,
) -> None:
    """Multiprocessing build worker — inherits parent's modules via fork."""
    import os

    os.environ["ATO_BUILD_ID"] = build_id
    os.environ["ATO_BUILD_HISTORY_DB"] = db_path
    os.environ["ATO_BUILD_WORKER"] = "1"
    os.environ["ATO_BUILD_STARTED_AT"] = str(build_started_at)
    os.environ["PYTHONUNBUFFERED"] = "1"

    if not verbose:
        sys.stdout = open(os.devnull, "w")

    os.chdir(project_root)

    # Re-initialize logging handlers. The forkserver child starts with an
    # empty root logger because multiprocessing clears logging state during
    # child bootstrap. We must manually re-install the handlers.
    import logging as _logging

    from atopile.logging import _db_handler

    root = _logging.getLogger()
    # Always re-configure handlers — forkserver children may or may not
    # inherit the parent's handlers depending on the Python version.
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(_logging.DEBUG)
    if verbose:
        from atopile.logging import handler as _console_handler

        root.addHandler(_console_handler)
    root.addHandler(_db_handler)

    # Load project config from ato.yaml and apply build options.
    # This is the equivalent of config.apply_options() in the CLI path.

    from atopile.config import config

    if not standalone:
        config.project_dir = Path(project_root)
        config.reload()
    config.apply_options(
        entry=entry,
        standalone=standalone,
        working_dir=Path(project_root),
        selected_builds=[build_name],
        include_targets=include_targets or [],
        exclude_targets=exclude_targets or [],
        frozen=frozen,
        keep_picked_parts=keep_picked_parts,
        keep_net_names=keep_net_names,
        keep_designators=keep_designators,
        standardize_designators=standardize_designators,
    )

    if os.environ.get("ATO_SAFE"):
        try:
            import resource

            resource.setrlimit(
                resource.RLIMIT_CORE,
                (resource.RLIM_INFINITY, resource.RLIM_INFINITY),
            )
        except ValueError, OSError, ImportError:
            pass

    from atopile.buildutil import run_build_worker

    run_build_worker(build_name, build_id)

    # Flush buffered log entries to the DB. The atexit handler may not
    # run reliably in multiprocessing children, so flush explicitly.
    from atopile.logging import AtoLogger

    AtoLogger.flush_all()


def _kill_process_tree(pid: int, sig: int = signal.SIGTERM) -> None:
    """Send a signal to a process and all its children."""
    # First collect children before killing the parent
    children = []
    try:
        import subprocess as _sp

        result = _sp.run(["pgrep", "-P", str(pid)], capture_output=True, text=True)
        if result.returncode == 0:
            children = [int(p) for p in result.stdout.strip().splitlines() if p.strip()]
    except Exception:
        pass

    # Kill children first, then parent
    for child_pid in children:
        try:
            os.kill(child_pid, sig)
        except OSError, ProcessLookupError:
            pass

    try:
        os.kill(pid, sig)
    except OSError, ProcessLookupError:
        pass


def _safe_terminate(
    process: multiprocessing.process.BaseProcess, timeout: float = 3
) -> None:
    """Terminate a worker process and its children, escalating to SIGKILL if needed.

    Catches OSError/ProcessLookupError so a dead process never crashes
    the caller (and by extension the server).
    """
    pid = process.pid

    try:
        _kill_process_tree(pid, signal.SIGTERM)
    except OSError, ProcessLookupError:
        return  # already dead

    process.join(timeout=timeout)
    if process.is_alive():
        log.warning("Worker %s did not exit after SIGTERM, sending SIGKILL", pid)
        _kill_process_tree(pid, signal.SIGKILL)
        process.join(timeout=2)
        if process.is_alive():
            log.error("Worker %s still alive after SIGKILL, abandoning", pid)


def _run_build(
    build: Build,
    result_q: queue.Queue[BuildResultMsg],
    cancel_flags: dict[str, bool],
    mp_ctx: multiprocessing.context.BaseContext,
    worker_processes: dict | None = None,
) -> None:
    """
    Run a single build via multiprocessing and report progress.

    Uses the platform-optimal start method (fork on Linux, forkserver on
    macOS, spawn on Windows) — always through multiprocessing, never
    subprocess.Popen.
    """
    if not build.build_id:
        raise RuntimeError("BuildQueue requires builds to have build_id set")

    process: multiprocessing.process.BaseProcess | None = None
    final_stages: list[BuildStage] = []
    error_msg: str | None = None
    return_code: int = -1

    def _register_process(proc: multiprocessing.process.BaseProcess) -> None:
        if worker_processes is not None:
            worker_processes[build.build_id] = proc

    def _unregister_process() -> None:
        if worker_processes is not None:
            worker_processes.pop(build.build_id, None)

    try:
        build_root = build.target.root

        log.info(
            "Build %s: starting worker - build=%s, cwd=%s",
            build.build_id,
            build.name,
            build_root,
        )
        process = mp_ctx.Process(
            target=_mp_build_worker,
            kwargs={
                "build_name": build.name,
                "build_id": build.build_id,
                "db_path": str(BUILD_HISTORY_DB),
                "build_started_at": build.started_at or time.time(),
                "project_root": build_root,
                "verbose": build.verbose,
                "include_targets": build.include_targets or None,
                "exclude_targets": build.exclude_targets or None,
                "frozen": build.frozen,
                "keep_picked_parts": build.keep_picked_parts,
                "keep_net_names": build.keep_net_names,
                "keep_designators": build.keep_designators,
                "standardize_designators": build.standardize_designators,
                "standalone": build.standalone,
                "entry": build.target.entry,
            },
        )
        process.start()
        _register_process(process)

        # ---- monitor loop -------------------------------------------------
        last_stages: list[BuildStage] = []
        poll_interval = 0.25
        last_emit = 0.0

        while process.is_alive():
            if cancel_flags.get(build.build_id, False):
                log.info(
                    "Build %s: cancel flag detected, terminating worker pid=%s",
                    build.build_id,
                    process.pid,
                )
                _safe_terminate(process)
                log.info(
                    "Build %s: after terminate - alive=%s, exitcode=%s",
                    build.build_id,
                    process.is_alive(),
                    process.exitcode,
                )
                _unregister_process()
                result_q.put(BuildCancelledMsg(build_id=build.build_id))
                return

            build_info = BuildHistory.get(build.build_id)
            current_stages = build_info.stages if build_info else []

            now = time.time()
            if current_stages != last_stages or (now - last_emit) >= poll_interval:
                log.debug(
                    "Build %s: stage update - %d stages",
                    build.build_id,
                    len(current_stages),
                )
                result_q.put(
                    BuildStageMsg(
                        build_id=build.build_id,
                        stages=current_stages,
                    )
                )
                last_stages = current_stages
                last_emit = now

            time.sleep(poll_interval)

        # ---- collect results ----------------------------------------------
        process.join()
        return_code = process.exitcode if process.exitcode is not None else -1

        build_info = BuildHistory.get(build.build_id)
        if build_info:
            final_stages = build_info.stages

        if return_code != 0:
            try:
                error_logs, _ = read_build_logs(
                    build_id=build.build_id,
                    log_levels=["ERROR", "ALERT"],
                    count=10,
                    order="DESC",
                )
                if error_logs:
                    error_msg = "\n".join(
                        log_entry["message"] for log_entry in reversed(error_logs)
                    )
                else:
                    error_msg = f"Build failed with code {return_code}"
            except Exception:
                error_msg = f"Build failed with code {return_code}"

    except Exception as exc:
        error_msg = str(exc)
        return_code = -1
    finally:
        if process is not None and process.is_alive():
            _safe_terminate(process)
        _unregister_process()

    result_q.put(
        BuildCompletedMsg(
            build_id=build.build_id,
            return_code=return_code,
            error=error_msg,
            stages=final_stages,
        )
    )


class BuildQueue:
    """
    Manages build execution and result fan-out for build workers.
    """

    def __init__(
        self,
        max_concurrent: int = MAX_CONCURRENT_BUILDS,
    ):
        self._max_concurrent = max(1, max_concurrent)
        self._running = False
        # Lazy-initialized on first _launch_build(). Cannot be eager because
        # the global _build_queue is created at module import time, and
        # get_mp_context() starts a forkserver that re-imports this module.
        self._mp_ctx: multiprocessing.context.BaseContext | None = None

        # Result queue for worker threads to report back
        self._result_q: queue.Queue[BuildResultMsg] = queue.Queue()

        # Cancel flags (thread-safe dict for signaling cancellation)
        self._cancel_flags: dict[str, bool] = {}
        self._cancel_lock = threading.Lock()

        # Track live worker processes for cleanup
        self._worker_processes: dict[str, multiprocessing.process.BaseProcess] = {}

        # Orchestrator thread
        self._orchestrator_thread: threading.Thread | None = None

        # Callbacks
        self.on_change: Callable[[str, str], None] | None = None
        self.on_completed: Callable[[Build], None] | None = None

    def start(self) -> None:
        """Start the result-processing thread."""
        if self._running:
            return

        self._running = True

        # Start orchestrator thread
        self._orchestrator_thread = threading.Thread(
            target=self._orchestrate, daemon=True
        )
        self._orchestrator_thread.start()
        log.info("BuildQueue: Started")

    def enqueue(self, build: Build) -> bool:
        """
        Add a build to the queue.

        Returns True if enqueued, False if already in queue/active.
        """
        if not build.build_id:
            log.error("BuildQueue: enqueue called without build_id")
            return False

        if not self._running:
            self.start()

        if build.started_at is None:
            build.started_at = time.time()
        build.status = BuildStatus.QUEUED
        log.debug("BuildQueue: Enqueued %s", build.build_id)

        # Write to DB — single source of truth
        BuildHistory.set(build)

        self._emit_change(build.build_id, "queued")

        self._launch_build(build)
        return True

    def submit_builds(self, builds: list[Build]) -> list[Build]:
        """Preflight and enqueue builds, returning queue records in input order."""
        BuildHistory.init_db()

        for build in builds:
            self.enqueue(build)

        return list(builds)

    def wait_for_builds(
        self,
        build_ids: list[str],
        on_update: Callable[[], None] | None = None,
        poll_interval: float = 0.5,
    ) -> dict[str, int]:
        """
        Block until all builds complete.

        Returns dict of build_id -> return_code.
        """
        if not build_ids:
            return {}

        pending = set(build_ids)
        results: dict[str, int] = {}

        while pending:
            to_remove: list[str] = []
            for build_id in pending:
                build = BuildHistory.get(build_id)
                if not build:
                    results[build_id] = 1
                    to_remove.append(build_id)
                    continue

                if build.status in (
                    BuildStatus.SUCCESS,
                    BuildStatus.WARNING,
                    BuildStatus.FAILED,
                    BuildStatus.CANCELLED,
                ):
                    if build.return_code is not None:
                        results[build_id] = build.return_code
                    elif build.status in (BuildStatus.SUCCESS, BuildStatus.WARNING):
                        results[build_id] = 0
                    else:
                        results[build_id] = 1
                    to_remove.append(build_id)

            for build_id in to_remove:
                pending.discard(build_id)

            if pending and on_update:
                on_update()

            if pending:
                time.sleep(poll_interval)

        if on_update:
            on_update()

        return results

    def get_queue_state(self) -> dict:
        """
        Return the full queue state for UI rendering.

        Returns dict with:
            - active: list of currently running build IDs (in no particular order)
            - pending: list of pending build IDs (in queue order)
            - max_concurrent: maximum concurrent builds
        """
        active = [
            build.build_id for build in BuildHistory.get_building() if build.build_id
        ]
        pending = [
            build.build_id for build in BuildHistory.get_queued() if build.build_id
        ]
        return {
            "active": active,
            "pending": pending,
            "max_concurrent": self._max_concurrent,
        }

    def _orchestrate(self) -> None:
        """
        Orchestrator loop for applying worker results and runtime cleanup.
        """
        while self._running:
            try:
                self._apply_results()
                self._cleanup_completed_builds()
            except Exception:
                log.exception("BuildQueue: orchestrator loop failed")
            time.sleep(0.2)

    def _emit_change(self, build_id: str, event_type: str) -> None:
        if self.on_change:
            try:
                self.on_change(build_id, event_type)
            except Exception:
                log.exception("BuildQueue: on_change callback failed")

    def _apply_results(self) -> None:
        """Apply results from worker threads."""
        while True:
            try:
                msg = self._result_q.get_nowait()
            except queue.Empty:
                break

            try:
                if isinstance(msg, BuildStageMsg):
                    # Stages tracked in DB; this just triggers notification
                    self._emit_change(msg.build_id, "stages")

                elif isinstance(msg, BuildCompletedMsg):
                    self._handle_completed(msg)

                elif isinstance(msg, BuildCancelledMsg):
                    log.info(
                        "BuildQueue: processing BuildCancelledMsg for %s",
                        msg.build_id,
                    )
                    try:
                        existing = BuildHistory.get(msg.build_id)
                        if existing:
                            # Mark any running stages as failed so the UI
                            # shows a red X instead of a spinner.
                            updated_stages = [
                                stage.model_copy(update={"status": StageStatus.FAILED})
                                if stage.status == StageStatus.RUNNING
                                else stage
                                for stage in existing.stages
                            ]
                            BuildHistory.set(
                                existing.model_copy(
                                    update={
                                        "status": BuildStatus.CANCELLED,
                                        "error": "Build cancelled by user",
                                        "stages": updated_stages,
                                    }
                                )
                            )
                        else:
                            log.warning(
                                "BuildQueue: no existing build for cancel %s",
                                msg.build_id,
                            )
                    except Exception:
                        log.exception(
                            "BuildQueue: failed to save cancelled status for %s",
                            msg.build_id,
                        )
                    with self._cancel_lock:
                        self._cancel_flags.pop(msg.build_id, None)

                    self._emit_change(msg.build_id, "cancelled")
            except Exception:
                log.exception(
                    "BuildQueue: failed to process result for %s",
                    getattr(msg, "build_id", "unknown"),
                )

    def _handle_completed(self, msg: BuildCompletedMsg) -> None:
        """Handle a build-completed message."""
        warnings = sum(1 for s in msg.stages if s.status == StageStatus.WARNING)
        errors = sum(1 for s in msg.stages if s.status == StageStatus.FAILED)
        status = BuildStatus.from_return_code(msg.return_code, warnings)

        existing = BuildHistory.get(msg.build_id)
        if existing is None:
            raise RuntimeError(f"BuildQueue: missing build history for {msg.build_id}")

        build = existing.model_copy(
            update={
                "status": status,
                "return_code": msg.return_code,
                "error": msg.error,
                "stages": msg.stages,
                "warnings": warnings,
                "errors": errors,
            }
        )

        BuildHistory.set(build)
        with self._cancel_lock:
            self._cancel_flags.pop(msg.build_id, None)

        self._emit_change(msg.build_id, "completed")

        if self.on_completed:
            build = BuildHistory.get(msg.build_id)
            if build:
                try:
                    self.on_completed(build)
                except Exception:
                    log.exception("BuildQueue: on_completed callback failed")

        if msg.error and status == BuildStatus.FAILED:
            log.error("BuildQueue: Build %s failed:\n%s", msg.build_id, msg.error)
        else:
            log.info(
                "BuildQueue: Build %s completed with status %s", msg.build_id, status
            )

    def _launch_build(self, build: Build) -> None:
        """Transition a queued build to running and start its worker thread."""
        build_id = build.build_id
        if not build_id:
            raise RuntimeError("BuildQueue requires builds to have build_id set")

        BuildHistory.set(build.model_copy(update={"status": BuildStatus.BUILDING}))
        with self._cancel_lock:
            self._cancel_flags[build_id] = False

        if self._mp_ctx is None:
            from atopile.mp_context import get_mp_context

            self._mp_ctx = get_mp_context()
        else:
            # Verify forkserver is still alive; recover if dead.
            from atopile.mp_context import ensure_forkserver_healthy

            if not ensure_forkserver_healthy():
                from atopile.mp_context import get_mp_context

                self._mp_ctx = get_mp_context()

        worker = threading.Thread(
            target=_run_build,
            args=(
                build,
                self._result_q,
                self._cancel_flags,
                self._mp_ctx,
                self._worker_processes,
            ),
            daemon=True,
            name=f"build-worker-{build_id[:8]}",
        )
        worker.start()
        log.info("BuildQueue: Started %s", build_id)
        self._emit_change(build_id, "started")

    def cancel_build(self, build_id: str) -> bool:
        """Cancel a running build.

        For QUEUED builds: immediately marks as cancelled in DB.
        For BUILDING builds: sets a flag that the monitor loop picks up.
        The monitor loop handles termination, DB update, and notification.
        """
        try:
            build = BuildHistory.get(build_id)
        except Exception:
            log.exception("BuildQueue: failed to get build %s for cancel", build_id)
            return False
        if not build:
            return False
        if build.status not in (BuildStatus.QUEUED, BuildStatus.BUILDING):
            return False

        if build.status == BuildStatus.QUEUED:
            try:
                BuildHistory.set(
                    build.model_copy(
                        update={
                            "status": BuildStatus.CANCELLED,
                            "error": "Build cancelled by user",
                        }
                    )
                )
            except Exception:
                log.exception("BuildQueue: failed to save cancel for %s", build_id)
            self._emit_change(build_id, "cancelled")
        else:
            # For running builds, just set the flag. The worker monitor thread
            # will detect it, terminate the process, and report completion.
            with self._cancel_lock:
                if build_id not in self._cancel_flags:
                    # Build has no monitor thread (shouldn't happen), mark directly
                    log.warning(
                        "BuildQueue: no cancel flag for running build %s", build_id
                    )
                    return False
                self._cancel_flags[build_id] = True
        log.info("Build %s cancel requested", build_id)
        return True

    def stop(self) -> None:
        """Stop the orchestrator thread, terminate workers, and shut down forkserver."""
        self._running = False

        # Signal all workers to cancel
        with self._cancel_lock:
            for build_id in list(self._cancel_flags.keys()):
                self._cancel_flags[build_id] = True

        # Terminate any still-running worker processes
        self._terminate_all_workers()

        # Mark any tracked builds still in non-terminal state as failed so
        # they don't stay stuck as BUILDING in the DB forever.
        for build_id in list(self._cancel_flags.keys()):
            try:
                existing = BuildHistory.get(build_id)
                if existing and existing.status in (
                    BuildStatus.QUEUED,
                    BuildStatus.BUILDING,
                ):
                    BuildHistory.set(
                        existing.model_copy(
                            update={
                                "status": BuildStatus.FAILED,
                                "error": "Build interrupted by shutdown",
                            }
                        )
                    )
            except Exception:
                log.debug("Failed to mark build %s as failed on stop", build_id)

        if self._orchestrator_thread and self._orchestrator_thread.is_alive():
            self._orchestrator_thread.join(timeout=2.0)
        self._orchestrator_thread = None

        # Shut down forkserver so it doesn't linger as an orphan
        try:
            from atopile.mp_context import shutdown_forkserver

            shutdown_forkserver()
        except Exception:
            pass
        self._mp_ctx = None

    def _terminate_all_workers(self) -> None:
        """Terminate all tracked worker processes."""
        for build_id, process in list(self._worker_processes.items()):
            log.info("BuildQueue: Terminating worker %s", build_id)
            _safe_terminate(process)

        self._worker_processes.clear()

    def clear(self) -> None:
        """Clear the queue and active set. Used for testing."""
        self.stop()
        with self._cancel_lock:
            self._cancel_flags.clear()

    def get_status(self) -> dict:
        """Return current queue status for debugging."""
        queued_builds = BuildHistory.get_queued()
        active_builds = BuildHistory.get_building()
        return {
            "pending_count": len(queued_builds),
            "active_count": len(active_builds),
            "active_builds": [
                build.build_id for build in active_builds if build.build_id
            ],
            "max_concurrent": self._max_concurrent,
            "orchestrator_running": self._running,
        }

    def get_max_concurrent(self) -> int:
        """Return the stored max concurrent builds setting."""
        return self._max_concurrent

    def set_max_concurrent(self, value: int) -> None:
        """
        Retain the setting for compatibility, but build launch is immediate.
        """
        new_max = max(1, value)
        old_max = self._max_concurrent
        self._max_concurrent = new_max
        log.info(
            "BuildQueue: max_concurrent changed from %d to %d (launch is immediate)",
            old_max,
            new_max,
        )

    def _cleanup_completed_builds(self) -> None:
        """
        Remove no-longer-live runtime tracking.
        """
        for build_id in list(self._cancel_flags):
            try:
                build = BuildHistory.get(build_id)
            except Exception:
                continue
            if build is not None and build.status in (
                BuildStatus.QUEUED,
                BuildStatus.BUILDING,
            ):
                continue
            with self._cancel_lock:
                self._cancel_flags.pop(build_id, None)


# Retained for compatibility with existing settings/UI surfaces.
_DEFAULT_MAX_CONCURRENT = os.cpu_count() or 4

# Global build queue instance.
_build_queue = BuildQueue(max_concurrent=_DEFAULT_MAX_CONCURRENT)

# Settings state
_build_settings = {
    "use_default_max_concurrent": True,
    "custom_max_concurrent": _DEFAULT_MAX_CONCURRENT,
}


__all__ = [
    "_build_queue",
    "_build_settings",
    "_DEFAULT_MAX_CONCURRENT",
    "BuildQueue",
]
