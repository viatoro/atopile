"""Autolayout service for DeepPCB-backed AI placement and routing.

This module is a public surface — import :class:`AutolayoutService`
from here. The service is an ordinary class; constructing it wires
everything up in one shot (layout-service listener + observer
callbacks), so there's no factory or singleton to worry about.
Whoever creates the instance owns its lifetime — in the server,
that's :class:`atopile.server.ui.websocket.CoreSocket`.

Re-exports are lazy via ``__getattr__`` so that importing a cheap
submodule like ``atopile.autolayout.models`` (which only needs
``pydantic``) does not eagerly pull in ``service`` and its
``faebryk`` dependency. The web-ide vsix build's minimal
type-generation venv relies on that distinction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from atopile.autolayout.models import PreCheckItem, PreviewResult
    from atopile.autolayout.service import AutolayoutService

__all__ = ["AutolayoutService", "PreCheckItem", "PreviewResult"]


def __getattr__(name: str) -> Any:
    if name == "AutolayoutService":
        from atopile.autolayout.service import AutolayoutService

        return AutolayoutService
    if name in ("PreCheckItem", "PreviewResult"):
        from atopile.autolayout import models

        return getattr(models, name)
    raise AttributeError(f"module 'atopile.autolayout' has no attribute {name!r}")
