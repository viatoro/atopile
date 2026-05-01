"""Shared HL netlist diff helper tests."""

from __future__ import annotations

from faebryk.libs.eda.hl.convert.netlist_diff import compare_netlists
from faebryk.libs.eda.hl.models.netlist import Net, Netlist, TerminalRef


def _terminal(owner_name: str, pin: str) -> TerminalRef:
    return TerminalRef(
        kind="schematic_pin",
        owner_name=owner_name,
        terminal_id=pin,
    )


def test_compare_netlists_separates_terminal_and_name_differences() -> None:
    expected = Netlist(
        nets=[
            Net(
                id="gnd",
                name="GND",
                terminals=[_terminal("U1", "1"), _terminal("J1", "2")],
            ),
            Net(
                id="sig",
                name="SIG",
                terminals=[_terminal("U1", "2"), _terminal("R1", "1")],
            ),
        ]
    )
    actual = Netlist(
        nets=[
            Net(
                id="ground",
                name="GROUND",
                terminals=[_terminal("U1", "1"), _terminal("J1", "2")],
            ),
            Net(
                id="sig",
                name="SIG",
                terminals=[_terminal("U1", "2"), _terminal("R1", "1")],
            ),
            Net(
                id="extra",
                name="EXTRA",
                terminals=[_terminal("TP1", "1"), _terminal("TP2", "1")],
            ),
        ]
    )

    diff = compare_netlists(expected, actual)

    assert diff.equivalent_by_terminals is False
    assert len(diff.missing) == 0
    assert len(diff.extra) == 1
    assert len(diff.name_mismatches) == 1
    assert "name_mismatches=1" in diff.format_report()
