"""Typed exceptions for the DeepPCB API client."""

from __future__ import annotations


class DeepPCBError(Exception):
    """Base exception for all DeepPCB client errors."""


class DeepPCBAPIError(DeepPCBError):
    """Raised when the DeepPCB API returns a non-success HTTP status."""

    def __init__(self, status_code: int, body: str, message: str | None = None):
        self.status_code = status_code
        self.body = body
        msg = message or f"DeepPCB API error {status_code}"
        if body:
            msg += f": {body[:500]}"
        super().__init__(msg)


class DeepPCBClientError(DeepPCBAPIError):
    """4xx client errors (bad request, not found, conflict, etc.)."""


class DeepPCBServerError(DeepPCBAPIError):
    """5xx server errors."""


class DeepPCBValidationError(DeepPCBError):
    """Raised when board or constraint validation fails."""

    def __init__(self, error: str | None = None, warnings: list[str] | None = None):
        self.error = error
        self.warnings = warnings or []
        msg = f"Validation failed: {error}" if error else "Validation failed"
        super().__init__(msg)
