from __future__ import annotations

import sys
import time
import traceback
from dataclasses import dataclass
from multiprocessing.connection import Connection
from pathlib import Path
from types import SimpleNamespace

from atopile.lsp.forkserver_protocol import (
    CompileRequest,
    CompileSummary,
    CrashRequest,
    QueryRequest,
    WorkerRequest,
    WorkerResponse,
)


@dataclass
class _FakeDocument:
    uri: str
    path: str
    source: str


class _FakeWorkspace:
    def __init__(self) -> None:
        self.text_documents: dict[str, _FakeDocument] = {}

    def get_text_document(self, uri: str) -> _FakeDocument:
        return self.text_documents[uri]


class _FakeServer:
    def __init__(self) -> None:
        self.workspace = _FakeWorkspace()

    def text_document_publish_diagnostics(self, _params) -> None:
        return None


def _load_runtime():
    from atopile.lsp import lsp_server

    lsp_server.LSP_SERVER = _FakeServer()
    return lsp_server


def _get_warm_base():
    from atopile.lsp import forkbase_preload

    return forkbase_preload.WARM_COMPILER_BASE


def _prewarm_units() -> float:
    import faebryk.core.faebrykpy as fbrk
    import faebryk.core.graph as graph
    from atopile.compiler.build import StdlibRegistry
    from faebryk.library.Units import register_all_units

    started_at = time.perf_counter()
    g = graph.GraphView.create()
    tg = fbrk.TypeGraph.create(g=g)
    StdlibRegistry(tg)
    register_all_units(g, tg)
    return round((time.perf_counter() - started_at) * 1000, 1)


def _get_current_rss_mb() -> float:
    if sys.platform == "win32":
        return 0.0

    try:
        import resource
    except ModuleNotFoundError:
        return 0.0

    status_path = Path("/proc/self/status")
    try:
        for line in status_path.read_text().splitlines():
            if line.startswith("VmRSS:"):
                rss_kb = int(line.split()[1])
                return round(rss_kb / 1024, 1)
    except Exception:
        pass

    rss_raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return round(rss_raw / (1024 * 1024), 1)
    return round(rss_raw / 1024, 1)


