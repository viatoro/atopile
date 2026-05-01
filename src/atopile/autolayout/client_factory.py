"""DeepPCB client provider with transparent token rotation.

Owns the single :class:`DeepPCBClient` instance and rebuilds it when
the gateway auth token changes. Callers get a client via
:meth:`require` — this is the same pattern the service used inline,
lifted into a small object so it's independently testable.
"""

from __future__ import annotations

from typing import Any


class DeepPCBClientProvider:
    """Lazy-built DeepPCB client with token-refresh awareness."""

    def __init__(self) -> None:
        self._client: Any = None
        self._last_token: str | None = None
        self._client = self._make()

    def _make(self) -> Any:
        """Create a DeepPCB client using gateway auth."""
        from atopile.agent.gateway import DEEPPCB_BASE_URL, get_token
        from atopile.autolayout.deeppcb.client import DeepPCBClient

        token = get_token()
        if token:
            self._last_token = token
            return DeepPCBClient(auth_token=token, base_url=DEEPPCB_BASE_URL)
        return None

    def require(self) -> Any:
        """Return the client, recreating if the auth token refreshed.

        Raises ``RuntimeError`` if no token is available.
        """
        from atopile.agent.gateway import get_token

        token = get_token()
        if token and token != self._last_token:
            if self._client is not None:
                self._client.close()
            self._client = self._make()

        if self._client is None:
            self._client = self._make()

        if self._client is None:
            raise RuntimeError("DeepPCB not configured. Sign in via the extension.")
        return self._client
