"""Stdlib browsing tools — let the agent inspect the standard library."""

from __future__ import annotations

import asyncio
from typing import Any, TypedDict

from atopile.agent.scope import Scope
from atopile.agent.tools.registry import tool
from atopile.model import stdlib as stdlib_domain


class StdlibListArgs(TypedDict, total=False):
    type_filter: str | None
    search: str | None
    limit: int


@tool(
    "Search the atopile standard library for modules, interfaces, and components.",
    label="Searched stdlib",
)
async def stdlib_list(args: StdlibListArgs, scope: Scope) -> dict[str, Any]:
    type_filter = args.get("type_filter")
    search = args.get("search")
    limit = int(args.get("limit", 120))

    response = await asyncio.to_thread(
        stdlib_domain.handle_get_stdlib,
        str(type_filter) if isinstance(type_filter, str) and type_filter else None,
        str(search) if isinstance(search, str) and search else None,
    )
    items = [item.model_dump() for item in response.items[:limit]]
    return {"items": items, "total": response.total, "returned": len(items)}


class StdlibGetItemArgs(TypedDict):
    item_id: str


@tool(
    "Get details for a stdlib item. Use an id from stdlib_list.",
    label="Inspected stdlib item",
)
async def stdlib_get_item(args: StdlibGetItemArgs, scope: Scope) -> dict[str, Any]:
    item_id = str(args.get("item_id", "")).strip()
    if not item_id:
        raise ValueError("item_id is required")
    item = await asyncio.to_thread(
        lambda: next(
            (e for e in stdlib_domain.get_standard_library() if e.id == item_id),
            None,
        )
    )
    if item is None:
        return {"found": False, "item_id": item_id}
    return {"found": True, "item_id": item_id, "item": item.model_dump()}
