"""
Entry points for building from ato sources.
"""

import time
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import faebryk.core.faebrykpy as fbrk
import faebryk.core.graph as graph
import faebryk.core.node as fabll
from atopile.compiler import (
    DslException,
    DslImportError,
    DslRichException,
    DslUndefinedSymbolError,
)
from atopile.compiler import ast_types as AST
from atopile.compiler.antlr_visitor import ANTLRVisitor
from atopile.compiler.ast_visitor import (
    STDLIB_TYPES,
    ASTVisitor,
    BuildState,
)
from atopile.compiler.gentypegraph import ImportRef
from atopile.compiler.parse import parse_file, parse_text_as_file
from atopile.compiler.parser.AtoParser import AtoParser
from atopile.config import find_project_dir
from atopile.errors import accumulate, iter_leaf_exceptions
from faebryk.libs.util import import_from_path, once, unique


@dataclass
class BuildFileResult:
    ast_root: AST.File
    state: BuildState
    visitor: ASTVisitor


class StdlibRegistry:
    """Lazy loader for stdlib types."""

    def __init__(
        self,
        tg: fbrk.TypeGraph,
        extra_types: set[type[fabll.Node]] | None = None,
    ) -> None:
        self._tg = tg
        self._cache: dict[str, graph.BoundNode] = {}
        all_types = STDLIB_TYPES | (extra_types or set())
        self._stdlib_types = {type_._type_identifier(): type_ for type_ in all_types}
        # Bootstrap has_source_chunk so Zig can find it during instantiation
        import faebryk.library._F as F

        F.has_source_chunk.bind_typegraph(self._tg).get_or_create_type()

    def get(self, name: str) -> graph.BoundNode:
        if name not in self._cache:
            if name not in self._stdlib_types:
                raise KeyError(f"Unknown stdlib type: {name}")
            obj = self._stdlib_types[name]
            type_node = fabll.TypeNodeBoundTG.get_or_create_type_in_tg(self._tg, obj)
            self._cache[name] = type_node
        return self._cache[name]

    def __contains__(self, name: str) -> bool:
        return name in self._stdlib_types


class TestStdlibRegistry:
    """Tests for lazy stdlib loading."""

    def test_lazy_loading(self):
        """Types are only created when first accessed."""
        g = graph.GraphView.create()
        tg = fbrk.TypeGraph.create(g=g)
        registry = StdlibRegistry(tg)

        assert tg.get_type_by_name(type_identifier="Resistor") is None

        node = registry.get("Resistor")
        assert node is not None
        assert tg.get_type_by_name(type_identifier="Resistor") is not None

    def test_caching(self):
        """Same type node returned on repeated access."""
        g = graph.GraphView.create()
        tg = fbrk.TypeGraph.create(g=g)
        registry = StdlibRegistry(tg)

        node1 = registry.get("Resistor")
        node2 = registry.get("Resistor")
        assert node1.node().is_same(other=node2.node())

    def test_unknown_type_raises(self):
        """KeyError raised for unknown stdlib types."""
        import pytest

        g = graph.GraphView.create()
        tg = fbrk.TypeGraph.create(g=g)
        registry = StdlibRegistry(tg)

        with pytest.raises(KeyError):
            registry.get("NotARealType")


class FabllTypeImportError(Exception):
    """Base class for fabll.Node import errors."""

    pass


class FabllTypeFileNotFoundError(FabllTypeImportError):
    pass


class FabllTypeSymbolNotFoundError(FabllTypeImportError):
    pass


class FabllTypeNotATypeError(FabllTypeImportError):
    pass


class FabllTypeNotNodeSubclassError(FabllTypeImportError):
    pass


def import_fabll_type(path: Path, symbol: str) -> type[fabll.Node]:
    """
    Import a fabll.Node subclass from a Python file.

    Raises FabllTypeImportError subclasses on failure:
    - FabllTypeFileNotFoundError: file doesn't exist
    - FabllTypeSymbolNotFoundError: symbol not found in module
    - FabllTypeNotATypeError: symbol is not a type
    - FabllTypeNotNodeSubclassError: type is not a fabll.Node subclass
    """
    try:
        obj = import_from_path(path, symbol)
    except FileNotFoundError as e:
        raise FabllTypeFileNotFoundError(f"File not found: {path}") from e
    except (AttributeError, ImportError) as e:
        raise FabllTypeSymbolNotFoundError(
            f"Symbol `{symbol}` not found in `{path}`"
        ) from e

    if not isinstance(obj, type):
        raise FabllTypeNotATypeError(f"Symbol `{symbol}` in `{path}` is not a type")

    if not issubclass(obj, fabll.Node):
        raise FabllTypeNotNodeSubclassError(
            f"Symbol `{symbol}` in `{path}` is not a fabll.Node subclass"
        )

    return obj


