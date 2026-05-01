"""Package tools — search, install, and create local packages."""

from __future__ import annotations

import asyncio
from typing import Any, NotRequired, TypedDict

from atopile.agent.scope import Scope
from atopile.agent.tools.registry import tool
from atopile.model import packages as packages_domain
from atopile.model import projects as projects_domain


class PackagesSearchArgs(TypedDict):
    query: str


@tool(
    "Search the atopile package registry for reusable modules and libraries.",
    label="Searched packages",
)
async def packages_search(args: PackagesSearchArgs, scope: Scope) -> dict[str, Any]:
    result = await asyncio.to_thread(
        packages_domain.handle_search_registry,
        str(args.get("query", "")),
        scope.project_root,
    )
    return result.model_dump(by_alias=True)


class PackagesInstallArgs(TypedDict):
    identifier: str
    version: NotRequired[str | None]


@tool(
    "Install a package from the atopile registry into the project.",
    label="Installed package",
)
async def packages_install(args: PackagesInstallArgs, scope: Scope) -> dict[str, Any]:
    identifier = str(args.get("identifier", ""))
    version = args.get("version")
    clean_version = str(version) if isinstance(version, str) and version else None
    await asyncio.to_thread(
        packages_domain.install_package_to_project,
        scope.project_root,
        identifier,
        clean_version,
    )
    return {"success": True, "identifier": identifier, "version": clean_version}


class PackagesCreateLocalArgs(TypedDict):
    name: str
    entry_module: str
    description: NotRequired[str | None]


@tool(
    "Create a local sub-package under packages/ and add it as a dependency.",
    label="Created local package",
)
async def packages_create_local(
    args: PackagesCreateLocalArgs, scope: Scope
) -> dict[str, Any]:
    result = await asyncio.to_thread(
        projects_domain.create_local_package,
        scope.project_root,
        str(args.get("name", "")),
        str(args.get("entry_module", "")),
        str(args.get("description"))
        if isinstance(args.get("description"), str)
        else None,
    )
    return {"success": True, **result}


class WorkspaceListTargetsArgs(TypedDict):
    pass


@tool(
    "List all build targets across the project and its local sub-packages.",
    label="Listed targets",
)
async def workspace_list_targets(
    args: WorkspaceListTargetsArgs, scope: Scope
) -> dict[str, Any]:
    response = await asyncio.to_thread(
        projects_domain.handle_get_projects,
        [scope.project_root],
    )
    projects = [p.model_dump(by_alias=True) for p in response.projects]
    return {
        "projects": projects,
        "total_projects": len(projects),
        "total_targets": sum(len(p["targets"]) for p in projects),
    }
