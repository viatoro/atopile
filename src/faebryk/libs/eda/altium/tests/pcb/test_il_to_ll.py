"""IL to low-level Altium record translation tests."""

from __future__ import annotations

import struct

from faebryk.libs.eda.altium import export_altium_pcb
from faebryk.libs.eda.altium.convert.pcb.il_ll import (
    convert_il_to_ll,
    convert_ll_to_il,
)
from faebryk.libs.eda.altium.models.pcb import ll
from faebryk.libs.eda.altium.models.pcb.il import (
    AltiumArc,
    AltiumClassComponent,
    AltiumClassLayer,
    AltiumClassNet,
    AltiumClassPad,
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
    AltiumRulePasteMaskExpansion,
    AltiumRulePolygonConnectStyle,
    AltiumRuleRoutingVias,
    AltiumRuleSolderMaskExpansion,
    AltiumRuleWidth,
    AltiumText,
    AltiumTrack,
    AltiumVia,
    BoardConfig,
    BoardCopperOrdering,
    BoardLayer,
)
from faebryk.libs.eda.altium.models.pcb.il import (
    BoardOutlineSegment as AltiumBoardOutlineSegment,
)


def _build_supported_il_fixture() -> AltiumPcb:
    return AltiumPcb(
        board=BoardConfig(
            name="translator-fixture",
            board_thickness=250_000,
            layers=[
                BoardLayer(
                    id="layer-top",
                    name="Top Layer",
                    kind=AltiumLayerType.COPPER,
                    altium_layer_number=1,
                ),
                BoardLayer(
                    id="layer-bottom",
                    name="Bottom Layer",
                    kind=AltiumLayerType.COPPER,
                    altium_layer_number=32,
                ),
            ],
            copper_ordering=BoardCopperOrdering(
                ordered_layer_ids=["layer-top", "layer-bottom"]
            ),
            outline=[
                AltiumBoardOutlineSegment(start=(0, 0), end=(100_000, 0)),
                AltiumBoardOutlineSegment(
                    start=(100_000, 0),
                    end=(100_000, 50_000),
                    kind="arc",
                    center=(75_000, 0),
                    radius=25_000,
                    start_angle_deg=0.0,
                    end_angle_deg=90.0,
                ),
                AltiumBoardOutlineSegment(start=(100_000, 50_000), end=(0, 50_000)),
                AltiumBoardOutlineSegment(start=(0, 50_000), end=(0, 0)),
            ],
        ),
        nets=[
            AltiumNet(id="net-gnd", name="GND"),
            AltiumNet(id="net-vcc", name="VCC"),
        ],
        components=[
            AltiumComponent(
                id="component-u1",
                designator="U1",
                footprint="QFN",
                x=2_000,
                y=3_000,
                rotation=0.0,
                layer=1,
            ),
        ],
        classes=[
            AltiumClassNet(
                id="class-net",
                name="power",
                members=["net-gnd", "net-vcc"],
            ),
            AltiumClassComponent(
                id="class-component",
                name="ics",
                members=["component-u1"],
            ),
            AltiumClassPad(id="class-pad", name="critical", members=["pad-u1-1"]),
            AltiumClassLayer(id="class-layer", name="layers", members=["layer-top"]),
        ],
        rules=[
            AltiumRuleClearance(name="Clearance", gap=250),
            AltiumRuleWidth(name="Width", min_limit=300, max_limit=400, preferred=350),
            AltiumRuleHoleSize(name="Hole Size", min_limit=100, max_limit=500),
            AltiumRuleRoutingVias(
                name="Routing Vias",
                width=900,
                min_width=800,
                max_width=1000,
                hole_width=250,
                min_hole_width=200,
                max_hole_width=300,
            ),
            AltiumRuleSolderMaskExpansion(name="Solder Mask Expansion", expansion=120),
            AltiumRulePasteMaskExpansion(name="Paste Mask Expansion", expansion=90),
            AltiumRulePolygonConnectStyle(
                name="Polygon Connect Style",
                connect_style=AltiumPolygonConnectStyle.RELIEF,
            ),
            AltiumRule(name="Custom"),
        ],
        primitives=[
            AltiumPad(
                id="pad-u1-1",
                component_id="component-u1",
                name="1",
                layer=1,
                net_id="net-gnd",
                x=1_000,
                y=2_000,
                top_size_x=400,
                top_size_y=400,
                mid_size_x=400,
                mid_size_y=400,
                bot_size_x=400,
                bot_size_y=400,
                hole_size=200,
                shape=AltiumPadShape.ROUND,
                rotation=0.0,
            ),
            AltiumPad(
                id="pad-float",
                component_id="missing-component",
                name="2",
                layer=1,
                net_id="net-vcc",
                x=2_000,
                y=3_000,
                top_size_x=300,
                top_size_y=300,
                mid_size_x=300,
                mid_size_y=300,
                bot_size_x=300,
                bot_size_y=300,
                hole_size=0,
            ),
            AltiumTrack(
                id="track-1",
                component_id="component-u1",
                layer=1,
                net_id="net-gnd",
                x1=1_000,
                y1=2_000,
                x2=5_000,
                y2=2_000,
                width=200,
            ),
            AltiumVia(
                id="via-1",
                net_id="net-vcc",
                layer=1,
                x=6_000,
                y=6_000,
                diameter=800,
                hole_size=300,
                start_layer=1,
                end_layer=32,
            ),
            AltiumArc(
                id="arc-1",
                component_id="component-u1",
                layer=1,
                net_id="net-gnd",
                center_x=7_000,
                center_y=8_000,
                radius=1_000,
                start_angle=0.0,
                end_angle=90.0,
                width=100,
            ),
            AltiumText(
                id="text-1",
                component_id="component-u1",
                layer=1,
                net_id="net-vcc",
                x=10_000,
                y=10_000,
                height=500,
                rotation=0.0,
                text="U1",
            ),
            AltiumFill(
                id="fill-1",
                component_id="component-u1",
                layer=1,
                net_id="net-vcc",
                x1=0,
                y1=0,
                x2=4_000,
                y2=4_000,
            ),
            AltiumRegion(
                id="region-1",
                component_id="component-u1",
                layer=1,
                net_id="net-gnd",
                outline=[(0, 0), (4_000, 0), (4_000, 4_000), (0, 4_000)],
                is_keepout=False,
            ),
        ],
    )


