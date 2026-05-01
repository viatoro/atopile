"""Shared auth runtime state for CLI, agent, and service clients."""

from __future__ import annotations

from threading import Lock

GATEWAY_BASE_URL = "https://gateway.atopile.io"
OPENAI_BASE_URL = f"{GATEWAY_BASE_URL}/openai/v1"
ANTHROPIC_BASE_URL = f"{GATEWAY_BASE_URL}/anthropic"
EXA_SEARCH_URL = f"{GATEWAY_BASE_URL}/exa/search"

_lock = Lock()
_auth_token: str | None = None


def set_auth_token(token: str | None) -> None:
    global _auth_token
    with _lock:
        _auth_token = token.strip() if token and token.strip() else None


def get_auth_token() -> str | None:
    with _lock:
        token = _auth_token

    if token:
        return token

    from atopile.auth.session import get_stored_access_token

    return get_stored_access_token()
