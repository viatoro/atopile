"""Skills tool — let the agent inspect and load skill documents."""

from __future__ import annotations

from typing import Any, TypedDict

from atopile.agent.scope import Scope
from atopile.agent.tools.registry import tool


class SkillsListArgs(TypedDict, total=False):
    pass


@tool("List active and available skills.", label="Listed skills")
async def skills_list(args: SkillsListArgs, scope: Scope) -> dict[str, Any]:
    available = Scope.available_skills()
    active = set(scope.active_skills)
    return {
        "active": list(scope.active_skills),
        "available": [sid for sid in available if sid not in active],
    }


class SkillsLoadArgs(TypedDict):
    skill_id: str


@tool(
    "Load a skill to get domain-specific guidance for the current task.",
    label="Loaded skill",
)
async def skills_load(args: SkillsLoadArgs, scope: Scope) -> dict[str, Any]:
    skill_id = str(args.get("skill_id", "")).strip()
    if not skill_id:
        raise ValueError("skill_id is required")

    available = Scope.available_skills()
    if skill_id not in available:
        return {
            "loaded": False,
            "skill_id": skill_id,
            "error": f"Unknown skill '{skill_id}'",
            "available": available,
        }

    if skill_id not in scope.active_skills:
        scope.active_skills.append(skill_id)

    return {"loaded": True, "skill_id": skill_id, "active": list(scope.active_skills)}
