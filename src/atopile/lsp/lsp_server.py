# Copyright (c) 2024 atopile contributors
# SPDX-License-Identifier: MIT
"""
Language Server Protocol implementation for atopile.

This LSP server provides:
- Diagnostics (errors/warnings on build)
- Completion (dot, new, import)
- Hover information
- Go-to-definition
- Code actions (auto-import)
- Find references
- Document formatting
"""

from __future__ import annotations

import inspect
import logging
import re
import textwrap
import time
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from importlib.metadata import version as get_package_version
from pathlib import Path
from typing import Any

import lsprotocol.types as lsp
from pygls import uris
from pygls.lsp.server import LanguageServer

import faebryk.core.faebrykpy as fbrk
import faebryk.core.graph as graph
import faebryk.core.node as fabll
import faebryk.library._F as F
from atopile.compiler import DslImportError, DslRichException, DslUndefinedSymbolError
from atopile.compiler import ast_types as AST
from atopile.compiler.build import (
    BuildFileResult,
    Linker,
    StdlibRegistry,
    UnresolvedTypeReferencesError,
    _build_from_ctx,
    build_stage_2,
)
from atopile.compiler.parse import parse_text_as_file_recovering
from atopile.compiler.parse_utils import get_src_info_from_token
from atopile.config import find_project_dir
from atopile.errors import (
    DowngradedExceptionCollector,
    UserException,
    iter_leaf_exceptions,
)
from atopile.logging import get_logger
from atopile.lsp.forkserver_broker import BROKER

logger = get_logger(__name__)


def _configure_lsp_logging() -> None:
    # pygls/json-rpc emits a large volume of registration and wire-traffic logs
    # at INFO, which drowns out the lifecycle events we actually care about.
    logging.getLogger("pygls").setLevel(logging.WARNING)
    logging.getLogger("pygls.server").setLevel(logging.WARNING)
    logging.getLogger("pygls.protocol").setLevel(logging.WARNING)
    logging.getLogger("pygls.protocol.json_rpc").setLevel(logging.ERROR)
    logging.getLogger("pygls.feature_manager").setLevel(logging.WARNING)
    logging.getLogger("pygls.workspace").setLevel(logging.WARNING)


_configure_lsp_logging()


def reset_type_caches() -> int:
    """Clear graph-backed faebryk type caches."""
    from faebryk.core.node import TypeNodeBoundTG

    TypeNodeBoundTG.__TYPE_NODE_MAP__.clear()
    pending = [fabll.Node]
    cache_count = 0
    while pending:
        cls = pending.pop()
        if type_cache := getattr(cls, "_type_cache", None):
            cache_count += len(type_cache)
            type_cache.clear()
        pending.extend(cls.__subclasses__())
    return cache_count


def clear_type_caches_for_file(
    file_path: Path, import_path: str | None = None
) -> tuple[int, int]:
    """Clear cached graph-backed types and type names for a file."""
    cache_count = reset_type_caches()

    prefixes = {str(file_path.resolve()) + "::"}
    if import_path:
        prefixes.add(import_path + "::")

    file_type_count = 0
    for type_id in list(fabll.Node._seen_types):
        if any(type_id.startswith(prefix) for prefix in prefixes):
            del fabll.Node._seen_types[type_id]
            file_type_count += 1

    return cache_count, file_type_count


# LSP Server Configuration
DISTRIBUTION_NAME = "atopile"
TOOL_DISPLAY = "atopile"
MAX_WORKERS = 5
SEMANTIC_QUERY_METHODS = frozenset(
    {
        "hover",
        "definition",
        "type_definition",
        "references",
        "prepare_rename",
        "rename",
    }
)
BASE_QUERY_CAPABILITIES = frozenset({"completion", "code_action"})
SEMANTIC_QUERY_CAPABILITIES = BASE_QUERY_CAPABILITIES | SEMANTIC_QUERY_METHODS

LSP_SERVER = LanguageServer(
    name=DISTRIBUTION_NAME,
    version=get_package_version(DISTRIBUTION_NAME),
    max_workers=MAX_WORKERS,
    text_document_sync_kind=lsp.TextDocumentSyncKind.Full,
)


# -----------------------------------------------------------------------------
# Document State Management
# -----------------------------------------------------------------------------


@dataclass
class ASTNodeLocation:
    """Represents the source location of an AST node."""

    start_line: int  # 1-indexed (from ANTLR)
    start_col: int  # 0-indexed
    end_line: int  # 1-indexed
    end_col: int  # 0-indexed
    node: fabll.Node

    def contains_position(self, line: int, col: int) -> bool:
        """Check if position (1-indexed line, 0-indexed col) is in this range."""
        if line < self.start_line or line > self.end_line:
            return False
        if line == self.start_line and col < self.start_col:
            return False
        if line == self.end_line and col > self.end_col:
            return False
        return True


@dataclass
class DocumentState:
    """
    Holds the build state for a single document.

    State is always for the current generation only. Partial queries are allowed
    when the current-generation build produced enough structure to support them.
    """

    uri: str
    version: int = 0

    # Graph instances - each document gets its own graph for isolation
    graph_view: graph.GraphView | None = None
    type_graph: fbrk.TypeGraph | None = None
    stdlib: StdlibRegistry | None = None

    # Build results
    build_result: BuildFileResult | None = None
    query_capabilities: set[str] = field(default_factory=set)

    # AST node index for hover/go-to-definition: maps (line, col) to nodes
    ast_nodes: list[ASTNodeLocation] = field(default_factory=list)
    invalid_ranges: list[lsp.Range] = field(default_factory=list)

    # Last build error (if any)
    last_error: Exception | None = None

    # Diagnostics from last build
    diagnostics: list[lsp.Diagnostic] = field(default_factory=list)

    # Resolved dependency paths for the last linked build
    resolved_dependency_paths: set[Path] = field(default_factory=set)
    phase_timings_ms: dict[str, float] = field(default_factory=dict)
    profile_counts: dict[str, int] = field(default_factory=dict)
    compiled_file_timings_ms: dict[str, float] = field(default_factory=dict)

    def ensure_graph(self) -> tuple[graph.GraphView, fbrk.TypeGraph, StdlibRegistry]:
        """Ensure graph infrastructure exists, creating if needed."""
        if self.graph_view is None:
            self.graph_view = graph.GraphView.create()
        if self.type_graph is None:
            self.type_graph = fbrk.TypeGraph.create(g=self.graph_view)
        if self.stdlib is None:
            self.stdlib = StdlibRegistry(self.type_graph)
        return self.graph_view, self.type_graph, self.stdlib

    def reset_graph(self) -> None:
        """Reset the graph for a fresh build."""
        # Destroy old graph if exists
        if self.graph_view is not None:
            try:
                self.graph_view.destroy()
            except Exception:
                pass
        self.graph_view = None
        self.type_graph = None
        self.stdlib = None
        self.build_result = None
        self.query_capabilities = set()
        self.ast_nodes = []
        self.invalid_ranges = []
        self.resolved_dependency_paths = set()
        self.phase_timings_ms = {}
        self.profile_counts = {}
        self.compiled_file_timings_ms = {}


# Global document state storage
DOCUMENT_STATES: dict[str, DocumentState] = {}


def get_document_state(uri: str) -> DocumentState:
    """Get or create document state for a URI."""
    if uri not in DOCUMENT_STATES:
        DOCUMENT_STATES[uri] = DocumentState(uri=uri)
    return DOCUMENT_STATES[uri]


def get_file_path(uri: str) -> Path:
    """Convert a URI to a file path."""
    fs_path = uris.to_fs_path(uri)
    if fs_path is None:
        # For non-file URIs, extract a reasonable path
        if uri.startswith("file://"):
            return Path(uri[7:])
        # Return a placeholder path for test URIs
        return Path(f"/tmp/{uri.replace(':', '_').replace('/', '_')}.ato")
    return Path(fs_path)


def get_file_contents(uri: str) -> tuple[Path, str]:
    """Get the file path and contents for a URI."""
    document = LSP_SERVER.workspace.get_text_document(uri)
    return Path(document.path), document.source


def _broker_manages_document(uri: str) -> bool:
    return BROKER.get_document(uri) is not None


def _broker_query(uri: str, method: str, payload: dict[str, Any]) -> Any:
    return BROKER.query(uri, method, payload)


def _diagnostic_to_invalid_range(diagnostic: lsp.Diagnostic) -> lsp.Range:
    return lsp.Range(
        start=lsp.Position(
            line=diagnostic.range.start.line,
            character=diagnostic.range.start.character,
        ),
        end=lsp.Position(
            line=diagnostic.range.end.line,
            character=diagnostic.range.end.character,
        ),
    )


def _position_in_range(position: lsp.Position, range_: lsp.Range) -> bool:
    if position.line < range_.start.line or position.line > range_.end.line:
        return False
    if (
        position.line == range_.start.line
        and position.character < range_.start.character
    ):
        return False
    if position.line == range_.end.line and position.character > range_.end.character:
        return False
    return True


def _position_is_invalid(state: DocumentState, position: lsp.Position) -> bool:
    return any(_position_in_range(position, range_) for range_ in state.invalid_ranges)


def _range_overlaps_invalid_region(state: DocumentState, range_: lsp.Range) -> bool:
    for invalid_range in state.invalid_ranges:
        if _position_in_range(range_.start, invalid_range) or _position_in_range(
            range_.end, invalid_range
        ):
            return True
        if _position_in_range(invalid_range.start, range_) or _position_in_range(
            invalid_range.end, range_
        ):
            return True
    return False


def _compute_query_capabilities(
    build_result: BuildFileResult | None,
    ast_nodes: list[ASTNodeLocation],
    type_graph: fbrk.TypeGraph | None,
    graph_view: graph.GraphView | None,
) -> set[str]:
    capabilities = set(BASE_QUERY_CAPABILITIES)
    if build_result is None or type_graph is None or graph_view is None:
        return capabilities
    capabilities.update(SEMANTIC_QUERY_METHODS)
    return capabilities


# -----------------------------------------------------------------------------
# Source Location Utilities
# -----------------------------------------------------------------------------


def source_info_to_lsp_range(source_info: AST.SourceInfo) -> lsp.Range:
    """Convert AST SourceInfo to LSP Range (0-indexed lines)."""
    return lsp.Range(
        start=lsp.Position(
            line=max(source_info.start_line - 1, 0),
            character=source_info.start_col,
        ),
        end=lsp.Position(
            line=max(source_info.end_line - 1, 0),
            character=source_info.end_col,
        ),
    )


def file_location_to_lsp_range(loc: AST.FileLocation) -> lsp.Range:
    """Convert AST FileLocation to LSP Range (0-indexed lines)."""
    return lsp.Range(
        start=lsp.Position(
            line=max(loc.get_start_line() - 1, 0),
            character=loc.get_start_col(),
        ),
        end=lsp.Position(
            line=max(loc.get_end_line() - 1, 0),
            character=loc.get_end_col(),
        ),
    )


def exception_to_diagnostic(
    exc: UserException,
    severity: lsp.DiagnosticSeverity = lsp.DiagnosticSeverity.Error,
) -> tuple[Path | None, lsp.Diagnostic]:
    """Convert a UserException to an LSP Diagnostic."""
    start_file_path = None
    start_line, start_col = 0, 0
    stop_line, stop_col = 0, 0

    origin_start = getattr(exc, "origin_start", None)
    origin_stop = getattr(exc, "origin_stop", None)
    source_chunk = exc.source_chunk

    if origin_start is not None:
        start_file_path, start_line, start_col = get_src_info_from_token(origin_start)
        if origin_stop is not None:
            stop_line, stop_col = (
                origin_stop.line,
                origin_stop.column + len(origin_stop.text),
            )
        else:
            stop_line, stop_col = start_line + 1, 0
    elif source_chunk is not None:
        try:
            loc = source_chunk.loc.get()
            start_line = loc.get_start_line()
            start_col = loc.get_start_col()
            stop_line = loc.get_end_line()
            stop_col = loc.get_end_col()
            src_path = source_chunk.get_path()
            start_file_path = None if not src_path or src_path == "None" else src_path
        except Exception:
            pass

    # Convert from 1-indexed (ANTLR) to 0-indexed (LSP)
    start_line = max(start_line - 1, 0)
    stop_line = max(stop_line - 1, 0)

    # Handle case where file path is the string "None" (from parsing string sources)
    file_path = (
        Path(start_file_path) if start_file_path and start_file_path != "None" else None
    )

    deprecated_symbol_match = re.match(r"'([^']+)' is deprecated", exc.message)
    deprecated_symbol = (
        deprecated_symbol_match.group(1) if deprecated_symbol_match else None
    )
    if deprecated_symbol is not None and source_chunk is not None:
        try:
            found_at = source_chunk.get_text().find(deprecated_symbol)
            if found_at >= 0:
                start_col += found_at
                stop_line = start_line
                stop_col = start_col + len(deprecated_symbol)
        except Exception:
            pass
    elif file_path is not None and deprecated_symbol is not None:
        try:
            lines = file_path.read_text().splitlines()
            if start_line < len(lines):
                line_text = lines[start_line]
                found_at = line_text.find(deprecated_symbol, start_col)
                if found_at >= 0:
                    start_col = found_at
                    stop_line = start_line
                    stop_col = found_at + len(deprecated_symbol)
        except Exception:
            pass

    return file_path, lsp.Diagnostic(
        range=lsp.Range(
            start=lsp.Position(line=start_line, character=start_col),
            end=lsp.Position(line=stop_line, character=stop_col),
        ),
        message=exc.message,
        severity=severity,
        code=getattr(exc, "code", None),
        source=TOOL_DISPLAY,
    )


def _dsl_rich_exception_to_diagnostic(
    exc: DslRichException,
    source_text: str | None = None,
    severity: lsp.DiagnosticSeverity = lsp.DiagnosticSeverity.Error,
) -> tuple[Path | None, lsp.Diagnostic] | None:
    source_chunk = exc.source_chunk
    if source_chunk is None:
        return None

    loc = source_chunk.loc.get()

    symbol_match = re.search(r"Symbol `([^`]+)`", exc.message)
    if symbol_match is None:
        symbol_match = re.match(r"`([^`]+)`", exc.message)
    symbol_name = symbol_match.group(1) if symbol_match else None

    range_ = file_location_to_lsp_range(loc)
    if symbol_name is not None and source_text is not None:
        line_no = range_.start.line
        lines = source_text.splitlines()
        if line_no < len(lines):
            line_text = lines[line_no]
            found_at = line_text.find(symbol_name, range_.start.character)
            if found_at >= 0:
                range_ = lsp.Range(
                    start=lsp.Position(line=line_no, character=found_at),
                    end=lsp.Position(
                        line=line_no, character=found_at + len(symbol_name)
                    ),
                )

    file_path = exc.file_path
    resolved_path = file_path.resolve() if isinstance(file_path, Path) else file_path
    return resolved_path, lsp.Diagnostic(
        range=range_,
        message=exc.message,
        severity=severity,
        source=TOOL_DISPLAY,
    )


def _pathless_deprecation_diagnostic_matches_source(
    diag: lsp.Diagnostic,
    *,
    source_text: str,
) -> bool:
    deprecated_symbol_match = re.match(r"'([^']+)' is deprecated", diag.message)
    if deprecated_symbol_match is None:
        return True

    deprecated_symbol = deprecated_symbol_match.group(1)
    lines = source_text.splitlines()
    line_idx = diag.range.start.line
    if line_idx < 0 or line_idx >= len(lines):
        return False

    line_text = lines[line_idx]
    start = diag.range.start.character
    end = diag.range.end.character
    if (
        0 <= start <= end <= len(line_text)
        and line_text[start:end] == deprecated_symbol
    ):
        return True

    return deprecated_symbol in line_text


def _find_diagnostics_for_exception(
    exc: Exception,
    g: graph.GraphView,
    tg: fbrk.TypeGraph,
    message: str,
) -> list[lsp.Diagnostic]:
    """
    Try to find source locations from the graph for an exception.

    For errors like "No handler for node type: BinaryExpression", this searches
    the graph for all instances of that AST node type and returns diagnostics
    for each one.
    """
    import re

    diagnostics: list[lsp.Diagnostic] = []

    # Try to extract AST node type from error messages like:
    # "No handler for node type: BinaryExpression"
    match = re.search(r"No handler for node type: (\w+)", str(exc))
    if not match:
        return diagnostics

    node_type_name = match.group(1)

    # Try to get the AST type class
    try:
        ast_type_cls = getattr(AST, node_type_name)
    except AttributeError:
        return diagnostics

    # Search the graph for all instances of this type
    try:
        bound_type = ast_type_cls.bind_typegraph(tg)
        for inst in bound_type.get_instances(g):
            try:
                typed_node = inst.cast(ast_type_cls)
                loc = typed_node.source.get().loc.get()

                diagnostics.append(
                    lsp.Diagnostic(
                        range=lsp.Range(
                            start=lsp.Position(
                                line=max(loc.get_start_line() - 1, 0),
                                character=loc.get_start_col(),
                            ),
                            end=lsp.Position(
                                line=max(loc.get_end_line() - 1, 0),
                                character=loc.get_end_col(),
                            ),
                        ),
                        message=message,
                        severity=lsp.DiagnosticSeverity.Error,
                        source=TOOL_DISPLAY,
                    )
                )
            except Exception:
                continue
    except Exception:
        pass

    return diagnostics


