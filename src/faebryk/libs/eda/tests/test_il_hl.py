"""IL-to-HL converter tests for the shared EDA package."""

from __future__ import annotations

from pathlib import Path

import pytest

from faebryk.libs.eda.altium.convert.pcb.il_hl import (
    convert_pcb_il_to_hl as convert_altium_pcb_il_to_hl,
)
from faebryk.libs.eda.altium.convert.schematic.file_ll import SchDocCodec
from faebryk.libs.eda.altium.convert.schematic.il_hl import (
    _connected_entry_name_map,
    read_altium_schematic_to_hl,
)
from faebryk.libs.eda.altium.convert.schematic.il_hl import (
    convert_schematic_il_to_hl as convert_altium_schematic_il_to_hl,
)
from faebryk.libs.eda.altium.convert.schematic.il_ll import convert_ll_to_il
from faebryk.libs.eda.altium.models.pcb.il import (
    AltiumComponent,
    AltiumLayerType,
    AltiumNet,
    AltiumPad,
    AltiumPadShape,
    AltiumPcb,
    AltiumTrack,
    AltiumVia,
    BoardConfig,
    BoardCopperOrdering,
    BoardLayer,
)
from faebryk.libs.eda.altium.models.schematic.il import (
    AltiumSchematic,
    SchematicComponent,
    SchematicNetLabel,
    SchematicParameter,
    SchematicPin,
    SchematicPort,
    SchematicSheetEntry,
    SchematicSheetSymbol,
    SchematicWire,
)
from faebryk.libs.eda.hl.convert.eda_file_hl import Netlist
from faebryk.libs.eda.hl.convert.netlist_diff import compare_netlists
from faebryk.libs.eda.hl.convert.pcb_netlist import convert_pcb_to_netlist
from faebryk.libs.eda.hl.convert.schematic_netlist import convert_schematic_to_netlist
from faebryk.libs.eda.hl.models.pcb import Obround, Polygon, Rectangle, RoundedRectangle
from faebryk.libs.eda.kicad.convert.pcb.il_hl import (
    convert_pcb_il_to_hl as convert_kicad_pcb_il_to_hl,
)
from faebryk.libs.eda.kicad.convert.schematic.il_hl import _reference_for_sheet
from faebryk.libs.eda.kicad.convert.schematic.il_hl import (
    convert_schematic_il_to_hl as convert_kicad_schematic_il_to_hl,
)
from faebryk.libs.eda.kicad.convert.schematic.raw import read_raw_kicad_schematic
from faebryk.libs.kicad.fileformats import kicad

_ALTIUM_SCHDOC_FIXTURE = Path(
    "/home/ip/workspace/atopile_altium_pcb/.local/AltiumSharp/TestData/"
    "Power Supply.SchDoc"
)
_ALTIUM_HIERARCHICAL_SCHDOC_FIXTURE = Path(
    "/home/ip/workspace/atopile_altium_pcb/.local/AltiumSharp/TestData/Overview.SchDoc"
)
_ARCHIMAJOR_SCHDOC_FIXTURE = Path(
    "/home/ip/workspace/atopile_altium_pcb/.local/altium_demos/Mirror-test/"
    "Archimajor.SchDoc"
)
_SIMPLE_DASH_SCHDOC_FIXTURE = Path(
    "/home/ip/workspace/atopile_altium_pcb/.local/altium_demos/qfsae-pcb/"
    "simple-dash-q23/TOP.SchDoc"
)
_SIMPLE_DASH_NET_FIXTURE = Path(
    "/home/ip/workspace/atopile_altium_pcb/.local/altium_demos/qfsae-pcb/"
    "simple-dash-q23/TOP.NET"
)
_ESOCORE_ROOT_SCHDOC_FIXTURE = Path(
    "/home/ip/workspace/atopile_altium_pcb/.local/altium_demos/EsoCore/"
    "hardware/edge/altium/Schematics/Root_page.SchDoc"
)


def _pcb_xy(x: float, y: float) -> kicad.pcb.Xy:
    return kicad.pcb.Xy(x=x, y=y)


def _pcb_xyr(x: float, y: float, r: float | None = None) -> kicad.pcb.Xyr:
    return kicad.pcb.Xyr(x=x, y=y, r=r)


def _schematic_xyr(x: float, y: float, r: float | None = None) -> kicad.pcb.Xyr:
    return kicad.pcb.Xyr(x=x, y=y, r=r)


def _effects() -> kicad.pcb.Effects:
    return kicad.pcb.Effects(
        font=kicad.pcb.Font(
            size=kicad.pcb.Wh(w=1.0, h=1.0),
            thickness=0.15,
            bold=None,
            italic=None,
        ),
        hide=None,
        justify=None,
    )


