from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from faebryk.libs.eda.hl.convert.pcb_netlist import convert_pcb_to_netlist
from faebryk.libs.eda.kicad.convert.pcb.il_hl import convert_pcb_il_to_hl
from faebryk.libs.kicad.fileformats import kicad

_WORKSPACE_ROOT = Path(__file__).resolve().parents[7]
_DEFAULT_MANIFEST = _WORKSPACE_ROOT / ".local/kicad_demos/manifest.json"


def _manifest_path() -> Path:
    configured = os.environ.get("KICAD_DEMO_MANIFEST")
    return Path(configured) if configured else _DEFAULT_MANIFEST


def _load_demo_manifest() -> list[dict[str, object]]:
    manifest_path = _manifest_path()
    if not manifest_path.exists():
        pytest.skip(f"KiCad demo manifest not found: {manifest_path}")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise AssertionError(f"Expected a JSON list in {manifest_path}")
    return payload


def _resolve_demo_path(manifest_path: Path, value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise AssertionError(f"Invalid manifest path entry: {value!r}")
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return (manifest_path.parent / candidate).resolve()


def _pcb_version(pcb_path: Path) -> int | None:
    match = re.search(
        r"\(version\s+(\d+)\)",
        pcb_path.read_text(encoding="utf-8"),
    )
    if match is None:
        return None
    return int(match.group(1))


def _supported_v9_demo_pcbs() -> list[Path]:
    manifest_path = _manifest_path()
    demos = _load_demo_manifest()
    supported: list[Path] = []

    for demo in demos:
        pcb_path = _resolve_demo_path(manifest_path, demo.get("pcb_path"))
        assert pcb_path.exists(), f"PCB not found: {pcb_path}"
        if _pcb_version(pcb_path) == 20241229:
            supported.append(pcb_path)

    return supported


def test_modern_pcb_footprint_metadata_property_parses_without_geometry() -> None:
    pcb = kicad.loads(
        kicad.pcb.PcbFile,
        """
        (kicad_pcb
            (version 20241229)
            (generator "pcbnew")
            (generator_version "9.0")
            (general
                (thickness 1)
                (legacy_teardrops no)
            )
            (paper "A4")
            (layers
                (0 "F.Cu" signal)
                (2 "B.Cu" signal)
                (5 "F.SilkS" user "F.Silkscreen")
                (25 "Edge.Cuts" user)
                (35 "F.Fab" user)
            )
            (setup
                (pcbplotparams
                    (layerselection 0x00010fc_ffffffff)
                    (outputdirectory "")
                )
            )
            (footprint "Resistor_SMD:R_0402_1005Metric"
                (layer "F.Cu")
                (uuid "0095d3c2-3443-4ef0-bd21-227a46d64e0a")
                (at 153.21 82.4)
                (descr "fixture")
                (tags "fixture")
                (property "Reference" "R1"
                    (at 0 -1.17 0)
                    (layer "F.SilkS")
                    (uuid "3f0376ae-8735-4d23-95a6-986c091de5eb")
                    (effects
                        (font
                            (size 0.7 0.7)
                            (thickness 0.15)
                        )
                    )
                )
                (property ki_fp_filters "R_*")
                (path "/demo")
                (sheetname "/")
                (sheetfile "demo.kicad_sch")
                (attr smd)
                (fp_text user "${REFERENCE}"
                    (at 0 0 0)
                    (layer "F.Fab")
                    (effects
                        (font
                            (size 0.7 0.7)
                            (thickness 0.15)
                        )
                    )
                )
                (pad "1" smd rect
                    (at -0.5 0)
                    (size 0.5 0.5)
                    (layers "F.Cu" "F.Paste" "F.Mask")
                )
            )
        )
        """,
    ).kicad_pcb

    footprint = pcb.footprints[0]
    assert footprint.propertys[1].name == "ki_fp_filters"
    assert footprint.propertys[1].value == "R_*"
    assert footprint.propertys[1].at is None
    assert footprint.propertys[1].layer is None


def test_demo_v9_pcb_projects_parse_and_project_to_hl() -> None:
    supported = _supported_v9_demo_pcbs()
    exercised = 0

    for pcb_path in supported:
        pcb = kicad.loads(kicad.pcb.PcbFile, pcb_path).kicad_pcb
        hl = convert_pcb_il_to_hl(pcb)

        assert len(pcb.footprints) > 0, f"{pcb_path}: no footprints parsed"
        assert len(hl.collections) == len(pcb.footprints), (
            f"{pcb_path}: HL footprint projection mismatch"
        )
        exercised += 1

    if exercised == 0:
        pytest.skip("No supported v9 KiCad PCB demo projects were available")


def test_small_v9_pcb_demo_projects_reconstruct_netlists() -> None:
    supported = sorted(
        _supported_v9_demo_pcbs(),
        key=lambda pcb_path: pcb_path.stat().st_size,
    )
    if not supported:
        pytest.skip("No supported v9 KiCad PCB demo projects were available")

    pcb_path = supported[0]
    pcb = kicad.loads(kicad.pcb.PcbFile, pcb_path).kicad_pcb
    hl = convert_pcb_il_to_hl(pcb)
    netlist = convert_pcb_to_netlist(hl)

    assert len(netlist.nets) > 0, f"{pcb_path}: no PCB nets reconstructed"
