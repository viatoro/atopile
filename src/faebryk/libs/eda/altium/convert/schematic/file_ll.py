"""Bidirectional conversion between Altium SchDoc files and schematic LL."""

from __future__ import annotations

import math
import re
import struct
from pathlib import Path

from aaf2.cfb import CompoundFileBinary

from faebryk.libs.eda.altium.models.schematic.ll import (
    AltiumSchDoc,
    BusRecord,
    ComponentRecord,
    JunctionRecord,
    NetLabelRecord,
    ParameterRecord,
    PinRecord,
    PortRecord,
    PowerObjectRecord,
    SchematicRecord,
    SchRecordType,
    SheetEntryRecord,
    SheetSymbolRecord,
    UnknownRecord,
    WireRecord,
)

_HEADER_VALUE = "Protel for Windows - Schematic Capture Binary File Version 5.0"
_VERTEX_KEY_RE = re.compile(r"^[XY]\d+$", re.IGNORECASE)


def _lookup_key(properties: dict[str, str], name: str) -> str | None:
    needle = name.lower()
    for key in properties:
        if key.lower() == needle:
            return key
    return None


def _get_param(properties: dict[str, str], name: str, default: str = "") -> str:
    key = _lookup_key(properties, name)
    if key is None:
        return default
    return properties[key]


def _get_int(properties: dict[str, str], name: str, default: int = 0) -> int:
    value = _get_param(properties, name, "")
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _get_bool(properties: dict[str, str], name: str) -> bool:
    return _get_param(properties, name, "").strip().upper() in {"1", "T", "TRUE"}


def _set_param(properties: dict[str, str], name: str, value: str) -> None:
    key = _lookup_key(properties, name) or name
    properties[key] = value


def _drop_param(properties: dict[str, str], name: str) -> None:
    key = _lookup_key(properties, name)
    if key is not None:
        properties.pop(key, None)


def _split_raw(raw_value: int, scale: int) -> tuple[int, int]:
    base = math.trunc(raw_value / scale)
    frac = raw_value - base * scale
    return base, frac


def _coord_from_dxp(properties: dict[str, str], name: str) -> int:
    dxp = _get_int(properties, name, 0)
    frac = _get_int(properties, f"{name}_Frac", _get_int(properties, f"{name}_FRAC", 0))
    return dxp * 100_000 + frac


def _set_coord_param(properties: dict[str, str], name: str, raw_value: int) -> None:
    dxp, frac = _split_raw(raw_value, 100_000)
    if dxp:
        _set_param(properties, name, str(dxp))
    else:
        _drop_param(properties, name)
    if frac:
        frac_key = _lookup_key(properties, f"{name}_Frac") or f"{name}_Frac"
        properties[frac_key] = str(frac)
        other_frac = f"{name}_FRAC" if frac_key.endswith("_Frac") else f"{name}_Frac"
        _drop_param(properties, other_frac)
    else:
        _drop_param(properties, f"{name}_Frac")
        _drop_param(properties, f"{name}_FRAC")


def _point_list_from_properties(properties: dict[str, str]) -> list[tuple[int, int]]:
    count = _get_int(properties, "LocationCount", 0)
    points: list[tuple[int, int]] = []
    for index in range(1, count + 1):
        x = _get_int(properties, f"X{index}", 0) * 1000
        y = _get_int(properties, f"Y{index}", 0) * 1000
        points.append((x, y))
    return points


def _set_point_list(properties: dict[str, str], points: list[tuple[int, int]]) -> None:
    for key in list(properties):
        if _VERTEX_KEY_RE.match(key):
            properties.pop(key, None)
    if points or _lookup_key(properties, "LocationCount") is not None:
        _set_param(properties, "LocationCount", str(len(points)))
    for index, (x_value, y_value) in enumerate(points, start=1):
        x_units, _ = _split_raw(x_value, 1000)
        y_units, _ = _split_raw(y_value, 1000)
        _set_param(properties, f"X{index}", str(x_units))
        _set_param(properties, f"Y{index}", str(y_units))


def _set_text_param(
    properties: dict[str, str],
    name: str,
    value: str,
    *,
    default: str = "",
) -> None:
    if value != default or _lookup_key(properties, name) is not None:
        _set_param(properties, name, value)
    else:
        _drop_param(properties, name)