def _find_diagnostics_for_unresolved_type_references(
    exc: UnresolvedTypeReferencesError,
    build_result: BuildFileResult | None,
    source_text: str,
) -> list[lsp.Diagnostic]:
    """Map unresolved linker refs back to source locations when possible."""
    if build_result is None:
        return []

    diagnostics: list[lsp.Diagnostic] = []

    for _type_node, unresolved_type_ref in exc.unresolved_type_references:
        try:
            unresolved_ident = fbrk.TypeGraph.get_type_reference_identifier(
                type_reference=unresolved_type_ref
            )
        except Exception:
            unresolved_ident = None

        matched = False
        for (
            type_ref,
            import_ref,
            source_node,
            traceback_stack,
        ) in build_result.state.external_type_refs:
            try:
                same_ref = type_ref.node().is_same(other=unresolved_type_ref.node())
            except Exception:
                same_ref = False

            try:
                candidate_ident = fbrk.TypeGraph.get_type_reference_identifier(
                    type_reference=type_ref
                )
            except Exception:
                candidate_ident = None

            if not same_ref and candidate_ident != unresolved_ident:
                continue

            matched = True
            if import_ref is None:
                symbol_name = candidate_ident or unresolved_ident or "<unknown>"
                rich_exc = DslRichException(
                    f"Symbol `{symbol_name}` is not defined in this scope",
                    original=DslUndefinedSymbolError(),
                    source_node=source_node,
                    traceback=traceback_stack,
                )
            elif import_ref.path is None:
                rich_exc = DslRichException(
                    (
                        "Standard library import "
                        f"`{import_ref.name}` could not be resolved"
                    ),
                    original=DslImportError(),
                    source_node=source_node,
                    traceback=traceback_stack,
                )
            else:
                rich_exc = DslRichException(
                    f"Unable to resolve import `{import_ref.path}`",
                    original=DslImportError(),
                    source_node=source_node,
                    traceback=traceback_stack,
                )

            rich_diag = _dsl_rich_exception_to_diagnostic(
                rich_exc, source_text=source_text
            )
            if rich_diag is not None:
                _exc_path, diag = rich_diag
                diagnostics.append(diag)
            break

        if not matched:
            diagnostics.append(
                lsp.Diagnostic(
                    range=lsp.Range(
                        start=lsp.Position(line=0, character=0),
                        end=lsp.Position(line=0, character=0),
                    ),
                    message=str(exc),
                    severity=lsp.DiagnosticSeverity.Warning,
                    source=TOOL_DISPLAY,
                )
            )

    return diagnostics


# -----------------------------------------------------------------------------
# AST Indexing for Hover/Go-to-Definition
# -----------------------------------------------------------------------------


def index_ast_nodes(ast_root: AST.File) -> list[ASTNodeLocation]:
    """
    Build an index of AST nodes with their source locations.

    This enables efficient lookup of "what node is at position X".
    """
    nodes: list[ASTNodeLocation] = []

    def add_node_with_source(node: fabll.Node) -> None:
        """Add a node to the index if it has source location info."""
        try:
            if hasattr(node, "source"):
                source_chunk = node.source.get()
                loc = source_chunk.loc.get()
                nodes.append(
                    ASTNodeLocation(
                        start_line=loc.get_start_line(),
                        start_col=loc.get_start_col(),
                        end_line=loc.get_end_line(),
                        end_col=loc.get_end_col(),
                        node=node,
                    )
                )
        except Exception:
            pass

    # Add the root file node
    add_node_with_source(ast_root)

    # Traverse the AST using the proper structure
    try:
        scope = ast_root.scope.get()
        stmts = scope.stmts.get()

        for stmt in stmts.as_list():
            # Try to cast to known statement types
            for ast_type in [
                AST.BlockDefinition,
                AST.ImportStmt,
                AST.Assignment,
                AST.ConnectStmt,
            ]:
                try:
                    typed_node = stmt.cast(ast_type)
                    add_node_with_source(typed_node)

                    # For BlockDefinitions, also index their contents
                    if ast_type == AST.BlockDefinition:
                        _index_block_contents(typed_node, nodes, add_node_with_source)
                    break
                except Exception:
                    continue
    except Exception:
        pass

    # Sort by specificity (smaller ranges first) for better hover results
    nodes.sort(
        key=lambda n: (
            n.end_line - n.start_line,
            n.end_col - n.start_col,
        )
    )

    return nodes


def _index_block_contents(
    block: AST.BlockDefinition,
    nodes: list[ASTNodeLocation],
    add_fn: Any,
) -> None:
    """Index the contents of a block definition."""
    try:
        block_scope = block.scope.get()
        block_stmts = block_scope.stmts.get()

        for stmt in block_stmts.as_list():
            for ast_type in [
                AST.Assignment,
                AST.ConnectStmt,
                AST.BlockDefinition,
                AST.ImportStmt,
            ]:
                try:
                    typed_node = stmt.cast(ast_type)
                    add_fn(typed_node)

                    # Handle nested assignments (for new expressions)
                    if ast_type == AST.Assignment:
                        try:
                            expr = typed_node.expression.get()
                            new_expr = expr.cast(AST.NewExpression)
                            add_fn(new_expr)
                        except Exception:
                            pass
                    break
                except Exception:
                    continue
    except Exception:
        pass


def find_node_at_position(
    nodes: list[ASTNodeLocation], line: int, col: int
) -> fabll.Node | None:
    """
    Find the most specific AST node at the given position.

    Args:
        nodes: List of AST node locations (should be sorted by specificity)
        line: 1-indexed line number
        col: 0-indexed column number

    Returns:
        The most specific node containing the position, or None.
    """
    for node_loc in nodes:
        if node_loc.contains_position(line, col):
            return node_loc.node
    return None


# -----------------------------------------------------------------------------
# Build Integration
# -----------------------------------------------------------------------------


def build_document(
    uri: str,
    source: str,
    *,
    source_path: Path | None = None,
    source_overrides: Mapping[Path, str] | None = None,
    warm_base=None,
    reuse_warm_base_graph: bool = False,
) -> DocumentState:
    """
    Build a document and update its state.

    This is the core build function that:
    1. Parses and builds the document
    2. Extracts diagnostics (errors/warnings)
    3. Indexes AST nodes for hover/definition

    Returns the updated DocumentState.
    """
    state = get_document_state(uri)
    state.reset_graph()
    state.version += 1
    state.diagnostics = []
    state.last_error = None
    state.query_capabilities = set(BASE_QUERY_CAPABILITIES)

    current_build_result: BuildFileResult | None = None
    current_ast_nodes: list[ASTNodeLocation] = []
    invalid_ranges: list[lsp.Range] = []
    build_started_at = time.perf_counter()
    phase_timings_ms: dict[str, float] = {}
    profile_counts: dict[str, int] = {}
    file_path = source_path or get_file_path(uri)
    document_path = file_path.resolve()

    def finish_phase(name: str, started_at: float) -> None:
        phase_timings_ms[name] = round((time.perf_counter() - started_at) * 1000, 1)

    # Create a NEW graph for this build attempt
    graph_prep_started_at = time.perf_counter()
    borrowed_warm_base_graph = False
    current_file_is_preloaded = (
        warm_base is not None and document_path in warm_base.prelinked_module_type_ids
    )
    if (
        warm_base is not None
        and reuse_warm_base_graph
        and not current_file_is_preloaded
    ):
        new_graph_view, new_type_graph = warm_base.borrow_forked_graph()
        borrowed_warm_base_graph = True
        linked_modules_seed = warm_base.create_linked_modules_seed(new_type_graph)
    elif warm_base is not None and not current_file_is_preloaded:
        new_graph_view, new_type_graph = warm_base.create_working_graph()
        linked_modules_seed = warm_base.create_linked_modules_seed(new_type_graph)
    else:
        new_graph_view = graph.GraphView.create()
        new_type_graph = fbrk.TypeGraph.create(g=new_graph_view)
        linked_modules_seed = None
    finish_phase("graph_prep", graph_prep_started_at)
    new_stdlib = StdlibRegistry(new_type_graph)
    g, tg, stdlib = new_graph_view, new_type_graph, new_stdlib

    state.resolved_dependency_paths = set()
    state.compiled_file_timings_ms = {}

    cache_clear_started_at = time.perf_counter()
    cache_count, file_type_count = clear_type_caches_for_file(document_path)
    finish_phase("cache_clear", cache_clear_started_at)
    profile_counts["cache_entries_cleared"] = cache_count
    profile_counts["file_types_cleared"] = file_type_count

    # Collect diagnostics during build
    diagnostics: list[lsp.Diagnostic] = []

    build_succeeded = False
    linking_succeeded = False
    with DowngradedExceptionCollector(
        UserException, suppress_logging=True
    ) as collector:
        try:
            parse_started_at = time.perf_counter()
            parse_result = parse_text_as_file_recovering(source, src_path=document_path)
            finish_phase("parse", parse_started_at)
            syntax_diagnostics = [
                exception_to_diagnostic(exc)[1] for exc in parse_result.errors
            ]
            diagnostics.extend(syntax_diagnostics)
            invalid_ranges.extend(
                _diagnostic_to_invalid_range(diagnostic)
                for diagnostic in syntax_diagnostics
            )
            profile_counts["syntax_errors"] = len(parse_result.errors)
            logger.debug("Starting recovered build_from_ctx")
            build_started_phase_at = time.perf_counter()
            result = _build_from_ctx(
                g=g,
                tg=tg,
                import_path=str(document_path),
                root_ctx=parse_result.tree,
                file_path=document_path,
            )
            finish_phase("build", build_started_phase_at)
            logger.debug(
                "Recovered build completed. type_roots=%s syntax_errors=%s",
                list(result.state.type_roots.keys()),
                len(parse_result.errors),
            )
            current_build_result = result
            build_succeeded = True
            profile_counts["type_roots"] = len(result.state.type_roots)

            # Index AST nodes for hover/go-to-definition
            current_ast_nodes = index_ast_nodes(result.ast_root)
            profile_counts["ast_nodes"] = len(current_ast_nodes)

            # Try to link imports (may fail if dependencies missing)
            link_started_at = time.perf_counter()
            try:
                # Get config for linker
                from atopile.config import config

                try:
                    project_root = (
                        _infer_lsp_project_root(file_path) or file_path.parent
                    )
                    config.apply_options(entry=None, working_dir=project_root)
                except Exception:
                    pass

                linker = Linker(
                    config_obj=config,
                    stdlib=stdlib,
                    tg=tg,
                    source_overrides=source_overrides,
                    linked_modules_seed=linked_modules_seed,
                )
                try:
                    build_stage_2(
                        g=g,
                        tg=tg,
                        linker=linker,
                        result=result,
                        validate=False,
                    )
                except Exception:
                    # Keep linker diagnostics, but still try deferred execution.
                    # This lets inheritance/retypes complete for editor features
                    # even when unrelated unresolved symbols remain in the file.
                    from atopile.compiler.deferred_executor import DeferredExecutor

                    try:
                        DeferredExecutor(
                            g=g,
                            tg=tg,
                            state=result.state,
                            visitor=result.visitor,
                            stdlib=linker._stdlib,
                            file_imports=linker,
                        ).execute()
                    except Exception:
                        pass
                    raise
                state.resolved_dependency_paths = set(linker._linked_modules)
                linking_succeeded = True
            except Exception as link_error:
                if isinstance(link_error, UnresolvedTypeReferencesError):
                    unresolved_diags = _find_diagnostics_for_unresolved_type_references(
                        link_error,
                        build_result=result,
                        source_text=source,
                    )
                    if unresolved_diags:
                        diagnostics.extend(unresolved_diags)
                    else:
                        diagnostics.append(
                            lsp.Diagnostic(
                                range=lsp.Range(
                                    start=lsp.Position(line=0, character=0),
                                    end=lsp.Position(line=0, character=0),
                                ),
                                message=f"Import resolution: {link_error}",
                                severity=lsp.DiagnosticSeverity.Warning,
                                source=TOOL_DISPLAY,
                            )
                        )
                elif isinstance(link_error, DslRichException):
                    rich_diag = _dsl_rich_exception_to_diagnostic(
                        link_error, source_text=source
                    )
                    if rich_diag is not None:
                        exc_path, diag = rich_diag
                        if exc_path is None or exc_path.resolve() == document_path:
                            diagnostics.append(diag)
                        else:
                            diagnostics.append(
                                lsp.Diagnostic(
                                    range=lsp.Range(
                                        start=lsp.Position(line=0, character=0),
                                        end=lsp.Position(line=0, character=0),
                                    ),
                                    message=link_error.message,
                                    severity=lsp.DiagnosticSeverity.Error,
                                    source=TOOL_DISPLAY,
                                )
                            )
                    else:
                        diagnostics.append(
                            lsp.Diagnostic(
                                range=lsp.Range(
                                    start=lsp.Position(line=0, character=0),
                                    end=lsp.Position(line=0, character=0),
                                ),
                                message=link_error.message,
                                severity=lsp.DiagnosticSeverity.Error,
                                source=TOOL_DISPLAY,
                            )
                        )
                elif isinstance(link_error, BaseExceptionGroup):
                    handled_rich_error = False
                    for leaf in iter_leaf_exceptions(link_error):
                        if isinstance(leaf, UnresolvedTypeReferencesError):
                            unresolved_diags = (
                                _find_diagnostics_for_unresolved_type_references(
                                    leaf,
                                    build_result=result,
                                    source_text=source,
                                )
                            )
                            if unresolved_diags:
                                diagnostics.extend(unresolved_diags)
                                handled_rich_error = True
                            continue

                        if isinstance(leaf, DslRichException):
                            handled_rich_error = True
                            rich_diag = _dsl_rich_exception_to_diagnostic(
                                leaf, source_text=source
                            )
                            if rich_diag is not None:
                                exc_path, diag = rich_diag
                                if (
                                    exc_path is None
                                    or exc_path.resolve() == document_path
                                ):
                                    diagnostics.append(diag)
                    if not handled_rich_error:
                        diagnostics.append(
                            lsp.Diagnostic(
                                range=lsp.Range(
                                    start=lsp.Position(line=0, character=0),
                                    end=lsp.Position(line=0, character=0),
                                ),
                                message=f"Import resolution: {link_error}",
                                severity=lsp.DiagnosticSeverity.Warning,
                                source=TOOL_DISPLAY,
                            )
                        )
                else:
                    # Linking failures are warnings when they are not
                    # source-located DSL errors.
                    diagnostics.append(
                        lsp.Diagnostic(
                            range=lsp.Range(
                                start=lsp.Position(line=0, character=0),
                                end=lsp.Position(line=0, character=0),
                            ),
                            message=f"Import resolution: {link_error}",
                            severity=lsp.DiagnosticSeverity.Warning,
                            source=TOOL_DISPLAY,
                        )
                    )
            finally:
                finish_phase("link", link_started_at)

        except* UserException as exc_group:
            for exc in iter_leaf_exceptions(exc_group):
                if isinstance(exc, DslRichException):
                    rich_diag = _dsl_rich_exception_to_diagnostic(
                        exc, source_text=source
                    )
                    if rich_diag is not None:
                        exc_path, diag = rich_diag
                        if exc_path is None or exc_path.resolve() == document_path:
                            diagnostics.append(diag)
                        continue
                exc_path, diag = exception_to_diagnostic(exc)
                if exc_path is None or exc_path.resolve() == document_path:
                    diagnostics.append(diag)

        except* Exception as exc_group:
            # Handle non-UserException errors
            for exc in iter_leaf_exceptions(exc_group):
                state.last_error = exc
                logger.exception(f"Build error for {uri}: {exc}")

                if isinstance(exc, UnresolvedTypeReferencesError):
                    unresolved_diags = _find_diagnostics_for_unresolved_type_references(
                        exc,
                        build_result=result,
                        source_text=source,
                    )
                    if unresolved_diags:
                        diagnostics.extend(unresolved_diags)
                        continue

                if isinstance(exc, DslRichException):
                    rich_diag = _dsl_rich_exception_to_diagnostic(
                        exc, source_text=source
                    )
                    if rich_diag is not None:
                        exc_path, diag = rich_diag
                        if exc_path is None or exc_path.resolve() == document_path:
                            diagnostics.append(diag)
                            continue

                # Try to find source locations from the graph for unhandled node types
                node_diagnostics = _find_diagnostics_for_exception(exc, g, tg, str(exc))

                if node_diagnostics:
                    diagnostics.extend(node_diagnostics)
                else:
                    # Fallback to line 0 if we can't find locations
                    diagnostics.append(
                        lsp.Diagnostic(
                            range=lsp.Range(
                                start=lsp.Position(line=0, character=0),
                                end=lsp.Position(line=0, character=0),
                            ),
                            message=f"Build error: {exc}",
                            severity=lsp.DiagnosticSeverity.Error,
                            source=TOOL_DISPLAY,
                        )
                    )

        # Add warning diagnostics from collector
        for error, severity_level in collector:
            if severity_level == logging.WARNING:
                exc_path, diag = exception_to_diagnostic(
                    error, severity=lsp.DiagnosticSeverity.Warning
                )
                if (
                    exc_path is None
                    and not _pathless_deprecation_diagnostic_matches_source(
                        diag, source_text=source
                    )
                ):
                    continue
                if exc_path is None or exc_path.resolve() == document_path:
                    diagnostics.append(diag)

    # If build AND linking succeeded, validate field references against typegraph
    # Only validate when linking succeeded - otherwise external types aren't resolved
    # and we'd get false positives for valid references to imported types
    logger.debug(
        "Build status: build_succeeded=%s linking_succeeded=%s previous_build=%s",
        build_succeeded,
        linking_succeeded,
        "yes" if state.build_result else "no",
    )
    if build_succeeded and linking_succeeded and current_build_result is not None:
        field_validation_started_at = time.perf_counter()
        field_ref_diagnostics = _validate_field_references(
            g=g, tg=tg, build_result=current_build_result, uri=uri
        )
        finish_phase("field_validation", field_validation_started_at)
        diagnostics.extend(field_ref_diagnostics)
        profile_counts["field_ref_diagnostics"] = len(field_ref_diagnostics)
    phase_timings_ms["total"] = round(
        (time.perf_counter() - build_started_at) * 1000, 1
    )
    logger.info(
        "Built %s: diagnostics=%s deps=%s",
        document_path,
        len(diagnostics),
        len(state.resolved_dependency_paths),
    )

    if build_succeeded:
        state.graph_view = new_graph_view
        state.type_graph = new_type_graph
        state.stdlib = new_stdlib
        state.build_result = current_build_result
        state.ast_nodes = current_ast_nodes
        state.query_capabilities = _compute_query_capabilities(
            current_build_result,
            current_ast_nodes,
            state.type_graph,
            state.graph_view,
        )
    else:
        if not borrowed_warm_base_graph:
            try:
                new_graph_view.destroy()
            except Exception:
                pass
        logger.debug("Build failed for %s with no partial semantic state", uri)

    state.diagnostics = diagnostics
    state.invalid_ranges = invalid_ranges
    state.phase_timings_ms = phase_timings_ms
    state.profile_counts = profile_counts
    if current_build_result is not None:
        state.compiled_file_timings_ms = dict(
            current_build_result.state.compiled_file_timings_ms
        )
    else:
        state.compiled_file_timings_ms = {}
    return state