def _pcb_property(name: str, value: str) -> kicad.pcb.Property:
    return kicad.pcb.Property(
        name=name,
        value=value,
        at=_pcb_xyr(0.0, 0.0, 0.0),
        unlocked=None,
        layer="F.SilkS",
        hide=None,
        uuid=None,
        effects=_effects(),
    )


def _schematic_property(name: str, value: str) -> kicad.schematic.Property:
    return kicad.schematic.Property(
        name=name,
        value=value,
        id=None,
        at=_schematic_xyr(0.0, 0.0, 0.0),
        effects=_effects(),
    )


def test_altium_pcb_il_to_hl_projects_into_expected_netlist() -> None:
    pcb = AltiumPcb(
        board=BoardConfig(
            id="board",
            name="fixture",
            layers=[
                BoardLayer(
                    id="top",
                    name="Top Layer",
                    kind=AltiumLayerType.COPPER,
                    altium_layer_number=1,
                ),
                BoardLayer(
                    id="bottom",
                    name="Bottom Layer",
                    kind=AltiumLayerType.COPPER,
                    altium_layer_number=32,
                ),
            ],
            copper_ordering=BoardCopperOrdering(ordered_layer_ids=["top", "bottom"]),
        ),
        nets=[AltiumNet(id="net-sig", name="SIG")],
        components=[
            AltiumComponent(
                id="u1",
                designator="U1",
                footprint="SOIC-8",
                x=0,
                y=0,
                rotation=0.0,
                layer=1,
            ),
            AltiumComponent(
                id="j1",
                designator="J1",
                footprint="HDR-1x1",
                x=0,
                y=0,
                rotation=0.0,
                layer=32,
            ),
        ],
        primitives=[
            AltiumPad(
                id="u1-pad-1",
                component_id="u1",
                name="1",
                x=0,
                y=0,
                top_size_x=100,
                top_size_y=100,
                layer=1,
                net_id="net-sig",
                shape=AltiumPadShape.ROUND,
            ),
            AltiumPad(
                id="j1-pad-1",
                component_id="j1",
                name="1",
                x=20,
                y=0,
                top_size_x=100,
                top_size_y=100,
                layer=32,
                shape=AltiumPadShape.ROUND,
            ),
            AltiumTrack(
                id="track-1",
                layer=1,
                net_id="net-sig",
                x1=0,
                y1=0,
                x2=10,
                y2=0,
                width=10,
            ),
            AltiumTrack(
                id="track-2",
                layer=32,
                net_id="net-sig",
                x1=10,
                y1=0,
                x2=20,
                y2=0,
                width=10,
            ),
            AltiumVia(
                id="via-1",
                x=10,
                y=0,
                net_id="net-sig",
                diameter=40,
                hole_size=20,
                start_layer=1,
                end_layer=32,
            ),
        ],
    )

    hl_pcb = convert_altium_pcb_il_to_hl(pcb)
    assert convert_pcb_to_netlist(hl_pcb).normalized() == (
        (
            "SIG",
            (
                ("pcb_pad", "j1", "J1", "1"),
                ("pcb_pad", "u1", "U1", "1"),
            ),
        ),
    )


def test_altium_pcb_il_to_hl_preserves_non_circular_pad_shapes() -> None:
    pcb = AltiumPcb(
        board=BoardConfig(
            id="board",
            layers=[
                BoardLayer(
                    id="top",
                    name="Top Layer",
                    kind=AltiumLayerType.COPPER,
                    altium_layer_number=1,
                )
            ],
            copper_ordering=BoardCopperOrdering(ordered_layer_ids=["top"]),
        ),
        components=[
            AltiumComponent(
                id="u1",
                designator="U1",
                footprint="PKG",
                x=0,
                y=0,
                rotation=0.0,
                layer=1,
            )
        ],
        primitives=[
            AltiumPad(
                id="rect-pad",
                component_id="u1",
                name="1",
                x=0,
                y=0,
                top_size_x=200,
                top_size_y=100,
                layer=1,
                shape=AltiumPadShape.RECT,
                rotation=90.0,
            ),
            AltiumPad(
                id="oct-pad",
                component_id="u1",
                name="2",
                x=500,
                y=0,
                top_size_x=200,
                top_size_y=100,
                layer=1,
                shape=AltiumPadShape.OCTAGONAL,
            ),
            AltiumPad(
                id="rr-pad",
                component_id="u1",
                name="3",
                x=1000,
                y=0,
                top_size_x=200,
                top_size_y=100,
                layer=1,
                shape=AltiumPadShape.ROUND_RECT,
            ),
        ],
    )

    hl_pcb = convert_altium_pcb_il_to_hl(pcb)
    assert isinstance(
        hl_pcb.collections[0].collections[0].geometries[0].shape,
        Rectangle,
    )
    assert isinstance(hl_pcb.collections[0].collections[1].geometries[0].shape, Polygon)
    assert isinstance(
        hl_pcb.collections[0].collections[2].geometries[0].shape,
        RoundedRectangle,
    )


