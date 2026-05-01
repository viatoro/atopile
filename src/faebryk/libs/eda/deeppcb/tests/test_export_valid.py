"""Validate that HL → DeepPCB export produces well-formed board JSON.

Two levels of validation:
1. **Offline** — Pydantic model validation (structure, types, enums).
2. **Online** — DeepPCB API ``check_board`` (semantic: net refs, padstack
   IDs, layer indices, placement feasibility).

Online tests require ``DEEPPCB_API_KEY`` in the environment and are skipped
otherwise.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from faebryk.libs.eda.deeppcb.convert import file_ll
from faebryk.libs.eda.deeppcb.convert.il_hl import convert_hl_to_ll
from faebryk.libs.eda.deeppcb.tests.constants import (
    ALL_BOARDS,
    API_BOARDS,
    BOARD_EXPECTATIONS,
    HAS_API_KEY,
    REF_BOARDS,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_kicad_hl(path: Path):
    from faebryk.libs.eda.kicad.convert.pcb.il_hl import convert_pcb_il_to_hl
    from faebryk.libs.kicad.fileformats import kicad

    pcb_file = kicad.loads(kicad.pcb.PcbFile, path)
    return convert_pcb_il_to_hl(pcb_file.kicad_pcb)


def _export_to_deeppcb_dict(kicad_path: Path) -> dict:
    """KiCad → HL → DeepPCB LL → JSON dict."""
    hl = _load_kicad_hl(kicad_path)
    ll_board = convert_hl_to_ll(hl)
    json_str = file_ll.dump(ll_board)
    return json.loads(json_str)


def _validate_internal_refs(board_dict: dict) -> list[str]:
    """Check that cross-references within the board are consistent.

    Returns a list of error strings (empty = all good).
    """
    errors: list[str] = []

    padstack_ids = {ps["id"] for ps in board_dict.get("padstacks", [])}
    comp_def_ids = {cd["id"] for cd in board_dict.get("componentDefinitions", [])}
    component_ids = {c["id"] for c in board_dict.get("components", [])}
    num_layers = len(board_dict.get("layers", []))

    # Components reference valid definitions
    for comp in board_dict.get("components", []):
        if comp["definition"] not in comp_def_ids:
            errors.append(
                f"Component {comp['id']!r} references unknown definition "
                f"{comp['definition']!r}"
            )

    # Pins reference valid padstacks
    for cd in board_dict.get("componentDefinitions", []):
        for pin in cd.get("pins", []):
            if pin["padstack"] not in padstack_ids:
                errors.append(
                    f"Pin {pin['id']!r} in definition {cd['id']!r} references "
                    f"unknown padstack {pin['padstack']!r}"
                )

    # Net pins reference valid components
    for net in board_dict.get("nets", []):
        for pin_ref in net.get("pins", []):
            # Format: "component_id-pin_id"
            parts = pin_ref.rsplit("-", 1)
            if len(parts) == 2:
                comp_id = parts[0]
                if comp_id not in component_ids:
                    errors.append(
                        f"Net {net['id']!r} pin ref {pin_ref!r} references "
                        f"unknown component {comp_id!r}"
                    )

    # Vias reference valid padstacks
    for via in board_dict.get("vias", []):
        if via["padstack"] not in padstack_ids:
            errors.append(
                f"Via at {via['position']} references unknown padstack "
                f"{via['padstack']!r}"
            )

    # Via definitions reference valid padstacks
    for via_def in board_dict.get("viaDefinitions", []):
        if via_def not in padstack_ids:
            errors.append(
                f"viaDefinitions entry {via_def!r} references unknown padstack"
            )

    # Layer indices in range
    for wire in board_dict.get("wires", []):
        if wire["layer"] >= num_layers:
            errors.append(
                f"Wire on layer {wire['layer']} but only {num_layers} layers exist"
            )

    for plane in board_dict.get("planes", []):
        if plane["layer"] >= num_layers:
            errors.append(
                f"Plane on layer {plane['layer']} but only {num_layers} layers exist"
            )

    # Padstack layer indices in range
    for ps in board_dict.get("padstacks", []):
        for layer_idx in ps.get("layers", []):
            if layer_idx >= num_layers:
                errors.append(
                    f"Padstack {ps['id']!r} references layer {layer_idx} "
                    f"but only {num_layers} layers exist"
                )

    return errors


# ---------------------------------------------------------------------------
# Offline: Pydantic model validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kicad_path", ALL_BOARDS)
def test_pydantic_valid(kicad_path: Path):
    """Export board and validate against Pydantic DeepPCBBoard model."""
    if not kicad_path.exists():
        pytest.skip("example not found")

    from atopile.autolayout.deeppcb.models import DeepPCBBoard

    board_dict = _export_to_deeppcb_dict(kicad_path)
    board = DeepPCBBoard.model_validate(board_dict)
    assert len(board.components) >= BOARD_EXPECTATIONS[kicad_path].min_components
    assert len(board.nets) > 0


# ---------------------------------------------------------------------------
# Offline: internal cross-reference validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kicad_path", ALL_BOARDS)
def test_internal_refs(kicad_path: Path):
    """Verify all cross-references are valid in the exported board."""
    if not kicad_path.exists():
        pytest.skip("example not found")

    board_dict = _export_to_deeppcb_dict(kicad_path)
    errors = _validate_internal_refs(board_dict)
    assert errors == [], "Reference errors:\n" + "\n".join(errors)


# ---------------------------------------------------------------------------
# Online: DeepPCB API check_board validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kicad_path", API_BOARDS)
def test_api_check_board(kicad_path: Path):
    """Export board and validate via DeepPCB API check_board."""
    if not HAS_API_KEY:
        pytest.skip("DEEPPCB_API_KEY not set")
    if not kicad_path.exists():
        pytest.skip("example not found")

    from atopile.autolayout.deeppcb import DeepPCBClient, JobType

    board_dict = _export_to_deeppcb_dict(kicad_path)

    with DeepPCBClient(auth_token=os.environ["DEEPPCB_AUTH_TOKEN"]) as client:
        result = client.check_board(
            json_file_path=_write_tmp_board(board_dict),
            job_type=JobType.ROUTING,
        )

    assert result.is_valid, f"API rejected board: {result.error}\n" + _format_anomalies(
        result.anomalies
    )


def _write_tmp_board(board_dict: dict) -> Path:
    import tempfile

    tmp = Path(tempfile.mktemp(suffix=".deeppcb"))
    tmp.write_text(json.dumps(board_dict, indent=2), encoding="utf-8")
    return tmp


def _format_anomalies(anomalies) -> str:
    if not anomalies:
        return ""
    lines = []
    for a in anomalies:
        lines.append(f"  [{a.severity}] {a.code}: {a.message}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Offline: structural comparison against API-generated reference boards
# ---------------------------------------------------------------------------


def _assert_structural_match(ours: dict, ref: dict, label: str) -> None:
    """Compare structural properties that must match between our export and the
    API-generated reference, ignoring coordinate convention differences (Y-sign,
    rotation representation)."""

    # Same components
    our_ids = sorted(c["id"] for c in ours["components"])
    ref_ids = sorted(c["id"] for c in ref["components"])
    assert our_ids == ref_ids, f"{label}: component IDs differ: {our_ids} vs {ref_ids}"

    # Same component sides
    for c1 in ours["components"]:
        c2 = next(c for c in ref["components"] if c["id"] == c1["id"])
        assert c1["side"] == c2["side"], (
            f"{label}: component {c1['id']} side: {c1['side']} vs {c2['side']}"
        )

    # Same net connectivity (pin sets per net).
    # Normalize pin refs: strip @N suffixes since numbering may vary.
    def _normalize_pins(pins: list[str]) -> frozenset[str]:
        return frozenset(p.split("@")[0] for p in pins)

    our_nets = {_normalize_pins(n["pins"]): n["id"] for n in ours["nets"] if n["pins"]}
    ref_nets = {_normalize_pins(n["pins"]): n["id"] for n in ref["nets"] if n["pins"]}
    assert our_nets.keys() == ref_nets.keys(), (
        f"{label}: net connectivity differs\n"
        f"  Only in ours: {our_nets.keys() - ref_nets.keys()}\n"
        f"  Only in ref:  {ref_nets.keys() - our_nets.keys()}"
    )

    # Same number of named pins per component definition.
    # Unnamed pads (padN) are structural and may not appear in the reference.
    def _named_pin_count(cd: dict) -> int:
        return sum(1 for p in cd["pins"] if not p["id"].startswith("pad"))

    our_pin_counts = {
        cd["id"]: _named_pin_count(cd) for cd in ours["componentDefinitions"]
    }
    ref_pin_counts = {
        cd["id"]: _named_pin_count(cd) for cd in ref["componentDefinitions"]
    }
    for comp in ours["components"]:
        our_count = our_pin_counts.get(comp["definition"], 0)
        ref_comp = next(c for c in ref["components"] if c["id"] == comp["id"])
        ref_count = ref_pin_counts.get(ref_comp["definition"], 0)
        assert our_count == ref_count, (
            f"{label}: component {comp['id']} named pin count: {our_count} vs "
            f"{ref_count}"
        )

    # Same padstack shape types referenced by pins
    our_ps = {ps["id"]: ps["shape"]["type"] for ps in ours["padstacks"]}
    for cd in ours["componentDefinitions"]:
        for pin in cd["pins"]:
            shape_type = our_ps.get(pin["padstack"])
            assert shape_type is not None, (
                f"{label}: pin {pin['id']} refs missing padstack {pin['padstack']}"
            )


@pytest.mark.parametrize("kicad_path,ref_path", REF_BOARDS)
def test_reference_match(kicad_path: Path, ref_path: Path):
    """Compare our export against the API-generated reference."""
    if not kicad_path.exists():
        pytest.skip("example not found")
    if not ref_path.exists():
        pytest.skip("reference fixture not found")

    ours = _export_to_deeppcb_dict(kicad_path)
    ref = json.loads(ref_path.read_text())
    _assert_structural_match(ours, ref, kicad_path.stem)
