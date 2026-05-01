from pathlib import Path

from atopile.server.ui import sidebar
from atopile.server.ui.store import Store


def _write_project(root: Path, ato_source: str) -> None:
    (root / "src").mkdir()
    (root / "ato.yaml").write_text(
        """
requires-atopile: ^0.14.0
paths:
  src: ./src
builds:
  default:
    entry: main.ato:App
""".strip()
    )
    (root / "src" / "main.ato").write_text(ato_source)


def test_structure_refresh_recovers_after_introspection_failure(
    tmp_path: Path,
) -> None:
    _write_project(
        tmp_path,
        """
module App:
    x = App
""".strip(),
    )
    store = Store()

    sidebar.refresh_project_structure_data(store, str(tmp_path))

    failed = store.get("structure_data")
    assert failed.project_root == str(tmp_path)
    assert failed.modules == []
    assert failed.total == 0
    assert failed.loading is False
    assert failed.error is not None
    assert "disabled until you press Refresh" in failed.error

    (tmp_path / "src" / "main.ato").write_text(
        """
module App:
    pass
""".strip()
    )

    sidebar.refresh_project_structure_data(store, str(tmp_path))

    recovered = store.get("structure_data")
    assert recovered.project_root == str(tmp_path)
    assert recovered.loading is False
    assert recovered.error is None
    assert recovered.total == 1
    assert [module.name for module in recovered.modules] == ["App"]