class LinkerException(Exception):
    pass


class UnresolvedTypeReferencesError(LinkerException):
    def __init__(
        self,
        message: str,
        unresolved_type_references: list[tuple[graph.BoundNode, graph.BoundNode]],
    ) -> None:
        super().__init__(message)
        self.unresolved_type_references = unresolved_type_references


class ImportPathNotFoundError(LinkerException):
    pass


class UndefinedSymbolError(LinkerException):
    pass


class CircularImportError(LinkerException):
    pass


class SearchPathResolver:
    """
    Implements search-path resolution for path-based imports.

    Resolution order:
    1. Directory containing the importing file (when available).
    2. Extra search paths supplied by the caller (insertion order, duplicates removed).
    3. Project `src` directory (from config, when known).
    4. Project `.ato/modules` directory (from config, when known).
    5. Project root directory (discovered via `find_project_dir`).
    6. Stdlib `.ato` directory (`faebryk/library/`).

    Each candidate path is normalised (expanduser + resolve) and deduplicated. If the
    current project declares a package identifier and the import path starts with it,
    the resolver first rewrites the prefix to the project `src` directory and probes
    that absolute path immediately. If it does not exist, the raw import string is then
    checked relative to each search path in order. Any successful probe returns the
    normalised file location; otherwise an `ImportPathNotFoundError` is raised.
    """

    def __init__(self, config_obj, *, extra_search_paths: Iterable[Path]) -> None:
        project = getattr(config_obj, "project", None)
        package_cfg = getattr(project, "package", None)
        project_paths = getattr(project, "paths", None)
        self._extra_search_paths = extra_search_paths
        self._project_src = self._normalize_path_optional(
            getattr(project_paths, "src", None)
        )
        self._project_modules = self._normalize_path_optional(
            getattr(project_paths, "modules", None)
        )
        self._project_root = self._normalize_path_optional(
            getattr(project_paths, "root", None)
        )
        self._package_identifier: str | None = getattr(package_cfg, "identifier", None)
        self._stdlib_ato_dir = (
            Path(__file__).parent.parent.parent / "faebryk" / "library"
        )

    @staticmethod
    def _normalize_path(path: Path) -> Path:
        return path.expanduser().resolve()

    @staticmethod
    def _normalize_path_optional(path: Path | None) -> Path | None:
        return None if path is None else SearchPathResolver._normalize_path(path)

    @property
    @once
    def static_paths(self) -> tuple[Path, ...]:
        return tuple(
            unique(
                [
                    SearchPathResolver._normalize_path(path)
                    for path in [
                        *self._extra_search_paths,
                        self._project_src,
                        self._project_modules,
                    ]
                    if path is not None
                ],
                key=str,
            )
        )

    def _rewrite_package_identifier(self, raw_path: str) -> Path | None:
        if (
            self._package_identifier is not None
            and self._project_src is not None
            and raw_path.startswith(self._package_identifier)
        ):
            return self._normalize_path(
                Path(
                    raw_path.replace(
                        self._package_identifier, str(self._project_src), 1
                    )
                )
            )

    def search_paths(self, base_file: Path | None) -> Generator[Path, None, None]:
        if base_file is not None:
            yield self._normalize_path(base_file).parent

        yield from self.static_paths

        if self._project_root is not None:
            yield self._project_root

        if (
            base_file is not None
            and (project_dir := find_project_dir(base_file)) is not None
        ):
            yield self._normalize_path(project_dir)

        # Stdlib .ato files
        if self._stdlib_ato_dir.exists():
            yield self._stdlib_ato_dir

    @once
    def resolve(self, raw_path: str, base_file: Path | None) -> Path:
        # Package self-imports take precedence
        if (rewritten := self._rewrite_package_identifier(raw_path)) is not None:
            if rewritten.exists():
                return rewritten

        for search_dir in self.search_paths(base_file):
            if (candidate := search_dir / raw_path).exists():
                return self._normalize_path(candidate)

        raise ImportPathNotFoundError(self._make_import_error_message(raw_path))

    def _make_import_error_message(self, raw_path: str) -> str:
        """Generate an error message with hints for common misconfigurations."""
        msg = f"Unable to resolve import `{raw_path}`"
        path_parts = raw_path.split("/")
        potential_vendor = path_parts[0]

        if package_identifier := self._package_identifier:
            package_vendor = package_identifier.split("/")[0]
            if len(path_parts) >= 2:
                if package_vendor == potential_vendor:
                    msg += (
                        "\n\nHint: Your ato.yaml has `package.identifier: "
                        f"{package_identifier}` but this import starts with "
                        f"`{'/'.join(path_parts[:2])}`. If this is a self-reference, "
                        "check that your package.identifier matches the start of the "
                        "import path."
                    )

        return msg


