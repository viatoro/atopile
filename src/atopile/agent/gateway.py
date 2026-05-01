"""Gateway client — single auth point for all requests to gateway.atopile.io.

Auth tokens are managed by :mod:`atopile.auth` (keyring / file storage)
and read via ``get_auth_token()``.  Everything else gets pre-authed clients
from this module — no other code touches tokens directly.
"""

from __future__ import annotations

from threading import Lock

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from atopile.auth.runtime import (
    ANTHROPIC_BASE_URL,
    EXA_SEARCH_URL,
    GATEWAY_BASE_URL,
    OPENAI_BASE_URL,
    get_auth_token,
)

DEEPPCB_BASE_URL = f"{GATEWAY_BASE_URL}/deeppcb"

# Re-export for convenience — legacy callers import these via ``gateway``.
__all__ = [
    "ANTHROPIC_BASE_URL",
    "DEEPPCB_BASE_URL",
    "EXA_SEARCH_URL",
    "GATEWAY_BASE_URL",
    "OPENAI_BASE_URL",
    "anthropic_client",
    "get_token",
    "http_headers",
    "is_authenticated",
    "openai_client",
]

_lock = Lock()
_openai_clients: dict[tuple[float, str | None], AsyncOpenAI] = {}
_anthropic_clients: dict[tuple[float, str | None], AsyncAnthropic] = {}


def get_token() -> str | None:
    """Return the current auth token, or None if not signed in."""
    return get_auth_token()


def is_authenticated() -> bool:
    """Check if the user is signed in."""
    return get_auth_token() is not None


def openai_client(*, timeout: float = 120.0) -> AsyncOpenAI:
    """Get an OpenAI client authed against the gateway.

    Clients are cached by (timeout, token) and recreated when the token
    changes.
    """
    token = get_auth_token()
    if not token:
        raise RuntimeError("Not signed in. Sign in from the extension sidebar.")

    key = (timeout, token)
    with _lock:
        client = _openai_clients.get(key)
        if client is None:
            # Evict stale entries for this timeout with a different token
            _openai_clients.pop(
                next(
                    (k for k in _openai_clients if k[0] == timeout and k[1] != token),
                    None,  # type: ignore[arg-type]
                ),
                None,
            )
            client = AsyncOpenAI(
                api_key="gateway",
                base_url=OPENAI_BASE_URL,
                timeout=timeout,
                default_headers={"Authorization": f"Bearer {token}"},
            )
            _openai_clients[key] = client
        return client


def anthropic_client(*, timeout: float = 120.0) -> AsyncAnthropic:
    """Get an Anthropic client authed against the gateway.

    Clients are cached by (timeout, token) and recreated when the token
    changes. The gateway's Bearer token is forwarded in Authorization;
    Anthropic's upstream ``x-api-key`` is injected server-side.
    """
    token = get_auth_token()
    if not token:
        raise RuntimeError("Not signed in. Sign in from the extension sidebar.")

    key = (timeout, token)
    with _lock:
        client = _anthropic_clients.get(key)
        if client is None:
            _anthropic_clients.pop(
                next(
                    (
                        k
                        for k in _anthropic_clients
                        if k[0] == timeout and k[1] != token
                    ),
                    None,  # type: ignore[arg-type]
                ),
                None,
            )
            client = AsyncAnthropic(
                api_key="gateway",
                base_url=ANTHROPIC_BASE_URL,
                timeout=timeout,
                default_headers={"Authorization": f"Bearer {token}"},
            )
            _anthropic_clients[key] = client
        return client


def http_headers() -> dict[str, str]:
    """Auth headers for non-OpenAI gateway requests (Exa, etc.)."""
    token = get_auth_token()
    if not token:
        raise RuntimeError("Not signed in. Sign in from the extension sidebar.")
    return {"Authorization": f"Bearer {token}"}
