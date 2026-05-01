"""Platform-optimal multiprocessing context for build workers.

On macOS, Python defaults to 'spawn' for multiprocessing, which creates a fresh
interpreter and re-imports all modules -- adding significant overhead per build
subprocess. On Linux and macOS we use 'forkserver' which pre-loads modules in a
single-threaded server process and forks cleanly from there.

This module provides the fastest safe start method per platform:
- Linux: 'forkserver' (modules pre-loaded, forks from a clean single-threaded process)
- macOS: 'forkserver' (safe with CoreFoundation/threads, modules pre-loaded)
- Windows: 'spawn' (only available option)

WHY NOT 'fork' ON LINUX:
Plain fork() inherits all mutexes held by the parent's threads (asyncio event
loop, thread pool, orchestrator). Those threads don't survive in the child, so
their locks are permanently stuck. The child deadlocks on the first import or
logging call. Python 3.14 fixes logging and GIL locks via at-fork handlers, but
the import lock (_imp) can only be queried for the *current* thread — if another
thread held it at fork time, the child has no way to detect or release it.
forkserver avoids this entirely: its process is permanently single-threaded.

The forkserver is started eagerly in cli.py main() before any command creates
threads. Workers (ATO_BUILD_WORKER=1) skip this since they never spawn children.
"""

from __future__ import annotations

import logging
import multiprocessing
import multiprocessing.context
import os
import sys

log = logging.getLogger(__name__)

_mp_context: multiprocessing.context.BaseContext | None = None
_forkserver_preload: tuple[str, ...] = ()


def get_optimal_start_method() -> str:
    """Return the fastest safe multiprocessing start method for this OS.

    Can be overridden with ATO_MP_START_METHOD env var for testing.
    """
    override = os.environ.get("ATO_MP_START_METHOD")
    if override:
        return override

    if sys.platform == "linux":
        return "forkserver"
    elif sys.platform == "darwin":
        return "forkserver"
    else:
        return "spawn"


def _forkserver_noop() -> None:
    """No-op target used to warm up the forkserver process."""


def get_mp_context(
    *, forkserver_preload: tuple[str, ...] | list[str] | None = None
) -> multiprocessing.context.BaseContext:
    """Get a cached multiprocessing context using the optimal start method.

    For forkserver mode (Linux/macOS), eagerly starts the server process before
    any command creates threads. Called once from cli.py main().
    """
    global _mp_context, _forkserver_preload

    requested_preload = tuple(forkserver_preload or ())
    if (
        _mp_context is not None
        and requested_preload
        and requested_preload != _forkserver_preload
    ):
        shutdown_forkserver()

    if _mp_context is not None:
        return _mp_context

    method = get_optimal_start_method()
    if sys.platform == "darwin":
        os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

    if method == "forkserver" and requested_preload:
        multiprocessing.set_forkserver_preload(list(requested_preload))

    ctx = multiprocessing.get_context(method)
    if method == "forkserver":
        # Temporarily override sys.argv so the forkserver shows
        # 'atopile-forkserver' in ps instead of the parent's command.
        saved_argv = sys.argv
        sys.argv = ["atopile-forkserver"]
        try:
            warmup = ctx.Process(target=_forkserver_noop)
            warmup.start()
            warmup.join()
        finally:
            sys.argv = saved_argv

    _mp_context = ctx
    _forkserver_preload = requested_preload
    return _mp_context


def ensure_forkserver_healthy() -> bool:
    """Check if the forkserver is alive and restart it if dead.

    Returns True if healthy (or not using forkserver mode).
    """
    global _mp_context
    if _mp_context is None or get_optimal_start_method() != "forkserver":
        return True

    try:
        saved_argv = sys.argv
        sys.argv = ["atopile-forkserver"]
        try:
            probe = _mp_context.Process(target=_forkserver_noop)
            probe.start()
            probe.join(timeout=10)
            if probe.is_alive():
                probe.terminate()
                probe.join(timeout=2)
                log.warning("Forkserver probe timed out, resetting mp context")
                _mp_context = None
                return False
        finally:
            sys.argv = saved_argv
        return True
    except Exception:
        log.warning("Forkserver is dead, resetting mp context", exc_info=True)
        _mp_context = None
        return False


def shutdown_forkserver() -> None:
    """Stop the forkserver and reset the cached context."""
    global _mp_context, _forkserver_preload
    if _mp_context is None:
        return

    if get_optimal_start_method() == "forkserver":
        try:
            from multiprocessing.forkserver import _forkserver

            _forkserver._stop()
        except Exception:
            log.debug("Forkserver stop failed (may already be dead)", exc_info=True)

    _mp_context = None
    _forkserver_preload = ()
