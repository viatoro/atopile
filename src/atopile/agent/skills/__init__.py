"""Skill loading utilities."""

from __future__ import annotations

from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent


def load_skill(skill_id: str) -> str:
    """Load a skill's SKILL.md and return its content."""
    path = SKILLS_DIR / skill_id / "SKILL.md"
    return path.read_text(encoding="utf-8").strip()