def _set_int_param(
    properties: dict[str, str],
    name: str,
    value: int,
    *,
    default: int = 0,
) -> None:
    if value != default or _lookup_key(properties, name) is not None:
        _set_param(properties, name, str(value))
    else:
        _drop_param(properties, name)


def _set_bool_param(properties: dict[str, str], name: str, value: bool) -> None:
    if value or _lookup_key(properties, name) is not None:
        if value:
            _set_param(properties, name, "T")
        else:
            _drop_param(properties, name)


def _apply_common_record_properties(
    properties: dict[str, str],
    record: SchematicRecord,
) -> None:
    _set_param(properties, "RECORD", str(int(record.record_type)))
    if record.owner_index >= 0 or _lookup_key(properties, "OwnerIndex") is not None:
        if record.owner_index >= 0:
            _set_param(properties, "OwnerIndex", str(record.owner_index))
        else:
            _drop_param(properties, "OwnerIndex")
    _set_int_param(
        properties,
        "OwnerPartId",
        record.owner_part_id,
        default=-1,
    )
    _set_int_param(
        properties,
        "OwnerPartDisplayMode",
        record.owner_part_display_mode,
        default=0,
    )
    _set_text_param(properties, "UniqueID", record.unique_id or "")


class ParameterBlockCodec:
    @staticmethod
    def encode(properties: dict[str, str]) -> bytes:
        text = "|" + "|".join(f"{key}={value}" for key, value in properties.items())
        if properties:
            text += "|"
        encoded = text.encode("cp1252", errors="replace")
        return struct.pack("<I", len(encoded) + 1) + encoded + b"\x00"

    @staticmethod
    def decode_one(data: bytes, offset: int) -> tuple[dict[str, str], int]:
        if offset + 4 > len(data):
            raise ValueError("unexpected end of parameter stream")
        size = struct.unpack_from("<I", data, offset)[0] & 0x00FFFFFF
        offset += 4
        if offset + size > len(data):
            raise ValueError("parameter block overruns stream")
        payload = data[offset : offset + size]
        offset += size
        if payload.endswith(b"\x00"):
            payload = payload[:-1]
        text = payload.decode("cp1252", errors="replace")
        properties: dict[str, str] = {}
        for item in text.split("|"):
            if not item or "=" not in item:
                continue
            key, value = item.split("=", 1)
            properties[key] = value
        return properties, offset

    @staticmethod
    def decode_many(data: bytes) -> list[dict[str, str]]:
        offset = 0
        blocks: list[dict[str, str]] = []
        while offset < len(data):
            properties, offset = ParameterBlockCodec.decode_one(data, offset)
            blocks.append(properties)
        return blocks


class ComponentRecordCodec:
    @staticmethod
    def decode(index: int, properties: dict[str, str]) -> ComponentRecord:
        return ComponentRecord(
            index=index,
            owner_index=_get_int(properties, "OwnerIndex", -1),
            owner_part_id=_get_int(properties, "OwnerPartId", -1),
            owner_part_display_mode=_get_int(properties, "OwnerPartDisplayMode", 0),
            unique_id=_get_param(properties, "UniqueID") or None,
            properties=dict(properties),
            lib_reference=_get_param(properties, "LibReference"),
            design_item_id=_get_param(properties, "DesignItemId"),
            description=_get_param(properties, "ComponentDescription"),
            location=(
                _coord_from_dxp(properties, "Location.X"),
                _coord_from_dxp(properties, "Location.Y"),
            ),
            orientation=_get_int(properties, "Orientation", 0),
            current_part_id=_get_int(properties, "CurrentPartId", 1),
            part_count=_get_int(properties, "PartCount", 1),
            display_mode_count=_get_int(properties, "DisplayModeCount", 1),
        )

    @staticmethod
    def encode(record: ComponentRecord) -> bytes:
        properties = dict(record.properties)
        _apply_common_record_properties(properties, record)
        _set_text_param(properties, "LibReference", record.lib_reference)
        _set_text_param(properties, "DesignItemId", record.design_item_id)
        _set_text_param(properties, "ComponentDescription", record.description)
        _set_coord_param(properties, "Location.X", record.location[0])
        _set_coord_param(properties, "Location.Y", record.location[1])
        _set_int_param(properties, "Orientation", record.orientation, default=0)
        _set_int_param(properties, "CurrentPartId", record.current_part_id, default=1)
        _set_int_param(properties, "PartCount", record.part_count, default=1)
        _set_int_param(
            properties,
            "DisplayModeCount",
            record.display_mode_count,
            default=1,
        )
        return ParameterBlockCodec.encode(properties)


