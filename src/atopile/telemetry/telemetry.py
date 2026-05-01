"""
We collect anonymous telemetry data to help improve atopile.
To opt out, add `telemetry: false` to your project's ~/atopile/telemetry.yaml file.

What we collect:
- Hashed user id so we know how many unique users we have
- Hashed project id
- Error logs
- How long the build took
- ato version
- Git hash of current commit
"""

import atexit
import logging
import queue
import threading
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable

from atopile.telemetry.client import TelemetryClient
from atopile.telemetry.config import TelemetryConfig
from atopile.telemetry.properties import TelemetryProperties, ThinProperties
from faebryk.libs.util import once

log = logging.getLogger(__name__)

QUEUE_SIZE = 256
WORKER_WAKE_INTERVAL = 0.1  # seconds
FLUSH_TIMEOUT = 3.0  # seconds


# ---------------------------------------------------------------------------
# Queue items
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _CaptureItem:
    event: str
    distinct_id: uuid.UUID
    thin: ThinProperties


@dataclass(frozen=True)
class _CaptureExceptionItem:
    exception: Exception
    distinct_id: uuid.UUID
    thin: ThinProperties


_QueueItem = _CaptureItem | _CaptureExceptionItem


# ---------------------------------------------------------------------------
# Telemetry singleton
# ---------------------------------------------------------------------------


class Telemetry:
    """Async telemetry dispatcher: queues events and sends them
    via TelemetryClient on a background thread."""

    def __init__(self) -> None:
        self._config = TelemetryConfig.load()
        self._queue: queue.Queue[_QueueItem] = queue.Queue(maxsize=QUEUE_SIZE)
        self._shutdown = threading.Event()
        self._worker: threading.Thread | None = None
        self._worker_lock = threading.Lock()
        if self._config.telemetry:
            atexit.register(self.flush)

    @once
    @staticmethod
    def get() -> "Telemetry":
        return Telemetry()

    # -- public --

    def capture(
        self,
        event: str,
        thin: ThinProperties,
    ) -> None:
        if not self._config.telemetry:
            return
        self._enqueue(
            _CaptureItem(
                event=event,
                distinct_id=self._config.id,
                thin=thin,
            )
        )

    def capture_exception(
        self,
        exc: Exception,
        thin: ThinProperties,
    ) -> None:
        if not self._config.telemetry:
            return
        self._enqueue(
            _CaptureExceptionItem(
                exception=exc,
                distinct_id=self._config.id,
                thin=thin,
            )
        )

    def flush(self) -> None:
        """Drain pending events. Blocks up to FLUSH_TIMEOUT."""
        try:
            self._shutdown.set()
            worker = self._worker
            if worker is not None and worker.is_alive():
                worker.join(timeout=FLUSH_TIMEOUT)
        except Exception as e:
            log.debug("Failed to flush telemetry on exit: %s", e, exc_info=e)

    # -- internal --

    def _enqueue(self, item: _QueueItem) -> None:
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            log.debug("Telemetry queue full, dropping event")
            return

        try:
            self._ensure_worker_started()
        except Exception as e:
            log.debug("Failed to start telemetry worker: %s", e, exc_info=e)

    def _ensure_worker_started(self) -> None:
        if self._shutdown.is_set():
            return

        with self._worker_lock:
            if self._worker is not None and self._worker.is_alive():
                return

            self._worker = threading.Thread(
                target=self._worker_loop,
                name="telemetry-worker",
                daemon=True,
            )
            self._worker.start()

    def _worker_loop(self) -> None:
        client: TelemetryClient | None = None
        try:
            while True:
                try:
                    item = self._queue.get(timeout=WORKER_WAKE_INTERVAL)
                except queue.Empty:
                    if self._shutdown.is_set() and self._queue.empty():
                        break
                    continue

                if client is None:
                    try:
                        client = TelemetryClient()
                    except Exception as e:
                        log.debug(
                            "Failed to initialize telemetry client: %s",
                            e,
                            exc_info=e,
                        )
                        self._queue.task_done()
                        if self._shutdown.is_set() and self._queue.empty():
                            break
                        continue

                try:
                    props = TelemetryProperties(thin=item.thin)
                    dump = props.dump()

                    match item:
                        case _CaptureItem():
                            client.capture(
                                event=item.event,
                                distinct_id=item.distinct_id,
                                properties=dump,
                            )
                        case _CaptureExceptionItem():
                            client.capture_exception(
                                exception=item.exception,
                                distinct_id=item.distinct_id,
                                properties=dump,
                            )
                except Exception as e:
                    log.debug("Failed to send telemetry event: %s", e, exc_info=e)

                self._queue.task_done()

                if self._shutdown.is_set() and self._queue.empty():
                    break
        finally:
            if client is not None:
                client.close()


# ---------------------------------------------------------------------------
# Thin wrappers
# ---------------------------------------------------------------------------


def capture_exception(exc: Exception, properties: dict[str, Any] | None = None) -> None:
    thin = ThinProperties(extra=properties)
    Telemetry.get().capture_exception(exc, thin)


def capture_auth_event(token: str, email: str | None = None) -> None:
    """Fire a cli:authenticated telemetry event with Clerk user info.

    The email is passed directly from the extension (extracted from the
    id_token). The access token is decoded only for the clerk_user_id.
    """
    import base64
    import json as _json

    try:
        clerk_user_id = ""
        parts = token.split(".")
        if len(parts) >= 2:
            padded = parts[1] + "=" * (-len(parts[1]) % 4)
            payload = _json.loads(base64.urlsafe_b64decode(padded))
            clerk_user_id = payload.get("sub", "")

        thin = ThinProperties(
            extra={
                "clerk_email": email or "",
                "clerk_user_id": clerk_user_id,
            }
        )
        Telemetry.get().capture("cli:authenticated", thin)
    except Exception:
        pass


@contextmanager
def capture(
    event_start: str | Callable[[], str],
    event_end: str | Callable[[], str],
    properties: dict | None = None,
) -> Generator[None, Any, None]:
    if callable(event_start):
        event_start = event_start()
    if callable(event_end):
        event_end = event_end()

    thin = ThinProperties(extra=properties)
    t = Telemetry.get()

    t.capture(event_start, thin)

    try:
        yield
    except Exception as e:
        t.capture_exception(e, thin)
        raise

    t.capture(event_end, thin)
