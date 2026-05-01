"""CLI tests for faebryk.libs.eda."""

from __future__ import annotations

from typer.testing import CliRunner

import faebryk.libs.eda.__main__ as eda_main
from faebryk.libs.eda.hl.models.netlist import Net, Netlist, TerminalRef

runner = CliRunner()


def _example_netlist() -> Netlist:
    return Netlist(
        nets=[
            Net(
                id="net-gnd",
                name="GND",
                aliases=["GROUND"],
                terminals=[
                    TerminalRef(
                        kind="schematic_pin",
                        owner_id="u1",
                        owner_name="U1",
                        terminal_id="1",
                    ),
                    TerminalRef(
                        kind="schematic_pin",
                        owner_id="j1",
                        owner_name="J1",
                        terminal_id="2",
                    ),
                ],
            ),
            Net(
                id="net-1",
                terminals=[
                    TerminalRef(
                        kind="pcb_pad",
                        owner_id="tp1",
                        owner_name="TP1",
                        terminal_id="1",
                    )
                ],
            ),
        ]
    )


def test_netlist_for_input_routes_schematic_files(monkeypatch, tmp_path) -> None:
    input_path = tmp_path / "fixture.kicad_sch"
    input_path.write_text("")

    import faebryk.libs.eda.hl.convert.eda_file_hl as eda_file_hl
    import faebryk.libs.eda.hl.convert.schematic_netlist as schematic_netlist

    sentinel_hl = object()
    expected = _example_netlist()

    monkeypatch.setattr(eda_file_hl.Schematic, "decode", lambda path: sentinel_hl)
    monkeypatch.setattr(
        schematic_netlist,
        "convert_schematic_to_netlist",
        lambda hl: expected,
    )

    assert eda_main._netlist_for_input(input_path) is expected


def test_netlist_for_input_routes_pcb_files(monkeypatch, tmp_path) -> None:
    input_path = tmp_path / "fixture.PcbDoc"
    input_path.write_text("")

    import faebryk.libs.eda.hl.convert.eda_file_hl as eda_file_hl
    import faebryk.libs.eda.hl.convert.pcb_netlist as pcb_netlist

    sentinel_hl = object()
    expected = _example_netlist()

    monkeypatch.setattr(eda_file_hl.PCB, "decode", lambda path: sentinel_hl)
    monkeypatch.setattr(
        pcb_netlist,
        "convert_pcb_to_netlist",
        lambda hl: expected,
    )

    assert eda_main._netlist_for_input(input_path) is expected


def test_netlist_command_renders_pretty_overview(monkeypatch, tmp_path) -> None:
    input_path = tmp_path / "fixture.SchDoc"
    input_path.write_text("")

    monkeypatch.setattr(eda_main, "_netlist_for_input", lambda path: _example_netlist())

    result = runner.invoke(eda_main.app, ["netlist", str(input_path)])

    assert result.exit_code == 0
    assert f"File:  {input_path}" in result.output
    assert "Kind:  altium_sch" in result.output
    assert "Nets:  2" in result.output
    assert "GND [GROUND] (2 terminals)" in result.output
    assert "schematic_pin" in result.output
    assert "J1.2" in result.output
    assert "U1.1" in result.output
    assert "net-1 (1 terminals)" in result.output
    assert "pcb_pad" in result.output
    assert "TP1.1" in result.output


def test_netlist_command_rejects_unknown_suffix(tmp_path) -> None:
    input_path = tmp_path / "fixture.txt"
    input_path.write_text("")

    result = runner.invoke(eda_main.app, ["netlist", str(input_path)])

    assert result.exit_code == 1
    assert "Unsupported file type for netlist: .txt" in result.output