def _infer_lsp_project_root(path: Path) -> Path | None:
    """
    Infer the owning project root for an LSP-opened file.

    Files opened from ``<project>/.ato/modules/...`` should still resolve imports
    against ``<project>`` rather than the dependency package's nested ``ato.yaml``.
    """
    resolved = path.resolve()

    for candidate in (resolved, *resolved.parents):
        if candidate.name == "modules" and candidate.parent.name == ".ato":
            return candidate.parent.parent

    return find_project_dir(resolved)


def _validate_field_references(
    g: graph.GraphView,
    tg: fbrk.TypeGraph,
    build_result,
    uri: str,
) -> list[lsp.Diagnostic]:
    """
    Validate all field references in the document against the linked typegraph.

    This catches references to non-existent members like `micro.power_big` where
    `power_big` doesn't exist on the `micro` type.

    Returns a list of diagnostics for invalid field references.
    """
    diagnostics: list[lsp.Diagnostic] = []

    try:
        # Get all FieldRef instances from the graph
        field_ref_type = AST.FieldRef.bind_typegraph(tg)

        for inst in field_ref_type.get_instances(g):
            try:
                fr = inst.cast(AST.FieldRef)

                # Get the parts of the field reference including array indices
                parts = list(fr.parts.get().as_list())
                if len(parts) < 2:
                    # Single-part references are just local names, skip
                    continue

                # Build path including array indices (e.g., ["r1", "unnamed[0]"])
                path_parts: list[str] = []
                for p in parts:
                    part = p.cast(AST.FieldRefPart)
                    name = part.name.get().get_single()
                    key = part.get_key()
                    if key is not None:
                        # Include array index in the path segment
                        path_parts.append(f"{name}[{key}]")
                    else:
                        path_parts.append(name)

                # Find the containing module type to validate the path
                # The first part should be a child of one of the type_roots
                # Strip index from first part for lookup
                root_name = path_parts[0].split("[")[0]
                parent_type = None

                for type_name, type_node in build_result.state.type_roots.items():
                    try:
                        # Check if root_name is a child of this type
                        children = tg.collect_make_children(type_node=type_node)
                        for child_name, _ in children:
                            if child_name == root_name:
                                parent_type = type_node
                                break
                    except Exception:
                        continue
                    if parent_type is not None:
                        break

                if parent_type is None:
                    # Couldn't find containing type, skip validation
                    continue

                # Use ensure_child_reference with validate=True to check the path.
                # The path includes array indices like "unnamed[0]" which the
                # typegraph can resolve through type mounts.
                try:
                    path_for_validation: list[str | fbrk.EdgeTraversal] = list(
                        path_parts
                    )
                    tg.ensure_child_reference(
                        type_node=parent_type,
                        path=path_for_validation,
                        validate=True,
                    )
                    # Path resolved successfully - no error
                except fbrk.TypeGraphPathError as exc:
                    # Path doesn't exist - create diagnostic
                    loc = fr.source.get().loc.get()
                    line = loc.get_start_line() - 1
                    col = loc.get_start_col()
                    # Use path without indices for display
                    display_parts = [p.split("[")[0] for p in path_parts]
                    full_path = ".".join(display_parts)

                    # Format error message based on error kind
                    if exc.kind == "missing_parent":
                        failing_segment = exc.failing_segment or "unknown"
                        # Strip index from failing segment for display
                        failing_segment = failing_segment.split("[")[0]
                        parent_path = ".".join(
                            [p.split("[")[0] for p in path_parts[:-1]]
                        )
                        message = (
                            f"Field `{failing_segment}` does not exist "
                            f"on `{parent_path or path_parts[0].split('[')[0]}`"
                        )
                    elif exc.kind == "missing_child":
                        # missing_child means the field doesn't exist on the type
                        failing_segment = exc.failing_segment or "unknown"
                        failing_segment = failing_segment.split("[")[0]
                        idx = exc.failing_segment_index or 1
                        parent_path = ".".join(
                            [p.split("[")[0] for p in path_parts[:idx]]
                        )
                        message = (
                            f"Field `{failing_segment}` does not exist "
                            f"on `{parent_path}`"
                        )
                    elif exc.kind == "invalid_index":
                        index_val = exc.index_value or exc.failing_segment
                        container = ".".join(
                            [
                                p.split("[")[0]
                                for p in path_parts[: exc.failing_segment_index]
                            ]
                            if exc.path
                            else []
                        )
                        message = f"Index `{index_val}` out of range on `{container}`"
                    else:
                        message = f"Field `{full_path}` is not defined"

                    diagnostics.append(
                        lsp.Diagnostic(
                            range=lsp.Range(
                                start=lsp.Position(line=line, character=col),
                                end=lsp.Position(
                                    line=line, character=col + len(full_path)
                                ),
                            ),
                            message=message,
                            severity=lsp.DiagnosticSeverity.Error,
                            source=TOOL_DISPLAY,
                        )
                    )

            except Exception as e:
                logger.debug(f"Error processing FieldRef for validation: {e}")
                continue

    except Exception as e:
        logger.debug(f"Error in field reference validation: {e}")

    return diagnostics


# -----------------------------------------------------------------------------
# Completion Helpers
# -----------------------------------------------------------------------------


def extract_field_reference_before_dot(line: str, position: int) -> str | None:
    """
    Extract a field reference before a dot at the given position.

    For example, if line is "mymodule.instance." and position is at the end,
    returns "mymodule.instance".
    """
    # Find the dot position
    if position > 0 and position <= len(line) and line[position - 1] == ".":
        dot_position = position - 1
    else:
        # Look backwards for the most recent dot
        dot_position = -1
        for i in range(min(position - 1, len(line) - 1), -1, -1):
            if line[i] == ".":
                dot_position = i
                break
        if dot_position == -1:
            return None

    # Find the start of the field reference
    start = dot_position
    while start > 0:
        char = line[start - 1]
        if char.isalnum() or char in "._[]":
            start -= 1
        else:
            break

    field_ref = line[start:dot_position].strip()
    return field_ref if field_ref else None


def get_node_completions(node: fabll.Node) -> list[lsp.CompletionItem]:
    """
    Extract completion items from a fabll node.

    Returns parameters, sub-modules, and interfaces as completion items.
    """
    completion_items = []

    try:
        # Get children via composition edges
        children = fbrk.EdgeComposition.get_children_query(
            bound_node=node.instance, direct_only=True
        )

        for child_bound in children:
            try:
                child = fabll.Node(instance=child_bound)

                # Get child name from parent edge
                parent_edge = fbrk.EdgeComposition.get_parent_edge(
                    bound_node=child_bound
                )
                if parent_edge is None:
                    continue
                child_name = fbrk.EdgeComposition.get_name(edge=parent_edge.edge())

                if child_name is None:
                    continue

                # Skip internal/anonymous children
                if child_name.startswith("_") or child_name.startswith("anon"):
                    continue

                class_name = type(child).__name__

                # Determine completion item kind by checking traits
                # Use try_get_trait to avoid exceptions
                try:
                    if child.has_trait(fabll.is_module):
                        kind = lsp.CompletionItemKind.Field
                        detail = f"Module: {class_name}"
                    elif child.has_trait(fabll.is_interface):
                        kind = lsp.CompletionItemKind.Interface
                        detail = f"Interface: {class_name}"
                    elif child.has_trait(F.Parameters.is_parameter):
                        kind = lsp.CompletionItemKind.Unit
                        detail = "Parameter"
                    else:
                        continue
                except Exception:
                    # If trait checking fails, skip this child
                    continue

                completion_items.append(
                    lsp.CompletionItem(
                        label=child_name,
                        kind=kind,
                        detail=detail,
                        documentation=lsp.MarkupContent(
                            kind=lsp.MarkupKind.Markdown,
                            value=f"**{child_name}**: {detail}",
                        ),
                    )
                )

            except Exception:
                continue

    except Exception as e:
        logger.debug(f"Error extracting completions: {e}")

    return completion_items


def get_type_node_completions(
    tg: fbrk.TypeGraph, type_node: graph.BoundNode
) -> list[lsp.CompletionItem]:
    """Extract completion items from a TypeGraph type node without instantiation."""
    from atopile.model.module_introspection import _get_item_type, _is_user_facing_child

    completion_items: list[lsp.CompletionItem] = []

    try:
        make_children = tg.collect_make_children(type_node=type_node)
    except Exception as e:
        logger.debug(f"Error extracting type-node completions: {e}")
        return completion_items

    for identifier, make_child in make_children:
        if not identifier:
            continue

        base_name = identifier.split("[", 1)[0]
        if not _is_user_facing_child(base_name):
            continue

        try:
            type_ref = tg.get_make_child_type_reference(make_child=make_child)
            resolved_type = fbrk.Linker.get_resolved_type(type_reference=type_ref)
            if resolved_type is not None:
                type_name = fbrk.TypeGraph.get_type_name(type_node=resolved_type)
            else:
                type_name = fbrk.TypeGraph.get_type_reference_identifier(
                    type_reference=type_ref
                )
        except Exception:
            resolved_type = None
            type_name = "Unknown"

        type_name_lower = type_name.lower()
        if "pointer" in type_name_lower or "sequence" in type_name_lower:
            continue

        item_type = _get_item_type(tg, resolved_type, type_name)
        if item_type is None:
            continue

        if item_type == "module":
            kind = lsp.CompletionItemKind.Field
            detail = f"Module: {type_name}"
        elif item_type == "interface":
            kind = lsp.CompletionItemKind.Interface
            detail = f"Interface: {type_name}"
        elif item_type == "parameter":
            kind = lsp.CompletionItemKind.Unit
            detail = "Parameter"
        elif item_type == "component":
            kind = lsp.CompletionItemKind.Class
            detail = f"Component: {type_name}"
        else:
            continue

        completion_items.append(
            lsp.CompletionItem(
                label=identifier,
                kind=kind,
                detail=detail,
                documentation=lsp.MarkupContent(
                    kind=lsp.MarkupKind.Markdown,
                    value=f"**{identifier}**: {detail}",
                ),
            )
        )

    return completion_items


def _resolve_make_child_type_node(
    tg: fbrk.TypeGraph, type_node: graph.BoundNode, child_name: str
) -> graph.BoundNode | None:
    """Resolve a child name to its type node using make-child type references."""
    try:
        make_children = tg.collect_make_children(type_node=type_node)
    except Exception:
        return None

    for identifier, make_child in make_children:
        base_name = identifier.split("[", 1)[0]
        if identifier != child_name and base_name != child_name:
            continue
        try:
            type_ref = tg.get_make_child_type_reference(make_child=make_child)
            resolved_type = fbrk.Linker.get_resolved_type(type_reference=type_ref)
        except Exception:
            resolved_type = None
        if resolved_type is not None:
            return resolved_type

    return None


def _resolve_dot_completion_node(
    state: DocumentState, field_ref: str
) -> fabll.Node | None:
    parts = field_ref.split(".")
    if not parts:
        return None

    root_name = parts[0].split("[")[0]
    type_roots = state.build_result.state.type_roots
    g, tg, _ = state.ensure_graph()

    for type_name, type_node in type_roots.items():
        path = parts[1:] if type_name == root_name else parts
        if type_name != root_name:
            children = tg.collect_make_children(type_node=type_node)
            if not any(child_name == root_name for child_name, _ in children):
                continue

        base_instance = tg.instantiate_node(type_node=type_node, attributes={})
        if not path:
            return fabll.Node(instance=base_instance)

        try:
            ref = tg.ensure_child_reference(type_node=type_node, path=path)
            resolved = tg.reference_resolve(reference_node=ref, base_node=base_instance)
        except Exception:
            continue

        return fabll.Node(instance=resolved)

    return None


def _resolve_dot_completion_type_node(
    state: DocumentState, field_ref: str
) -> graph.BoundNode | None:
    parts = [part.split("[", 1)[0] for part in field_ref.split(".") if part]
    if not parts:
        return None

    root_name = parts[0]
    type_roots = state.build_result.state.type_roots
    _, tg, _ = state.ensure_graph()

    for type_name, type_node in type_roots.items():
        if type_name == root_name:
            current_type = type_node
            remaining = parts[1:]
        else:
            current_type = _resolve_make_child_type_node(tg, type_node, root_name)
            if current_type is None:
                continue
            remaining = parts[1:]

        for part in remaining:
            current_type = _resolve_make_child_type_node(tg, current_type, part)
            if current_type is None:
                break

        if current_type is not None:
            return current_type

    return None


def get_stdlib_types() -> list[type[fabll.Node]]:
    """Get all stdlib types available for import."""
    symbols = vars(F).values()
    return [s for s in symbols if isinstance(s, type) and issubclass(s, fabll.Node)]


def _is_module_or_interface_type(node_type: type[fabll.Node]) -> bool:
    """Check if a node type is a module or interface by inspecting class attributes."""
    # In the new API, traits are declared as class attributes using MakeEdge
    return hasattr(node_type, "_is_module") or hasattr(node_type, "_is_interface")


def node_type_to_completion_item(node_type: type[fabll.Node]) -> lsp.CompletionItem:
    """Convert a node type to a completion item."""
    # Check for traits using class attribute inspection
    # Since the new API uses MakeEdge for trait attachment at class level
    has_module_trait = hasattr(node_type, "_is_module")
    has_interface_trait = hasattr(node_type, "_is_interface")
    has_parameter_trait = hasattr(node_type, "_is_parameter")

    if has_module_trait:
        kind = lsp.CompletionItemKind.Field
    elif has_interface_trait:
        kind = lsp.CompletionItemKind.Interface
    elif has_parameter_trait:
        kind = lsp.CompletionItemKind.Unit
    else:
        kind = lsp.CompletionItemKind.Class

    base_class = node_type.mro()[1] if len(node_type.mro()) > 1 else object
    type_name = node_type.__name__

    return lsp.CompletionItem(
        label=type_name,
        kind=kind,
        detail=f"Base: {base_class.__name__}",
        documentation=lsp.MarkupContent(
            kind=lsp.MarkupKind.Markdown,
            value=node_type.__doc__ or "",
        )
        if node_type.__doc__
        else None,
    )


def get_importable_paths(uri: str) -> list[Path]:
    """Get all importable .ato and .py files relative to the project."""
    file_path = get_file_path(uri)
    root = file_path.parent

    prj_root = _infer_lsp_project_root(file_path)
    if prj_root:
        root = prj_root

    ato_files = list(root.rglob("**/*.ato")) + list(root.rglob("**/*.py"))
    ato_files = [
        path.relative_to(root)
        for path in ato_files
        if path.is_file() and path != file_path
    ]

    if prj_root:
        # Remove .ato/modules prefix; exclude parts/ and local/ paths in packages
        ato_files = [
            path if path.parts[:2] != (".ato", "modules") else Path(*path.parts[2:])
            for path in ato_files
            if path.parts[:2] != (".ato", "modules")
            or ("parts" not in path.parts[2:] and path.parts[2:3] != ("local",))
        ]

    return ato_files


def _resolve_import_target_path(uri: str, raw_path: str) -> Path | None:
    from atopile.compiler.build import SearchPathResolver
    from atopile.config import config

    current_file_path = get_file_path(uri)
    project_dir = _infer_lsp_project_root(current_file_path)

    try:
        if project_dir is not None:
            config.apply_options(entry=None, working_dir=project_dir)
    except Exception:
        pass

    resolver = SearchPathResolver(config, extra_search_paths=[])
    try:
        return resolver.resolve(raw_path, base_file=current_file_path)
    except Exception:
        return None


