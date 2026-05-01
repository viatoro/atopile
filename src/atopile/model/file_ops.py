"""Filesystem mutation helpers for UI-facing file explorer actions."""

from __future__ import annotations

import shutil
from pathlib import Path

from atopile.pathutils import is_windows_path


def _project_root(project_root_str: str) -> Path:
    if not project_root_str:
        raise ValueError("Project root is required")
    project_root = Path(project_root_str).resolve()
    if not project_root.exists():
        raise FileNotFoundError(f"Project root not found: {project_root}")
    if not project_root.is_dir():
        raise NotADirectoryError(f"Project root is not a directory: {project_root}")
    return project_root


def _normalize_relative_path(relative_path: str) -> str:
    normalized = relative_path.replace("\\", "/")
    if not normalized:
        return ""
    if normalized.startswith("/") or is_windows_path(normalized):
        raise ValueError(f"Expected project-relative path, got: {relative_path}")

    parts: list[str] = []
    for part in normalized.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            raise ValueError(f"Path escapes project root: {relative_path}")
        parts.append(part)
    return "/".join(parts)


def _resolve_project_path(
    project_root_str: str, relative_path: str
) -> tuple[Path, Path]:
    project_root = _project_root(project_root_str)
    normalized_relative_path = _normalize_relative_path(relative_path)
    path = (
        project_root.joinpath(*normalized_relative_path.split("/"))
        if normalized_relative_path
        else project_root
    )
    resolved = path.resolve(strict=False)
    if resolved != project_root and not resolved.is_relative_to(project_root):
        raise ValueError(f"Path escapes project root: {relative_path}")
    return project_root, resolved


def _validate_name(name: str) -> str:
    trimmed = name.strip()
    if not trimmed:
        raise ValueError("Name cannot be empty.")
    if "/" in trimmed or "\\" in trimmed:
        raise ValueError("Name cannot contain path separators.")
    if trimmed in {".", ".."}:
        raise ValueError("Name cannot be '.' or '..'.")
    return trimmed


def _require_non_root(project_root: Path, path: Path) -> None:
    if path == project_root:
        raise ValueError("Cannot mutate project root")


def next_duplicate_path(source: Path) -> Path:
    suffix = "".join(source.suffixes) if source.is_file() else ""
    base_name = source.name[: -len(suffix)] if suffix else source.name
    for index in range(1, 10_000):
        candidate_name = (
            f"{base_name} copy{suffix}"
            if index == 1
            else f"{base_name} copy {index}{suffix}"
        )
        candidate = source.with_name(candidate_name)
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not find duplicate name for {source}")


def create_file(path_str: str) -> str:
    path = Path(path_str)
    if path.exists():
        raise FileExistsError(f"Path already exists: {path}")
    path.parent.mkdir(parents=False, exist_ok=True)
    path.touch(exist_ok=False)
    return str(path)


def create_folder(path_str: str) -> str:
    path = Path(path_str)
    path.mkdir(parents=False, exist_ok=False)
    return str(path)


def rename_path(path_str: str, new_path_str: str) -> str:
    path = Path(path_str)
    new_path = Path(new_path_str)
    if not path.exists():
        raise FileNotFoundError(f"Path not found: {path}")
    if new_path.exists():
        raise FileExistsError(f"Path already exists: {new_path}")
    path.rename(new_path)
    return str(new_path)


def delete_path(path_str: str) -> None:
    path = Path(path_str)
    if path.is_dir():
        shutil.rmtree(path)
        return
    path.unlink()


def duplicate_path(path_str: str) -> str:
    source = Path(path_str)
    if not source.exists():
        raise FileNotFoundError(f"Path not found: {source}")
    destination = next_duplicate_path(source)
    if source.is_dir():
        shutil.copytree(source, destination)
    else:
        shutil.copy2(source, destination)
    return str(destination)


def create_project_file(
    project_root_str: str, parent_relative_path: str, name: str
) -> str:
    _, parent = _resolve_project_path(project_root_str, parent_relative_path)
    return create_file(str(parent / _validate_name(name)))


def create_project_folder(
    project_root_str: str, parent_relative_path: str, name: str
) -> str:
    _, parent = _resolve_project_path(project_root_str, parent_relative_path)
    return create_folder(str(parent / _validate_name(name)))


def rename_project_path(
    project_root_str: str, relative_path: str, new_name: str
) -> str:
    project_root, path = _resolve_project_path(project_root_str, relative_path)
    _require_non_root(project_root, path)
    return rename_path(str(path), str(path.with_name(_validate_name(new_name))))


def delete_project_path(project_root_str: str, relative_path: str) -> str:
    project_root, path = _resolve_project_path(project_root_str, relative_path)
    _require_non_root(project_root, path)
    delete_path(str(path))
    return str(path)


def duplicate_project_path(project_root_str: str, relative_path: str) -> str:
    project_root, path = _resolve_project_path(project_root_str, relative_path)
    _require_non_root(project_root, path)
    return duplicate_path(str(path))
