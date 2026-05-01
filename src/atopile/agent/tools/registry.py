"""Tool registry — @tool decorator, schema generation, and dispatch."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import (
    Any,
    Awaitable,
    Callable,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from atopile.agent.scope import Scope
from atopile.logging import get_logger

log = get_logger(__name__)

ToolHandler = Callable[[dict[str, Any], Scope], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ToolDef:
    """A tool's name, schema, handler, and UI label."""

    name: str
    label: str
    schema: dict[str, Any]
    handler: ToolHandler


# ── TypedDict → JSON schema conversion ───────────────────────────────


def _python_type_to_json(annotation: Any) -> dict[str, Any]:
    """Convert a Python type annotation to a JSON Schema fragment."""
    origin = get_origin(annotation)
    args = get_args(annotation)

    # Union types: X | None → {"type": ["<base>", "null"]}
    if origin is Union:
        non_none = [a for a in args if a is not type(None)]
        has_none = type(None) in args
        if len(non_none) == 1:
            base = _python_type_to_json(non_none[0])
            if has_none:
                base_type = base.get("type")
                if isinstance(base_type, str):
                    base["type"] = [base_type, "null"]
            return base
        # Multi-type union — fall back to untyped
        return {}

    # list[X] → {"type": "array", "items": ...}
    if origin is list:
        item_schema = _python_type_to_json(args[0]) if args else {}
        result: dict[str, Any] = {"type": "array"}
        if item_schema:
            result["items"] = item_schema
        return result

    # dict[str, X] → {"type": "object"}
    if origin is dict:
        return {"type": "object"}

    # Primitives
    if annotation is str:
        return {"type": "string"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}

    return {}


def _typed_dict_to_json_schema(td: type) -> dict[str, Any]:
    """Convert a TypedDict class to an OpenAI-compatible JSON Schema."""
    hints = get_type_hints(td)
    required_keys = set(getattr(td, "__required_keys__", set()))

    properties: dict[str, Any] = {}
    for key, annotation in hints.items():
        prop = _python_type_to_json(annotation)
        if prop:
            properties[key] = prop

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required_keys:
        schema["required"] = sorted(required_keys)
    return schema


def _build_tool_schema(
    name: str, description: str, args_type: type | None
) -> dict[str, Any]:
    """Build an OpenAI function-tool schema from name + description + TypedDict."""
    schema: dict[str, Any] = {
        "type": "function",
        "name": name,
        "description": description,
    }
    if args_type is not None:
        schema["parameters"] = _typed_dict_to_json_schema(args_type)
    else:
        schema["parameters"] = {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }
    return schema


# ── @tool decorator ──────────────────────────────────────────────────


def tool(description: str, *, label: str | None = None) -> Callable:
    """Decorator that marks an async function as a tool.

    Usage::

        class MyArgs(TypedDict):
            query: str
            limit: int

        @tool("Search for things.", label="Searched")
        async def my_search(args: MyArgs, scope: Scope) -> dict[str, Any]:
            ...

    The function name becomes the tool name. The TypedDict on the
    ``args`` parameter becomes the JSON schema. ``description`` is the
    one-liner shown to the model. ``label`` is the short human-readable
    verb shown in the UI when the tool runs; if omitted, the tool name
    is used as-is.
    """

    def decorator(fn: ToolHandler) -> ToolHandler:
        # Extract the TypedDict from the args parameter
        hints = get_type_hints(fn)
        args_type = hints.get("args")

        # Check if it's a TypedDict (has __annotations__ and __required_keys__)
        is_typed_dict = (
            args_type is not None
            and isinstance(args_type, type)
            and hasattr(args_type, "__annotations__")
            and hasattr(args_type, "__required_keys__")
        )

        fn._tool_def = ToolDef(  # type: ignore[attr-defined]
            name=fn.__name__,
            label=label if label is not None else fn.__name__,
            schema=_build_tool_schema(
                fn.__name__,
                description,
                args_type if is_typed_dict else None,
            ),
            handler=fn,
        )
        return fn

    return decorator


# ── Registry ─────────────────────────────────────────────────────────


def collect_tools_from_module(module: Any) -> list[ToolDef]:
    """Scan a module for @tool-decorated functions and return their ToolDefs."""
    tools: list[ToolDef] = []
    for _name, obj in inspect.getmembers(module, inspect.isfunction):
        tool_def = getattr(obj, "_tool_def", None)
        if isinstance(tool_def, ToolDef):
            tools.append(tool_def)
    return tools


class ToolRegistry:
    """Collects tools and dispatches execution by name."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}

    def register(self, tool_def: ToolDef) -> None:
        if tool_def.name in self._tools:
            log.warning("Tool %r registered twice — overwriting", tool_def.name)
        self._tools[tool_def.name] = tool_def

    def register_module(self, module: Any) -> None:
        """Register all @tool-decorated functions from a module."""
        for tool_def in collect_tools_from_module(module):
            self.register(tool_def)

    def definitions(self) -> list[dict[str, Any]]:
        """Return OpenAI-format tool schemas for all registered tools."""
        return [t.schema for t in self._tools.values()]

    def label(self, name: str) -> str:
        """Return the UI label for a tool, or the name itself if unregistered."""
        tool_def = self._tools.get(name)
        return tool_def.label if tool_def is not None else name

    async def execute(
        self,
        name: str,
        args: dict[str, Any],
        scope: Scope,
    ) -> dict[str, Any]:
        tool_def = self._tools.get(name)
        if tool_def is None:
            return {"error": f"Unknown tool: {name}"}
        return await tool_def.handler(args, scope)
