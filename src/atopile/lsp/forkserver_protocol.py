from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import lsprotocol.types as lsp


@dataclass(slots=True)
class CompileSummary:
    diagnostics: list[lsp.Diagnostic]
    dependency_paths: list[Path]
    query_capabilities: list[str]
    phase_timings_ms: dict[str, float] = field(default_factory=dict)
    profile_counts: dict[str, int] = field(default_factory=dict)
    compiled_file_timings_ms: dict[str, float] = field(default_factory=dict)
    startup_timings_ms: dict[str, float] = field(default_factory=dict)
    error: str | None = None


@dataclass(slots=True)
class CompileRequest:
    uri: str
    source: str
    version: int
    generation: int
    open_overlays: dict[Path, str]
    broker_spawn_started_at: float | None = None
    broker_spawn_returned_at: float | None = None
    broker_send_started_at: float | None = None


@dataclass(slots=True)
class QueryRequest:
    method: Literal[
        "completion",
        "hover",
        "definition",
        "type_definition",
        "references",
        "prepare_rename",
        "rename",
        "code_action",
    ]
    uri: str
    generation: int
    payload: dict[str, Any]


@dataclass(slots=True)
class WatchInvalidation:
    paths: list[Path]


@dataclass(slots=True)
class CrashRequest:
    reason: str = "requested"


WorkerRequest = CompileRequest | QueryRequest | WatchInvalidation | CrashRequest


@dataclass(slots=True)
class WorkerResponse:
    ok: bool
    generation: int | None = None
    result: Any = None
    error: str | None = None
    dependencies: list[Path] = field(default_factory=list)
