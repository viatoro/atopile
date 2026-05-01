"""Hash-anchored line editing engine.

LINE:HASH references allow safe, concurrent-edit-aware file modifications.
The agent reads files with hashline-annotated output, then uses those
anchors to target edits precisely even if lines have shifted.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass
from typing import Literal

from atopile.agent.scope import Scope, ScopeError

# ── Constants ─────────────────────────────────────────────────────────

_HASH_HEX_CHARS = 4
_HASHLINE_CONTEXT_LINES = 2
_MAX_WRITE_FILE_BYTES = 600_000
_MAX_UI_DIFF_BYTES = 220_000

_ANCHOR_RE = re.compile(r"^(\d+)\s*:\s*([0-9A-Za-z]{1,16})$")
_ANCHOR_PREFIX_RE = re.compile(r"^(\d+)\s*:\s*([0-9A-Za-z]{1,16})")


# ── Data types ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LineAnchor:
    line: int
    hash: str


@dataclass(frozen=True)
class HashlineMismatch:
    line: int
    expected: str
    actual: str


@dataclass(frozen=True)
class SetLineOperation:
    index: int
    anchor: LineAnchor
    new_text: str


@dataclass(frozen=True)
class ReplaceLinesOperation:
    index: int
    start_anchor: LineAnchor
    end_anchor: LineAnchor
    new_text: str


@dataclass(frozen=True)
class InsertAfterOperation:
    index: int
    anchor: LineAnchor
    text: str


EditOperation = SetLineOperation | ReplaceLinesOperation | InsertAfterOperation


class HashlineMismatchError(ScopeError):
    """Raised when hashline anchors are stale for the current file content."""

    def __init__(self, mismatches: list[HashlineMismatch], file_lines: list[str]):
        self.mismatches = mismatches
        self.file_lines = file_lines
        self.remaps = {
            f"{m.line}:{m.expected}": f"{m.line}:{m.actual}" for m in mismatches
        }
        super().__init__(_format_mismatch_error(mismatches, file_lines))


# ── Public API ────────────────────────────────────────────────────────


def compute_line_hash(line_number: int, line_text: str) -> str:
    _ = line_number  # reserved for future hash variants
    normalized = re.sub(r"\s+", "", line_text.rstrip("\r"))
    digest = hashlib.blake2b(normalized.encode("utf-8"), digest_size=2).hexdigest()
    return digest[:_HASH_HEX_CHARS]


def format_hashline_content(lines: list[str], start_line: int = 1) -> str:
    """Return lines encoded as LINE:HASH|CONTENT."""
    if start_line < 1:
        raise ScopeError("start_line must be >= 1")
    return "\n".join(
        f"{line_no}:{compute_line_hash(line_no, line)}|{line}"
        for line_no, line in enumerate(lines, start=start_line)
    )


def apply_edits(scope: Scope, path: str, edits: list[dict]) -> dict:
    """Apply a batch of hash-anchored edits atomically to a project file."""
    file_path = scope.resolve(path)
    if not file_path.exists() or not file_path.is_file():
        raise ScopeError(f"File does not exist: {path}")

    raw_content = file_path.read_text(encoding="utf-8")
    line_ending = _detect_line_ending(raw_content)
    had_trailing_newline = _has_trailing_newline(raw_content)

    normalized_content = _normalize_newlines(raw_content)
    original_lines = normalized_content.splitlines()

    operations = _parse_operations(edits)
    _validate_non_overlapping(operations)

    mismatches = _collect_mismatches(operations, original_lines)
    if mismatches:
        raise HashlineMismatchError(mismatches, original_lines)

    updated_lines = list(original_lines)
    first_changed_line: int | None = None
    operations_applied = 0
    noop_details: list[str] = []

    for operation in sorted(operations, key=_sort_key, reverse=True):
        if isinstance(operation, SetLineOperation):
            start_idx = operation.anchor.line - 1
            old_lines = updated_lines[start_idx : start_idx + 1]
            new_lines = _split_replacement_lines(operation.new_text)
            if old_lines == new_lines:
                noop_details.append(
                    "edits[{}] set_line at {}:{}".format(
                        operation.index,
                        operation.anchor.line,
                        operation.anchor.hash,
                    )
                )
                continue
            updated_lines[start_idx : start_idx + 1] = new_lines
            operations_applied += 1
            first_changed_line = _min_changed(first_changed_line, operation.anchor.line)
            continue

        if isinstance(operation, ReplaceLinesOperation):
            start_idx = operation.start_anchor.line - 1
            end_idx = operation.end_anchor.line
            old_lines = updated_lines[start_idx:end_idx]
            new_lines = _split_replacement_lines(operation.new_text)
            if old_lines == new_lines:
                noop_details.append(
                    "edits[{}] replace_lines {}:{} -> {}:{}".format(
                        operation.index,
                        operation.start_anchor.line,
                        operation.start_anchor.hash,
                        operation.end_anchor.line,
                        operation.end_anchor.hash,
                    )
                )
                continue
            updated_lines[start_idx:end_idx] = new_lines
            operations_applied += 1
            first_changed_line = _min_changed(
                first_changed_line,
                operation.start_anchor.line,
            )
            continue

        # InsertAfterOperation
        insert_lines = _split_insert_lines(operation.text)
        insert_idx = operation.anchor.line
        updated_lines[insert_idx:insert_idx] = insert_lines
        operations_applied += 1
        first_changed_line = _min_changed(
            first_changed_line,
            operation.anchor.line + 1,
        )

    if operations_applied == 0:
        message = "No changes made. All edit operations were no-ops."
        if noop_details:
            details = "\n".join(f"- {d}" for d in noop_details)
            message = f"{message}\n{details}"
        raise ScopeError(message)

    if updated_lines == original_lines:
        raise ScopeError(
            "No changes made. Edit operations cancelled out to identical content."
        )

    normalized_output = "\n".join(updated_lines)
    output = _restore_line_endings(normalized_output, line_ending)
    output = _restore_trailing_newline(
        output,
        line_ending=line_ending,
        had_trailing_newline=had_trailing_newline,
    )

    output_bytes = len(output.encode("utf-8"))
    if output_bytes > _MAX_WRITE_FILE_BYTES:
        raise ScopeError("Refusing to write very large file content")

    relative_path = str(file_path.relative_to(scope.project_root))
    diff_summary = _build_diff_summary(
        relative_path=relative_path,
        before_lines=original_lines,
        after_lines=updated_lines,
    )
    ui_payload = _build_ui_diff_payload(
        relative_path=relative_path,
        before_text=raw_content,
        after_text=output,
    )

    file_path.write_text(output, encoding="utf-8")

    response: dict[str, object] = {
        "path": relative_path,
        "operations_requested": len(operations),
        "operations_applied": operations_applied,
        "first_changed_line": first_changed_line,
        "total_lines": len(updated_lines),
        "bytes": output_bytes,
        "diff": diff_summary,
    }
    if ui_payload is not None:
        response["_ui"] = {"edit_diff": ui_payload}
    return response


# ── Parsing ───────────────────────────────────────────────────────────


def _parse_line_anchor(raw_anchor: str) -> LineAnchor:
    if not isinstance(raw_anchor, str):
        raise ScopeError("Anchor must be a string")
    cleaned = raw_anchor.strip()
    if not cleaned:
        raise ScopeError("Anchor must not be empty")
    cleaned = cleaned.split("|", 1)[0].strip()
    cleaned = cleaned.split("  ", 1)[0].strip()
    match = _ANCHOR_RE.match(cleaned) or _ANCHOR_PREFIX_RE.match(cleaned)
    if not match:
        raise ScopeError(
            f"Invalid anchor '{raw_anchor}'. "
            "Expected LINE:HASH (for example '12:1a2b')."
        )
    line = int(match.group(1))
    if line < 1:
        raise ScopeError(f"Anchor line must be >= 1, got {line}")
    return LineAnchor(line=line, hash=match.group(2).lower())


def _parse_operations(edits: list[dict]) -> list[EditOperation]:
    if not isinstance(edits, list):
        raise ScopeError("edits must be an array")
    if not edits:
        raise ScopeError("edits must not be empty")

    operations: list[EditOperation] = []
    for idx, raw_edit in enumerate(edits):
        if not isinstance(raw_edit, dict):
            raise ScopeError(f"edits[{idx}] must be an object")
        if len(raw_edit) != 1:
            raise ScopeError(
                "edits[{}] must contain exactly one key: set_line, "
                "replace_lines, or insert_after".format(idx)
            )

        if "set_line" in raw_edit:
            p = _validate_payload(
                raw_edit["set_line"], ["anchor", "new_text"], idx, "set_line"
            )
            operations.append(
                SetLineOperation(
                    index=idx,
                    anchor=_parse_line_anchor(p["anchor"]),
                    new_text=p["new_text"],
                )
            )
        elif "replace_lines" in raw_edit:
            p = _validate_payload(
                raw_edit["replace_lines"],
                ["start_anchor", "end_anchor", "new_text"],
                idx,
                "replace_lines",
            )
            start = _parse_line_anchor(p["start_anchor"])
            end = _parse_line_anchor(p["end_anchor"])
            if start.line > end.line:
                raise ScopeError(
                    f"edits[{idx}] replace_lines.start_anchor must be <= end_anchor"
                )
            operations.append(
                ReplaceLinesOperation(
                    index=idx,
                    start_anchor=start,
                    end_anchor=end,
                    new_text=p["new_text"],
                )
            )
        elif "insert_after" in raw_edit:
            p = _validate_payload(
                raw_edit["insert_after"], ["anchor", "text"], idx, "insert_after"
            )
            operations.append(
                InsertAfterOperation(
                    index=idx,
                    anchor=_parse_line_anchor(p["anchor"]),
                    text=p["text"],
                )
            )
        else:
            raise ScopeError(
                f"edits[{idx}] must contain one of"
                " set_line, replace_lines, or insert_after"
            )

    return operations


def _validate_payload(
    payload: object,
    required_keys: list[str],
    edit_index: int,
    variant: str,
) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise ScopeError(f"edits[{edit_index}].{variant} must be an object")
    unknown = [k for k in payload if k not in required_keys]
    missing = [k for k in required_keys if k not in payload]
    if unknown or missing:
        raise ScopeError(
            f"edits[{edit_index}].{variant} must contain only keys {required_keys}"
        )
    validated: dict[str, str] = {}
    for key in required_keys:
        value = payload[key]
        if not isinstance(value, str):
            raise ScopeError(f"edits[{edit_index}].{variant}.{key} must be a string")
        validated[key] = value
    if variant == "insert_after" and validated["text"] == "":
        raise ScopeError(f"edits[{edit_index}].insert_after.text must be non-empty")
    return validated


# ── Validation ────────────────────────────────────────────────────────


def _collect_mismatches(
    operations: list[EditOperation],
    file_lines: list[str],
) -> list[HashlineMismatch]:
    mismatches: dict[tuple[int, str, str], HashlineMismatch] = {}
    for operation in operations:
        for anchor in _anchors_for(operation):
            if anchor.line < 1 or anchor.line > len(file_lines):
                raise ScopeError(
                    f"Anchor line {anchor.line} is out of range "
                    f"(file has {len(file_lines)} lines)"
                )
            actual = compute_line_hash(anchor.line, file_lines[anchor.line - 1])
            if actual == anchor.hash:
                continue
            key = (anchor.line, anchor.hash, actual)
            mismatches[key] = HashlineMismatch(
                line=anchor.line,
                expected=anchor.hash,
                actual=actual,
            )
    return sorted(mismatches.values(), key=lambda m: (m.line, m.expected))


def _anchors_for(operation: EditOperation) -> list[LineAnchor]:
    if isinstance(operation, ReplaceLinesOperation):
        return [operation.start_anchor, operation.end_anchor]
    return [operation.anchor]


def _sort_key(operation: EditOperation) -> tuple[int, int]:
    if isinstance(operation, ReplaceLinesOperation):
        return (operation.end_anchor.line, operation.index)
    return (operation.anchor.line, operation.index)


def _validate_non_overlapping(operations: list[EditOperation]) -> None:
    spans = [_span(op) for op in operations]
    for i, first in enumerate(spans):
        for second in spans[i + 1 :]:
            if _spans_overlap(first, second):
                raise ScopeError(
                    f"Overlapping edit spans between "
                    f"edits[{first[2]}] ({first[3]}) and "
                    f"edits[{second[2]}] ({second[3]})"
                )


def _span(operation: EditOperation) -> tuple[int, int, int, str, bool]:
    if isinstance(operation, SetLineOperation):
        return (
            operation.anchor.line,
            operation.anchor.line,
            operation.index,
            "set_line",
            False,
        )
    if isinstance(operation, ReplaceLinesOperation):
        return (
            operation.start_anchor.line,
            operation.end_anchor.line,
            operation.index,
            "replace_lines",
            False,
        )
    return (
        operation.anchor.line,
        operation.anchor.line,
        operation.index,
        "insert_after",
        True,
    )


def _spans_overlap(
    a: tuple[int, int, int, str, bool],
    b: tuple[int, int, int, str, bool],
) -> bool:
    a_start, a_end, _, _, a_insert = a
    b_start, b_end, _, _, b_insert = b
    if a_insert and b_insert:
        return a_start == b_start
    if a_insert:
        return b_start <= a_start <= b_end
    if b_insert:
        return a_start <= b_start <= a_end
    return not (a_end < b_start or b_end < a_start)


# ── Newline handling ──────────────────────────────────────────────────


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _detect_line_ending(text: str) -> Literal["\n", "\r\n", "\r"]:
    if "\r\n" in text:
        return "\r\n"
    if "\r" in text:
        return "\r"
    return "\n"


def _has_trailing_newline(text: str) -> bool:
    return text.endswith("\n") or text.endswith("\r")


def _restore_line_endings(text: str, line_ending: Literal["\n", "\r\n", "\r"]) -> str:
    if line_ending == "\n":
        return text
    return text.replace("\n", line_ending)


def _restore_trailing_newline(
    text: str,
    *,
    line_ending: Literal["\n", "\r\n", "\r"],
    had_trailing_newline: bool,
) -> str:
    if had_trailing_newline:
        if text:
            if not text.endswith(line_ending):
                return text + line_ending
            return text
        return line_ending
    while text.endswith(line_ending):
        text = text[: -len(line_ending)]
    return text


def _split_replacement_lines(text: str) -> list[str]:
    normalized = _normalize_newlines(text)
    if normalized == "":
        return []
    return normalized.split("\n")


def _split_insert_lines(text: str) -> list[str]:
    normalized = _normalize_newlines(text)
    if normalized == "":
        raise ScopeError("insert_after.text must be non-empty")
    return normalized.split("\n")


# ── Formatting ────────────────────────────────────────────────────────


def _min_changed(current: int | None, candidate: int) -> int:
    if current is None:
        return candidate
    return min(current, candidate)


def _format_mismatch_error(
    mismatches: list[HashlineMismatch],
    file_lines: list[str],
) -> str:
    mismatch_lines = {m.line for m in mismatches}
    display_lines: set[int] = set()
    for m in mismatches:
        start = max(1, m.line - _HASHLINE_CONTEXT_LINES)
        end = min(len(file_lines), m.line + _HASHLINE_CONTEXT_LINES)
        display_lines.update(range(start, end + 1))

    heading = (
        "{} line{} changed since last read. Use updated LINE:HASH refs shown "
        "below (>>> marks changed lines)."
    )
    output = [
        heading.format(len(mismatches), "s" if len(mismatches) != 1 else ""),
        "",
    ]

    ordered = sorted(display_lines)
    prev = -1
    for ln in ordered:
        if prev != -1 and ln > prev + 1:
            output.append("    ...")
        prev = ln
        content = file_lines[ln - 1]
        actual = compute_line_hash(ln, content)
        prefix = f"{ln}:{actual}|{content}"
        output.append(f">>> {prefix}" if ln in mismatch_lines else f"    {prefix}")

    output.append("")
    output.append("Quick fix - replace stale refs:")
    for m in mismatches:
        output.append(f"  {m.line}:{m.expected} -> {m.line}:{m.actual}")

    return "\n".join(output)


def _build_diff_summary(
    *,
    relative_path: str,
    before_lines: list[str],
    after_lines: list[str],
) -> dict[str, object]:
    diff_lines = list(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=f"a/{relative_path}",
            tofile=f"b/{relative_path}",
            lineterm="",
        )
    )
    added = sum(
        1 for ln in diff_lines if ln.startswith("+") and not ln.startswith("+++")
    )
    removed = sum(
        1 for ln in diff_lines if ln.startswith("-") and not ln.startswith("---")
    )
    hunks = sum(1 for ln in diff_lines if ln.startswith("@@"))
    preview_limit = 140
    return {
        "added_lines": added,
        "removed_lines": removed,
        "hunks": hunks,
        "preview": "\n".join(diff_lines[:preview_limit]),
        "truncated": len(diff_lines) > preview_limit,
    }


def _build_ui_diff_payload(
    *,
    relative_path: str,
    before_text: str,
    after_text: str,
) -> dict[str, object] | None:
    if (
        len(before_text.encode("utf-8")) > _MAX_UI_DIFF_BYTES
        or len(after_text.encode("utf-8")) > _MAX_UI_DIFF_BYTES
    ):
        return None
    return {
        "path": relative_path,
        "before_content": before_text,
        "after_content": after_text,
    }