def _get_types_from_import_target(
    uri: str, line_prefix: str
) -> list[lsp.CompletionItem]:
    match = re.search(
        r'from\s+"(?P<path>[^"]+)"\s+import(?:\s+[\w,\s]*)?$',
        line_prefix,
    )
    if not match:
        return []

    target_file = _resolve_import_target_path(uri, match.group("path"))
    if target_file is None:
        return []

    try:
        source = target_file.read_text()
    except Exception:
        return []

    items: list[lsp.CompletionItem] = []
    for line in source.splitlines():
        stripped = line.strip()
        type_match = re.match(
            r"^(module|interface|component)\s+([A-Za-z_][A-Za-z0-9_]*)"
            r"(?:\s+from\s+[A-Za-z_][A-Za-z0-9_]*)?\s*:",
            stripped,
        )
        if not type_match:
            continue

        kind_name, type_name = type_match.groups()
        if kind_name == "interface":
            kind = lsp.CompletionItemKind.Interface
        elif kind_name == "component":
            kind = lsp.CompletionItemKind.Class
        else:
            kind = lsp.CompletionItemKind.Field

        items.append(
            lsp.CompletionItem(
                label=type_name,
                kind=kind,
                detail=f'Imported from "{match.group("path")}"',
            )
        )

    return items


def _get_available_new_symbol_completions(
    uri: str, source: str, state: DocumentState
) -> list[lsp.CompletionItem]:
    items_by_label: dict[str, lsp.CompletionItem] = {}

    if state.build_result is not None:
        for name in state.build_result.state.type_roots:
            items_by_label[name] = lsp.CompletionItem(
                label=name,
                kind=lsp.CompletionItemKind.Class,
                detail="Local type",
            )

    for line in source.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        import_match = re.match(r"^import\s+(.+)$", stripped)
        if import_match:
            for imported_name in import_match.group(1).split(","):
                type_name = imported_name.strip()
                if not type_name:
                    continue
                node_type = _find_stdlib_type(type_name)
                if node_type is not None:
                    items_by_label[type_name] = node_type_to_completion_item(node_type)
            continue

        from_match = re.match(r'^from\s+"([^"]+)"\s+import\s+(.+)$', stripped)
        if from_match:
            import_path, imported_names = from_match.groups()
            target_items = {
                item.label: item
                for item in _get_types_from_import_target(
                    uri, f'from "{import_path}" import {imported_names}'
                )
            }
            for imported_name in imported_names.split(","):
                type_name = imported_name.strip()
                if type_name and type_name in target_items:
                    items_by_label[type_name] = target_items[type_name]

    return sorted(items_by_label.values(), key=lambda item: item.label)


# -----------------------------------------------------------------------------
# LSP Event Handlers
# -----------------------------------------------------------------------------


@LSP_SERVER.feature(lsp.INITIALIZE)
def on_initialize(params: lsp.InitializeParams) -> None:
    """Handle LSP initialization."""
    logger.info(f"LSP server initializing for workspace: {params.root_uri}")
    BROKER.initialize(params.root_uri)


@LSP_SERVER.feature(lsp.TEXT_DOCUMENT_DID_OPEN)
def on_document_did_open(params: lsp.DidOpenTextDocumentParams) -> None:
    """Handle document open - build and publish diagnostics."""
    uri = params.text_document.uri
    text = params.text_document.text

    BROKER.open_or_update(uri, text, params.text_document.version)


@LSP_SERVER.feature(lsp.TEXT_DOCUMENT_DID_CHANGE)
def on_document_did_change(params: lsp.DidChangeTextDocumentParams) -> None:
    """Handle document change - rebuild and publish diagnostics."""
    uri = params.text_document.uri
    document = LSP_SERVER.workspace.get_text_document(uri)

    BROKER.open_or_update(uri, document.source, params.text_document.version)


@LSP_SERVER.feature(lsp.TEXT_DOCUMENT_DID_SAVE)
def on_document_did_save(params: lsp.DidSaveTextDocumentParams) -> None:
    """Handle document save - rebuild and publish diagnostics."""
    uri = params.text_document.uri
    document = LSP_SERVER.workspace.get_text_document(uri)

    broker_doc = BROKER.get_document(uri)
    version = broker_doc.version if broker_doc is not None else 0
    BROKER.open_or_update(uri, document.source, version)


@LSP_SERVER.feature(lsp.TEXT_DOCUMENT_DID_CLOSE)
def on_document_did_close(params: lsp.DidCloseTextDocumentParams) -> None:
    """
    Handle document close - clean up state to free memory.

    Only open/active files are kept in memory. When a file is closed,
    we release its graph and state to keep memory usage low.
    """
    uri = params.text_document.uri

    if uri in DOCUMENT_STATES:
        state = DOCUMENT_STATES[uri]
        state.reset_graph()
        del DOCUMENT_STATES[uri]
        logger.debug(f"Cleaned up state for closed document: {uri}")
    BROKER.close(uri)


@LSP_SERVER.feature(
    lsp.TEXT_DOCUMENT_DIAGNOSTIC,
    lsp.DiagnosticOptions(
        identifier=TOOL_DISPLAY,
        inter_file_dependencies=True,
        workspace_diagnostics=False,
    ),
)
def on_document_diagnostic(
    params: lsp.DocumentDiagnosticParams,
) -> lsp.RelatedFullDocumentDiagnosticReport:
    """Handle diagnostic request."""
    uri = params.text_document.uri
    if _broker_manages_document(uri):
        state = BROKER.compile_document(uri)
    else:
        state = get_document_state(uri)
    return lsp.RelatedFullDocumentDiagnosticReport(
        items=state.diagnostics,
        related_documents=None,
    )


@LSP_SERVER.feature(
    lsp.TEXT_DOCUMENT_COMPLETION,
    lsp.CompletionOptions(
        trigger_characters=[".", " "],
        resolve_provider=False,
    ),
)
def on_document_completion(params: lsp.CompletionParams) -> lsp.CompletionList | None:
    """Handle completion request."""
    if _broker_manages_document(params.text_document.uri):
        return _broker_query(
            params.text_document.uri,
            "completion",
            {
                "line": params.position.line,
                "character": params.position.character,
            },
        )
    return _document_completion_impl(params)


def _document_completion_impl(
    params: lsp.CompletionParams,
) -> lsp.CompletionList | None:
    """Completion implementation that operates on the local in-process state."""
    try:
        uri = params.text_document.uri
        document = LSP_SERVER.workspace.get_text_document(uri)
        lines = document.source.splitlines()

        if params.position.line >= len(lines):
            return None

        line = lines[params.position.line]
        char_before = line[: params.position.character]
        stripped = char_before.rstrip()

        # Dot completion: "resistor." or partial member like "resistor.vo"
        dot_match = re.search(
            r"(?P<field>[A-Za-z0-9_\[\]]+(?:\.[A-Za-z0-9_\[\]]+)*)\.(?P<prefix>[A-Za-z_][A-Za-z0-9_]*)?$",
            char_before,
        )
        if dot_match:
            field_ref = dot_match.group("field")
            member_prefix = dot_match.group("prefix") or ""

            state = get_document_state(uri)
            if state.build_result is None:
                return None

            try:
                completions: list[lsp.CompletionItem] = []
                existing_labels: set[str] = set()

                node = None
                try:
                    node = _resolve_dot_completion_node(state, field_ref)
                except Exception as e:
                    logger.debug(f"Dot completion instance resolution error: {e}")
                if node is not None:
                    for item in get_node_completions(node):
                        if item.label in existing_labels:
                            continue
                        completions.append(item)
                        existing_labels.add(item.label)

                _, tg, _ = state.ensure_graph()
                type_node = _resolve_dot_completion_type_node(state, field_ref)
                if type_node is not None:
                    for item in get_type_node_completions(tg, type_node):
                        if item.label in existing_labels:
                            continue
                        completions.append(item)
                        existing_labels.add(item.label)

                if not completions:
                    return None
                if member_prefix:
                    completions = [
                        item
                        for item in completions
                        if item.label.startswith(member_prefix)
                    ]
                return lsp.CompletionList(is_incomplete=False, items=completions)

            except Exception as e:
                logger.debug(f"Dot completion error: {e}")
                return None

        # "new" keyword completion, including partial symbols like `new Tex`
        elif new_match := re.search(
            r"\bnew(?:\s+([A-Za-z_][A-Za-z0-9_]*))?$", stripped
        ):
            state = get_document_state(uri)
            items = _get_available_new_symbol_completions(uri, document.source, state)
            prefix = new_match.group(1) or ""
            if prefix:
                items = [item for item in items if item.label.startswith(prefix)]
            return lsp.CompletionList(is_incomplete=False, items=items)

        # "import" keyword completion (stdlib)
        elif stripped.endswith("import") or (
            "import " in char_before and stripped.endswith(",")
        ):
            if "from" in char_before:
                items = _get_types_from_import_target(uri, char_before)
                return lsp.CompletionList(is_incomplete=False, items=items)

            items = []
            for node_type in get_stdlib_types():
                # Include all stdlib types that are not internal
                if not node_type.__name__.startswith("_"):
                    items.append(node_type_to_completion_item(node_type))

            return lsp.CompletionList(is_incomplete=False, items=items)

        # "from" keyword completion (file paths)
        elif stripped.endswith("from"):
            paths = get_importable_paths(uri)
            items = [
                lsp.CompletionItem(
                    label=f'"{path.as_posix()}"',
                    kind=lsp.CompletionItemKind.File,
                )
                for path in paths
            ]
            return lsp.CompletionList(is_incomplete=False, items=items)

    except Exception as e:
        logger.exception(f"Completion error: {e}")

    return None


@LSP_SERVER.feature(lsp.TEXT_DOCUMENT_HOVER)
def on_document_hover(params: lsp.HoverParams) -> lsp.Hover | None:
    """Handle hover request."""
    if _broker_manages_document(params.text_document.uri):
        return _broker_query(
            params.text_document.uri,
            "hover",
            {
                "line": params.position.line,
                "character": params.position.character,
            },
        )
    return _document_hover_impl(params)


def _document_hover_impl(params: lsp.HoverParams) -> lsp.Hover | None:
    """Hover implementation that operates on the local in-process state."""
    try:
        uri = params.text_document.uri
        state = get_document_state(uri)
        if _position_is_invalid(state, params.position):
            return None
        document = LSP_SERVER.workspace.get_text_document(uri)

        # First, try to get the word under cursor for type info
        # This handles hovering on type names like "Resistor" in imports/new
        word = _get_word_at_position(document.source, params.position)
        if word:
            hover_text = _get_type_hover_info(word, state)
            if hover_text:
                return lsp.Hover(
                    contents=lsp.MarkupContent(
                        kind=lsp.MarkupKind.Markdown,
                        value=hover_text,
                    ),
                )

        # Fall back to AST-based lookup for other constructs
        if state.ast_nodes:
            line = params.position.line + 1
            col = params.position.character

            node = find_node_at_position(state.ast_nodes, line, col)
            if node is not None:
                # Skip generic File nodes - not useful for hover
                if isinstance(node, AST.File):
                    return None

                hover_text = _build_hover_text(node, state)
                if hover_text:
                    hover_range = None
                    try:
                        source_chunk = node.source.get()
                        loc = source_chunk.loc.get()
                        hover_range = file_location_to_lsp_range(loc)
                    except Exception:
                        pass

                    return lsp.Hover(
                        contents=lsp.MarkupContent(
                            kind=lsp.MarkupKind.Markdown,
                            value=hover_text,
                        ),
                        range=hover_range,
                    )

    except Exception as e:
        logger.debug(f"Hover error: {e}")

    return None


def _build_hover_text(node: fabll.Node, state: DocumentState) -> str | None:
    """Build hover text for a node."""
    try:
        type_name = type(node).__name__

        # Handle different AST node types
        if isinstance(node, AST.BlockDefinition):
            block_type = node.get_block_type()
            name = node.get_type_ref_name()
            super_type = node.get_super_type_ref_name()
            if super_type:
                return f"**{block_type}** `{name}` extends `{super_type}`"
            return f"**{block_type}** `{name}`"

        elif isinstance(node, AST.Assignment):
            target = node.get_target()
            parts = list(target.parts.get().as_list())
            if parts:
                first_part = parts[0].cast(AST.FieldRefPart)
                name = first_part.name.get().get_single()
                return f"**Assignment** to `{name}`"

        elif isinstance(node, AST.ImportStmt):
            imported_type = node.get_type_ref_name()
            path = node.get_path()
            # Get docstring for stdlib imports
            hover_info = _get_type_hover_info(imported_type, state)
            if hover_info:
                return hover_info
            if path:
                return f"**Import** `{imported_type}` from `{path}`"
            return f"**Import** `{imported_type}` (stdlib)"

        elif isinstance(node, AST.NewExpression):
            type_ref = node.get_type_ref_name()
            count = node.get_new_count()
            # Get docstring for the type
            hover_info = _get_type_hover_info(type_ref, state)
            if hover_info:
                if count:
                    return f"**New** `{type_ref}[{count}]`\n\n---\n\n{hover_info}"
                return hover_info
            if count:
                return f"**New** `{type_ref}[{count}]`"
            return f"**New** `{type_ref}`"

        elif isinstance(node, AST.FieldRef):
            parts = list(node.parts.get().as_list())
            path_str = ".".join(
                p.cast(AST.FieldRefPart).name.get().get_single() for p in parts
            )
            return f"**Field reference** `{path_str}`"

        elif isinstance(node, AST.Quantity):
            value = node.get_value()
            unit = node.try_get_unit_symbol()
            if unit:
                return f"**Quantity** `{value}{unit}`"
            return f"**Quantity** `{value}`"

        # Generic fallback
        return f"**{type_name}**"

    except Exception as e:
        logger.debug(f"Hover text error: {e}")
        return None


def _get_type_hover_info(type_name: str, state: DocumentState) -> str | None:
    """Get hover information for a type name (local or stdlib)."""
    # Check for stdlib type first
    stdlib_type = _find_stdlib_type(type_name)
    if stdlib_type is not None:
        return _build_stdlib_type_hover(stdlib_type, state.type_graph)

    # Check for local type
    if state.build_result is not None:
        if type_name in state.build_result.state.type_roots:
            return _build_local_type_hover(type_name, state)

    return None


def _build_local_type_hover(type_name: str, state: DocumentState) -> str:
    """Build hover text for a local .ato type with docstring and members."""
    lines = []
    lines.append(f"**module** `{type_name}`")

    # Try to extract docstring from has_doc_string trait
    doc_string = _get_local_type_docstring(type_name, state)
    if doc_string:
        lines.append(f"\n---\n\n{doc_string}")

    # Try to get member info from the type
    members = _get_local_type_members(type_name, state)
    if members:
        lines.append("\n---\n\n**Members:**")
        for member_name, member_type in members[:10]:
            lines.append(f"\n- `{member_name}`: {member_type}")
        if len(members) > 10:
            lines.append(f"\n- *... and {len(members) - 10} more*")

    return "\n".join(lines)


def _get_local_type_docstring(type_name: str, state: DocumentState) -> str | None:
    """Extract docstring from a local type's has_doc_string trait."""
    if state.type_graph is None or state.graph_view is None:
        return None

    if state.build_result is None:
        return None

    try:
        tg = state.type_graph

        # Get the type root for this type name
        type_root = state.build_result.state.type_roots.get(type_name)
        if type_root is None:
            return None

        # Get the has_doc_string trait type in this typegraph
        from faebryk.core.node import TypeNodeBoundTG

        trait_type = TypeNodeBoundTG.get_or_create_type_in_tg(tg=tg, t=F.has_doc_string)

        # Look up the trait directly on the type_root
        impl = fbrk.Trait.try_get_trait(
            target=type_root,
            trait_type=trait_type,
        )

        if impl is not None:
            # Wrap it in has_doc_string and extract the doc_string
            doc_trait = F.has_doc_string(impl)
            # Dedent to remove leading whitespace from multiline strings
            return textwrap.dedent(doc_trait.doc_string).strip()

    except Exception as e:
        logger.debug(f"Error getting local type docstring: {e}")

    return None


def _get_local_type_members(
    type_name: str, state: DocumentState
) -> list[tuple[str, str]]:
    """Get member list for a local type from the AST."""
    members: list[tuple[str, str]] = []

    if state.type_graph is None or state.graph_view is None:
        return members

    if state.build_result is None:
        return members

    try:
        g = state.graph_view
        tg = state.type_graph

        # Check if this type exists
        if type_name not in state.build_result.state.type_roots:
            return members

        # Look through assignments in the type to find members
        assignment_type = AST.Assignment.bind_typegraph(tg)
        for inst in assignment_type.get_instances(g):
            try:
                assignment = inst.cast(AST.Assignment)

                # Check if this assignment belongs to our type by checking parent scope
                # This is a simplification - we check if the assignment's scope matches
                parent = assignment.source.get()
                scope = parent.scope.get()

                # Get the block name from scope if available
                if hasattr(scope, "name"):
                    scope_name = scope.name.get().get_single()
                    if scope_name == type_name:
                        # Get target name
                        target = assignment.target.get().deref()
                        target_fr = target.cast(AST.FieldRef)
                        parts = list(target_fr.parts.get().as_list())
                        if parts:
                            first_part = parts[0].cast(AST.FieldRefPart)
                            member_name = first_part.name.get().get_single()

                            # Get value type info
                            value = assignment.value.get().deref()
                            if hasattr(value, "__class__"):
                                if value.__class__.__name__ == "NewExpression":
                                    new_expr = value.cast(AST.NewExpression)
                                    type_ref_name = new_expr.get_type_ref_name()
                                    members.append((member_name, type_ref_name))
                                else:
                                    members.append((member_name, "field"))
            except Exception:
                continue

    except Exception as e:
        logger.debug(f"Error getting local type members: {e}")

    return members


