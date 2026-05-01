"""Low-level Altium schematic file/LL roundtrip tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from faebryk.libs.eda.altium.convert.schematic.file_ll import SchDocCodec
from faebryk.libs.eda.altium.models.schematic.ll import (
    AltiumSchDoc,
    ComponentRecord,
    JunctionRecord,
    NetLabelRecord,
    ParameterRecord,
    PinRecord,
    PowerObjectRecord,
    SchRecordType,
    UnknownRecord,
    WireRecord,
)

_HEADER = "Protel for Windows - Schematic Capture Binary File Version 5.0"
_SCHDOC_FIXTURE = Path(
    "/home/ip/workspace/atopile_altium_pcb/.local/AltiumSharp/TestData/"
    "Power Supply.SchDoc"
)


def _build_ll_fixture() -> AltiumSchDoc:
    return AltiumSchDoc(
        header_parameters={"HEADER": _HEADER, "Weight": "0", "UniqueID": "DOC12345"},
        additional_parameters={"HEADER": _HEADER},
        storage_data=b"synthetic-storage",
        records=[
            ComponentRecord(
                index=0,
                lib_reference="TLV9001",
                design_item_id="TLV9001IDBVR",
                description="Operational amplifier",
                location=(12_000_000, 8_000_000),
                orientation=1,
                part_count=2,
                display_mode_count=1,
                properties={"RECORD": "1", "Color": "128"},
            ),
            PinRecord(
                index=1,
                owner_index=0,
                owner_part_id=1,
                name="IN+",
                designator="1",
                location=(11_500_000, 8_500_000),
                length=3_000_000,
                electrical=4,
                orientation=1,
                show_name=True,
                show_designator=True,
                properties={"RECORD": "2"},
            ),
            ParameterRecord(
                index=2,
                owner_index=0,
                owner_part_id=-1,
                record_type=int(SchRecordType.DESIGNATOR),
                name="Designator",
                text="U1",
                location=(12_500_000, 9_000_000),
                color=8_388_608,
                font_id=1,
                is_designator=True,
                read_only_state=1,
                properties={"RECORD": "34"},
            ),
            WireRecord(
                index=3,
                vertices=[
                    (10_000_000, 10_000_000),
                    (12_000_000, 10_000_000),
                    (12_000_000, 12_000_000),
                ],
                color=8_388_608,
                line_width=1,
                properties={"RECORD": "27"},
            ),
            NetLabelRecord(
                index=4,
                text="VREF",
                location=(12_000_000, 12_500_000),
                color=128,
                font_id=1,
                properties={"RECORD": "25"},
            ),
            JunctionRecord(
                index=5,
                location=(12_000_000, 12_000_000),
                color=128,
                properties={"RECORD": "29"},
            ),
            PowerObjectRecord(
                index=6,
                text="GND",
                location=(9_000_000, 9_000_000),
                orientation=3,
                style=4,
                color=128,
                font_id=1,
                show_net_name=True,
                properties={"RECORD": "17"},
            ),
            UnknownRecord(
                index=7,
                record_type=int(SchRecordType.DOCUMENT_OPTIONS),
                properties={
                    "RECORD": "31",
                    "FontIdCount": "1",
                    "CustomX": "1000",
                    "CustomY": "800",
                },
            ),
        ],
    )


def test_schdoc_ll_roundtrip_preserves_supported_records_and_streams() -> None:
    doc = _build_ll_fixture()

    restored = SchDocCodec.decode(SchDocCodec.encode(doc))

    assert restored.header_parameters["HEADER"] == _HEADER
    assert restored.additional_parameters["HEADER"] == _HEADER
    assert restored.storage_data == b"synthetic-storage"
    assert len(restored.components) == 1
    assert len(restored.pins) == 1
    assert len(restored.parameters) == 1
    assert len(restored.wires) == 1
    assert len(restored.net_labels) == 1
    assert len(restored.junctions) == 1
    assert len(restored.power_objects) == 1
    assert len(restored.unknown_records) == 1
    assert restored.components[0].lib_reference == "TLV9001"
    assert restored.pins[0].owner_index == 0
    assert restored.parameters[0].is_designator is True
    assert restored.wires[0].vertices[2] == (12_000_000, 12_000_000)
    assert restored.unknown_records[0].record_type == int(
        SchRecordType.DOCUMENT_OPTIONS
    )


def test_schdoc_file_roundtrip_writes_a_valid_compound_file(tmp_path: Path) -> None:
    doc = _build_ll_fixture()
    output_path = tmp_path / "fixture.SchDoc"
    SchDocCodec.write(doc, output_path)

    restored = SchDocCodec.read(output_path)

    assert output_path.exists()
    assert len(restored.records) == len(doc.records)
    assert restored.components[0].description == "Operational amplifier"
    assert restored.net_labels[0].text == "VREF"


@pytest.mark.skipif(
    not _SCHDOC_FIXTURE.exists(),
    reason="local AltiumSharp schematic fixture not available",
)
def test_real_schdoc_fixture_parses_core_record_families() -> None:
    doc = SchDocCodec.read(_SCHDOC_FIXTURE)

    assert doc.header_parameters["HEADER"].startswith("Protel for Windows")
    assert doc.header_parameters["MinorVersion"] == "9"
    assert doc.additional_parameters["HEADER"].startswith("Protel for Windows")
    assert doc.storage_data is not None
    assert len(doc.components) == 7
    assert len(doc.pins) == 18
    assert len(doc.parameters) == 269
    assert len(doc.wires) == 17
    assert len(doc.net_labels) == 1
    assert len(doc.junctions) == 7
    assert len(doc.power_objects) == 7
    assert any(
        record.record_type == int(SchRecordType.DOCUMENT_OPTIONS)
        for record in doc.unknown_records
    )
