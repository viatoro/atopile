"""Checklist tool — lets the agent track progress on multi-step tasks."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypedDict

from atopile.agent.scope import Scope
from atopile.agent.tools.registry import tool

# ── Data model ───────────────────────────────────────────────────────


class Status(Enum):
    pending = "pending"
    in_progress = "in_progress"
    done = "done"
    blocked = "blocked"


_VALID_TRANSITIONS: dict[Status, set[Status]] = {
    Status.pending: {Status.in_progress},
    Status.in_progress: {Status.done, Status.blocked},
    Status.done: set(),
    Status.blocked: {Status.in_progress},
}


@dataclass
class ChecklistItem:
    id: str
    description: str
    status: Status = Status.pending


@dataclass
class Checklist:
    items: list[ChecklistItem] = field(default_factory=list)

    def get(self, item_id: str) -> ChecklistItem | None:
        return next((i for i in self.items if i.id == item_id), None)

    def summary(self) -> list[dict[str, str]]:
        return [
            {
                "id": item.id,
                "description": item.description,
                "status": item.status.value,
            }
            for item in self.items
        ]

    def snapshot(self) -> list[dict[str, str]]:
        """Serialize to a JSON-safe list for persistence."""
        return self.summary()

    @classmethod
    def restore(cls, data: list[dict[str, str]]) -> Checklist:
        """Restore from a serialized snapshot."""
        items = []
        for entry in data:
            try:
                status = Status(entry.get("status", "pending"))
            except ValueError:
                status = Status.pending
            items.append(
                ChecklistItem(
                    id=str(entry.get("id", "")),
                    description=str(entry.get("description", "")),
                    status=status,
                )
            )
        return cls(items=items)


# ── Helpers ──────────────────────────────────────────────────────────


def _get_checklist(scope: Scope) -> Checklist:
    """Get the checklist from scope, creating one if needed."""
    if scope.checklist is None:
        scope.checklist = Checklist()
    return scope.checklist


# ── Tools ────────────────────────────────────────────────────────────


class ChecklistSetArgs(TypedDict):
    items: list[dict[str, str]]


@tool(
    "Create or replace the task checklist. Each item needs an 'id' and 'description'.",
    label="Set checklist",
)
async def checklist_set(args: ChecklistSetArgs, scope: Scope) -> dict[str, Any]:
    raw_items = args.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("items must be a non-empty list")

    items = []
    for item in raw_items:
        if not isinstance(item, dict):
            raise ValueError("Each item must be an object with 'id' and 'description'")
        item_id = item.get("id")
        if not item_id or not isinstance(item_id, str):
            raise ValueError("Each item must have a string 'id'")
        items.append(
            ChecklistItem(
                id=item_id,
                description=str(item.get("description", "")),
            )
        )

    scope.checklist = Checklist(items=items)
    return {"items": scope.checklist.summary()}


class ChecklistUpdateArgs(TypedDict):
    item_id: str
    status: str


@tool(
    "Update a checklist item's status."
    " Valid: pending → in_progress → done|blocked,"
    " blocked → in_progress.",
    label="Updated checklist",
)
async def checklist_update(args: ChecklistUpdateArgs, scope: Scope) -> dict[str, Any]:
    item_id = str(args.get("item_id", ""))
    raw_status = str(args.get("status", ""))

    try:
        new_status = Status(raw_status)
    except ValueError:
        valid = ", ".join(s.value for s in Status)
        raise ValueError(f"Invalid status '{raw_status}'. Must be one of: {valid}")

    checklist = _get_checklist(scope)
    item = checklist.get(item_id)
    if item is None:
        raise ValueError(f"No checklist item with id '{item_id}'")

    allowed = _VALID_TRANSITIONS[item.status]
    if new_status not in allowed:
        allowed_str = ", ".join(s.value for s in allowed) or "none (terminal)"
        raise ValueError(
            f"Cannot transition from '{item.status.value}' to '{new_status.value}'. "
            f"Allowed: {allowed_str}"
        )

    item.status = new_status
    return {
        "item_id": item.id,
        "status": item.status.value,
        "items": checklist.summary(),
    }


class ChecklistGetArgs(TypedDict, total=False):
    pass


@tool("Get the current checklist.", label="Read checklist")
async def checklist_get(args: ChecklistGetArgs, scope: Scope) -> dict[str, Any]:
    checklist = _get_checklist(scope)
    return {"items": checklist.summary()}
