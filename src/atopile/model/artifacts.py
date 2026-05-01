"""Artifact domain logic for build outputs."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from atopile import config
from atopile.data_models import PinoutComponent
from atopile.model.builds import resolve_build_target_config
from faebryk.exporters.pcb.kicad.artifacts import (
    KicadCliExportError,
    export_glb,
    githash_layout,
)


def _build_config(
    project_root: str,
    target: str = "default",
) -> config.BuildTargetConfig:
    return resolve_build_target_config(project_root, target)


def read_artifact(
    project_root: str,
    target: str,
    suffix: str,
) -> dict | None:
    """Read a JSON build artifact by target name and file suffix."""
    path = _build_config(project_root, target).paths.output_base.with_suffix(suffix)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {suffix}: {exc}") from exc


def list_targets_with_artifact(project_root: str, suffix: str) -> list[str]:
    """Return build target names whose artifact file exists."""
    project_path = Path(project_root)
    if not project_path.exists():
        raise ValueError(f"Project path does not exist: {project_root}")
    project_cfg = config.ProjectConfig.from_path(project_path)
    if project_cfg is None:
        raise ValueError(f"No ato.yaml found in: {project_root}")
    return sorted(
        name
        for name, build_cfg in project_cfg.builds.items()
        if build_cfg.paths.output_base.with_suffix(suffix).exists()
    )


def get_pinout(
    project_root: str,
    target: str = "default",
) -> list[PinoutComponent] | None:
    """Read per-component pinout JSON artifacts for a build target."""
    build_dir = _build_config(project_root, target).paths.output_base.parent
    pinout_dir = build_dir / "pinout"
    if not pinout_dir.exists():
        return None
    results: list[PinoutComponent] = []
    for path in sorted(pinout_dir.glob("*.json")):
        data = json.loads(path.read_text()) if path.exists() else {}
        results.append(PinoutComponent.model_validate({**data, "id": path.stem}))
    return results


def generate_3d_model(project_root: str, target: str = "default") -> dict[str, str]:
    """Generate a GLB preview from the target's existing layout."""
    build_cfg = _build_config(project_root, target)
    layout_path = build_cfg.paths.layout
    if not layout_path.exists():
        raise FileNotFoundError(
            f"Layout file not found: {layout_path}\n\n"
            "Run a full build first to generate the layout."
        )

    glb_path = build_cfg.paths.output_base.with_suffix(".pcba.glb")
    glb_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="atopile-glb-") as temp_dir:
        temp_layout = Path(temp_dir) / layout_path.name
        githash_layout(layout_path, temp_layout)
        try:
            export_glb(temp_layout, glb_file=glb_path, project_dir=layout_path.parent)
        except KicadCliExportError as exc:
            raise ValueError(f"Failed to generate 3D model: {exc}") from exc

    return {"modelPath": str(glb_path)}