def _build_stdlib_type_hover(
    type_obj: type[fabll.Node], type_graph: fbrk.TypeGraph | None = None
) -> str:
    """Build hover text for a stdlib type with docstring, members, and usage example."""
    lines = []

    # Type header
    type_name = type_obj.__name__

    # Check if it's a module or interface
    if hasattr(type_obj, "_is_module"):
        lines.append(f"**module** `{type_name}`")
    elif hasattr(type_obj, "_is_interface"):
        lines.append(f"**interface** `{type_name}`")
    else:
        lines.append(f"**class** `{type_name}`")

    # Add base class info
    bases = [b.__name__ for b in type_obj.__bases__ if b.__name__ != "Node"]
    if bases:
        lines.append(f"\n*extends* `{', '.join(bases)}`")

    # Add docstring if available
    if type_obj.__doc__:
        docstring = type_obj.__doc__.strip()
        lines.append(f"\n---\n\n{docstring}")

    # Add member info (parameters, interfaces, etc.)
    members = _get_type_members(type_obj)
    if members:
        lines.append("\n---\n\n**Members:**")
        for member_name, member_type in members[:10]:  # Limit to 10
            lines.append(f"\n- `{member_name}`: {member_type}")
        if len(members) > 10:
            lines.append(f"\n- *... and {len(members) - 10} more*")

    # Add usage example if available (requires a TypeGraph to extract the trait)
    usage_example = _get_type_usage_example(type_obj, type_graph)
    if usage_example:
        example_text, language = usage_example
        lines.append("\n---\n\n**Usage Example:**")
        # Use proper code fence with language identifier
        lang_tag = language if language else "ato"
        lines.append(f"\n```{lang_tag}\n{example_text.strip()}\n```")

    return "\n".join(lines)


def _get_type_usage_example(
    type_obj: type[fabll.Node], type_graph: fbrk.TypeGraph | None
) -> tuple[str, str] | None:
    """Extract usage example from a type's has_usage_example trait.

    Returns (example_text, language) tuple or None if no example available.
    """
    if type_graph is None:
        return None

    try:
        # Bind the type to the typegraph to access type traits
        type_bound = type_obj.bind_typegraph(type_graph)

        # Try to get the has_usage_example trait
        usage_trait = type_bound.try_get_type_trait(F.has_usage_example)
        if usage_trait is not None:
            # Dedent the example to remove leading whitespace from multiline strings
            example = textwrap.dedent(usage_trait.example).strip()
            language = usage_trait.language
            return (example, language)
    except Exception as e:
        logger.debug(f"Failed to get usage example for {type_obj.__name__}: {e}")

    return None


def _get_type_members(type_obj: type[fabll.Node]) -> list[tuple[str, str]]:
    """Get the members (children) defined on a type."""
    members = []

    # Traits and internal types to filter out (not useful as "members")
    trait_types = {
        "has_designator_prefix",
        "is_lead",
        "has_usage_example",
        "can_attach_to_footprint",
        "can_bridge",
        "is_pickable_by_type",
        "has_simple_value_representation",
        "can_attach_to_any_pad",
    }

    # Single-letter names are usually loop variable leakage
    #  (e.g., `e` in `for e in unnamed:`)
    def is_loop_variable(name: str) -> bool:
        return len(name) == 1 and name.islower()

    # Look for class attributes that define children (_ChildField) or lists of them
    for attr_name in dir(type_obj):
        if attr_name.startswith("_"):
            continue

        # Skip loop variable leakage
        if is_loop_variable(attr_name):
            continue

        try:
            attr = getattr(type_obj, attr_name)
            attr_type_name = type(attr).__name__

            # Handle lists of _ChildFields (like unnamed = [Electrical.MakeChild()
            #  for _ in range(2)])
            if isinstance(attr, list) and attr:
                first_item = attr[0]
                if type(first_item).__name__ == "_ChildField":
                    if hasattr(first_item, "nodetype") and first_item.nodetype:
                        item_type = first_item.nodetype.__name__
                        # Skip trait types
                        if item_type in trait_types:
                            continue
                        members.append((attr_name, f"{item_type}[{len(attr)}]"))
                    continue

            # Check if it's a child field definition
            if attr_type_name == "_ChildField":
                if hasattr(attr, "nodetype") and attr.nodetype is not None:
                    node_type = attr.nodetype
                    type_name = node_type.__name__

                    # Skip trait types
                    if type_name in trait_types:
                        continue

                    # For parameters, try to get the unit from dependants
                    if type_name in ("NumericParameter", "EnumParameter"):
                        unit = _get_parameter_unit(attr)
                        if unit:
                            members.append((attr_name, f"{type_name}({unit})"))
                        else:
                            members.append((attr_name, type_name))
                    else:
                        members.append((attr_name, type_name))

        except Exception:
            continue

    # Sort to put common ones first (resistance, capacitance, etc)
    priority_names = [
        "resistance",
        "capacitance",
        "inductance",
        "voltage",
        "current",
        "power",
        "frequency",
        "unnamed",
        "hv",
        "lv",
        "line",
        "reference",
    ]

    def sort_key(item: tuple[str, str]) -> tuple[int, str]:
        name = item[0]
        for i, pname in enumerate(priority_names):
            if pname in name.lower():
                return (i, name)
        return (len(priority_names), name)

    members.sort(key=sort_key)
    return members


def _get_parameter_unit(child_field: Any) -> str | None:
    """Extract unit from a NumericParameter's dependants."""
    try:
        if not hasattr(child_field, "_dependants"):
            return None

        for dep in child_field._dependants:
            if not hasattr(dep, "nodetype") or dep.nodetype is None:
                continue
            dep_name = dep.nodetype.__name__
            # Units are typically named like "Farad", "Volt", "Ohm", "Ampere", etc.
            # Skip "has_unit" trait itself
            if dep_name == "has_unit":
                continue
            # Check if it's a unit (in F.Units)
            if hasattr(F.Units, dep_name):
                return dep_name
    except Exception:
        pass
    return None


@LSP_SERVER.feature(lsp.TEXT_DOCUMENT_DEFINITION)
def on_document_definition(
    params: lsp.DefinitionParams,
) -> lsp.Location | list[lsp.LocationLink] | None:
    """Handle go-to-definition request."""
    if _broker_manages_document(params.text_document.uri):
        return _broker_query(
            params.text_document.uri,
            "definition",
            {
                "line": params.position.line,
                "character": params.position.character,
            },
        )
    return _document_definition_impl(params)


def _document_definition_impl(
    params: lsp.DefinitionParams,
) -> lsp.Location | list[lsp.LocationLink] | None:
    """Definition implementation that operates on the local in-process state."""
    try:
        uri = params.text_document.uri
        state = get_document_state(uri)
        if _position_is_invalid(state, params.position):
            return None

        # Try AST-based lookup first
        if state.ast_nodes:
            # Convert to 1-indexed line for AST lookup
            line = params.position.line + 1
            col = params.position.character

            node = find_node_at_position(state.ast_nodes, line, col)
            if node is not None:
                target_location = _find_definition_location(node, state, uri)
                if target_location:
                    return target_location

        # Fallback: Extract field reference or word under cursor
        document = LSP_SERVER.workspace.get_text_document(uri)

        # Try field reference first (for instances like ldo_3V3 or some_obj.member)
        field_ref_info = _get_field_reference_at_position(
            document.source, params.position
        )
        if field_ref_info:
            full_path, clicked_word = field_ref_info

            # Check if we clicked on a child member (not the root)
            # e.g., clicking on "some_power" in "some_other_module.some_power"
            first_part = full_path.split(".")[0].split("[")[0]
            clicked_word_stripped = _strip_array_indices(clicked_word)

            if clicked_word_stripped != first_part:
                # Clicked on a child member - try to find its definition
                child_location = _find_child_member_definition(
                    full_path, clicked_word_stripped, state, uri
                )
                if child_location:
                    return child_location

            # Try instance definition (for root instances like ldo_3V3)
            instance_location = _find_instance_definition(full_path, state, uri)
            if instance_location:
                return instance_location

        # Fallback to type definition lookup
        word = _get_word_at_position(document.source, params.position)
        if word:
            location = _find_type_definition(word, state, uri)
            if location:
                return location

    except Exception as e:
        logger.debug(f"Definition error: {e}")

    return None


@LSP_SERVER.feature(lsp.TEXT_DOCUMENT_TYPE_DEFINITION)
def on_document_type_definition(
    params: lsp.TypeDefinitionParams,
) -> lsp.Location | list[lsp.LocationLink] | None:
    """
    Handle go-to-type-definition request.

    This takes you from an instance (e.g., ldo_3V3) to its type definition
    (e.g., TLV75901_driver module definition).
    """
    if _broker_manages_document(params.text_document.uri):
        return _broker_query(
            params.text_document.uri,
            "type_definition",
            {
                "line": params.position.line,
                "character": params.position.character,
            },
        )
    return _document_type_definition_impl(params)


def _document_type_definition_impl(
    params: lsp.TypeDefinitionParams,
) -> lsp.Location | list[lsp.LocationLink] | None:
    """Type-definition implementation that operates on the local in-process state."""
    try:
        uri = params.text_document.uri
        state = get_document_state(uri)
        if _position_is_invalid(state, params.position):
            return None

        # Get the field reference at the cursor position
        document = LSP_SERVER.workspace.get_text_document(uri)
        field_ref_info = _get_field_reference_at_position(
            document.source, params.position
        )

        if field_ref_info:
            full_path, _clicked_word = field_ref_info
            # Find the type of this instance
            type_location = _find_instance_type_definition(full_path, state, uri)
            if type_location:
                return type_location

        # Fallback: Try word-based lookup for type names
        word = _get_word_at_position(document.source, params.position)
        if word:
            location = _find_type_definition(word, state, uri)
            if location:
                return location

    except Exception as e:
        logger.debug(f"Type definition error: {e}")

    return None


def _get_instance_type_name(field_path: str, state: DocumentState) -> str | None:
    """
    Get the type name of an instance from its field path.

    For example, if `ldo_3V3 = new TLV75901_driver`, returns "TLV75901_driver".
    """
    if state.type_graph is None or state.graph_view is None:
        logger.debug(
            "Instance type lookup early return: tg=%s g=%s",
            state.type_graph is not None,
            state.graph_view is not None,
        )
        return None

    try:
        g = state.graph_view
        tg = state.type_graph

        # Strip array indices for lookup (e.g., "gpios[0]" -> "gpios")
        stripped_path = _strip_array_indices(field_path)
        paths_to_try = [field_path]
        if stripped_path != field_path:
            paths_to_try.append(stripped_path)

        # Search through all Assignment nodes to find the one that defines this field
        assignment_type = AST.Assignment.bind_typegraph(tg)
        for search_path in paths_to_try:
            for inst in assignment_type.get_instances(g):
                try:
                    assignment = inst.cast(AST.Assignment)

                    # Get the target field ref from the assignment
                    target = assignment.target.get().deref()
                    target_fr = target.cast(AST.FieldRef)

                    # Build the target path
                    parts = list(target_fr.parts.get().as_list())
                    target_parts = []
                    for p in parts:
                        part = p.cast(AST.FieldRefPart)
                        name = part.name.get().get_single()
                        target_parts.append(name)
                    target_path = ".".join(target_parts)

                    # Check if this assignment defines the field we're looking for
                    if target_path == search_path:
                        # Get the assignable value and cast to NewExpression
                        assignable = assignment.assignable.get()
                        value_node = assignable.value.get().deref()

                        # Try to cast to NewExpression
                        try:
                            new_expr = value_node.cast(AST.NewExpression)
                            return new_expr.get_type_ref_name()
                        except Exception:
                            pass

                except Exception as e:
                    logger.debug("Error checking assignment for type: %s", e)
                    continue

    except Exception as e:
        logger.debug(f"Instance type lookup error: {e}")

    try:
        resolved_type = _resolve_dot_completion_type_node(state, stripped_path)
        if resolved_type is not None:
            if state.build_result is not None:
                for (
                    local_name,
                    local_type,
                ) in state.build_result.state.type_roots.items():
                    try:
                        if resolved_type.node().is_same(other=local_type.node()):
                            return local_name
                    except Exception:
                        continue
            return fbrk.TypeGraph.get_type_name(type_node=resolved_type)
    except Exception as e:
        logger.debug("Recursive instance type lookup error: %s", e)

    return None


def _find_instance_type_definition(
    field_path: str, state: DocumentState, uri: str
) -> lsp.Location | None:
    """
    Find the type definition of an instance.

    For example, if `ldo_3V3 = new TLV75901_driver`, clicking on `ldo_3V3`
    and selecting "Go to Type Definition" should take you to where
    TLV75901_driver is defined.
    """
    type_name = _get_instance_type_name(field_path, state)
    if type_name:
        return _find_type_definition(type_name, state, uri)
    return None


def _find_member_in_typegraph(
    member_name: str,
    type_node: graph.BoundNode,
    state: DocumentState,
    fallback_uri: str,
) -> lsp.Location | None:
    from atopile.compiler.ast_visitor import ASTVisitor

    if state.type_graph is None:
        return None

    try:
        for identifier, make_child in state.type_graph.collect_make_children(
            type_node=type_node
        ):
            base_name = identifier.split("[", 1)[0] if identifier else identifier
            if identifier != member_name and base_name != member_name:
                continue

            source_chunk = ASTVisitor.get_source_chunk(make_child)
            if source_chunk is None:
                continue

            loc = source_chunk.loc.get()
            target_uri = fallback_uri
            if source_path := source_chunk.get_path():
                target_uri = Path(source_path).resolve().as_uri()

            start_char = loc.get_start_col()
            end_char = loc.get_end_col()
            found_at = source_chunk.get_text().find(member_name)
            if found_at >= 0:
                start_char += found_at
                end_char = start_char + len(member_name)

            return lsp.Location(
                uri=target_uri,
                range=lsp.Range(
                    start=lsp.Position(
                        line=loc.get_start_line() - 1,
                        character=start_char,
                    ),
                    end=lsp.Position(
                        line=loc.get_end_line() - 1,
                        character=end_char,
                    ),
                ),
            )
    except Exception as e:
        logger.debug("Error finding member in typegraph: %s", e)

    return None


def _find_child_member_definition(
    full_path: str, clicked_member: str, state: DocumentState, uri: str
) -> lsp.Location | None:
    """
    Find the definition of a child member within a parent type.

    For example, clicking on `.some_power` in `some_other_module.some_power`:
    - full_path = "some_other_module.some_power"
    - clicked_member = "some_power"
    This will find where `some_power` is defined within `OtherModule`.
    """
    # Split the path to get the parent
    parts = full_path.split(".")

    # Find the index of the clicked member in the path
    # It should be the last part that matches clicked_member
    member_index = -1
    for i in range(len(parts) - 1, -1, -1):
        # Strip array indices for comparison
        part_name = _strip_array_indices(parts[i])
        if part_name == clicked_member:
            member_index = i
            break

    if member_index <= 0:
        # Clicked on the root - no parent to look into
        return None

    # Get the parent path (everything before the clicked member)
    parent_path = ".".join(parts[:member_index])

    if (
        parent_type_node := _resolve_dot_completion_type_node(state, parent_path)
    ) is not None:
        if location := _find_member_in_typegraph(
            clicked_member, parent_type_node, state, uri
        ):
            return location

    # Find the type of the parent
    parent_type_name = _get_instance_type_name(parent_path, state)
    if not parent_type_name:
        return None

    logger.debug(f"Looking for '{clicked_member}' in type '{parent_type_name}'")

    # Now find where clicked_member is defined within parent_type_name
    # This could be in a local type definition or in a stdlib/external type

    # Check stdlib types
    stdlib_type = _find_stdlib_type(parent_type_name)
    if stdlib_type is not None:
        # For stdlib types, we can try to find the member in the source
        return _find_member_in_stdlib_type(clicked_member, stdlib_type)

    # Check external types
    return _find_member_in_external_type(clicked_member, parent_type_name, state, uri)


def _find_member_in_stdlib_type(
    member_name: str, stdlib_type: type
) -> lsp.Location | None:
    """Find a member definition within a stdlib type."""
    try:
        # Get the source file for the stdlib type
        source_file = inspect.getfile(stdlib_type)

        # Read the source and find the member
        source = Path(source_file).read_text()
        lines = source.splitlines()

        # Look for patterns like:
        # - "member_name = " (assignment)
        # - "member_name:" (declaration)
        for i, line in enumerate(lines):
            stripped = line.strip()
            if (
                stripped.startswith(f"{member_name} =")
                or stripped.startswith(f"{member_name}=")
                or stripped.startswith(f"{member_name}:")
            ):
                col = line.index(member_name)
                return lsp.Location(
                    uri=f"file://{source_file}",
                    range=lsp.Range(
                        start=lsp.Position(line=i, character=col),
                        end=lsp.Position(line=i, character=col + len(member_name)),
                    ),
                )

    except Exception as e:
        logger.debug(f"Error finding member in stdlib type: {e}")

    return None


