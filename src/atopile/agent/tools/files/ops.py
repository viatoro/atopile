"""File-operation tools for the agent."""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, TypedDict

from atopile.agent.scope import Scope, ScopeError
from atopile.agent.tools.files.hashline import (
    _MAX_WRITE_FILE_BYTES,
    _normalize_newlines,
    apply_edits,
    format_hashline_content,
)
from atopile.agent.tools.registry import tool


@dataclass(frozen=True)
class MatchLine:
    path: str
    line: int
    text: str


# ── Constants ─────────────────────────────────────────────────────────

_ALLOWED_EXTENSIONS = {
    ".ato",
    ".py",
    ".pyi",
    ".md",
    ".txt",
    ".yaml",
    ".yml",
    ".json",
    ".toml",
    ".ini",
    ".cfg",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".css",
    ".sh",
}

_EXCLUDED_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    ".venv",
    "venv",
    "__pycache__",
    ".ato",
    "build",
    "node_modules",
    "dist",
    "coverage",
}

_MAX_CONTEXT_FILE_BYTES = 180_000

_ALLOWED_CREATE_FILE_EXTENSIONS = (
    ".ato",
    ".md",
    ".py",
    ".yaml",
    ".yml",
)
_FABLL_PY_CREATE_ROOTS = (Path("src/faebryk/library"),)


# ── File helpers ──────────────────────────────────────────────────────


def _is_context_file(path: Path, project_root: Path) -> bool:
    if not path.is_file():
        return False
    if path.suffix.lower() not in _ALLOWED_EXTENSIONS:
        return False
    if path.stat().st_size > _MAX_CONTEXT_FILE_BYTES:
        return False
    for part in path.relative_to(project_root).parts:
        if part in _EXCLUDED_DIR_NAMES:
            return False
    return True


def _list_context_files(project_root: Path, limit: int = 300) -> list[str]:
    results: list[str] = []
    for file_path in sorted(project_root.rglob("*")):
        if len(results) >= limit:
            break
        if not _is_context_file(file_path, project_root):
            continue
        results.append(str(file_path.relative_to(project_root)))
    return results


def _read_file_chunk(
    scope: Scope,
    path: str,
    *,
    start_line: int = 1,
    max_lines: int = 200,
) -> dict:
    if start_line < 1:
        raise ScopeError("start_line must be >= 1")
    if max_lines < 1:
        raise ScopeError("max_lines must be >= 1")

    file_path = scope.resolve(path)
    if not file_path.exists() or not file_path.is_file():
        raise ScopeError(f"File does not exist: {path}")

    data = file_path.read_text(encoding="utf-8")
    lines = _normalize_newlines(data).splitlines()
    start_idx = start_line - 1
    end_idx = min(len(lines), start_idx + max_lines)

    chunk = format_hashline_content(lines[start_idx:end_idx], start_line=start_line)
    return {
        "path": str(file_path.relative_to(scope.project_root)),
        "start_line": start_line,
        "end_line": end_idx,
        "total_lines": len(lines),
        "content": chunk,
    }


def _search_in_files(
    scope: Scope,
    query: str,
    *,
    limit: int = 50,
) -> list[MatchLine]:
    needle = query.strip().lower()
    if not needle:
        return []

    project_root = scope.project_root
    matches: list[MatchLine] = []
    for path in project_root.rglob("*"):
        if len(matches) >= limit:
            break
        if not _is_context_file(path, project_root):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for idx, line in enumerate(text.splitlines(), start=1):
            if needle not in line.lower():
                continue
            matches.append(
                MatchLine(
                    path=str(path.relative_to(project_root)),
                    line=idx,
                    text=line.strip()[:260],
                )
            )
            if len(matches) >= limit:
                break

    return matches