def test_il_to_ll_preserves_supported_semantics() -> None:
    ll = convert_il_to_ll(_build_supported_il_fixture())

    assert len(ll.nets) == 2
    assert len(ll.components) == 1
    assert ll.pads[0].component == 0
    assert ll.pads[1].component == -1
    assert ll.tracks[0].component == 0
    assert ll.vias[0].net == 1
    assert ll.arcs[0].radius == 1_000
    assert ll.texts[0].text == "U1"
    assert ll.fills[0].x1 == 0
    assert ll.board_vertices[0].x == 0
    assert ll.board_vertices[0].y == 0
    assert len(ll.board_vertices) > 4

    rule_kinds = {r.kind for r in ll.rules}
    assert {"Clearance", "Width", "HoleSize", "RoutingVias"}.issubset(rule_kinds)
    assert {
        "SolderMaskExpansion",
        "PasteMaskExpansion",
        "PolygonConnectStyle",
    }.issubset(rule_kinds)
    assert "generic" in rule_kinds


def test_ll_il_ll_preserves_revisited_board_outline_vertices() -> None:
    source = ll.AltiumPcbDoc(
        board_vertices=[
            ll.AltiumBoardVertex(x=0, y=0),
            ll.AltiumBoardVertex(x=10, y=0),
            ll.AltiumBoardVertex(x=10, y=10),
            ll.AltiumBoardVertex(x=10, y=0),
            ll.AltiumBoardVertex(x=0, y=0),
        ]
    )

    roundtripped = convert_il_to_ll(convert_ll_to_il(source))

    assert roundtripped.board_vertices == source.board_vertices


