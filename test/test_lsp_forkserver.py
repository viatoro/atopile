from __future__ import annotations

import os
import time
from contextlib import suppress
from pathlib import Path

import lsprotocol.types as lsp
import pytest

import atopile.lsp.forkbase_preload as forkbase_preload
import atopile.lsp.lsp_server as lsp_server
import faebryk.core.faebrykpy as fbrk
from atopile.lsp.forkserver_broker import BROKER, ManagedDocument
from atopile.lsp.forkserver_protocol import CompileSummary, WorkerResponse


@pytest.fixture(autouse=True)
def _broker_lifecycle():
    BROKER.shutdown()
    BROKER.initialize(None)
    try:
        yield
    finally:
        BROKER.shutdown()


def _write_file(path: Path, source: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    return path.resolve().as_uri()


def _wait_until(predicate, *, timeout: float = 5.0, interval: float = 0.05) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError("condition not met before timeout")


def _write_project_ato_yaml(project_root: Path) -> None:
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "ato.yaml").write_text(
        "requires-atopile: ^0.14.0\npaths:\n  src: .\n"
    )


def _write_package_ato_yaml(
    package_root: Path,
    identifier: str,
    *,
    dependencies: list[str] | None = None,
) -> None:
    lines = [
        "requires-atopile: ^0.14.0",
        "paths:",
        "  src: .",
        "package:",
        f"  identifier: {identifier}",
    ]
    if dependencies:
        lines.extend(["dependencies:"])
        for dep in dependencies:
            lines.extend(
                [
                    "- type: registry",
                    f"  identifier: {dep}",
                    "  release: 0.1.0",
                ]
            )
    lines.append("")
    package_root.mkdir(parents=True, exist_ok=True)
    (package_root / "ato.yaml").write_text("\n".join(lines))


def test_broker_compiles_and_serves_hover(tmp_path: Path):
    uri = _write_file(
        tmp_path / "app.ato",
        "import Resistor\n\nmodule App:\n    r1 = new Resistor\n",
    )

    state = BROKER.open_or_update(uri, Path(tmp_path / "app.ato").read_text(), 1)

    assert state.diagnostics == []

    result = BROKER.query(uri, "hover", {"line": 0, "character": 8})

    assert isinstance(result, lsp.Hover)
    BROKER.close(uri)


def test_buffer_edits_recycle_worker_after_single_compile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    app_path = tmp_path / "app.ato"
    uri = app_path.resolve().as_uri()
    doc = ManagedDocument(
        uri=uri,
        path=app_path.resolve(),
        source="import Resistor\n\nmodule App:\n    r1 = new Resistor\n",
    )
    BROKER._docs[uri] = doc
    retire_count = 0
    summary = CompileSummary(
        diagnostics=[],
        dependency_paths=[],
        query_capabilities=["completion"],
    )

    def wrapped_retire() -> None:
        nonlocal retire_count
        retire_count += 1
        BROKER._compile_count = 0

    monkeypatch.setattr(BROKER, "_configure_preload_project_locked", lambda _path: None)
    monkeypatch.setattr(BROKER, "_ensure_active_compile_worker_locked", lambda: None)
    monkeypatch.setattr(
        BROKER,
        "_send_request_with_timings_locked",
        lambda _request: (WorkerResponse(ok=True, result=summary), 0.0, 0.0),
    )
    monkeypatch.setattr(BROKER, "_refresh_dependency_watches_locked", lambda: None)
    monkeypatch.setattr(BROKER, "_ensure_idle_pool_locked", lambda sync: None)
    monkeypatch.setattr(BROKER, "_retire_active_worker_locked", wrapped_retire)

    for compile_idx in range(2):
        if compile_idx > 0:
            doc.source = (
                "import Resistor\n\nmodule App:\n"
                f"    r1 = new Resistor\n    r{compile_idx + 1} = new Resistor\n"
            )
            BROKER._bump_dirty(doc, reason=f"buffer update version={compile_idx + 1}")
        BROKER._compile_locked(doc)
        if compile_idx < 1:
            assert retire_count == 0
        else:
            assert retire_count == 1

    BROKER.close(uri)


