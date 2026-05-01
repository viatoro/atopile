from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import lsprotocol.types as lsp
from pygls import uris
from watchdog.events import FileSystemEventHandler, FileSystemMovedEvent
from watchdog.observers import Observer

from atopile.config import find_project_dir
from atopile.logging import get_logger
from atopile.lsp.forkserver_protocol import (
    CompileRequest,
    CompileSummary,
    QueryRequest,
    WorkerResponse,
)
from atopile.lsp.forkserver_worker import worker_main
from atopile.mp_context import get_mp_context, shutdown_forkserver

logger = get_logger(__name__)

FORKSERVER_PRELOAD = ("atopile.lsp.forkbase_preload",)
APP_ROOT = Path(__file__).resolve().parents[3]
FORKBASE_ROOTS = tuple(
    root
    for root in (
        APP_ROOT / "src" / "atopile" / "compiler",
        APP_ROOT / "src" / "faebryk" / "core",
        APP_ROOT / "src" / "faebryk" / "library",
    )
    if root.exists()
)
IGNORED_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "zig-out",
}
WATCHED_SUFFIXES = {".ato", ".py", ".yaml"}
IDLE_WORKER_POOL_SIZE = 1
MAX_COMPILES_PER_WORKER = 1


@dataclass
class ManagedDocument:
    uri: str
    path: Path
    source: str
    version: int = 0
    requested_generation: int = 0
    compiled_generation: int = -1
    diagnostics: list[lsp.Diagnostic] = field(default_factory=list)
    dependency_paths: set[Path] = field(default_factory=set)
    query_capabilities: set[str] = field(default_factory=set)
    dirty: bool = True
    dirty_reason: str | None = "initial"
    phase_timings_ms: dict[str, float] = field(default_factory=dict)
    startup_timings_ms: dict[str, float] = field(default_factory=dict)
    profile_counts: dict[str, int] = field(default_factory=dict)
    compiled_file_timings_ms: dict[str, float] = field(default_factory=dict)


@dataclass
class WorkerHandle:
    process: Any
    conn: Any
    spawn_started_at: float | None = None
    spawn_returned_at: float | None = None
    spawn_ms: float | None = None


class _ScopedWatcher(FileSystemEventHandler):
    def __init__(self, broker: "ForkserverBroker", *, kind: str) -> None:
        super().__init__()
        self._broker = broker
        self._kind = kind

    def on_created(self, event) -> None:
        self._handle_single_path_event(event)

    def on_deleted(self, event) -> None:
        self._handle_single_path_event(event)

    def on_modified(self, event) -> None:
        self._handle_single_path_event(event)

    def on_moved(self, event) -> None:
        if event.is_directory:
            return
        self._dispatch_path(Path(event.src_path))
        if isinstance(event, FileSystemMovedEvent) and event.dest_path:
            self._dispatch_path(Path(event.dest_path))

    def _handle_single_path_event(self, event) -> None:
        if event.is_directory:
            return
        self._dispatch_path(Path(event.src_path))

    def _dispatch_path(self, path: Path) -> None:
        if any(part in IGNORED_PARTS for part in path.parts):
            return
        if path.suffix not in WATCHED_SUFFIXES and path.name != "ato.yaml":
            return
        self._broker.handle_watched_path(path.resolve(), kind=self._kind)