class PinRecordCodec:
    @staticmethod
    def decode(index: int, properties: dict[str, str]) -> PinRecord:
        conglomerate = _get_int(properties, "PinConglomerate", 0)
        return PinRecord(
            index=index,
            owner_index=_get_int(properties, "OwnerIndex", -1),
            owner_part_id=_get_int(properties, "OwnerPartId", 1),
            owner_part_display_mode=_get_int(properties, "OwnerPartDisplayMode", 0),
            unique_id=_get_param(properties, "UniqueID") or None,
            properties=dict(properties),
            name=_get_param(properties, "Name"),
            designator=_get_param(properties, "Designator"),
            location=(
                _coord_from_dxp(properties, "Location.X"),
                _coord_from_dxp(properties, "Location.Y"),
            ),
            length=_coord_from_dxp(properties, "PinLength"),
            electrical=_get_int(properties, "Electrical", 0),
            orientation=conglomerate & 0x03,
            show_name=(conglomerate & 0x08) != 0,
            show_designator=(conglomerate & 0x10) != 0,
            description=_get_param(properties, "Description"),
        )

    @staticmethod
    def encode(record: PinRecord) -> bytes:
        properties = dict(record.properties)
        _apply_common_record_properties(properties, record)
        base_conglomerate = _get_int(properties, "PinConglomerate", 0)
        base_conglomerate &= ~0x1B
        base_conglomerate |= record.orientation & 0x03
        if record.show_name:
            base_conglomerate |= 0x08
        if record.show_designator:
            base_conglomerate |= 0x10
        _set_text_param(properties, "Name", record.name)
        _set_text_param(properties, "Designator", record.designator)
        _set_coord_param(properties, "Location.X", record.location[0])
        _set_coord_param(properties, "Location.Y", record.location[1])
        _set_coord_param(properties, "PinLength", record.length)
        _set_int_param(properties, "Electrical", record.electrical, default=0)
        _set_int_param(properties, "PinConglomerate", base_conglomerate, default=0)
        _set_text_param(properties, "Description", record.description)
        return ParameterBlockCodec.encode(properties)


class ParameterRecordCodec:
    @staticmethod
    def decode(
        index: int,
        properties: dict[str, str],
        record_type: int,
    ) -> ParameterRecord:
        return ParameterRecord(
            index=index,
            owner_index=_get_int(properties, "OwnerIndex", -1),
            owner_part_id=_get_int(properties, "OwnerPartId", -1),
            owner_part_display_mode=_get_int(properties, "OwnerPartDisplayMode", 0),
            unique_id=_get_param(properties, "UniqueID") or None,
            properties=dict(properties),
            name=_get_param(properties, "Name"),
            text=_get_param(properties, "Text"),
            location=(
                _coord_from_dxp(properties, "Location.X"),
                _coord_from_dxp(properties, "Location.Y"),
            ),
            color=_get_int(properties, "Color", 0),
            font_id=_get_int(properties, "FontID", 0),
            is_hidden=_get_bool(properties, "IsHidden"),
            is_designator=record_type == int(SchRecordType.DESIGNATOR),
            read_only_state=_get_int(properties, "ReadOnlyState", 0),
            record_type=record_type,
        )

    @staticmethod
    def encode(record: ParameterRecord) -> bytes:
        properties = dict(record.properties)
        if record.is_designator:
            record.record_type = int(SchRecordType.DESIGNATOR)
        else:
            record.record_type = int(SchRecordType.PARAMETER)
        _apply_common_record_properties(properties, record)
        _set_text_param(properties, "Name", record.name)
        _set_text_param(properties, "Text", record.text)
        _set_coord_param(properties, "Location.X", record.location[0])
        _set_coord_param(properties, "Location.Y", record.location[1])
        _set_int_param(properties, "Color", record.color, default=0)
        _set_int_param(properties, "FontID", record.font_id, default=0)
        _set_bool_param(properties, "IsHidden", record.is_hidden)
        _set_int_param(properties, "ReadOnlyState", record.read_only_state, default=0)
        return ParameterBlockCodec.encode(properties)


