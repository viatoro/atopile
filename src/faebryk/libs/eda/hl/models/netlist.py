"""Shared netlist model used as the HL connectivity metric."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, order=True)
class TerminalRef:
    kind: str
    terminal_id: str
    owner_id: str | None = None
    owner_name: str | None = None

    def normalized(self) -> tuple[str, str, str, str]:
        return (
            self.kind,
            self.owner_id or "",
            self.owner_name or "",
            self.terminal_id,
        )


@dataclass(kw_only=True)
class Net:
    id: str
    name: str | None = None
    aliases: list[str] = field(default_factory=list)
    terminals: list[TerminalRef] = field(default_factory=list)

    def normalized(self) -> tuple[str, tuple[tuple[str, str, str, str], ...]]:
        return (
            self.name or self.id,
            tuple(sorted(terminal.normalized() for terminal in self.terminals)),
        )


@dataclass(kw_only=True)
class Netlist:
    nets: list[Net] = field(default_factory=list)

    def normalized(
        self,
    ) -> tuple[tuple[str, tuple[tuple[str, str, str, str], ...]], ...]:
        return tuple(sorted(net.normalized() for net in self.nets))