def test_queries_reuse_worker_for_current_generation(tmp_path: Path):
    app_path = tmp_path / "app.ato"
    uri = _write_file(
        app_path,
        "import Resistor\n\nmodule App:\n    r1 = new Resistor\n",
    )

    BROKER.open_or_update(uri, app_path.read_text(), 1)
    assert BROKER._process is not None
    pid_before = BROKER._process.pid
    epoch_before = BROKER._epoch

    hover = BROKER.query(uri, "hover", {"line": 0, "character": 8})

    assert isinstance(hover, lsp.Hover)
    assert BROKER._process is not None
    assert BROKER._process.pid == pid_before
    assert BROKER._epoch == epoch_before
    BROKER.close(uri)


def test_worker_restart_does_not_dirty_unrelated_documents(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    primary_path = tmp_path / "primary.ato"
    secondary_path = tmp_path / "secondary.ato"
    primary_uri = primary_path.resolve().as_uri()
    secondary_uri = secondary_path.resolve().as_uri()

    primary = ManagedDocument(
        uri=primary_uri,
        path=primary_path.resolve(),
        source="module Primary:\n    pass\n",
        requested_generation=3,
        compiled_generation=3,
        dirty=False,
        dirty_reason=None,
        query_capabilities={"hover"},
    )
    secondary = ManagedDocument(
        uri=secondary_uri,
        path=secondary_path.resolve(),
        source="module Secondary:\n    pass\n",
        requested_generation=7,
        compiled_generation=7,
        dirty=False,
        dirty_reason=None,
        query_capabilities={"hover"},
    )
    BROKER._docs = {primary_uri: primary, secondary_uri: secondary}
    monkeypatch.setattr(BROKER, "_stop_worker", lambda: None)
    monkeypatch.setattr(BROKER, "_ensure_idle_pool_locked", lambda sync: None)

    BROKER._restart_worker_locked()

    assert not primary.dirty
    assert not secondary.dirty
    assert primary.compiled_generation == 3
    assert secondary.compiled_generation == 7
    assert primary.query_capabilities == {"hover"}
    assert secondary.query_capabilities == {"hover"}


def test_query_restart_only_invalidates_target_document(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    primary_path = tmp_path / "primary.ato"
    secondary_path = tmp_path / "secondary.ato"
    primary_uri = primary_path.resolve().as_uri()
    secondary_uri = secondary_path.resolve().as_uri()

    primary = ManagedDocument(
        uri=primary_uri,
        path=primary_path.resolve(),
        source="module Primary:\n    pass\n",
        requested_generation=2,
        compiled_generation=2,
        dirty=False,
        dirty_reason=None,
        query_capabilities={"code_action"},
    )
    secondary = ManagedDocument(
        uri=secondary_uri,
        path=secondary_path.resolve(),
        source="module Secondary:\n    pass\n",
        requested_generation=5,
        compiled_generation=5,
        dirty=False,
        dirty_reason=None,
        query_capabilities={"code_action"},
    )
    BROKER._docs = {primary_uri: primary, secondary_uri: secondary}

    compile_summary = CompileSummary(
        diagnostics=[],
        dependency_paths=[],
        query_capabilities=["code_action"],
    )
    query_attempts = 0

    def fake_query_send(_request):
        nonlocal query_attempts
        query_attempts += 1
        if query_attempts == 1:
            return WorkerResponse(ok=False, error="generation mismatch")
        return WorkerResponse(ok=True, result={"ok": True})

    monkeypatch.setattr(BROKER, "_configure_preload_project_locked", lambda _path: None)
    monkeypatch.setattr(BROKER, "_ensure_active_compile_worker_locked", lambda: None)
    monkeypatch.setattr(BROKER, "_refresh_dependency_watches_locked", lambda: None)
    monkeypatch.setattr(BROKER, "_ensure_idle_pool_locked", lambda sync: None)
    monkeypatch.setattr(BROKER, "_stop_worker", lambda: None)
    monkeypatch.setattr(BROKER, "_send_request_locked", fake_query_send)
    monkeypatch.setattr(
        BROKER,
        "_send_request_with_timings_locked",
        lambda _request: (WorkerResponse(ok=True, result=compile_summary), 0.0, 0.0),
    )

    result = BROKER.query(primary_uri, "code_action", {})

    assert result == {"ok": True}
    assert not primary.dirty
    assert primary.compiled_generation == primary.requested_generation
    assert not secondary.dirty
    assert secondary.compiled_generation == 5
    assert secondary.query_capabilities == {"code_action"}


def test_same_path_watcher_invalidation_is_ignored_when_overlay_matches_disk(
    tmp_path: Path,
):
    app_path = tmp_path / "app.ato"
    source = "import Resistor\n\nmodule App:\n    r1 = new Resistor\n"
    uri = _write_file(app_path, source)

    doc = BROKER.open_or_update(uri, source, 1)
    assert not doc.dirty
    generation_before = doc.requested_generation

    BROKER.invalidate_by_path(app_path)

    doc = BROKER.get_document(uri)
    assert doc is not None
    assert not doc.dirty
    assert doc.requested_generation == generation_before
    BROKER.close(uri)


def test_modules_file_watcher_invalidation_is_ignored_when_overlay_matches_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    project_root = tmp_path / "project"
    module_path = project_root / ".ato" / "modules" / "vendor" / "pkg" / "part.ato"
    source = "module Part:\n    pass\n"
    uri = _write_file(module_path, source)

    invalidated_paths: list[Path] = []

    def tracking_invalidate(path: Path) -> None:
        invalidated_paths.append(path)

    monkeypatch.setattr(BROKER, "_invalidate_forkbase_locked", tracking_invalidate)

    doc = BROKER.open_or_update(uri, source, 1)
    assert not doc.dirty
    generation_before = doc.requested_generation

    BROKER.invalidate_by_path(module_path)

    doc = BROKER.get_document(uri)
    assert doc is not None
    assert not doc.dirty
    assert doc.requested_generation == generation_before
    assert invalidated_paths == []
    BROKER.close(uri)


def test_dependency_watcher_invalidates_external_dependency(tmp_path: Path):
    workspace_root = tmp_path / "workspace"
    external_root = tmp_path / "external"
    dep_path = external_root / "dep.ato"
    app_path = workspace_root / "app.ato"

    BROKER.shutdown()
    BROKER.initialize(workspace_root.resolve().as_uri())

    _write_file(dep_path, "module Part:\n    pass\n")
    uri = _write_file(
        app_path,
        'from "../external/dep.ato" import Part\n\nmodule App:\n    p = new Part\n',
    )

    doc = BROKER.open_or_update(uri, app_path.read_text(), 1)
    assert dep_path.resolve() in doc.dependency_paths
    assert dep_path.parent.resolve() in BROKER._dependency_watch_roots

    dep_path.write_text("module Part:\n    value: V\n")

    _wait_until(
        lambda: BROKER.get_document(uri) is not None and BROKER.get_document(uri).dirty
    )  # type: ignore[union-attr]
    BROKER.close(uri)


def test_broker_recovers_after_worker_death_and_recompiles(tmp_path: Path):
    app_path = tmp_path / "app.ato"
    uri = _write_file(
        app_path,
        "import Resistor\n\nmodule App:\n    r1 = new Resistor\n",
    )

    BROKER.open_or_update(uri, app_path.read_text(), 1)
    assert BROKER._process is not None
    old_pid = BROKER._process.pid
    BROKER._process.terminate()
    BROKER._process.join(timeout=2)
    if BROKER._process.is_alive():
        BROKER._process.kill()
        BROKER._process.join(timeout=2)
    assert not BROKER._process.is_alive()

    result = BROKER.query(uri, "hover", {"line": 0, "character": 8})

    assert isinstance(result, lsp.Hover)
    assert BROKER._process is not None
    assert BROKER._process.is_alive()
    assert BROKER._process.pid != old_pid
    doc = BROKER.get_document(uri)
    assert doc is not None
    assert doc.compiled_generation == doc.requested_generation
    BROKER.close(uri)


def test_failed_generation_does_not_answer_from_last_good(tmp_path: Path):
    app_path = tmp_path / "app.ato"
    uri = _write_file(
        app_path,
        "import Resistor\n\nmodule App:\n    r1 = new Resistor\n",
    )

    doc = BROKER.open_or_update(uri, app_path.read_text(), 1)
    assert "hover" in doc.query_capabilities
    assert isinstance(
        BROKER.query(uri, "hover", {"line": 0, "character": 8}), lsp.Hover
    )

    app_path.write_text("module App:\n    r1 = new\n")
    doc = BROKER.open_or_update(uri, app_path.read_text(), 2)

    assert doc.compiled_generation == doc.requested_generation
    assert doc.diagnostics
    assert BROKER.query(uri, "hover", {"line": 1, "character": 11}) is None
    BROKER.close(uri)


def test_failed_generation_still_supports_current_generation_completion(tmp_path: Path):
    app_path = tmp_path / "app.ato"
    uri = _write_file(
        app_path,
        "import Resistor\n\nmodule App:\n    r1 = new Resistor\n",
    )

    doc = BROKER.open_or_update(uri, app_path.read_text(), 1)
    assert "completion" in doc.query_capabilities

    app_path.write_text(
        "import Resistor\n\nmodule App:\n    r1 = new Resistor\n    r1.\n"
    )
    doc = BROKER.open_or_update(uri, app_path.read_text(), 2)

    assert doc.compiled_generation == doc.requested_generation
    assert "completion" in doc.query_capabilities
    result = BROKER.query(uri, "completion", {"line": 4, "character": 7})

    assert isinstance(result, lsp.CompletionList)
    assert any(item.label == "resistance" for item in result.items)
    BROKER.close(uri)


def test_build_document_records_phase_timings(monkeypatch, tmp_path: Path):
    app_path = tmp_path / "app.ato"
    source = "import Resistor\n\nmodule App:\n    r1 = new Resistor\n"
    uri = _write_file(app_path, source)
    calls: list[str] = []

    monkeypatch.setattr(
        lsp_server,
        "_validate_field_references",
        lambda *args, **kwargs: calls.append("field") or [],
    )

    if uri in lsp_server.DOCUMENT_STATES:
        lsp_server.DOCUMENT_STATES[uri].reset_graph()
        del lsp_server.DOCUMENT_STATES[uri]

    state = lsp_server.build_document(uri, source, source_path=app_path)

    assert calls == ["field"]
    assert state.profile_counts["field_ref_diagnostics"] == 0
    assert "graph_prep" in state.phase_timings_ms
    assert "cache_clear" in state.phase_timings_ms
    assert "parse" in state.phase_timings_ms
    assert "build" in state.phase_timings_ms
    assert "link" in state.phase_timings_ms
    assert "field_validation" in state.phase_timings_ms
    assert "total" in state.phase_timings_ms

    if uri in lsp_server.DOCUMENT_STATES:
        lsp_server.DOCUMENT_STATES[uri].reset_graph()
        del lsp_server.DOCUMENT_STATES[uri]


def test_broker_records_phase_timings(tmp_path: Path):
    app_path = tmp_path / "app.ato"
    uri = _write_file(
        app_path,
        "import Resistor\n\nmodule App:\n    r1 = new Resistor\n",
    )

    doc = BROKER.open_or_update(uri, app_path.read_text(), 1)
    assert doc.phase_timings_ms["total"] >= 0
    assert "build" in doc.phase_timings_ms
    assert "field_validation" in doc.phase_timings_ms
    assert doc.startup_timings_ms["worker_rss_before_compile_mb"] >= 0
    assert doc.startup_timings_ms["worker_rss_after_compile_mb"] >= 0
    assert doc.startup_timings_ms["worker_units_prewarm"] >= 0
    assert "worker_rss_delta_mb" in doc.startup_timings_ms
    assert doc.profile_counts["field_ref_diagnostics"] >= 0
    assert isinstance(doc.compiled_file_timings_ms, dict)


def test_project_warm_preload_has_no_unresolved_refs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    project_root = tmp_path / "project"
    _write_project_ato_yaml(project_root)
    module_root = project_root / ".ato" / "modules" / "vendor" / "pkg"
    _write_package_ato_yaml(module_root, "vendor/pkg")
    (module_root / "pkg.ato").write_text("module Driver:\n    pass\n")
    monkeypatch.setenv("ATO_LSP_PRELOAD_PROJECT_ROOT", str(project_root))

    warm_base = forkbase_preload._build_warm_base()
    try:
        unresolved = fbrk.Linker.collect_unresolved_type_references(
            type_graph=warm_base.type_graph
        )
        assert unresolved == []
        assert any(
            ".ato/modules" in str(path) for path in warm_base.prelinked_module_type_ids
        )
    finally:
        with suppress(Exception):
            warm_base.graph_view.destroy()


def test_non_project_workspace_root_skips_nested_project_module_preload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    workspace_root = tmp_path / "workspace"
    project_root = workspace_root / "examples" / "i2c"
    _write_project_ato_yaml(project_root)
    module_root = project_root / ".ato" / "modules" / "vendor" / "pkg"
    _write_package_ato_yaml(module_root, "vendor/pkg")
    (module_root / "pkg.ato").write_text("module Driver:\n    pass\n")
    monkeypatch.setenv("ATO_LSP_PRELOAD_PROJECT_ROOT", str(workspace_root))

    warm_base = forkbase_preload._build_warm_base()
    try:
        unresolved = fbrk.Linker.collect_unresolved_type_references(
            type_graph=warm_base.type_graph
        )
        assert unresolved == []
        assert not any(
            path.is_relative_to(workspace_root)
            for path in warm_base.prelinked_module_type_ids
        )
    finally:
        with suppress(Exception):
            warm_base.graph_view.destroy()


def test_infer_preload_project_root_for_modules_file(tmp_path: Path):
    project_root = tmp_path / "project"
    module_file = (
        project_root / ".ato" / "modules" / "vendor" / "pkg" / "parts" / "thing.ato"
    )
    module_file.parent.mkdir(parents=True, exist_ok=True)
    module_file.write_text("module Thing:\n    pass\n")

    inferred = BROKER._infer_preload_project_root(module_file)

    assert inferred == project_root.resolve()


def test_infer_preload_project_root_ignores_non_modules_file(tmp_path: Path):
    project_root = tmp_path / "project"
    app_file = project_root / "src" / "app.ato"
    app_file.parent.mkdir(parents=True, exist_ok=True)
    app_file.write_text("module App:\n    pass\n")

    inferred = BROKER._infer_preload_project_root(app_file)

    assert inferred is None


def test_configure_preload_project_root_keeps_workspace_root_for_non_modules_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    workspace_root = tmp_path / "workspace"
    app_a = workspace_root / "examples" / "i2c" / "i2c.ato"
    app_b = workspace_root / "examples" / "esp32_minimal" / "esp32_minimal.ato"
    app_a.parent.mkdir(parents=True, exist_ok=True)
    app_b.parent.mkdir(parents=True, exist_ok=True)
    app_a.write_text("module AppA:\n    pass\n")
    app_b.write_text("module AppB:\n    pass\n")

    BROKER._workspace_root = workspace_root.resolve()
    monkeypatch.setenv("ATO_LSP_PRELOAD_PROJECT_ROOT", str(workspace_root.resolve()))

    stop_calls = 0
    shutdown_calls = 0

    def fake_stop_worker() -> None:
        nonlocal stop_calls
        stop_calls += 1

    def fake_shutdown_forkserver() -> None:
        nonlocal shutdown_calls
        shutdown_calls += 1

    monkeypatch.setattr(BROKER, "_stop_worker", fake_stop_worker)
    monkeypatch.setattr(
        "atopile.lsp.forkserver_broker.shutdown_forkserver", fake_shutdown_forkserver
    )
    BROKER._mp_ctx = object()

    BROKER._configure_preload_project_locked(app_a)
    BROKER._configure_preload_project_locked(app_b)

    assert os.environ["ATO_LSP_PRELOAD_PROJECT_ROOT"] == str(workspace_root.resolve())
    assert stop_calls == 0
    assert shutdown_calls == 0


def test_configure_preload_project_root_keeps_workspace_root_for_modules_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    workspace_root = tmp_path / "workspace"
    module_file = (
        workspace_root
        / "examples"
        / "esp32_minimal"
        / ".ato"
        / "modules"
        / "vendor"
        / "pkg"
        / "pkg.ato"
    )
    module_file.parent.mkdir(parents=True, exist_ok=True)
    module_file.write_text("module Pkg:\n    pass\n")

    BROKER._workspace_root = workspace_root.resolve()
    monkeypatch.setenv("ATO_LSP_PRELOAD_PROJECT_ROOT", str(workspace_root.resolve()))

    stop_calls = 0
    shutdown_calls = 0

    def fake_stop_worker() -> None:
        nonlocal stop_calls
        stop_calls += 1

    def fake_shutdown_forkserver() -> None:
        nonlocal shutdown_calls
        shutdown_calls += 1

    monkeypatch.setattr(BROKER, "_stop_worker", fake_stop_worker)
    monkeypatch.setattr(
        "atopile.lsp.forkserver_broker.shutdown_forkserver", fake_shutdown_forkserver
    )
    BROKER._mp_ctx = object()

    BROKER._configure_preload_project_locked(module_file)

    assert os.environ["ATO_LSP_PRELOAD_PROJECT_ROOT"] == str(workspace_root.resolve())
    assert stop_calls == 0
    assert shutdown_calls == 0


def test_initialize_none_clears_stale_workspace_root(tmp_path: Path):
    stale_root = tmp_path / "old-workspace"
    fresh_root = tmp_path / "fresh-workspace"
    stale_root.mkdir(parents=True, exist_ok=True)
    fresh_root.mkdir(parents=True, exist_ok=True)

    BROKER._workspace_root = stale_root.resolve()

    BROKER.initialize(None)
    try:
        assert BROKER._workspace_root is None
    finally:
        BROKER.shutdown()

    BROKER.initialize(fresh_root.resolve().as_uri())
    try:
        assert BROKER._workspace_root == fresh_root.resolve()
    finally:
        BROKER.shutdown()


def test_initialize_does_not_create_idle_pool(monkeypatch: pytest.MonkeyPatch):
    idle_pool_calls: list[bool] = []
    get_mp_context_calls = 0

    def fake_get_mp_context(*, forkserver_preload):
        nonlocal get_mp_context_calls
        get_mp_context_calls += 1
        return object()

    monkeypatch.setattr(
        BROKER,
        "_ensure_idle_pool_locked",
        lambda *, sync: idle_pool_calls.append(sync),
    )
    monkeypatch.setattr(BROKER, "_ensure_watcher_locked", lambda: None)
    monkeypatch.setattr(
        "atopile.lsp.forkserver_broker.get_mp_context",
        fake_get_mp_context,
    )
    BROKER._mp_ctx = None

    BROKER.initialize(None)

    assert idle_pool_calls == []
    assert get_mp_context_calls == 0
    assert BROKER._mp_ctx is None


def test_build_document_uses_owning_project_root_for_modules_file(tmp_path: Path):
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "ato.yaml").write_text(
        "requires-atopile: ^0.14.0\npaths:\n  src: .\n"
    )

    buttons_root = project_root / ".ato" / "modules" / "vendor" / "buttons"
    buttons_root.mkdir(parents=True, exist_ok=True)
    (buttons_root / "ato.yaml").write_text(
        "\n".join(
            [
                "requires-atopile: ^0.14.0",
                "paths:",
                "  src: .",
                "package:",
                "  identifier: vendor/buttons",
                "",
            ]
        )
    )
    (buttons_root / "buttons.ato").write_text("module Button:\n    pass\n")

    pkg_root = project_root / ".ato" / "modules" / "vendor" / "pkg"
    pkg_root.mkdir(parents=True, exist_ok=True)
    (pkg_root / "ato.yaml").write_text(
        "\n".join(
            [
                "requires-atopile: ^0.14.0",
                "paths:",
                "  src: .",
                "package:",
                "  identifier: vendor/pkg",
                "dependencies:",
                "- type: registry",
                "  identifier: vendor/buttons",
                "  release: 0.1.0",
                "",
            ]
        )
    )
    module_file = pkg_root / "pkg.ato"
    module_source = (
        'from "vendor/buttons/buttons.ato" import Button\n\n'
        "module Driver:\n"
        "    button = new Button\n"
    )
    module_file.write_text(module_source)
    uri = module_file.resolve().as_uri()

    state = lsp_server.build_document(uri, module_source, source_path=module_file)
    messages = [diagnostic.message for diagnostic in state.diagnostics]

    assert not any(
        "Unable to resolve import `vendor/buttons/buttons.ato`" in message
        for message in messages
    )

    if uri in lsp_server.DOCUMENT_STATES:
        lsp_server.DOCUMENT_STATES[uri].reset_graph()
        del lsp_server.DOCUMENT_STATES[uri]


def test_broker_modules_file_avoids_generic_unresolved_link_warning(tmp_path: Path):
    project_root = tmp_path / "project"
    _write_project_ato_yaml(project_root)
    module_root = project_root / ".ato" / "modules" / "vendor" / "pkg"
    _write_package_ato_yaml(module_root, "vendor/pkg")
    module_file = module_root / "pkg.ato"
    module_file.write_text(
        "\n".join(
            [
                "import has_single_electric_reference_shared",
                "",
                "module Driver:",
                "    trait has_single_electric_reference_shared",
                "",
            ]
        )
    )
    uri = module_file.as_uri()

    BROKER.shutdown()
    BROKER.initialize(project_root.as_uri())
    try:
        doc = BROKER.open_or_update(uri, module_file.read_text(), 1)
        messages = [diagnostic.message for diagnostic in doc.diagnostics]

        assert (
            "Import resolution: Unresolved type references remaining after linking"
            not in messages
        )
        assert not any(
            "Unable to resolve import" in message
            or "is not defined in this scope" in message
            for message in messages
        )
        assert messages == [
            "'has_single_electric_reference_shared' is deprecated. Use "
            "'has_single_electric_reference' instead.",
        ]
        assert doc.dependency_paths
    finally:
        BROKER.shutdown()


def test_broker_import_toggle_avoids_generic_unresolved_link_warning(
    tmp_path: Path,
):
    workspace_root = tmp_path / "workspace"
    project_root = workspace_root / "examples" / "i2c"
    _write_project_ato_yaml(project_root)
    app_path = project_root / "app.ato"
    app_path.write_text("module App:\n    pass\n")
    uri = app_path.as_uri()

    BROKER.shutdown()
    BROKER.initialize(workspace_root.as_uri())
    try:
        for version, source in enumerate(
            [
                "module App:\n    pass\n",
                "import X\nmodule App:\n    pass\n",
                "module App:\n    pass\n",
            ],
            start=1,
        ):
            doc = BROKER.open_or_update(uri, source, version)
            messages = [diagnostic.message for diagnostic in doc.diagnostics]
            assert (
                "Import resolution: Unresolved type references remaining after linking"
                not in messages
            )

        assert doc.diagnostics == []
    finally:
        BROKER.shutdown()


def test_broker_compiles_stdlib_ato_file_import(tmp_path: Path):
    """Broker should compile files that import from stdlib .ato files."""
    project_root = tmp_path / "project"
    _write_project_ato_yaml(project_root)

    app_path = project_root / "app.ato"
    uri = _write_file(
        app_path,
        (
            'from "regulators.ato" '
            "import FixedLDO\n\nmodule App from FixedLDO:\n    pass\n"
        ),
    )

    state = BROKER.open_or_update(uri, app_path.read_text(), 1)

    assert state.diagnostics == []

    # Hover on "App" (local type inheriting from FixedLDO) should work
    result = BROKER.query(uri, "hover", {"line": 2, "character": 8})

    assert isinstance(result, lsp.Hover)
    BROKER.close(uri)