class WireRecordCodec:
    @staticmethod
    def decode(index: int, properties: dict[str, str]) -> WireRecord:
        return WireRecord(
            index=index,
            owner_index=_get_int(properties, "OwnerIndex", -1),
            owner_part_id=_get_int(properties, "OwnerPartId", -1),
            owner_part_display_mode=_get_int(properties, "OwnerPartDisplayMode", 0),
            unique_id=_get_param(properties, "UniqueID") or None,
            properties=dict(properties),
            vertices=_point_list_from_properties(properties),
            color=_get_int(properties, "Color", 0),
            line_width=_get_int(properties, "LineWidth", 0),
        )

    @staticmethod
    def encode(record: WireRecord) -> bytes:
        properties = dict(record.properties)
        _apply_common_record_properties(properties, record)
        _set_point_list(properties, record.vertices)
        _set_int_param(properties, "Color", record.color, default=0)
        _set_int_param(properties, "LineWidth", record.line_width, default=0)
        return ParameterBlockCodec.encode(properties)


class BusRecordCodec:
    @staticmethod
    def decode(index: int, properties: dict[str, str]) -> BusRecord:
        return BusRecord(
            index=index,
            owner_index=_get_int(properties, "OwnerIndex", -1),
            owner_part_id=_get_int(properties, "OwnerPartId", -1),
            owner_part_display_mode=_get_int(properties, "OwnerPartDisplayMode", 0),
            unique_id=_get_param(properties, "UniqueID") or None,
            properties=dict(properties),
            vertices=_point_list_from_properties(properties),
            color=_get_int(properties, "Color", 0),
            line_width=_get_int(properties, "LineWidth", 0),
        )

    @staticmethod
    def encode(record: BusRecord) -> bytes:
        properties = dict(record.properties)
        _apply_common_record_properties(properties, record)
        _set_point_list(properties, record.vertices)
        _set_int_param(properties, "Color", record.color, default=0)
        _set_int_param(properties, "LineWidth", record.line_width, default=0)
        return ParameterBlockCodec.encode(properties)


class NetLabelRecordCodec:
    @staticmethod
    def decode(index: int, properties: dict[str, str]) -> NetLabelRecord:
        return NetLabelRecord(
            index=index,
            owner_index=_get_int(properties, "OwnerIndex", -1),
            owner_part_id=_get_int(properties, "OwnerPartId", -1),
            owner_part_display_mode=_get_int(properties, "OwnerPartDisplayMode", 0),
            unique_id=_get_param(properties, "UniqueID") or None,
            properties=dict(properties),
            text=_get_param(properties, "Text"),
            location=(
                _coord_from_dxp(properties, "Location.X"),
                _coord_from_dxp(properties, "Location.Y"),
            ),
            color=_get_int(properties, "Color", 0),
            font_id=_get_int(properties, "FontID", 0),
        )

    @staticmethod
    def encode(record: NetLabelRecord) -> bytes:
        properties = dict(record.properties)
        _apply_common_record_properties(properties, record)
        _set_text_param(properties, "Text", record.text)
        _set_coord_param(properties, "Location.X", record.location[0])
        _set_coord_param(properties, "Location.Y", record.location[1])
        _set_int_param(properties, "Color", record.color, default=0)
        _set_int_param(properties, "FontID", record.font_id, default=0)
        return ParameterBlockCodec.encode(properties)


class JunctionRecordCodec:
    @staticmethod
    def decode(index: int, properties: dict[str, str]) -> JunctionRecord:
        return JunctionRecord(
            index=index,
            owner_index=_get_int(properties, "OwnerIndex", -1),
            owner_part_id=_get_int(properties, "OwnerPartId", -1),
            owner_part_display_mode=_get_int(properties, "OwnerPartDisplayMode", 0),
            unique_id=_get_param(properties, "UniqueID") or None,
            properties=dict(properties),
            location=(
                _coord_from_dxp(properties, "Location.X"),
                _coord_from_dxp(properties, "Location.Y"),
            ),
            color=_get_int(properties, "Color", 0),
        )

    @staticmethod
    def encode(record: JunctionRecord) -> bytes:
        properties = dict(record.properties)
        _apply_common_record_properties(properties, record)
        _set_coord_param(properties, "Location.X", record.location[0])
        _set_coord_param(properties, "Location.Y", record.location[1])
        _set_int_param(properties, "Color", record.color, default=0)
        return ParameterBlockCodec.encode(properties)


