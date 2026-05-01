# This file is part of the faebryk project
# SPDX-License-Identifier: MIT

"""Bidirectional conversion between Altium files and low-level LL records.

The decoder implemented here is a strict inverse of the writer formats in this
module. It is intended to support:

- ``file -> ll`` for files produced by this exporter
- ``ll -> file`` for PcbDoc emission
- stable ``file -> ll -> file`` roundtrip tests at the exporter boundary
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
from dataclasses import asdict, dataclass
from pathlib import Path

from aaf2.cfb import CompoundFileBinary

from faebryk.libs.eda.altium.models.constants import (
    ALTIUM_LAYER_NAMES,
    ALTIUM_LAYER_SHORT_NAMES,
    MULTI_LAYER,
    TOTAL_LAYER_COUNT,
    to_mil,
)
from faebryk.libs.eda.altium.models.pcb.ll import (
    AltiumArc,
    AltiumBoardVertex,
    AltiumComponent,
    AltiumFill,
    AltiumNet,
    AltiumPad,
    AltiumPcbDoc,
    AltiumRegion,
    AltiumRule,
    AltiumText,
    AltiumTrack,
    AltiumVia,
)

_STREAM_GROUPS = (
    "Board6",
    "Nets6",
    "Components6",
    "Tracks6",
    "Vias6",
    "Pads6",
    "Rules6",
    "Arcs6",
    "Texts6",
    "Fills6",
    "ShapeBasedRegions6",
    "Polygons6",
    "Regions6",
    "Classes6",
    "ComponentBodies6",
    "Dimensions6",
    "Models",
    "WideStrings6",
)
_REWRITTEN_STREAM_GROUPS = {
    "Board6",
    "Nets6",
    "Components6",
    "Tracks6",
    "Vias6",
    "Pads6",
    "Arcs6",
    "Texts6",
    "Fills6",
    "ShapeBasedRegions6",
}
_FINGERPRINTABLE_STREAM_GROUPS = _REWRITTEN_STREAM_GROUPS | {"Rules6"}

_RECORD_TYPE_ARC = 1
_RECORD_TYPE_PAD = 2
_RECORD_TYPE_VIA = 3
_RECORD_TYPE_TRACK = 4
_RECORD_TYPE_TEXT = 5
_RECORD_TYPE_FILL = 6
_RECORD_TYPE_REGION = 11

_SHORT_LAYER_TO_NUMBER = {
    name: number for number, name in ALTIUM_LAYER_SHORT_NAMES.items()
}
_V9_STACK_THICKNESS_RE = re.compile(r"^V9_STACK_LAYER(\d+)_(COPTHICK|DIELHEIGHT)$")


def _parse_mil(value: str, *, default: int = 0) -> int:
    if not value:
        return default
    text = value.strip().lower()
    if text.endswith("mil"):
        text = text[:-3]
    return round(float(text) * 10_000)


def _decode_pascal_string(data: bytes) -> str:
    if not data:
        return ""
    size = data[0]
    return data[1 : 1 + size].decode("cp1252", errors="replace")


def _encode_pascal_string(text: str) -> bytes:
    encoded = text.encode("cp1252", errors="replace")[:255]
    return struct.pack("<B", len(encoded)) + encoded


def _decode_utf16le_zstring(data: bytes) -> str:
    return data.decode("utf-16le", errors="ignore").split("\x00", 1)[0]


def _encode_utf16le_fixed(text: str, size: int) -> bytes:
    encoded = text.encode("utf-16le", errors="ignore")[:size]
    if len(encoded) < size:
        encoded += b"\x00" * (size - len(encoded))
    return encoded


def _ll_semantic_fingerprint(doc: AltiumPcbDoc) -> str:
    payload = {
        "nets": [asdict(item) for item in doc.nets],
        "components": [asdict(item) for item in doc.components],
        "pads": [asdict(item) for item in doc.pads],
        "tracks": [asdict(item) for item in doc.tracks],
        "arcs": [asdict(item) for item in doc.arcs],
        "texts": [asdict(item) for item in doc.texts],
        "fills": [asdict(item) for item in doc.fills],
        "vias": [asdict(item) for item in doc.vias],
        "regions": [asdict(item) for item in doc.regions],
        "board_vertices": [asdict(item) for item in doc.board_vertices],
        "rules": [asdict(item) for item in doc.rules],
        "layer_count": doc.layer_count,
        "board_thickness": doc.board_thickness,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stream_group_payload(doc: AltiumPcbDoc, group: str) -> object:
    if group == "Board6":
        return {
            "layer_count": doc.layer_count,
            "board_thickness": doc.board_thickness,
            "board_vertices": [asdict(item) for item in doc.board_vertices],
        }
    if group == "Nets6":
        return [asdict(item) for item in doc.nets]
    if group == "Components6":
        return [asdict(item) for item in doc.components]
    if group == "Tracks6":
        return [asdict(item) for item in doc.tracks]
    if group == "Vias6":
        return [asdict(item) for item in doc.vias]
    if group == "Pads6":
        return [asdict(item) for item in doc.pads]
    if group == "Rules6":
        return [asdict(item) for item in doc.rules]
    if group == "Arcs6":
        return [asdict(item) for item in doc.arcs]
    if group == "Texts6":
        return [asdict(item) for item in doc.texts]
    if group == "Fills6":
        return [asdict(item) for item in doc.fills]
    if group == "ShapeBasedRegions6":
        return [asdict(item) for item in doc.regions]
    raise KeyError(group)


def _compute_stream_fingerprints(doc: AltiumPcbDoc) -> dict[str, str]:
    fingerprints: dict[str, str] = {}
    for group in sorted(_FINGERPRINTABLE_STREAM_GROUPS):
        payload = _stream_group_payload(doc, group)
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        fingerprints[group] = hashlib.sha256(encoded).hexdigest()
    return fingerprints


def _encode_property_record(props: dict[str, str]) -> bytes:
    text = "|" + "|".join(f"{k}={v}" for k, v in props.items()) + "|"
    encoded = text.encode("cp1252", errors="replace")
    return struct.pack("<I", len(encoded) + 1) + encoded + b"\x00"


def _decode_property_record(data: bytes) -> dict[str, str]:
    if len(data) < 4:
        raise ValueError("property record too short")
    size = struct.unpack_from("<I", data, 0)[0]
    payload = data[4 : 4 + size]
    if payload.endswith(b"\x00"):
        text_bytes = payload[:-1]
    else:
        # Some real PcbDoc corpora store property-record payloads without the
        # trailing NUL even though the surrounding size prefix is otherwise
        # valid. Decode those records as-is so unchanged documents can still be
        # parsed and preserved byte-for-byte via raw-stream passthrough.
        text_bytes = payload
    text = text_bytes.decode("cp1252", errors="replace")
    props: dict[str, str] = {}
    for item in text.split("|"):
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        props[key] = value
    return props


def _u16_component(value: int) -> int:
    return -1 if value == 0xFFFF else value


def _u16_net(value: int) -> int:
    return -1 if value == 0xFFFF else value


def _decode_binary_records(
    data: bytes,
    count: int,
    decoder,
) -> list:
    cursor = BinaryCursor(data)
    records = []
    for _ in range(count):
        chunk = cursor.data[cursor.offset :]
        record = decoder(chunk[: _record_size(chunk)])
        cursor.offset += _record_size(chunk)
        records.append(record)
    cursor.ensure_consumed()
    return records


def _record_size(data: bytes) -> int:
    record_type = data[0]
    if record_type in {
        _RECORD_TYPE_ARC,
        _RECORD_TYPE_VIA,
        _RECORD_TYPE_TRACK,
        _RECORD_TYPE_FILL,
        _RECORD_TYPE_REGION,
    }:
        return 5 + struct.unpack_from("<I", data, 1)[0]
    if record_type == _RECORD_TYPE_TEXT:
        sr1_len = struct.unpack_from("<I", data, 1)[0]
        sr2_offset = 5 + sr1_len
        sr2_len = struct.unpack_from("<I", data, sr2_offset)[0]
        return sr2_offset + 4 + sr2_len
    if record_type == _RECORD_TYPE_PAD:
        offset = 1
        for _ in range(6):
            sr_len = struct.unpack_from("<I", data, offset)[0]
            offset += 4 + sr_len
        return offset
    raise ValueError(f"unsupported record type {record_type}")


def _decode_property_records(
    data: bytes,
    count: int,
    decoder,
    *,
    with_index: bool = False,
) -> list:
    cursor = BinaryCursor(data)
    records = []
    for index in range(count):
        record_len = struct.unpack_from("<I", cursor.data, cursor.offset)[0]
        total_len = 4 + record_len
        chunk = cursor.read_exact(total_len)
        records.append(decoder(chunk, index) if with_index else decoder(chunk))
    cursor.ensure_consumed()
    return records


def _decode_optional_property_records(
    data: bytes,
    count: int,
    decoder,
) -> list:
    try:
        return _decode_property_records(data, count, decoder)
    except KeyError, UnicodeDecodeError, ValueError, struct.error:
        return []


@dataclass
class BinaryCursor:
    data: bytes
    offset: int = 0

    def read_exact(self, size: int) -> bytes:
        end = self.offset + size
        if end > len(self.data):
            raise ValueError("unexpected end of record")
        chunk = self.data[self.offset : end]
        self.offset = end
        return chunk

    def read_u8(self) -> int:
        return struct.unpack("<B", self.read_exact(1))[0]

    def read_u32(self) -> int:
        return struct.unpack("<I", self.read_exact(4))[0]

    def read_subrecord(self) -> bytes:
        return self.read_exact(self.read_u32())

    def ensure_consumed(self) -> None:
        if self.offset != len(self.data):
            raise ValueError(
                f"record has trailing data: {len(self.data) - self.offset} bytes"
            )


class HeaderCodec:
    @staticmethod
    def encode(record_count: int) -> bytes:
        return struct.pack("<I", record_count)

    @staticmethod
    def decode(data: bytes) -> int:
        if len(data) != 4:
            raise ValueError("header stream must be exactly 4 bytes")
        return struct.unpack("<I", data)[0]


class FileHeaderCodec:
    _TITLE = b"PCB 6.0 Binary File"

    @classmethod
    def encode(cls) -> bytes:
        return struct.pack("<IB", 1 + len(cls._TITLE), len(cls._TITLE)) + cls._TITLE

    @classmethod
    def decode(cls, data: bytes) -> None:
        if len(data) < 4:
            raise ValueError("FileHeader stream too short")
        subrecord_len = struct.unpack_from("<I", data, 0)[0]
        payload = data[4:]

        # Writer shape: uint32(len+1) + uint8(len) + ASCII bytes
        if (
            subrecord_len <= len(payload)
            and payload
            and payload[0] + 1 == subrecord_len
        ):
            string_len = payload[0]
            title = payload[1 : 1 + string_len]
            if title != cls._TITLE:
                raise ValueError(f"unexpected FileHeader title: {title!r}")
            return

        # External files observed so far store a UTF-16LE prefix like:
        # uint32(chars) + UTF-16LE("PCB 5.0 Bi")
        if len(payload) % 2 == 0:
            try:
                title = payload.decode("utf-16le", errors="strict")
            except UnicodeDecodeError:
                title = ""
            if title.startswith("PCB "):
                return

        raise ValueError("unrecognized FileHeader encoding")


class BoardCodec:
    @staticmethod
    def _decode_props(data: bytes) -> dict[str, str]:
        return _decode_property_record(data)

    @staticmethod
    def _derive_v9_stack_thickness(props: dict[str, str]) -> int:
        thickness_by_layer: dict[int, dict[str, int]] = {}
        for key, value in props.items():
            match = _V9_STACK_THICKNESS_RE.match(key)
            if match is None:
                continue
            layer_index = int(match.group(1))
            field_name = match.group(2)
            parsed = _parse_mil(value, default=0)
            if parsed <= 0:
                continue
            thickness_by_layer.setdefault(layer_index, {})[field_name] = parsed
        return sum(
            layer_fields.get("COPTHICK", 0) + layer_fields.get("DIELHEIGHT", 0)
            for _, layer_fields in sorted(thickness_by_layer.items())
        )

    @staticmethod
    def encode(doc: AltiumPcbDoc) -> bytes:
        layer_count = max(doc.layer_count, 2)
        props: dict[str, str] = {
            "RECORD": "Board",
            "ORIGINX": to_mil(0),
            "ORIGINY": to_mil(0),
            "BOARDTHICKNESS": to_mil(doc.board_thickness),
            "LAYERSETSCOUNT": str(layer_count - 1),
        }

        used_layers: list[int] = [1]
        for i in range(2, layer_count):
            used_layers.append(i)
        used_layers.append(32)

        for i in range(1, TOTAL_LAYER_COUNT + 1):
            prefix = f"LAYER{i}"
            props[f"{prefix}NAME"] = doc.layer_names.get(
                i,
                ALTIUM_LAYER_NAMES.get(i, f"Layer {i}"),
            )
            if i <= 32:
                props[f"{prefix}COPTHICK"] = "1.4mil"
                props[f"{prefix}DIELCONST"] = "4.800"
                props[f"{prefix}DIELHEIGHT"] = "12.6mil"
                props[f"{prefix}DIELMATERIAL"] = "FR-4"
                props[f"{prefix}DIELTYPE"] = "0"
                if i in used_layers:
                    idx = used_layers.index(i)
                    props[f"{prefix}NEXT"] = str(
                        used_layers[idx + 1] if idx + 1 < len(used_layers) else 0
                    )
                    props[f"{prefix}PREV"] = str(used_layers[idx - 1] if idx > 0 else 0)
                else:
                    props[f"{prefix}NEXT"] = "0"
                    props[f"{prefix}PREV"] = "0"
            else:
                props[f"{prefix}NEXT"] = "0"
                props[f"{prefix}PREV"] = "0"
                props[f"{prefix}COPTHICK"] = "0mil"
                props[f"{prefix}DIELCONST"] = "0"
                props[f"{prefix}DIELHEIGHT"] = "0mil"
                props[f"{prefix}DIELMATERIAL"] = ""
                props[f"{prefix}DIELTYPE"] = "0"
            props[f"{prefix}MECHENABLED"] = "FALSE"

        for i, vertex in enumerate(doc.board_vertices):
            props[f"VX{i}"] = to_mil(vertex.x)
            props[f"VY{i}"] = to_mil(vertex.y)
            props[f"KIND{i}"] = "0"

        return _encode_property_record(props)

    @staticmethod
    def decode(data: bytes) -> tuple[int, int, list[AltiumBoardVertex], dict[int, str]]:
        props = BoardCodec._decode_props(data)
        layer_count = max(2, int(props.get("LAYERSETSCOUNT", "1")) + 1)
        board_thickness = _parse_mil(props.get("BOARDTHICKNESS", ""), default=0)
        if board_thickness <= 0:
            board_thickness = BoardCodec._derive_v9_stack_thickness(props)
        layer_names = {
            layer: props[f"LAYER{layer}NAME"]
            for layer in range(1, TOTAL_LAYER_COUNT + 1)
            if props.get(f"LAYER{layer}NAME")
        }
        vertices: list[AltiumBoardVertex] = []
        index = 0
        while f"VX{index}" in props and f"VY{index}" in props:
            vertices.append(
                AltiumBoardVertex(
                    x=_parse_mil(props[f"VX{index}"]),
                    y=_parse_mil(props[f"VY{index}"]),
                )
            )
            index += 1
        return layer_count, board_thickness, vertices, layer_names


class NetCodec:
    @staticmethod
    def encode(net: AltiumNet) -> bytes:
        return _encode_property_record(
            {
                "RECORD": "Net",
                "ID": str(net.index),
                "NAME": net.name,
            }
        )

    @staticmethod
    def decode(data: bytes, index: int) -> AltiumNet:
        props = _decode_property_record(data)
        return AltiumNet(index=int(props.get("ID", index)), name=props.get("NAME", ""))


class ComponentCodec:
    @staticmethod
    def encode(comp: AltiumComponent) -> bytes:
        layer_name = ALTIUM_LAYER_SHORT_NAMES.get(comp.layer, "TOP")
        return _encode_property_record(
            {
                "RECORD": "Component",
                "LAYER": layer_name,
                "PATTERN": comp.footprint_name,
                "X": to_mil(comp.x),
                "Y": to_mil(comp.y),
                "ROTATION": f"{comp.rotation:.6f}",
                "SOURCEDESIGNATOR": comp.designator,
                "NAMEON": "TRUE" if comp.name_on else "FALSE",
                "COMMENTON": "TRUE" if comp.comment_on else "FALSE",
                "SOURCEFOOTPRINTLIBRARY": "",
                "SOURCECOMPONENTLIBRARY": "",
                "SOURCELIBREFERENCE": comp.footprint_name,
            }
        )

    @staticmethod
    def decode(data: bytes, index: int) -> AltiumComponent:
        props = _decode_property_record(data)
        return AltiumComponent(
            index=index,
            designator=props.get("SOURCEDESIGNATOR", ""),
            footprint_name=props.get("PATTERN", ""),
            x=_parse_mil(props.get("X", ""), default=0),
            y=_parse_mil(props.get("Y", ""), default=0),
            rotation=float(props.get("ROTATION", "0")),
            layer=_SHORT_LAYER_TO_NUMBER.get(props.get("LAYER", "TOP"), 1),
            name_on=props.get("NAMEON", "TRUE") == "TRUE",
            comment_on=props.get("COMMENTON", "FALSE") == "TRUE",
        )


class TrackCodec:
    _STRUCT = struct.Struct("<BBBHHHIiiiiiHB")

    @classmethod
    def encode(cls, track: AltiumTrack) -> bytes:
        subrecord = cls._STRUCT.pack(
            track.layer & 0xFF,
            0,
            0,
            track.net & 0xFFFF,
            0xFFFF,
            0xFFFF if track.component < 0 else (track.component & 0xFFFF),
            0,
            track.x1,
            track.y1,
            track.x2,
            track.y2,
            track.width,
            0,
            0,
        )
        return struct.pack("<BI", _RECORD_TYPE_TRACK, len(subrecord)) + subrecord

    @classmethod
    def decode(cls, data: bytes) -> AltiumTrack:
        cursor = BinaryCursor(data)
        record_type = cursor.read_u8()
        if record_type != _RECORD_TYPE_TRACK:
            raise ValueError(f"unexpected track record type: {record_type}")
        subrecord = cursor.read_exact(cursor.read_u32())
        cursor.ensure_consumed()
        (
            layer,
            _flags1,
            _flags2,
            net,
            _polygon,
            component,
            _padding,
            x1,
            y1,
            x2,
            y2,
            width,
            _subpolyindex,
            _padding2,
        ) = cls._STRUCT.unpack(subrecord[: cls._STRUCT.size])
        return AltiumTrack(
            layer=layer,
            net=_u16_net(net),
            x1=x1,
            y1=y1,
            x2=x2,
            y2=y2,
            width=width,
            component=_u16_component(component),
        )


class ViaCodec:
    _STRUCT = struct.Struct("<BBBHHHIiiiiBB")

    @classmethod
    def encode(cls, via: AltiumVia) -> bytes:
        subrecord = cls._STRUCT.pack(
            0,
            0,
            0,
            via.net & 0xFFFF,
            0,
            0xFFFF,
            0,
            via.x,
            via.y,
            via.diameter,
            via.hole_size,
            via.start_layer & 0xFF,
            via.end_layer & 0xFF,
        )
        return struct.pack("<BI", _RECORD_TYPE_VIA, len(subrecord)) + subrecord

    @classmethod
    def decode(cls, data: bytes) -> AltiumVia:
        cursor = BinaryCursor(data)
        record_type = cursor.read_u8()
        if record_type != _RECORD_TYPE_VIA:
            raise ValueError(f"unexpected via record type: {record_type}")
        subrecord = cursor.read_exact(cursor.read_u32())
        cursor.ensure_consumed()
        (
            _unknown,
            _flags1,
            _flags2,
            net,
            _padding1,
            _component,
            _padding2,
            x,
            y,
            diameter,
            hole_size,
            start_layer,
            end_layer,
        ) = cls._STRUCT.unpack(subrecord[: cls._STRUCT.size])
        return AltiumVia(
            x=x,
            y=y,
            diameter=diameter,
            hole_size=hole_size,
            start_layer=start_layer,
            end_layer=end_layer,
            net=_u16_net(net),
        )


class PadCodec:
    _STRUCT = struct.Struct("<BBBHHHIiiiiiiiiiBBBdBBB")

    @classmethod
    def encode(cls, pad: AltiumPad) -> bytes:
        pad_layer = MULTI_LAYER if pad.is_tht else pad.layer
        name_pascal = _encode_pascal_string(pad.name)
        sr1 = struct.pack("<I", len(name_pascal)) + name_pascal
        sr2 = struct.pack("<I", 0)
        sr3 = struct.pack("<I", 0)
        sr4 = struct.pack("<I", 0)

        plated = 1 if pad.plated else 0
        sr5_data = cls._STRUCT.pack(
            pad_layer & 0xFF,
            0,
            0,
            pad.net & 0xFFFF,
            0,
            0xFFFF if pad.component < 0 else (pad.component & 0xFFFF),
            0,
            pad.x,
            pad.y,
            pad.top_size_x,
            pad.top_size_y,
            pad.mid_size_x,
            pad.mid_size_y,
            pad.bot_size_x,
            pad.bot_size_y,
            pad.hole_size,
            pad.shape & 0xFF,
            pad.shape & 0xFF,
            pad.shape & 0xFF,
            pad.rotation,
            plated,
            0,
            0,
        )
        sr5_data += b"\x00" * 23
        sr5_data += struct.pack("<ii", 0, 0)
        sr5_data += b"\x00" * 7
        sr5_data += struct.pack("<BB", 0, 0)
        sr5_data += b"\x00" * 3
        sr5_data += struct.pack("<I", 0)
        sr5 = struct.pack("<I", len(sr5_data)) + sr5_data

        if pad.slot_size > 0:
            sr6_data = bytearray(596)
            for i in range(29):
                struct.pack_into("<i", sr6_data, i * 4, pad.top_size_x)
                struct.pack_into("<i", sr6_data, 116 + i * 4, pad.top_size_y)
                sr6_data[232 + i] = pad.shape & 0xFF
            sr6_data[262] = 2
            struct.pack_into("<i", sr6_data, 263, pad.slot_size)
            struct.pack_into("<d", sr6_data, 267, pad.slot_rotation)
            for i in range(32):
                sr6_data[532 + i] = pad.shape & 0xFF
            sr6 = struct.pack("<I", len(sr6_data)) + bytes(sr6_data)
        else:
            sr6 = struct.pack("<I", 0)

        return struct.pack("<B", _RECORD_TYPE_PAD) + sr1 + sr2 + sr3 + sr4 + sr5 + sr6

    @classmethod
    def decode(cls, data: bytes) -> AltiumPad:
        cursor = BinaryCursor(data)
        record_type = cursor.read_u8()
        if record_type != _RECORD_TYPE_PAD:
            raise ValueError(f"unexpected pad record type: {record_type}")
        sr1 = cursor.read_subrecord()
        _sr2 = cursor.read_subrecord()
        _sr3 = cursor.read_subrecord()
        _sr4 = cursor.read_subrecord()
        sr5 = cursor.read_subrecord()
        sr6 = cursor.read_subrecord()
        cursor.ensure_consumed()

        if len(sr5) < 110:
            raise ValueError("pad subrecord 5 too short")

        (
            layer,
            _flags1,
            _flags2,
            net,
            _padding1,
            component,
            _padding2,
            x,
            y,
            top_size_x,
            top_size_y,
            mid_size_x,
            mid_size_y,
            bot_size_x,
            bot_size_y,
            hole_size,
            top_shape,
            _mid_shape,
            _bot_shape,
            rotation,
            plated,
            _padding3,
            _pad_mode,
        ) = cls._STRUCT.unpack(sr5[:63])

        is_tht = layer == MULTI_LAYER
        resolved_layer = 1 if is_tht else layer
        slot_size = 0
        slot_rotation = 0.0
        if len(sr6) >= 275 and sr6[262] == 2:
            slot_size = struct.unpack_from("<i", sr6, 263)[0]
            slot_rotation = struct.unpack_from("<d", sr6, 267)[0]

        return AltiumPad(
            component=_u16_component(component),
            name=_decode_pascal_string(sr1),
            x=x,
            y=y,
            top_size_x=top_size_x,
            top_size_y=top_size_y,
            mid_size_x=mid_size_x,
            mid_size_y=mid_size_y,
            bot_size_x=bot_size_x,
            bot_size_y=bot_size_y,
            hole_size=hole_size,
            shape=top_shape,
            rotation=rotation,
            net=_u16_net(net),
            layer=resolved_layer,
            is_tht=is_tht,
            plated=bool(plated),
            slot_size=slot_size,
            slot_rotation=slot_rotation,
        )


class ArcCodec:
    _STRUCT = struct.Struct("<BBBHHHIiiiddiH")

    @classmethod
    def encode(cls, arc: AltiumArc) -> bytes:
        subrecord = cls._STRUCT.pack(
            arc.layer & 0xFF,
            0,
            0,
            arc.net & 0xFFFF,
            0xFFFF,
            0xFFFF if arc.component < 0 else (arc.component & 0xFFFF),
            0,
            arc.center_x,
            arc.center_y,
            arc.radius,
            arc.start_angle,
            arc.end_angle,
            arc.width,
            0,
        )
        return struct.pack("<BI", _RECORD_TYPE_ARC, len(subrecord)) + subrecord

    @classmethod
    def decode(cls, data: bytes) -> AltiumArc:
        cursor = BinaryCursor(data)
        record_type = cursor.read_u8()
        if record_type != _RECORD_TYPE_ARC:
            raise ValueError(f"unexpected arc record type: {record_type}")
        subrecord = cursor.read_exact(cursor.read_u32())
        cursor.ensure_consumed()
        (
            layer,
            _flags1,
            _flags2,
            net,
            _polygon,
            component,
            _padding,
            center_x,
            center_y,
            radius,
            start_angle,
            end_angle,
            width,
            _subpolyindex,
        ) = cls._STRUCT.unpack(subrecord[: cls._STRUCT.size])
        return AltiumArc(
            layer=layer,
            net=_u16_net(net),
            component=_u16_component(component),
            center_x=center_x,
            center_y=center_y,
            radius=radius,
            start_angle=start_angle,
            end_angle=end_angle,
            width=width,
        )


class TextCodec:
    @staticmethod
    def encode(text: AltiumText) -> bytes:
        needs_extended_fields = any(
            (
                text.stroke_font_type != 1,
                text.is_comment,
                text.is_designator,
                text.font_type != 0,
                text.is_bold,
                text.is_italic,
                bool(text.font_name),
                text.is_inverted,
                text.is_inverted_rect,
                text.is_frame,
                text.is_offset_border,
                text.is_justification_valid,
                text.margin_border_width,
                text.textbox_rect_width,
                text.textbox_rect_height,
                text.text_offset_width,
                text.text_justification != 3,
            )
        )
        sr1 = bytearray(252 if needs_extended_fields else 137)
        struct.pack_into("<B", sr1, 0, text.layer & 0xFF)
        struct.pack_into(
            "<H", sr1, 7, 0xFFFF if text.component < 0 else (text.component & 0xFFFF)
        )
        struct.pack_into("<i", sr1, 13, text.x)
        struct.pack_into("<i", sr1, 17, text.y)
        struct.pack_into("<i", sr1, 21, text.height)
        struct.pack_into("<H", sr1, 25, text.stroke_font_type & 0xFFFF)
        struct.pack_into("<d", sr1, 27, text.rotation)
        sr1[35] = 1 if text.is_mirrored else 0
        struct.pack_into("<i", sr1, 36, text.stroke_width)
        if len(sr1) >= 137:
            sr1[40] = 1 if text.is_comment else 0
            sr1[41] = 1 if text.is_designator else 0
            sr1[43] = text.font_type & 0xFF
            sr1[44] = 1 if text.is_bold else 0
            sr1[45] = 1 if text.is_italic else 0
            sr1[46:110] = _encode_utf16le_fixed(text.font_name, 64)
            sr1[110] = 1 if text.is_inverted else 0
            struct.pack_into("<i", sr1, 111, text.margin_border_width)
            struct.pack_into("<I", sr1, 115, 0)
            sr1[123] = 1 if text.is_inverted_rect else 0
            struct.pack_into("<i", sr1, 124, text.textbox_rect_width)
            struct.pack_into("<i", sr1, 128, text.textbox_rect_height)
            sr1[132] = text.text_justification & 0xFF
            struct.pack_into("<i", sr1, 133, text.text_offset_width)
        if len(sr1) >= 240:
            sr1[230] = 1 if text.is_frame else 0
            sr1[231] = 1 if text.is_offset_border else 0
        if len(sr1) >= 241:
            sr1[240] = 1 if text.is_justification_valid else 0
        sr2 = _encode_pascal_string(text.text)
        return (
            struct.pack("<B", _RECORD_TYPE_TEXT)
            + struct.pack("<I", len(sr1))
            + bytes(sr1)
            + struct.pack("<I", len(sr2))
            + sr2
        )

    @staticmethod
    def decode(data: bytes) -> AltiumText:
        cursor = BinaryCursor(data)
        record_type = cursor.read_u8()
        if record_type != _RECORD_TYPE_TEXT:
            raise ValueError(f"unexpected text record type: {record_type}")
        sr1 = cursor.read_subrecord()
        sr2 = cursor.read_subrecord()
        cursor.ensure_consumed()
        if len(sr1) < 40:
            raise ValueError("text subrecord 1 too short")
        is_comment = False
        is_designator = False
        font_type = 0
        is_bold = False
        is_italic = False
        font_name = ""
        is_inverted = False
        is_inverted_rect = False
        is_frame = False
        is_offset_border = False
        is_justification_valid = False
        margin_border_width = 0
        textbox_rect_width = 0
        textbox_rect_height = 0
        text_offset_width = 0
        text_justification = 3
        if len(sr1) >= 123:
            is_comment = bool(sr1[40])
            is_designator = bool(sr1[41])
            font_type = sr1[43]
            is_bold = bool(sr1[44])
            is_italic = bool(sr1[45])
            font_name = _decode_utf16le_zstring(sr1[46:110])
            is_inverted = bool(sr1[110])
            margin_border_width = struct.unpack_from("<i", sr1, 111)[0]
            is_inverted_rect = bool(sr1[123])
            textbox_rect_width = struct.unpack_from("<i", sr1, 124)[0]
            textbox_rect_height = struct.unpack_from("<i", sr1, 128)[0]
            text_justification = sr1[132]
            text_offset_width = struct.unpack_from("<i", sr1, 133)[0]
        if len(sr1) >= 240:
            is_frame = bool(sr1[230])
            is_offset_border = bool(sr1[231])
        elif textbox_rect_width and textbox_rect_height:
            is_frame = True
        if len(sr1) >= 241:
            is_justification_valid = bool(sr1[240])
        return AltiumText(
            layer=sr1[0],
            component=_u16_component(struct.unpack_from("<H", sr1, 7)[0]),
            x=struct.unpack_from("<i", sr1, 13)[0],
            y=struct.unpack_from("<i", sr1, 17)[0],
            height=struct.unpack_from("<i", sr1, 21)[0],
            rotation=struct.unpack_from("<d", sr1, 27)[0],
            is_mirrored=bool(sr1[35]),
            stroke_width=struct.unpack_from("<i", sr1, 36)[0],
            text=_decode_pascal_string(sr2),
            stroke_font_type=struct.unpack_from("<H", sr1, 25)[0],
            is_comment=is_comment,
            is_designator=is_designator,
            font_type=font_type,
            is_bold=is_bold,
            is_italic=is_italic,
            font_name=font_name,
            is_inverted=is_inverted,
            is_inverted_rect=is_inverted_rect,
            is_frame=is_frame,
            is_offset_border=is_offset_border,
            is_justification_valid=is_justification_valid,
            margin_border_width=margin_border_width,
            textbox_rect_width=textbox_rect_width,
            textbox_rect_height=textbox_rect_height,
            text_offset_width=text_offset_width,
            text_justification=text_justification,
        )


class FillCodec:
    _STRUCT = struct.Struct("<BBBHHHIiiiid")

    @classmethod
    def encode(cls, fill: AltiumFill) -> bytes:
        subrecord = cls._STRUCT.pack(
            fill.layer & 0xFF,
            0,
            0,
            fill.net & 0xFFFF,
            0,
            0xFFFF if fill.component < 0 else (fill.component & 0xFFFF),
            0,
            fill.x1,
            fill.y1,
            fill.x2,
            fill.y2,
            fill.rotation,
        )
        return struct.pack("<BI", _RECORD_TYPE_FILL, len(subrecord)) + subrecord

    @classmethod
    def decode(cls, data: bytes) -> AltiumFill:
        cursor = BinaryCursor(data)
        record_type = cursor.read_u8()
        if record_type != _RECORD_TYPE_FILL:
            raise ValueError(f"unexpected fill record type: {record_type}")
        subrecord = cursor.read_exact(cursor.read_u32())
        cursor.ensure_consumed()
        (
            layer,
            _flags1,
            _flags2,
            net,
            _padding1,
            component,
            _padding2,
            x1,
            y1,
            x2,
            y2,
            rotation,
        ) = cls._STRUCT.unpack(subrecord[: cls._STRUCT.size])
        return AltiumFill(
            layer=layer,
            net=_u16_net(net),
            component=_u16_component(component),
            x1=x1,
            y1=y1,
            x2=x2,
            y2=y2,
            rotation=rotation,
        )


class RegionCodec:
    @staticmethod
    def encode(region: AltiumRegion) -> bytes:
        flags2 = 0x02 if region.is_keepout else 0
        header = struct.pack(
            "<BBBHHHxxxxxHxx",
            region.layer & 0xFF,
            0,
            flags2,
            region.net & 0xFFFF,
            0xFFFF,
            0xFFFF if region.component < 0 else (region.component & 0xFFFF),
            len(region.holes),
        )
        if region.is_keepout:
            props_text = (
                f"|KIND=0|ISBOARDCUTOUT=FALSE|SUBPOLYINDEX=65535"
                f"|KEEPOUTRESTRICTIONS={region.keepout_restrictions}|"
            )
        else:
            props_text = "|KIND=0|ISBOARDCUTOUT=FALSE|SUBPOLYINDEX=65535|"
        props_encoded = props_text.encode("cp1252") + b"\x00"
        props_block = struct.pack("<I", len(props_encoded)) + props_encoded

        vertex_data = struct.pack("<I", max(0, len(region.outline) - 1))
        for x, y in region.outline:
            vertex_data += struct.pack("<BiiiiIdd", 0, x, y, 0, 0, 0, 0.0, 0.0)
        for hole in region.holes:
            vertex_data += struct.pack("<I", len(hole))
            for x, y in hole:
                vertex_data += struct.pack("<dd", float(x), float(y))

        sr1_data = header + props_block + vertex_data
        return struct.pack("<BI", _RECORD_TYPE_REGION, len(sr1_data)) + sr1_data

    @staticmethod
    def decode(data: bytes) -> AltiumRegion:
        cursor = BinaryCursor(data)
        record_type = cursor.read_u8()
        if record_type != _RECORD_TYPE_REGION:
            raise ValueError(f"unexpected region record type: {record_type}")
        sr1 = cursor.read_exact(cursor.read_u32())
        cursor.ensure_consumed()
        if len(sr1) < 18:
            raise ValueError("region subrecord too short")
        (
            layer,
            _flags1,
            flags2,
            net,
            _polygon,
            component,
            hole_count,
        ) = struct.unpack_from("<BBBHHHxxxxxHxx", sr1, 0)
        props_len = struct.unpack_from("<I", sr1, 18)[0]
        props_start = 22
        props_end = props_start + props_len
        props = _decode_property_record(sr1[18:props_end])

        cursor2 = BinaryCursor(sr1[props_end:])
        stored_vertex_count = cursor2.read_u32()
        outline_count = stored_vertex_count + 1 if stored_vertex_count > 0 else 0
        outline: list[tuple[int, int]] = []
        for _ in range(outline_count):
            _is_round = cursor2.read_u8()
            x, y, _cx, _cy, _radius, _start, _end = struct.unpack(
                "<iiiiIdd", cursor2.read_exact(36)
            )
            outline.append((x, y))
        holes: list[list[tuple[int, int]]] = []
        for _ in range(hole_count):
            hole_vertices = cursor2.read_u32()
            hole: list[tuple[int, int]] = []
            for _ in range(hole_vertices):
                x, y = struct.unpack("<dd", cursor2.read_exact(16))
                hole.append((round(x), round(y)))
            holes.append(hole)
        cursor2.ensure_consumed()

        return AltiumRegion(
            layer=layer,
            net=_u16_net(net),
            component=_u16_component(component),
            outline=outline,
            holes=holes,
            is_keepout=bool(flags2 & 0x02),
            keepout_restrictions=int(props.get("KEEPOUTRESTRICTIONS", "0")),
        )


class RuleCodec:
    @staticmethod
    def encode(rule: AltiumRule) -> bytes:
        props = dict(rule.properties)
        props["RECORD"] = "Rule"
        props["RULEKIND"] = rule.kind
        props["NAME"] = rule.name
        return _encode_property_record(props)

    @staticmethod
    def decode(data: bytes) -> AltiumRule:
        props = _decode_property_record(data)
        return AltiumRule(
            kind=props.pop("RULEKIND"),
            name=props.pop("NAME"),
            properties={key: value for key, value in props.items() if key != "RECORD"},
        )


class PcbDocCodec:
    @staticmethod
    def encode(doc: AltiumPcbDoc) -> dict[str, bytes]:
        current_stream_fingerprints = _compute_stream_fingerprints(doc)
        if (
            doc.raw_streams
            and doc.semantic_fingerprint is not None
            and doc.semantic_fingerprint == _ll_semantic_fingerprint(doc)
        ):
            return dict(doc.raw_streams)

        streams: dict[str, bytes] = dict(doc.raw_streams)

        streams.setdefault("FileHeader", FileHeaderCodec.encode())

        def preserve_group(group: str) -> bool:
            previous = doc.stream_fingerprints.get(group)
            if previous is None or previous != current_stream_fingerprints[group]:
                return False
            return f"{group}/Header" in streams and f"{group}/Data" in streams

        if not preserve_group("Board6"):
            streams["Board6/Header"] = HeaderCodec.encode(1)
            streams["Board6/Data"] = BoardCodec.encode(doc)
        if not preserve_group("Nets6"):
            streams["Nets6/Header"] = HeaderCodec.encode(len(doc.nets))
            streams["Nets6/Data"] = b"".join(NetCodec.encode(n) for n in doc.nets)
        if not preserve_group("Components6"):
            streams["Components6/Header"] = HeaderCodec.encode(len(doc.components))
            streams["Components6/Data"] = b"".join(
                ComponentCodec.encode(c) for c in doc.components
            )
        if not preserve_group("Tracks6"):
            streams["Tracks6/Header"] = HeaderCodec.encode(len(doc.tracks))
            streams["Tracks6/Data"] = b"".join(TrackCodec.encode(t) for t in doc.tracks)
        if not preserve_group("Vias6"):
            streams["Vias6/Header"] = HeaderCodec.encode(len(doc.vias))
            streams["Vias6/Data"] = b"".join(ViaCodec.encode(v) for v in doc.vias)
        if not preserve_group("Pads6"):
            streams["Pads6/Header"] = HeaderCodec.encode(len(doc.pads))
            streams["Pads6/Data"] = b"".join(PadCodec.encode(p) for p in doc.pads)
        if not preserve_group("Arcs6"):
            streams["Arcs6/Header"] = HeaderCodec.encode(len(doc.arcs))
            streams["Arcs6/Data"] = b"".join(ArcCodec.encode(a) for a in doc.arcs)
        if not preserve_group("Texts6"):
            streams["Texts6/Header"] = HeaderCodec.encode(len(doc.texts))
            streams["Texts6/Data"] = b"".join(TextCodec.encode(t) for t in doc.texts)
        if not preserve_group("Fills6"):
            streams["Fills6/Header"] = HeaderCodec.encode(len(doc.fills))
            streams["Fills6/Data"] = b"".join(FillCodec.encode(f) for f in doc.fills)
        if not preserve_group("ShapeBasedRegions6"):
            streams["ShapeBasedRegions6/Header"] = HeaderCodec.encode(len(doc.regions))
            streams["ShapeBasedRegions6/Data"] = b"".join(
                RegionCodec.encode(r) for r in doc.regions
            )

        if not preserve_group("Rules6"):
            if (
                doc.rules
                or "Rules6/Header" not in streams
                or "Rules6/Data" not in streams
            ):
                streams["Rules6/Header"] = HeaderCodec.encode(len(doc.rules))
                streams["Rules6/Data"] = b"".join(
                    RuleCodec.encode(r) for r in doc.rules
                )

        for name in _STREAM_GROUPS:
            header_key = f"{name}/Header"
            data_key = f"{name}/Data"
            if name in _REWRITTEN_STREAM_GROUPS or name == "Rules6":
                continue
            streams.setdefault(header_key, HeaderCodec.encode(0))
            streams.setdefault(data_key, b"")
        doc.stream_fingerprints = current_stream_fingerprints
        return streams

    @staticmethod
    def decode(streams: dict[str, bytes]) -> AltiumPcbDoc:
        FileHeaderCodec.decode(streams["FileHeader"])

        board_count = HeaderCodec.decode(streams["Board6/Header"])
        if board_count != 1:
            raise ValueError(f"expected exactly one Board6 record, got {board_count}")
        layer_count, board_thickness, board_vertices, layer_names = BoardCodec.decode(
            streams["Board6/Data"]
        )

        nets = _decode_property_records(
            streams["Nets6/Data"],
            HeaderCodec.decode(streams["Nets6/Header"]),
            NetCodec.decode,
            with_index=True,
        )
        components = _decode_property_records(
            streams["Components6/Data"],
            HeaderCodec.decode(streams["Components6/Header"]),
            ComponentCodec.decode,
            with_index=True,
        )
        tracks = _decode_binary_records(
            streams["Tracks6/Data"],
            HeaderCodec.decode(streams["Tracks6/Header"]),
            TrackCodec.decode,
        )
        vias = _decode_binary_records(
            streams["Vias6/Data"],
            HeaderCodec.decode(streams["Vias6/Header"]),
            ViaCodec.decode,
        )
        pads = _decode_binary_records(
            streams["Pads6/Data"],
            HeaderCodec.decode(streams["Pads6/Header"]),
            PadCodec.decode,
        )
        rules = _decode_optional_property_records(
            streams["Rules6/Data"],
            HeaderCodec.decode(streams["Rules6/Header"]),
            RuleCodec.decode,
        )
        arcs = _decode_binary_records(
            streams["Arcs6/Data"],
            HeaderCodec.decode(streams["Arcs6/Header"]),
            ArcCodec.decode,
        )
        texts = _decode_binary_records(
            streams["Texts6/Data"],
            HeaderCodec.decode(streams["Texts6/Header"]),
            TextCodec.decode,
        )
        fills = _decode_binary_records(
            streams["Fills6/Data"],
            HeaderCodec.decode(streams["Fills6/Header"]),
            FillCodec.decode,
        )
        regions = _decode_binary_records(
            streams["ShapeBasedRegions6/Data"],
            HeaderCodec.decode(streams["ShapeBasedRegions6/Header"]),
            RegionCodec.decode,
        )

        doc = AltiumPcbDoc(
            nets=nets,
            components=components,
            pads=pads,
            tracks=tracks,
            arcs=arcs,
            texts=texts,
            fills=fills,
            vias=vias,
            regions=regions,
            board_vertices=board_vertices,
            rules=rules,
            layer_count=layer_count,
            board_thickness=board_thickness,
            layer_names=layer_names,
            raw_streams=dict(streams),
        )
        doc.semantic_fingerprint = _ll_semantic_fingerprint(doc)
        doc.stream_fingerprints = _compute_stream_fingerprints(doc)
        return doc

    @staticmethod
    def read(path: Path) -> AltiumPcbDoc:
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
        if "FileHeader" not in streams:
            raise ValueError("missing /FileHeader stream")
        return PcbDocCodec.decode(streams)

    @staticmethod
    def write(doc: AltiumPcbDoc, path: Path) -> None:
        from faebryk.libs.eda.altium.lib.cfb_writer import CfbWriter

        streams = PcbDocCodec.encode(doc)
        cfb = CfbWriter()
        for stream_path, data in streams.items():
            cfb.add_stream(stream_path, data)
        cfb.write(path)


__all__ = [
    "ArcCodec",
    "BoardCodec",
    "ComponentCodec",
    "FillCodec",
    "FileHeaderCodec",
    "HeaderCodec",
    "NetCodec",
    "PadCodec",
    "PcbDocCodec",
    "RegionCodec",
    "RuleCodec",
    "TextCodec",
    "TrackCodec",
    "ViaCodec",
]