def test_altium_schematic_il_to_hl_projects_into_expected_netlist() -> None:
    schematic = AltiumSchematic(
        id="sheet-top",
        components=[
            SchematicComponent(
                id="u1",
                lib_reference="U",
                design_item_id="IC",
                pins=[
                    SchematicPin(id="u1-1", name="VIN", designator="1", location=(0, 0))
                ],
                parameters=[
                    SchematicParameter(
                        id="u1-designator",
                        name="Designator",
                        text="U1",
                        is_designator=True,
                    )
                ],
            ),
            SchematicComponent(
                id="j1",
                lib_reference="J",
                design_item_id="CONN",
                pins=[
                    SchematicPin(
                        id="j1-1", name="SIG", designator="1", location=(20, 0)
                    )
                ],
                parameters=[
                    SchematicParameter(
                        id="j1-designator",
                        name="Designator",
                        text="J1",
                        is_designator=True,
                    )
                ],
            ),
        ],
        wires=[SchematicWire(id="wire-1", vertices=[(0, 0), (20, 0)])],
        net_labels=[SchematicNetLabel(id="net-1", text="SIG", location=(10, 0))],
    )

    hl_schematic = convert_altium_schematic_il_to_hl(schematic)
    assert convert_schematic_to_netlist(hl_schematic).normalized() == (
        (
            "SIG",
            (
                ("schematic_pin", "j1", "J1", "1"),
                ("schematic_pin", "u1", "U1", "1"),
            ),
        ),
    )


def test_altium_schematic_il_to_hl_uses_pin_endpoints_for_connectivity() -> None:
    schematic = AltiumSchematic(
        id="sheet-top",
        components=[
            SchematicComponent(
                id="u1",
                lib_reference="U",
                design_item_id="IC",
                pins=[
                    SchematicPin(
                        id="u1-1",
                        name="VIN",
                        designator="1",
                        location=(30_000, 0),
                        length=10_000,
                        orientation=2,
                    )
                ],
                parameters=[
                    SchematicParameter(
                        id="u1-designator",
                        name="Designator",
                        text="U1",
                        is_designator=True,
                    )
                ],
            ),
            SchematicComponent(
                id="j1",
                lib_reference="J",
                design_item_id="CONN",
                pins=[
                    SchematicPin(
                        id="j1-1",
                        name="SIG",
                        designator="1",
                        location=(20_000, 30_000),
                        length=10_000,
                        orientation=3,
                    )
                ],
                parameters=[
                    SchematicParameter(
                        id="j1-designator",
                        name="Designator",
                        text="J1",
                        is_designator=True,
                    )
                ],
            ),
        ],
        wires=[
            SchematicWire(
                id="wire-1",
                vertices=[(0, 0), (20_000, 0), (20_000, 20_000)],
            )
        ],
        net_labels=[SchematicNetLabel(id="net-1", text="SIG", location=(10_000, 0))],
    )

    hl_schematic = convert_altium_schematic_il_to_hl(schematic)
    assert convert_schematic_to_netlist(hl_schematic).normalized() == (
        (
            "SIG",
            (
                ("schematic_pin", "j1", "J1", "1"),
                ("schematic_pin", "u1", "U1", "1"),
            ),
        ),
    )


def test_altium_schematic_il_to_hl_projects_sheet_symbols_and_ports() -> None:
    schematic = AltiumSchematic(
        id="top.SchDoc",
        sheet_symbols=[
            SchematicSheetSymbol(
                id="sheet-symbol-1",
                location=(40_000, 30_000),
                x_size=15_000,
                y_size=5_000,
                file_name="child.SchDoc",
                sheet_name="Child",
                entries=[
                    SchematicSheetEntry(
                        id="sheet-entry-1",
                        name="IN",
                        side=0,
                        distance_from_top=1_000,
                    )
                ],
            )
        ],
        ports=[
            SchematicPort(
                id="port-1",
                location=(10_000, 20_000),
                name="IN",
                width=5_000,
                height=1_000,
            )
        ],
        wires=[
            SchematicWire(
                id="wire-1",
                vertices=[(15_000, 20_000), (20_000, 20_000)],
            )
        ],
    )

    hl_schematic = convert_altium_schematic_il_to_hl(schematic)
    top_sheet = hl_schematic.sheets[0]

    assert len(top_sheet.symbols) == 1
    assert top_sheet.symbols[0].kind == "sheet"
    assert top_sheet.symbols[0].pins[0].name == "IN"
    assert top_sheet.symbols[0].extra_properties["file_name"] == "child.SchDoc"
    assert len(top_sheet.pins) == 1
    assert top_sheet.pins[0].name == "IN"
    assert top_sheet.pins[0].location == (15_000.0, 20_000.0)


