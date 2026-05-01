"""Web search tool powered by Exa."""

from __future__ import annotations

import asyncio
from typing import Any, TypedDict

import httpx

from atopile.agent import gateway
from atopile.agent.scope import Scope
from atopile.agent.tools.registry import tool


def _exa_search(*, query: str, num_results: int) -> dict[str, Any]:
    """Blocking Exa search call — run via asyncio.to_thread."""
    payload: dict[str, Any] = {
        "query": query,
        "numResults": num_results,
        "type": "auto",
        "contents": {"highlights": {"max_characters": 2000}},
    }

    headers = {
        **gateway.http_headers(),
        "accept": "application/json",
        "content-type": "application/json",
    }

    with httpx.Client(timeout=httpx.Timeout(30, connect=5), verify=True) as client:
        resp = client.post(gateway.EXA_SEARCH_URL, headers=headers, json=payload)
        resp.raise_for_status()

    results: list[dict[str, Any]] = []
    for i, r in enumerate(resp.json().get("results") or [], 1):
        if not isinstance(r, dict):
            continue
        entry: dict[str, Any] = {
            "rank": i,
            "title": r.get("title", ""),
            "url": r.get("url", ""),
        }
        if r.get("publishedDate"):
            entry["published_date"] = r["publishedDate"]
        if isinstance(r.get("highlights"), list):
            entry["highlights"] = [
                str(h)[:900] for h in r["highlights"] if isinstance(h, str) and h
            ][:6]
        results.append(entry)

    return {"query": query, "num_results": len(results), "results": results}


class WebSearchArgs(TypedDict, total=False):
    query: str
    num_results: int


@tool(
    "Search the web for datasheets, reference designs, and technical information.",
    label="Web search",
)
async def web_search(args: WebSearchArgs, scope: Scope) -> dict[str, Any]:
    query = str(args.get("query", "")).strip()
    if not query:
        raise ValueError("query is required")
    num_results = max(1, min(25, int(args.get("num_results", 8))))
    return await asyncio.to_thread(_exa_search, query=query, num_results=num_results)
