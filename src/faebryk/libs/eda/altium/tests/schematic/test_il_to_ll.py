"""Altium schematic LL/IL translation tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from faebryk.libs.eda.altium.convert.schematic.file_ll import SchDocCodec
from faebryk.libs.eda.altium.convert.schematic.il_ll import (
    convert_il_to_ll,
    convert_ll_to_il,
)
from faebryk.libs.eda.altium.models.schematic.il import (
    AltiumSchematic,
    SchematicComponent,
    SchematicParameter,
    SchematicPin,
    SchematicPort,
    SchematicSheetEntry,
    SchematicSheetSymbol,
    SchematicWire,
)
from faebryk.libs.eda.altium.models.schematic.ll import (
    AltiumSchDoc,
    ComponentRecord,
    ParameterRecord,
    PinRecord,
    PortRecord,
    SchRecordType,
    SheetEntryRecord,
    SheetSymbolRecord,
    WireRecord,
)

_SCHDOC_FIXTURE = Path(
    "/home/ip/workspace/atopile_altium_pcb/.local/AltiumSharp/TestData/"
    "Power Supply.SchDoc"
)


def _build_ll_fixture() -> AltiumSchDoc:
    return AltiumSchDoc(
        header_parameters={"HEADER": "Protel", "Weight": "0"},
        records=[
            ComponentRecord(
                index=0,
                lib_reference="TLV9001",
                design_item_id="TLV9001IDBVR",
                description="Operational amplifier",
                location=(10_000_000, 10_000_000),
                orientation=1,
            ),
            PinRecord(
                index=1,
                owner_index=0,
                owner_part_id=1,
                name="IN+",
                designator="1",
                location=(9_500_000, 10_000_000),
                length=3_000_000,
                electrical=4,
            ),
            ParameterRecord(
                index=2,
                owner_index=0,
                owner_part_id=-1,
                record_type=int(SchRecordType.DESIGNATOR),
                name="Designator",
                text="U1",
                location=(10_500_000, 10_500_000),
                is_designator=True,
            ),
            WireRecord(
                index=3,
                vertices=[(0, 0), (1_000_000, 0), (1_000_000, 2_000_000)],
                line_width=1,
            ),
        ],
    )


def test_convert_ll_to_il_reconstructs_component_children() -> None:
    schematic = convert_ll_to_il(_build_ll_fixture())

    assert len(schematic.components) == 1
    assert schematic.components[0].pins[0].name == "IN+"
    assert schematic.components[0].parameters[0].text == "U1"
    assert len(schematic.wires) == 1
    assert schematic.components[0].location == (10_000_000, 10_000_000)
    assert schematic.components[0].pins[0].location == (9_500_000, 10_000_000)
    assert schematic.wires[0].vertices[2] == (100_000_000, 200_000_000)


def test_convert_il_to_ll_flattens_component_children_after_parent() -> None:
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
                        location=(10_500_000, 10_500_000),
                        is_designator=True,
                    )
                ],
            )
        ],
        wires=[
            SchematicWire(
                id="wire-1",
                vertices=[(0, 0), (1_000_000, 0), (1_000_000, 2_000_000)],
                line_width=1,
            )
        ],
    )

    doc = convert_il_to_ll(schematic)

    assert len(doc.records) == 4
    assert isinstance(doc.records[0], ComponentRecord)
    assert isinstance(doc.records[1], PinRecord)
    assert isinstance(doc.records[2], ParameterRecord)
    assert isinstance(doc.records[3], WireRecord)
    assert doc.records[1].owner_index == 0
    assert doc.records[2].owner_index == 0
    assert doc.records[3].owner_index == -1
    assert doc.records[3].vertices[2] == (10_000, 20_000)


def test_convert_ll_to_il_reconstructs_sheet_symbols_and_ports() -> None:
    schematic = convert_ll_to_il(
        AltiumSchDoc(
            records=[
                SheetSymbolRecord(
                    index=0,
                    location=(40_000_000, 30_000_000),
                    x_size=15_000_000,
                    y_size=5_000_000,
                    file_name="child.SchDoc",
                    sheet_name="Child",
                    symbol_type="Normal",
                ),
                SheetEntryRecord(
                    index=1,
                    owner_index=0,
                    name="IN",
                    side=0,
                    distance_from_top=1_000_000,
                ),
                PortRecord(
                    index=2,
                    location=(10_000_000, 20_000_000),
                    name="IN",
                    width=5_000_000,
                    height=1_000_000,
                ),
            ]
        )
    )

    assert len(schematic.sheet_symbols) == 1
    assert schematic.sheet_symbols[0].file_name == "child.SchDoc"
    assert schematic.sheet_symbols[0].sheet_name == "Child"
    assert schematic.sheet_symbols[0].entries[0].name == "IN"
    assert len(schematic.ports) == 1
    assert schematic.ports[0].name == "IN"


def test_convert_il_to_ll_flattens_sheet_symbols_after_parent() -> None:
    schematic = AltiumSchematic(
        sheet_symbols=[
            SchematicSheetSymbol(
                id="sheet-symbol-1",
                location=(40_000_000, 30_000_000),
                x_size=15_000_000,
                y_size=5_000_000,
                file_name="child.SchDoc",
                sheet_name="Child",
                entries=[
                    SchematicSheetEntry(
                        id="sheet-entry-1",
                        name="IN",
                        side=0,
                        distance_from_top=1_000_000,
                    )
                ],
            )
        ],
        ports=[
            SchematicPort(
                id="port-1",
                location=(10_000_000, 20_000_000),
                name="IN",
                width=5_000_000,
                height=1_000_000,
            )
        ],
    )

    doc = convert_il_to_ll(schematic)

    assert isinstance(doc.records[0], SheetSymbolRecord)
    assert isinstance(doc.records[1], SheetEntryRecord)
    assert isinstance(doc.records[2], PortRecord)
    assert doc.records[1].owner_index == 0


@pytest.mark.skipif(
    not _SCHDOC_FIXTURE.exists(),
    reason="local AltiumSharp schematic fixture not available",
)
def test_real_schdoc_fixture_ll_to_il_preserves_core_counts() -> None:
    schematic = convert_ll_to_il(SchDocCodec.read(_SCHDOC_FIXTURE))

    assert len(schematic.components) == 7
    assert sum(len(component.pins) for component in schematic.components) == 18
    assert len(schematic.wires) == 17
    assert len(schematic.net_labels) == 1
    assert len(schematic.junctions) == 7
    assert len(schematic.power_objects) == 7