def _create_path(
    scope: Scope,
    path: str,
    *,
    kind: Literal["file", "directory"] = "file",
    content: str = "",
    overwrite: bool = False,
    parents: bool = True,
) -> dict:
    raw_path = path.strip()
    if not raw_path:
        raise ScopeError("path must not be empty")

    target_path = scope.resolve(raw_path)
    project_root = scope.project_root
    if target_path == project_root:
        raise ScopeError("Refusing to create project root path")

    if kind not in {"file", "directory"}:
        raise ScopeError("kind must be 'file' or 'directory'")

    if kind == "directory":
        if content:
            raise ScopeError("content is only supported when kind='file'")
        if target_path.exists() and not target_path.is_dir():
            raise ScopeError(f"Path already exists and is not a directory: {path}")
        if not parents and not target_path.parent.exists():
            raise ScopeError("Parent directory does not exist; set parents=true")
        existed = target_path.exists()
        target_path.mkdir(parents=parents, exist_ok=True)
        return {
            "path": str(target_path.relative_to(project_root)),
            "kind": "directory",
            "created": not existed,
        }

    extension = target_path.suffix.lower()
    if extension not in _ALLOWED_CREATE_FILE_EXTENSIONS:
        allowed = ", ".join(_ALLOWED_CREATE_FILE_EXTENSIONS)
        raise ScopeError(f"Only {allowed} files can be created with this tool.")

    relative_target = target_path.relative_to(project_root)
    if extension == ".py" and not any(
        relative_target == root or relative_target.is_relative_to(root)
        for root in _FABLL_PY_CREATE_ROOTS
    ):
        allowed_roots = ", ".join(str(r) for r in _FABLL_PY_CREATE_ROOTS)
        raise ScopeError(
            f"Python files may only be created for fabll modules under: {allowed_roots}"
        )

    if target_path.exists():
        if target_path.is_dir():
            raise ScopeError(f"Path already exists and is a directory: {path}")
        if not overwrite:
            raise ScopeError(f"File already exists: {path}")
        overwrote = True
    else:
        overwrote = False

    if not parents and not target_path.parent.exists():
        raise ScopeError("Parent directory does not exist; set parents=true")

    content_bytes = len(content.encode("utf-8"))
    if content_bytes > _MAX_WRITE_FILE_BYTES:
        raise ScopeError("Refusing to write very large file content")

    target_path.parent.mkdir(parents=parents, exist_ok=True)
    target_path.write_text(content, encoding="utf-8")

    return {
        "path": str(relative_target),
        "kind": "file",
        "extension": extension,
        "bytes": content_bytes,
        "created": not overwrote,
        "overwrote": overwrote,
    }


def _rename_path(
    scope: Scope,
    old_path: str,
    new_path: str,
    *,
    overwrite: bool = False,
) -> dict:
    source_path = scope.resolve(old_path)
    destination_path = scope.resolve(new_path)
    project_root = scope.project_root

    if source_path == project_root:
        raise ScopeError("Refusing to rename project root")
    if destination_path == project_root:
        raise ScopeError("Refusing to rename to project root")
    if source_path == destination_path:
        raise ScopeError("old_path and new_path must be different")
    if not source_path.exists():
        raise ScopeError(f"Path does not exist: {old_path}")

    source_kind = (
        "symlink"
        if source_path.is_symlink()
        else "directory"
        if source_path.is_dir()
        else "file"
    )

    destination_exists = destination_path.exists()
    if destination_exists:
        if not overwrite:
            raise ScopeError(f"Destination already exists: {new_path}")
        dest_kind = (
            "symlink"
            if destination_path.is_symlink()
            else "directory"
            if destination_path.is_dir()
            else "file"
        )
        if source_kind != dest_kind:
            raise ScopeError(
                f"Cannot overwrite different path kind ({source_kind} -> {dest_kind})"
            )
        if dest_kind == "directory":
            shutil.rmtree(destination_path)
        else:
            destination_path.unlink()

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.rename(destination_path)

    return {
        "old_path": str(source_path.relative_to(project_root)),
        "new_path": str(destination_path.relative_to(project_root)),
        "kind": source_kind,
        "overwrote": bool(destination_exists),
    }