class Linker:
    def __init__(
        self,
        config_obj,
        stdlib: StdlibRegistry,
        tg: fbrk.TypeGraph,
        extra_search_paths: Iterable[Path] | None = None,
        source_overrides: Mapping[Path, str] | None = None,
        linked_modules_seed: Mapping[Path, Mapping[str, graph.BoundNode]] | None = None,
    ) -> None:
        self._resolver = SearchPathResolver(
            config_obj, extra_search_paths=extra_search_paths or []
        )
        self._stdlib = stdlib
        self._tg = tg
        self._active_paths: set[Path] = set()
        self._linked_modules: dict[Path, dict[str, graph.BoundNode]] = {
            path: dict(type_roots)
            for path, type_roots in (linked_modules_seed or {}).items()
        }
        self._stdlib_ato_files = self._discover_stdlib_ato_files()
        self._source_overrides = {
            SearchPathResolver._normalize_path(path): source
            for path, source in (source_overrides or {}).items()
        }

    def _discover_stdlib_ato_files(self) -> dict[str, Path]:
        stdlib_ato_files = {}
        faebryk_lib_path = Path(__file__).parent.parent.parent / "faebryk" / "library"
        if faebryk_lib_path.exists():
            for file_path in faebryk_lib_path.glob("*.ato"):
                name = file_path.stem
                stdlib_ato_files[name] = file_path
        return stdlib_ato_files

    def resolve(
        self, path: str, name: str, base_file: Path | None
    ) -> graph.BoundNode | None:
        """
        Resolve a type from an imported file.
        Implements the FileImportLookup protocol for DeferredExecutor.
        """
        if Path(path).stem in self._stdlib_ato_files:
            stdlib_path = self._stdlib_ato_files[Path(path).stem]
            if stdlib_path in self._linked_modules:
                return self._linked_modules[stdlib_path].get(name)
            return None

        try:
            source_path = self._resolver.resolve(raw_path=path, base_file=base_file)
        except ImportPathNotFoundError:
            return None

        if source_path in self._linked_modules:
            return self._linked_modules[source_path].get(name)
        return None

    def _find_import_node_for_ref(
        self, import_ref: ImportRef, build_state: BuildState
    ) -> fabll.Node | None:
        """Find the AST node that triggered this import."""
        for ref, node in build_state.explicit_import_refs:
            if ref == import_ref:
                return node
        for _, ref, node, _ in build_state.external_type_refs:
            if ref == import_ref:
                return node
        return None

    def _find_import_source_node_for_ref(
        self,
        import_ref: ImportRef,
        build_state: BuildState,
        *,
        prefer_path: bool = False,
    ) -> fabll.Node | None:
        source_node = self._find_import_node_for_ref(import_ref, build_state)
        if (
            prefer_path
            and source_node is not None
            and source_node.isinstance(AST.ImportStmt)
            and import_ref.path is not None
        ):
            import_stmt = source_node.cast(AST.ImportStmt)
            if import_stmt.get_path() is not None:
                return import_stmt.path.get()
        return source_node

    def _build_path(
        self,
        *,
        graph: graph.GraphView,
        import_path: str,
        source_path: Path,
    ) -> BuildFileResult:
        source_override = self._source_overrides.get(source_path)
        if source_override is not None:
            return build_source(
                g=graph,
                tg=self._tg,
                source=source_override,
                import_path=import_path,
                source_path=source_path,
            )
        return build_file(
            g=graph,
            tg=self._tg,
            import_path=import_path,
            path=source_path,
        )

    @staticmethod
    def _record_compiled_file_timing(
        build_state: BuildState, source_path: Path, elapsed_ms: float
    ) -> None:
        path_key = str(source_path.resolve())
        build_state.compiled_file_timings_ms[path_key] = round(
            build_state.compiled_file_timings_ms.get(path_key, 0.0) + elapsed_ms,
            1,
        )

    @staticmethod
    def _merge_compiled_file_timings(
        build_state: BuildState, child_state: BuildState
    ) -> None:
        for path_key, elapsed_ms in child_state.compiled_file_timings_ms.items():
            build_state.compiled_file_timings_ms[path_key] = round(
                build_state.compiled_file_timings_ms.get(path_key, 0.0) + elapsed_ms,
                1,
            )

    def _build_and_link_dependency(
        self,
        *,
        graph: graph.GraphView,
        build_state: BuildState,
        import_path: str,
        source_path: Path,
    ) -> BuildFileResult:
        dependency_started_at = time.perf_counter()
        child_result = self._build_path(
            graph=graph,
            import_path=import_path,
            source_path=source_path,
        )
        self._link_recursive(graph, child_result.state)
        from atopile.compiler.deferred_executor import DeferredExecutor

        # Imported .ato dependencies need stage-2 execution as well so inheritance,
        # retypes, and deferred loops are visible when the parent file links against
        # their exported types.
        DeferredExecutor(
            g=graph,
            tg=self._tg,
            state=child_result.state,
            visitor=child_result.visitor,
            stdlib=self._stdlib,
            file_imports=self,
        ).execute()
        self._record_compiled_file_timing(
            build_state,
            source_path,
            round((time.perf_counter() - dependency_started_at) * 1000, 1),
        )
        self._merge_compiled_file_timings(build_state, child_result.state)
        return child_result

    def _build_imported_file(
        self, graph: graph.GraphView, import_ref: ImportRef, build_state: BuildState
    ) -> graph.BoundNode:
        assert import_ref.path is not None

        try:
            source_path = self._resolver.resolve(
                raw_path=import_ref.path, base_file=build_state.file_path
            )
        except ImportPathNotFoundError as e:
            raise DslRichException(
                message=str(e),
                original=DslImportError(str(e)),
                source_node=self._find_import_source_node_for_ref(
                    import_ref,
                    build_state,
                    prefer_path=True,
                ),
            ) from e

        # Rest of method continues with source_path defined

        if source_path in self._linked_modules:
            if import_ref.name in self._linked_modules[source_path]:
                return self._linked_modules[source_path][import_ref.name]
            if source_path.suffix != ".py":
                raise DslRichException(
                    f"Symbol `{import_ref.name}` not found in `{source_path}`",
                    traceback=[],
                    original=DslUndefinedSymbolError(),
                    source_node=self._find_import_node_for_ref(import_ref, build_state),
                )

        assert source_path.exists()

        if source_path.suffix == ".py":
            try:
                node_t = import_fabll_type(source_path, import_ref.name)
            except FabllTypeImportError as e:
                raise DslRichException(
                    str(e),
                    traceback=[],
                    original=DslImportError(str(e)),
                    source_node=self._find_import_node_for_ref(import_ref, build_state),
                ) from e

            type_node = node_t.bind_typegraph(self._tg).get_or_create_type()
            if source_path not in self._linked_modules:
                self._linked_modules[source_path] = {}
            self._linked_modules[source_path][import_ref.name] = type_node
            return type_node

        try:
            child_result = self._build_and_link_dependency(
                graph=graph,
                build_state=build_state,
                import_path=import_ref.path,
                source_path=source_path,
            )
        except DslRichException as ex:
            # Add import frame showing where this import was triggered
            import_node = self._find_import_node_for_ref(import_ref, build_state)
            if import_node and build_state.file_path:
                ex.add_import_frame(import_node, build_state.file_path)
            raise
        except BaseExceptionGroup as ex:
            import_node = self._find_import_node_for_ref(import_ref, build_state)
            if import_node and build_state.file_path:
                for leaf in iter_leaf_exceptions(ex):
                    if isinstance(leaf, DslRichException):
                        leaf.add_import_frame(import_node, build_state.file_path)
            raise
        except DslException as ex:
            # Bare exception from child — wrap with import context
            import_node = self._find_import_node_for_ref(import_ref, build_state)
            raise DslRichException(
                str(ex),
                traceback=[],
                original=ex,
                source_node=import_node,
            ) from ex

        self._linked_modules[source_path] = child_result.state.type_roots
        try:
            return child_result.state.type_roots[import_ref.name]
        except KeyError:
            # The imported module doesn't exist in the file
            import_node = self._find_import_node_for_ref(import_ref, build_state)
            available_modules = list(child_result.state.type_roots.keys())
            raise DslRichException(
                f"Module '{import_ref.name}' not found in '{source_path}'. "
                f"Available modules: {available_modules}",
                traceback=[],
                source_node=import_node,
            )

    def link_imports(self, g: graph.GraphView, build_state: BuildState) -> None:
        resolved_path = (
            self._resolver._normalize_path(build_state.file_path)
            if build_state.file_path is not None
            else None
        )

        match resolved_path:
            case None:
                self._link(g, build_state)
            case _:
                with self._guard_path(resolved_path):
                    self._link(g, build_state)
                    self._linked_modules[resolved_path] = build_state.type_roots

        # Only check for unresolved refs at the top level
        if unresolved := fbrk.Linker.collect_unresolved_type_references(
            type_graph=self._tg
        ):
            raise UnresolvedTypeReferencesError(
                "Unresolved type references remaining after linking", unresolved
            )

    def _link_recursive(self, graph: graph.GraphView, build_state: BuildState) -> None:
        """Link imports without checking unresolved refs (for recursive calls)."""
        resolved_path = (
            self._resolver._normalize_path(build_state.file_path)
            if build_state.file_path is not None
            else None
        )

        match resolved_path:
            case None:
                self._link(graph, build_state)
            case _:
                with self._guard_path(resolved_path):
                    self._link(graph, build_state)
                    self._linked_modules[resolved_path] = build_state.type_roots

    def _link(
        self,
        graph: graph.GraphView,
        build_state: BuildState,
    ) -> None:
        from atopile.compiler import DslRichException

        with accumulate(DslRichException, group_message="Linker errors") as errors:
            explicit_refs_with_type_uses = {
                import_ref
                for _, import_ref, _, _ in build_state.external_type_refs
                if import_ref is not None and import_ref.path is not None
            }
            for import_ref, _ in build_state.explicit_import_refs:
                if (
                    import_ref.path is None
                    or import_ref in explicit_refs_with_type_uses
                ):
                    continue
                with errors.collect():
                    self._build_imported_file(graph, import_ref, build_state)

            for (
                type_reference,
                import_ref,
                source_node,
                traceback_stack,
            ) in build_state.external_type_refs:
                with errors.collect():
                    if import_ref is None:
                        # Local type reference — look up in type_roots
                        type_id = fbrk.TypeGraph.get_type_reference_identifier(
                            type_reference=type_reference
                        )
                        target = build_state.type_roots.get(type_id)
                        if target is None:
                            msg = f"Symbol `{type_id}` is not defined in this scope"
                            if (
                                type_id in self._stdlib
                                or type_id in self._stdlib_ato_files
                            ):
                                msg += (
                                    f". Add `import {type_id}` to use"
                                    f" `{type_id}` from the standard library"
                                )
                            raise DslRichException(
                                msg,
                                original=DslUndefinedSymbolError(),
                                source_node=source_node,
                                traceback=traceback_stack,
                            )
                    elif import_ref.path is None:
                        # stdlib import - first check Python types, then .ato files
                        if import_ref.name in self._stdlib:
                            target = self._stdlib.get(import_ref.name)
                        elif import_ref.name in self._stdlib_ato_files:
                            stdlib_path = self._stdlib_ato_files[import_ref.name]
                            if stdlib_path in self._linked_modules:
                                if import_ref.name in self._linked_modules[stdlib_path]:
                                    target = self._linked_modules[stdlib_path][
                                        import_ref.name
                                    ]
                                else:
                                    raise DslRichException(
                                        f"Symbol `{import_ref.name}` not found in "
                                        f"stdlib file `{stdlib_path}`",
                                        traceback=[],
                                        original=DslUndefinedSymbolError(),
                                        source_node=self._find_import_node_for_ref(
                                            import_ref, build_state
                                        ),
                                    )
                            else:
                                child_result = self._build_and_link_dependency(
                                    graph=graph,
                                    build_state=build_state,
                                    import_path=str(stdlib_path),
                                    source_path=stdlib_path,
                                )
                                self._linked_modules[stdlib_path] = (
                                    child_result.state.type_roots
                                )
                                target = child_result.state.type_roots[import_ref.name]
                    else:
                        # file import (includes stdlib .ato files)
                        target = self._build_imported_file(
                            graph, import_ref, build_state
                        )

                    fbrk.Linker.link_type_reference(
                        g=graph,
                        type_reference=type_reference,
                        target_type_node=target,
                    )

            # Process inheritance imports - these are file imports needed for parent
            # types in inheritance relationships. We don't create type references for
            # these, we just ensure the files are built/linked so that DeferredExecutor
            # can look them up when resolving inheritance.
            for import_ref in build_state.inheritance_imports:
                with errors.collect():
                    self._build_imported_file(graph, import_ref, build_state)

    @contextmanager
    def _guard_path(self, path: Path) -> Generator[None, None, None]:
        if path in self._active_paths:
            raise CircularImportError(f"Circular import detected at `{path}`")

        self._active_paths.add(path)

        try:
            yield
        finally:
            self._active_paths.remove(path)