class ForkserverBroker:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._shutting_down = threading.Event()
        self._docs: dict[str, ManagedDocument] = {}
        self._epoch = 0
        self._compile_count = 0
        self._mp_ctx = None
        self._active_worker: WorkerHandle | None = None
        self._idle_workers: list[WorkerHandle] = []
        self._pool_refill_thread: threading.Thread | None = None
        self._pool_generation = 0
        self._process = None
        self._conn = None
        self._workspace_root: Path | None = None
        self._watcher: Observer | None = None
        self._forkbase_watch_handles: dict[Path, Any] = {}
        self._dependency_watch_handles: dict[Path, Any] = {}
        self._dependency_watch_roots: set[Path] = set()

    def initialize(self, root_uri: str | None) -> None:
        with self._lock:
            self._shutting_down.clear()
            self._workspace_root = None
            if root_uri:
                fs_path = uris.to_fs_path(root_uri)
                if fs_path:
                    self._workspace_root = Path(fs_path).resolve()
            self._configure_preload_project_locked(None)
            self._ensure_watcher_locked()

    def shutdown(self) -> None:
        self._shutting_down.set()
        watcher: Observer | None = None
        with self._lock:
            watcher = self._watcher
            self._watcher = None
            self._forkbase_watch_handles.clear()
            self._dependency_watch_handles.clear()
            self._dependency_watch_roots.clear()

        if watcher is not None:
            watcher.stop()
            watcher.join(timeout=2)

        with self._lock:
            self._stop_worker()
            self._docs.clear()
            self._mp_ctx = None
            self._workspace_root = None
        shutdown_forkserver()

    def open_or_update(self, uri: str, source: str, version: int) -> ManagedDocument:
        with self._lock:
            path = Path(uris.to_fs_path(uri) or uri).resolve()
            doc = self._docs.get(uri)
            if doc is None:
                doc = ManagedDocument(
                    uri=uri, path=path, source=source, version=version
                )
                self._docs[uri] = doc
            else:
                doc.path = path
                doc.source = source
                doc.version = version
            self._bump_dirty(doc, reason=f"buffer update version={version}")
            self._compile_locked(doc)
            return doc

    def get_document(self, uri: str) -> ManagedDocument | None:
        return self._docs.get(uri)

    def close(self, uri: str) -> None:
        with self._lock:
            removed = self._docs.pop(uri, None)
            self._refresh_dependency_watches_locked()
            if removed is None:
                return
            # Closing a document is a deterministic recycle boundary. Prefer
            # dropping child state outright over preserving partial in-child
            # state across closes.
            self._stop_worker()

    def compile_document(self, uri: str) -> ManagedDocument:
        with self._lock:
            doc = self._docs[uri]
            self._compile_locked(doc)
            return doc

    def handle_watched_path(self, path: Path, *, kind: str) -> None:
        if self._shutting_down.is_set():
            return
        with self._lock:
            if self._shutting_down.is_set():
                return
            if kind == "forkbase":
                self._invalidate_forkbase_locked(path)
                return
            self.invalidate_by_path(path)

    def invalidate_by_path(self, path: Path) -> None:
        with self._lock:
            resolved = path.resolve()
            synced_open_doc = next(
                (
                    doc
                    for doc in self._docs.values()
                    if resolved == doc.path and self._path_matches_overlay_source(doc)
                ),
                None,
            )
            if synced_open_doc is not None:
                logger.debug(
                    "Ignoring watcher invalidation for open buffer "
                    "path=%s reason=overlay-synced",
                    resolved,
                )
                for doc in self._docs.values():
                    if doc.uri == synced_open_doc.uri:
                        continue
                    if resolved in doc.dependency_paths:
                        self._bump_dirty(doc, reason=f"dependency change {resolved}")
                return
            if self._is_project_modules_path(resolved):
                self._invalidate_forkbase_locked(resolved)
                return
            for doc in self._docs.values():
                if resolved == doc.path:
                    self._bump_dirty(doc, reason=f"watcher path change {resolved}")
                    continue
                if resolved in doc.dependency_paths:
                    self._bump_dirty(doc, reason=f"dependency change {resolved}")

    def query(self, uri: str, method: str, payload: dict[str, Any]) -> Any:
        with self._lock:
            doc = self._docs[uri]
            self._compile_locked(doc)
            if method not in doc.query_capabilities:
                logger.debug(
                    "Broker query unsupported uri=%s method=%s generation=%s "
                    "capabilities=%s",
                    uri,
                    method,
                    doc.requested_generation,
                    sorted(doc.query_capabilities),
                )
                return None

            request = QueryRequest(
                method=method,
                uri=uri,
                generation=doc.requested_generation,
                payload=payload,
            )

            for _attempt in range(2):
                try:
                    response = self._send_request_locked(request)
                except Exception:
                    logger.warning(
                        "LSP worker query failed, restarting and recompiling",
                        exc_info=True,
                    )
                    self._restart_worker_locked()
                    self._mark_stale_locked(
                        doc,
                        reason=f"worker query retry epoch={self._epoch}",
                    )
                    self._compile_locked(doc)
                    if method not in doc.query_capabilities:
                        return None
                    continue

                if response.ok:
                    return response.result
                if response.error == "generation mismatch":
                    logger.info(
                        "LSP worker query generation mismatch; "
                        "restarting and recompiling"
                    )
                    self._restart_worker_locked()
                    self._mark_stale_locked(
                        doc,
                        reason=f"worker restart epoch={self._epoch}",
                    )
                    self._compile_locked(doc)
                    if method not in doc.query_capabilities:
                        return None
                    continue
                logger.debug(
                    "Broker query non-ok uri=%s method=%s generation=%s error=%s",
                    uri,
                    method,
                    doc.requested_generation,
                    response.error,
                )
                return None

            logger.debug(
                "Broker query exhausted retries uri=%s method=%s generation=%s",
                uri,
                method,
                doc.requested_generation,
            )
            return None

    def _ensure_watcher_locked(self) -> None:
        if self._watcher is None:
            watcher = Observer()
            watcher.daemon = True
            watcher.start()
            self._watcher = watcher

        for root in FORKBASE_ROOTS:
            if root in self._forkbase_watch_handles:
                continue
            handler = _ScopedWatcher(self, kind="forkbase")
            self._forkbase_watch_handles[root] = self._watcher.schedule(
                handler, str(root), recursive=True
            )

        self._refresh_dependency_watches_locked()

    def _refresh_dependency_watches_locked(self) -> None:
        if self._watcher is None:
            return

        roots: set[Path] = set()
        for doc in self._docs.values():
            for dep_path in doc.dependency_paths:
                if any(
                    root == dep_path or root in dep_path.parents
                    for root in FORKBASE_ROOTS
                ):
                    continue
                roots.add(dep_path.parent)

        # Watchdog unschedule() can deadlock if an in-flight callback is already
        # dispatching back into the broker while we hold the broker lock. Keep
        # dependency watches monotonic within a broker lifetime and clear them
        # only on full shutdown.
        for root in roots - self._dependency_watch_roots:
            if not root.exists():
                continue
            handler = _ScopedWatcher(self, kind="dependency")
            self._dependency_watch_handles[root] = self._watcher.schedule(
                handler, str(root), recursive=False
            )

        self._dependency_watch_roots.update(roots)

    def _bump_dirty(self, doc: ManagedDocument, *, reason: str) -> None:
        if not doc.dirty:
            doc.requested_generation += 1
        else:
            logger.debug(
                "Document already dirty uri=%s generation=%s "
                "old_reason=%s new_reason=%s",
                doc.uri,
                doc.requested_generation,
                doc.dirty_reason,
                reason,
            )
        doc.dirty = True
        doc.dirty_reason = reason
        self._reset_doc_fields(doc)

    def _mark_stale_locked(self, doc: ManagedDocument, *, reason: str) -> None:
        doc.dirty = True
        doc.dirty_reason = reason
        self._reset_doc_fields(doc)
        doc.compiled_generation = -1

    @staticmethod
    def _reset_doc_fields(doc: ManagedDocument) -> None:
        doc.query_capabilities = set()
        doc.dependency_paths = set()
        doc.phase_timings_ms = {}
        doc.startup_timings_ms = {}
        doc.profile_counts = {}
        doc.compiled_file_timings_ms = {}

    def _path_matches_overlay_source(self, doc: ManagedDocument) -> bool:
        try:
            if not doc.path.exists():
                return False
            return doc.path.read_text() == doc.source
        except Exception:
            return False

    @staticmethod
    def _infer_preload_project_root(path: Path) -> Path | None:
        resolved = path.resolve()

        for candidate in (resolved, *resolved.parents):
            if candidate.name == "modules" and candidate.parent.name == ".ato":
                return candidate.parent.parent

        return None

    def _configure_preload_project_locked(self, path: Path | None) -> None:
        project_root = self._workspace_root
        if project_root is None and path is not None:
            project_root = self._infer_preload_project_root(path)
        if project_root is None and path is not None:
            project_root = find_project_dir(path.resolve()) or path.parent

        if project_root is None:
            os.environ.pop("ATO_LSP_PRELOAD_PROJECT_ROOT", None)
            return

        project = str(project_root.resolve())
        if os.environ.get("ATO_LSP_PRELOAD_PROJECT_ROOT") == project:
            return
        os.environ["ATO_LSP_PRELOAD_PROJECT_ROOT"] = project
        if self._mp_ctx is not None:
            self._stop_worker()
            shutdown_forkserver()
            self._mp_ctx = None

    @staticmethod
    def _is_project_modules_path(path: Path) -> bool:
        parts = path.parts
        for idx in range(len(parts) - 1):
            if parts[idx] == ".ato" and parts[idx + 1] == "modules":
                return True
        return False

    def _build_compile_request(self, doc: ManagedDocument) -> CompileRequest:
        overlays = {tracked.path: tracked.source for tracked in self._docs.values()}
        return CompileRequest(
            uri=doc.uri,
            source=doc.source,
            version=doc.version,
            generation=doc.requested_generation,
            open_overlays=overlays,
            broker_spawn_started_at=(
                self._active_worker.spawn_started_at if self._active_worker else None
            ),
            broker_spawn_returned_at=(
                self._active_worker.spawn_returned_at if self._active_worker else None
            ),
        )

    def _compile_locked(self, doc: ManagedDocument) -> None:
        if not doc.dirty and doc.compiled_generation == doc.requested_generation:
            return
        self._configure_preload_project_locked(doc.path)
        logger.info(
            "Compiling uri=%s generation=%s reason=%s",
            doc.uri,
            doc.requested_generation,
            doc.dirty_reason or "unknown",
        )

        if self._compile_count >= MAX_COMPILES_PER_WORKER:
            logger.info(
                "Recycling LSP worker before compile after max compiles "
                "previous_compiles=%s max_compiles=%s",
                self._compile_count,
                MAX_COMPILES_PER_WORKER,
            )
            self._retire_active_worker_locked()

        response: WorkerResponse | None = None
        request_roundtrip_started_at = time.perf_counter()
        send_ms: float | None = None
        recv_wait_ms: float | None = None

        for _attempt in range(2):
            try:
                self._ensure_active_compile_worker_locked()
                request = self._build_compile_request(doc)
                request.broker_send_started_at = time.perf_counter()
                response, send_ms, recv_wait_ms = (
                    self._send_request_with_timings_locked(request)
                )
                break
            except Exception:
                logger.warning(
                    "LSP worker compile failed, restarting and retrying",
                    exc_info=True,
                )
                self._restart_worker_locked()
                self._mark_stale_locked(
                    doc,
                    reason=f"worker compile retry epoch={self._epoch}",
                )

        if response is None or not response.ok or response.result is None:
            doc.diagnostics = [
                lsp.Diagnostic(
                    range=lsp.Range(
                        start=lsp.Position(line=0, character=0),
                        end=lsp.Position(line=0, character=0),
                    ),
                    message=(
                        response.error
                        if response is not None and response.error
                        else "LSP worker compile failed"
                    ),
                    severity=lsp.DiagnosticSeverity.Error,
                    source="atopile",
                )
            ]
            doc.compiled_generation = doc.requested_generation
            doc.query_capabilities = set()
            doc.dirty = False
            doc.dirty_reason = None
            self._refresh_dependency_watches_locked()
            return

        summary = response.result
        assert isinstance(summary, CompileSummary)
        doc.diagnostics = summary.diagnostics
        doc.dependency_paths = set(summary.dependency_paths)
        doc.compiled_generation = doc.requested_generation
        doc.query_capabilities = set(summary.query_capabilities)
        doc.phase_timings_ms = dict(summary.phase_timings_ms)
        doc.compiled_file_timings_ms = dict(summary.compiled_file_timings_ms)
        startup_timings_ms = dict(summary.startup_timings_ms)
        startup_timings_ms["broker_request_roundtrip"] = round(
            (time.perf_counter() - request_roundtrip_started_at) * 1000, 1
        )
        if send_ms is not None:
            startup_timings_ms["broker_send_ms"] = round(send_ms, 1)
        if recv_wait_ms is not None:
            startup_timings_ms["broker_recv_wait_ms"] = round(recv_wait_ms, 1)
        if self._active_worker is not None and self._active_worker.spawn_ms is not None:
            startup_timings_ms["broker_worker_spawn"] = self._active_worker.spawn_ms
        worker_compile_total = startup_timings_ms.get("worker_compile_request_total")
        if worker_compile_total is not None:
            startup_timings_ms["broker_overhead_outside_compile"] = round(
                startup_timings_ms["broker_request_roundtrip"] - worker_compile_total,
                1,
            )
        doc.startup_timings_ms = startup_timings_ms
        doc.profile_counts = dict(summary.profile_counts)
        doc.dirty = False
        doc.dirty_reason = None
        self._compile_count += 1
        self._ensure_idle_pool_locked(sync=False)
        if summary.phase_timings_ms:
            phase_summary = " ".join(
                f"{name}={value:.1f}ms"
                for name, value in summary.phase_timings_ms.items()
            )
            startup_summary = " ".join(
                f"{name}={value:.1f}ms" for name, value in startup_timings_ms.items()
            )
            counts_summary = " ".join(
                f"{name}={value}" for name, value in summary.profile_counts.items()
            )
            logger.info(
                "Compile summary uri=%s generation=%s %s%s%s%s%s",
                doc.uri,
                doc.compiled_generation,
                phase_summary,
                " startup=" if startup_summary else "",
                startup_summary,
                " counts=" if counts_summary else "",
                counts_summary,
            )
        self._refresh_dependency_watches_locked()

    def _ensure_active_compile_worker_locked(self) -> None:
        if self._active_worker is not None and self._active_worker.process.is_alive():
            self._sync_active_worker_refs_locked()
            return

        self._active_worker = None
        self._sync_active_worker_refs_locked()

        while self._idle_workers:
            handle = self._idle_workers.pop()
            if handle.process.is_alive():
                self._active_worker = handle
                self._compile_count = 0
                self._sync_active_worker_refs_locked()
                return
            self._stop_handle(handle)

        if self._mp_ctx is None:
            self._mp_ctx = get_mp_context(forkserver_preload=FORKSERVER_PRELOAD)
        self._active_worker = self._spawn_worker_handle()
        self._compile_count = 0
        self._sync_active_worker_refs_locked()

    def _restart_worker_locked(self) -> None:
        self._stop_worker()
        self._epoch += 1
        self._compile_count = 0
        self._ensure_idle_pool_locked(sync=False)

    def _invalidate_forkbase_locked(self, changed_path: Path) -> None:
        logger.info("Invalidating forkbase from %s", changed_path)
        for doc in self._docs.values():
            self._bump_dirty(doc, reason=f"forkbase invalidation {changed_path}")
        self._stop_worker()
        shutdown_forkserver()
        self._mp_ctx = get_mp_context(forkserver_preload=FORKSERVER_PRELOAD)
        self._restart_worker_locked()

    def _stop_worker(self) -> None:
        self._pool_generation += 1
        if self._active_worker is not None:
            self._stop_handle(self._active_worker)
            self._active_worker = None
        while self._idle_workers:
            self._stop_handle(self._idle_workers.pop())
        self._compile_count = 0
        self._sync_active_worker_refs_locked()

    def _send_request_locked(self, request: object) -> WorkerResponse:
        assert self._conn is not None
        self._conn.send(request)
        response = self._conn.recv()
        assert isinstance(response, WorkerResponse)
        return response

    def _send_request_with_timings_locked(
        self, request: object
    ) -> tuple[WorkerResponse, float, float]:
        assert self._conn is not None
        send_started_at = time.perf_counter()
        self._conn.send(request)
        send_finished_at = time.perf_counter()
        response = self._conn.recv()
        recv_finished_at = time.perf_counter()
        assert isinstance(response, WorkerResponse)
        return (
            response,
            (send_finished_at - send_started_at) * 1000,
            (recv_finished_at - send_finished_at) * 1000,
        )

    def _sync_active_worker_refs_locked(self) -> None:
        if self._active_worker is None:
            self._process = None
            self._conn = None
            return
        self._process = self._active_worker.process
        self._conn = self._active_worker.conn

    def _retire_active_worker_locked(self) -> None:
        if self._active_worker is not None:
            self._stop_handle(self._active_worker)
            self._active_worker = None
            self._compile_count = 0
            self._sync_active_worker_refs_locked()

    def _ensure_idle_pool_locked(self, *, sync: bool) -> None:
        self._idle_workers = [
            handle for handle in self._idle_workers if handle.process.is_alive()
        ]
        if self._mp_ctx is None:
            self._mp_ctx = get_mp_context(forkserver_preload=FORKSERVER_PRELOAD)
        if len(self._idle_workers) >= IDLE_WORKER_POOL_SIZE:
            return
        if sync:
            while len(self._idle_workers) < IDLE_WORKER_POOL_SIZE:
                self._idle_workers.append(self._spawn_worker_handle())
            return
        if self._pool_refill_thread is not None and self._pool_refill_thread.is_alive():
            return
        pool_generation = self._pool_generation
        mp_ctx = self._mp_ctx
        thread = threading.Thread(
            target=self._fill_idle_pool_background,
            args=(pool_generation, mp_ctx),
            daemon=True,
        )
        self._pool_refill_thread = thread
        thread.start()

    def _fill_idle_pool_background(self, pool_generation: int, mp_ctx) -> None:
        handle: WorkerHandle | None = None
        try:
            handle = self._spawn_worker_handle(mp_ctx=mp_ctx)
        except Exception:
            logger.warning("Failed to prewarm idle LSP worker", exc_info=True)
        with self._lock:
            self._pool_refill_thread = None
            if handle is None:
                return
            if (
                self._shutting_down.is_set()
                or pool_generation != self._pool_generation
                or self._mp_ctx is not mp_ctx
            ):
                self._stop_handle(handle)
                return
            if len(self._idle_workers) >= IDLE_WORKER_POOL_SIZE:
                self._stop_handle(handle)
                return
            self._idle_workers.append(handle)

    def _spawn_worker_handle(self, *, mp_ctx=None) -> WorkerHandle:
        ctx = mp_ctx or self._mp_ctx
        assert ctx is not None
        parent_conn, child_conn = ctx.Pipe()
        process = ctx.Process(target=worker_main, args=(child_conn,))
        process.daemon = True
        spawn_started_at = time.perf_counter()
        process.start()
        spawn_returned_at = time.perf_counter()
        spawn_ms = round((spawn_returned_at - spawn_started_at) * 1000, 1)
        child_conn.close()
        logger.info(
            "Started LSP compiler worker epoch=%s pid=%s spawn=%.1fms",
            self._epoch,
            process.pid,
            spawn_ms,
        )
        return WorkerHandle(
            process=process,
            conn=parent_conn,
            spawn_started_at=spawn_started_at,
            spawn_returned_at=spawn_returned_at,
            spawn_ms=spawn_ms,
        )

    @staticmethod
    def _stop_handle(handle: WorkerHandle) -> None:
        try:
            handle.conn.close()
        except Exception:
            pass
        if handle.process.is_alive():
            handle.process.terminate()
            handle.process.join(timeout=2)
            if handle.process.is_alive():
                handle.process.kill()
                handle.process.join(timeout=2)


BROKER = ForkserverBroker()
