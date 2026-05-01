# ruff: noqa: F401
"""Tools subpackage — builds and exports the tool registry."""

from atopile.agent.tools.registry import ToolDef, ToolRegistry, tool


def create_registry() -> ToolRegistry:
    """Build a fully-populated tool registry from all tool modules."""
    from atopile.agent.tools import (
        build,
        checklist,
        files,
        packages,
        parts,
        reporting,
        skills,
        stdlib,
        websearch,
    )

    registry = ToolRegistry()
    for module in [
        files,
        build,
        parts,
        packages,
        checklist,
        skills,
        reporting,
        stdlib,
        websearch,
    ]:
        registry.register_module(module)
    return registry
