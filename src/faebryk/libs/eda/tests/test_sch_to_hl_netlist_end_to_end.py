"""Discovered Altium schematic-to-reference-netlist regressions."""

from __future__ import annotations

from pathlib import Path

import pytest

from faebryk.libs.eda.hl.convert.eda_file_hl import (
    Netlist as NetlistDecoder,
)
from faebryk.libs.eda.hl.convert.eda_file_hl import (
    Schematic as SchematicDecoder,
)
from faebryk.libs.eda.hl.convert.netlist_diff import compare_netlists
from faebryk.libs.eda.hl.convert.schematic import convert_schematic_to_netlist
from faebryk.libs.eda.hl.models.netlist import Netlist
from faebryk.libs.eda.hl.models.schematic import Schematic
from faebryk.libs.util import repo_root as _repo_root

_NOT_FULLY_SUPPORTED_PAIR_BASENAMES = {
    ("Root_page.NET", "Root_page.SchDoc"),
    ("PG_V0.NET", "PG_V0.SchDoc"),
    ("main.NET", "main.SchDoc"),
    ("TOP.NET", "TOP.SchDoc"),
}


def _same_stem_reference_pair_relpaths() -> list[tuple[Path, Path]]:
    root = _repo_root()
    if not root.exists():
        return []

    pairs: list[tuple[Path, Path]] = []
    for net_path in sorted(root.rglob("*.NET")):
        for candidate in (
            net_path.with_suffix(".SchDoc"),
            net_path.with_suffix(".schdoc"),
        ):
            if candidate.exists():
                pairs.append((net_path.relative_to(root), candidate.relative_to(root)))
                break
    return pairs


_PAIR_RELPATHS = _same_stem_reference_pair_relpaths()


def _load_reference_pair(
    repo_root: Path,
    net_relpath: Path,
    schdoc_relpath: Path,
) -> tuple[Netlist, Schematic, Netlist]:
    reference = NetlistDecoder.decode(repo_root / net_relpath)
    schematic = SchematicDecoder.decode(repo_root / schdoc_relpath)
    actual = convert_schematic_to_netlist(schematic)
    return reference, schematic, actual


def _pair_id(pair: tuple[Path, Path]) -> str:
    net_relpath, schdoc_relpath = pair
    return f"{net_relpath} -> {schdoc_relpath}"


def _reference_has_connectivity_oracle(reference: Netlist) -> bool:
    return any(len(net.terminals) > 1 for net in reference.nets)


def _terminal_set(netlist: Netlist) -> set[tuple[str, str]]:
    return {
        (terminal.owner_name or terminal.owner_id or "", terminal.terminal_id)
        for net in netlist.nets
        for terminal in net.terminals
        if terminal.kind != "schematic_sheet_pin"
    }


def _unresolved_child_sheets(schematic: Schematic) -> list[str]:
    value = schematic.extra_properties.get("unresolved_child_sheets")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _is_not_fully_supported(net_relpath: Path, schdoc_relpath: Path) -> bool:
    return (
        net_relpath.name,
        schdoc_relpath.name,
    ) in _NOT_FULLY_SUPPORTED_PAIR_BASENAMES


@pytest.mark.skipif(
    not _PAIR_RELPATHS,
    reason="local same-stem Altium .NET/.SchDoc pairs not available",
)
@pytest.mark.parametrize(
    ("net_relpath", "schdoc_relpath"),
    [pytest.param(*pair, id=_pair_id(pair)) for pair in _PAIR_RELPATHS],
)
def test_same_stem_altium_reference_pairs_are_loadable(
    repo_root: Path,
    net_relpath: Path,
    schdoc_relpath: Path,
) -> None:
    reference, _, actual = _load_reference_pair(repo_root, net_relpath, schdoc_relpath)

    assert len(reference.nets) > 0
    assert len(actual.nets) > 0


@pytest.mark.skipif(
    not _PAIR_RELPATHS,
    reason="local same-stem Altium .NET/.SchDoc pairs not available",
)
@pytest.mark.parametrize(
    ("net_relpath", "schdoc_relpath"),
    [pytest.param(*pair, id=_pair_id(pair)) for pair in _PAIR_RELPATHS],
)
def test_same_stem_altium_reference_pairs_cover_reference_terminals(
    repo_root: Path,
    net_relpath: Path,
    schdoc_relpath: Path,
) -> None:
    reference, schematic, actual = _load_reference_pair(
        repo_root,
        net_relpath,
        schdoc_relpath,
    )

    reference_terminals = _terminal_set(reference)
    actual_terminals = _terminal_set(actual)
    missing_terminals = sorted(reference_terminals - actual_terminals)
    extra_terminals = sorted(actual_terminals - reference_terminals)

    assert not (reference_terminals - actual_terminals), (
        f"missing_reference_terminals={missing_terminals[:20]} "
        f"extra_actual_terminals={extra_terminals[:20]} "
        f"unresolved_child_sheets={_unresolved_child_sheets(schematic)}"
    )


@pytest.mark.skipif(
    not _PAIR_RELPATHS,
    reason="local same-stem Altium .NET/.SchDoc pairs not available",
)
@pytest.mark.parametrize(
    ("net_relpath", "schdoc_relpath"),
    [pytest.param(*pair, id=_pair_id(pair)) for pair in _PAIR_RELPATHS],
)
def test_same_stem_altium_reference_pairs_match_reference_connectivity_when_comparable(
    repo_root: Path,
    net_relpath: Path,
    schdoc_relpath: Path,
) -> None:
    reference, schematic, actual = _load_reference_pair(
        repo_root,
        net_relpath,
        schdoc_relpath,
    )

    if not _reference_has_connectivity_oracle(reference):
        return

    if _terminal_set(reference) != _terminal_set(actual):
        return

    if _unresolved_child_sheets(schematic):
        return

    diff = compare_netlists(reference, actual)

    if _is_not_fully_supported(net_relpath, schdoc_relpath):
        assert diff.equivalent_by_terminals, diff.format_report()
    else:
        assert diff.fully_equal, diff.format_report()
