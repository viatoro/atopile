"""Prototype HL schematic model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from faebryk.libs.geometry.basic import Geometry

type Point2D = Geometry.Point2D


@dataclass(kw_only=True)
class SourceMetadata:
    id: str | None = None
    source_id: str | None = None
    source_order: int | None = None
    extra_properties: dict[str, object] = field(default_factory=dict)


@dataclass(kw_only=True)
class Pin(SourceMetadata):
    name: str
    location: Point2D


@dataclass(kw_only=True)
class Symbol(SourceMetadata):
    name: str | None = None
    refdes: str | None = None
    kind: Literal["component", "sheet"] = "component"
    child_sheet_id: str | None = None
    pins: list[Pin] = field(default_factory=list)


@dataclass(kw_only=True)
class WireSegment(SourceMetadata):
    points: list[Point2D] = field(default_factory=list)


@dataclass(kw_only=True)
class Junction(SourceMetadata):
    location: Point2D


@dataclass(kw_only=True)
class Net(SourceMetadata):
    name: str
    anchor: Point2D
    is_power: bool = False
    is_global: bool = True


@dataclass(kw_only=True)
class Sheet(SourceMetadata):
    name: str | None = None
    pins: list[Pin] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    wires: list[WireSegment] = field(default_factory=list)
    junctions: list[Junction] = field(default_factory=list)
    nets: list[Net] = field(default_factory=list)


@dataclass(kw_only=True)
class Schematic(SourceMetadata):
    top_sheet_id: str | None = None
    sheets: list[Sheet] = field(default_factory=list)

    @property
    def sheet_by_id(self) -> dict[str, Sheet]:
        return {sheet.id: sheet for sheet in self.sheets if sheet.id is not None}
