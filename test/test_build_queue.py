from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

import atopile.model.build_queue as build_queue_module
import atopile.model.sqlite as sqlite_module
from atopile.buildutil import generate_build_id
from atopile.cli import build as cli_build
from atopile.data_models import (
    Build,
    BuildRequest,
    BuildStage,
    BuildStatus,
    ResolvedBuildTarget,
    StageStatus,
)
from atopile.model import builds as builds_domain
from atopile.model.build_queue import BuildQueue
from atopile.model.sqlite import BuildHistory


@pytest.fixture()
def isolated_build_history(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "build_history.db"
    monkeypatch.setattr(sqlite_module, "BUILD_HISTORY_DB", db_path)
    monkeypatch.setattr(build_queue_module, "BUILD_HISTORY_DB", db_path)
    BuildHistory.init_db()
    yield


def _make_build(project_root: Path, name: str) -> Build:
    started_at = 1000.0 + len(name)
    target = ResolvedBuildTarget(root=str(project_root), name=name)
    return Build(
        build_id=generate_build_id(str(project_root), name, started_at),
        name=name,
        project_root=str(project_root),
        target=target,
        status=BuildStatus.QUEUED,
        started_at=started_at,
    )


def _complete_launch(launched: list[str]):
    def _launch(build_or_queue: BuildQueue | Build, build: Build | None = None) -> None:
        build = build_or_queue if isinstance(build_or_queue, Build) else build
        assert build is not None
        launched.append(build.build_id)
        existing = BuildHistory.get(build.build_id)
        assert existing is not None
        building = existing.model_copy(
            update={
                "status": BuildStatus.BUILDING,
                "stages": [
                    BuildStage(
                        name="Compile",
                        stage_id="compile",
                        status=StageStatus.RUNNING,
                    )
                ],
            }
        )
        BuildHistory.set(building)
        BuildHistory.set(
            building.model_copy(
                update={
                    "status": BuildStatus.SUCCESS,
                    "return_code": 0,
                    "stages": [
                        BuildStage(
                            name="Compile",
                            stage_id="compile",
                            status=StageStatus.SUCCESS,
                        )
                    ],
                }
            )
        )

    return _launch


def test_cli_single_build_starts(
    tmp_path: Path, monkeypatch, isolated_build_history
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    build = _make_build(project_root, "default")
    launched: list[str] = []

    monkeypatch.setattr(
        cli_build,
        "BuildPrinter",
        MagicMock(spec=cli_build.BuildPrinter),
    )
    monkeypatch.setattr(BuildQueue, "_launch_build", _complete_launch(launched))

    results = cli_build._run_build_queue([build], jobs=1, verbose=False)

    assert launched == [build.build_id]
    assert results == {build.build_id: 0}


def test_extension_single_build_starts(
    tmp_path: Path, monkeypatch, isolated_build_history
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "ato.yaml").write_text("builds: {}\n", encoding="utf-8")
    target = ResolvedBuildTarget(root=str(project_root), name="default")
    launched: list[str] = []

    monkeypatch.setattr(
        builds_domain._build_queue,
        "_launch_build",
        _complete_launch(launched),
    )
    monkeypatch.setattr(builds_domain._build_queue, "_running", True)

    submitted = builds_domain.handle_start_build(
        BuildRequest(project_root=str(project_root), targets=[target])
    )

    assert launched == [submitted[0].build_id]


def test_cli_multi_build_starts(
    tmp_path: Path, monkeypatch, isolated_build_history
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    builds = [
        _make_build(project_root, "default"),
        _make_build(project_root, "mfg"),
    ]
    launched: list[str] = []

    monkeypatch.setattr(
        cli_build,
        "BuildPrinter",
        MagicMock(spec=cli_build.BuildPrinter),
    )
    monkeypatch.setattr(BuildQueue, "_launch_build", _complete_launch(launched))

    results = cli_build._run_build_queue(builds, jobs=2, verbose=False)

    assert launched == [build.build_id for build in builds]
    assert results == {build.build_id: 0 for build in builds}


def test_extension_multi_build_starts(
    tmp_path: Path, monkeypatch, isolated_build_history
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "ato.yaml").write_text("builds: {}\n", encoding="utf-8")
    targets = [
        ResolvedBuildTarget(root=str(project_root), name="default"),
        ResolvedBuildTarget(root=str(project_root), name="mfg"),
    ]
    launched: list[str] = []

    monkeypatch.setattr(
        builds_domain._build_queue,
        "_launch_build",
        _complete_launch(launched),
    )
    monkeypatch.setattr(builds_domain._build_queue, "_running", True)

    submitted = builds_domain.handle_start_build(
        BuildRequest(project_root=str(project_root), targets=targets)
    )

    assert launched == [build.build_id for build in submitted]


def test_extension_standalone_build_starts(
    tmp_path: Path, monkeypatch, isolated_build_history
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    entry = project_root / "main.ato"
    entry.write_text("component App:\n    pass\n", encoding="utf-8")
    launched: list[str] = []

    monkeypatch.setattr(
        builds_domain._build_queue,
        "_launch_build",
        _complete_launch(launched),
    )
    monkeypatch.setattr(builds_domain._build_queue, "_running", True)

    submitted = builds_domain.handle_start_build(
        BuildRequest(
            project_root=str(project_root),
            entry="main.ato:App",
            standalone=True,
        )
    )

    assert len(submitted) == 1
    assert launched == [submitted[0].build_id]
    assert submitted[0].standalone is True
    assert submitted[0].target.root == str(project_root)
    assert submitted[0].target.entry == "main.ato:App"