@pytest.mark.skipif(
    not _ALTIUM_SCHDOC_FIXTURE.exists(),
    reason="local AltiumSharp schematic fixture not available",
)
def test_real_altium_schematic_fixture_projects_to_connected_named_nets() -> None:
    schematic = convert_ll_to_il(SchDocCodec.read(_ALTIUM_SCHDOC_FIXTURE))

    hl_schematic = convert_altium_schematic_il_to_hl(schematic)
    normalized = convert_schematic_to_netlist(hl_schematic).normalized()

    assert len(normalized) < sum(
        len(symbol.pins) for symbol in hl_schematic.sheets[0].symbols
    )
    assert any(name == "GND" for name, _members in normalized)
    assert any(name == "5V" for name, _members in normalized)
    assert any(
        name == "VREG_SHDN" and ("schematic_pin", "component-63", "IC4", "3") in members
        for name, members in normalized
    )


@pytest.mark.skipif(
    not _ALTIUM_HIERARCHICAL_SCHDOC_FIXTURE.exists(),
    reason="local AltiumSharp hierarchical schematic fixture not available",
)
def test_real_altium_hierarchical_fixture_loads_multiple_sheets() -> None:
    hl_schematic = read_altium_schematic_to_hl(_ALTIUM_HIERARCHICAL_SCHDOC_FIXTURE)

    assert hl_schematic.top_sheet_id == "Overview.SchDoc"
    assert len(hl_schematic.sheets) >= 3
    top_sheet = hl_schematic.sheet_by_id["Overview.SchDoc"]
    child_ids = {
        symbol.child_sheet_id
        for symbol in top_sheet.symbols
        if symbol.kind == "sheet" and symbol.child_sheet_id is not None
    }
    assert any(child_id.endswith(":DAC") for child_id in child_ids)
    assert any(child_id.endswith(":Power Supply") for child_id in child_ids)
    dac_sheet_id = next(child_id for child_id in child_ids if child_id.endswith(":DAC"))
    dac_sheet = hl_schematic.sheet_by_id[dac_sheet_id]
    assert {
        "CS_5V",
        "SCLK_5V",
        "MOSI_5V",
        "LDAC_5V",
    }.issubset({pin.name for pin in dac_sheet.pins})


@pytest.mark.skipif(
    not _ARCHIMAJOR_SCHDOC_FIXTURE.exists(),
    reason="local Archimajor schematic fixture not available",
)
def test_archimajor_fixture_expands_repeated_child_sheet_instances() -> None:
    hl_schematic = read_altium_schematic_to_hl(_ARCHIMAJOR_SCHDOC_FIXTURE)

    assert hl_schematic.top_sheet_id == "Archimajor.SchDoc"
    assert len(hl_schematic.sheets) == 22
    top_sheet = hl_schematic.sheet_by_id["Archimajor.SchDoc"]
    repeat_symbols = {
        symbol.name: symbol
        for symbol in top_sheet.symbols
        if symbol.kind == "sheet"
        and symbol.extra_properties.get("repeat_range") is not None
    }
    assert repeat_symbols["Repeat(M,1,4)"].extra_properties[
        "repeated_child_sheet_ids"
    ] == [
        "Archimajor.SchDoc/51:Motors:1",
        "Archimajor.SchDoc/51:Motors:2",
        "Archimajor.SchDoc/51:Motors:3",
        "Archimajor.SchDoc/51:Motors:4",
    ]
    assert repeat_symbols["Repeat(M,5,8)"].extra_properties[
        "repeated_child_sheet_ids"
    ] == [
        "Archimajor.SchDoc/247:Motors:5",
        "Archimajor.SchDoc/247:Motors:6",
        "Archimajor.SchDoc/247:Motors:7",
        "Archimajor.SchDoc/247:Motors:8",
    ]
    assert repeat_symbols["Repeat(T,1,5)"].extra_properties[
        "repeated_child_sheet_ids"
    ] == [
        "Archimajor.SchDoc/288:Thermocouples:1",
        "Archimajor.SchDoc/288:Thermocouples:2",
        "Archimajor.SchDoc/288:Thermocouples:3",
        "Archimajor.SchDoc/288:Thermocouples:4",
        "Archimajor.SchDoc/288:Thermocouples:5",
    ]
    assert "Archimajor.SchDoc/107:EndStops" in hl_schematic.sheet_by_id


