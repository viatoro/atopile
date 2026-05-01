"""Altium schematic IL contract tests."""

from __future__ import annotations

from faebryk.libs.eda.altium.models.schematic.il import (
    AltiumSchematic,
    SchematicComponent,
    SchematicParameter,
    SchematicPin,
    SchematicWire,
)


def test_schematic_il_groups_component_children_and_serializes() -> None:
    schematic = AltiumSchematic(
        header_parameters={"HEADER": "Protel", "Weight": "0"},
        components=[
            SchematicComponent(
                id="component-1",
                lib_reference="TLV9001",
                design_item_id="TLV9001IDBVR",
                description="Operational amplifier",
                location=(10_000_000, 10_000_000),
                orientation=1,
                pins=[
                    SchematicPin(
                        id="pin-1",
                        name="IN+",
                        designator="1",
                        location=(9_500_000, 10_000_000),
                        length=3_000_000,
                        electrical=4,
                    )
                ],
                parameters=[
                    SchematicParameter(
                        id="parameter-1",
                        name="Designator",
                        text="U1",
                        is_designator=True,
                        location=(10_500_000, 10_500_000),
                    )
                ],
            )
        ],
        wires=[
            SchematicWire(
                id="wire-1",
                vertices=[(0, 0), (1_000_000, 0), (1_000_000, 2_000_000)],
            )
        ],
    )

    payload = schematic.to_dict()

    assert len(schematic.components) == 1
    assert len(schematic.all_pins) == 1
    assert schematic.components[0].parameters[0].is_designator is True
    assert payload["components"][0]["pins"][0]["name"] == "IN+"
    assert payload["wires"][0]["vertices"][2] == (1_000_000, 2_000_000)