def test_ll_net_minus_one_decodes_to_no_net() -> None:
    doc = ll.AltiumPcbDoc(
        nets=[ll.AltiumNet(index=0, name="GND")],
        pads=[
            ll.AltiumPad(
                component=-1,
                name="1",
                x=0,
                y=0,
                top_size_x=100,
                top_size_y=100,
                mid_size_x=100,
                mid_size_y=100,
                bot_size_x=100,
                bot_size_y=100,
                hole_size=0,
                shape=1,
                rotation=0.0,
                net=-1,
                layer=1,
            ),
            ll.AltiumPad(
                component=-1,
                name="2",
                x=0,
                y=0,
                top_size_x=100,
                top_size_y=100,
                mid_size_x=100,
                mid_size_y=100,
                bot_size_x=100,
                bot_size_y=100,
                hole_size=0,
                shape=1,
                rotation=0.0,
                net=0,
                layer=1,
            ),
        ],
        layer_count=2,
    )

    il_doc = convert_ll_to_il(doc)

    assert il_doc.pad_primitives[0].net_id is None
    assert il_doc.pad_primitives[1].net_id == "net-1"


def test_polygon_metadata_overrides_region_net_and_layer() -> None:
    polygon_payload = "|LAYER=BOTTOM|NET=0|POUROVER=TRUE|".encode("cp1252") + b"\x00"
    doc = ll.AltiumPcbDoc(
        nets=[ll.AltiumNet(index=0, name="GND")],
        regions=[
            ll.AltiumRegion(
                layer=1,
                net=-1,
                component=-1,
                outline=[(0, 0), (100, 0), (100, 100), (0, 100)],
            )
        ],
        layer_count=2,
        raw_streams={
            "Polygons6/Header": struct.pack("<I", 1),
            "Polygons6/Data": struct.pack("<I", len(polygon_payload)) + polygon_payload,
        },
    )

    il_doc = convert_ll_to_il(doc)

    region = il_doc.primitives[0]
    assert isinstance(region, AltiumRegion)
    assert region.layer == 32
    assert region.net_id == "net-1"


