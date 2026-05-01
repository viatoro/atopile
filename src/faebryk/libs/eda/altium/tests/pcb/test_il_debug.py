"""Debug serialization tests for the Altium IL model."""

from __future__ import annotations

from faebryk.libs.eda.altium.models.pcb.il import (
    AltiumArc,
    AltiumClassNet,
    AltiumComponent,
    AltiumLayerType,
    AltiumNet,
    AltiumPad,
    AltiumPcb,
    AltiumRuleClearance,
    AltiumRuleWidth,
    AltiumTrack,
    AltiumVia,
    BoardConfig,
    BoardCopperOrdering,
    BoardLayer,
    BoardOutlineSegment,
)


def test_altium_il_debug_payload() -> None:
    """Make sure IL data can be serialized for tests and REPL inspection."""
    pcb = AltiumPcb(
        board=BoardConfig(
            name="debug-board",
            board_thickness=350000,
            layers=[
                BoardLayer(
                    id="layer-top",
                    name="Top Layer",
                    kind=AltiumLayerType.COPPER,
                    altium_layer_number=1,
                    source_id="src:top",
                )
            ],
            copper_ordering=BoardCopperOrdering(ordered_layer_ids=["layer-top"]),
            outline=[
                BoardOutlineSegment(start=(0, 0), end=(100000, 0)),
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
                footprint="QFN24",
                x=100,
                y=200,
                rotation=0.0,
                layer=1,
            )
        ],
        rules=[
            AltiumRuleClearance(id="rule-clearance", name="clearance", gap=2500),
            AltiumRuleWidth(
                id="rule-width",
                name="track-width",
                min_limit=1000,
                max_limit=4000,
                preferred=1200,
            ),
        ],
        classes=[AltiumClassNet(id="class-gnd", name="gnd-class", members=["net-gnd"])],
        primitives=[
            AltiumPad(
                id="pad-u1-1",
                component_id="cmp-u1",
                name="1",
                layer=1,
                net_id="net-gnd",
            ),
            AltiumTrack(
                id="track-1",
                net_id="net-gnd",
                layer=1,
                x1=0,
                y1=0,
                x2=100000,
                y2=0,
                width=1000,
            ),
            AltiumVia(
                id="via-1",
                net_id="net-vcc",
                x=25000,
                y=25000,
                diameter=1000,
                hole_size=500,
            ),
            AltiumArc(
                id="arc-1",
                layer=1,
                net_id="net-vcc",
                center_x=50000,
                center_y=50000,
                radius=2000,
                start_angle=0.0,
                end_angle=90.0,
                width=600,
            ),
        ],
    )

    payload = pcb.to_dict()

    assert payload["board"]["name"] == "debug-board"
    assert len(payload["classes"]) == 1
    assert len(payload["rules"]) == 2
    assert len(payload["primitives"]) == 4
    assert payload["primitives"][0]["component_id"] == "cmp-u1"
    assert payload["primitives"][0]["net_id"] == "net-gnd"
    assert payload["primitives"][0]["primitive_kind"] == "pad"
    assert "extra_properties" in payload["board"]

    json_payload = pcb.to_json()
    assert isinstance(json_payload, str)
    assert '"debug-board"' in json_payload