class WorkerRuntime:
    def __init__(self, *, process_started_at: float) -> None:
        self.process_started_at = process_started_at
        runtime_started_at = time.perf_counter()
        self.lsp_server = _load_runtime()
        self.units_prewarm_ms = _prewarm_units()
        self.runtime_init_ms = round(
            (time.perf_counter() - runtime_started_at) * 1000, 1
        )
        self.process_to_runtime_ready_ms = round(
            (time.perf_counter() - self.process_started_at) * 1000, 1
        )
        self.compiled_generation: dict[str, int] = {}
        self.first_request_received_ms: float | None = None
        self.first_request_wait_after_ready_ms: float | None = None

    def compile(self, request: CompileRequest) -> WorkerResponse:
        request_started_at = time.perf_counter()
        rss_before_compile_mb = _get_current_rss_mb()
        path = Path(self.lsp_server.get_file_path(request.uri)).resolve()
        fake_doc = _FakeDocument(uri=request.uri, path=str(path), source=request.source)
        self.lsp_server.LSP_SERVER.workspace.text_documents[request.uri] = fake_doc

        try:
            warm_base_started_at = time.perf_counter()
            warm_base = _get_warm_base()
            warm_base_get_ms = round(
                (time.perf_counter() - warm_base_started_at) * 1000, 1
            )
            state = self.lsp_server.build_document(
                request.uri,
                request.source,
                source_path=path,
                source_overrides=request.open_overlays,
                warm_base=warm_base,
                reuse_warm_base_graph=True,
            )
            self.compiled_generation[request.uri] = request.generation
            startup_timings_ms = {
                "worker_runtime_init": self.runtime_init_ms,
                "worker_units_prewarm": self.units_prewarm_ms,
                "worker_process_to_runtime_ready": self.process_to_runtime_ready_ms,
                "warm_base_get": warm_base_get_ms,
                "worker_process_to_compile_enter": round(
                    (request_started_at - self.process_started_at) * 1000, 1
                ),
                "worker_compile_request_total": round(
                    (time.perf_counter() - request_started_at) * 1000, 1
                ),
                "worker_rss_before_compile_mb": rss_before_compile_mb,
            }
            if request.broker_spawn_returned_at is not None:
                startup_timings_ms["worker_main_enter_after_broker_spawn_returned"] = (
                    round(
                        (self.process_started_at - request.broker_spawn_returned_at)
                        * 1000,
                        1,
                    )
                )
            if request.broker_send_started_at is not None:
                startup_timings_ms[
                    "worker_request_received_after_broker_send_started"
                ] = round(
                    (request_started_at - request.broker_send_started_at) * 1000, 1
                )
            if self.first_request_received_ms is not None:
                startup_timings_ms["worker_first_request_received"] = (
                    self.first_request_received_ms
                )
            if self.first_request_wait_after_ready_ms is not None:
                startup_timings_ms["worker_first_request_wait_after_ready"] = (
                    self.first_request_wait_after_ready_ms
                )
            rss_after_compile_mb = _get_current_rss_mb()
            startup_timings_ms["worker_rss_after_compile_mb"] = rss_after_compile_mb
            startup_timings_ms["worker_rss_delta_mb"] = round(
                rss_after_compile_mb - rss_before_compile_mb, 1
            )
            summary = CompileSummary(
                diagnostics=state.diagnostics,
                dependency_paths=sorted(state.resolved_dependency_paths),
                query_capabilities=sorted(state.query_capabilities),
                phase_timings_ms=dict(state.phase_timings_ms),
                profile_counts=dict(state.profile_counts),
                compiled_file_timings_ms=dict(state.compiled_file_timings_ms),
                startup_timings_ms=startup_timings_ms,
                error=str(state.last_error) if state.last_error else None,
            )
            return WorkerResponse(
                ok=True,
                generation=request.generation,
                result=summary,
                dependencies=summary.dependency_paths,
            )
        except Exception as exc:
            return WorkerResponse(
                ok=False,
                generation=request.generation,
                error="".join(traceback.format_exception(exc)),
            )

    def query(self, request: QueryRequest) -> WorkerResponse:
        compiled_generation = self.compiled_generation.get(request.uri)
        if compiled_generation != request.generation:
            return WorkerResponse(
                ok=False,
                generation=compiled_generation,
                error="generation mismatch",
            )

        try:
            result = self._dispatch_query(request)
            return WorkerResponse(ok=True, generation=request.generation, result=result)
        except Exception as exc:
            return WorkerResponse(
                ok=False,
                generation=request.generation,
                error="".join(traceback.format_exception(exc)),
            )

    def _dispatch_query(self, request: QueryRequest):
        uri = request.uri
        payload = request.payload
        lsp_server = self.lsp_server

        if request.method == "completion":
            params = SimpleNamespace(
                text_document=SimpleNamespace(uri=uri),
                position=SimpleNamespace(
                    line=payload["line"], character=payload["character"]
                ),
                context=None,
            )
            return lsp_server._document_completion_impl(params)

        if request.method == "hover":
            params = SimpleNamespace(
                text_document=SimpleNamespace(uri=uri),
                position=SimpleNamespace(
                    line=payload["line"], character=payload["character"]
                ),
            )
            return lsp_server._document_hover_impl(params)

        if request.method == "definition":
            params = SimpleNamespace(
                text_document=SimpleNamespace(uri=uri),
                position=SimpleNamespace(
                    line=payload["line"], character=payload["character"]
                ),
            )
            return lsp_server._document_definition_impl(params)

        if request.method == "type_definition":
            params = SimpleNamespace(
                text_document=SimpleNamespace(uri=uri),
                position=SimpleNamespace(
                    line=payload["line"], character=payload["character"]
                ),
            )
            return lsp_server._document_type_definition_impl(params)

        if request.method == "references":
            params = SimpleNamespace(
                text_document=SimpleNamespace(uri=uri),
                position=SimpleNamespace(
                    line=payload["line"], character=payload["character"]
                ),
                context=SimpleNamespace(
                    include_declaration=payload.get("include_declaration", False)
                ),
            )
            return lsp_server._find_references_impl(params)

        if request.method == "prepare_rename":
            params = SimpleNamespace(
                text_document=SimpleNamespace(uri=uri),
                position=SimpleNamespace(
                    line=payload["line"], character=payload["character"]
                ),
            )
            return lsp_server._prepare_rename_impl(params)

        if request.method == "rename":
            params = SimpleNamespace(
                text_document=SimpleNamespace(uri=uri),
                position=SimpleNamespace(
                    line=payload["line"], character=payload["character"]
                ),
                new_name=payload["new_name"],
            )
            return lsp_server._rename_impl(params)

        if request.method == "code_action":
            diagnostics = payload.get("diagnostics", [])
            params = SimpleNamespace(
                text_document=SimpleNamespace(uri=uri),
                range=SimpleNamespace(
                    start=SimpleNamespace(
                        line=payload["start_line"],
                        character=payload["start_character"],
                    ),
                    end=SimpleNamespace(
                        line=payload["end_line"],
                        character=payload["end_character"],
                    ),
                ),
                context=SimpleNamespace(diagnostics=diagnostics),
            )
            return lsp_server._code_action_impl(params)

        raise ValueError(f"Unknown query method: {request.method}")


def worker_main(conn: Connection) -> None:
    process_started_at = time.perf_counter()
    runtime = WorkerRuntime(process_started_at=process_started_at)

    while True:
        recv_started_at = time.perf_counter()
        try:
            request: WorkerRequest = conn.recv()
        except EOFError:
            conn.close()
            return

        if runtime.first_request_received_ms is None:
            runtime.first_request_received_ms = round(
                (time.perf_counter() - process_started_at) * 1000, 1
            )
            runtime.first_request_wait_after_ready_ms = round(
                (time.perf_counter() - recv_started_at) * 1000, 1
            )

        if isinstance(request, CompileRequest):
            conn.send(runtime.compile(request))
            continue
        if isinstance(request, QueryRequest):
            conn.send(runtime.query(request))
            continue
        if isinstance(request, CrashRequest):
            conn.close()
            raise RuntimeError(request.reason)

        conn.send(WorkerResponse(ok=False, error=f"Unsupported request: {request!r}"))