def _find_member_in_external_type(
    member_name: str, type_name: str, state: DocumentState, uri: str
) -> lsp.Location | None:
    """Find a member definition within an externally imported type."""
    if state.build_result is None:
        return None

    try:
        # First find the external type's file
        # Format: (type_ref, import_ref, node, traceback_stack)
        external_refs = state.build_result.state.external_type_refs
        for _type_ref, import_ref, _node, _traceback in external_refs:
            if import_ref.name != type_name:
                continue

            if not hasattr(import_ref, "path") or not import_ref.path:
                continue

            # Resolve the path
            current_file_path = get_file_path(uri)
            current_dir = current_file_path.parent
            project_dir = _infer_lsp_project_root(current_file_path)

            target_file = None

            # Try relative to current file
            relative_to_current = current_dir / import_ref.path
            if relative_to_current.exists():
                target_file = relative_to_current

            # Try .ato/modules/
            if target_file is None and project_dir:
                modules_path = project_dir / ".ato" / "modules" / import_ref.path
                if modules_path.exists():
                    target_file = modules_path

            if target_file is None:
                continue

            # Read the file and find the member within the type
            source = target_file.read_text()
            lines = source.splitlines()

            # First find the type definition
            in_type_block = False
            indent_level = 0
            type_pattern = re.compile(
                rf"^\s*(module|interface|component)\s+{re.escape(type_name)}\s*"
            )

            for i, line in enumerate(lines):
                if type_pattern.match(line):
                    in_type_block = True
                    # Determine the indent level of the block content
                    indent_level = (
                        len(line) - len(line.lstrip()) + 4
                    )  # Assume 4-space indent
                    continue

                if in_type_block:
                    # Check if we've exited the block
                    if line.strip() and not line.startswith(" " * indent_level):
                        # Check if it's a new top-level definition
                        if re.match(r"^\s*(module|interface|component)\s+", line):
                            break

                    # Look for the member definition
                    stripped = line.strip()
                    if (
                        stripped.startswith(f"{member_name} =")
                        or stripped.startswith(f"{member_name}=")
                        or stripped.startswith(f"{member_name}:")
                    ):
                        col = line.index(member_name)
                        return lsp.Location(
                            uri=f"file://{target_file.resolve()}",
                            range=lsp.Range(
                                start=lsp.Position(line=i, character=col),
                                end=lsp.Position(
                                    line=i, character=col + len(member_name)
                                ),
                            ),
                        )

    except Exception as e:
        logger.debug(f"Error finding member in external type: {e}")

    return None


def _get_word_at_position(source: str, position: lsp.Position) -> str | None:
    """Extract the word (identifier) at the given position."""
    lines = source.splitlines()
    if position.line >= len(lines):
        return None

    line = lines[position.line]
    col = position.character

    # Find word boundaries
    start = col
    end = col

    # Expand to the left
    while start > 0 and (line[start - 1].isalnum() or line[start - 1] == "_"):
        start -= 1

    # Expand to the right
    while end < len(line) and (line[end].isalnum() or line[end] == "_"):
        end += 1

    if start == end:
        return None

    return line[start:end]


def _get_field_reference_at_position(
    source: str, position: lsp.Position
) -> tuple[str, str] | None:
    """
    Extract the full field reference path at the given position.

    For `usb_c.usb.usb_if.buspower`, clicking on different parts returns:
    - Clicking on `usb_c` -> ("usb_c", "usb_c")  # (full_path, clicked_part)
    - Clicking on `usb` -> ("usb_c.usb", "usb")
    - Clicking on `usb_if` -> ("usb_c.usb.usb_if", "usb_if")
    - Clicking on `buspower` -> ("usb_c.usb.usb_if.buspower", "buspower")

    Returns (full_path_to_cursor, word_at_cursor) or None.
    """
    lines = source.splitlines()
    if position.line >= len(lines):
        return None

    line = lines[position.line]
    col = position.character

    if col >= len(line):
        col = len(line) - 1 if line else 0

    if not line:
        return None

    # First, find the simple word at cursor (without dots)
    word_start = col
    word_end = col

    # Expand word to the left (just alphanumeric + underscore)
    while word_start > 0 and (
        line[word_start - 1].isalnum() or line[word_start - 1] == "_"
    ):
        word_start -= 1

    # Expand word to the right (including array indices)
    while word_end < len(line) and (line[word_end].isalnum() or line[word_end] == "_"):
        word_end += 1

    # Also include array index immediately after the word (e.g., gpios[0])
    if word_end < len(line) and line[word_end] == "[":
        bracket_end = word_end + 1
        while bracket_end < len(line) and line[bracket_end] != "]":
            bracket_end += 1
        if bracket_end < len(line):
            word_end = bracket_end + 1  # Include the closing ]

    if word_start == word_end:
        return None

    word_at_cursor = line[word_start:word_end]

    # Now expand to get the full field reference path (including dots and brackets)
    # Go left from word_start to find the start of the field reference
    path_start = word_start
    while path_start > 0:
        prev_char = line[path_start - 1]
        if prev_char == ".":
            # Include the dot and continue looking for identifier
            path_start -= 1
            # Now look for identifier before the dot
            while path_start > 0 and (
                line[path_start - 1].isalnum()
                or line[path_start - 1] == "_"
                or line[path_start - 1] == "]"
            ):
                if line[path_start - 1] == "]":
                    # Handle array indexing like [0]
                    path_start -= 1
                    while path_start > 0 and line[path_start - 1] != "[":
                        path_start -= 1
                    if path_start > 0:
                        path_start -= 1  # Include the [
                else:
                    path_start -= 1
        elif prev_char == "]":
            # Handle array indexing
            path_start -= 1
            while path_start > 0 and line[path_start - 1] != "[":
                path_start -= 1
            if path_start > 0:
                path_start -= 1
        else:
            break

    # The full path up to and including the cursor position
    full_path_to_cursor = line[path_start:word_end]

    return (full_path_to_cursor, word_at_cursor)


def _find_definition_location(
    node: fabll.Node, state: DocumentState, uri: str
) -> lsp.Location | None:
    """Find the definition location for a node."""
    try:
        # Handle type references (new expressions, imports)
        if isinstance(node, AST.NewExpression):
            type_ref = node.get_type_ref_name()
            return _find_type_definition(type_ref, state, uri)

        elif isinstance(node, AST.TypeRef):
            name = node.name.get().get_single()
            return _find_type_definition(name, state, uri)

        elif isinstance(node, AST.ImportStmt):
            imported_type = node.get_type_ref_name()
            import_path = node.get_path()

            if import_path:
                target_file = _resolve_import_target_path(uri, import_path)
                if target_file is not None:
                    return _find_type_in_ato_file(imported_type, target_file)

            return _find_type_definition(imported_type, state, uri)

        elif isinstance(node, AST.FieldRef):
            # Get the field path being clicked on
            parts = list(node.parts.get().as_list())
            if not parts:
                return None

            # Build the field path string (e.g., "ldo_3V3" or "usb_c.usb")
            path_parts = []
            for p in parts:
                part = p.cast(AST.FieldRefPart)
                name = part.name.get().get_single()
                path_parts.append(name)
            field_path = ".".join(path_parts)

            # First, try to find the instance definition (assignment statement)
            instance_location = _find_instance_definition(field_path, state, uri)
            if instance_location is not None:
                return instance_location

            # Fallback: try to find a type definition with this name
            first_part = path_parts[0]
            return _find_type_definition(first_part, state, uri)

    except Exception as e:
        logger.debug(f"Definition location error: {e}")

    return None


def _strip_array_indices(field_path: str) -> str:
    """
    Strip array indices from a field path.

    Examples:
    - "gpios[0]" -> "gpios"
    - "gpios[0].line" -> "gpios.line"
    - "micro.gpios[5]" -> "micro.gpios"
    """
    return re.sub(r"\[\d+\]", "", field_path)


def _find_instance_definition(
    field_path: str, state: DocumentState, uri: str
) -> lsp.Location | None:
    """
    Find where an instance (field) is defined via assignment.

    For example:
    - clicking on `ldo_3V3` takes you to `ldo_3V3 = new ...`
    - clicking on `gpios[0]` takes you to `gpios = new ElectricLogic[49]`
    """
    if state.type_graph is None or state.graph_view is None:
        return None

    try:
        g = state.graph_view
        tg = state.type_graph

        # Paths to search for: exact match first, then stripped version
        paths_to_try = [field_path]
        stripped_path = _strip_array_indices(field_path)
        if stripped_path != field_path:
            paths_to_try.append(stripped_path)

        # Search through all Assignment nodes in the AST
        assignment_type = AST.Assignment.bind_typegraph(tg)
        for search_path in paths_to_try:
            for inst in assignment_type.get_instances(g):
                try:
                    assignment = inst.cast(AST.Assignment)

                    # Get the target field ref from the assignment
                    target = assignment.target.get().deref()
                    target_fr = target.cast(AST.FieldRef)

                    # Build the target path
                    parts = list(target_fr.parts.get().as_list())
                    target_parts = []
                    for p in parts:
                        part = p.cast(AST.FieldRefPart)
                        name = part.name.get().get_single()
                        target_parts.append(name)
                    target_path = ".".join(target_parts)

                    # Check if this assignment defines the field we're looking for
                    if target_path == search_path:
                        loc = assignment.source.get().loc.get()
                        return lsp.Location(
                            uri=uri,
                            range=lsp.Range(
                                start=lsp.Position(
                                    line=loc.get_start_line() - 1,
                                    character=loc.get_start_col(),
                                ),
                                end=lsp.Position(
                                    line=loc.get_end_line() - 1,
                                    character=loc.get_end_col(),
                                ),
                            ),
                        )
                except Exception as e:
                    logger.debug(f"Error checking assignment: {e}")
                    continue

    except Exception as e:
        logger.debug(f"Instance definition search error: {e}")

    return None


def _find_type_definition(
    type_name: str, state: DocumentState, uri: str
) -> lsp.Location | None:
    """Find where a type is defined."""
    # Check for local definitions first
    if state.build_result is not None:
        type_roots = state.build_result.state.type_roots

        if type_name in type_roots:
            # Find the AST node for this type definition
            for node_loc in state.ast_nodes:
                if isinstance(node_loc.node, AST.BlockDefinition):
                    try:
                        if node_loc.node.get_type_ref_name() == type_name:
                            return lsp.Location(
                                uri=uri,
                                range=lsp.Range(
                                    start=lsp.Position(
                                        line=node_loc.start_line - 1,
                                        character=node_loc.start_col,
                                    ),
                                    end=lsp.Position(
                                        line=node_loc.end_line - 1,
                                        character=node_loc.end_col,
                                    ),
                                ),
                            )
                    except Exception:
                        continue

    with suppress(Exception):
        document = LSP_SERVER.workspace.get_text_document(uri)
        imported_location = _find_imported_type_definition_from_source(
            type_name, document.source, uri
        )
        if imported_location is not None:
            return imported_location

    # Check for imported types from external .ato files
    external_location = _find_external_type_definition(type_name, state, uri)
    if external_location is not None:
        return external_location

    # Check for stdlib types (like Resistor, Capacitor, etc.)
    stdlib_type = _find_stdlib_type(type_name)
    if stdlib_type is not None:
        return _get_type_source_location(stdlib_type)

    return None


def _find_external_type_definition(
    type_name: str, state: DocumentState, uri: str
) -> lsp.Location | None:
    """Find where an imported type is defined in external .ato files."""
    if state.build_result is None:
        return None

    # Look through external_type_refs for the import
    # Format: (type_ref, import_ref, node, traceback_stack)
    external_refs = state.build_result.state.external_type_refs
    for _type_ref, import_ref, _node, _traceback in external_refs:
        # import_ref can be None for local type references
        if import_ref is None or import_ref.name != type_name:
            continue

        # Check if it has a path (non-stdlib imports)
        if not hasattr(import_ref, "path") or not import_ref.path:
            continue

        # Get the current file's directory and project directory
        current_file_path = get_file_path(uri)
        current_dir = current_file_path.parent
        project_dir = _infer_lsp_project_root(current_file_path)

        # Try multiple resolution strategies in order of priority:

        # 1. Relative to current file's directory (for local imports like "parts/...")
        relative_to_current = current_dir / import_ref.path
        if relative_to_current.exists():
            return _find_type_in_ato_file(type_name, relative_to_current)

        if project_dir is not None:
            # 2. Check in .ato/modules (for package imports)
            ato_modules_path = project_dir / ".ato" / "modules" / import_ref.path
            if ato_modules_path.exists():
                return _find_type_in_ato_file(type_name, ato_modules_path)

            # 3. Relative to project directory (for project-level imports)
            relative_to_project = project_dir / import_ref.path
            if relative_to_project.exists():
                return _find_type_in_ato_file(type_name, relative_to_project)

    return None


def _find_imported_type_definition_from_source(
    type_name: str, source: str, uri: str
) -> lsp.Location | None:
    """Find a path-imported type definition directly from document source."""
    type_pattern = re.compile(r"\b" + re.escape(type_name) + r"\b")

    for line in source.splitlines():
        match = re.match(
            r'\s*from\s+"(?P<path>[^"]+)"\s+import\s+(?P<names>.+)\s*$', line
        )
        if not match:
            continue

        names = [name.strip() for name in match.group("names").split(",")]
        if type_name not in names:
            continue

        target_file = _resolve_import_target_path(uri, match.group("path"))
        if target_file is None:
            continue

        location = _find_type_in_ato_file(type_name, target_file)
        if location is not None:
            return location

        # Support imports that may contain aliases or stray whitespace by checking the
        # original names segment for the exact symbol token before giving up.
        if type_pattern.search(match.group("names")):
            return _find_type_in_ato_file(type_name, target_file)

    return None


def _find_type_in_ato_file(type_name: str, file_path: Path) -> lsp.Location | None:
    """Find a type definition in an .ato file by searching for the block definition."""
    try:
        content = file_path.read_text()
        lines = content.splitlines()

        # Look for 'module TypeName:', 'interface TypeName:', or 'component TypeName:'
        # Also handle 'module TypeName from BaseType:'
        escaped_name = re.escape(type_name)
        pattern = rf"^(module|interface|component)\s+{escaped_name}(\s+from\s+\w+)?\s*:"

        for line_num, line in enumerate(lines):
            stripped = line.lstrip()
            if re.match(pattern, stripped):
                # Find where the type name starts in the line
                name_match = re.search(rf"\b{re.escape(type_name)}\b", line)
                if name_match:
                    return lsp.Location(
                        uri=f"file://{file_path.resolve()}",
                        range=lsp.Range(
                            start=lsp.Position(
                                line=line_num, character=name_match.start()
                            ),
                            end=lsp.Position(line=line_num, character=name_match.end()),
                        ),
                    )

    except Exception as e:
        logger.debug(f"Error finding type in {file_path}: {e}")

    return None


def _find_stdlib_type(type_name: str) -> type[fabll.Node] | None:
    """Find a stdlib type by name."""
    if hasattr(F, type_name):
        type_obj = getattr(F, type_name)
        if isinstance(type_obj, type) and issubclass(type_obj, fabll.Node):
            return type_obj
    return None


def _get_type_source_location(type_obj: type) -> lsp.Location | None:
    """Get the source file location for a Python type."""
    import inspect

    try:
        source_file = inspect.getfile(type_obj)
        source_lines, start_line = inspect.getsourcelines(type_obj)

        # Convert file path to URI
        file_uri = uris.from_fs_path(source_file)
        if file_uri is None:
            return None

        return lsp.Location(
            uri=file_uri,
            range=lsp.Range(
                start=lsp.Position(line=start_line - 1, character=0),
                end=lsp.Position(line=start_line - 1 + len(source_lines), character=0),
            ),
        )
    except (TypeError, OSError) as e:
        logger.debug(f"Could not get source location for {type_obj}: {e}")
        return None


# -----------------------------------------------------------------------------
# Code Actions (Auto-import)
# -----------------------------------------------------------------------------


@LSP_SERVER.feature(
    lsp.TEXT_DOCUMENT_CODE_ACTION,
    lsp.CodeActionOptions(
        code_action_kinds=[lsp.CodeActionKind.QuickFix, lsp.CodeActionKind.Source],
    ),
)
def on_code_action(
    params: lsp.CodeActionParams,
) -> list[lsp.CodeAction] | None:
    """Handle code action request (e.g., auto-import, open datasheet)."""
    if _broker_manages_document(params.text_document.uri):
        return _broker_query(
            params.text_document.uri,
            "code_action",
            {
                "start_line": params.range.start.line,
                "start_character": params.range.start.character,
                "end_line": params.range.end.line,
                "end_character": params.range.end.character,
                "diagnostics": params.context.diagnostics,
            },
        )
    return _code_action_impl(params)


def _code_action_impl(params: lsp.CodeActionParams) -> list[lsp.CodeAction] | None:
    """Code-action implementation that operates on the local in-process state."""
    try:
        uri = params.text_document.uri
        document = LSP_SERVER.workspace.get_text_document(uri)

        actions: list[lsp.CodeAction] = []

        # Get the word at the cursor position (or selection start)
        position = params.range.start
        word = _get_word_at_position(document.source, position)

        if word:
            # Check if this word is a stdlib type that's not imported
            action = _create_auto_import_action(word, uri, document.source)
            if action:
                actions.append(action)

        # Also check diagnostics for undefined type errors
        for diagnostic in params.context.diagnostics:
            msg_lower = diagnostic.message.lower()
            if "undefined" in msg_lower or "unknown" in msg_lower:
                # Try to extract the type name from the diagnostic
                import_action = _create_import_from_diagnostic(
                    diagnostic, uri, document.source
                )
                if import_action and import_action not in actions:
                    actions.append(import_action)

        # Check for datasheet action
        datasheet_action = _create_datasheet_action(uri, position)
        if datasheet_action:
            actions.append(datasheet_action)

        return actions if actions else None

    except Exception as e:
        logger.debug(f"Code action error: {e}")
        return None


