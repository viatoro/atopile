"""Cross-platform path identity helpers."""

from __future__ import annotations

import re

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:([/\\]|$)")
_WINDOWS_DRIVE_ROOT_RE = re.compile(r"^[A-Za-z]:/$")
_UNC_RE = re.compile(r"^//[^/]+/[^/]+(?:/|$)")


def _normalize_separators(path: str) -> str:
    return path.replace("\\", "/")


def _collapse_separators(path: str) -> str:
    if _UNC_RE.match(path):
        return "//" + re.sub(r"/+", "/", path[2:])
    return re.sub(r"/+", "/", path)


def is_windows_path(path: str) -> bool:
    """Return whether a path uses Windows drive-letter or UNC semantics."""
    normalized = _normalize_separators(path)
    return bool(_WINDOWS_DRIVE_RE.match(normalized) or _UNC_RE.match(normalized))


def normalize_path(path: str) -> str:
    """Normalize separators and trailing slashes without changing path identity."""
    if not path:
        return ""

    normalized = _collapse_separators(_normalize_separators(path))
    if normalized == "/" or _WINDOWS_DRIVE_ROOT_RE.match(normalized):
        return normalized

    stripped = normalized.rstrip("/")
    return stripped or "/"


def path_key(path: str) -> str:
    """Return the canonical key used for path identity comparisons."""
    normalized = normalize_path(path)
    return normalized.lower() if is_windows_path(normalized) else normalized


def same_path(left: str | None, right: str | None) -> bool:
    """Return whether two optional paths identify the same location."""
    if not left or not right:
        return left == right
    return path_key(left) == path_key(right)
