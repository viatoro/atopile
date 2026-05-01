"""Parts tools — search, install, and datasheet lookup."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, TypedDict

from atopile.agent.scope import Scope
from atopile.agent.tools.registry import tool
from atopile.model import parts as parts_domain


class PartsSearchArgs(TypedDict, total=False):
    query: str
    limit: int


@tool("Search LCSC/JLC parts by keyword.", label="Searched parts")
async def parts_search(args: PartsSearchArgs, scope: Scope) -> dict[str, Any]:
    parts, error = await asyncio.to_thread(
        parts_domain.PartCatalog.search,
        str(args.get("query", "")).strip(),
        limit=int(args.get("limit", 20)),
    )
    return {"parts": parts, "total": len(parts), "error": error}


class PartsInstallArgs(TypedDict, total=False):
    lcsc_id: str
    create_package: bool
    project_path: str | None


@tool(
    "Install an LCSC part into the project."
    " Use create_package=true to wrap it in a"
    " reusable local package.",
    label="Installed part",
)
async def parts_install(args: PartsInstallArgs, scope: Scope) -> dict[str, Any]:
    lcsc_id = str(args.get("lcsc_id", "")).strip().upper()
    create_package = bool(args.get("create_package", False))
    install_root = scope.resolve_project_path(args.get("project_path"))

    if create_package:
        result = await asyncio.to_thread(
            parts_domain.ProjectParts.install_as_package,
            lcsc_id,
            str(install_root),
        )
    else:
        result = await asyncio.to_thread(
            parts_domain.ProjectParts.install_raw,
            lcsc_id,
            str(install_root),
        )

    payload = {"success": True, "lcsc_id": lcsc_id, **result}
    if install_root != scope.project_root:
        payload["project_path"] = str(install_root.relative_to(scope.project_root))
    return payload


class PartsDatasheetArgs(TypedDict):
    lcsc_id: str


@tool(
    "Get the datasheet PDF for a part by LCSC ID."
    " Installs the part automatically if needed.",
    label="Read datasheet",
)
async def parts_datasheet(args: PartsDatasheetArgs, scope: Scope) -> dict[str, Any]:
    lcsc_id = str(args.get("lcsc_id", "")).strip().upper()
    if not lcsc_id:
        raise ValueError("lcsc_id is required")

    # Try to find an already-installed part's datasheet
    pdf_path, url = await asyncio.to_thread(
        _find_or_download_datasheet,
        scope.project_root,
        lcsc_id,
    )

    # If not installed, install it first then retry
    if pdf_path is None and url is None:
        await asyncio.to_thread(
            parts_domain.ProjectParts.install_raw,
            lcsc_id,
            str(scope.project_root),
        )
        pdf_path, url = await asyncio.to_thread(
            _find_or_download_datasheet,
            scope.project_root,
            lcsc_id,
        )

    if pdf_path is None:
        return {
            "found": False,
            "lcsc_id": lcsc_id,
            "datasheet_url": url,
            "message": "Could not resolve datasheet PDF.",
        }

    scope.make_searchable(pdf_path)

    return {
        "found": True,
        "lcsc_id": lcsc_id,
        "datasheet_path": str(pdf_path),
        "datasheet_url": url,
    }


def _find_or_download_datasheet(
    project_root: Path,
    lcsc_id: str,
) -> tuple[Path | None, str | None]:
    # TODO: remove this shim once the parts backend downloads datasheets
    # on install. Currently parts_install doesn't fetch the PDF, so we
    # parse the .ato file for the URL and download on demand here.
    """Find the datasheet PDF for an installed part, downloading if needed."""
    from atopile.config import config
    from faebryk.exporters.documentation.datasheets import (
        _download_datasheet,
        _extract_filename_from_url,
    )
    from faebryk.libs.codegen.atocodeparse import AtoCodeParse

    parts_dir = config.project.paths.parts
    if not parts_dir.is_dir():
        return None, None

    # Search parts directories for one matching the LCSC ID
    for part_dir in parts_dir.iterdir():
        if not part_dir.is_dir():
            continue
        ato_path = part_dir / (part_dir.name + ".ato")
        if not ato_path.is_file():
            continue

        # Check if this part's .ato file contains the LCSC ID
        try:
            ato_text = ato_path.read_text(encoding="utf-8")
        except Exception:
            continue
        if lcsc_id not in ato_text.upper():
            continue

        # Parse the datasheet URL from the .ato file
        try:
            ato = AtoCodeParse.ComponentFile(ato_path)
            _, trait_args = ato.parse_trait("has_datasheet")
            url = trait_args.get("datasheet")
        except AtoCodeParse.TraitNotFound, Exception:
            url = None

        if not url:
            return None, None

        # Check if PDF already exists
        filename = _extract_filename_from_url(url)
        pdf_path = part_dir / filename
        if pdf_path.exists():
            return pdf_path, url

        # Download it
        try:
            _download_datasheet(url, pdf_path)
            return pdf_path, url
        except Exception:
            return None, url

    return None, None