@pytest.mark.skipif(
    not _ARCHIMAJOR_SCHDOC_FIXTURE.exists(),
    reason="local Archimajor schematic fixture not available",
)
def test_archimajor_fixture_suffixes_repeated_local_refs_and_nets() -> None:
    normalized = convert_schematic_to_netlist(
        read_altium_schematic_to_hl(_ARCHIMAJOR_SCHDOC_FIXTURE)
    ).normalized()
    net_members = {name: members for name, members in normalized}

    assert "CA1A" in net_members
    assert "CA1H" in net_members
    assert "ENCAA" in net_members
    assert "ENCAH" in net_members
    assert "DIR1" in net_members
    assert "DIR8" in net_members
    assert "CA1" not in net_members
    assert (
        "schematic_pin",
        "Archimajor.SchDoc/51:Motors:1:component-57",
        "U15A",
        "42",
    ) in net_members["CA1A"]
    assert (
        "schematic_pin",
        "Archimajor.SchDoc/247:Motors:8:component-57",
        "U15H",
        "42",
    ) in net_members["CA1H"]


def test_connected_entry_name_map_only_renames_generic_entries() -> None:
    atmega = SchematicSheetSymbol(
        id="atmega",
        location=(41_000_000, 51_000_000),
        x_size=13_000_000,
        y_size=30_000_000,
        sheet_name="atmega328p",
        file_name="atmega328p.SchDoc",
        entries=[
            SchematicSheetEntry(
                id="atmega-neopixels",
                name="NEOPIXELS_OUT",
                side=1,
                distance_from_top=2_700_000,
            ),
            SchematicSheetEntry(
                id="atmega-ss",
                name="SS_SIGNAL",
                side=0,
                distance_from_top=2_600_000,
            ),
            SchematicSheetEntry(
                id="atmega-cs",
                name="SPI_CS_n",
                side=1,
                distance_from_top=200_000,
            ),
        ],
    )
    start_stop = SchematicSheetSymbol(
        id="start-stop",
        location=(30_000_000, 15_000_000),
        x_size=10_000_000,
        y_size=7_000_000,
        sheet_name="startStop",
        file_name="startStop.SchDoc",
        entries=[
            SchematicSheetEntry(
                id="start-stop-signal",
                name="SIGNAL",
                side=1,
                distance_from_top=200_000,
            )
        ],
    )
    shift_lights = SchematicSheetSymbol(
        id="shift-lights",
        location=(47_000_000, 15_000_000),
        x_size=9_000_000,
        y_size=7_000_000,
        sheet_name="shiftLights",
        file_name="shiftLights.SchDoc",
        entries=[
            SchematicSheetEntry(
                id="shift-lights-signal",
                name="SIGNAL",
                side=1,
                distance_from_top=200_000,
            )
        ],
    )
    mcp2515 = SchematicSheetSymbol(
        id="mcp2515",
        location=(56_000_000, 50_000_000),
        x_size=10_000_000,
        y_size=10_000_000,
        sheet_name="MCP2515",
        file_name="MCP2515.SchDoc",
        entries=[
            SchematicSheetEntry(
                id="mcp2515-cs",
                name="CS",
                side=0,
                distance_from_top=100_000,
            )
        ],
    )
    doc = AltiumSchematic(
        sheet_symbols=[atmega, start_stop, shift_lights, mcp2515],
        wires=[
            SchematicWire(
                id="wire-ss",
                vertices=[
                    (41_000_000, 25_000_000),
                    (40_000_000, 25_000_000),
                    (40_000_000, 13_000_000),
                ],
            ),
            SchematicWire(
                id="wire-neopixels",
                vertices=[
                    (54_000_000, 24_000_000),
                    (60_000_000, 24_000_000),
                    (60_000_000, 13_000_000),
                    (56_000_000, 13_000_000),
                ],
            ),
            SchematicWire(
                id="wire-cs",
                vertices=[(54_000_000, 49_000_000), (56_000_000, 49_000_000)],
            ),
        ],
    )

    assert _connected_entry_name_map(doc, atmega) == {}
    assert _connected_entry_name_map(doc, start_stop) == {"SIGNAL": "SS_SIGNAL"}
    assert _connected_entry_name_map(doc, shift_lights) == {"SIGNAL": "NEOPIXELS_OUT"}
    assert _connected_entry_name_map(doc, mcp2515) == {"CS": "SPI_CS_n"}