def _build_from_ctx(
    *,
    g: graph.GraphView,
    tg: fbrk.TypeGraph,
    import_path: str | None,
    root_ctx: AtoParser.File_inputContext,
    file_path: Path | None,
    extra_types: set[type[fabll.Node]] | None = None,
) -> BuildFileResult:
    ast_root = ANTLRVisitor(g, tg, file_path).visit(root_ctx)
    assert isinstance(ast_root, AST.File)
    visitor = ASTVisitor(ast_root, g, tg, import_path, file_path, extra_types)
    return BuildFileResult(ast_root=ast_root, state=visitor.build(), visitor=visitor)


def build_file(
    *,
    g: graph.GraphView,
    tg: fbrk.TypeGraph,
    import_path: str,
    path: Path,
) -> BuildFileResult:
    return _build_from_ctx(
        g=g,
        tg=tg,
        import_path=import_path,
        root_ctx=parse_file(path),
        file_path=path,
    )


def build_source(
    *,
    g: graph.GraphView,
    tg: fbrk.TypeGraph,
    source: str,
    import_path: str | None = None,
    source_path: Path | None = None,
    extra_types: set[type[fabll.Node]] | None = None,
) -> BuildFileResult:
    if import_path is None:
        import uuid

        import_path = f"__source_{uuid.uuid4().hex[:8]}__.ato"

    return _build_from_ctx(
        g=g,
        tg=tg,
        import_path=import_path,
        root_ctx=parse_text_as_file(source, src_path=source_path or import_path),
        file_path=source_path,
        extra_types=extra_types,
    )


