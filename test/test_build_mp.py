"""Tests for multiprocessing build workers."""

import os
import shutil
import sys
from pathlib import Path

import pytest

from faebryk.libs.util import app_root as _app_root
from faebryk.libs.util import run_live

pytestmark = pytest.mark.easyeda

EXAMPLES_DIR = _app_root() / "examples"
AUTO_PICKING = EXAMPLES_DIR / "auto-picking"


@pytest.fixture()
def example_project(tmp_path: Path) -> Path:
    """Copy the auto-picking example to a temp dir so builds don't interfere."""
    dest = tmp_path / "auto-picking"
    shutil.copytree(AUTO_PICKING, dest)
    return dest


def _run_build(example_project: Path, env_override: dict | None = None):
    env = {**os.environ, "NONINTERACTIVE": "1", **(env_override or {})}
    stdout, stderr, _ = run_live(
        [sys.executable, "-m", "atopile", "build", "-b", "auto-pick"],
        env=env,
        cwd=example_project,
        stdout=print,
        stderr=print,
        timeout=120,
    )
    combined = stdout + stderr
    assert "Build successful!" in combined
    assert "starting worker" in combined


def test_build_with_mp(example_project: Path):
    """Build completes successfully via multiprocessing worker."""
    _run_build(example_project)


@pytest.mark.skipif(sys.platform != "linux", reason="fork only safe on Linux")
def test_build_with_fork(example_project: Path):
    """Build using fork start method."""
    _run_build(example_project, {"ATO_MP_START_METHOD": "fork"})


@pytest.mark.skipif(sys.platform != "linux", reason="forkserver on Linux only in CI")
def test_build_with_forkserver(example_project: Path):
    """Build using forkserver start method."""
    _run_build(example_project, {"ATO_MP_START_METHOD": "forkserver"})


@pytest.mark.skipif(sys.platform != "linux", reason="spawn is slow, test on Linux CI")
def test_build_with_spawn(example_project: Path):
    """Build using spawn start method (slowest, but works everywhere)."""
    _run_build(example_project, {"ATO_MP_START_METHOD": "spawn"})