def test_altium_il_to_hl_strips_control_chars_from_designators() -> None:
    il_schematic = AltiumSchematic(
        components=[
            SchematicComponent(
                id="component-1",
                pins=[
                    SchematicPin(
                        id="pin-1",
                        name="1",
                        designator="1",
                        location=(2_000_000, 1_000_000),
                        length=1_000_000,
                        orientation=2,
                    )
                ],
                parameters=[
                    SchematicParameter(
                        id="param-1",
                        name="Designator",
                        text="\x08C5",
                        is_designator=True,
                    )
                ],
            )
        ],
        net_labels=[
            SchematicNetLabel(
                id="label-1",
                text="SIG",
                location=(1_000_000, 1_000_000),
            )
        ],
        wires=[
            SchematicWire(
                id="wire-1",
                vertices=[(1_000_000, 1_000_000), (2_000_000, 1_000_000)],
            )
        ],
    )

    hl_schematic = convert_altium_schematic_il_to_hl(il_schematic)
    assert convert_schematic_to_netlist(hl_schematic).normalized() == (
        (
            "SIG",
            (("schematic_pin", "component-1", "C5", "1"),),
        ),
    )


@pytest.mark.skipif(
    not _SIMPLE_DASH_SCHDOC_FIXTURE.exists() or not _SIMPLE_DASH_NET_FIXTURE.exists(),
    reason="local simple-dash Altium fixture pair not available",
)
def test_simple_dash_fixture_matches_reference_connectivity() -> None:
    actual = convert_schematic_to_netlist(
        read_altium_schematic_to_hl(_SIMPLE_DASH_SCHDOC_FIXTURE)
    )
    reference = Netlist.decode(_SIMPLE_DASH_NET_FIXTURE)

    diff = compare_netlists(reference, actual)

    assert diff.equivalent_by_terminals, diff.format_report()


@pytest.mark.skipif(
    not _ESOCORE_ROOT_SCHDOC_FIXTURE.exists(),
    reason="local EsoCore root-page fixture not available",
)
def test_esocore_root_page_reports_unresolved_child_sheets() -> None:
    hl_schematic = read_altium_schematic_to_hl(_ESOCORE_ROOT_SCHDOC_FIXTURE)

    assert hl_schematic.extra_properties["unresolved_child_sheets"] == [
        "Root_page.SchDoc:U_Tag -> Tag.SchDoc"
    ]


def test_kicad_pcb_il_to_hl_projects_into_expected_netlist() -> None:
    footprint_u1 = kicad.pcb.Footprint(
        uuid="u1",
        name="U",
        layer="F.Cu",
        at=_pcb_xyr(0.0, 0.0, 0.0),
        propertys=[
            _pcb_property("Reference", "U1"),
            _pcb_property("Value", "IC"),
        ],
        pads=[
            kicad.pcb.Pad(
                uuid="u1-pad-1",
                name="1",
                type="smd",
                shape="circle",
                at=_pcb_xyr(0.0, 0.0, 0.0),
                size=kicad.pcb.Wh(w=1.0, h=1.0),
                layers=["F.Cu"],
                net=kicad.pcb.Net(number=1, name="SIG"),
            )
        ],
    )
    footprint_j1 = kicad.pcb.Footprint(
        uuid="j1",
        name="J",
        layer="B.Cu",
        at=_pcb_xyr(20.0, 0.0, 0.0),
        propertys=[
            _pcb_property("Reference", "J1"),
            _pcb_property("Value", "CONN"),
        ],
        pads=[
            kicad.pcb.Pad(
                uuid="j1-pad-1",
                name="1",
                type="smd",
                shape="circle",
                at=_pcb_xyr(0.0, 0.0, 0.0),
                size=kicad.pcb.Wh(w=1.0, h=1.0),
                layers=["B.Cu"],
                net=kicad.pcb.Net(number=0, name=""),
            )
        ],
    )
    board = kicad.pcb.KicadPcb(
        version=20250114,
        generator="test",
        generator_version="1",
        nets=[
            kicad.pcb.Net(number=0, name=""),
            kicad.pcb.Net(number=1, name="SIG"),
        ],
        footprints=[footprint_u1, footprint_j1],
        segments=[
            kicad.pcb.Segment(
                layer="F.Cu",
                net=1,
                start=_pcb_xy(0.0, 0.0),
                end=_pcb_xy(10.0, 0.0),
                width=0.25,
                uuid=None,
            ),
            kicad.pcb.Segment(
                layer="B.Cu",
                net=1,
                start=_pcb_xy(10.0, 0.0),
                end=_pcb_xy(20.0, 0.0),
                width=0.25,
                uuid=None,
            ),
        ],
        vias=[
            kicad.pcb.Via(
                at=_pcb_xy(10.0, 0.0),
                size=1.0,
                drill=0.5,
                layers=["F.Cu", "B.Cu"],
                net=1,
                uuid=None,
            )
        ],
        arcs=[],
        zones=[],
    )

    hl_pcb = convert_kicad_pcb_il_to_hl(board)
    assert convert_pcb_to_netlist(hl_pcb).normalized() == (
        (
            "SIG",
            (
                ("pcb_pad", "j1", "J1", "1"),
                ("pcb_pad", "u1", "U1", "1"),
            ),
        ),
    )


