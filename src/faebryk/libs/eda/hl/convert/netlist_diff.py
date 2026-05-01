"""Shared HL netlist comparison helpers."""

from __future__ import annotations

from dataclasses import dataclass, field

from faebryk.libs.eda.hl.models.netlist import Net, Netlist

type TerminalSet = tuple[tuple[str, str], ...]
_IGNORED_TERMINAL_KINDS = {"schematic_sheet_pin"}


@dataclass(frozen=True)
class ConnectivityEntry:
    terminals: TerminalSet
    name: str | None = None
    aliases: tuple[str, ...] = ()

    @property
    def display_name(self) -> str:
        if self.name is None and not self.aliases:
            return "<unnamed>"
        if not self.aliases:
            return self.name or "<unnamed>"
        base = self.name or "<unnamed>"
        return f"{base} [{', '.join(self.aliases)}]"


@dataclass(kw_only=True)
class NetlistDiff:
    missing: list[ConnectivityEntry] = field(default_factory=list)
    extra: list[ConnectivityEntry] = field(default_factory=list)
    name_mismatches: list[tuple[ConnectivityEntry, ConnectivityEntry]] = field(
        default_factory=list
    )

    @property
    def equivalent_by_terminals(self) -> bool:
        return not self.missing and not self.extra

    @property
    def fully_equal(self) -> bool:
        return self.equivalent_by_terminals and not self.name_mismatches

    def format_report(self, *, limit: int = 10) -> str:
        parts = [
            f"missing={len(self.missing)}",
            f"extra={len(self.extra)}",
            f"name_mismatches={len(self.name_mismatches)}",
        ]
        if self.missing:
            parts.append(
                "missing_examples="
                + str(
                    [
                        (entry.display_name, entry.terminals[:4])
                        for entry in self.missing[:limit]
                    ]
                )
            )
        if self.extra:
            parts.append(
                "extra_examples="
                + str(
                    [
                        (entry.display_name, entry.terminals[:4])
                        for entry in self.extra[:limit]
                    ]
                )
            )
        if self.name_mismatches:
            parts.append(
                "name_examples="
                + str(
                    [
                        (expected.display_name, actual.display_name)
                        for expected, actual in self.name_mismatches[:limit]
                    ]
                )
            )
        return " ".join(parts)


def _entry(net: Net) -> ConnectivityEntry:
    return ConnectivityEntry(
        terminals=tuple(
            sorted(
                (
                    terminal.owner_name or terminal.owner_id or "",
                    terminal.terminal_id,
                )
                for terminal in net.terminals
                if terminal.kind not in _IGNORED_TERMINAL_KINDS
            )
        ),
        name=net.name,
        aliases=tuple(sorted(net.aliases)),
    )


def _entry_sort_key(entry: ConnectivityEntry) -> tuple[bool, str, TerminalSet]:
    return (entry.name is None, entry.name or "", entry.terminals)


def connectivity_entries(netlist: Netlist) -> tuple[ConnectivityEntry, ...]:
    entries = [_entry(net) for net in netlist.nets]
    entries = [entry for entry in entries if entry.terminals]
    return tuple(sorted(entries, key=_entry_sort_key))


def compare_netlists(expected: Netlist, actual: Netlist) -> NetlistDiff:
    expected_entries = connectivity_entries(expected)
    actual_entries = connectivity_entries(actual)

    expected_by_terminals = {entry.terminals: entry for entry in expected_entries}
    actual_by_terminals = {entry.terminals: entry for entry in actual_entries}

    missing = sorted(
        (
            expected_by_terminals[terminals]
            for terminals in expected_by_terminals.keys() - actual_by_terminals.keys()
        ),
        key=_entry_sort_key,
    )
    extra = sorted(
        (
            actual_by_terminals[terminals]
            for terminals in actual_by_terminals.keys() - expected_by_terminals.keys()
        ),
        key=_entry_sort_key,
    )
    shared_terminals = sorted(expected_by_terminals.keys() & actual_by_terminals.keys())
    name_mismatches = [
        (expected_by_terminals[terminals], actual_by_terminals[terminals])
        for terminals in shared_terminals
        if expected_by_terminals[terminals].name != actual_by_terminals[terminals].name
        or expected_by_terminals[terminals].aliases
        != actual_by_terminals[terminals].aliases
    ]

    return NetlistDiff(
        missing=missing,
        extra=extra,
        name_mismatches=name_mismatches,
    )
