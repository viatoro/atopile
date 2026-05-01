# This file is part of the faebryk project
# SPDX-License-Identifier: MIT

"""Bidirectional translation between Altium LL records and the IL."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import struct
from dataclasses import dataclass

from faebryk.libs.eda.altium.models import constants
from faebryk.libs.eda.altium.models.pcb import il, ll

logger = logging.getLogger(__name__)

_SHORT_LAYER_TO_NUMBER = {
    name: number for number, name in constants.ALTIUM_LAYER_SHORT_NAMES.items()
}


@dataclass
class Context:
    net_id_to_index: dict[str | None, int]
    component_id_to_index: dict[str | None, int]
    net_id_by_index: dict[int, str]
    component_id_by_index: dict[int, str]
    warnings: list[str]


def _il_semantic_fingerprint(doc: il.AltiumPcb) -> str:
    payload = doc.to_dict()
    payload.pop("source_raw_streams", None)
    payload.pop("source_semantic_fingerprint", None)
    payload.pop("source_ll_semantic_fingerprint", None)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _adapt_layer(layer: int | il.LayerReference | None) -> int:
    if isinstance(layer, int):
        return layer
    if isinstance(layer, il.LayerReference):
        if layer.altium_layer_number is None:
            logger.warning(
                "LayerReference %s missing numeric layer; defaulting to top layer.",
                layer.layer_id,
            )
            return 1
        return layer.altium_layer_number
    return 1


def _encode_net_ref(net_id: str | None, ctx: Context) -> int:
    if net_id is None:
        return -1
    return ctx.net_id_to_index.get(net_id, -1)


def _decode_net_id(net_ref: int, ctx: Context) -> str | None:
    if net_ref < 0:
        return None
    return ctx.net_id_by_index.get(net_ref)


def _decode_polygon_metadata(
    raw_streams: dict[str, bytes],
) -> list[tuple[int | None, int | None]]:
    header = raw_streams.get("Polygons6/Header")
    data = raw_streams.get("Polygons6/Data")
    if header is None or data is None or len(header) != 4:
        return []
    count = struct.unpack("<I", header)[0]
    offset = 0
    metadata: list[tuple[int | None, int | None]] = []
    for _ in range(count):
        if offset + 4 > len(data):
            break
        size = struct.unpack_from("<I", data, offset)[0]
        payload = data[offset + 4 : offset + 4 + size]
        offset += 4 + size
        if not payload.endswith(b"\x00"):
            continue
        text = payload[:-1].decode("cp1252", errors="replace")
        props: dict[str, str] = {}
        for item in text.split("|"):
            if not item or "=" not in item:
                continue
            key, value = item.split("=", 1)
            props[key] = value
        layer = _SHORT_LAYER_TO_NUMBER.get(props.get("LAYER", ""))
        net_value = props.get("NET")
        net = int(net_value) if net_value is not None else None
        metadata.append((layer, net))
    return metadata


class NetCodec:
    @staticmethod
    def encode(net: il.AltiumNet, index: int, ctx: Context) -> ll.AltiumNet:
        if net.id is None:
            ctx.warnings.append(
                "Encountered IL net without id; defaulting to anonymous "
                "net index mapping."
            )
        else:
            ctx.net_id_to_index[net.id] = index
        return ll.AltiumNet(index=index, name=net.name)

    @staticmethod
    def decode(net: ll.AltiumNet, ctx: Context) -> il.AltiumNet:
        net_id = f"net-{net.index + 1}"
        ctx.net_id_by_index[net.index] = net_id
        return il.AltiumNet(id=net_id, name=net.name)


class ComponentCodec:
    @staticmethod
    def encode(
        component: il.AltiumComponent, index: int, ctx: Context
    ) -> ll.AltiumComponent:
        if component.id is None:
            ctx.warnings.append(
                "Encountered IL component without id; component ownership "
                "may downgrade to free component."
            )
        else:
            ctx.component_id_to_index[component.id] = index
        return ll.AltiumComponent(
            index=index,
            designator=component.designator,
            footprint_name=component.footprint,
            x=component.x,
            y=component.y,
            rotation=component.rotation,
            layer=component.layer,
            name_on=component.name_on,
            comment_on=component.comment_on,
        )

    @staticmethod
    def decode(component: ll.AltiumComponent, ctx: Context) -> il.AltiumComponent:
        component_id = f"component-{component.index + 1}"
        ctx.component_id_by_index[component.index] = component_id
        return il.AltiumComponent(
            id=component_id,
            designator=component.designator,
            footprint=component.footprint_name,
            x=component.x,
            y=component.y,
            rotation=component.rotation,
            layer=component.layer,
            name_on=component.name_on,
            comment_on=component.comment_on,
        )


class PadCodec:
    _IL_PAD_SHAPES = {
        il.AltiumPadShape.ROUND: 1,
        il.AltiumPadShape.RECT: 2,
        il.AltiumPadShape.OCTAGONAL: 3,
        il.AltiumPadShape.ROUND_RECT: 2,
    }

    @staticmethod
    def _ll_pad_shape(shape: int) -> il.AltiumPadShape:
        if shape == constants.PAD_SHAPE_RECT:
            return il.AltiumPadShape.RECT
        if shape == constants.PAD_SHAPE_OCTAGONAL:
            return il.AltiumPadShape.OCTAGONAL
        return il.AltiumPadShape.ROUND

    @staticmethod
    def encode(pad: il.AltiumPad, ctx: Context) -> ll.AltiumPad:
        component_index = ctx.component_id_to_index.get(pad.component_id, -1)
        if (
            pad.component_id is not None
            and pad.component_id not in ctx.component_id_to_index
        ):
            ctx.warnings.append(
                f"Pad {pad.id!r} references unknown component "
                f"{pad.component_id!r}; stored as free pad."
            )
        shape = PadCodec._IL_PAD_SHAPES.get(pad.shape)
        if shape is None:
            ctx.warnings.append(
                f"Pad {pad.id!r} shape '{pad.shape}' downgraded to round."
            )
            shape = PadCodec._IL_PAD_SHAPES[il.AltiumPadShape.ROUND]
        return ll.AltiumPad(
            component=component_index,
            name=pad.name,
            x=pad.x,
            y=pad.y,
            top_size_x=pad.top_size_x,
            top_size_y=pad.top_size_y,
            mid_size_x=pad.mid_size_x,
            mid_size_y=pad.mid_size_y,
            bot_size_x=pad.bot_size_x,
            bot_size_y=pad.bot_size_y,
            hole_size=pad.hole_size,
            shape=shape,
            rotation=pad.rotation,
            net=_encode_net_ref(pad.net_id, ctx),
            layer=_adapt_layer(pad.layer),
            is_tht=pad.is_tht,
            plated=pad.plated,
            slot_size=getattr(pad, "slot_size", 0),
            slot_rotation=getattr(pad, "slot_rotation", 0.0),
        )

    @staticmethod
    def decode(pad: ll.AltiumPad, ctx: Context) -> il.AltiumPad:
        return il.AltiumPad(
            id=f"pad-{pad.component}-{pad.name}",
            component_id=ctx.component_id_by_index.get(pad.component),
            name=pad.name,
            layer=pad.layer,
            net_id=_decode_net_id(pad.net, ctx),
            x=pad.x,
            y=pad.y,
            top_size_x=pad.top_size_x,
            top_size_y=pad.top_size_y,
            mid_size_x=pad.mid_size_x,
            mid_size_y=pad.mid_size_y,
            bot_size_x=pad.bot_size_x,
            bot_size_y=pad.bot_size_y,
            hole_size=pad.hole_size,
            shape=PadCodec._ll_pad_shape(pad.shape),
            rotation=pad.rotation,
            is_tht=pad.is_tht,
            plated=pad.plated,
            slot_size=pad.slot_size,
            slot_rotation=pad.slot_rotation,
        )


class TrackCodec:
    @staticmethod
    def encode(track: il.AltiumTrack, ctx: Context) -> ll.AltiumTrack:
        return ll.AltiumTrack(
            layer=_adapt_layer(track.layer),
            net=_encode_net_ref(track.net_id, ctx),
            x1=track.x1,
            y1=track.y1,
            x2=track.x2,
            y2=track.y2,
            width=track.width,
            component=ctx.component_id_to_index.get(track.component_id, -1),
        )

    @staticmethod
    def decode(track: ll.AltiumTrack, seq: int, ctx: Context) -> il.AltiumTrack:
        return il.AltiumTrack(
            id=f"track-{track.net}-{seq}",
            component_id=ctx.component_id_by_index.get(track.component),
            layer=track.layer,
            net_id=_decode_net_id(track.net, ctx),
            x1=track.x1,
            y1=track.y1,
            x2=track.x2,
            y2=track.y2,
            width=track.width,
        )


class ViaCodec:
    @staticmethod
    def encode(via: il.AltiumVia, ctx: Context) -> ll.AltiumVia:
        return ll.AltiumVia(
            x=via.x,
            y=via.y,
            diameter=via.diameter,
            hole_size=via.hole_size,
            start_layer=via.start_layer,
            end_layer=via.end_layer,
            net=_encode_net_ref(via.net_id, ctx),
        )

    @staticmethod
    def decode(via: ll.AltiumVia, ctx: Context) -> il.AltiumVia:
        return il.AltiumVia(
            id=f"via-{via.x}-{via.y}",
            net_id=_decode_net_id(via.net, ctx),
            x=via.x,
            y=via.y,
            diameter=via.diameter,
            hole_size=via.hole_size,
            start_layer=via.start_layer,
            end_layer=via.end_layer,
        )


class ArcCodec:
    @staticmethod
    def encode(arc: il.AltiumArc, ctx: Context) -> ll.AltiumArc:
        return ll.AltiumArc(
            layer=_adapt_layer(arc.layer),
            net=_encode_net_ref(arc.net_id, ctx),
            component=ctx.component_id_to_index.get(arc.component_id, -1),
            center_x=arc.center_x,
            center_y=arc.center_y,
            radius=arc.radius,
            start_angle=arc.start_angle,
            end_angle=arc.end_angle,
            width=arc.width,
        )

    @staticmethod
    def decode(arc: ll.AltiumArc, seq: int, ctx: Context) -> il.AltiumArc:
        return il.AltiumArc(
            id=f"arc-{seq}",
            component_id=ctx.component_id_by_index.get(arc.component),
            layer=arc.layer,
            net_id=_decode_net_id(arc.net, ctx),
            center_x=arc.center_x,
            center_y=arc.center_y,
            radius=arc.radius,
            start_angle=arc.start_angle,
            end_angle=arc.end_angle,
            width=arc.width,
        )


class TextCodec:
    @staticmethod
    def encode(text: il.AltiumText, ctx: Context) -> ll.AltiumText:
        if text.layer is None:
            ctx.warnings.append(f"Text {text.id!r} has no layer; defaulting to top.")
        return ll.AltiumText(
            layer=_adapt_layer(text.layer),
            component=ctx.component_id_to_index.get(text.component_id, -1),
            x=text.x,
            y=text.y,
            height=text.height,
            rotation=text.rotation,
            is_mirrored=text.is_mirrored,
            stroke_width=text.stroke_width,
            text=text.text,
            stroke_font_type=text.stroke_font_type,
            is_comment=text.is_comment,
            is_designator=text.is_designator,
            font_type=text.font_type,
            is_bold=text.is_bold,
            is_italic=text.is_italic,
            font_name=text.font_name,
            is_inverted=text.is_inverted,
            is_inverted_rect=text.is_inverted_rect,
            is_frame=text.is_frame,
            is_offset_border=text.is_offset_border,
            is_justification_valid=text.is_justification_valid,
            margin_border_width=text.margin_border_width,
            textbox_rect_width=text.textbox_rect_width,
            textbox_rect_height=text.textbox_rect_height,
            text_offset_width=text.text_offset_width,
            text_justification=text.text_justification,
        )

    @staticmethod
    def decode(text: ll.AltiumText, seq: int, ctx: Context) -> il.AltiumText:
        return il.AltiumText(
            id=f"text-{seq}",
            component_id=ctx.component_id_by_index.get(text.component),
            layer=text.layer,
            net_id=getattr(text, "net", None),
            x=text.x,
            y=text.y,
            height=text.height,
            rotation=text.rotation,
            text=text.text,
            is_mirrored=text.is_mirrored,
            stroke_width=text.stroke_width,
            stroke_font_type=text.stroke_font_type,
            is_comment=text.is_comment,
            is_designator=text.is_designator,
            font_type=text.font_type,
            is_bold=text.is_bold,
            is_italic=text.is_italic,
            font_name=text.font_name,
            is_inverted=text.is_inverted,
            is_inverted_rect=text.is_inverted_rect,
            is_frame=text.is_frame,
            is_offset_border=text.is_offset_border,
            is_justification_valid=text.is_justification_valid,
            margin_border_width=text.margin_border_width,
            textbox_rect_width=text.textbox_rect_width,
            textbox_rect_height=text.textbox_rect_height,
            text_offset_width=text.text_offset_width,
            text_justification=text.text_justification,
        )


class FillCodec:
    @staticmethod
    def encode(fill: il.AltiumFill, ctx: Context) -> ll.AltiumFill:
        return ll.AltiumFill(
            layer=_adapt_layer(fill.layer),
            net=_encode_net_ref(fill.net_id, ctx),
            component=ctx.component_id_to_index.get(fill.component_id, -1),
            x1=fill.x1,
            y1=fill.y1,
            x2=fill.x2,
            y2=fill.y2,
            rotation=getattr(fill, "rotation", 0.0),
        )

    @staticmethod
    def decode(fill: ll.AltiumFill, seq: int, ctx: Context) -> il.AltiumFill:
        return il.AltiumFill(
            id=f"fill-{seq}",
            component_id=ctx.component_id_by_index.get(fill.component),
            layer=fill.layer,
            net_id=_decode_net_id(fill.net, ctx),
            x1=fill.x1,
            y1=fill.y1,
            x2=fill.x2,
            y2=fill.y2,
            rotation=fill.rotation,
        )


class RegionCodec:
    @staticmethod
    def encode(region: il.AltiumRegion, ctx: Context) -> ll.AltiumRegion:
        return ll.AltiumRegion(
            layer=_adapt_layer(region.layer),
            net=_encode_net_ref(region.net_id, ctx),
            component=ctx.component_id_to_index.get(region.component_id, -1),
            outline=list(region.outline),
            holes=[list(hole) for hole in region.holes],
            is_keepout=region.is_keepout,
            keepout_restrictions=region.keepout_restrictions,
        )

    @staticmethod
    def decode(region: ll.AltiumRegion, seq: int, ctx: Context) -> il.AltiumRegion:
        return il.AltiumRegion(
            id=f"region-{seq}",
            component_id=ctx.component_id_by_index.get(region.component),
            layer=region.layer,
            net_id=_decode_net_id(region.net, ctx),
            outline=region.outline,
            holes=[list(hole) for hole in region.holes],
            is_keepout=region.is_keepout,
            keepout_restrictions=region.keepout_restrictions,
        )


class RuleCodec:
    @staticmethod
    def _to_int(value: object) -> int:
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            v = value.strip()
            if v.endswith("mil"):
                return int(round(float(v[:-3]) * 10000))
            try:
                return int(round(float(v)))
            except ValueError:
                return 0
        return 0

    @staticmethod
    def encode(rule: il.AltiumRule, ctx: Context) -> ll.AltiumRule:
        scope1 = str(rule.scope1 or "All")
        scope2 = str(rule.scope2 or "All")
        if isinstance(rule, il.AltiumRuleClearance):
            return ll.AltiumRule(
                kind="Clearance",
                name=rule.name,
                properties={
                    "MINIMUM": rule.gap,
                    "SCOPE1EXPRESSION": scope1,
                    "SCOPE2EXPRESSION": scope2,
                },
            )
        if isinstance(rule, il.AltiumRuleWidth):
            return ll.AltiumRule(
                kind="Width",
                name=rule.name,
                properties={
                    "MINLIMIT": rule.min_limit,
                    "MAXLIMIT": rule.max_limit,
                    "PREFERRED": rule.preferred,
                    "SCOPE1EXPRESSION": scope1,
                    "SCOPE2EXPRESSION": scope2,
                },
            )
        if isinstance(rule, il.AltiumRuleHoleSize):
            return ll.AltiumRule(
                kind="HoleSize",
                name=rule.name,
                properties={
                    "MINLIMIT": rule.min_limit,
                    "MAXLIMIT": rule.max_limit,
                    "SCOPE1EXPRESSION": scope1,
                    "SCOPE2EXPRESSION": scope2,
                },
            )
        if isinstance(rule, il.AltiumRuleRoutingVias):
            return ll.AltiumRule(
                kind="RoutingVias",
                name=rule.name,
                properties={
                    "WIDTH": rule.width,
                    "MINWIDTH": rule.min_width,
                    "MAXWIDTH": rule.max_width,
                    "HOLEWIDTH": rule.hole_width,
                    "MINHOLEWIDTH": rule.min_hole_width,
                    "MAXHOLEWIDTH": rule.max_hole_width,
                    "SCOPE1EXPRESSION": scope1,
                    "SCOPE2EXPRESSION": scope2,
                },
            )
        if isinstance(rule, il.AltiumRuleSolderMaskExpansion):
            return ll.AltiumRule(
                kind="SolderMaskExpansion",
                name=rule.name,
                properties={
                    "EXPANSION": rule.expansion,
                    "SCOPE1EXPRESSION": scope1,
                    "SCOPE2EXPRESSION": scope2,
                },
            )
        if isinstance(rule, il.AltiumRulePasteMaskExpansion):
            return ll.AltiumRule(
                kind="PasteMaskExpansion",
                name=rule.name,
                properties={
                    "EXPANSION": rule.expansion,
                    "SCOPE1EXPRESSION": scope1,
                    "SCOPE2EXPRESSION": scope2,
                },
            )
        if isinstance(rule, il.AltiumRulePolygonConnectStyle):
            return ll.AltiumRule(
                kind="PolygonConnectStyle",
                name=rule.name,
                properties={
                    "CONNECTSTYLE": str(rule.connect_style),
                    "AIRGAPWIDTH": rule.air_gap_width,
                    "RELIEFCONDUCTORWIDTH": rule.relief_conductor_width,
                    "RELIEFENTRIES": rule.relief_entries,
                    "SCOPE1EXPRESSION": scope1,
                    "SCOPE2EXPRESSION": scope2,
                },
            )
        ctx.warnings.append(
            f"Rule kind {type(rule).__name__} is not mapped to low-level "
            "typed fields; using property bag fallback."
        )
        return ll.AltiumRule(
            kind=str(getattr(rule, "kind", "generic")),
            name=rule.name,
            properties={
                "SCOPE1EXPRESSION": scope1,
                "SCOPE2EXPRESSION": scope2,
            },
        )

    @staticmethod
    def decode(rule: ll.AltiumRule, ctx: Context) -> il.AltiumRule:
        scope1 = rule.properties.get("SCOPE1EXPRESSION", "All")
        scope2 = rule.properties.get("SCOPE2EXPRESSION", "All")
        if rule.kind == "Clearance":
            return il.AltiumRuleClearance(
                name=rule.name,
                scope1=str(scope1),
                scope2=str(scope2),
                gap=RuleCodec._to_int(rule.properties.get("MINIMUM")),
                extra_properties=dict(rule.properties),
            )
        if rule.kind == "Width":
            return il.AltiumRuleWidth(
                name=rule.name,
                scope1=str(scope1),
                scope2=str(scope2),
                min_limit=RuleCodec._to_int(rule.properties.get("MINLIMIT")),
                max_limit=RuleCodec._to_int(rule.properties.get("MAXLIMIT")),
                preferred=RuleCodec._to_int(rule.properties.get("PREFERRED")),
                extra_properties=dict(rule.properties),
            )
        if rule.kind == "HoleSize":
            return il.AltiumRuleHoleSize(
                name=rule.name,
                scope1=str(scope1),
                scope2=str(scope2),
                min_limit=RuleCodec._to_int(rule.properties.get("MINLIMIT")),
                max_limit=RuleCodec._to_int(rule.properties.get("MAXLIMIT")),
                extra_properties=dict(rule.properties),
            )
        if rule.kind == "RoutingVias":
            return il.AltiumRuleRoutingVias(
                name=rule.name,
                scope1=str(scope1),
                scope2=str(scope2),
                width=RuleCodec._to_int(rule.properties.get("WIDTH")),
                min_width=RuleCodec._to_int(rule.properties.get("MINWIDTH")),
                max_width=RuleCodec._to_int(rule.properties.get("MAXWIDTH")),
                hole_width=RuleCodec._to_int(rule.properties.get("HOLEWIDTH")),
                min_hole_width=RuleCodec._to_int(rule.properties.get("MINHOLEWIDTH")),
                max_hole_width=RuleCodec._to_int(rule.properties.get("MAXHOLEWIDTH")),
                extra_properties=dict(rule.properties),
            )
        if rule.kind == "SolderMaskExpansion":
            return il.AltiumRuleSolderMaskExpansion(
                name=rule.name,
                scope1=str(scope1),
                scope2=str(scope2),
                expansion=RuleCodec._to_int(rule.properties.get("EXPANSION")),
                extra_properties=dict(rule.properties),
            )
        if rule.kind == "PasteMaskExpansion":
            return il.AltiumRulePasteMaskExpansion(
                name=rule.name,
                scope1=str(scope1),
                scope2=str(scope2),
                expansion=RuleCodec._to_int(rule.properties.get("EXPANSION")),
                extra_properties=dict(rule.properties),
            )
        if rule.kind == "PolygonConnectStyle":
            connect_style = str(rule.properties.get("CONNECTSTYLE", "direct"))
            return il.AltiumRulePolygonConnectStyle(
                name=rule.name,
                scope1=str(scope1),
                scope2=str(scope2),
                connect_style=connect_style,  # type: ignore[arg-type]
                air_gap_width=RuleCodec._to_int(rule.properties.get("AIRGAPWIDTH")),
                relief_conductor_width=RuleCodec._to_int(
                    rule.properties.get("RELIEFCONDUCTORWIDTH")
                ),
                relief_entries=RuleCodec._to_int(rule.properties.get("RELIEFENTRIES")),
                extra_properties=dict(rule.properties),
            )
        return il.AltiumRule(name=rule.name, extra_properties=dict(rule.properties))


class BoardCodec:
    @staticmethod
    def _append_vertex(
        vertices: list[ll.AltiumBoardVertex],
        point: tuple[int | float, int | float],
        *,
        allow_consecutive_duplicate: bool = False,
    ) -> None:
        x = int(round(point[0]))
        y = int(round(point[1]))
        if (
            not allow_consecutive_duplicate
            and vertices
            and (vertices[-1].x, vertices[-1].y) == (x, y)
        ):
            return
        vertices.append(ll.AltiumBoardVertex(x=x, y=y))

    @staticmethod
    def _outline_arc_points(
        segment: il.BoardOutlineSegment,
    ) -> list[tuple[float, float]]:
        if segment.center is None or segment.radius is None:
            return [segment.start, segment.end]
        if segment.start_angle_deg is None or segment.end_angle_deg is None:
            return [segment.start, segment.end]
        cx, cy = segment.center
        sweep = segment.end_angle_deg - segment.start_angle_deg
        if sweep <= 0:
            sweep += 360.0
        steps = max(8, int(abs(sweep) / 10))
        points: list[tuple[float, float]] = []
        for i in range(steps + 1):
            angle = math.radians(segment.start_angle_deg + sweep * (i / steps))
            x = cx + segment.radius * math.cos(angle)
            y = cy - segment.radius * math.sin(angle)
            points.append((x, y))
        return points

    @staticmethod
    def _collect_used_ll_layers(doc: ll.AltiumPcbDoc) -> set[int]:
        used_layers = {1}
        used_layers.update(pad.layer for pad in doc.pads if isinstance(pad.layer, int))
        used_layers.update(
            track.layer for track in doc.tracks if isinstance(track.layer, int)
        )
        used_layers.update(arc.layer for arc in doc.arcs if isinstance(arc.layer, int))
        used_layers.update(
            text.layer for text in doc.texts if isinstance(text.layer, int)
        )
        used_layers.update(
            fill.layer for fill in doc.fills if isinstance(fill.layer, int)
        )
        used_layers.update(
            region.layer for region in doc.regions if isinstance(region.layer, int)
        )
        for via in doc.vias:
            used_layers.add(via.start_layer)
            used_layers.add(via.end_layer)
        for component in doc.components:
            used_layers.add(component.layer)
        return {layer for layer in used_layers if layer > 0}

    @staticmethod
    def _adapt_board_outline(doc: ll.AltiumPcbDoc) -> list[il.BoardOutlineSegment]:
        if hasattr(doc, "board_outline_segments"):
            return doc.board_outline_segments  # type: ignore[return-value]
        outline: list[il.BoardOutlineSegment] = []
        vertices = doc.board_vertices
        if len(vertices) < 2:
            return outline
        for idx, current in enumerate(vertices[:-1]):
            next_vertex = vertices[idx + 1]
            outline.append(
                il.BoardOutlineSegment(
                    start=(current.x, current.y),
                    end=(next_vertex.x, next_vertex.y),
                    kind="line",
                )
            )
        return outline

    @staticmethod
    def _layer_kind(layer_number: int) -> il.AltiumLayerType:
        if 1 <= layer_number <= 32:
            return il.AltiumLayerType.COPPER
        if layer_number in (33, 34):
            return il.AltiumLayerType.OVERLAY
        if layer_number in (35, 36):
            return il.AltiumLayerType.PASTE
        if layer_number in (37, 38):
            return il.AltiumLayerType.MASK
        return il.AltiumLayerType.MECHANICAL

    @staticmethod
    def encode_outline(doc: il.AltiumPcb, ctx: Context) -> list[ll.AltiumBoardVertex]:
        vertices: list[ll.AltiumBoardVertex] = []
        for segment in doc.board.outline:
            if segment.kind == "arc":
                ctx.warnings.append(
                    f"Board outline arc '{segment.start}' -> '{segment.end}' "
                    "converted into sampled vertices."
                )
                points = BoardCodec._outline_arc_points(segment)
                if points:
                    if not vertices:
                        BoardCodec._append_vertex(vertices, points[0])
                        points = points[1:]
                    elif (vertices[-1].x, vertices[-1].y) != (
                        int(round(points[0][0])),
                        int(round(points[0][1])),
                    ):
                        BoardCodec._append_vertex(vertices, points[0])
                        points = points[1:]
                for point in points:
                    BoardCodec._append_vertex(vertices, point)
                continue
            if not vertices:
                BoardCodec._append_vertex(vertices, segment.start)
            elif (vertices[-1].x, vertices[-1].y) != (
                int(round(segment.start[0])),
                int(round(segment.start[1])),
            ):
                BoardCodec._append_vertex(vertices, segment.start)
            BoardCodec._append_vertex(
                vertices,
                segment.end,
                allow_consecutive_duplicate=True,
            )
        if (
            doc.board.extra_properties.get("ll_outline_closed")
            and vertices
            and (vertices[0].x, vertices[0].y) != (vertices[-1].x, vertices[-1].y)
        ):
            vertices.append(ll.AltiumBoardVertex(x=vertices[0].x, y=vertices[0].y))
        return vertices

    @staticmethod
    def decode(doc: ll.AltiumPcbDoc) -> il.BoardConfig:
        used_layers = BoardCodec._collect_used_ll_layers(doc)
        copper_layer_ids = [
            f"layer-{layer}"
            for layer in sorted(
                layer_num for layer_num in used_layers if 1 <= layer_num <= 32
            )
        ]
        board_layers = [
            il.BoardLayer(
                id=f"layer-{layer}",
                name=doc.layer_names.get(
                    layer,
                    constants.ALTIUM_LAYER_NAMES.get(layer, f"Layer {layer}"),
                ),
                kind=BoardCodec._layer_kind(layer),
                altium_layer_number=layer,
                source_name=doc.layer_names.get(layer),
            )
            for layer in sorted(used_layers)
        ]
        return il.BoardConfig(
            name=None,
            board_thickness=doc.board_thickness,
            layers=board_layers,
            copper_ordering=il.BoardCopperOrdering(ordered_layer_ids=copper_layer_ids),
            outline=BoardCodec._adapt_board_outline(doc),
            extra_properties={
                "ll_layer_count": doc.layer_count,
                "ll_outline_closed": bool(
                    doc.board_vertices
                    and len(doc.board_vertices) > 1
                    and doc.board_vertices[0] == doc.board_vertices[-1]
                ),
            },
        )


class PcbCodec:
    @staticmethod
    def encode(doc: il.AltiumPcb) -> ll.AltiumPcbDoc:
        ctx = Context({}, {}, {}, {}, [])
        result = ll.AltiumPcbDoc()
        result.nets = [
            NetCodec.encode(net, idx, ctx) for idx, net in enumerate(doc.nets)
        ]
        result.components = [
            ComponentCodec.encode(component, idx, ctx)
            for idx, component in enumerate(doc.components)
        ]
        result.board_vertices = BoardCodec.encode_outline(doc, ctx)
        result.board_thickness = doc.board.board_thickness
        result.layer_count = max(
            int(
                doc.board.extra_properties.get(
                    "ll_layer_count",
                    len(doc.board.copper_ordering.ordered_layer_ids),
                )
            ),
            2,
        )
        result.layer_names = {
            layer.altium_layer_number: (layer.source_name or layer.name)
            for layer in doc.board.layers
            if layer.altium_layer_number is not None
        }
        for primitive in doc.primitives:
            if isinstance(primitive, il.AltiumPad):
                result.pads.append(PadCodec.encode(primitive, ctx))
            elif isinstance(primitive, il.AltiumTrack):
                result.tracks.append(TrackCodec.encode(primitive, ctx))
            elif isinstance(primitive, il.AltiumVia):
                result.vias.append(ViaCodec.encode(primitive, ctx))
            elif isinstance(primitive, il.AltiumArc):
                result.arcs.append(ArcCodec.encode(primitive, ctx))
            elif isinstance(primitive, il.AltiumText):
                result.texts.append(TextCodec.encode(primitive, ctx))
            elif isinstance(primitive, il.AltiumFill):
                result.fills.append(FillCodec.encode(primitive, ctx))
            elif isinstance(primitive, il.AltiumRegion):
                result.regions.append(RegionCodec.encode(primitive, ctx))
            else:
                ctx.warnings.append(
                    f"Unsupported primitive '{type(primitive).__name__}' "
                    "dropped at IL→LL seam."
                )
        result.rules = [RuleCodec.encode(rule, ctx) for rule in doc.rules]
        if doc.classes:
            ctx.warnings.append(
                "IL classes (net/component/pad/layer) are not representable in "
                "low-level records and were dropped during IL→LL translation."
            )
            for class_entry in doc.classes:
                if isinstance(
                    class_entry,
                    (
                        il.AltiumClassNet,
                        il.AltiumClassComponent,
                        il.AltiumClassPad,
                        il.AltiumClassLayer,
                    ),
                ):
                    ctx.warnings.append(
                        f"Dropped class kind={class_entry.kind.value} "
                        f"id={class_entry.id!r} "
                        f"name={class_entry.name!r} in IL→LL translation."
                    )
                elif isinstance(class_entry, il.AltiumClass):
                    ctx.warnings.append(
                        f"Dropped unmapped class kind={class_entry.kind.value} "
                        f"id={class_entry.id!r} name={class_entry.name!r}."
                    )
        result.translation_warnings = ctx.warnings
        if doc.source_raw_streams:
            result.raw_streams = dict(doc.source_raw_streams)
            result.stream_fingerprints = dict(doc.source_stream_fingerprints)
        if (
            doc.source_raw_streams
            and doc.source_semantic_fingerprint is not None
            and doc.source_semantic_fingerprint == _il_semantic_fingerprint(doc)
        ):
            result.semantic_fingerprint = doc.source_ll_semantic_fingerprint
        if ctx.warnings:
            logger.debug("Altium IL→LL warnings: %s", ctx.warnings)
        return result

    @staticmethod
    def decode(doc: ll.AltiumPcbDoc) -> il.AltiumPcb:
        ctx = Context({}, {}, {}, {}, [])
        polygon_metadata = _decode_polygon_metadata(doc.raw_streams)
        il_nets = [NetCodec.decode(net, ctx) for net in doc.nets]
        il_components = [
            ComponentCodec.decode(component, ctx) for component in doc.components
        ]
        result = il.AltiumPcb(
            board=BoardCodec.decode(doc),
            nets=il_nets,
            components=il_components,
            classes=[],
            rules=[RuleCodec.decode(rule, ctx) for rule in doc.rules],
            primitives=[],
        )
        for pad in doc.pads:
            result.primitives.append(PadCodec.decode(pad, ctx))
        for track in doc.tracks:
            result.primitives.append(
                TrackCodec.decode(track, len(result.primitives), ctx)
            )
        for via in doc.vias:
            result.primitives.append(ViaCodec.decode(via, ctx))
        for arc in doc.arcs:
            result.primitives.append(ArcCodec.decode(arc, len(result.primitives), ctx))
        for text in doc.texts:
            result.primitives.append(
                TextCodec.decode(text, len(result.primitives), ctx)
            )
        for fill in doc.fills:
            result.primitives.append(
                FillCodec.decode(fill, len(result.primitives), ctx)
            )
        for index, region in enumerate(doc.regions):
            decoded_region = RegionCodec.decode(region, len(result.primitives), ctx)
            if index < len(polygon_metadata):
                polygon_layer, polygon_net = polygon_metadata[index]
                if polygon_layer is not None:
                    decoded_region.layer = polygon_layer
                if polygon_net is not None:
                    decoded_region.net_id = _decode_net_id(polygon_net, ctx)
            result.primitives.append(decoded_region)
        result.source_semantic_fingerprint = _il_semantic_fingerprint(result)
        result.source_raw_streams = dict(doc.raw_streams)
        result.source_ll_semantic_fingerprint = doc.semantic_fingerprint
        result.source_stream_fingerprints = dict(doc.stream_fingerprints)
        return result


def convert_il_to_ll(doc: il.AltiumPcb) -> ll.AltiumPcbDoc:
    return PcbCodec.encode(doc)


def convert_ll_to_il(doc: ll.AltiumPcbDoc) -> il.AltiumPcb:
    return PcbCodec.decode(doc)


__all__ = [
    "ArcCodec",
    "BoardCodec",
    "ComponentCodec",
    "Context",
    "FillCodec",
    "NetCodec",
    "PcbCodec",
    "PadCodec",
    "RegionCodec",
    "RuleCodec",
    "TextCodec",
    "TrackCodec",
    "ViaCodec",
    "convert_il_to_ll",
    "convert_ll_to_il",
]
