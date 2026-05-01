"""Altium IL core contract tests."""

from __future__ import annotations

from faebryk.libs.eda.altium.models.pcb.il import (
    AltiumArc,
    AltiumClassComponent,
    AltiumClassKind,
    AltiumClassLayer,
    AltiumClassNet,
    AltiumClassPad,
    AltiumComponent,
    AltiumFill,
    AltiumLayerType,
    AltiumNet,
    AltiumPad,
    AltiumPcb,
    AltiumPolygonConnectStyle,
    AltiumRegion,
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
    BoardLayer,
    BoardOutlineSegment,
)


def _make_board_with_connectivity() -> AltiumPcb:
    return AltiumPcb(
        board=BoardConfig(
            name="phase-1-board",
            board_thickness=250000,
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
                BoardOutlineSegment(start=(0, 0), end=(100000, 0)),
                BoardOutlineSegment(start=(100000, 0), end=(100000, 100000)),
                BoardOutlineSegment(start=(100000, 100000), end=(0, 100000)),
                BoardOutlineSegment(start=(0, 100000), end=(0, 0)),
            ],
        ),
        nets=[
            AltiumNet(id="net-gnd", name="GND"),
            AltiumNet(id="net-vcc", name="VCC"),
        ],
        components=[
            AltiumComponent(
                id="cmp-u1",
                designator="U1",
                footprint="QFN48",
                x=1000,
                y=2000,
                rotation=0.0,
                layer=1,
            ),
            AltiumComponent(
                id="cmp-r1",
                designator="R1",
                footprint="0603",
                x=100000,
                y=100000,
                rotation=90.0,
                layer=1,
            ),
        ],
        primitives=[
            AltiumPad(
                id="pad-u1-1",
                component_id="cmp-u1",
                name="1",
                layer=1,
                net_id="net-gnd",
                x=1100,
                y=2100,
            ),
            AltiumTrack(
                id="trk-1",
                layer=1,
                net_id="net-gnd",
                x1=1100,
                y1=2100,
                x2=2000,
                y2=2400,
                width=300,
            ),
            AltiumVia(
                id="via-1",
                net_id="net-gnd",
                x=1500,
                y=2500,
                start_layer=1,
                end_layer=32,
                diameter=1000,
                hole_size=500,
            ),
            AltiumArc(
                id="arc-1",
                layer=1,
                net_id="net-vcc",
                center_x=5000,
                center_y=5000,
                radius=1000,
                start_angle=10.0,
                end_angle=90.0,
                width=250,
            ),
            AltiumText(
                id="text-1",
                text="U1",
                layer=1,
                x=1000,
                y=2100,
                height=250,
                rotation=0.0,
                net_id="net-gnd",
            ),
            AltiumFill(
                id="fill-1",
                layer=1,
                net_id="net-vcc",
                x1=0,
                y1=0,
                x2=1000,
                y2=1000,
            ),
            AltiumRegion(
                id="region-1",
                layer=1,
                net_id="net-vcc",
                outline=[(0, 0), (1000, 0), (1000, 1000), (0, 1000)],
                is_keepout=False,
            ),
        ],
    )


def test_altium_il_contract_covering_primitives_layers_and_connectivity() -> None:
    """Verify ALIL-01, ALIL-02 and ALIL-03 contract behavior."""
    pcb = _make_board_with_connectivity()

    assert len(pcb.board.layers) == 2
    assert pcb.board.layers[0].id == "layer-top"
    assert len(pcb.primitives) == 7
    assert pcb.primitives[0].id == "pad-u1-1"
    assert pcb.pad_primitives[0].component_id == "cmp-u1"
    assert pcb.track_primitives[0].net_id == "net-gnd"
    assert pcb.via_primitives[0].net_id == "net-gnd"
    assert pcb.primitives[3].primitive_kind == "arc"  # type: ignore[comparison-overlap]

    net_ids = [n.id for n in pcb.nets]
    assert net_ids == ["net-gnd", "net-vcc"]
    assert pcb.components[0].id == "cmp-u1"


def test_altium_il_minimum_typed_rules_and_classes() -> None:
    """Verify ALIL-04 typed contracts are explicit and objective-checkable."""
    pcb = AltiumPcb(
        rules=[
            AltiumRuleClearance(id="r-clearance", name="Clearance", gap=2500),
            AltiumRuleWidth(
                id="r-width",
                name="Width",
                min_limit=1000,
                max_limit=1200,
                preferred=1100,
            ),
            AltiumRuleHoleSize(
                id="r-hole",
                name="Hole Size",
                min_limit=400,
                max_limit=500,
            ),
            AltiumRuleRoutingVias(
                id="r-routing-vias",
                name="Routing Vias",
                width=1000,
                min_width=900,
                max_width=1100,
                hole_width=500,
                min_hole_width=400,
                max_hole_width=600,
            ),
            AltiumRuleSolderMaskExpansion(
                id="r-smask",
                name="Solder Mask Expansion",
                expansion=200,
            ),
            AltiumRulePasteMaskExpansion(
                id="r-paste",
                name="Paste Mask Expansion",
                expansion=150,
            ),
            AltiumRulePolygonConnectStyle(
                id="r-poly",
                name="Polygon Connect Style",
                connect_style=AltiumPolygonConnectStyle.RELIEF,
                air_gap_width=300,
                relief_conductor_width=200,
                relief_entries=2,
            ),
        ],
        classes=[
            AltiumClassNet(
                id="class-net",
                name="power-nets",
                members=["net-gnd", "net-vcc"],
            ),
            AltiumClassComponent(
                id="class-component",
                name="ic",
                members=["cmp-u1"],
            ),
            AltiumClassPad(
                id="class-pad",
                name="critical-pads",
                members=["pad-u1-1"],
            ),
            AltiumClassLayer(
                id="class-layer",
                name="signal-layers",
                members=["layer-top", "layer-bottom"],
            ),
        ],
    )

    rule_kinds = [type(rule) for rule in pcb.rules]
    assert AltiumRuleClearance in [*rule_kinds]
    assert AltiumRuleWidth in rule_kinds
    assert AltiumRuleHoleSize in rule_kinds
    assert "routing_vias" in [rule.kind.value for rule in pcb.rules]  # type: ignore[union-attr]
    assert "solder_mask_expansion" in [rule.kind.value for rule in pcb.rules]  # type: ignore[union-attr]
    assert "paste_mask_expansion" in [rule.kind.value for rule in pcb.rules]  # type: ignore[union-attr]
    assert "polygon_connect_style" in [rule.kind.value for rule in pcb.rules]  # type: ignore[union-attr]

    class_kinds = [rule.kind for rule in pcb.classes]
    assert AltiumClassKind.NET in class_kinds
    assert AltiumClassKind.COMPONENT in class_kinds
    assert AltiumClassKind.PAD in class_kinds
    assert AltiumClassKind.LAYER in class_kinds