def build_stage_2(
    g: graph.GraphView,
    tg: fbrk.TypeGraph,
    linker: Linker,
    result: BuildFileResult,
    validate: bool = True,
) -> None:
    from atopile.compiler.deferred_executor import DeferredExecutor

    linker.link_imports(g, result.state)
    DeferredExecutor(
        g=g,
        tg=tg,
        state=result.state,
        visitor=result.visitor,
        stdlib=linker._stdlib,
        file_imports=linker,
    ).execute()

    if validate:
        with accumulate() as accumulator:
            types_to_validate: list[tuple[Path | None, graph.BoundNode]] = []

            # 1. Add types from imported files
            for file_path, types in linker._linked_modules.items():
                for _, type_node in types.items():
                    types_to_validate.append((file_path, type_node))

            # 2. Add types from the entry file if not already covered
            entry_file_path = (
                linker._resolver._normalize_path(result.state.file_path)
                if result.state.file_path
                else None
            )
            if entry_file_path is None or entry_file_path not in linker._linked_modules:
                for _, type_node in result.state.type_roots.items():
                    types_to_validate.append((entry_file_path, type_node))

            for file_path, type_node in types_to_validate:
                for error_node, message in tg.validate_type(type_node=type_node):
                    with accumulator.collect():
                        source_chunk = ASTVisitor.get_source_chunk(error_node)
                        raise DslRichException(
                            message,
                            original=DslException(message),
                            source_node=source_chunk,
                            traceback=[],
                        )