class PowerObjectRecordCodec:
    @staticmethod
    def decode(index: int, properties: dict[str, str]) -> PowerObjectRecord:
        return PowerObjectRecord(
            index=index,
            owner_index=_get_int(properties, "OwnerIndex", -1),
            owner_part_id=_get_int(properties, "OwnerPartId", -1),
            owner_part_display_mode=_get_int(properties, "OwnerPartDisplayMode", 0),
            unique_id=_get_param(properties, "UniqueID") or None,
            properties=dict(properties),
            text=_get_param(properties, "Text"),
            location=(
                _coord_from_dxp(properties, "Location.X"),
                _coord_from_dxp(properties, "Location.Y"),
            ),
            orientation=_get_int(properties, "Orientation", 0),
            style=_get_int(properties, "Style", 0),
            color=_get_int(properties, "Color", 0),
            font_id=_get_int(properties, "FontID", 0),
            show_net_name=_get_bool(properties, "ShowNetName"),
        )

    @staticmethod
    def encode(record: PowerObjectRecord) -> bytes:
        properties = dict(record.properties)
        _apply_common_record_properties(properties, record)
        _set_text_param(properties, "Text", record.text)
        _set_coord_param(properties, "Location.X", record.location[0])
        _set_coord_param(properties, "Location.Y", record.location[1])
        _set_int_param(properties, "Orientation", record.orientation, default=0)
        _set_int_param(properties, "Style", record.style, default=0)
        _set_int_param(properties, "Color", record.color, default=0)
        _set_int_param(properties, "FontID", record.font_id, default=0)
        _set_bool_param(properties, "ShowNetName", record.show_net_name)
        return ParameterBlockCodec.encode(properties)


class SheetSymbolRecordCodec:
    @staticmethod
    def decode(index: int, properties: dict[str, str]) -> SheetSymbolRecord:
        return SheetSymbolRecord(
            index=index,
            owner_index=_get_int(properties, "OwnerIndex", -1),
            owner_part_id=_get_int(properties, "OwnerPartId", -1),
            owner_part_display_mode=_get_int(properties, "OwnerPartDisplayMode", 0),
            unique_id=_get_param(properties, "UniqueID") or None,
            properties=dict(properties),
            location=(
                _coord_from_dxp(properties, "Location.X"),
                _coord_from_dxp(properties, "Location.Y"),
            ),
            x_size=_coord_from_dxp(properties, "XSize"),
            y_size=_coord_from_dxp(properties, "YSize"),
            is_mirrored=_get_bool(properties, "IsMirrored"),
            file_name=_get_param(properties, "FileName"),
            sheet_name=_get_param(properties, "SheetName"),
            line_width=_get_int(properties, "LineWidth", 0),
            color=_get_int(properties, "Color", 0),
            area_color=_get_int(properties, "AreaColor", 0),
            is_solid=_get_bool(properties, "IsSolid"),
            show_hidden_fields=_get_bool(properties, "ShowHiddenFields"),
            symbol_type=_get_param(properties, "SymbolType"),
        )

    @staticmethod
    def encode(record: SheetSymbolRecord) -> bytes:
        properties = dict(record.properties)
        _apply_common_record_properties(properties, record)
        _set_coord_param(properties, "Location.X", record.location[0])
        _set_coord_param(properties, "Location.Y", record.location[1])
        _set_coord_param(properties, "XSize", record.x_size)
        _set_coord_param(properties, "YSize", record.y_size)
        _set_bool_param(properties, "IsMirrored", record.is_mirrored)
        _set_text_param(properties, "FileName", record.file_name)
        _set_text_param(properties, "SheetName", record.sheet_name)
        _set_int_param(properties, "LineWidth", record.line_width, default=0)
        _set_int_param(properties, "Color", record.color, default=0)
        _set_int_param(properties, "AreaColor", record.area_color, default=0)
        _set_bool_param(properties, "IsSolid", record.is_solid)
        _set_bool_param(properties, "ShowHiddenFields", record.show_hidden_fields)
        _set_text_param(properties, "SymbolType", record.symbol_type)
        return ParameterBlockCodec.encode(properties)


