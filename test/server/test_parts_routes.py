from __future__ import annotations

from pathlib import Path

import pytest

from atopile import errors
from atopile.model import parts


def _mock_convert_deps(
    monkeypatch,
    project_root: Path,
    *,
    base_name: str = "Raspberry_Pi_RP2040",
    seed_installed: bool = True,
) -> None:
    """Stub out the filesystem side-effects of convert_to_package_raw.

    When seed_installed is True, list_installed reports the part as already
    installed at the project root (the precondition convert_to_package_raw
    checks). install_raw / uninstall_raw / create_local_package are mocked
    so the test only exercises the orchestration.
    """

    installed = (
        [{"identifier": base_name, "lcsc": "C2040", "path": ""}]
        if seed_installed
        else []
    )
    monkeypatch.setattr(
        parts.ProjectParts,
        "list_installed",
        lambda project_root: list(installed),
    )

    def _fake_create_local_package(
        project_root_arg, name, entry_module, description=None
    ):
        package_root = Path(project_root_arg) / "packages" / name
        package_root.mkdir(parents=True)
        wrapper_path = package_root / f"{name}.ato"
        return {
            "path": str(package_root),
            "module_path": str(wrapper_path),
            "identifier": f"local/{name.lower().replace('_', '-')}",
            "import_statement": (
                f'from "local/{name.lower().replace("_", "-")}/{name}.ato" '
                f"import {entry_module}"
            ),
        }

    monkeypatch.setattr(
        "atopile.model.projects.create_local_package",
        _fake_create_local_package,
    )
    monkeypatch.setattr(
        parts.ProjectParts,
        "install_raw",
        lambda lcsc_id, package_root: {
            "identifier": base_name,
            "path": f"{package_root}/parts/{base_name}",
        },
    )
    monkeypatch.setattr(
        parts.ProjectParts,
        "uninstall_raw",
        lambda lcsc_id, project_root: {"identifier": base_name, "path": ""},
    )


def test_convert_to_package_creates_wrapper(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    _mock_convert_deps(monkeypatch, project_root)

    response = parts.ProjectParts.convert_to_package_raw(
        "C2040",
        str(project_root),
    )

    wrapper_path = (
        project_root / "packages" / "Raspberry_Pi_RP2040" / "Raspberry_Pi_RP2040.ato"
    )

    assert response["created_package"] is True
    assert response["identifier"] == "local/raspberry-pi-rp2040"
    assert response["import_statement"] == (
        'from "local/raspberry-pi-rp2040/Raspberry_Pi_RP2040.ato" '
        "import Raspberry_Pi_RP2040"
    )
    assert wrapper_path.read_text(encoding="utf-8") == (
        'from "parts/Raspberry_Pi_RP2040/Raspberry_Pi_RP2040.ato" import '
        "Raspberry_Pi_RP2040_package\n\n"
        "module Raspberry_Pi_RP2040:\n"
        "    package = new Raspberry_Pi_RP2040_package\n"
    )


def test_convert_to_package_auto_suffixes_on_collision(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "packages" / "Raspberry_Pi_RP2040").mkdir(parents=True)
    _mock_convert_deps(monkeypatch, project_root)

    response = parts.ProjectParts.convert_to_package_raw(
        "C2040",
        str(project_root),
    )

    assert response["entry_module"] == "Raspberry_Pi_RP2040_2"
    assert response["identifier"] == "local/raspberry-pi-rp2040-2"
    wrapper_path = (
        project_root
        / "packages"
        / "Raspberry_Pi_RP2040_2"
        / "Raspberry_Pi_RP2040_2.ato"
    )
    assert wrapper_path.exists()


def test_convert_to_package_uninstalls_original(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    _mock_convert_deps(monkeypatch, project_root)

    uninstalled: list[tuple[str, str]] = []

    def _fake_uninstall_raw(lcsc_id: str, project_root_arg: str) -> dict:
        uninstalled.append((lcsc_id, project_root_arg))
        return {"identifier": "Raspberry_Pi_RP2040", "path": ""}

    monkeypatch.setattr(parts.ProjectParts, "uninstall_raw", _fake_uninstall_raw)

    parts.ProjectParts.convert_to_package_raw("C2040", str(project_root))

    assert uninstalled == [("C2040", str(project_root))]


def test_convert_to_package_rejects_when_part_not_installed(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    _mock_convert_deps(monkeypatch, project_root, seed_installed=False)

    with pytest.raises(errors.UserException):
        parts.ProjectParts.convert_to_package_raw(
            "C2040",
            str(project_root),
        )


def test_install_as_package_runs_install_then_convert(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """install_as_package is thin wrapper: install_raw + convert_to_package_raw."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    # Seed the list_installed mock as empty; install_raw call should flip it.
    _mock_convert_deps(monkeypatch, project_root, seed_installed=False)

    installed_calls: list[tuple[str, str]] = []

    def _fake_install_raw(lcsc_id: str, project_root_arg: str) -> dict:
        installed_calls.append((lcsc_id, project_root_arg))
        # After install_raw runs, list_installed should see the part.
        monkeypatch.setattr(
            parts.ProjectParts,
            "list_installed",
            lambda project_root: [
                {"identifier": "Raspberry_Pi_RP2040", "lcsc": "C2040", "path": ""}
            ],
        )
        return {
            "identifier": "Raspberry_Pi_RP2040",
            "path": f"{project_root_arg}/parts/Raspberry_Pi_RP2040",
        }

    monkeypatch.setattr(parts.ProjectParts, "install_raw", _fake_install_raw)

    response = parts.ProjectParts.install_as_package("C2040", str(project_root))

    assert response["created_package"] is True
    # Root install, then package install — two install_raw invocations.
    assert len(installed_calls) == 2
    assert installed_calls[0][1] == str(project_root)
    assert installed_calls[1][1].endswith("packages/Raspberry_Pi_RP2040")
