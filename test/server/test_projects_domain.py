import subprocess
from pathlib import Path

import yaml

from atopile.data_models import UiProjectState
from atopile.model import projects as projects_domain
from faebryk.libs.package.dist import Dist


def _write_project_config(project_root: Path, *, entry: str = "main.ato:App") -> None:
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "ato.yaml").write_text(
        f"""
requires-atopile: ^0.14.0
builds:
  default:
    entry: {entry}
""".strip()
    )


def test_discover_projects_includes_nested_package_targets(tmp_path: Path):
    project_root = tmp_path / "auto-picking"
    _write_project_config(project_root)

    package_root = project_root / "packages" / "rp2040"
    package_root.mkdir(parents=True)
    (package_root / "ato.yaml").write_text(
        """
requires-atopile: ^0.14.0
builds:
  package:
    entry: rp2040.ato:RP2040
""".strip()
    )

    skipped_root = project_root / ".ato" / "modules" / "vendor" / "ignored"
    skipped_root.mkdir(parents=True)
    (skipped_root / "ato.yaml").write_text(
        """
requires-atopile: ^0.14.0
builds:
  hidden:
    entry: hidden.ato:Hidden
""".strip()
    )

    projects = projects_domain.discover_projects_in_paths([project_root])

    assert len(projects) == 1
    assert [target.name for target in projects[0].targets] == ["default", "package"]
    assert projects[0].targets[0].root == str(project_root)
    assert projects[0].targets[1].root == str(package_root)


def test_create_local_package_uses_layouts_directory(tmp_path: Path):
    project_root = tmp_path / "demo"
    project_root.mkdir()
    (project_root / "ato.yaml").write_text(
        """
requires-atopile: ^0.14.0
paths:
  src: ./
  layout: ./layouts
builds:
  default:
    entry: main.ato:App
""".strip()
    )

    package = projects_domain.create_local_package(project_root, "rp2040", "RP2040")

    package_root = Path(package["path"])
    package_ato = yaml.safe_load(
        (package_root / "ato.yaml").read_text(encoding="utf-8")
    )

    assert (package_root / "layouts").is_dir()
    assert not (package_root / "elec").exists()
    assert package_ato["paths"]["layout"] == "./layouts"


def test_refresh_project_adds_new_project_to_list(tmp_path: Path):
    existing_root = tmp_path / "existing"
    new_root = tmp_path / "new-project"
    _write_project_config(existing_root)
    _write_project_config(new_root)

    existing = projects_domain.handle_get_project(str(existing_root))
    assert existing is not None

    refreshed, replacement = projects_domain.refresh_project([existing], str(new_root))

    assert replacement is not None
    assert [project.root for project in refreshed] == [
        str(existing_root),
        str(new_root),
    ]


def test_refresh_project_removes_missing_project_from_list(tmp_path: Path):
    project_root = tmp_path / "demo"
    _write_project_config(project_root)

    existing = projects_domain.handle_get_project(str(project_root))
    assert existing is not None

    (project_root / "ato.yaml").write_text(
        """
requires-atopile: ^0.14.0
dependencies: []
""".strip()
    )

    refreshed, replacement = projects_domain.refresh_project(
        [existing], str(project_root)
    )

    assert replacement is None
    assert refreshed == []


def test_normalize_discovery_paths_deduplicates_preserving_order(tmp_path: Path):
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"

    normalized = projects_domain.normalize_discovery_paths([root_a, root_b, root_a])

    assert normalized == [root_a, root_b]


def test_normalize_project_state_repairs_selection(tmp_path: Path):
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    _write_project_config(first_root)
    _write_project_config(second_root)

    project_list = projects_domain.handle_get_projects([tmp_path]).projects
    stale_state = UiProjectState(
        selected_project_root=str(tmp_path / "missing"),
        selected_target=None,
    )

    normalized, selection_changed = projects_domain.normalize_project_state(
        project_list, stale_state
    )

    assert selection_changed is True
    assert normalized.selected_project_root == str(first_root)
    assert normalized.selected_target is not None
    assert normalized.selected_target.root == str(first_root)


def test_create_local_package_writes_file_dependency_identifier(tmp_path: Path):
    project_root = tmp_path / "demo"
    project_root.mkdir()
    (project_root / "ato.yaml").write_text(
        """
requires-atopile: ^0.14.0
paths:
  src: ./
  layout: ./layouts
builds:
  default:
    entry: main.ato:App
""".strip()
    )

    projects_domain.create_local_package(project_root, "Raspberry_Pi_RP2040", "RP2040")

    project_ato = yaml.safe_load(
        (project_root / "ato.yaml").read_text(encoding="utf-8")
    )

    assert project_ato["dependencies"] == [
        {
            "type": "file",
            "path": "./packages/Raspberry_Pi_RP2040",
            "identifier": "local/raspberry-pi-rp2040",
        }
    ]


def test_create_local_package_backfills_existing_file_dependency_identifier(
    tmp_path: Path,
):
    project_root = tmp_path / "demo"
    project_root.mkdir()
    (project_root / "ato.yaml").write_text(
        """
requires-atopile: ^0.14.0
paths:
  src: ./
  layout: ./layouts
builds:
  default:
    entry: main.ato:App
dependencies:
  - type: file
    path: ./packages/Raspberry_Pi_RP2040
""".strip()
    )

    projects_domain.create_local_package(project_root, "Raspberry_Pi_RP2040", "RP2040")

    project_ato = yaml.safe_load(
        (project_root / "ato.yaml").read_text(encoding="utf-8")
    )

    assert project_ato["dependencies"] == [
        {
            "type": "file",
            "path": "./packages/Raspberry_Pi_RP2040",
            "identifier": "local/raspberry-pi-rp2040",
        }
    ]


def test_create_local_package_can_build_dist_without_repository(tmp_path: Path):
    project_root = tmp_path / "demo"
    project_root.mkdir()
    subprocess.run(["git", "init"], cwd=project_root, check=True, capture_output=True)
    (project_root / "ato.yaml").write_text(
        """
requires-atopile: ^0.14.0
paths:
  src: ./
  layout: ./layouts
builds:
  default:
    entry: main.ato:App
""".strip()
    )

    package = projects_domain.create_local_package(project_root, "rp2040", "RP2040")

    dist = Dist.build_dist(Path(package["path"]), tmp_path / "dist")

    assert dist.identifier == "local/rp2040"
