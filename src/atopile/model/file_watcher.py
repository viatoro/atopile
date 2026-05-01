"""Native filesystem watcher using watchdog with a shared observer.

Architecture:
- One module-level `Observer` is shared across all `FileWatcher` instances to
  keep FSEvents stream count low on macOS.
- Each `FileWatcher` owns its own `_EventDispatcher`. Per-watcher dispatchers
  are what watchdog's observer routes events to — so a watcher that scheduled
  `/foo/bar` only ever sees events under `/foo/bar`, even if another watcher
  is observing `/foo` on the same Observer. This is load-bearing: without it,
  background activity anywhere in the workspace would keep resetting a
  narrowly-scoped watcher's debounce timer.
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any, Literal, cast

from watchdog.events import FileSystemEvent, PatternMatchingEventHandler
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

from atopile.data_models import FileNode, UiProjectFilesData

log = logging.getLogger(__name__)


def _configure_watchdog_logging() -> None:
    for name in (
        "watchdog",
        "watchdog.events",
        "watchdog.observers",
        "watchdog.observers.fsevents",
        "watchdog.observers.inotify_buffer",
        "watchdog.observers.polling",
        "fsevents",
    ):
        logger = logging.getLogger(name)
        logger.setLevel(logging.WARNING)
        logger.propagate = False
        logger.disabled = True


_IGNORED_DIR_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".ato",
        ".cache",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        ".vscode",
        ".cursor",
        "__pycache__",
        "node_modules",
        "dist",
        "zig-out",
    }
)

_IGNORED_FILE_NAMES = frozenset(
    {
        ".DS_Store",
    }
)

_TRACK_DIR_EVENTS = frozenset(
    {
        ".ato",
        ".git",
    }
)

_WATCH_PATTERNS = ["*"]

# Ignore patterns for watchdog (applied early, skips contents of ignored
# directories). We only ignore contents (*/{name}/*), not the directories
# themselves, so we can detect when tracked directories like .ato and .git
# are created/deleted.
_IGNORE_PATTERNS = [
    *(f"*/{name}/*" for name in _IGNORED_DIR_NAMES),
    *(name for name in _IGNORED_FILE_NAMES),
    *(f"*/{name}" for name in _IGNORED_FILE_NAMES),
]

_WATCHER_TIMEOUT_SECONDS = 0.1


@dataclass
class FileChangeResult:
    """Watcher callback payload."""

    created: list[Path] = field(default_factory=list)
    deleted: list[Path] = field(default_factory=list)
    changed: list[Path] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.created or self.deleted or self.changed)


_ChangeCallback = Callable[[FileChangeResult], Awaitable[None] | None]
_TreeCallback = Callable[[UiProjectFilesData], Awaitable[None] | None]


class _EventDispatcher(PatternMatchingEventHandler):
    """Per-FileWatcher event handler: glob match, debounce, content-hash filter.

    One instance per `FileWatcher`. The shared Observer routes events to the
    dispatcher that scheduled the originating watch, so path scoping is
    handled by watchdog itself — no cross-watcher event bleed.
    """

    def __init__(
        self,
        name: str,
        glob: str,
        callback: Callable[[FileChangeResult], None],
        debounce_s: float,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        super().__init__(
            patterns=_WATCH_PATTERNS,
            ignore_patterns=_IGNORE_PATTERNS,
            ignore_directories=False,  # Allow events on tracked directories
            case_sensitive=False,
        )
        self._name = name
        self._glob = glob
        self._callback = callback
        self._debounce_s = debounce_s
        self._loop = loop
        self._lock = Lock()
        self._pending = FileChangeResult()
        self._timer: asyncio.TimerHandle | None = None
        self._file_hashes: dict[Path, str] = {}
        self._closed = False

    def close(self) -> None:
        """Cancel any pending timer and drop state. Safe to call repeatedly."""
        with self._lock:
            self._closed = True
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._pending = FileChangeResult()
            self._file_hashes.clear()

    def update_hash(self, path: Path) -> None:
        """Record the current content hash of `path`.

        Used by callers that wrote the file themselves so the resulting
        change event is suppressed as "no real change" on the next dispatch.
        """
        h = self._hash_file(path)
        if h is not None:
            with self._lock:
                self._file_hashes[path] = h

    def _matches_glob(self, path_str: str) -> bool:
        return fnmatch.fnmatch(path_str, self._glob) or fnmatch.fnmatch(
            Path(path_str).name, self._glob.split("/")[-1]
        )

    def _dispatch(self, path_str: str, event_type: str) -> None:
        path = Path(path_str)
        if FileWatcher._is_ignored(path, allow_tracked_dirs=True):
            return
        if not self._matches_glob(path_str):
            return

        with self._lock:
            if self._closed:
                return
            if event_type == "created":
                self._pending.created.append(path)
            elif event_type == "deleted":
                self._pending.deleted.append(path)
            elif event_type == "changed" and path not in self._pending.created:
                self._pending.changed.append(path)

        # We're on watchdog's observer thread. loop.call_later and
        # TimerHandle.cancel aren't thread-safe (they mutate the loop's
        # scheduled heap without locks), so hop to the event loop before
        # touching timers.
        self._loop.call_soon_threadsafe(self._reschedule_timer)

    def _reschedule_timer(self) -> None:
        with self._lock:
            if self._closed:
                return
            if self._timer is not None:
                self._timer.cancel()
            self._timer = self._loop.call_later(self._debounce_s, self._fire)

    def _fire(self) -> None:
        with self._lock:
            result = self._pending
            if not result:
                return
            self._pending = FileChangeResult()
            self._timer = None

        result = self._filter_by_hash(result)

        if not (result.created or result.changed or result.deleted):
            return

        sample = result.created[:2] + result.changed[:2] + result.deleted[:2]
        log.debug(
            "File watcher '%s' triggered: %s created, %s changed, %s deleted",
            self._name,
            len(result.created),
            len(result.changed),
            len(result.deleted),
        )
        log.debug(
            "File watcher '%s' sample: %s",
            self._name,
            ", ".join(str(p) for p in sample[:3]),
        )
        self._callback(result)

    def on_created(self, event: FileSystemEvent) -> None:
        self._dispatch(self._path_str(event.src_path), "created")

    def on_deleted(self, event: FileSystemEvent) -> None:
        self._dispatch(self._path_str(event.src_path), "deleted")

    def on_modified(self, event: FileSystemEvent) -> None:
        self._dispatch(self._path_str(event.src_path), "changed")

    def on_moved(self, event: FileSystemEvent) -> None:
        self._dispatch(self._path_str(event.src_path), "deleted")
        if hasattr(event, "dest_path"):
            self._dispatch(self._path_str(event.dest_path), "created")

    @staticmethod
    def _path_str(path: str | bytes) -> str:
        return path.decode() if isinstance(path, bytes) else path

    @staticmethod
    def _hash_file(path: Path) -> str | None:
        """Return SHA-256 hex digest of a file, or None if unreadable."""
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError, IOError:
            return None

    def _filter_by_hash(self, result: FileChangeResult) -> FileChangeResult:
        """Drop 'changed' entries whose content hash hasn't actually changed.

        Also updates stored hashes for created/changed files and removes
        hashes for deleted files.
        """
        truly_changed: list[Path] = []
        for p in result.changed:
            if p.is_dir():
                truly_changed.append(p)
                continue
            new_hash = self._hash_file(p)
            if new_hash is None:
                continue
            old_hash = self._file_hashes.get(p)
            if old_hash != new_hash:
                self._file_hashes[p] = new_hash
                truly_changed.append(p)

        for p in result.created:
            h = self._hash_file(p)
            if h is not None:
                self._file_hashes[p] = h

        for p in result.deleted:
            self._file_hashes.pop(p, None)

        return FileChangeResult(
            created=result.created,
            deleted=result.deleted,
            changed=truly_changed,
        )


# Module-level singleton Observer — shared so macOS doesn't hit FSEvents
# stream limits across many FileWatcher instances.
_observer: Any = None
_observer_lock = Lock()


def _get_observer() -> Any:
    """Return the shared watchdog Observer, starting it on first access.

    Falls back to the polling observer if the native backend can't start
    (e.g. when FSEvents stream limits are hit).
    """
    global _observer
    with _observer_lock:
        if _observer is None:
            _configure_watchdog_logging()
            try:
                obs = Observer(timeout=_WATCHER_TIMEOUT_SECONDS)
                obs.start()
                log.info("Using native file observer")
            except Exception as e:
                log.warning("Native observer failed (%s), falling back to polling", e)
                obs = PollingObserver(timeout=_WATCHER_TIMEOUT_SECONDS)
                obs.start()
                log.warning("Using polling file observer")
            _observer = obs
        return _observer


class FileWatcher:
    """Native filesystem watcher using watchdog.

    Watches directories for file changes matching a glob pattern.
    Uses OS-native notifications (FSEvents on macOS, inotify on Linux).
    """

    def __init__(
        self,
        name: str,
        *,
        on_change: _ChangeCallback | _TreeCallback,
        paths: Sequence[Path] | None = None,
        paths_provider: Callable[[], Sequence[Path]] | None = None,
        glob: str = "**/*",
        debounce_s: float = 0.5,
        mode: Literal["changes", "tree"] = "changes",
    ) -> None:
        if paths is None and paths_provider is None:
            raise ValueError("paths or paths_provider must be provided")

        self._name = name
        self._paths_provider = paths_provider
        self._static_paths = list(paths or [])
        self._mode = mode
        self._on_change = on_change
        self._glob = glob
        self._debounce_s = debounce_s
        self._scheduled: dict[Path, Any] = {}
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._tree: list[FileNode] | None = None
        self._dispatcher: _EventDispatcher | None = None

    @staticmethod
    def _is_ignored(path: Path, *, allow_tracked_dirs: bool = False) -> bool:
        """Return whether a path should be excluded from watch/scanning logic."""
        if path.name in _IGNORED_FILE_NAMES:
            return True
        parts = path.parts
        for i, part in enumerate(parts):
            if part in _IGNORED_DIR_NAMES:
                if (
                    allow_tracked_dirs
                    and i == len(parts) - 1
                    and part in _TRACK_DIR_EVENTS
                ):
                    return False
                return True
        return False

    @staticmethod
    def _scan_tree(dir_path: Path) -> list[FileNode]:
        nodes: list[FileNode] = []
        try:
            entries = list(dir_path.iterdir())
        except PermissionError:
            log.debug("Permission denied: %s", dir_path)
            return nodes

        for entry in entries:
            if FileWatcher._is_ignored(entry):
                continue
            if entry.is_dir():
                nodes.append(
                    FileNode(name=entry.name, children=FileWatcher._scan_tree(entry))
                )
            elif entry.is_file():
                nodes.append(FileNode(name=entry.name))
        FileWatcher._sort_tree(nodes)
        return nodes

    @staticmethod
    def _sort_tree(nodes: list[FileNode]) -> None:
        nodes.sort(
            key=lambda node: (0 if node.children is not None else 1, node.name.lower())
        )

    def _resolve_paths(self) -> set[Path]:
        paths = self._paths_provider() if self._paths_provider else self._static_paths
        return {p for p in paths if p.exists()}

    def _get_tree_root(self) -> Path | None:
        paths = self._paths_provider() if self._paths_provider else self._static_paths
        return paths[0] if paths else None

    async def _refresh_tree(self) -> bool:
        root = self._get_tree_root()
        next_tree = await asyncio.to_thread(self._scan_tree, root) if root else []
        if self._tree == next_tree:
            return False
        self._tree = next_tree
        return True

    async def watch(self, paths: Sequence[Path] | None = None) -> None:
        if paths is not None:
            self._static_paths = list(paths)
            self._wake_event.set()

        if self._task is None or self._task.done():
            if self._stop_event.is_set():
                self._stop_event = asyncio.Event()
                self._wake_event = asyncio.Event()
            self._task = asyncio.create_task(self._run())

        if self._mode == "tree":
            await self._refresh_tree()
            await self._emit_tree()

    async def _run(self) -> None:
        """Run the file watcher until stopped."""
        loop = asyncio.get_running_loop()

        def sync_callback(result: FileChangeResult) -> None:
            async def dispatch() -> None:
                if self._mode == "tree":
                    if await self._refresh_tree():
                        await self._emit_tree()
                else:
                    response = cast(_ChangeCallback, self._on_change)(result)
                    if isinstance(response, Awaitable):
                        await response
                log.debug(
                    "File watcher '%s': +%d ~%d -%d",
                    self._name,
                    len(result.created),
                    len(result.changed),
                    len(result.deleted),
                )

            loop.call_soon_threadsafe(lambda: asyncio.create_task(dispatch()))

        self._dispatcher = _EventDispatcher(
            name=self._name,
            glob=self._glob,
            callback=sync_callback,
            debounce_s=self._debounce_s,
            loop=loop,
        )
        log.info("File watcher '%s' started", self._name)

        try:
            while not self._stop_event.is_set():
                new_paths = self._resolve_paths()

                for path in set(self._scheduled.keys()) - new_paths:
                    self._unschedule_path(path)

                for path in new_paths - set(self._scheduled.keys()):
                    self._schedule_path(path)

                self._wake_event.clear()
                try:
                    await asyncio.wait_for(self._wake_event.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
        finally:
            for path in list(self._scheduled.keys()):
                self._unschedule_path(path)
            if self._dispatcher is not None:
                self._dispatcher.close()
                self._dispatcher = None
            log.info("File watcher '%s' stopped", self._name)

    def _schedule_path(self, path: Path) -> None:
        observer = _get_observer()
        assert self._dispatcher is not None
        try:
            watch = observer.schedule(self._dispatcher, str(path), recursive=True)
            self._scheduled[path] = watch
            log.debug("Watcher '%s' now watching: %s", self._name, path)
        except Exception as e:
            log.warning("Watcher '%s' failed to watch %s: %s", self._name, path, e)

    def _unschedule_path(self, path: Path) -> None:
        watch = self._scheduled.pop(path, None)
        if watch is None:
            return
        observer = _get_observer()
        try:
            observer.unschedule(watch)
            log.debug("Watcher '%s' stopped watching: %s", self._name, path)
        except Exception:
            pass

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    def notify_saved(self, path: Path) -> None:
        """Tell the watcher we just wrote this file ourselves.

        Updates the content hash so the resulting filesystem event
        is correctly recognised as unchanged and suppressed.
        """
        if self._dispatcher is not None:
            self._dispatcher.update_hash(path.resolve())

    async def _emit_tree(self) -> None:
        if self._mode != "tree":
            raise RuntimeError(
                "_emit_tree() is only supported for tree-mode FileWatcher"
            )
        log.info(
            "File watcher '%s' emitting tree root_entries=%d",
            self._name,
            len(self._tree or []),
        )
        project_root = self._get_tree_root()
        response = cast(_TreeCallback, self._on_change)(
            UiProjectFilesData(
                project_root=str(project_root) if project_root else None,
                files=self._tree or [],
            )
        )
        if isinstance(response, Awaitable):
            await response