class SheetEntryRecordCodec:
    @staticmethod
    def decode(index: int, properties: dict[str, str]) -> SheetEntryRecord:
        return SheetEntryRecord(
            index=index,
            owner_index=_get_int(properties, "OwnerIndex", -1),
            owner_part_id=_get_int(properties, "OwnerPartId", -1),
            owner_part_display_mode=_get_int(properties, "OwnerPartDisplayMode", 0),
            unique_id=_get_param(properties, "UniqueID") or None,
            properties=dict(properties),
            side=_get_int(properties, "Side", 0),
            distance_from_top=_coord_from_dxp(properties, "DistanceFromTop"),
            name=_get_param(properties, "Name"),
            io_type=_get_int(properties, "IoType", 0),
            style=_get_int(properties, "Style", 0),
            arrow_kind=_get_param(properties, "ArrowKind"),
            harness_type=_get_param(properties, "HarnessType"),
            harness_color=_get_int(properties, "HarnessColor", 0),
            font_id=_get_int(
                properties,
                "TextFontID",
                _get_int(properties, "FontID", 0),
            ),
            color=_get_int(properties, "Color", 0),
            area_color=_get_int(properties, "AreaColor", 0),
            text_color=_get_int(properties, "TextColor", 0),
            text_style=_get_param(properties, "TextStyle"),
        )

    @staticmethod
    def encode(record: SheetEntryRecord) -> bytes:
        properties = dict(record.properties)
        _apply_common_record_properties(properties, record)
        _set_int_param(properties, "Side", record.side, default=0)
        _set_coord_param(properties, "DistanceFromTop", record.distance_from_top)
        _set_text_param(properties, "Name", record.name)
        _set_int_param(properties, "IoType", record.io_type, default=0)
        _set_int_param(properties, "Style", record.style, default=0)
        _set_text_param(properties, "ArrowKind", record.arrow_kind)
        _set_text_param(properties, "HarnessType", record.harness_type)
        _set_int_param(properties, "HarnessColor", record.harness_color, default=0)
        _set_int_param(properties, "TextFontID", record.font_id, default=0)
        _set_int_param(properties, "Color", record.color, default=0)
        _set_int_param(properties, "AreaColor", record.area_color, default=0)
        _set_int_param(properties, "TextColor", record.text_color, default=0)
        _set_text_param(properties, "TextStyle", record.text_style)
        return ParameterBlockCodec.encode(properties)


class PortRecordCodec:
    @staticmethod
    def decode(index: int, properties: dict[str, str]) -> PortRecord:
        return PortRecord(
            index=index,
            owner_index=_get_int(properties, "OwnerIndex", -1),
            owner_part_id=_get_int(properties, "OwnerPartId", -1),
            owner_part_display_mode=_get_int(properties, "OwnerPartDisplayMode", 0),
            unique_id=_get_param(properties, "UniqueID") or None,
            properties=dict(properties),
            location=(
                _coord_from_dxp(properties, "Location.X"),
                _coord_from_dxp(properties, "Location.Y"),
            ),
            name=_get_param(properties, "Name"),
            io_type=_get_int(properties, "IoType", 0),
            style=_get_int(properties, "Style", 0),
            alignment=_get_int(properties, "Alignment", 0),
            width=_coord_from_dxp(properties, "Width"),
            height=_coord_from_dxp(properties, "Height"),
            border_width=_get_int(properties, "BorderWidth", 0),
            auto_size=_get_bool(properties, "AutoSize"),
            connected_end=_get_int(properties, "ConnectedEnd", 0),
            cross_reference=_get_param(properties, "CrossReference"),
            show_net_name=_get_bool(properties, "ShowNetName"),
            harness_type=_get_param(properties, "HarnessType"),
            harness_color=_get_int(properties, "HarnessColor", 0),
            is_custom_style=_get_bool(properties, "IsCustomStyle"),
            font_id=_get_int(properties, "FontID", 0),
            color=_get_int(properties, "Color", 0),
            area_color=_get_int(properties, "AreaColor", 0),
            text_color=_get_int(properties, "TextColor", 0),
        )

    @staticmethod
    def encode(record: PortRecord) -> bytes:
        properties = dict(record.properties)
        _apply_common_record_properties(properties, record)
        _set_coord_param(properties, "Location.X", record.location[0])
        _set_coord_param(properties, "Location.Y", record.location[1])
        _set_text_param(properties, "Name", record.name)
        _set_int_param(properties, "IoType", record.io_type, default=0)
        _set_int_param(properties, "Style", record.style, default=0)
        _set_int_param(properties, "Alignment", record.alignment, default=0)
        _set_coord_param(properties, "Width", record.width)
        _set_coord_param(properties, "Height", record.height)
        _set_int_param(properties, "BorderWidth", record.border_width, default=0)
        _set_bool_param(properties, "AutoSize", record.auto_size)
        _set_int_param(properties, "ConnectedEnd", record.connected_end, default=0)
        _set_text_param(properties, "CrossReference", record.cross_reference)
        _set_bool_param(properties, "ShowNetName", record.show_net_name)
        _set_text_param(properties, "HarnessType", record.harness_type)
        _set_int_param(properties, "HarnessColor", record.harness_color, default=0)
        _set_bool_param(properties, "IsCustomStyle", record.is_custom_style)
        _set_int_param(properties, "FontID", record.font_id, default=0)
        _set_int_param(properties, "Color", record.color, default=0)
        _set_int_param(properties, "AreaColor", record.area_color, default=0)
        _set_int_param(properties, "TextColor", record.text_color, default=0)
        return ParameterBlockCodec.encode(properties)


