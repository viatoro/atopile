# This file is part of the faebryk project
# SPDX-License-Identifier: MIT

"""
Shared utilities for JSON visualization exporters (pinout, power tree, schematic, etc.).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TypedDict

import faebryk.core.node as fabll


class TreeMetaEntry(TypedDict):
    label: str
    value: str
    preview: bool
    alternateValue: str | None


def strip_root_hex(name: str) -> str:
    """Strip leading hex node ID prefix like '0xF8C9.' from names."""
    stripped = re.sub(r"^0x[0-9A-Fa-f]+\.", "", name)
    return stripped if stripped else name


def get_node_type_name(node: fabll.Node | None) -> str | None:
    if node is None:
        return None
    try:
        type_name = node.get_type_name()
    except Exception:
        return None
    if not type_name:
        return None
    return re.sub(r"\[0x[0-9A-Fa-f]+\]$", "", type_name)


def write_json(data: dict, path: Path) -> None:
    """Write JSON atomically via temp file.

    Creates parent directories if needed. Uses a .tmp suffix during writing
    and atomically renames on success to avoid partial writes.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        temp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def compact_dict(data: dict) -> dict:
    return {key: value for key, value in data.items() if value is not None}


def compact_meta(
    data: dict[str, str | None],
    *,
    alternate_values: dict[str, str | None] | None = None,
    preview_keys: tuple[str, ...] = (),
    labels: dict[str, str] | None = None,
) -> dict[str, TreeMetaEntry] | None:
    labels = labels or {}
    alternate_values = alternate_values or {}
    compacted = {
        key: {
            "label": labels.get(key, key.replace("_", " ").title()),
            "value": value,
            "preview": key in preview_keys,
            "alternateValue": alternate_values.get(key),
        }
        for key, value in data.items()
        if value is not None and value != "" and value != "?"
    }
    return compacted or None


def assign_tree_group_metadata(
    app: fabll.Node,
    nodes: list[dict],
    owners_by_node_id: dict[str, fabll.Node],
) -> None:
    def iter_group_modules(owner: fabll.Node):
        current = owner
        while True:
            yield current
            if current.is_same(app):
                break
            parent = current.get_parent()
            if parent is None:
                break
            current = parent[0]

    def get_group_info(module: fabll.Node) -> tuple[str, str] | None:
        type_label = get_node_type_name(module)
        if type_label:
            type_label = type_label.split("::")[-1]

        if module.is_same(app):
            if not type_label:
                return None
            return (f"root:{app.get_root_id()}", type_label)

        if not type_label:
            return None
        return (strip_root_hex(module.get_full_name()), type_label)

    group_sizes: dict[str, int] = {}
    for node in nodes:
        owner = owners_by_node_id.get(node["id"])
        if owner is None:
            continue
        for module in iter_group_modules(owner):
            group_info = get_group_info(module)
            if group_info is None:
                continue
            group_sizes[group_info[0]] = group_sizes.get(group_info[0], 0) + 1

    for node in nodes:
        owner = owners_by_node_id.get(node["id"])
        if owner is None:
            node.pop("groupId", None)
            node.pop("groupLabel", None)
            continue

        group_info = next(
            (
                info
                for module in iter_group_modules(owner)
                if (info := get_group_info(module)) is not None
                and group_sizes.get(info[0], 0) > 1
            ),
            None,
        )
        if group_info is None:
            node.pop("groupId", None)
            node.pop("groupLabel", None)
            continue

        node["groupId"] = group_info[0]
        node["groupLabel"] = group_info[1]


def build_tree_groups(nodes: list[dict]) -> list[dict]:
    groups: dict[str, dict] = {}
    for node in nodes:
        group_id = node.get("groupId")
        group_label = node.get("groupLabel")
        if not group_id or not group_label:
            continue

        existing = groups.get(group_id)
        if existing:
            existing["memberIds"].append(node["id"])
            continue

        groups[group_id] = {
            "id": group_id,
            "label": group_label,
            "memberIds": [node["id"]],
            "accentKind": node["type"],
        }

    return list(groups.values())