def _delete_path(
    scope: Scope,
    path: str,
    *,
    recursive: bool = True,
) -> dict:
    target_path = scope.resolve(path)
    project_root = scope.project_root

    if target_path == project_root:
        raise ScopeError("Refusing to delete project root")
    if not target_path.exists():
        raise ScopeError(f"Path does not exist: {path}")

    if target_path.is_symlink():
        kind = "symlink"
        target_path.unlink()
    elif target_path.is_dir():
        kind = "directory"
        if recursive:
            shutil.rmtree(target_path)
        else:
            try:
                target_path.rmdir()
            except OSError as exc:
                raise ScopeError(
                    "Directory is not empty; set recursive=true to delete"
                ) from exc
    else:
        kind = "file"
        target_path.unlink()

    return {
        "path": str(target_path.relative_to(project_root)),
        "kind": kind,
        "deleted": True,
    }


# ── Tool definitions ──────────────────────────────────────────────────


class ProjectListFilesArgs(TypedDict, total=False):
    limit: int


@tool("List source and config files in the selected project.", label="Listed files")
async def project_list_files(
    args: ProjectListFilesArgs, scope: Scope
) -> dict[str, Any]:
    limit = int(args.get("limit", 300))
    files = await asyncio.to_thread(_list_context_files, scope.project_root, limit)
    return {"files": files, "total": len(files)}


class ProjectReadFileArgs(TypedDict, total=False):
    path: str
    start_line: int
    max_lines: int


@tool(
    "Read a file chunk from the selected project,"
    " including package files under .ato/modules.",
    label="Read",
)
async def project_read_file(args: ProjectReadFileArgs, scope: Scope) -> dict[str, Any]:
    return await asyncio.to_thread(
        _read_file_chunk,
        scope,
        str(args.get("path", "")),
        start_line=int(args.get("start_line", 1)),
        max_lines=int(args.get("max_lines", 220)),
    )


class ProjectSearchArgs(TypedDict, total=False):
    query: str
    limit: int


@tool("Search source and config files by substring.", label="Searched")
async def project_search(args: ProjectSearchArgs, scope: Scope) -> dict[str, Any]:
    matches = await asyncio.to_thread(
        _search_in_files,
        scope,
        str(args.get("query", "")),
        limit=int(args.get("limit", 60)),
    )
    return {"matches": [asdict(m) for m in matches], "total": len(matches)}


class ProjectEditFileArgs(TypedDict, total=False):
    path: str
    edits: list[dict[str, Any]]


@tool(
    "Apply edits to a project file using"
    " LINE:HASH anchors from project_read_file output.",
    label="Edited",
)
async def project_edit_file(args: ProjectEditFileArgs, scope: Scope) -> dict[str, Any]:
    edits = args.get("edits")
    if not isinstance(edits, list):
        raise ValueError("edits must be a list")
    return await asyncio.to_thread(apply_edits, scope, str(args.get("path", "")), edits)


class ProjectCreatePathArgs(TypedDict, total=False):
    path: str
    kind: str
    content: str
    overwrite: bool
    parents: bool


@tool("Create an in-scope directory or allowed file type.", label="Created")
async def project_create_path(
    args: ProjectCreatePathArgs, scope: Scope
) -> dict[str, Any]:
    content = args.get("content", "")
    if content is None:
        content = ""
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    kind = str(args.get("kind", "file")).strip().lower()
    return await asyncio.to_thread(
        _create_path,
        scope,
        str(args.get("path", "")),
        kind=kind,
        content=content,
        overwrite=bool(args.get("overwrite", False)),
        parents=bool(args.get("parents", True)),
    )


class ProjectMovePathArgs(TypedDict, total=False):
    old_path: str
    new_path: str
    overwrite: bool


@tool(
    "Move or rename a file or directory within the selected project.",
    label="Moved",
)
async def project_move_path(args: ProjectMovePathArgs, scope: Scope) -> dict[str, Any]:
    return await asyncio.to_thread(
        _rename_path,
        scope,
        str(args.get("old_path", "")),
        str(args.get("new_path", "")),
        overwrite=bool(args.get("overwrite", False)),
    )


class ProjectDeletePathArgs(TypedDict, total=False):
    path: str
    recursive: bool


@tool("Delete a file or directory within the selected project.", label="Deleted")
async def project_delete_path(
    args: ProjectDeletePathArgs, scope: Scope
) -> dict[str, Any]:
    return await asyncio.to_thread(
        _delete_path,
        scope,
        str(args.get("path", "")),
        recursive=bool(args.get("recursive", True)),
    )
