"""Raw KiCad schematic helpers for constructs not present in the typed AST."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sexpdata import Symbol, loads

type Point2D = tuple[float, float]


@dataclass(frozen=True, kw_only=True)
class RawHierarchicalLabel:
    text: str
    at: Point2D
    uuid: str | None = None


@dataclass(frozen=True, kw_only=True)
class RawBusAlias:
    name: str
    members: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class RawSymbolInstance:
    uuid: str
    mirror: str | None = None
    references_by_path: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class RawPolyline:
    uuid: str | None = None
    points: tuple[Point2D, ...] = ()


@dataclass(frozen=True, kw_only=True)
class RawKicadSchematic:
    hierarchical_labels: tuple[RawHierarchicalLabel, ...] = ()
    bus_aliases: dict[str, tuple[str, ...]] = field(default_factory=dict)
    symbols_by_uuid: dict[str, RawSymbolInstance] = field(default_factory=dict)
    polylines: tuple[RawPolyline, ...] = ()


def _symbol_name(value: object) -> str | None:
    if isinstance(value, Symbol):
        return value.value()
    return None


def _string_value(value: object) -> str | None:
    if isinstance(value, Symbol):
        return value.value()
    if isinstance(value, str):
        return value
    return None


def _at_value(expr: list[object]) -> Point2D | None:
    if _symbol_name(expr[0]) != "at":
        return None
    if len(expr) < 3:
        return None
    x = expr[1]
    y = expr[2]
    if not isinstance(x, int | float) or not isinstance(y, int | float):
        return None
    return (float(x), float(y))


def _pts_value(expr: list[object]) -> tuple[Point2D, ...]:
    if _symbol_name(expr[0]) != "pts":
        return ()
    points: list[Point2D] = []
    for child in expr[1:]:
        if not isinstance(child, list) or not child:
            continue
        if _symbol_name(child[0]) != "xy" or len(child) < 3:
            continue
        x = child[1]
        y = child[2]
        if not isinstance(x, int | float) or not isinstance(y, int | float):
            continue
        points.append((float(x), float(y)))
    return tuple(points)


def _normalize_instance_path(path_value: str) -> str:
    parts = [part for part in path_value.split("/") if part]
    if len(parts) <= 1:
        return "/"
    return "/" + "/".join(parts[1:]) + "/"


def _references_by_path(expr: list[object]) -> dict[str, str]:
    if _symbol_name(expr[0]) != "instances":
        return {}

    references: dict[str, str] = {}
    for child in expr[1:]:
        if not isinstance(child, list) or not child:
            continue
        if _symbol_name(child[0]) != "project":
            continue
        for path_expr in child[1:]:
            if not isinstance(path_expr, list) or not path_expr:
                continue
            if _symbol_name(path_expr[0]) != "path":
                continue
            raw_path = _string_value(path_expr[1]) if len(path_expr) >= 2 else None
            if raw_path is None:
                continue
            reference: str | None = None
            for path_child in path_expr[2:]:
                if not isinstance(path_child, list) or not path_child:
                    continue
                if _symbol_name(path_child[0]) == "reference" and len(path_child) >= 2:
                    reference = _string_value(path_child[1])
            if reference is None:
                continue
            references[_normalize_instance_path(raw_path)] = reference
    return references


def read_raw_kicad_schematic(path: Path) -> RawKicadSchematic:
    root = loads(path.read_text(encoding="utf-8"))
    if not isinstance(root, list) or not root:
        raise ValueError(f"Expected a top-level list in {path}")
    if _symbol_name(root[0]) != "kicad_sch":
        raise ValueError(f"Expected a kicad_sch root in {path}")

    hierarchical_labels: list[RawHierarchicalLabel] = []
    bus_aliases: dict[str, tuple[str, ...]] = {}
    symbols_by_uuid: dict[str, RawSymbolInstance] = {}
    polylines: list[RawPolyline] = []

    for item in root[1:]:
        if not isinstance(item, list) or not item:
            continue
        head = _symbol_name(item[0])
        if head == "bus_alias":
            name = _string_value(item[1]) if len(item) >= 2 else None
            members_expr = item[2] if len(item) >= 3 else None
            if (
                name is None
                or not isinstance(members_expr, list)
                or not members_expr
                or _symbol_name(members_expr[0]) != "members"
            ):
                continue
            members = tuple(
                member
                for member in (_string_value(value) for value in members_expr[1:])
                if member
            )
            bus_aliases[name] = members
            continue

        if head != "hierarchical_label":
            if head == "polyline":
                points: tuple[Point2D, ...] = ()
                uuid: str | None = None
                for child in item[1:]:
                    if not isinstance(child, list) or not child:
                        continue
                    child_head = _symbol_name(child[0])
                    if child_head == "pts":
                        points = _pts_value(child)
                    elif child_head == "uuid" and len(child) >= 2:
                        uuid = _string_value(child[1])
                if len({point for point in points}) >= 2:
                    polylines.append(RawPolyline(uuid=uuid, points=points))
                continue

            if head != "symbol":
                continue

            uuid: str | None = None
            mirror: str | None = None
            references_by_path: dict[str, str] = {}
            for child in item[1:]:
                if not isinstance(child, list) or not child:
                    continue
                child_head = _symbol_name(child[0])
                if child_head == "uuid" and len(child) >= 2:
                    uuid = _string_value(child[1])
                elif child_head == "mirror" and len(child) >= 2:
                    mirror = _symbol_name(child[1]) or _string_value(child[1])
                elif child_head == "instances":
                    references_by_path = _references_by_path(child)
            if uuid is not None:
                symbols_by_uuid[uuid] = RawSymbolInstance(
                    uuid=uuid,
                    mirror=mirror,
                    references_by_path=references_by_path,
                )
            continue

        text = _string_value(item[1]) if len(item) >= 2 else None
        if text is None:
            continue
        at: Point2D | None = None
        uuid: str | None = None
        for child in item[2:]:
            if not isinstance(child, list) or not child:
                continue
            child_head = _symbol_name(child[0])
            if child_head == "at":
                at = _at_value(child)
            elif child_head == "uuid" and len(child) >= 2:
                uuid = _string_value(child[1])
        if at is None:
            continue
        hierarchical_labels.append(
            RawHierarchicalLabel(
                text=text,
                at=at,
                uuid=uuid,
            )
        )

    return RawKicadSchematic(
        hierarchical_labels=tuple(hierarchical_labels),
        bus_aliases=bus_aliases,
        symbols_by_uuid=symbols_by_uuid,
        polylines=tuple(polylines),
    )


__all__ = [
    "RawBusAlias",
    "RawHierarchicalLabel",
    "RawKicadSchematic",
    "RawPolyline",
    "RawSymbolInstance",
    "read_raw_kicad_schematic",
]