class UnknownRecordCodec:
    @staticmethod
    def decode(
        index: int,
        properties: dict[str, str],
        record_type: int,
    ) -> UnknownRecord:
        return UnknownRecord(
            index=index,
            record_type=record_type,
            owner_index=_get_int(properties, "OwnerIndex", -1),
            owner_part_id=_get_int(properties, "OwnerPartId", -1),
            owner_part_display_mode=_get_int(properties, "OwnerPartDisplayMode", 0),
            unique_id=_get_param(properties, "UniqueID") or None,
            properties=dict(properties),
        )

    @staticmethod
    def encode(record: UnknownRecord) -> bytes:
        properties = dict(record.properties)
        _apply_common_record_properties(properties, record)
        return ParameterBlockCodec.encode(properties)


class RecordCodec:
    @staticmethod
    def decode(index: int, properties: dict[str, str]) -> SchematicRecord:
        record_type = _get_int(properties, "RECORD", 0)
        if record_type == int(SchRecordType.COMPONENT):
            return ComponentRecordCodec.decode(index, properties)
        if record_type == int(SchRecordType.PIN):
            return PinRecordCodec.decode(index, properties)
        if record_type in {int(SchRecordType.PARAMETER), int(SchRecordType.DESIGNATOR)}:
            return ParameterRecordCodec.decode(index, properties, record_type)
        if record_type == int(SchRecordType.WIRE):
            return WireRecordCodec.decode(index, properties)
        if record_type == int(SchRecordType.BUS):
            return BusRecordCodec.decode(index, properties)
        if record_type == int(SchRecordType.NET_LABEL):
            return NetLabelRecordCodec.decode(index, properties)
        if record_type == int(SchRecordType.JUNCTION):
            return JunctionRecordCodec.decode(index, properties)
        if record_type == int(SchRecordType.POWER_OBJECT):
            return PowerObjectRecordCodec.decode(index, properties)
        if record_type == int(SchRecordType.SHEET_SYMBOL):
            return SheetSymbolRecordCodec.decode(index, properties)
        if record_type == int(SchRecordType.SHEET_ENTRY):
            return SheetEntryRecordCodec.decode(index, properties)
        if record_type == int(SchRecordType.PORT):
            return PortRecordCodec.decode(index, properties)
        return UnknownRecordCodec.decode(index, properties, record_type)

    @staticmethod
    def encode(record: SchematicRecord) -> bytes:
        if isinstance(record, ComponentRecord):
            return ComponentRecordCodec.encode(record)
        if isinstance(record, PinRecord):
            return PinRecordCodec.encode(record)
        if isinstance(record, ParameterRecord):
            return ParameterRecordCodec.encode(record)
        if isinstance(record, WireRecord):
            return WireRecordCodec.encode(record)
        if isinstance(record, BusRecord):
            return BusRecordCodec.encode(record)
        if isinstance(record, NetLabelRecord):
            return NetLabelRecordCodec.encode(record)
        if isinstance(record, JunctionRecord):
            return JunctionRecordCodec.encode(record)
        if isinstance(record, PowerObjectRecord):
            return PowerObjectRecordCodec.encode(record)
        if isinstance(record, SheetSymbolRecord):
            return SheetSymbolRecordCodec.encode(record)
        if isinstance(record, SheetEntryRecord):
            return SheetEntryRecordCodec.encode(record)
        if isinstance(record, PortRecord):
            return PortRecordCodec.encode(record)
        if isinstance(record, UnknownRecord):
            return UnknownRecordCodec.encode(record)
        raise TypeError(f"unsupported schematic record type: {type(record)!r}")