def test_kicad_pcb_il_to_hl_preserves_non_circular_pad_shapes() -> None:
    footprint = kicad.pcb.Footprint(
        uuid="u1",
        name="U",
        layer="F.Cu",
        at=_pcb_xyr(0.0, 0.0, 45.0),
        propertys=[
            _pcb_property("Reference", "U1"),
            _pcb_property("Value", "IC"),
        ],
        pads=[
            kicad.pcb.Pad(
                uuid="rect-pad",
                name="1",
                type="smd",
                shape="rect",
                at=_pcb_xyr(0.0, 0.0, 0.0),
                size=kicad.pcb.Wh(w=2.0, h=1.0),
                layers=["F.Cu"],
                net=kicad.pcb.Net(number=1, name="SIG"),
            ),
            kicad.pcb.Pad(
                uuid="oval-pad",
                name="2",
                type="smd",
                shape="oval",
                at=_pcb_xyr(5.0, 0.0, 0.0),
                size=kicad.pcb.Wh(w=2.0, h=1.0),
                layers=["F.Cu"],
                net=kicad.pcb.Net(number=1, name="SIG"),
            ),
            kicad.pcb.Pad(
                uuid="rr-pad",
                name="3",
                type="smd",
                shape="roundrect",
                at=_pcb_xyr(10.0, 0.0, 0.0),
                size=kicad.pcb.Wh(w=2.0, h=1.0),
                roundrect_rratio=0.2,
                layers=["F.Cu"],
                net=kicad.pcb.Net(number=1, name="SIG"),
            ),
        ],
    )
    board = kicad.pcb.KicadPcb(
        version=20250114,
        generator="test",
        generator_version="1",
        nets=[kicad.pcb.Net(number=1, name="SIG")],
        footprints=[footprint],
    )

    hl_pcb = convert_kicad_pcb_il_to_hl(board)
    pad_geometries = hl_pcb.collections[0].collections
    assert isinstance(pad_geometries[0].geometries[0].shape, Rectangle)
    assert isinstance(pad_geometries[1].geometries[0].shape, Obround)
    assert isinstance(pad_geometries[2].geometries[0].shape, RoundedRectangle)
    assert hl_pcb.collections[0].collections[0].geometries[0].shape.rotation_deg == 45.0


def test_kicad_schematic_il_to_hl_projects_into_expected_netlist() -> None:
    lib_symbol = kicad.schematic.Symbol(
        name="Device:R",
        power=False,
        symbols=[
            kicad.schematic.SymbolUnit(
                name="Device:R_1_1",
                pins=[
                    kicad.schematic.SymbolPin(
                        at=_schematic_xyr(0.0, 0.0, 0.0),
                        length=2.54,
                        type="passive",
                        style="line",
                        name=kicad.schematic.PinName(name="A", effects=_effects()),
                        number=kicad.schematic.PinNumber(
                            number="1", effects=_effects()
                        ),
                    ),
                    kicad.schematic.SymbolPin(
                        at=_schematic_xyr(20.0, 0.0, 0.0),
                        length=2.54,
                        type="passive",
                        style="line",
                        name=kicad.schematic.PinName(name="B", effects=_effects()),
                        number=kicad.schematic.PinNumber(
                            number="2", effects=_effects()
                        ),
                    ),
                ],
            )
        ],
        propertys=[],
    )
    schematic = kicad.schematic.KicadSch(
        version=20250114,
        generator="test",
        paper="A4",
        uuid="root",
        lib_symbols=kicad.schematic.LibSymbols(symbols=[lib_symbol]),
        title_block=kicad.schematic.TitleBlock(
            title=None,
            date=None,
            rev=None,
            company=None,
        ),
        wires=[
            kicad.schematic.Wire(
                uuid="wire-1",
                pts=kicad.schematic.Pts(xys=[_pcb_xy(0.0, 0.0), _pcb_xy(20.0, 0.0)]),
                stroke=kicad.schematic.Stroke(
                    width=0.15,
                    type="solid",
                    color=kicad.schematic.Color(r=0, g=0, b=0, a=0),
                ),
            )
        ],
        junctions=[],
        labels=[
            kicad.schematic.Label(
                uuid="label-1",
                text="SIG",
                at=_schematic_xyr(10.0, 0.0, 0.0),
                effects=_effects(),
            )
        ],
        global_labels=[],
        sheets=[],
        symbols=[
            kicad.schematic.SymbolInstance(
                uuid="r1",
                lib_id="Device:R",
                at=_schematic_xyr(0.0, 0.0, 0.0),
                unit=1,
                in_bom=True,
                on_board=True,
                fields_autoplaced=False,
                pins=[],
                convert=None,
                propertys=[
                    _schematic_property("Reference", "R1"),
                    _schematic_property("Value", "10k"),
                ],
            )
        ],
        texts=[],
        no_connects=[],
        buss=[],
        bus_entrys=[],
    )

    hl_schematic = convert_kicad_schematic_il_to_hl(schematic)
    assert convert_schematic_to_netlist(hl_schematic).normalized() == (
        (
            "/SIG",
            (
                ("schematic_pin", "r1", "R1", "1"),
                ("schematic_pin", "r1", "R1", "2"),
            ),
        ),
    )