def _create_auto_import_action(
    type_name: str, uri: str, source: str
) -> lsp.CodeAction | None:
    """Create an auto-import code action for a type if it's a stdlib type."""
    # Check if it's a stdlib type
    stdlib_type = _find_stdlib_type(type_name)
    if stdlib_type is None:
        return None

    # Check if already imported
    if _is_already_imported(type_name, source):
        return None

    # Find where to insert the import (after existing imports or at top)
    insert_line = _find_import_insert_line(source)

    # Create the edit
    new_text = f"import {type_name}\n"

    return lsp.CodeAction(
        title=f"Import '{type_name}' from stdlib",
        kind=lsp.CodeActionKind.QuickFix,
        diagnostics=[],
        edit=lsp.WorkspaceEdit(
            changes={
                uri: [
                    lsp.TextEdit(
                        range=lsp.Range(
                            start=lsp.Position(line=insert_line, character=0),
                            end=lsp.Position(line=insert_line, character=0),
                        ),
                        new_text=new_text,
                    )
                ]
            }
        ),
        is_preferred=True,
    )


def _create_import_from_diagnostic(
    diagnostic: lsp.Diagnostic, uri: str, source: str
) -> lsp.CodeAction | None:
    """Create an auto-import action from an 'undefined type' diagnostic."""
    # Try to extract type name from message like "undefined type 'Resistor'"
    message = diagnostic.message

    # Common patterns for undefined type messages
    patterns = [
        r"undefined.*['\"](\w+)['\"]",
        r"unknown.*['\"](\w+)['\"]",
        r"['\"](\w+)['\"].*not defined",
        r"['\"](\w+)['\"].*not found",
    ]

    for pattern in patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            type_name = match.group(1)
            return _create_auto_import_action(type_name, uri, source)

    return None


def _is_already_imported(type_name: str, source: str) -> bool:
    """Check if a type is already imported in the source."""
    # Check for various import patterns:
    # - import TypeName
    # - import TypeName, OtherType
    # - from "path" import TypeName
    patterns = [
        rf"\bimport\s+{type_name}\b",
        rf"\bimport\s+[\w,\s]*\b{type_name}\b",
        rf'from\s+"[^"]+"\s+import\s+[\w,\s]*\b{type_name}\b',
    ]

    for pattern in patterns:
        if re.search(pattern, source):
            return True

    return False


def _find_import_insert_line(source: str) -> int:
    """Find the line number where a new import should be inserted."""
    lines = source.splitlines()
    last_import_line = -1

    for i, line in enumerate(lines):
        stripped = line.strip()
        # Check if this is an import line
        if stripped.startswith("import ") or stripped.startswith("from "):
            last_import_line = i

    # If we found imports, insert after the last one
    if last_import_line >= 0:
        return last_import_line + 1

    # Otherwise, insert at the beginning (after any comments/pragmas)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return i

    return 0


def _create_datasheet_action(uri: str, position: lsp.Position) -> lsp.CodeAction | None:
    """Create a code action to open the datasheet for a component."""
    try:
        uri_path = uris.to_fs_path(uri)
        if uri_path is None:
            return None
        current_file = Path(uri_path)

        # If we're in a parts/ folder file, extract LCSC ID directly
        lcsc_id = _extract_lcsc_id_from_file(current_file)

        # If not in a parts file, try to find the type at cursor and follow it
        if not lcsc_id:
            document = LSP_SERVER.workspace.get_text_document(uri)
            state = get_document_state(uri)

            # Get word at cursor - could be a type name or field
            word = _get_word_at_position(document.source, position)
            if word:
                type_location = None

                # First, try as a type name (e.g., "Texas_Instruments_MAX3243CDBR")
                type_location = _find_type_definition(word, state, uri)

                # If not found, try as an instance name (e.g., "package")
                # and look up what type it was assigned
                if not type_location:
                    type_location = _find_instance_type_definition(word, state, uri)

                if type_location:
                    type_uri_path = uris.to_fs_path(type_location.uri)
                    if type_uri_path:
                        type_path = Path(type_uri_path)
                        lcsc_id = _extract_lcsc_id_from_file(type_path)

        if not lcsc_id:
            return None

        # Try to find local datasheet file first
        project_dir = _infer_lsp_project_root(current_file)
        if project_dir:
            # Check both possible locations
            search_paths = [
                project_dir / "build" / "documentation" / "datasheets",
                project_dir / "build",
            ]
            for search_path in search_paths:
                if search_path.exists():
                    # Search for PDF with LCSC ID in filename
                    for pdf in search_path.rglob(f"*{lcsc_id}*.pdf"):
                        file_uri = uris.from_fs_path(str(pdf))
                        return lsp.CodeAction(
                            title=f"📄 Open Datasheet ({lcsc_id})",
                            kind=lsp.CodeActionKind.QuickFix,
                            command=lsp.Command(
                                title="Open Datasheet",
                                command="vscode.open",
                                arguments=[file_uri],
                            ),
                        )

        # Fallback: open LCSC product page
        lcsc_url = f"https://www.lcsc.com/product-detail/{lcsc_id}.html"
        return lsp.CodeAction(
            title=f"🔗 View on LCSC ({lcsc_id})",
            kind=lsp.CodeActionKind.QuickFix,
            command=lsp.Command(
                title="View on LCSC",
                command="vscode.open",
                arguments=[lcsc_url],
            ),
        )

    except Exception as e:
        logger.debug(f"Datasheet action error: {e}")

    return None


def _extract_lcsc_id_from_file(file_path: Path) -> str | None:
    """Extract LCSC ID from a part file by parsing the has_part_picked trait."""
    try:
        if not file_path.exists():
            return None

        content = file_path.read_text()

        # Look for supplier_partno="CXXXXX" pattern in has_part_picked trait
        match = re.search(r'supplier_partno\s*=\s*["\']([Cc]\d+)["\']', content)
        if match:
            return match.group(1)

        # Also check for lcsc = "CXXXXX" assignment
        match = re.search(r'\blcsc\s*=\s*["\']([Cc]\d+)["\']', content)
        if match:
            return match.group(1)

    except Exception as e:
        logger.debug(f"Error extracting LCSC ID from {file_path}: {e}")

    return None


# -----------------------------------------------------------------------------
# Find All References
# -----------------------------------------------------------------------------


@LSP_SERVER.feature(lsp.TEXT_DOCUMENT_REFERENCES)
def on_find_references(
    params: lsp.ReferenceParams,
) -> list[lsp.Location] | None:
    """Handle find references request."""
    if _broker_manages_document(params.text_document.uri):
        return _broker_query(
            params.text_document.uri,
            "references",
            {
                "line": params.position.line,
                "character": params.position.character,
                "include_declaration": params.context.include_declaration,
            },
        )
    return _find_references_impl(params)


def _find_references_impl(params: lsp.ReferenceParams) -> list[lsp.Location] | None:
    """Find-references implementation that operates on the local in-process state."""
    try:
        uri = params.text_document.uri
        state = get_document_state(uri)
        if _position_is_invalid(state, params.position):
            return None
        document = LSP_SERVER.workspace.get_text_document(uri)

        # Get the field reference at cursor position (e.g., "usb_c.usb" not just "usb")
        field_ref = _get_field_reference_at_position(document.source, params.position)
        if not field_ref:
            return None

        full_path, word_at_cursor = field_ref
        logger.debug(f"Find references: full_path={full_path}, word={word_at_cursor}")

        # Get document state for graph-based search
        # Use graph-based search (semantic, accurate)
        references = _find_field_references_from_graph(full_path, state, uri)
        references = [
            location
            for location in references
            if not _range_overlaps_invalid_region(state, location.range)
        ]

        # Optionally include the declaration
        if params.context.include_declaration:
            first_part = full_path.split(".")[0].split("[")[0]
            definition = _find_type_definition(first_part, state, uri)
            if definition and definition not in references:
                references.insert(0, definition)

        return references if references else None

    except Exception as e:
        logger.debug(f"Find references error: {e}")
        return None


def _find_references_in_document(
    word: str, uri: str, source: str
) -> list[lsp.Location]:
    """Find all references to a word in a document."""
    references = []
    lines = source.splitlines()

    # Build regex pattern to match the word as a whole word
    # Match word boundaries to avoid partial matches
    pattern = re.compile(rf"\b{re.escape(word)}\b")

    for line_num, line in enumerate(lines):
        for match in pattern.finditer(line):
            references.append(
                lsp.Location(
                    uri=uri,
                    range=lsp.Range(
                        start=lsp.Position(line=line_num, character=match.start()),
                        end=lsp.Position(line=line_num, character=match.end()),
                    ),
                )
            )

    return references


def _find_field_references_from_graph(
    field_path: str, state: DocumentState, uri: str
) -> list[lsp.Location]:
    """
    Find all references to a field path or type name using the graph.

    This queries all FieldRef and TypeRef instances in the AST graph and returns
    those that match the target.

    For field paths like "usb_c.usb", it finds:
    - usb_c.usb (exact match)
    - usb_c.usb.usb_if.buspower (path continues)

    For type names like "Resistor", it finds all TypeRef uses.
    """
    references = []

    if state.build_result is None or state.type_graph is None:
        return references

    try:
        g = state.graph_view
        tg = state.type_graph

        # Search FieldRef instances
        field_ref_type = AST.FieldRef.bind_typegraph(tg)
        for inst in field_ref_type.get_instances(g):
            try:
                fr = inst.cast(AST.FieldRef)

                # Get the path parts
                parts = list(fr.parts.get().as_list())
                path_parts = []
                for p in parts:
                    part = p.cast(AST.FieldRefPart)
                    name = part.name.get().get_single()
                    path_parts.append(name)

                ref_path = ".".join(path_parts)

                # Check if this reference matches the target path
                if ref_path == field_path or ref_path.startswith(field_path + "."):
                    loc = fr.source.get().loc.get()
                    line = loc.get_start_line() - 1  # Convert to 0-indexed
                    col = loc.get_start_col()
                    end_col = col + len(field_path)

                    references.append(
                        lsp.Location(
                            uri=uri,
                            range=lsp.Range(
                                start=lsp.Position(line=line, character=col),
                                end=lsp.Position(line=line, character=end_col),
                            ),
                        )
                    )
            except Exception as e:
                logger.debug(f"Error processing FieldRef: {e}")
                continue

        # Also search TypeRef instances (for type names like "Resistor")
        type_ref_type = AST.TypeRef.bind_typegraph(tg)
        for inst in type_ref_type.get_instances(g):
            try:
                tr = inst.cast(AST.TypeRef)
                type_name = tr.name.get().get_single()

                # Check if this type reference matches
                if type_name == field_path:
                    loc = tr.source.get().loc.get()
                    line = loc.get_start_line() - 1
                    col = loc.get_start_col()
                    end_col = col + len(field_path)

                    references.append(
                        lsp.Location(
                            uri=uri,
                            range=lsp.Range(
                                start=lsp.Position(line=line, character=col),
                                end=lsp.Position(line=line, character=end_col),
                            ),
                        )
                    )
            except Exception as e:
                logger.debug(f"Error processing TypeRef: {e}")
                continue

    except Exception as e:
        logger.debug(f"Graph-based reference search error: {e}")

    return references


def _find_references_in_workspace(word: str) -> list[lsp.Location]:
    """Find all references to a word across the workspace."""
    references = []

    # Get all open documents from workspace
    try:
        workspace = LSP_SERVER.workspace
        for doc_uri, doc in workspace.text_documents.items():
            if doc and doc.source:
                doc_refs = _find_references_in_document(word, doc_uri, doc.source)
                references.extend(doc_refs)
    except Exception as e:
        logger.debug(f"Workspace reference search error: {e}")

    return references


# -----------------------------------------------------------------------------
# Rename Symbol
# -----------------------------------------------------------------------------


@LSP_SERVER.feature(lsp.TEXT_DOCUMENT_PREPARE_RENAME)
def on_prepare_rename(
    params: lsp.PrepareRenameParams,
) -> lsp.PrepareRenameResult | None:
    """
    Validate that a rename is possible at the cursor position.

    Returns the range of the symbol to be renamed, or None if rename
    is not possible at this position.
    """
    if _broker_manages_document(params.text_document.uri):
        return _broker_query(
            params.text_document.uri,
            "prepare_rename",
            {
                "line": params.position.line,
                "character": params.position.character,
            },
        )
    return _prepare_rename_impl(params)


def _prepare_rename_impl(
    params: lsp.PrepareRenameParams,
) -> lsp.PrepareRenameResult | None:
    """Prepare-rename implementation that operates on the local in-process state."""
    try:
        uri = params.text_document.uri
        state = get_document_state(uri)
        if _position_is_invalid(state, params.position):
            return None
        document = LSP_SERVER.workspace.get_text_document(uri)
        source = document.source
        position = params.position

        # Get the symbol at the cursor position
        symbol_info = _get_renameable_symbol_at_position(source, position)
        if symbol_info is None:
            return None

        symbol_name, symbol_range = symbol_info
        return lsp.PrepareRenamePlaceholder(
            range=symbol_range,
            placeholder=symbol_name,
        )

    except Exception as e:
        logger.debug(f"Prepare rename error: {e}")
        return None


@LSP_SERVER.feature(lsp.TEXT_DOCUMENT_RENAME)
def on_rename(params: lsp.RenameParams) -> lsp.WorkspaceEdit | None:
    """
    Rename a symbol across the document.

    Finds all references to the symbol and returns a WorkspaceEdit
    containing the text edits to rename all occurrences.
    """
    if _broker_manages_document(params.text_document.uri):
        return _broker_query(
            params.text_document.uri,
            "rename",
            {
                "line": params.position.line,
                "character": params.position.character,
                "new_name": params.new_name,
            },
        )
    return _rename_impl(params)


def _rename_impl(params: lsp.RenameParams) -> lsp.WorkspaceEdit | None:
    """Rename implementation that operates on the local in-process state."""
    try:
        uri = params.text_document.uri
        state = get_document_state(uri)
        if _position_is_invalid(state, params.position):
            return None
        document = LSP_SERVER.workspace.get_text_document(uri)
        source = document.source
        position = params.position
        new_name = params.new_name

        # Validate the new name is a valid identifier
        if not _is_valid_identifier(new_name):
            logger.debug(f"Invalid new name: {new_name}")
            return None

        # Get the symbol at the cursor position
        symbol_info = _get_renameable_symbol_at_position(source, position)
        if symbol_info is None:
            return None

        old_name, _ = symbol_info

        # Get document state for graph-based search
        # Find all references using the graph
        # We need to find all occurrences of the symbol name
        text_edits = _find_rename_edits(old_name, new_name, state, uri, source)
        text_edits = [
            edit
            for edit in text_edits
            if not _range_overlaps_invalid_region(state, edit.range)
        ]

        if not text_edits:
            return None

        return lsp.WorkspaceEdit(
            changes={uri: text_edits},
        )

    except Exception as e:
        logger.debug(f"Rename error: {e}")
        return None


def _get_renameable_symbol_at_position(
    source: str, position: lsp.Position
) -> tuple[str, lsp.Range] | None:
    """
    Get the renameable symbol at the cursor position.

    Returns (symbol_name, range) for simple identifiers only.
    Field paths like "a.b.c" are handled by identifying which part
    the cursor is on.
    """
    lines = source.splitlines()
    if position.line >= len(lines):
        return None

    line = lines[position.line]
    col = position.character

    if col >= len(line):
        return None

    # Check if cursor is on an identifier character
    char_at_cursor = line[col] if col < len(line) else ""
    if not (char_at_cursor.isalnum() or char_at_cursor == "_"):
        return None

    # Expand left to find start of identifier
    start = col
    while start > 0 and (line[start - 1].isalnum() or line[start - 1] == "_"):
        start -= 1

    # Expand right to find end of identifier
    end = col
    while end < len(line) and (line[end].isalnum() or line[end] == "_"):
        end += 1

    symbol = line[start:end]

    # Don't allow renaming keywords
    keywords = {
        "import",
        "from",
        "new",
        "module",
        "component",
        "interface",
        "signal",
        "pin",
        "assert",
        "within",
        "is",
        "to",
        "trait",
        "for",
        "in",
        "pass",
        "True",
        "False",
    }
    if symbol in keywords:
        return None

    symbol_range = lsp.Range(
        start=lsp.Position(line=position.line, character=start),
        end=lsp.Position(line=position.line, character=end),
    )

    return (symbol, symbol_range)


def _is_valid_identifier(name: str) -> bool:
    """Check if a name is a valid ato identifier."""
    if not name:
        return False
    # Must start with letter or underscore
    if not (name[0].isalpha() or name[0] == "_"):
        return False
    # Rest must be alphanumeric or underscore
    return all(c.isalnum() or c == "_" for c in name)


