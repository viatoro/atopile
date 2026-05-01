from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import faebryk.core.faebrykpy as fbrk
import faebryk.core.graph as graph
import faebryk.core.node as fabll
from atopile.compiler.ast_visitor import STDLIB_TYPES
from atopile.compiler.build import Linker, StdlibRegistry, build_file
from atopile.config import config, find_project_dir
from atopile.errors import DowngradedExceptionCollector, UserException
from atopile.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class WarmCompilerBase:
    graph_view: graph.GraphView
    type_graph: fbrk.TypeGraph
    prelinked_module_type_ids: dict[Path, dict[str, str]] = field(default_factory=dict)

    def borrow_forked_graph(self) -> tuple[graph.GraphView, fbrk.TypeGraph]:
        return self.graph_view, self.type_graph

    def create_working_graph(self) -> tuple[graph.GraphView, fbrk.TypeGraph]:
        g = graph.GraphView.create()
        tg = self.type_graph.copy_into(target_graph=g, minimal=False)
        return g, tg

    def create_linked_modules_seed(
        self, tg: fbrk.TypeGraph
    ) -> dict[Path, dict[str, graph.BoundNode]]:
        seed: dict[Path, dict[str, graph.BoundNode]] = {}
        for path, type_ids in self.prelinked_module_type_ids.items():
            resolved_types: dict[str, graph.BoundNode] = {}
            for name, type_identifier in type_ids.items():
                type_node = tg.get_type_by_name(type_identifier=type_identifier)
                if type_node is not None:
                    resolved_types[name] = type_node
            if resolved_types:
                seed[path] = resolved_types
        return seed


def _preload_stdlib_modules(
    *,
    g: graph.GraphView,
    tg: fbrk.TypeGraph,
    stdlib: StdlibRegistry,
) -> dict[Path, dict[str, str]]:
    from atopile.compiler.deferred_executor import DeferredExecutor

    linker = Linker(config_obj=None, stdlib=stdlib, tg=tg)
    prelinked: dict[Path, dict[str, str]] = {}

    for stdlib_path in linker._stdlib_ato_files.values():
        child_result = build_file(
            g=g,
            tg=tg,
            import_path=str(stdlib_path),
            path=stdlib_path,
        )
        linker._link_recursive(g, child_result.state)
        DeferredExecutor(
            g=g,
            tg=tg,
            state=child_result.state,
            visitor=child_result.visitor,
            stdlib=stdlib,
            file_imports=linker,
        ).execute()
        linker._linked_modules[stdlib_path] = child_result.state.type_roots
        prelinked[stdlib_path] = {
            name: fbrk.TypeGraph.get_type_name(type_node=type_node)
            for name, type_node in child_result.state.type_roots.items()
        }

    return prelinked


def _iter_project_module_roots() -> list[Path]:
    env_value = os.environ.get("ATO_LSP_PRELOAD_PROJECT_ROOT")
    if not env_value:
        return []

    project_root = Path(env_value).expanduser().resolve()
    if not project_root.exists():
        return []
    if find_project_dir(project_root) != project_root:
        logger.debug(
            "Skipping workspace module preload for non-project root %s",
            project_root,
        )
        return []

    module_roots: list[Path] = []
    for dirpath, dirnames, _filenames in os.walk(project_root):
        current = Path(dirpath)
        if current.name == ".git":
            dirnames[:] = []
            continue
        if current.name in {"__pycache__", ".venv", "node_modules", "dist", "zig-out"}:
            dirnames[:] = []
            continue
        if current.name == "modules" and current.parent.name == ".ato":
            module_roots.append(current.resolve())
            dirnames[:] = []
    return module_roots


def _preload_workspace_modules(
    *,
    g: graph.GraphView,
    tg: fbrk.TypeGraph,
    stdlib: StdlibRegistry,
    seed_modules: dict[Path, dict[str, str]],
) -> dict[Path, dict[str, str]]:
    from atopile.compiler.deferred_executor import DeferredExecutor

    prelinked = dict(seed_modules)
    seen_module_paths = set(prelinked)

    def sync_prelinked_from_linker(linker: Linker) -> None:
        for linked_path, type_roots in linker._linked_modules.items():
            if linked_path in seen_module_paths:
                continue
            prelinked[linked_path] = {
                name: fbrk.TypeGraph.get_type_name(type_node=type_node)
                for name, type_node in type_roots.items()
            }
            seen_module_paths.add(linked_path)

    for modules_root in _iter_project_module_roots():
        project_root = modules_root.parent.parent
        try:
            config.apply_options(entry=None, working_dir=project_root)
        except Exception:
            continue

        linker = Linker(
            config_obj=config,
            stdlib=stdlib,
            tg=tg,
            linked_modules_seed={
                path: {
                    name: type_node
                    for name, type_identifier in type_ids.items()
                    if (
                        type_node := tg.get_type_by_name(
                            type_identifier=type_identifier
                        )
                    )
                    is not None
                }
                for path, type_ids in prelinked.items()
            },
        )

        for module_path in sorted(modules_root.rglob("*.ato")):
            module_path = module_path.resolve()
            if (
                module_path in seen_module_paths
                or module_path in linker._linked_modules
            ):
                continue
            try:
                child_result = build_file(
                    g=g,
                    tg=tg,
                    import_path=str(module_path),
                    path=module_path,
                )
                linker._link_recursive(g, child_result.state)
                DeferredExecutor(
                    g=g,
                    tg=tg,
                    state=child_result.state,
                    visitor=child_result.visitor,
                    stdlib=stdlib,
                    file_imports=linker,
                ).execute()
                linker._linked_modules[module_path] = child_result.state.type_roots
                sync_prelinked_from_linker(linker)
            except Exception as exc:
                logger.debug(
                    "Skipping warm preload for workspace module %s: %s",
                    module_path,
                    exc,
                )

    return prelinked


def _build_warm_base() -> WarmCompilerBase:
    g = graph.GraphView.create()
    tg = fbrk.TypeGraph.create(g=g)
    stdlib = StdlibRegistry(tg)

    for node_type in STDLIB_TYPES:
        fabll.TypeNodeBoundTG.get_or_create_type_in_tg(tg, node_type)
    for node_type in STDLIB_TYPES:
        stdlib.get(node_type._type_identifier())

    with DowngradedExceptionCollector(
        UserException, suppress_logging=True
    ) as collector:
        prelinked_modules = _preload_stdlib_modules(g=g, tg=tg, stdlib=stdlib)
        prelinked_modules = _preload_workspace_modules(
            g=g,
            tg=tg,
            stdlib=stdlib,
            seed_modules=prelinked_modules,
        )

    for error, severity_level in collector:
        logger.debug("Suppressed warm-base warning (%s): %s", severity_level, error)

    return WarmCompilerBase(
        graph_view=g,
        type_graph=tg,
        prelinked_module_type_ids=prelinked_modules,
    )


WARM_COMPILER_BASE = _build_warm_base()
