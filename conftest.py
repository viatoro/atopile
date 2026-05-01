import logging
import os
import shutil
from pathlib import Path

import pathvalidate
import pytest

from atopile.logging import AtoLogger
from atopile.telemetry.config import ENABLE_TELEMETRY
from faebryk.libs.util import ConfigFlag, robustly_rm_dir
from faebryk.libs.util import repo_root as _repo_root

SKIP_EASYEDA = ConfigFlag(
    "SKIP_EASYEDA",
    default=False,
    descr="Skip tests that require the EasyEDA API to be reachable",
)

# Disable telemetry for testing
ENABLE_TELEMETRY.set(False)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "max_parallel(n): limit concurrent execution to n tests in this group",
    )
    config.addinivalue_line(
        "markers",
        "worker_affinity(separator): route parametrized tests with the same "
        "prefix (split on separator) to the same worker process",
    )
    config.addinivalue_line(
        "markers",
        "ato_logging(kind=None, identifier=None, context='', reset_root=False): "
        "configure the ato_logging_context fixture",
    )
    worker_id = os.environ.get("PYTEST_XDIST_WORKER")
    if worker_id is not None:
        logging.basicConfig(
            format=config.getini("log_file_format"),
            filename=Path("artifacts") / f"tests_{worker_id}.log",
            level=config.getini("log_file_level"),
        )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip easyeda-marked tests when FBRK_SKIP_EASYEDA is set, and emit
    orchestrator metadata during --collect-only."""
    if SKIP_EASYEDA:
        skip_marker = pytest.mark.skip(reason="FBRK_SKIP_EASYEDA is set")
        for item in items:
            if item.get_closest_marker("easyeda"):
                item.add_marker(skip_marker)

    if not config.option.collectonly:
        return
    seen: set[str] = set()
    for item in items:
        marker = item.get_closest_marker("max_parallel")
        if marker and marker.args:
            # Use file nodeid prefix as the group key
            prefix = item.nodeid.split("::", 1)[0] + "::"
            if prefix not in seen:
                seen.add(prefix)
                print(f"@max_parallel:{prefix}{marker.args[0]}")

    # Emit @worker_affinity lines for the orchestrator
    for item in items:
        marker = item.get_closest_marker("worker_affinity")
        if marker:
            separator: str = marker.kwargs.get("separator", ":")
            # Extract parametrize ID from nodeid (text between [ and ])
            bracket_start = item.nodeid.rfind("[")
            bracket_end = item.nodeid.rfind("]")
            if bracket_start < 0 or bracket_end < 0:
                continue
            param_id = item.nodeid[bracket_start + 1 : bracket_end]
            group_key = param_id.split(separator, 1)[0]
            print(f"@worker_affinity:{group_key}|{item.nodeid}")


@pytest.fixture()
def repo_root() -> Path:
    """Fixture providing the repository root path."""
    return _repo_root()


@pytest.fixture()
def setup_project_config(tmp_path):
    from atopile.config import ProjectConfig, ProjectPaths, config

    config.project = ProjectConfig.skeleton(
        entry="", paths=ProjectPaths(build=tmp_path / "build", root=tmp_path)
    )
    yield


@pytest.fixture()
def save_tmp_path_on_failure(tmp_path: Path, request: pytest.FixtureRequest):
    try:
        yield
    except Exception:
        node_name = str(request.node.name)
        safe_node_name = pathvalidate.sanitize_filename(node_name)
        artifact_path = _repo_root() / "artifacts" / safe_node_name
        if artifact_path.exists():
            robustly_rm_dir(artifact_path)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(tmp_path, artifact_path, ignore=shutil.ignore_patterns(".git"))

        raise


# Prevents test isolation issues
# FIXME
@pytest.fixture(autouse=True)
def clear_node_type_caches():
    """
    Clearing type caches avoids false hits when the same UUID is reused in a new graph.

    Note: We don't clear TypeNodeBoundTG.__TYPE_NODE_MAP__ because it's keyed by
    BoundNode, which includes graph identity
    """
    from faebryk.core.node import Node

    def _clear_type_caches():
        for cls in list(Node._seen_types.values()):
            if hasattr(cls, "_type_cache"):
                cls._type_cache.clear()
        Node._seen_types.clear()

    _clear_type_caches()
    yield
    _clear_type_caches()


@pytest.fixture(autouse=True)
def ato_logging_context(request: pytest.FixtureRequest):
    """
    Isolate global logging state for tests with explicit context activation.

    Configure behavior via:
    - marker: `@pytest.mark.ato_logging(...)`
    - defaults: unscoped context, no root logger reset
    - override: pass `kind=None` in marker kwargs to disable context activation
    """
    marker = request.node.get_closest_marker("ato_logging")
    if marker is None:
        options = {}
    else:
        options = marker.kwargs

    kind = options.get("kind", "unscoped")
    identifier = options.get("identifier", request.node.name)
    context = options.get("context", "")
    reset_root = bool(options.get("reset_root", False))

    with AtoLogger.test_context(
        kind=kind,
        identifier=identifier,
        context=context,
        reset_root=reset_root,
    ) as root:
        yield root


# Enable this to force GC collection after each test
# Useful for debugging memory leaks and segfaults on GC
# @pytest.hookimpl(tryfirst=True)
# def pytest_runtest_teardown(item, nextitem):
#    import gc
#
#    gc.collect()