def _find_rename_edits(
    old_name: str, new_name: str, state: DocumentState, uri: str, source: str
) -> list[lsp.TextEdit]:
    """
    Find all text edits needed to rename a symbol.

    Uses the graph to find all semantic references - no text-based fallback
    to ensure we only rename actual code references, not comments or strings.
    """
    edits = []
    seen_ranges: set[tuple[int, int, int, int]] = set()

    if state.build_result is None or state.type_graph is None:
        logger.debug("No build result for rename - cannot proceed")
        return edits

    graph_refs = _find_symbol_references_for_rename(old_name, state, uri)
    for ref in graph_refs:
        range_key = (
            ref.range.start.line,
            ref.range.start.character,
            ref.range.end.line,
            ref.range.end.character,
        )
        if range_key not in seen_ranges:
            seen_ranges.add(range_key)
            edits.append(lsp.TextEdit(range=ref.range, new_text=new_name))

    return edits


def _find_symbol_references_for_rename(
    symbol_name: str, state: DocumentState, uri: str
) -> list[lsp.Location]:
    """
    Find all references to a symbol name for renaming purposes.

    Uses the typegraph to understand the symbol's semantic context, then
    finds all source locations where it appears.

    Strategy:
    1. Check if symbol is a type name (in type_roots) -> search TypeRef instances
    2. Check if symbol is a member of any type (via collect_make_children) ->
       search FieldRef instances
    3. Also check SignaldefStmt and ForLoop for signal/iterator names
    """
    references = []

    if state.build_result is None or state.type_graph is None:
        return references

    try:
        g = state.graph_view
        tg = state.type_graph
        type_roots = state.build_result.state.type_roots

        # Determine what kind of symbol this is using the typegraph
        is_type_name = symbol_name in type_roots
        is_member_name = False
        member_parent_types: list[str] = []

        # Check if symbol is a member of any type using typegraph
        for type_name, type_node in type_roots.items():
            try:
                children = tg.collect_make_children(type_node=type_node)
                for child_name, _ in children:
                    if child_name == symbol_name:
                        is_member_name = True
                        member_parent_types.append(type_name)
            except Exception as e:
                logger.debug(f"Error checking children of {type_name}: {e}")

        logger.debug(
            f"Rename '{symbol_name}': is_type={is_type_name}, "
            f"is_member={is_member_name}, parents={member_parent_types}"
        )

        # 1. Search TypeRef instances if it's a type name
        if is_type_name:
            type_ref_type = AST.TypeRef.bind_typegraph(tg)
            for inst in type_ref_type.get_instances(g):
                try:
                    tr = inst.cast(AST.TypeRef)
                    type_name = tr.name.get().get_single()

                    if type_name == symbol_name:
                        loc = tr.source.get().loc.get()
                        line = loc.get_start_line() - 1
                        col = loc.get_start_col()
                        end_col = col + len(symbol_name)

                        references.append(
                            lsp.Location(
                                uri=uri,
                                range=lsp.Range(
                                    start=lsp.Position(line=line, character=col),
                                    end=lsp.Position(line=line, character=end_col),
                                ),
                            )
                        )
                except Exception as e:
                    logger.debug(f"Error processing TypeRef for rename: {e}")
                    continue

        # 2. If it's a member name, search FieldRef instances
        if is_member_name:
            field_ref_type = AST.FieldRef.bind_typegraph(tg)
            for inst in field_ref_type.get_instances(g):
                try:
                    fr = inst.cast(AST.FieldRef)
                    parts = list(fr.parts.get().as_list())

                    for part_node in parts:
                        part = part_node.cast(AST.FieldRefPart)
                        name = part.name.get().get_single()

                        if name == symbol_name:
                            try:
                                part_loc = part.source.get().loc.get()
                                line = part_loc.get_start_line() - 1
                                col = part_loc.get_start_col()
                                end_col = col + len(symbol_name)

                                references.append(
                                    lsp.Location(
                                        uri=uri,
                                        range=lsp.Range(
                                            start=lsp.Position(
                                                line=line, character=col
                                            ),
                                            end=lsp.Position(
                                                line=line, character=end_col
                                            ),
                                        ),
                                    )
                                )
                            except Exception:
                                loc = fr.source.get().loc.get()
                                line = loc.get_start_line() - 1
                                col = loc.get_start_col()

                                references.append(
                                    lsp.Location(
                                        uri=uri,
                                        range=lsp.Range(
                                            start=lsp.Position(
                                                line=line, character=col
                                            ),
                                            end=lsp.Position(
                                                line=line,
                                                character=col + len(symbol_name),
                                            ),
                                        ),
                                    )
                                )
                except Exception as e:
                    logger.debug(f"Error processing FieldRef for rename: {e}")
                    continue

        # 3. Search SignaldefStmt (signal definitions)
        signal_def_type = AST.SignaldefStmt.bind_typegraph(tg)
        for inst in signal_def_type.get_instances(g):
            try:
                sig = inst.cast(AST.SignaldefStmt)
                sig_name = sig.name.get().get_single()

                if sig_name == symbol_name:
                    loc = sig.source.get().loc.get()
                    line = loc.get_start_line() - 1
                    col = loc.get_start_col() + len("signal ")
                    end_col = col + len(symbol_name)

                    references.append(
                        lsp.Location(
                            uri=uri,
                            range=lsp.Range(
                                start=lsp.Position(line=line, character=col),
                                end=lsp.Position(line=line, character=end_col),
                            ),
                        )
                    )
            except Exception as e:
                logger.debug(f"Error processing SignaldefStmt for rename: {e}")
                continue

        # 4. Search ForLoop iterators
        for_loop_type = AST.ForLoop.bind_typegraph(tg)
        for inst in for_loop_type.get_instances(g):
            try:
                loop = inst.cast(AST.ForLoop)
                iter_name = loop.iterator_name.get().get_single()

                if iter_name == symbol_name:
                    loc = loop.source.get().loc.get()
                    line = loc.get_start_line() - 1
                    col = loc.get_start_col() + len("for ")
                    end_col = col + len(symbol_name)

                    references.append(
                        lsp.Location(
                            uri=uri,
                            range=lsp.Range(
                                start=lsp.Position(line=line, character=col),
                                end=lsp.Position(line=line, character=end_col),
                            ),
                        )
                    )
            except Exception as e:
                logger.debug(f"Error processing ForLoop for rename: {e}")
                continue

    except Exception as e:
        logger.debug(f"Symbol reference search error for rename: {e}")

    return references


# -----------------------------------------------------------------------------
# Document Formatting
# -----------------------------------------------------------------------------


def format_ato_source(source: str) -> str:
    """
    Format ato source code according to standard conventions.

    Formatting rules:
    - 4 spaces for indentation
    - Single space around operators (=, ~, ~>, <~)
    - Trailing whitespace removed
    - Single blank line between top-level blocks
    - Preserve comments and docstrings
    """
    lines = source.splitlines()
    formatted_lines = []

    for i, line in enumerate(lines):
        formatted_line = _format_line(line)
        formatted_lines.append(formatted_line)

    # Post-process: ensure proper blank lines between blocks
    result_lines = _normalize_blank_lines(formatted_lines)

    # Ensure file ends with a single newline
    result = "\n".join(result_lines)
    if result and not result.endswith("\n"):
        result += "\n"

    return result


def _format_line(line: str) -> str:
    """Format a single line of ato code."""
    # Preserve empty lines
    if not line.strip():
        return ""

    # Extract leading whitespace (indentation)
    stripped = line.lstrip()
    original_indent = line[: len(line) - len(stripped)]

    # Convert tabs to spaces and normalize indentation to multiples of 4
    # Any non-zero indentation is rounded up to at least 4 spaces
    indent_spaces = original_indent.replace("\t", "    ")
    if indent_spaces:
        # Round up to nearest multiple of 4, minimum 4
        indent_level = max(1, (len(indent_spaces) + 3) // 4)
    else:
        indent_level = 0
    normalized_indent = "    " * indent_level

    # Handle comments - preserve them but format what comes before
    if "#" in stripped:
        # Find comment position, being careful about strings
        comment_pos = _find_comment_position(stripped)
        if comment_pos is not None:
            code_part = stripped[:comment_pos].rstrip()
            comment_part = stripped[comment_pos:]
            # Ensure space after # in comment (but not for pragma)
            comment_part = _format_comment(comment_part)

            if code_part:
                # Format the code part, then append comment
                formatted_code = _format_code_segment(code_part)
                # Ensure single space before inline comment
                return normalized_indent + formatted_code + "  " + comment_part
            else:
                # Line is only a comment
                return normalized_indent + comment_part

    # Handle docstrings - preserve them as-is (just normalize indent)
    if stripped.startswith('"""') or stripped.startswith("'''"):
        return normalized_indent + stripped.rstrip()

    # Handle pragma statements
    if stripped.startswith("#pragma"):
        return normalized_indent + stripped.rstrip()

    # Format regular code
    formatted_code = _format_code_segment(stripped)
    return normalized_indent + formatted_code


def _find_comment_position(line: str) -> int | None:
    """
    Find the position of a comment (#) in a line, ignoring # inside strings.
    Returns None if no comment found.
    """
    in_string = False
    string_char = None

    for i, char in enumerate(line):
        if char in ('"', "'") and (i == 0 or line[i - 1] != "\\"):
            if not in_string:
                in_string = True
                string_char = char
            elif char == string_char:
                in_string = False
                string_char = None
        elif char == "#" and not in_string:
            return i

    return None


def _format_comment(comment: str) -> str:
    """
    Format a comment to ensure proper spacing after #.
    Preserves pragma comments as-is.
    """
    if not comment.startswith("#"):
        return comment

    # Don't modify pragma comments
    if comment.startswith("#pragma"):
        return comment

    # Remove the # and any leading whitespace
    rest = comment[1:].lstrip()

    # If empty comment, just return #
    if not rest:
        return "#"

    # Return with single space after #
    return "# " + rest


def _format_code_segment(code: str) -> str:
    """Format a segment of code (without comments)."""
    code = code.rstrip()

    # Skip formatting for docstrings
    if code.startswith('"""') or code.startswith("'''"):
        return code

    # Skip formatting for string-only lines
    if (code.startswith('"') and code.endswith('"')) or (
        code.startswith("'") and code.endswith("'")
    ):
        return code

    # Format operators with proper spacing
    code = _format_operators(code)

    # Clean up multiple spaces (but not in strings)
    code = _normalize_spaces(code)

    return code


def _format_operators(code: str) -> str:
    """Format operators with proper spacing."""
    # Track string boundaries to avoid formatting inside strings
    result = []
    i = 0
    in_string = False
    string_char = None

    while i < len(code):
        char = code[i]

        # Handle string boundaries
        if char in ('"', "'") and (i == 0 or code[i - 1] != "\\"):
            if not in_string:
                in_string = True
                string_char = char
            elif char == string_char:
                in_string = False
                string_char = None
            result.append(char)
            i += 1
            continue

        # Skip formatting inside strings
        if in_string:
            result.append(char)
            i += 1
            continue

        # Handle multi-character operators
        two_char = code[i : i + 2] if i + 1 < len(code) else ""
        three_char = code[i : i + 3] if i + 2 < len(code) else ""

        # Bridged connections: ~> and <~
        if two_char == "~>":
            _ensure_space_before(result)
            result.append("~>")
            i += 2
            _skip_spaces_and_add_one(code, i, result)
            continue
        elif two_char == "<~":
            _ensure_space_before(result)
            result.append("<~")
            i += 2
            _skip_spaces_and_add_one(code, i, result)
            continue

        # Compound assignment: +=, -=, |=, &=
        if two_char in ("+=", "-=", "|=", "&="):
            _ensure_space_before(result)
            result.append(two_char)
            i += 2
            _skip_spaces_and_add_one(code, i, result)
            continue

        # Comparison operators: <=, >=, ==
        if two_char in ("<=", ">=", "=="):
            _ensure_space_before(result)
            result.append(two_char)
            i += 2
            _skip_spaces_and_add_one(code, i, result)
            continue

        # Tolerance: +/-
        if three_char == "+/-":
            _ensure_space_before(result)
            result.append("+/-")
            i += 3
            _skip_spaces_and_add_one(code, i, result)
            continue

        # Arrow for retyping: ->
        if two_char == "->":
            _ensure_space_before(result)
            result.append("->")
            i += 2
            _skip_spaces_and_add_one(code, i, result)
            continue

        # Double colon for traits: ::
        if two_char == "::":
            # No spaces around ::
            result.append("::")
            i += 2
            continue

        # Single connection operator: ~
        if char == "~":
            _ensure_space_before(result)
            result.append("~")
            i += 1
            _skip_spaces_and_add_one(code, i, result)
            continue

        # Assignment operator: = (but not ==, <=, >=, +=, -=, |=, &=)
        if char == "=":
            # Check if this is part of a compound operator we didn't catch
            prev_char = result[-1] if result else ""
            if prev_char not in ("+", "-", "|", "&", "<", ">", "=", "!"):
                _ensure_space_before(result)
                result.append("=")
                i += 1
                _skip_spaces_and_add_one(code, i, result)
                continue

        # Colon for type hints or block definitions
        if char == ":":
            # Don't add space before colon
            result.append(":")
            i += 1
            # Add space after colon if not end of line and not ::
            if i < len(code) and code[i] != ":" and code[i] != "\n":
                if code[i] != " ":
                    result.append(" ")
            continue

        # Comma: space after but not before
        if char == ",":
            # Remove trailing space before comma
            while result and result[-1] == " ":
                result.pop()
            result.append(",")
            i += 1
            # Add space after comma if not at end
            if i < len(code) and code[i] != " ":
                result.append(" ")
            continue

        # Semicolon for multiple statements
        if char == ";":
            # Remove trailing space before semicolon
            while result and result[-1] == " ":
                result.pop()
            result.append(";")
            i += 1
            # Add space after semicolon if not at end
            if i < len(code) and code[i] != " ":
                result.append(" ")
            continue

        result.append(char)
        i += 1

    return "".join(result)


def _ensure_space_before(result: list[str]) -> None:
    """Ensure there's a space at the end of result (before an operator)."""
    if result and result[-1] != " ":
        result.append(" ")


def _skip_spaces_and_add_one(code: str, start: int, result: list[str]) -> None:
    """Skip consecutive spaces in code from start, adding exactly one space."""
    # This is a helper but the actual skipping happens in the main loop
    # We just ensure one space is added after an operator
    result.append(" ")


def _normalize_spaces(code: str) -> str:
    """Normalize multiple consecutive spaces to single space (outside strings)."""
    result = []
    i = 0
    in_string = False
    string_char = None
    prev_space = False

    while i < len(code):
        char = code[i]

        # Handle string boundaries
        if char in ('"', "'") and (i == 0 or code[i - 1] != "\\"):
            if not in_string:
                in_string = True
                string_char = char
            elif char == string_char:
                in_string = False
                string_char = None

        # Inside strings, preserve everything
        if in_string:
            result.append(char)
            prev_space = False
            i += 1
            continue

        # Outside strings, collapse multiple spaces
        if char == " ":
            if not prev_space:
                result.append(char)
            prev_space = True
        else:
            result.append(char)
            prev_space = False

        i += 1

    return "".join(result)


def _normalize_blank_lines(lines: list[str]) -> list[str]:
    """
    Normalize blank lines between blocks.
    - Remove trailing blank lines
    - Ensure single blank line between module/interface definitions
    - Remove multiple consecutive blank lines
    """
    if not lines:
        return lines

    result = []
    prev_blank = False

    for line in lines:
        stripped = line.strip()
        is_blank = not stripped

        # Check if this line starts a new block (module, interface, component)
        is_block_start = bool(
            re.match(r"^(module|interface|component)\s+\w+", stripped)
        )

        if is_blank:
            # Only add blank line if we're not already after a blank line
            if not prev_blank:
                result.append("")
            prev_blank = True
        else:
            # Add extra blank line before block definitions (if not at start)
            if is_block_start and result and not prev_blank:
                result.append("")

            result.append(line)
            prev_blank = False

    # Remove trailing blank lines
    while result and not result[-1].strip():
        result.pop()

    return result


@LSP_SERVER.feature(
    lsp.TEXT_DOCUMENT_FORMATTING,
    lsp.DocumentFormattingOptions(),
)
def on_document_formatting(
    params: lsp.DocumentFormattingParams,
) -> list[lsp.TextEdit] | None:
    """Handle document formatting request."""
    uri = params.text_document.uri
    logger.debug(f"Formatting document: {uri}")

    try:
        document = LSP_SERVER.workspace.get_text_document(uri)
        if not document or not document.source:
            return None

        original = document.source
        formatted = format_ato_source(original)

        # If no changes, return empty list
        if formatted == original:
            return []

        # Return a single edit replacing the entire document
        line_count = len(original.splitlines())
        last_line = original.splitlines()[-1] if original.splitlines() else ""

        return [
            lsp.TextEdit(
                range=lsp.Range(
                    start=lsp.Position(line=0, character=0),
                    end=lsp.Position(line=line_count, character=len(last_line)),
                ),
                new_text=formatted,
            )
        ]
    except Exception as e:
        logger.error(f"Formatting error: {e}")
        return None


@LSP_SERVER.feature(lsp.SHUTDOWN)
def on_shutdown(_params: Any = None) -> None:
    """Handle shutdown request."""
    logger.info("LSP server shutting down")
    BROKER.shutdown()
    # Clean up document states
    for state in DOCUMENT_STATES.values():
        state.reset_graph()
    DOCUMENT_STATES.clear()


@LSP_SERVER.feature(lsp.EXIT)
def on_exit(_params: Any = None) -> None:
    """Handle exit request."""
    logger.info("LSP server exiting")


# -----------------------------------------------------------------------------
# Server Entry Point
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    LSP_SERVER.start_io()