def test_kicad_schematic_il_to_hl_includes_common_unit_zero_pins() -> None:
    lib_symbol = kicad.schematic.Symbol(
        name="Connector:Split",
        power=False,
        symbols=[
            kicad.schematic.SymbolUnit(
                name="Connector:Split_0_0",
                pins=[
                    kicad.schematic.SymbolPin(
                        at=_schematic_xyr(0.0, 0.0, 0.0),
                        length=2.54,
                        type="passive",
                        style="line",
                        name=kicad.schematic.PinName(name="COMMON", effects=_effects()),
                        number=kicad.schematic.PinNumber(
                            number="10", effects=_effects()
                        ),
                    )
                ],
            ),
            kicad.schematic.SymbolUnit(
                name="Connector:Split_1_1",
                pins=[
                    kicad.schematic.SymbolPin(
                        at=_schematic_xyr(20.0, 0.0, 0.0),
                        length=2.54,
                        type="passive",
                        style="line",
                        name=kicad.schematic.PinName(name="UNIT1", effects=_effects()),
                        number=kicad.schematic.PinNumber(
                            number="15", effects=_effects()
                        ),
                    )
                ],
            ),
        ],
        propertys=[],
    )
    schematic = kicad.schematic.KicadSch(
        version=20250114,
        generator="test",
        paper="A4",
        uuid="root",
        lib_symbols=kicad.schematic.LibSymbols(symbols=[lib_symbol]),
        title_block=kicad.schematic.TitleBlock(
            title=None,
            date=None,
            rev=None,
            company=None,
        ),
        wires=[],
        junctions=[],
        labels=[],
        global_labels=[],
        sheets=[],
        symbols=[
            kicad.schematic.SymbolInstance(
                uuid="j1",
                lib_id="Connector:Split",
                at=_schematic_xyr(100.0, 50.0, 0.0),
                unit=1,
                in_bom=True,
                on_board=True,
                fields_autoplaced=False,
                pins=[],
                convert=None,
                propertys=[
                    _schematic_property("Reference", "J1"),
                    _schematic_property("Value", "Split"),
                ],
            )
        ],
        texts=[],
        no_connects=[],
        buss=[],
        bus_entrys=[],
    )

    hl_schematic = convert_kicad_schematic_il_to_hl(schematic)
    pins = {(pin.name, pin.location) for pin in hl_schematic.sheets[0].symbols[0].pins}
    assert pins == {
        ("10", (100.0, 50.0)),
        ("15", (120.0, 50.0)),
    }


def test_raw_kicad_schematic_tracks_instance_specific_references(
    tmp_path: Path,
) -> None:
    schematic_path = tmp_path / "child.kicad_sch"
    schematic_path.write_text(
        """
(kicad_sch
    (version 20250114)
    (generator "eeschema")
    (uuid "child-root")
    (paper "A4")
    (symbol
        (lib_id "Device:R")
        (at 0 0 0)
        (unit 1)
        (in_bom yes)
        (on_board yes)
        (uuid "sym-1")
        (property "Reference" "R7"
            (at 0 0 0)
            (effects (font (size 1 1)))
        )
        (property "Value" "10k"
            (at 0 0 0)
            (effects (font (size 1 1)))
        )
        (instances
            (project ""
                (path "/root-uuid/child-a"
                    (reference "R60")
                    (unit 1)
                )
                (path "/root-uuid/child-b"
                    (reference "R7")
                    (unit 1)
                )
            )
        )
    )
)
""".strip(),
        encoding="utf-8",
    )

    raw = read_raw_kicad_schematic(schematic_path)
    raw_instance = raw.symbols_by_uuid["sym-1"]
    assert raw_instance.references_by_path == {
        "/child-a/": "R60",
        "/child-b/": "R7",
    }
    assert _reference_for_sheet(raw_instance, "/child-a/", "R7") == "R60"
    assert _reference_for_sheet(raw_instance, "/child-b/", "R7") == "R7"
