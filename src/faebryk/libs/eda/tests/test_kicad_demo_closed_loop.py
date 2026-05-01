"""Closed-loop KiCad demo validation against exported schematic netlists."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from sexpdata import Symbol, loads

from faebryk.libs.eda.hl.convert.eda_file_hl import Schematic
from faebryk.libs.eda.hl.convert.schematic import convert_schematic_to_netlist
from faebryk.libs.eda.hl.convert.schematic_netlist import _synthetic_net_name
from faebryk.libs.eda.hl.models.netlist import Netlist

_KICAD_CLI = shutil.which("kicad-cli")
_WORKSPACE_ROOT = Path(__file__).resolve().parents[7]
_DEFAULT_MANIFEST = _WORKSPACE_ROOT / ".local/kicad_demos/manifest.json"


def _net_sort_key(
    entry: tuple[str | None, tuple[tuple[str, str], ...]],
) -> tuple[int, str, tuple[tuple[str, str], ...]]:
    name, terminals = entry
    return (0 if name is not None else 1, name or "", terminals)


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


def _export_schematic_netlist(schematic_path: Path) -> str:
    if _KICAD_CLI is None:
        pytest.skip("kicad-cli is not installed")
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "netlist.net"
        completed = subprocess.run(
            [
                _KICAD_CLI,
                "sch",
                "export",
                "netlist",
                str(schematic_path),
                "--format",
                "kicadsexpr",
                "-o",
                str(output_path),
            ],
            cwd=schematic_path.parent,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, (
            f"kicad-cli netlist export failed for {schematic_path}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
        return output_path.read_text(encoding="utf-8")


def _symbol_name(value: object) -> str | None:
    if isinstance(value, Symbol):
        return value.value()
    return None


def _canonical_export_netlist(
    netlist_text: str,
) -> tuple[tuple[str, tuple[tuple[str, str], ...]], ...]:
    root = loads(netlist_text)
    nets_expr = next(
        (
            expr
            for expr in root
            if isinstance(expr, list) and expr and _symbol_name(expr[0]) == "nets"
        ),
        None,
    )
    assert nets_expr is not None, "Expected a nets section in exported netlist"

    entries = [
        (
            _normalize_net_name(str(name_expr[1])),
            tuple(
                sorted(
                    {
                        (str(ref_expr[1]), str(pin_expr[1]))
                        for node_expr in net_expr[1:]
                        if isinstance(node_expr, list)
                        and node_expr
                        and _symbol_name(node_expr[0]) == "node"
                        for ref_expr in node_expr[1:]
                        if isinstance(ref_expr, list)
                        and ref_expr
                        and _symbol_name(ref_expr[0]) == "ref"
                        for pin_expr in node_expr[1:]
                        if isinstance(pin_expr, list)
                        and pin_expr
                        and _symbol_name(pin_expr[0]) == "pin"
                    }
                )
            ),
        )
        for net_expr in nets_expr[1:]
        if isinstance(net_expr, list)
        and net_expr
        and _symbol_name(net_expr[0]) == "net"
        for name_expr in net_expr[1:]
        if isinstance(name_expr, list)
        and name_expr
        and _symbol_name(name_expr[0]) == "name"
        and len(name_expr) >= 2
    ]
    return tuple(sorted(entries, key=_net_sort_key))


def _canonical_hl_netlist(
    netlist: Netlist,
) -> tuple[tuple[str | None, tuple[tuple[str, str], ...]], ...]:
    normalized: list[tuple[str | None, tuple[tuple[str, str], ...]]] = []
    for net in netlist.nets:
        terminals = tuple(
            sorted(
                {
                    (
                        str(terminal.owner_name or terminal.owner_id or ""),
                        terminal.terminal_id,
                    )
                    for terminal in net.terminals
                    if terminal.kind in {"schematic_pin", "pcb_pad"}
                    and (terminal.owner_name or terminal.owner_id)
                }
            )
        )
        if not terminals:
            continue
        synthetic_name = _synthetic_net_name(list(net.terminals))
        normalized.append(
            (
                _normalize_net_name(None if net.name == synthetic_name else net.name),
                terminals,
            )
        )
    return tuple(sorted(normalized, key=_net_sort_key))


def _normalize_net_name(name: str | None) -> str | None:
    if name is None:
        return None
    if name.startswith("Net-(") or name.startswith("unconnected-("):
        return None
    return name


def _pretty_diff(
    expected: tuple[tuple[str | None, tuple[tuple[str, str], ...]], ...],
    actual: tuple[tuple[str | None, tuple[tuple[str, str], ...]], ...],
) -> str:
    expected_set = set(expected)
    actual_set = set(actual)
    missing = sorted(expected_set - actual_set, key=_net_sort_key)
    extra = sorted(actual_set - expected_set, key=_net_sort_key)
    return f"missing={missing[:10]} extra={extra[:10]}"


def test_kicad_demo_projects_match_exported_schematic_netlist() -> None:
    manifest_path = _manifest_path()
    demos = _load_demo_manifest()
    exercised = 0
    skipped: list[str] = []

    for demo in demos:
        schematic_path = _resolve_demo_path(manifest_path, demo.get("schematic_path"))
        repo_dir = _resolve_demo_path(manifest_path, demo.get("repo_dir"))
        label = str(demo.get("repo_url") or repo_dir)

        assert schematic_path.exists(), (
            f"{label}: schematic not found: {schematic_path}"
        )

        exported = _canonical_export_netlist(_export_schematic_netlist(schematic_path))
        schematic_hl = _canonical_hl_netlist(
            convert_schematic_to_netlist(Schematic.decode(schematic_path))
        )

        assert schematic_hl == exported, (
            f"{label}: schematic HL mismatch vs kicad-cli export\n"
            f"{_pretty_diff(exported, schematic_hl)}"
        )
        exercised += 1

    if exercised == 0:
        pytest.skip(
            f"No supported KiCad demo projects were available. Skipped: {skipped}"
        )
