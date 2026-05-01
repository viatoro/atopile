"""Regression tests for KiCad schematic netlist reconstruction."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from sexpdata import Symbol, loads

from faebryk.libs.eda.hl.convert.eda_file_hl import Schematic
from faebryk.libs.eda.hl.convert.schematic_netlist import convert_schematic_to_netlist

_KICAD_CLI = shutil.which("kicad-cli")
_WORKSPACE_ROOT = Path(__file__).resolve().parents[7]
_QA_ROOT = _WORKSPACE_ROOT / ".local/kicad/qa/data/eeschema/netlists"


def _symbol_name(value: object) -> str | None:
    if isinstance(value, Symbol):
        return value.value()
    return None


def _exported_netlist(
    schematic_path: Path,
) -> tuple[tuple[str, tuple[tuple[str, str], ...]], ...]:
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
        root = loads(output_path.read_text(encoding="utf-8"))

    nets_expr = next(
        (
            expr
            for expr in root
            if isinstance(expr, list) and expr and _symbol_name(expr[0]) == "nets"
        ),
        None,
    )
    assert nets_expr is not None, f"Expected a nets section in {schematic_path}"

    nets: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    for net_expr in nets_expr[1:]:
        if not isinstance(net_expr, list) or not net_expr:
            continue
        if _symbol_name(net_expr[0]) != "net":
            continue

        name: str | None = None
        terminals: set[tuple[str, str]] = set()
        for child in net_expr[1:]:
            if not isinstance(child, list) or not child:
                continue
            child_head = _symbol_name(child[0])
            if child_head == "name" and len(child) >= 2 and isinstance(child[1], str):
                name = child[1]
                continue
            if child_head != "node":
                continue
            ref: str | None = None
            pin: str | None = None
            for node_child in child[1:]:
                if not isinstance(node_child, list) or not node_child:
                    continue
                node_head = _symbol_name(node_child[0])
                if node_head == "ref" and len(node_child) >= 2:
                    ref = str(node_child[1])
                elif node_head == "pin" and len(node_child) >= 2:
                    pin = str(node_child[1])
            if ref is not None and pin is not None:
                terminals.add((ref, pin))
        if name:
            nets.append((name, tuple(sorted(terminals))))

    return tuple(sorted(nets))


def _hl_netlist(
    schematic_path: Path,
) -> tuple[tuple[str, tuple[tuple[str, str], ...]], ...]:
    netlist = convert_schematic_to_netlist(Schematic.decode(schematic_path))
    return tuple(
        sorted(
            (
                net.name,
                tuple(
                    sorted(
                        {
                            (
                                str(terminal.owner_name or terminal.owner_id),
                                terminal.terminal_id,
                            )
                            for terminal in net.terminals
                            if terminal.kind == "schematic_pin"
                            and (terminal.owner_name or terminal.owner_id)
                        }
                    )
                ),
            )
            for net in netlist.nets
            if net.name
        )
    )


@pytest.mark.parametrize(
    ("fixture_dir", "schematic_name"),
    [
        ("bus_connection", "bus_connection.kicad_sch"),
        ("top_level_hier_pins", "top_level_hier_pins.kicad_sch"),
        ("issue14657", "issue14657.kicad_sch"),
    ],
)
def test_kicad_hierarchical_schematic_matches_kicad_cli_export(
    fixture_dir: str, schematic_name: str
) -> None:
    schematic_path = _QA_ROOT / fixture_dir / schematic_name
    if not schematic_path.exists():
        pytest.skip(f"fixture not found: {schematic_path}")

    assert _hl_netlist(schematic_path) == _exported_netlist(schematic_path)
