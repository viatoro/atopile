"""Low-level Cadence netlist model."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ComponentPin:
    pin: str
    net_name: str


@dataclass
class Component:
    record_id: str
    footprint: str
    refdes: str
    value: str = ""
    raw_header: str = ""
    pins: list[ComponentPin] = field(default_factory=list)


@dataclass
class Netlist:
    format_name: str | None = None
    components: list[Component] = field(default_factory=list)