class SchDocCodec:
    @staticmethod
    def encode(doc: AltiumSchDoc) -> dict[str, bytes]:
        streams = dict(doc.raw_streams)
        header_parameters = dict(doc.header_parameters)
        if not header_parameters:
            header_parameters = {"HEADER": _HEADER_VALUE, "Weight": "0"}
        file_header = [ParameterBlockCodec.encode(header_parameters)]
        file_header.extend(RecordCodec.encode(record) for record in doc.records)
        streams["FileHeader"] = b"".join(file_header)
        if doc.additional_parameters:
            streams["Additional"] = ParameterBlockCodec.encode(
                doc.additional_parameters
            )
        elif "Additional" in streams and not doc.additional_parameters:
            streams.pop("Additional", None)
        if doc.storage_data is not None:
            streams["Storage"] = doc.storage_data
        elif "Storage" in streams and doc.storage_data is None:
            streams.pop("Storage", None)
        return streams

    @staticmethod
    def decode(streams: dict[str, bytes]) -> AltiumSchDoc:
        if "FileHeader" not in streams:
            raise ValueError("missing /FileHeader stream")
        blocks = ParameterBlockCodec.decode_many(streams["FileHeader"])
        if not blocks:
            raise ValueError("empty FileHeader stream")
        header_parameters = blocks[0]
        records = [
            RecordCodec.decode(index, properties)
            for index, properties in enumerate(blocks[1:])
        ]
        additional_parameters: dict[str, str] = {}
        if streams.get("Additional"):
            additional_blocks = ParameterBlockCodec.decode_many(streams["Additional"])
            if additional_blocks:
                additional_parameters = additional_blocks[0]
        return AltiumSchDoc(
            header_parameters=header_parameters,
            additional_parameters=additional_parameters,
            records=records,
            storage_data=streams.get("Storage"),
            raw_streams=dict(streams),
        )

    @staticmethod
    def read(path: Path) -> AltiumSchDoc:
        streams: dict[str, bytes] = {}
        with path.open("rb") as handle:
            cfb = CompoundFileBinary(handle, mode="rb")
            try:

                def recurse(prefix: str) -> None:
                    for name, entry in cfb.listdir_dict(prefix).items():
                        full_path = (
                            f"{prefix.rstrip('/')}/{name}"
                            if prefix != "/"
                            else f"/{name}"
                        )
                        if entry.type == "storage":
                            recurse(full_path)
                            continue
                        streams[full_path.strip("/")] = bytes(
                            cfb.open(full_path, mode="r").read()
                        )

                recurse("/")
            finally:
                cfb.close()
        return SchDocCodec.decode(streams)

    @staticmethod
    def write(doc: AltiumSchDoc, path: Path) -> None:
        from faebryk.libs.eda.altium.lib.cfb_writer import CfbWriter

        streams = SchDocCodec.encode(doc)
        cfb = CfbWriter()
        for stream_path, data in streams.items():
            cfb.add_stream(stream_path, data)
        cfb.write(path)


__all__ = [
    "ComponentRecordCodec",
    "JunctionRecordCodec",
    "NetLabelRecordCodec",
    "ParameterBlockCodec",
    "ParameterRecordCodec",
    "PinRecordCodec",
    "PowerObjectRecordCodec",
    "RecordCodec",
    "SchDocCodec",
    "WireRecordCodec",
]