def _build_seam_smoke_il() -> AltiumPcb:
    return AltiumPcb(
        board=BoardConfig(
            name="roundtrip-seam-smoke",
            board_thickness=250_000,
            copper_ordering=BoardCopperOrdering(
                ordered_layer_ids=["layer-top", "layer-bottom"]
            ),
            layers=[
                BoardLayer(
                    id="layer-top",
                    name="Top Layer",
                    kind=AltiumLayerType.COPPER,
                    altium_layer_number=1,
                ),
                BoardLayer(
                    id="layer-bottom",
                    name="Bottom Layer",
                    kind=AltiumLayerType.COPPER,
                    altium_layer_number=32,
                ),
            ],
            outline=[
                AltiumBoardOutlineSegment(start=(0, 0), end=(20_000, 0)),
                AltiumBoardOutlineSegment(
                    start=(20_000, 0),
                    end=(20_000, 20_000),
                    kind="arc",
                    center=(10_000, 0),
                    radius=10_000.0,
                    start_angle_deg=0.0,
                    end_angle_deg=90.0,
                ),
                AltiumBoardOutlineSegment(start=(20_000, 20_000), end=(0, 20_000)),
                AltiumBoardOutlineSegment(start=(0, 20_000), end=(0, 0)),
            ],
        ),
        nets=[AltiumNet(id="net-gnd", name="GND"), AltiumNet(id="net-vcc", name="VCC")],
        components=[
            AltiumComponent(
                id="component-u1",
                designator="U1",
                footprint="QFN",
                x=10_000,
                y=12_000,
                rotation=0.0,
                layer=1,
            ),
        ],
        rules=[
            AltiumRuleClearance(name="Clearance", gap=250),
            AltiumRuleWidth(name="Width", min_limit=500, max_limit=700, preferred=600),
            AltiumRuleRoutingVias(
                name="Routing Vias",
                width=1000,
                min_width=900,
                max_width=1100,
                hole_width=400,
                min_hole_width=300,
                max_hole_width=500,
            ),
            AltiumRuleSolderMaskExpansion(name="Solder Mask Expansion", expansion=200),
            AltiumRulePasteMaskExpansion(name="Paste Mask Expansion", expansion=100),
            AltiumRulePolygonConnectStyle(
                name="Polygon Connect Style",
                connect_style=AltiumPolygonConnectStyle.RELIEF,
            ),
        ],
        classes=[
            AltiumClassNet(
                id="class-gnd",
                name="gnd-nets",
                members=["net-gnd"],
            ),
            AltiumClassComponent(
                id="class-u1",
                name="footprints",
                members=["component-u1"],
            ),
            AltiumClassPad(
                id="class-pads",
                name="critical-pads",
                members=["pad-u1-1"],
            ),
            AltiumClassLayer(
                id="class-layers",
                name="signal-layers",
                members=["layer-top", "layer-bottom"],
            ),
        ],
        primitives=[
            AltiumPad(
                id="pad-u1-1",
                component_id="component-u1",
                name="1",
                layer=1,
                net_id="net-gnd",
                x=1_000,
                y=2_000,
                top_size_x=500,
                top_size_y=500,
                mid_size_x=500,
                mid_size_y=500,
                bot_size_x=500,
                bot_size_y=500,
                hole_size=0,
            ),
            AltiumTrack(
                id="track-1",
                component_id="component-u1",
                net_id="net-gnd",
                layer=1,
                x1=1_000,
                y1=2_000,
                x2=3_000,
                y2=2_000,
                width=300,
            ),
            AltiumArc(
                id="arc-1",
                component_id="component-u1",
                layer=1,
                net_id="net-gnd",
                center_x=2_000,
                center_y=2_000,
                radius=500,
                start_angle=0.0,
                end_angle=180.0,
                width=100,
            ),
            AltiumVia(
                id="via-1",
                net_id="net-vcc",
                layer=1,
                x=3_000,
                y=3_000,
                diameter=800,
                hole_size=400,
                start_layer=1,
                end_layer=32,
            ),
            AltiumFill(
                id="fill-1",
                component_id="component-u1",
                layer=1,
                net_id="net-vcc",
                x1=1_000,
                y1=1_000,
                x2=2_500,
                y2=2_000,
                rotation=0.0,
            ),
            AltiumRegion(
                id="region-1",
                component_id="component-u1",
                layer=1,
                net_id="net-vcc",
                outline=[(500, 500), (1_500, 500), (1_500, 1_500), (500, 1_500)],
            ),
            AltiumText(
                id="text-1",
                component_id="component-u1",
                layer=1,
                net_id="net-vcc",
                x=8_000,
                y=8_000,
                text="U1",
                height=250,
                rotation=0.0,
            ),
        ],
    )


def test_export_altium_pcb_public_entrypoint_reaches_il_ll_seam(
    tmp_path, monkeypatch
) -> None:
    il_doc = _build_seam_smoke_il()
    converted = {"ll_doc": None}

    monkeypatch.setattr(
        "faebryk.libs.eda.altium.convert_pcb",
        lambda _kicad: il_doc,
    )

    def fake_write(ll_doc, path):
        converted["ll_doc"] = ll_doc
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")

    monkeypatch.setattr(
        "faebryk.libs.eda.altium.PcbDocCodec.write",
        staticmethod(fake_write),
    )

    out_path = tmp_path / "seam-smoke.PcbDoc"
    export_altium_pcb(object(), out_path)

    assert out_path.exists()
    ll_doc = converted["ll_doc"]
    assert ll_doc is not None
    assert len(ll_doc.arcs) == 1
    assert len(ll_doc.regions) == 1
    assert len(ll_doc.board_vertices) >= 5
    assert ll_doc.translation_warnings is not None
    assert any("class" in warning.lower() for warning in ll_doc.translation_warnings)
