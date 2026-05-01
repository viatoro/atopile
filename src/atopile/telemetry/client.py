"""Synchronous PostHog HTTP client.

Thin wrapper around the PostHog /capture/ REST endpoint.
Called from the worker thread in telemetry.py — no queue, no threads here.
"""

import datetime
import importlib.metadata
import logging
import time
import traceback
import uuid
from typing import Any

from faebryk.libs.http import http_client

log = logging.getLogger(__name__)

PH_PROJECT_KEY = "phc_IIl9Bip0fvyIzQFaOAubMYYM2aNZcn26Y784HcTeMVt"
PH_API_HOST = "https://telemetry.atopileapi.com"
PH_API_ENDPOINT = "/capture/"


class TelemetryClient:
    """Synchronous PostHog HTTP client with single-retry semantics."""

    def __init__(
        self,
        api_key: str = PH_PROJECT_KEY,
        host: str = PH_API_HOST,
    ) -> None:
        self._api_key = api_key
        self._url = f"{host.rstrip('/')}{PH_API_ENDPOINT}"
        try:
            ua = f"atopile/{importlib.metadata.version('atopile')}"
        except Exception:
            ua = "atopile/unknown"
        self._headers = {
            "Content-Type": "application/json",
            "User-Agent": ua,
        }
        self._session = http_client(headers=self._headers)
        self._http = self._session.__enter__()

    def close(self) -> None:
        self._session.__exit__(None, None, None)

    def capture(
        self,
        event: str,
        *,
        distinct_id: str | uuid.UUID | None = None,
        properties: dict[str, Any] | None = None,
        timestamp: str | None = None,
    ) -> None:
        """POST a single event. Retries once on transient failure."""
        payload: dict[str, Any] = {
            "api_key": self._api_key,
            "event": event,
            "distinct_id": str(distinct_id) if distinct_id else "",
            "properties": properties or {},
            "timestamp": timestamp
            or datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        self._post(payload)

    def capture_exception(
        self,
        exception: Exception,
        *,
        distinct_id: str | uuid.UUID | None = None,
        properties: dict[str, Any] | None = None,
    ) -> None:
        """POST an exception event. Retries once on transient failure."""
        exc_properties: dict[str, Any] = {
            "$exception_message": str(exception),
            "$exception_type": type(exception).__name__,
            "$exception_stack_trace": "".join(
                traceback.format_exception(
                    type(exception),
                    exception,
                    exception.__traceback__,
                )
            ),
        }
        if distinct_id:
            exc_properties["$exception_person"] = str(distinct_id)
        if properties:
            exc_properties.update(properties)
        self.capture(
            event="$exception",
            distinct_id=distinct_id,
            properties=exc_properties,
        )

    def _post(self, payload: dict[str, Any]) -> None:
        for attempt in range(2):
            try:
                resp = self._http.post(self._url, json=payload, timeout=5.0)
                if resp.status_code < 500:
                    return
                log.debug(
                    "Telemetry POST returned %d, attempt %d",
                    resp.status_code,
                    attempt + 1,
                )
            except Exception as e:
                log.debug(
                    "Telemetry POST failed: %s, attempt %d",
                    e,
                    attempt + 1,
                )
            if attempt == 0:
                time.sleep(0.5)
        log.debug("Telemetry event dropped after retries")
