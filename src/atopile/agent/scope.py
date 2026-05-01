"""Project scope — the per-turn context passed to every tool."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from atopile.agent.skills import SKILLS_DIR, load_skill

if TYPE_CHECKING:
    from atopile.agent.tools.checklist import Checklist


class ScopeError(ValueError):
    """Raised when the agent attempts to access a path outside its scope."""


@dataclass
class Scope:
    """Per-turn context for tools: file scope and shared state."""

    allowed_roots: list[Path] = field(default_factory=list)
    checklist: Checklist | None = None
    active_skills: list[str] = field(default_factory=list)
    # Callback that returns a future resolving with the ``Build`` payload
    # once ``build_id`` completes. Set by the session at turn-start so
    # tools (``build_run``) can await queued builds.
    wait_for_build: Callable[[str], asyncio.Future[dict[str, Any]]] | None = None
    _pending_documents: list[Path] = field(default_factory=list)

    def make_searchable(self, path: Path) -> None:
        """Mark a file to be uploaded for provider-side search.

        The session drains this list before the next model call.
        """
        if path not in self._pending_documents:
            self._pending_documents.append(path)

    def drain_pending_documents(self) -> list[Path]:
        """Pop all pending documents. Called by the session between tool loops."""
        docs = list(self._pending_documents)
        self._pending_documents.clear()
        return docs

    def resolve(self, path: str) -> Path:
        """Resolve a path, ensuring it falls within an allowed root."""
        raw = Path(path).expanduser()
        if raw.is_absolute():
            candidate = raw.resolve()
        else:
            # Resolve relative paths against the first allowed root (project root)
            if not self.allowed_roots:
                raise ScopeError("No allowed roots configured")
            candidate = (self.allowed_roots[0] / raw).resolve()

        if not any(candidate.is_relative_to(root) for root in self.allowed_roots):
            raise ScopeError(f"Path is outside allowed scope: {path}")
        return candidate

    @property
    def project_root(self) -> Path:
        """The primary project root (first allowed root)."""
        if not self.allowed_roots:
            raise ScopeError("No allowed roots configured")
        return self.allowed_roots[0]

    def resolve_project_path(self, raw: object) -> Path:
        """Resolve an optional relative sub-path, defaulting to project_root."""
        if not isinstance(raw, str) or not raw.strip():
            return self.project_root
        return self.resolve(raw.strip())

    def build_instructions(self) -> str:
        """Build the system prompt from active skills.

        Caches the result — only rebuilds when active_skills changes.
        """
        key = tuple(self.active_skills)
        if key != getattr(self, "_instructions_key", None):
            sections = []
            for sid in self.active_skills:
                try:
                    sections.append(load_skill(sid))
                except FileNotFoundError:
                    pass
            self._instructions_cache = "\n\n".join(sections)
            self._instructions_key = key
        return self._instructions_cache

    # Skills used only by utility providers (title gen), not meant to be
    # loaded by the agent via skills_load.
    _INTERNAL_SKILLS = frozenset({"title"})

    @staticmethod
    def available_skills() -> list[str]:
        """List all skill IDs available on disk."""
        return sorted(
            d.name
            for d in SKILLS_DIR.iterdir()
            if d.is_dir()
            and (d / "SKILL.md").exists()
            and d.name not in Scope._INTERNAL_SKILLS
        )
