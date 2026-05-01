# This file is part of the faebryk project
# SPDX-License-Identifier: MIT

"""Bidirectional translation between KiCad PCBs and the Altium IL."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from faebryk.libs.eda.altium.models.constants import (
    ALTIUM_LAYER_NAMES,
    LAYER_MAP,
    PAD_SHAPE_MAP,
    PAD_SHAPE_OCTAGONAL,
    PAD_SHAPE_RECT,
    PAD_SHAPE_ROUND,
    altium_to_mm,
    mm_to_altium,
)
from faebryk.libs.eda.altium.models.pcb.il import (
    AltiumArc,
    AltiumComponent,
    AltiumFill,
    AltiumLayerType,
    AltiumNet,
    AltiumPad,
    AltiumPadShape,
    AltiumPcb,
    AltiumPolygonConnectStyle,
    AltiumRegion,
    AltiumRule,
    AltiumRuleClearance,
    AltiumRuleHoleSize,
    AltiumRulePolygonConnectStyle,
    AltiumRuleWidth,
    AltiumText,
    AltiumTrack,
    AltiumVia,
    BoardConfig,
    BoardCopperOrdering,
    BoardLayer,
    LayerReference,
)
from faebryk.libs.eda.altium.models.pcb.il import (
    BoardOutlineSegment as AltiumBoardOutlineSegment,
)
from faebryk.libs.kicad.fileformats import kicad

logger = logging.getLogger(__name__)

_ALTIUM_TO_KICAD_LAYER_NAMES = {
    value: key for key, value in LAYER_MAP.items() if value <= 38
}
_KICAD_LAYER_NUMBERS = {
    "F.Cu": 0,
    "B.Mask": 3,
    "In1.Cu": 4,
    "F.SilkS": 5,
    "In2.Cu": 6,
    "B.SilkS": 7,
    "In3.Cu": 8,
    "F.Adhes": 9,
    "In4.Cu": 10,
    "B.Adhes": 11,
    "In5.Cu": 12,
    "F.Paste": 13,
    "In6.Cu": 14,
    "B.Paste": 15,
    "In7.Cu": 16,
    "Dwgs.User": 17,
    "In8.Cu": 18,
    "Cmts.User": 19,
    "In9.Cu": 20,
    "Eco1.User": 21,
    "In10.Cu": 22,
    "Eco2.User": 23,
    "In11.Cu": 24,
    "Edge.Cuts": 25,
    "In12.Cu": 26,
    "Margin": 27,
    "In13.Cu": 28,
    "B.CrtYd": 29,
    "In14.Cu": 30,
    "F.CrtYd": 31,
    "B.Cu": 2,
    "In15.Cu": 32,
    "B.Fab": 33,
    "In16.Cu": 34,
    "F.Fab": 35,
    "In17.Cu": 36,
    "F.Mask": 1,
    "In18.Cu": 38,
    "User.1": 39,
    "In19.Cu": 40,
    "User.2": 41,
    "In20.Cu": 42,
    "User.3": 43,
    "In21.Cu": 44,
    "User.4": 45,
    "In22.Cu": 46,
    "User.5": 47,
    "In23.Cu": 48,
    "User.6": 49,
    "In24.Cu": 50,
    "User.7": 51,
    "In25.Cu": 52,
    "User.8": 53,
    "In26.Cu": 54,
    "User.9": 55,
    "In27.Cu": 56,
    "In28.Cu": 58,
    "In29.Cu": 60,
    "In30.Cu": 62,
}
_KICAD_FALLBACK_USER_LAYERS = (
    "User.1",
    "User.2",
    "User.3",
    "User.4",
    "User.5",
    "User.6",
    "User.7",
    "User.8",
    "User.9",
    "Dwgs.User",
    "Cmts.User",
    "Eco1.User",
    "Eco2.User",
    "Margin",
    "B.Adhes",
    "F.Adhes",
)
_DEFAULT_KICAD_VERSION = 20241229
_DEFAULT_KICAD_GENERATOR = "faebryk"
_DEFAULT_KICAD_GENERATOR_VERSION = "0"
_ALTIUM_TEXT_TYPE_STROKE = 0
_ALTIUM_TEXT_TYPE_TRUETYPE = 1
_ALTIUM_TEXT_POSITION_LEFT_BOTTOM = 3
_ALTIUM_SPECIAL_LAYER_NAMES = {
    "AssemblyTop": "F.Fab",
    "AssemblyBottom": "B.Fab",
    "CourtyardTop": "F.CrtYd",
    "CourtyardBottom": "B.CrtYd",
    "GluePointsTop": "F.Adhes",
    "GluePointsBottom": "B.Adhes",
    "AssemblyNotes": "Cmts.User",
    "FabNotes": "Cmts.User",
    "Dimensions": "Dwgs.User",
    "DimensionsTop": "F.Fab",
    "DimensionsBottom": "B.Fab",
    "ValueTop": "F.Fab",
    "ValueBottom": "B.Fab",
    "DesignatorTop": "F.Fab",
    "DesignatorBottom": "B.Fab",
    "ComponentOutlineTop": "F.Fab",
    "ComponentOutlineBottom": "B.Fab",
    "ComponentCenterTop": "F.Fab",
    "ComponentCenterBottom": "B.Fab",
    "Board": "Edge.Cuts",
    "BoardShape": "Edge.Cuts",
    "VCut": "Edge.Cuts",
}


@dataclass
class ToAltiumContext:
    nets: list[AltiumNet] = field(default_factory=list)
    components: list[AltiumComponent] = field(default_factory=list)
    primitives: list[
        AltiumPad
        | AltiumTrack
        | AltiumVia
        | AltiumArc
        | AltiumText
        | AltiumFill
        | AltiumRegion
    ] = field(default_factory=list)
    rules: list[AltiumRule] = field(default_factory=list)
    board_outline_segments: list[AltiumBoardOutlineSegment] = field(
        default_factory=list
    )
    used_layers: set[int] = field(default_factory=lambda: {1})
    net_id_by_kicad_number: dict[int, str] = field(default_factory=dict)
    net_index_by_kicad_number: dict[int, int] = field(default_factory=dict)
    component_id_by_index: list[str] = field(default_factory=list)
    board_thickness: int = 0
    layer_count: int = 2
    translation_warnings: list[str] = field(default_factory=list)

    def add_layer(self, layer: int | None) -> None:
        if layer is not None and layer > 0:
            self.used_layers.add(layer)


@dataclass
class ToKicadContext:
    pcb: object = field(
        default_factory=lambda: kicad.pcb.KicadPcb(
            version=_DEFAULT_KICAD_VERSION,
            generator=_DEFAULT_KICAD_GENERATOR,
            generator_version=_DEFAULT_KICAD_GENERATOR_VERSION,
        )
    )
    net_number_by_id: dict[str | None, int] = field(default_factory=dict)
    net_name_by_id: dict[str | None, str] = field(default_factory=dict)
    net_obj_by_id: dict[str | None, object] = field(default_factory=dict)
    footprint_by_component_id: dict[str, object] = field(default_factory=dict)
    component_by_id: dict[str, AltiumComponent] = field(default_factory=dict)
    components_with_explicit_text: set[str] = field(default_factory=set)
    reference_text_by_component_id: dict[str, AltiumText] = field(default_factory=dict)
    value_text_by_component_id: dict[str, AltiumText] = field(default_factory=dict)
    consumed_component_text_ids: set[str] = field(default_factory=set)
    layer_name_by_number: dict[int, str] = field(default_factory=dict)
    translation_warnings: list[str] = field(default_factory=list)
    synthetic_component_index: int = 0
    zone_clearance_mm: float | None = None
    zone_min_thickness_mm: float | None = None
    zone_connect_mode: object | None = None
    zone_thermal_gap_mm: float | None = None
    zone_thermal_bridge_width_mm: float | None = None


def convert_kicad_to_altium(kicad_pcb) -> AltiumPcb:
    """Convert a KiCad PCB object directly into `AltiumPcb`."""
    ctx = ToAltiumContext()

    for kicad_net in kicad_pcb.nets:
        NetCodec.to_altium(kicad_net, ctx)

    footprints = list(kicad_pcb.footprints)
    for index, footprint in enumerate(footprints):
        ComponentCodec.to_altium(footprint, index, ctx)

    for index, footprint in enumerate(footprints):
        component_id = ctx.component_id_by_index[index]
        for pad in footprint.pads:
            PadCodec.to_altium(pad, footprint, component_id, index, ctx)

    for segment in kicad_pcb.segments:
        TrackCodec.to_altium_segment(segment, ctx)
    for via in kicad_pcb.vias:
        ViaCodec.to_altium(via, ctx)

    for index, footprint in enumerate(footprints):
        component_id = ctx.component_id_by_index[index]
        for line in footprint.fp_lines:
            TrackCodec.to_altium_graphic_line(line, ctx, component_id, footprint)
        for arc in footprint.fp_arcs:
            ArcCodec.to_altium_graphic_arc(arc, ctx, component_id, footprint)
        for circle in footprint.fp_circles:
            ArcCodec.to_altium_circle(circle, ctx, component_id, footprint)
        for rect in footprint.fp_rects:
            if not FillCodec.to_altium_rect(rect, ctx, component_id, footprint):
                TrackCodec.to_altium_rect_outline(rect, ctx, component_id, footprint)
        for fp_text in footprint.fp_texts:
            TextCodec.to_altium_fp_text(fp_text, ctx, component_id, footprint, index)
        for prop in footprint.propertys:
            TextCodec.to_altium_property(prop, ctx, component_id, footprint)
        for poly in footprint.fp_poly:
            RegionCodec.to_altium_fp_poly(poly, ctx, component_id, footprint)

    ctx.board_outline_segments = BoardCodec.extract_outline(kicad_pcb)
    BoardCodec.to_altium_graphics(kicad_pcb, ctx)
    RegionCodec.to_altium_zones(kicad_pcb, ctx)
    RuleCodec.to_altium(kicad_pcb, ctx)

    copper_layer_numbers = _copper_layer_numbers(kicad_pcb)
    if copper_layer_numbers:
        ctx.layer_count = max(len(copper_layer_numbers), 2)
        ctx.used_layers.update(copper_layer_numbers)
    else:
        ctx.used_layers.update({1, 32})

    if kicad_pcb.general:
        ctx.board_thickness = mm_to_altium(kicad_pcb.general.thickness)

    board_layers = [_make_board_layer(layer) for layer in sorted(ctx.used_layers)]
    ordered_copper_layer_ids = [
        f"layer-{layer}" for layer in copper_layer_numbers or [1, 32]
    ]
    doc = AltiumPcb(
        board=BoardConfig(
            name=None,
            board_thickness=ctx.board_thickness,
            layers=board_layers,
            copper_ordering=BoardCopperOrdering(
                ordered_layer_ids=ordered_copper_layer_ids
            ),
            outline=ctx.board_outline_segments,
            extra_properties={"ll_layer_count": ctx.layer_count},
        ),
        nets=ctx.nets,
        components=ctx.components,
        classes=[],
        rules=ctx.rules,
        primitives=ctx.primitives,
    )
    if ctx.translation_warnings:
        logger.debug("KiCad->Altium IL warnings: %s", ctx.translation_warnings)
    return doc


def convert_altium_to_kicad(doc: AltiumPcb):
    """Convert an `AltiumPcb` IL document into a KiCad PCB object."""
    ctx = ToKicadContext()
    ctx.pcb.nets.append(kicad.pcb.Net(number=0, name=""))
    ctx.net_obj_by_id[None] = ctx.pcb.nets[-1]

    _prepare_component_text_roles(doc, ctx)
    _prepare_zone_defaults(doc, ctx)
    BoardCodec.to_kicad(doc, ctx)
    for index, net in enumerate(doc.nets):
        NetCodec.to_kicad(net, index, ctx)
    for component in doc.components:
        ComponentCodec.to_kicad(component, ctx)

    for primitive in doc.primitives:
        if isinstance(primitive, AltiumPad):
            PadCodec.to_kicad(primitive, ctx)
        elif isinstance(primitive, AltiumTrack):
            TrackCodec.to_kicad(primitive, ctx)
        elif isinstance(primitive, AltiumVia):
            ViaCodec.to_kicad(primitive, ctx)
        elif isinstance(primitive, AltiumArc):
            ArcCodec.to_kicad(primitive, ctx)
        elif isinstance(primitive, AltiumText):
            TextCodec.to_kicad(primitive, ctx)
        elif isinstance(primitive, AltiumFill):
            FillCodec.to_kicad(primitive, ctx)
        elif isinstance(primitive, AltiumRegion):
            RegionCodec.to_kicad(primitive, ctx)

    RuleCodec.to_kicad(doc.rules, ctx)
    if ctx.translation_warnings:
        logger.debug("Altium IL->KiCad warnings: %s", ctx.translation_warnings)
    return ctx.pcb


def convert_pcb(kicad_pcb) -> AltiumPcb:
    """Backward-compatible KiCad->Altium conversion alias."""
    return convert_kicad_to_altium(kicad_pcb)


class NetCodec:
    @staticmethod
    def to_altium(kicad_net, ctx: ToAltiumContext) -> None:
        if kicad_net.number == 0:
            return
        net_index = len(ctx.nets)
        net_id = f"net-{net_index + 1}"
        ctx.net_id_by_kicad_number[kicad_net.number] = net_id
        ctx.net_index_by_kicad_number[kicad_net.number] = net_index
        ctx.nets.append(
            AltiumNet(
                id=net_id,
                name=kicad_net.name or f"Net{kicad_net.number}",
            )
        )

    @staticmethod
    def to_kicad(net: AltiumNet, index: int, ctx: ToKicadContext) -> None:
        number = index + 1
        ctx.net_number_by_id[net.id] = number
        ctx.net_name_by_id[net.id] = net.name
        kicad_net = kicad.pcb.Net(number=number, name=net.name)
        ctx.pcb.nets.append(kicad_net)
        ctx.net_obj_by_id[net.id] = ctx.pcb.nets[-1]


class ComponentCodec:
    @staticmethod
    def to_altium(footprint, index: int, ctx: ToAltiumContext) -> None:
        component_id = f"component-{index + 1}"
        ctx.component_id_by_index.append(component_id)

        designator = _try_get_property(footprint.propertys, "Reference") or (
            f"U{index + 1}"
        )
        layer = _get_altium_layer(footprint.layer) or 1
        ctx.add_layer(layer)

        name_on = True
        for prop in footprint.propertys:
            if prop.name == "Reference":
                name_on = not (prop.hide or (prop.effects and prop.effects.hide))
                break

        comment_on = False
        for prop in footprint.propertys:
            if prop.name == "Value":
                comment_on = not (prop.hide or (prop.effects and prop.effects.hide))
                break

        ctx.components.append(
            AltiumComponent(
                id=component_id,
                designator=designator,
                footprint=footprint.name,
                x=mm_to_altium(footprint.at.x),
                y=_coord_y(footprint.at.y),
                rotation=footprint.at.r if footprint.at.r is not None else 0.0,
                layer=layer,
                side="bottom" if layer == 32 else "top",
                name_on=name_on,
                comment_on=comment_on,
            )
        )

    @staticmethod
    def to_kicad(component: AltiumComponent, ctx: ToKicadContext) -> None:
        layer_name = _get_kicad_layer_name(component.layer, ctx, fallback="F.Cu")
        footprint = kicad.pcb.Footprint(
            name=component.footprint,
            layer=layer_name,
            at=_xyr(
                altium_to_mm(component.x),
                _coord_y_mm(component.y),
                component.rotation,
            ),
        )
        reference_text = (
            ctx.reference_text_by_component_id.get(component.id)
            if component.id is not None
            else None
        )
        value_text = (
            ctx.value_text_by_component_id.get(component.id)
            if component.id is not None
            else None
        )
        footprint.propertys.append(
            _make_component_property(
                "Reference",
                component.designator,
                component=component,
                text=reference_text,
                default_y_mm=-2.0,
                default_layer="B.SilkS" if component.layer == 32 else "F.SilkS",
                hidden=not component.name_on,
                ctx=ctx,
            )
        )
        footprint.propertys.append(
            _make_component_property(
                "Value",
                (
                    value_text.text
                    if value_text is not None
                    else (
                        component.designator
                        if component.designator.isalpha()
                        else (component.footprint if component.comment_on else "")
                    )
                ),
                component=component,
                text=value_text,
                default_y_mm=2.0,
                default_layer="B.SilkS" if component.layer == 32 else "F.SilkS",
                hidden=not component.comment_on,
                ctx=ctx,
            )
        )
        ctx.pcb.footprints.append(footprint)
        stored_footprint = ctx.pcb.footprints[-1]
        if component.id is not None:
            ctx.component_by_id[component.id] = component
            ctx.footprint_by_component_id[component.id] = stored_footprint


class PadCodec:
    @staticmethod
    def to_altium(
        pad,
        footprint,
        component_id: str,
        comp_idx: int,
        ctx: ToAltiumContext,
    ) -> None:
        fp_x = footprint.at.x
        fp_y = footprint.at.y
        fp_angle_deg = footprint.at.r if footprint.at.r is not None else 0.0
        fp_angle_rad = math.radians(fp_angle_deg)
        cos_a = math.cos(fp_angle_rad)
        sin_a = math.sin(fp_angle_rad)

        global_x, global_y = _fp_local_to_global(
            pad.at.x,
            pad.at.y,
            fp_x,
            fp_y,
            cos_a,
            sin_a,
        )
        size_x = mm_to_altium(pad.size.w)
        size_y = mm_to_altium(pad.size.h if pad.size.h is not None else pad.size.w)

        hole_size = 0
        slot_size = 0
        slot_rotation = 0.0
        is_tht = pad.type in ("thru_hole", "np_thru_hole")
        plated = pad.type != "np_thru_hole"
        if pad.drill and pad.drill.size_x:
            drill_x = mm_to_altium(pad.drill.size_x)
            drill_y = mm_to_altium(
                pad.drill.size_y if pad.drill.size_y else pad.drill.size_x
            )
            if drill_x != drill_y:
                hole_size = min(drill_x, drill_y)
                slot_size = max(drill_x, drill_y)
                slot_rotation = 0.0 if drill_x > drill_y else 90.0
            else:
                hole_size = drill_x

        net_id = None
        if pad.net is not None:
            net_id = ctx.net_id_by_kicad_number.get(pad.net.number)

        if is_tht:
            layer = 1
        else:
            layer = 1
            for layer_name in pad.layers:
                mapped = _get_altium_layer(layer_name)
                if mapped is not None and mapped <= 32:
                    layer = mapped
                    break
        ctx.add_layer(layer)

        ctx.primitives.append(
            AltiumPad(
                id=f"pad-{comp_idx}-{pad.name}-{len(ctx.primitives)}",
                component_id=component_id,
                name=pad.name,
                x=mm_to_altium(global_x),
                y=_coord_y(global_y),
                top_size_x=size_x,
                top_size_y=size_y,
                mid_size_x=size_x,
                mid_size_y=size_y,
                bot_size_x=size_x,
                bot_size_y=size_y,
                hole_size=hole_size,
                shape=_pad_shape_from_kicad(pad.shape),
                rotation=((pad.at.r if pad.at.r is not None else 0.0) + fp_angle_deg)
                % 360,
                net_id=net_id,
                layer=layer,
                is_tht=is_tht,
                plated=plated,
                slot_size=slot_size,
                slot_rotation=slot_rotation,
            )
        )

    @staticmethod
    def to_kicad(pad: AltiumPad, ctx: ToKicadContext) -> None:
        footprint = _ensure_pad_footprint(pad, ctx)
        component = (
            ctx.component_by_id.get(pad.component_id)
            if pad.component_id is not None
            else None
        )
        layer_name = _get_kicad_layer_name(pad.layer, ctx, fallback="F.Cu")

        if component is not None:
            local_x_mm, local_y_mm = _global_to_fp_local_mm(
                altium_to_mm(pad.x),
                _coord_y_mm(pad.y),
                component,
            )
            rotation = pad.rotation
        else:
            local_x_mm = altium_to_mm(pad.x)
            local_y_mm = _coord_y_mm(pad.y)
            rotation = pad.rotation

        drill = None
        if pad.hole_size > 0:
            drill_x = pad.hole_size
            drill_y = pad.hole_size
            if pad.slot_size > 0:
                if int(round(pad.slot_rotation)) % 180 == 0:
                    drill_x = pad.slot_size
                else:
                    drill_y = pad.slot_size
            drill_kwargs = {
                "size_x": altium_to_mm(drill_x),
                "size_y": altium_to_mm(drill_y) if drill_x != drill_y else None,
            }
            if drill_x != drill_y:
                drill_kwargs["shape"] = "oval"
            drill = kicad.pcb.PadDrill(**drill_kwargs)

        pad_type = "smd"
        if pad.is_tht:
            pad_type = "thru_hole" if pad.plated else "np_thru_hole"

        if pad.is_tht:
            pad_layers = ["*.Cu", "*.Mask"]
        elif layer_name == "B.Cu":
            pad_layers = ["B.Cu", "B.Paste", "B.Mask"]
            _ensure_kicad_layer_present("B.Paste", ctx)
            _ensure_kicad_layer_present("B.Mask", ctx)
        else:
            pad_layers = ["F.Cu", "F.Paste", "F.Mask"]
            _ensure_kicad_layer_present("F.Paste", ctx)
            _ensure_kicad_layer_present("F.Mask", ctx)

        kicad_pad = kicad.pcb.Pad(
            name=pad.name,
            type=pad_type,
            shape=_pad_shape_to_kicad(pad),
            at=_xyr(local_x_mm, local_y_mm, rotation),
            size=kicad.pcb.Wh(
                w=altium_to_mm(pad.top_size_x),
                h=altium_to_mm(pad.top_size_y),
            ),
            layers=pad_layers,
            drill=drill,
            net=ctx.net_obj_by_id.get(pad.net_id),
        )
        footprint.pads.append(kicad_pad)


class TrackCodec:
    @staticmethod
    def to_altium_segment(segment, ctx: ToAltiumContext) -> None:
        layer = _get_altium_layer(segment.layer)
        if layer is None:
            return
        ctx.add_layer(layer)
        net_index = ctx.net_index_by_kicad_number.get(segment.net, 0)
        ctx.primitives.append(
            AltiumTrack(
                id=f"track-{net_index}-{len(ctx.primitives)}",
                layer=layer,
                net_id=ctx.net_id_by_kicad_number.get(segment.net),
                x1=mm_to_altium(segment.start.x),
                y1=_coord_y(segment.start.y),
                x2=mm_to_altium(segment.end.x),
                y2=_coord_y(segment.end.y),
                width=mm_to_altium(segment.width),
            )
        )

    @staticmethod
    def to_altium_graphic_line(
        line,
        ctx: ToAltiumContext,
        component_id: str | None = None,
        footprint=None,
    ) -> None:
        layer = _get_geo_layer(line)
        if layer is None:
            return
        ctx.add_layer(layer)
        if footprint is None:
            x1 = mm_to_altium(line.start.x)
            y1 = _coord_y(line.start.y)
            x2 = mm_to_altium(line.end.x)
            y2 = _coord_y(line.end.y)
        else:
            fp_angle_deg = footprint.at.r if footprint.at.r is not None else 0.0
            fp_angle_rad = math.radians(fp_angle_deg)
            cos_a = math.cos(fp_angle_rad)
            sin_a = math.sin(fp_angle_rad)
            gx1, gy1 = _fp_local_to_global(
                line.start.x,
                line.start.y,
                footprint.at.x,
                footprint.at.y,
                cos_a,
                sin_a,
            )
            gx2, gy2 = _fp_local_to_global(
                line.end.x,
                line.end.y,
                footprint.at.x,
                footprint.at.y,
                cos_a,
                sin_a,
            )
            x1 = mm_to_altium(gx1)
            y1 = _coord_y(gy1)
            x2 = mm_to_altium(gx2)
            y2 = _coord_y(gy2)
        ctx.primitives.append(
            AltiumTrack(
                id=f"track-0-{len(ctx.primitives)}",
                layer=layer,
                component_id=component_id,
                x1=x1,
                y1=y1,
                x2=x2,
                y2=y2,
                width=_stroke_width(line),
            )
        )

    @staticmethod
    def to_altium_rect_outline(
        rect,
        ctx: ToAltiumContext,
        component_id: str | None = None,
        footprint=None,
    ) -> None:
        corners = [
            (rect.start.x, rect.start.y),
            (rect.end.x, rect.start.y),
            (rect.end.x, rect.end.y),
            (rect.start.x, rect.end.y),
        ]
        if footprint is None:
            transformed = corners
        else:
            fp_angle_deg = footprint.at.r if footprint.at.r is not None else 0.0
            fp_angle_rad = math.radians(fp_angle_deg)
            cos_a = math.cos(fp_angle_rad)
            sin_a = math.sin(fp_angle_rad)
            transformed = [
                _fp_local_to_global(
                    x,
                    y,
                    footprint.at.x,
                    footprint.at.y,
                    cos_a,
                    sin_a,
                )
                for x, y in corners
            ]
        for index in range(4):
            start_x, start_y = transformed[index]
            end_x, end_y = transformed[(index + 1) % 4]
            layer = _get_geo_layer(rect)
            if layer is None:
                continue
            ctx.add_layer(layer)
            ctx.primitives.append(
                AltiumTrack(
                    id=f"track-0-{len(ctx.primitives)}",
                    layer=layer,
                    component_id=component_id,
                    x1=mm_to_altium(start_x),
                    y1=_coord_y(start_y),
                    x2=mm_to_altium(end_x),
                    y2=_coord_y(end_y),
                    width=_stroke_width(rect),
                )
            )

    @staticmethod
    def to_kicad(track: AltiumTrack, ctx: ToKicadContext) -> None:
        layer_name = _get_kicad_layer_name(track.layer, ctx, fallback="F.Cu")
        if track.net_id is not None and layer_name.endswith(".Cu"):
            ctx.pcb.segments.append(
                kicad.pcb.Segment(
                    start=_xy(altium_to_mm(track.x1), _coord_y_mm(track.y1)),
                    end=_xy(altium_to_mm(track.x2), _coord_y_mm(track.y2)),
                    width=altium_to_mm(track.width),
                    layer=layer_name,
                    net=ctx.net_number_by_id.get(track.net_id, 0),
                )
            )
            return

        if track.component_id is not None and track.component_id in ctx.component_by_id:
            component = ctx.component_by_id[track.component_id]
            footprint = ctx.footprint_by_component_id[track.component_id]
            start_x, start_y = _global_to_fp_local_mm(
                altium_to_mm(track.x1),
                _coord_y_mm(track.y1),
                component,
            )
            end_x, end_y = _global_to_fp_local_mm(
                altium_to_mm(track.x2),
                _coord_y_mm(track.y2),
                component,
            )
            footprint.fp_lines.append(
                kicad.pcb.Line(
                    start=_xy(start_x, start_y),
                    end=_xy(end_x, end_y),
                    layer=layer_name,
                    stroke=_stroke(altium_to_mm(track.width)),
                )
            )
            return

        ctx.pcb.gr_lines.append(
            kicad.pcb.Line(
                start=_xy(altium_to_mm(track.x1), _coord_y_mm(track.y1)),
                end=_xy(altium_to_mm(track.x2), _coord_y_mm(track.y2)),
                layer=layer_name,
                stroke=_stroke(altium_to_mm(track.width)),
            )
        )


class ViaCodec:
    @staticmethod
    def to_altium(via, ctx: ToAltiumContext) -> None:
        start_layer = 1
        end_layer = 32
        if via.layers and len(via.layers) >= 2:
            mapped_start = _get_altium_layer(via.layers[0])
            mapped_end = _get_altium_layer(via.layers[1])
            if mapped_start is not None:
                start_layer = mapped_start
            if mapped_end is not None:
                end_layer = mapped_end
        ctx.add_layer(start_layer)
        ctx.add_layer(end_layer)

        x = mm_to_altium(via.at.x)
        y = _coord_y(via.at.y)
        ctx.primitives.append(
            AltiumVia(
                id=f"via-{x}-{y}",
                x=x,
                y=y,
                diameter=mm_to_altium(via.size),
                hole_size=mm_to_altium(via.drill),
                start_layer=start_layer,
                end_layer=end_layer,
                net_id=ctx.net_id_by_kicad_number.get(via.net),
            )
        )

    @staticmethod
    def to_kicad(via: AltiumVia, ctx: ToKicadContext) -> None:
        layers = [
            _get_kicad_layer_name(via.start_layer, ctx, fallback="F.Cu"),
            _get_kicad_layer_name(via.end_layer, ctx, fallback="B.Cu"),
        ]
        ctx.pcb.vias.append(
            kicad.pcb.Via(
                at=_xy(altium_to_mm(via.x), _coord_y_mm(via.y)),
                size=altium_to_mm(via.diameter),
                drill=altium_to_mm(via.hole_size),
                layers=layers,
                net=ctx.net_number_by_id.get(via.net_id, 0),
            )
        )


class ArcCodec:
    @staticmethod
    def to_altium_graphic_arc(
        arc,
        ctx: ToAltiumContext,
        component_id: str | None = None,
        footprint=None,
    ) -> None:
        layer = _get_geo_layer(arc)
        if layer is None:
            return
        ctx.add_layer(layer)
        if footprint is None:
            start = arc.start
            mid = arc.mid
            end = arc.end
        else:
            fp_angle_deg = footprint.at.r if footprint.at.r is not None else 0.0
            fp_angle_rad = math.radians(fp_angle_deg)
            cos_a = math.cos(fp_angle_rad)
            sin_a = math.sin(fp_angle_rad)
            start = _xy(
                *_fp_local_to_global(
                    arc.start.x,
                    arc.start.y,
                    footprint.at.x,
                    footprint.at.y,
                    cos_a,
                    sin_a,
                )
            )
            mid = _xy(
                *_fp_local_to_global(
                    arc.mid.x,
                    arc.mid.y,
                    footprint.at.x,
                    footprint.at.y,
                    cos_a,
                    sin_a,
                )
            )
            end = _xy(
                *_fp_local_to_global(
                    arc.end.x,
                    arc.end.y,
                    footprint.at.x,
                    footprint.at.y,
                    cos_a,
                    sin_a,
                )
            )
        cx, cy, radius, start_angle, end_angle = _arc_from_three_points(
            start.x,
            start.y,
            mid.x,
            mid.y,
            end.x,
            end.y,
        )
        ctx.primitives.append(
            AltiumArc(
                id=f"arc-{len(ctx.primitives)}",
                layer=layer,
                component_id=component_id,
                center_x=mm_to_altium(cx),
                center_y=_coord_y(cy),
                radius=mm_to_altium(radius),
                start_angle=start_angle,
                end_angle=end_angle,
                width=_stroke_width(arc),
            )
        )

    @staticmethod
    def to_altium_circle(
        circle,
        ctx: ToAltiumContext,
        component_id: str | None = None,
        footprint=None,
    ) -> None:
        layer = _get_geo_layer(circle)
        if layer is None:
            return
        ctx.add_layer(layer)
        if footprint is None:
            center_x = circle.center.x
            center_y = circle.center.y
        else:
            fp_angle_deg = footprint.at.r if footprint.at.r is not None else 0.0
            fp_angle_rad = math.radians(fp_angle_deg)
            cos_a = math.cos(fp_angle_rad)
            sin_a = math.sin(fp_angle_rad)
            center_x, center_y = _fp_local_to_global(
                circle.center.x,
                circle.center.y,
                footprint.at.x,
                footprint.at.y,
                cos_a,
                sin_a,
            )
        radius = math.sqrt(
            (circle.end.x - circle.center.x) ** 2
            + (circle.end.y - circle.center.y) ** 2
        )
        ctx.primitives.append(
            AltiumArc(
                id=f"arc-{len(ctx.primitives)}",
                layer=layer,
                component_id=component_id,
                center_x=mm_to_altium(center_x),
                center_y=_coord_y(center_y),
                radius=mm_to_altium(radius),
                start_angle=0.0,
                end_angle=360.0,
                width=_stroke_width(circle),
            )
        )

    @staticmethod
    def to_kicad(arc: AltiumArc, ctx: ToKicadContext) -> None:
        layer_name = _get_kicad_layer_name(arc.layer, ctx, fallback="F.SilkS")
        start_point, mid_point, end_point = _arc_points_mm(arc)

        if arc.net_id is not None and layer_name.endswith(".Cu"):
            ctx.pcb.arcs.append(
                kicad.pcb.ArcSegment(
                    start=_xy(*start_point),
                    mid=_xy(*mid_point),
                    end=_xy(*end_point),
                    width=altium_to_mm(arc.width),
                    layer=layer_name,
                    net=ctx.net_number_by_id.get(arc.net_id, 0),
                )
            )
            return

        if arc.component_id is not None and arc.component_id in ctx.component_by_id:
            component = ctx.component_by_id[arc.component_id]
            footprint = ctx.footprint_by_component_id[arc.component_id]
            start_local = _global_to_fp_local_mm(*start_point, component)
            mid_local = _global_to_fp_local_mm(*mid_point, component)
            end_local = _global_to_fp_local_mm(*end_point, component)
            footprint.fp_arcs.append(
                kicad.pcb.Arc(
                    start=_xy(*start_local),
                    mid=_xy(*mid_local),
                    end=_xy(*end_local),
                    layer=layer_name,
                    stroke=_stroke(altium_to_mm(arc.width)),
                )
            )
            return

        ctx.pcb.gr_arcs.append(
            kicad.pcb.Arc(
                start=_xy(*start_point),
                mid=_xy(*mid_point),
                end=_xy(*end_point),
                layer=layer_name,
                stroke=_stroke(altium_to_mm(arc.width)),
            )
        )


class TextCodec:
    @staticmethod
    def to_altium_fp_text(
        fp_text,
        ctx: ToAltiumContext,
        component_id: str,
        footprint,
        comp_index: int,
    ) -> None:
        if fp_text.hide:
            return
        if fp_text.effects and fp_text.effects.hide:
            return
        layer = _get_text_layer(fp_text)
        if layer is None:
            return
        ctx.add_layer(layer)
        fp_angle_deg = footprint.at.r if footprint.at.r is not None else 0.0
        fp_angle_rad = math.radians(fp_angle_deg)
        cos_a = math.cos(fp_angle_rad)
        sin_a = math.sin(fp_angle_rad)
        global_x, global_y = _fp_local_to_global(
            fp_text.at.x,
            fp_text.at.y,
            footprint.at.x,
            footprint.at.y,
            cos_a,
            sin_a,
        )
        font = fp_text.effects.font if fp_text.effects else None
        height = mm_to_altium(0.8)
        stroke_w = mm_to_altium(0.1)
        if font:
            if font.size and font.size.h is not None:
                height = mm_to_altium(font.size.h)
            elif font.size:
                height = mm_to_altium(font.size.w)
            if font.thickness is not None:
                stroke_w = mm_to_altium(font.thickness)

        text_content = fp_text.text
        if text_content in ("${REFERENCE}", "%R"):
            text_content = (
                _try_get_property(footprint.propertys, "Reference")
                or f"U{comp_index + 1}"
            )
        elif text_content in ("${VALUE}", "%V"):
            text_content = (
                _try_get_property(footprint.propertys, "Value") or footprint.name
            )

        font_face = _font_face(font)
        font_type = (
            _ALTIUM_TEXT_TYPE_TRUETYPE if font_face else _ALTIUM_TEXT_TYPE_STROKE
        )
        is_designator = str(fp_text.type) == "reference"
        is_comment = str(fp_text.type) == "value"

        ctx.primitives.append(
            AltiumText(
                id=f"text-{len(ctx.primitives)}",
                layer=layer,
                component_id=component_id,
                x=mm_to_altium(global_x),
                y=_coord_y(global_y),
                height=height,
                rotation=(fp_text.at.r or 0.0) % 360,
                is_mirrored=footprint.layer == "B.Cu",
                stroke_width=stroke_w,
                text=text_content,
                is_designator=is_designator,
                is_comment=is_comment,
                font_type=font_type,
                is_bold=_font_flag(font, "bold") if font else False,
                is_italic=_font_flag(font, "italic") if font else False,
                font_name=font_face or "",
                text_justification=_justify_to_altium_position(
                    (
                        getattr(fp_text.effects, "justify", None)
                        if fp_text.effects
                        else None
                    )
                ),
                is_justification_valid=bool(
                    fp_text.effects
                    and getattr(fp_text.effects, "justify", None) is not None
                ),
            )
        )

    @staticmethod
    def to_altium_property(
        prop,
        ctx: ToAltiumContext,
        component_id: str,
        footprint,
    ) -> None:
        if prop.hide:
            return
        if prop.effects and prop.effects.hide:
            return
        layer = _get_altium_layer(prop.layer)
        if layer is None:
            return
        ctx.add_layer(layer)
        fp_angle_deg = footprint.at.r if footprint.at.r is not None else 0.0
        fp_angle_rad = math.radians(fp_angle_deg)
        cos_a = math.cos(fp_angle_rad)
        sin_a = math.sin(fp_angle_rad)
        global_x, global_y = _fp_local_to_global(
            prop.at.x,
            prop.at.y,
            footprint.at.x,
            footprint.at.y,
            cos_a,
            sin_a,
        )
        font = prop.effects.font if prop.effects else None
        height = mm_to_altium(0.8)
        stroke_w = mm_to_altium(0.1)
        if font:
            if font.size and font.size.h is not None:
                height = mm_to_altium(font.size.h)
            elif font.size:
                height = mm_to_altium(font.size.w)
            if font.thickness is not None:
                stroke_w = mm_to_altium(font.thickness)

        font_face = _font_face(font)
        font_type = (
            _ALTIUM_TEXT_TYPE_TRUETYPE if font_face else _ALTIUM_TEXT_TYPE_STROKE
        )

        ctx.primitives.append(
            AltiumText(
                id=f"text-{len(ctx.primitives)}",
                layer=layer,
                component_id=component_id,
                x=mm_to_altium(global_x),
                y=_coord_y(global_y),
                height=height,
                rotation=(prop.at.r or 0.0) % 360,
                is_mirrored=footprint.layer == "B.Cu",
                stroke_width=stroke_w,
                text=prop.value,
                is_designator=prop.name == "Reference",
                is_comment=prop.name == "Value",
                font_type=font_type,
                is_bold=_font_flag(font, "bold") if font else False,
                is_italic=_font_flag(font, "italic") if font else False,
                font_name=font_face or "",
                text_justification=_justify_to_altium_position(
                    getattr(prop.effects, "justify", None) if prop.effects else None
                ),
                is_justification_valid=bool(
                    prop.effects and getattr(prop.effects, "justify", None) is not None
                ),
            )
        )

    @staticmethod
    def to_altium_gr_text(text, ctx: ToAltiumContext) -> None:
        if text.effects and text.effects.hide:
            return
        layer = _get_text_layer(text)
        if layer is None:
            return
        ctx.add_layer(layer)
        font = text.effects.font if text.effects else None
        height = mm_to_altium(0.8)
        stroke_w = mm_to_altium(0.1)
        if font:
            if font.size and font.size.h is not None:
                height = mm_to_altium(font.size.h)
            elif font.size:
                height = mm_to_altium(font.size.w)
            if font.thickness is not None:
                stroke_w = mm_to_altium(font.thickness)
        font_face = _font_face(font)
        font_type = (
            _ALTIUM_TEXT_TYPE_TRUETYPE if font_face else _ALTIUM_TEXT_TYPE_STROKE
        )
        ctx.primitives.append(
            AltiumText(
                id=f"text-{len(ctx.primitives)}",
                layer=layer,
                x=mm_to_altium(text.at.x),
                y=_coord_y(text.at.y),
                height=height,
                rotation=(text.at.r or 0.0) % 360,
                stroke_width=stroke_w,
                text=text.text,
                font_type=font_type,
                is_bold=_font_flag(font, "bold") if font else False,
                is_italic=_font_flag(font, "italic") if font else False,
                font_name=font_face or "",
                text_justification=_justify_to_altium_position(
                    getattr(text.effects, "justify", None) if text.effects else None
                ),
                is_justification_valid=bool(
                    text.effects and getattr(text.effects, "justify", None) is not None
                ),
            )
        )

    @staticmethod
    def to_kicad(text: AltiumText, ctx: ToKicadContext) -> None:
        if text.id is not None and text.id in ctx.consumed_component_text_ids:
            return
        if not text.text.strip() and _component_text_placeholder(text.text) is None:
            return
        layer_name = _get_kicad_layer_name(text.layer, ctx, fallback="F.SilkS")
        effects = _altium_text_effects(text)
        text_layer = _altium_text_layer(text, layer_name)
        aligned_x_mm, aligned_y_mm = _aligned_text_position_mm(text)
        if text.component_id is not None and text.component_id in ctx.component_by_id:
            component = ctx.component_by_id[text.component_id]
            footprint = ctx.footprint_by_component_id[text.component_id]
            local_x_mm, local_y_mm = _global_to_fp_local_mm(
                aligned_x_mm,
                aligned_y_mm,
                component,
            )
            text_content = _altium_special_string_to_kicad(
                text.text,
                {
                    "DESIGNATOR": "REFERENCE",
                    "COMMENT": "VALUE",
                    "VALUE": "ALTIUM_VALUE",
                    "LAYER_NAME": "LAYER",
                    "PRINT_DATE": "CURRENT_DATE",
                },
            )
            footprint.fp_texts.append(
                kicad.pcb.FpText(
                    type="user",
                    text=text_content,
                    at=_xyr(local_x_mm, local_y_mm, text.rotation),
                    layer=text_layer,
                    effects=effects,
                )
            )
            return

        ctx.pcb.gr_texts.append(
            kicad.pcb.Text(
                text=_altium_special_string_to_kicad(
                    text.text,
                    {
                        "LAYER_NAME": "LAYER",
                        "PRINT_DATE": "CURRENT_DATE",
                    },
                ),
                at=_xyr(aligned_x_mm, aligned_y_mm, text.rotation),
                layer=text_layer,
                effects=effects,
            )
        )


class FillCodec:
    @staticmethod
    def to_altium_rect(
        rect,
        ctx: ToAltiumContext,
        component_id: str | None = None,
        footprint=None,
    ) -> bool:
        if not _fill_is_solid(getattr(rect, "fill", None)):
            return False
        layer = _get_geo_layer(rect)
        if layer is None:
            return False
        ctx.add_layer(layer)
        if footprint is None:
            x1 = mm_to_altium(rect.start.x)
            y1 = _coord_y(rect.start.y)
            x2 = mm_to_altium(rect.end.x)
            y2 = _coord_y(rect.end.y)
        else:
            fp_angle_deg = footprint.at.r if footprint.at.r is not None else 0.0
            fp_angle_rad = math.radians(fp_angle_deg)
            cos_a = math.cos(fp_angle_rad)
            sin_a = math.sin(fp_angle_rad)
            gx1, gy1 = _fp_local_to_global(
                rect.start.x,
                rect.start.y,
                footprint.at.x,
                footprint.at.y,
                cos_a,
                sin_a,
            )
            gx2, gy2 = _fp_local_to_global(
                rect.end.x,
                rect.end.y,
                footprint.at.x,
                footprint.at.y,
                cos_a,
                sin_a,
            )
            x1 = mm_to_altium(gx1)
            y1 = _coord_y(gy1)
            x2 = mm_to_altium(gx2)
            y2 = _coord_y(gy2)
        ctx.primitives.append(
            AltiumFill(
                id=f"fill-{len(ctx.primitives)}",
                component_id=component_id,
                layer=layer,
                x1=x1,
                y1=y1,
                x2=x2,
                y2=y2,
            )
        )
        return True

    @staticmethod
    def to_kicad(fill: AltiumFill, ctx: ToKicadContext) -> None:
        if fill.rotation not in (0, 0.0):
            ctx.translation_warnings.append(
                f"Fill {fill.id!r} rotation={fill.rotation} ignored in IL->KiCad."
            )
        layer_name = _get_kicad_layer_name(fill.layer, ctx, fallback="F.SilkS")
        rect = kicad.pcb.Rect(
            start=_xy(altium_to_mm(fill.x1), _coord_y_mm(fill.y1)),
            end=_xy(altium_to_mm(fill.x2), _coord_y_mm(fill.y2)),
            layer=layer_name,
            stroke=_stroke(0.1),
            fill="solid",
        )
        if fill.component_id is not None and fill.component_id in ctx.component_by_id:
            component = ctx.component_by_id[fill.component_id]
            footprint = ctx.footprint_by_component_id[fill.component_id]
            start_local = _global_to_fp_local_mm(
                altium_to_mm(fill.x1),
                _coord_y_mm(fill.y1),
                component,
            )
            end_local = _global_to_fp_local_mm(
                altium_to_mm(fill.x2),
                _coord_y_mm(fill.y2),
                component,
            )
            footprint.fp_rects.append(
                kicad.pcb.Rect(
                    start=_xy(*start_local),
                    end=_xy(*end_local),
                    layer=layer_name,
                    stroke=_stroke(0.1),
                    fill="solid",
                )
            )
            return
        ctx.pcb.gr_rects.append(rect)


class RegionCodec:
    @staticmethod
    def to_altium_gr_poly(poly, ctx: ToAltiumContext) -> None:
        layer = _get_geo_layer(poly)
        if layer is None:
            return
        ctx.add_layer(layer)
        points = poly.pts.xys
        if len(points) < 2:
            return
        if _fill_is_yes(poly.fill) and len(points) >= 3:
            ctx.primitives.append(
                AltiumRegion(
                    id=f"region-{len(ctx.primitives)}",
                    layer=layer,
                    outline=[(mm_to_altium(pt.x), _coord_y(pt.y)) for pt in points],
                )
            )
            return
        width = _stroke_width(poly)
        for index in range(len(points)):
            point1 = points[index]
            point2 = points[(index + 1) % len(points)]
            ctx.primitives.append(
                AltiumTrack(
                    id=f"track-0-{len(ctx.primitives)}",
                    layer=layer,
                    x1=mm_to_altium(point1.x),
                    y1=_coord_y(point1.y),
                    x2=mm_to_altium(point2.x),
                    y2=_coord_y(point2.y),
                    width=width,
                )
            )

    @staticmethod
    def to_altium_fp_poly(
        poly,
        ctx: ToAltiumContext,
        component_id: str,
        footprint,
    ) -> None:
        layer = _get_geo_layer(poly)
        if layer is None:
            return
        ctx.add_layer(layer)
        fp_angle_deg = footprint.at.r if footprint.at.r is not None else 0.0
        fp_angle_rad = math.radians(fp_angle_deg)
        cos_a = math.cos(fp_angle_rad)
        sin_a = math.sin(fp_angle_rad)
        vertices = []
        for point in poly.pts.xys:
            global_x, global_y = _fp_local_to_global(
                point.x,
                point.y,
                footprint.at.x,
                footprint.at.y,
                cos_a,
                sin_a,
            )
            vertices.append((mm_to_altium(global_x), _coord_y(global_y)))
        if len(vertices) < 3:
            return
        ctx.primitives.append(
            AltiumRegion(
                id=f"region-{len(ctx.primitives)}",
                component_id=component_id,
                layer=layer,
                outline=vertices,
            )
        )

    @staticmethod
    def to_altium_zones(kicad_pcb, ctx: ToAltiumContext) -> None:
        for zone in kicad_pcb.zones:
            is_keepout = zone.keepout is not None
            if not zone.polygon or not zone.polygon.pts or not zone.polygon.pts.xys:
                continue
            keepout_restrictions = 0
            if is_keepout and zone.keepout:
                if zone.keepout.tracks == "not_allowed":
                    keepout_restrictions |= 1
                if zone.keepout.vias == "not_allowed":
                    keepout_restrictions |= 2
                if zone.keepout.copperpour == "not_allowed":
                    keepout_restrictions |= 4

            zone_layers: list[str] = []
            if zone.layers:
                zone_layers = list(zone.layers)
            elif zone.layer:
                zone_layers = [zone.layer]
            if not zone_layers:
                continue

            vertices = [
                (mm_to_altium(point.x), _coord_y(point.y))
                for point in zone.polygon.pts.xys
            ]
            if len(vertices) < 3:
                continue

            net_id = None if is_keepout else ctx.net_id_by_kicad_number.get(zone.net)
            for layer_name in zone_layers:
                layer = _get_altium_layer(layer_name)
                if layer is None:
                    continue
                ctx.add_layer(layer)
                ctx.primitives.append(
                    AltiumRegion(
                        id=f"region-{len(ctx.primitives)}",
                        layer=layer,
                        net_id=net_id,
                        outline=vertices,
                        is_keepout=is_keepout,
                        keepout_restrictions=keepout_restrictions,
                    )
                )

    @staticmethod
    def to_kicad(region: AltiumRegion, ctx: ToKicadContext) -> None:
        layer_name = _get_kicad_layer_name(region.layer, ctx, fallback="F.Cu")
        if (
            region.component_id is not None
            and region.component_id in ctx.component_by_id
        ):
            component = ctx.component_by_id[region.component_id]
            footprint = ctx.footprint_by_component_id[region.component_id]
            footprint.fp_poly.append(
                kicad.pcb.Polygon(
                    pts=_pts(
                        [
                            _global_to_fp_local_mm(
                                altium_to_mm(x),
                                _coord_y_mm(y),
                                component,
                            )
                            for x, y in region.outline
                        ]
                    ),
                    layer=layer_name,
                    fill="yes",
                )
            )
            return

        if layer_name.endswith(".Cu") or region.is_keepout or region.net_id is not None:
            zone_kwargs: dict[str, object] = {
                "net": ctx.net_number_by_id.get(region.net_id, 0),
                "net_name": ctx.net_name_by_id.get(region.net_id, ""),
                "layer": layer_name,
                "hatch": kicad.pcb.Hatch(mode="edge", pitch=0.5),
                "polygon": kicad.pcb.Polygon(
                    pts=_pts(
                        [(altium_to_mm(x), _coord_y_mm(y)) for x, y in region.outline]
                    )
                ),
                "filled_areas_thickness": False,
                "keepout": (
                    kicad.pcb.ZoneKeepout(
                        tracks=(
                            "not_allowed"
                            if region.keepout_restrictions & 1
                            else "allowed"
                        ),
                        vias=(
                            "not_allowed"
                            if region.keepout_restrictions & 2
                            else "allowed"
                        ),
                        pads="allowed",
                        copperpour=(
                            "not_allowed"
                            if region.keepout_restrictions & 4
                            else "allowed"
                        ),
                        footprints="allowed",
                    )
                    if region.is_keepout
                    else None
                ),
            }
            connect_pads = _make_zone_connect_pads(ctx)
            if connect_pads is not None:
                zone_kwargs["connect_pads"] = connect_pads
            if ctx.zone_min_thickness_mm is not None:
                zone_kwargs["min_thickness"] = ctx.zone_min_thickness_mm
            zone_fill = _make_zone_fill(region, ctx)
            if zone_fill is not None:
                zone_kwargs["fill"] = zone_fill
            if region.holes:
                ctx.translation_warnings.append(
                    f"Region {region.id!r} holes are not representable in the current "
                    "KiCad zone bridge and were dropped."
                )
            zone = kicad.pcb.Zone(**zone_kwargs)
            ctx.pcb.zones.append(zone)
            return

        ctx.pcb.gr_polys.append(
            kicad.pcb.Polygon(
                pts=_pts(
                    [(altium_to_mm(x), _coord_y_mm(y)) for x, y in region.outline]
                ),
                layer=layer_name,
                fill="yes",
            )
        )


class RuleCodec:
    @staticmethod
    def to_altium(kicad_pcb, ctx: ToAltiumContext) -> None:
        rules = kicad_pcb.setup.rules if kicad_pcb.setup else None
        if rules is None:
            return

        clearance = mm_to_altium(rules.min_clearance)
        ctx.rules.append(
            AltiumRuleClearance(
                name="Clearance",
                scope1="All",
                scope2="All",
                gap=clearance,
            )
        )

        width = mm_to_altium(rules.min_track_width)
        ctx.rules.append(
            AltiumRuleWidth(
                name="Width",
                scope1="All",
                scope2="All",
                min_limit=width,
                max_limit=width,
                preferred=width,
            )
        )

        if rules.min_through_hole_diameter:
            hole_size = mm_to_altium(rules.min_through_hole_diameter)
            ctx.rules.append(
                AltiumRuleHoleSize(
                    name="HoleSize",
                    scope1="All",
                    scope2="All",
                    min_limit=hole_size,
                    max_limit=hole_size,
                )
            )

    @staticmethod
    def to_kicad(rules: list[AltiumRule], ctx: ToKicadContext) -> None:
        if not rules:
            return
        ctx.translation_warnings.append(
            "Altium design rules are currently dropped in IL->KiCad conversion "
            "because the emitted `setup.rules` block is rejected by real KiCad."
        )


class BoardCodec:
    @staticmethod
    def extract_outline(kicad_pcb) -> list[AltiumBoardOutlineSegment]:
        from faebryk.libs.kicad.fileformats import kicad as kicad_file

        def _is_edge_cuts(geo) -> bool:
            return "Edge.Cuts" in kicad_file.geo.get_layers(geo)

        outline_segments: list[AltiumBoardOutlineSegment] = []

        for line in kicad_pcb.gr_lines:
            if _is_edge_cuts(line):
                outline_segments.append(
                    AltiumBoardOutlineSegment(
                        start=(mm_to_altium(line.start.x), _coord_y(line.start.y)),
                        end=(mm_to_altium(line.end.x), _coord_y(line.end.y)),
                        kind="line",
                    )
                )

        for rect in kicad_pcb.gr_rects:
            if _is_edge_cuts(rect):
                x1, y1 = rect.start.x, rect.start.y
                x2, y2 = rect.end.x, rect.end.y
                outline_segments.extend(
                    [
                        AltiumBoardOutlineSegment(
                            start=(mm_to_altium(x1), _coord_y(y1)),
                            end=(mm_to_altium(x2), _coord_y(y1)),
                            kind="line",
                        ),
                        AltiumBoardOutlineSegment(
                            start=(mm_to_altium(x2), _coord_y(y1)),
                            end=(mm_to_altium(x2), _coord_y(y2)),
                            kind="line",
                        ),
                        AltiumBoardOutlineSegment(
                            start=(mm_to_altium(x2), _coord_y(y2)),
                            end=(mm_to_altium(x1), _coord_y(y2)),
                            kind="line",
                        ),
                        AltiumBoardOutlineSegment(
                            start=(mm_to_altium(x1), _coord_y(y2)),
                            end=(mm_to_altium(x1), _coord_y(y1)),
                            kind="line",
                        ),
                    ]
                )

        for arc in kicad_pcb.gr_arcs:
            if not _is_edge_cuts(arc):
                continue
            cx, cy, radius, start_angle, end_angle = _arc_from_three_points(
                arc.start.x,
                arc.start.y,
                arc.mid.x,
                arc.mid.y,
                arc.end.x,
                arc.end.y,
            )
            if radius < 1e-6:
                continue
            outline_segments.append(
                AltiumBoardOutlineSegment(
                    start=(mm_to_altium(arc.start.x), _coord_y(arc.start.y)),
                    end=(mm_to_altium(arc.end.x), _coord_y(arc.end.y)),
                    kind="arc",
                    center=(mm_to_altium(cx), _coord_y(cy)),
                    radius=mm_to_altium(radius),
                    start_angle_deg=start_angle,
                    end_angle_deg=end_angle,
                )
            )

        return outline_segments

    @staticmethod
    def to_altium_graphics(kicad_pcb, ctx: ToAltiumContext) -> None:
        from faebryk.libs.kicad.fileformats import kicad as kicad_file

        def _is_edge_cuts(geo) -> bool:
            return "Edge.Cuts" in kicad_file.geo.get_layers(geo)

        for line in kicad_pcb.gr_lines:
            if _is_edge_cuts(line):
                continue
            TrackCodec.to_altium_graphic_line(line, ctx)

        for arc in kicad_pcb.gr_arcs:
            if _is_edge_cuts(arc):
                continue
            ArcCodec.to_altium_graphic_arc(arc, ctx)

        for circle in kicad_pcb.gr_circles:
            ArcCodec.to_altium_circle(circle, ctx)

        for rect in kicad_pcb.gr_rects:
            if _is_edge_cuts(rect):
                continue
            if not FillCodec.to_altium_rect(rect, ctx):
                TrackCodec.to_altium_rect_outline(rect, ctx)

        for poly in kicad_pcb.gr_polys:
            if _is_edge_cuts(poly):
                continue
            RegionCodec.to_altium_gr_poly(poly, ctx)

        for text in kicad_pcb.gr_texts:
            TextCodec.to_altium_gr_text(text, ctx)

    @staticmethod
    def to_kicad(doc: AltiumPcb, ctx: ToKicadContext) -> None:
        board_thickness_mm = altium_to_mm(doc.board.board_thickness)
        # Some real files encode BOARDTHICKNESS scaled 10_000x beyond the
        # coordinate-unit convention used elsewhere. Normalize those outliers so
        # the emitted KiCad header remains loadable.
        if board_thickness_mm > 100:
            board_thickness_mm /= 10_000
            ctx.translation_warnings.append(
                "Normalized oversized Altium board thickness while emitting KiCad "
                "board setup."
            )
        ctx.pcb.general = kicad.pcb.General(thickness=board_thickness_mm)
        copper_stack_layers = _kicad_copper_stack_layers(doc)
        inner_copper_layers = iter(copper_stack_layers[1:-1])
        if doc.board.layers:
            for layer in doc.board.layers:
                preferred_name = layer.source_name or layer.name
                if preferred_name in {"Top Layer", "Bottom Layer"}:
                    assigned_name = "F.Cu" if preferred_name == "Top Layer" else "B.Cu"
                elif preferred_name.startswith(("Mid-Layer ", "Internal Plane ")):
                    assigned_name = next(inner_copper_layers, copper_stack_layers[-1])
                else:
                    assigned_name = _assign_kicad_layer_name(
                        layer.altium_layer_number,
                        preferred_name,
                        ctx,
                    )
                ctx.layer_name_by_number[layer.altium_layer_number] = assigned_name
        for layer_name in copper_stack_layers:
            _ensure_kicad_layer_present(layer_name, ctx)

        layer_numbers = _used_altium_layers(doc)
        for layer_number in sorted(layer_numbers):
            layer_name = ctx.layer_name_by_number.setdefault(
                layer_number,
                _assign_kicad_layer_name(layer_number, None, ctx),
            )
            _ensure_kicad_layer_present(layer_name, ctx)
        if doc.board.outline:
            _ensure_kicad_layer_present("Edge.Cuts", ctx)

        for segment in doc.board.outline:
            if segment.kind == "arc":
                start, mid, end = _outline_arc_points_mm(segment)
                ctx.pcb.gr_arcs.append(
                    kicad.pcb.Arc(
                        start=_xy(*start),
                        mid=_xy(*mid),
                        end=_xy(*end),
                        layer="Edge.Cuts",
                        stroke=_stroke(0.1),
                    )
                )
                continue
            ctx.pcb.gr_lines.append(
                kicad.pcb.Line(
                    start=_xy(
                        altium_to_mm(segment.start[0]), _coord_y_mm(segment.start[1])
                    ),
                    end=_xy(
                        altium_to_mm(segment.end[0]),
                        _coord_y_mm(segment.end[1]),
                    ),
                    layer="Edge.Cuts",
                    stroke=_stroke(0.1),
                )
            )


def _get_altium_layer(kicad_layer: str | None) -> int | None:
    if kicad_layer is None:
        return None
    return LAYER_MAP.get(kicad_layer)


def _get_kicad_layer_name(
    layer: int | LayerReference | None,
    ctx: ToKicadContext,
    *,
    fallback: str,
) -> str:
    layer_number: int | None = None
    if isinstance(layer, int):
        layer_number = layer
    elif isinstance(layer, LayerReference):
        layer_number = layer.altium_layer_number
    if layer_number is None:
        return fallback
    if layer_number in ctx.layer_name_by_number:
        return ctx.layer_name_by_number[layer_number]
    mapped_name = _ALTIUM_TO_KICAD_LAYER_NAMES.get(layer_number)
    if mapped_name is not None:
        return mapped_name
    assigned_name = _assign_kicad_layer_name(layer_number, None, ctx)
    ctx.layer_name_by_number[layer_number] = assigned_name
    _ensure_kicad_layer_present(assigned_name, ctx)
    return assigned_name


def _coord_y(mm_y: float) -> int:
    return -mm_to_altium(mm_y)


def _coord_y_mm(altium_y: int) -> float:
    return -altium_to_mm(altium_y)


def _xy(x: float, y: float):
    return kicad.pcb.Xy(x=x, y=y)


def _xyr(x: float, y: float, r: float | None):
    return kicad.pcb.Xyr(x=x, y=y, r=r)


def _stroke(width_mm: float):
    return kicad.pcb.Stroke(width=max(width_mm, 0.01), type="solid")


def _font_face(font) -> str | None:
    face = getattr(font, "face", None)
    if face:
        return str(face)
    return None


def _font_flag(font, name: str) -> bool:
    value = getattr(font, name, None)
    return bool(value) if value is not None else False


def _justify_to_altium_position(justify) -> int:
    if justify is None:
        return _ALTIUM_TEXT_POSITION_LEFT_BOTTOM
    tokens = {
        str(value).lower()
        for value in (justify.justify1, justify.justify2, justify.justify3)
        if value is not None
    }
    if "left" in tokens:
        horiz = "left"
    elif "right" in tokens:
        horiz = "right"
    else:
        horiz = "center"
    if "top" in tokens:
        vert = "top"
    elif "bottom" in tokens:
        vert = "bottom"
    else:
        vert = "center"
    return {
        ("left", "top"): 1,
        ("left", "center"): 2,
        ("left", "bottom"): 3,
        ("center", "top"): 4,
        ("center", "center"): 5,
        ("center", "bottom"): 6,
        ("right", "top"): 7,
        ("right", "center"): 8,
        ("right", "bottom"): 9,
    }[(horiz, vert)]


def _altium_text_size_mm(text: AltiumText) -> float:
    size_mm = max(altium_to_mm(text.height), 0.01)
    if text.font_type == _ALTIUM_TEXT_TYPE_TRUETYPE:
        font_name = text.font_name.lower()
        if "arial" in font_name:
            size_mm *= 0.63
        else:
            size_mm *= 0.5
    return max(size_mm, 0.01)


def _altium_text_thickness_mm(text: AltiumText) -> float:
    return max(altium_to_mm(text.stroke_width or mm_to_altium(0.1)), 0.01)


def _altium_text_justify(text: AltiumText):
    code = (
        text.text_justification
        if text.is_justification_valid
        else _ALTIUM_TEXT_POSITION_LEFT_BOTTOM
    )
    justify_tokens: list[str] = []
    if code in {1, 2, 3}:
        justify_tokens.append("left")
    elif code in {7, 8, 9}:
        justify_tokens.append("right")
    if code in {1, 4, 7}:
        justify_tokens.append("top")
    elif code in {3, 6, 9}:
        justify_tokens.append("bottom")
    if text.is_mirrored:
        justify_tokens.append("mirror")
    if not justify_tokens:
        return None
    slots = justify_tokens + [None, None, None]
    return kicad.pcb.Justify(
        justify1=slots[0],
        justify2=slots[1],
        justify3=slots[2],
    )


def _altium_text_effects(text: AltiumText):
    return kicad.pcb.Effects(
        font=kicad.pcb.Font(
            size=kicad.pcb.Wh(
                w=_altium_text_size_mm(text),
                h=_altium_text_size_mm(text),
            ),
            thickness=_altium_text_thickness_mm(text),
            bold=True if text.is_bold else None,
            italic=True if text.is_italic else None,
        ),
        justify=_altium_text_justify(text),
    )


def _rotate_point_mm(
    x_mm: float,
    y_mm: float,
    origin_x_mm: float,
    origin_y_mm: float,
    rotation_deg: float,
) -> tuple[float, float]:
    angle = math.radians(rotation_deg)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    dx = x_mm - origin_x_mm
    dy = y_mm - origin_y_mm
    return (
        origin_x_mm + dx * cos_a + dy * sin_a,
        origin_y_mm - dx * sin_a + dy * cos_a,
    )


def _aligned_text_position_mm(text: AltiumText) -> tuple[float, float]:
    origin_x_mm = altium_to_mm(text.x)
    origin_y_mm = _coord_y_mm(text.y)
    x_mm = origin_x_mm
    y_mm = origin_y_mm
    margin_mm = altium_to_mm(
        text.text_offset_width if text.is_offset_border else text.margin_border_width
    )
    rect_width_mm = max(altium_to_mm(text.textbox_rect_width) - margin_mm * 2, 0.0)
    rect_height_mm = altium_to_mm(text.height)
    if text.is_mirrored:
        rect_width_mm = -rect_width_mm
    code = (
        text.text_justification
        if text.is_justification_valid
        else _ALTIUM_TEXT_POSITION_LEFT_BOTTOM
    )
    if code == 1:
        y_mm -= rect_height_mm
    elif code == 2:
        y_mm -= rect_height_mm / 2
    elif code == 4:
        x_mm += rect_width_mm / 2
        y_mm -= rect_height_mm
    elif code == 5:
        x_mm += rect_width_mm / 2
        y_mm -= rect_height_mm / 2
    elif code == 6:
        x_mm += rect_width_mm / 2
    elif code == 7:
        x_mm += rect_width_mm
        y_mm -= rect_height_mm
    elif code == 8:
        x_mm += rect_width_mm
        y_mm -= rect_height_mm / 2
    elif code == 9:
        x_mm += rect_width_mm

    char_size_mm = _altium_text_size_mm(text)
    if text.font_type == _ALTIUM_TEXT_TYPE_TRUETYPE:
        if code in {1, 4, 7}:
            y_mm -= char_size_mm * 0.016
        elif code in {2, 5, 8}:
            y_mm += char_size_mm * 0.085
        else:
            y_mm += char_size_mm * 0.17
    else:
        if code in {1, 4, 7}:
            y_mm -= char_size_mm * 0.0407
        elif code in {2, 5, 8}:
            y_mm += char_size_mm * 0.0355
        else:
            y_mm += char_size_mm * 0.1225

    return _rotate_point_mm(x_mm, y_mm, origin_x_mm, origin_y_mm, text.rotation)


def _altium_text_layer(
    text: AltiumText,
    layer_name: str,
):
    return kicad.pcb.TextLayer(
        layer=layer_name,
        knockout="knockout" if text.is_inverted else None,
    )


def _make_component_property(
    name: str,
    value: str,
    *,
    component: AltiumComponent,
    text: AltiumText | None,
    default_y_mm: float,
    default_layer: str,
    hidden: bool,
    ctx: ToKicadContext,
):
    if text is not None:
        aligned_x_mm, aligned_y_mm = _aligned_text_position_mm(text)
        x_mm, y_mm = _global_to_fp_local_mm(aligned_x_mm, aligned_y_mm, component)
        at = _xyr(x_mm, y_mm, text.rotation)
        layer_name = _get_kicad_layer_name(text.layer, ctx, fallback=default_layer)
        effects = _altium_text_effects(text)
    else:
        at = _xyr(0.0, default_y_mm, component.rotation)
        layer_name = default_layer
        effects = kicad.pcb.Effects(
            font=kicad.pcb.Font(
                size=kicad.pcb.Wh(w=1.0, h=1.0),
                thickness=0.15,
            )
        )
    return kicad.pcb.Property(
        name=name,
        value=value,
        at=at,
        layer=layer_name,
        hide=hidden,
        effects=effects,
    )


def _make_property(name: str, value: str, *, y_mm: float, hidden: bool):
    return kicad.pcb.Property(
        name=name,
        value=value,
        at=_xyr(0.0, y_mm, 0.0),
        layer="F.SilkS",
        hide=hidden,
        effects=kicad.pcb.Effects(
            font=kicad.pcb.Font(
                size=kicad.pcb.Wh(w=1.0, h=1.0),
                thickness=0.15,
            )
        ),
    )


def _try_get_property(properties: list, name: str) -> str | None:
    for prop in properties:
        if prop.name == name:
            return prop.value
    return None


def _fp_local_to_global(
    lx: float,
    ly: float,
    fp_x: float,
    fp_y: float,
    cos_a: float,
    sin_a: float,
) -> tuple[float, float]:
    gx = fp_x + lx * cos_a + ly * sin_a
    gy = fp_y - lx * sin_a + ly * cos_a
    return gx, gy


def _global_to_fp_local_mm(
    x_mm: float,
    y_mm: float,
    component: AltiumComponent,
) -> tuple[float, float]:
    fp_x_mm = altium_to_mm(component.x)
    fp_y_mm = _coord_y_mm(component.y)
    angle = math.radians(component.rotation)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    dx = x_mm - fp_x_mm
    dy = y_mm - fp_y_mm
    return dx * cos_a - dy * sin_a, dx * sin_a + dy * cos_a


def _stroke_width(obj) -> int:
    if obj.stroke is not None and obj.stroke.width:
        return mm_to_altium(obj.stroke.width)
    return mm_to_altium(0.1)


def _get_geo_layer(obj) -> int | None:
    layers = kicad.geo.get_layers(obj)
    for layer_name in layers:
        mapped = _get_altium_layer(layer_name)
        if mapped is not None:
            return mapped
    return None


def _get_text_layer(obj) -> int | None:
    if obj.layer is not None and hasattr(obj.layer, "layer"):
        return _get_altium_layer(obj.layer.layer)
    return _get_altium_layer(obj.layer)


def _arc_from_three_points(
    sx: float,
    sy: float,
    mx: float,
    my: float,
    ex: float,
    ey: float,
) -> tuple[float, float, float, float, float]:
    ax, ay = sx, sy
    bx, by = mx, my
    cx, cy = ex, ey

    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return (sx, sy, 0.0, 0.0, 0.0)

    ux = (
        (ax * ax + ay * ay) * (by - cy)
        + (bx * bx + by * by) * (cy - ay)
        + (cx * cx + cy * cy) * (ay - by)
    ) / d
    uy = (
        (ax * ax + ay * ay) * (cx - bx)
        + (bx * bx + by * by) * (ax - cx)
        + (cx * cx + cy * cy) * (bx - ax)
    ) / d

    radius = math.sqrt((ax - ux) ** 2 + (ay - uy) ** 2)
    start_angle = math.degrees(math.atan2(-(sy - uy), sx - ux)) % 360
    end_angle = math.degrees(math.atan2(-(ey - uy), ex - ux)) % 360
    mid_angle = math.degrees(math.atan2(-(my - uy), mx - ux)) % 360

    def _angle_between(angle: float, start: float, end: float) -> bool:
        if start <= end:
            return start <= angle <= end
        return angle >= start or angle <= end

    if not _angle_between(mid_angle, start_angle, end_angle):
        start_angle, end_angle = end_angle, start_angle

    return (ux, uy, radius, start_angle, end_angle)


def _arc_points_mm(
    arc: AltiumArc,
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    center_x_mm = altium_to_mm(arc.center_x)
    center_y_mm = _coord_y_mm(arc.center_y)
    radius_mm = altium_to_mm(arc.radius)
    sweep = arc.end_angle - arc.start_angle
    if sweep <= 0:
        sweep += 360.0
    mid_angle = arc.start_angle + sweep / 2.0
    return (
        _arc_point_mm(center_x_mm, center_y_mm, radius_mm, arc.start_angle),
        _arc_point_mm(center_x_mm, center_y_mm, radius_mm, mid_angle),
        _arc_point_mm(center_x_mm, center_y_mm, radius_mm, arc.end_angle),
    )


def _arc_point_mm(
    center_x_mm: float,
    center_y_mm: float,
    radius_mm: float,
    angle_deg: float,
) -> tuple[float, float]:
    angle_rad = math.radians(angle_deg)
    return (
        center_x_mm + radius_mm * math.cos(angle_rad),
        center_y_mm - radius_mm * math.sin(angle_rad),
    )


def _outline_arc_points_mm(
    segment: AltiumBoardOutlineSegment,
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    if (
        segment.center is None
        or segment.radius is None
        or segment.start_angle_deg is None
        or segment.end_angle_deg is None
    ):
        start = (altium_to_mm(segment.start[0]), _coord_y_mm(segment.start[1]))
        end = (altium_to_mm(segment.end[0]), _coord_y_mm(segment.end[1]))
        mid = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
        return start, mid, end
    center_x_mm = altium_to_mm(segment.center[0])
    center_y_mm = _coord_y_mm(segment.center[1])
    radius_mm = altium_to_mm(int(segment.radius))
    sweep = segment.end_angle_deg - segment.start_angle_deg
    if sweep <= 0:
        sweep += 360.0
    mid_angle = segment.start_angle_deg + sweep / 2.0
    return (
        _arc_point_mm(center_x_mm, center_y_mm, radius_mm, segment.start_angle_deg),
        _arc_point_mm(center_x_mm, center_y_mm, radius_mm, mid_angle),
        _arc_point_mm(center_x_mm, center_y_mm, radius_mm, segment.end_angle_deg),
    )


def _layer_kind(layer_number: int) -> AltiumLayerType:
    if 1 <= layer_number <= 32:
        return AltiumLayerType.COPPER
    if layer_number in (33, 34):
        return AltiumLayerType.OVERLAY
    if layer_number in (35, 36):
        return AltiumLayerType.PASTE
    if layer_number in (37, 38):
        return AltiumLayerType.MASK
    return AltiumLayerType.MECHANICAL


def _make_board_layer(layer_number: int) -> BoardLayer:
    return BoardLayer(
        id=f"layer-{layer_number}",
        name=ALTIUM_LAYER_NAMES.get(layer_number, f"Layer {layer_number}"),
        kind=_layer_kind(layer_number),
        altium_layer_number=layer_number,
    )


def _pad_shape_from_kicad(shape: str) -> AltiumPadShape:
    shape_name = str(shape)
    if shape_name == "roundrect":
        return AltiumPadShape.ROUND_RECT
    mapped_shape = PAD_SHAPE_MAP.get(shape_name, PAD_SHAPE_ROUND)
    if mapped_shape == PAD_SHAPE_RECT:
        return AltiumPadShape.RECT
    if mapped_shape == PAD_SHAPE_OCTAGONAL:
        return AltiumPadShape.OCTAGONAL
    return AltiumPadShape.ROUND


def _pad_shape_to_kicad(pad: AltiumPad) -> str:
    if pad.shape == AltiumPadShape.RECT:
        return "rect"
    if pad.shape == AltiumPadShape.ROUND_RECT:
        return "roundrect"
    if pad.shape == AltiumPadShape.OCTAGONAL:
        return "rect"
    if pad.top_size_x != pad.top_size_y:
        return "oval"
    return "circle"


def _copper_layer_numbers(kicad_pcb) -> list[int]:
    copper_layers: list[int] = []
    for layer in getattr(kicad_pcb, "layers", []) or []:
        if layer.type not in ("signal", "power", "mixed"):
            continue
        mapped = _get_altium_layer(layer.name)
        if mapped is None or mapped in copper_layers:
            continue
        copper_layers.append(mapped)
    return copper_layers


def _fill_is_solid(fill: object) -> bool:
    return str(fill).split(".")[-1] in {"solid", "yes"}


def _fill_is_yes(fill: object) -> bool:
    return str(fill).split(".")[-1] == "yes"


def _kicad_layer_type(layer_name: str) -> str:
    return "signal" if layer_name.endswith(".Cu") else "user"


def _ensure_kicad_layer_present(layer_name: str, ctx: ToKicadContext) -> None:
    if layer_name not in _KICAD_LAYER_NUMBERS:
        return
    if any(existing.name == layer_name for existing in ctx.pcb.layers):
        return
    ctx.pcb.layers.append(
        kicad.pcb.Layer(
            number=_KICAD_LAYER_NUMBERS[layer_name],
            name=layer_name,
            type=_kicad_layer_type(layer_name),
        )
    )


def _assign_kicad_layer_name(
    layer_number: int,
    preferred_name: str | None,
    ctx: ToKicadContext,
) -> str:
    normalized_preferred = (
        preferred_name.replace(" ", "") if preferred_name is not None else None
    )
    if normalized_preferred in _ALTIUM_SPECIAL_LAYER_NAMES:
        return _ALTIUM_SPECIAL_LAYER_NAMES[normalized_preferred]
    if preferred_name in _ALTIUM_SPECIAL_LAYER_NAMES:
        return _ALTIUM_SPECIAL_LAYER_NAMES[preferred_name]

    mapped_name = _ALTIUM_TO_KICAD_LAYER_NAMES.get(layer_number)
    if mapped_name is not None:
        return mapped_name

    if 57 <= layer_number <= 70:
        direct_user_layer = f"User.{layer_number - 56}"
        if direct_user_layer in _KICAD_LAYER_NUMBERS:
            return direct_user_layer

    used_layer_names = set(ctx.layer_name_by_number.values())
    if (
        preferred_name in _KICAD_LAYER_NUMBERS
        and preferred_name not in used_layer_names
    ):
        return preferred_name

    for candidate in _KICAD_FALLBACK_USER_LAYERS:
        if candidate not in used_layer_names:
            if preferred_name and preferred_name != candidate:
                ctx.translation_warnings.append(
                    f"Mapped unsupported Altium layer {preferred_name!r} to KiCad "
                    f"layer {candidate!r}."
                )
            return candidate

    fallback = _KICAD_FALLBACK_USER_LAYERS[-1]
    ctx.translation_warnings.append(
        f"Reused KiCad layer {fallback!r} for unsupported Altium layer "
        f"{preferred_name or layer_number!r} because no spare KiCad user layers "
        "remained."
    )
    return fallback


def _kicad_copper_stack_layers(doc: AltiumPcb) -> list[str]:
    layer_count = max(len(doc.board.copper_ordering.ordered_layer_ids), 2)
    # KiCad rejects odd copper-layer counts in the board layer table. When the
    # Altium IL only exposes an odd number of used copper layers, round up to
    # the next even stack and leave the extra inner layer empty.
    if layer_count % 2:
        layer_count += 1
    inner_layers = [f"In{index}.Cu" for index in range(1, max(layer_count - 1, 1))]
    if layer_count == 2:
        inner_layers = []
    return ["F.Cu", *inner_layers, "B.Cu"]


def _pts(points: list[tuple[float, float]]):
    pts = kicad.pcb.Pts()
    for x, y in points:
        pts.xys.append(_xy(x, y))
    return pts


def _altium_layer_number(layer: int | LayerReference | None) -> int | None:
    if isinstance(layer, int):
        return layer
    if isinstance(layer, LayerReference):
        return layer.altium_layer_number
    return None


def _used_altium_layers(doc: AltiumPcb) -> set[int]:
    used_layers = {1, 32}
    for layer in doc.board.layers:
        used_layers.add(layer.altium_layer_number)
    for primitive in doc.primitives:
        layer = primitive.layer
        if isinstance(layer, int):
            used_layers.add(layer)
        elif (
            isinstance(layer, LayerReference) and layer.altium_layer_number is not None
        ):
            used_layers.add(layer.altium_layer_number)
        if isinstance(primitive, AltiumVia):
            used_layers.add(primitive.start_layer)
            used_layers.add(primitive.end_layer)
    return {layer for layer in used_layers if layer > 0}


def _ensure_pad_footprint(pad: AltiumPad, ctx: ToKicadContext):
    if (
        pad.component_id is not None
        and pad.component_id in ctx.footprint_by_component_id
    ):
        return ctx.footprint_by_component_id[pad.component_id]

    ctx.synthetic_component_index += 1
    synthetic_id = pad.component_id or f"synthetic-pad-{ctx.synthetic_component_index}"
    layer_name = _get_kicad_layer_name(pad.layer, ctx, fallback="F.Cu")
    footprint = kicad.pcb.Footprint(
        name="Altium:SyntheticPad",
        layer=layer_name,
        at=_xyr(altium_to_mm(pad.x), _coord_y_mm(pad.y), 0.0),
    )
    footprint.propertys.append(
        _make_property(
            "Reference",
            f"ATP{ctx.synthetic_component_index}",
            y_mm=-1.0,
            hidden=True,
        )
    )
    footprint.propertys.append(
        _make_property("Value", "SyntheticPad", y_mm=1.0, hidden=True)
    )
    ctx.footprint_by_component_id[synthetic_id] = footprint
    ctx.pcb.footprints.append(footprint)
    stored_footprint = ctx.pcb.footprints[-1]
    ctx.footprint_by_component_id[synthetic_id] = stored_footprint
    return stored_footprint


def _prepare_component_text_roles(doc: AltiumPcb, ctx: ToKicadContext) -> None:
    component_by_id = {
        component.id: component
        for component in doc.components
        if component.id is not None
    }
    texts_by_component: dict[str, list[AltiumText]] = {}
    for primitive in doc.primitives:
        if not isinstance(primitive, AltiumText) or primitive.component_id is None:
            continue
        ctx.components_with_explicit_text.add(primitive.component_id)
        texts_by_component.setdefault(primitive.component_id, []).append(primitive)

    for component_id, texts in texts_by_component.items():
        component = component_by_id.get(component_id)
        if component is None:
            continue

        reference_text = next((text for text in texts if text.is_designator), None)
        if reference_text is None:
            reference_text = next(
                (
                    text
                    for text in texts
                    if _component_text_role(text, component) == "reference"
                ),
                None,
            )
        if reference_text is not None:
            ctx.reference_text_by_component_id[component_id] = reference_text
            if reference_text.id is not None:
                ctx.consumed_component_text_ids.add(reference_text.id)

        value_text = _choose_value_text(texts, component)
        if value_text is not None:
            ctx.value_text_by_component_id[component_id] = value_text
            if value_text.id is not None:
                ctx.consumed_component_text_ids.add(value_text.id)

        if (value_text is None and component.designator.isalpha()) or (
            value_text is not None
            and _normalize_component_text(value_text.text)
            == _normalize_component_text(component.designator)
        ):
            normalized_designator = _normalize_component_text(component.designator)
            for text in texts:
                if _normalize_component_text(text.text) != normalized_designator:
                    continue
                if text.id is not None:
                    ctx.consumed_component_text_ids.add(text.id)


def _prepare_zone_defaults(doc: AltiumPcb, ctx: ToKicadContext) -> None:
    for rule in doc.rules:
        if isinstance(rule, AltiumRuleClearance) and ctx.zone_clearance_mm is None:
            ctx.zone_clearance_mm = max(altium_to_mm(rule.gap), 0.0)
            continue
        if isinstance(rule, AltiumRuleWidth) and ctx.zone_min_thickness_mm is None:
            preferred = rule.preferred or rule.min_limit or rule.max_limit
            if preferred > 0:
                ctx.zone_min_thickness_mm = max(altium_to_mm(preferred), 0.01)
            continue
        if isinstance(rule, AltiumRulePolygonConnectStyle):
            if rule.connect_style == AltiumPolygonConnectStyle.DIRECT:
                ctx.zone_connect_mode = kicad.pcb.E_zone_connect_pads_mode.YES
            elif rule.connect_style == AltiumPolygonConnectStyle.NONE:
                ctx.zone_connect_mode = kicad.pcb.E_zone_connect_pads_mode.NO
            else:
                ctx.zone_connect_mode = None
            if rule.air_gap_width > 0:
                ctx.zone_thermal_gap_mm = max(altium_to_mm(rule.air_gap_width), 0.0)
            if rule.relief_conductor_width > 0:
                ctx.zone_thermal_bridge_width_mm = max(
                    altium_to_mm(rule.relief_conductor_width),
                    0.0,
                )


def _make_zone_connect_pads(ctx: ToKicadContext):
    if ctx.zone_connect_mode is None and ctx.zone_clearance_mm is None:
        return None
    return kicad.pcb.ConnectPads(
        mode=ctx.zone_connect_mode,
        clearance=ctx.zone_clearance_mm,
    )


def _make_zone_fill(region: AltiumRegion, ctx: ToKicadContext):
    fill_kwargs: dict[str, object] = {}
    if not region.is_keepout:
        fill_kwargs["enable"] = "yes"
    if ctx.zone_thermal_gap_mm is not None:
        fill_kwargs["thermal_gap"] = ctx.zone_thermal_gap_mm
    if ctx.zone_thermal_bridge_width_mm is not None:
        fill_kwargs["thermal_bridge_width"] = ctx.zone_thermal_bridge_width_mm
    if not fill_kwargs:
        return None
    return kicad.pcb.ZoneFill(**fill_kwargs)


def _normalize_component_text(text: str) -> str:
    return text.strip().lower()


def _altium_special_string_to_kicad(
    text: str,
    overrides: dict[str, str] | None = None,
) -> str:
    if not text:
        return text
    upper_overrides = {key.upper(): value for key, value in (overrides or {}).items()}

    def replacement(name: str) -> str:
        lookup = name.upper()
        return f"${{{upper_overrides.get(lookup, lookup)}}}"

    if text.startswith("."):
        return replacement(text[1:])
    if "'." not in text:
        return text

    result = text
    pos = len(result) - 1
    while pos > 0:
        if result[pos] != "." or result[pos - 1] != "'":
            pos -= 1
            continue
        end = result.find("'", pos + 1)
        if end == -1:
            pos -= 1
            continue
        result = (
            f"{result[: pos - 1]}"
            f"{replacement(result[pos + 1 : end])}"
            f"{result[end + 1 :]}"
        )
        pos = min(pos - 1, len(result) - 1)
    return result


def _component_text_placeholder(text: str) -> str | None:
    converted = _altium_special_string_to_kicad(
        text,
        {
            "DESIGNATOR": "REFERENCE",
            "COMMENT": "VALUE",
            "VALUE": "ALTIUM_VALUE",
            "LAYER_NAME": "LAYER",
            "PRINT_DATE": "CURRENT_DATE",
        },
    )
    normalized = _normalize_component_text(converted)
    if normalized in {"${reference}", "%r"}:
        return "${REFERENCE}"
    if normalized in {"${value}", "%v"}:
        return "${VALUE}"
    return None


def _component_text_role(
    text: AltiumText,
    component: AltiumComponent,
) -> str | None:
    if text.is_designator:
        return "reference"
    if text.is_comment:
        return "value"
    placeholder = _component_text_placeholder(text.text)
    if placeholder is not None:
        return None
    normalized = _normalize_component_text(text.text)
    if normalized == _normalize_component_text(component.designator):
        return "reference"
    if text.text.strip():
        return "value"
    return None


def _choose_value_text(
    texts: list[AltiumText],
    component: AltiumComponent,
) -> AltiumText | None:
    explicit = next((text for text in texts if text.is_comment), None)
    if explicit is not None:
        return explicit
    candidates = [
        text for text in texts if _component_text_role(text, component) == "value"
    ]
    candidates = [
        text for text in candidates if any(char.isalpha() for char in text.text.strip())
    ]
    if not candidates:
        return None

    def _score(text: AltiumText) -> tuple[int, int, int]:
        stripped = text.text.strip()
        has_alpha = int(any(char.isalpha() for char in stripped))
        not_numeric = int(not stripped.isdigit())
        return (has_alpha, not_numeric, len(stripped))

    return max(candidates, key=_score)


__all__ = [
    "ArcCodec",
    "BoardCodec",
    "ComponentCodec",
    "FillCodec",
    "NetCodec",
    "PadCodec",
    "RegionCodec",
    "RuleCodec",
    "TextCodec",
    "ToAltiumContext",
    "ToKicadContext",
    "TrackCodec",
    "ViaCodec",
    "convert_altium_to_kicad",
    "convert_kicad_to_altium",
    "convert_pcb",
]
