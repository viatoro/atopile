"""Cadence/OrCAD reference netlist importer tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from faebryk.libs.eda.cadence.convert.netlist.file_ll import NetlistCodec
from faebryk.libs.eda.cadence.convert.netlist.ll_hl import convert_ll_to_hl

_ARCHIMAJOR_NET_FIXTURE = Path(
    "/home/ip/workspace/atopile_altium_pcb/.local/altium_demos/Mirror-test/"
    "Z__altium_Mirror-test_Archimajor.NET"
)
_LUMINOX_NET_FIXTURE = Path(
    "/home/ip/workspace/atopile_altium_pcb/.local/altium_demos/"
    "hardware-luminox_to_sdi12/main.NET"
)


def test_orcad_pcbii_netlist_parser_handles_multiword_headers() -> None:
    payload = """\
( {OrCAD PCB II Netlist Format}
 ( 00000009 HDR2X3 ICSP ICSP
  ( 1 GND )
  ( 2 +5V )
 )
 ( 00000401 70543-01 PIN-IN-PASTE J14 CONN 2PIN VERT 0.1" SHRD LOCKING PIP
  ( 1 GND )
  ( 2 NetJ14_2 )
 )
)
"""

    ll_netlist = NetlistCodec.decode(payload)

    assert ll_netlist.format_name == "OrCAD PCB II Netlist Format"
    assert len(ll_netlist.components) == 2
    assert ll_netlist.components[0].footprint == "HDR2X3"
    assert ll_netlist.components[0].refdes == "ICSP"
    assert ll_netlist.components[0].value == "ICSP"
    assert ll_netlist.components[1].footprint == "70543-01 PIN-IN-PASTE"
    assert ll_netlist.components[1].refdes == "J14"
    assert ll_netlist.components[1].value == 'CONN 2PIN VERT 0.1" SHRD LOCKING PIP'
    assert ll_netlist.components[1].pins[1].net_name == "NetJ14_2"


def test_orcad_pcbii_netlist_parser_prefers_designator_over_short_package_names() -> (
    None
):
    payload = """\
( {OrCAD PCB II Netlist Format}
 ( 00000001 TP 1V2_ TP
  ( 1 1V2 )
 )
 ( 00000002 PAD07 12VH Test point
  ( 1 ?1 )
 )
)
"""

    ll_netlist = NetlistCodec.decode(payload)

    assert ll_netlist.components[0].footprint == "TP"
    assert ll_netlist.components[0].refdes == "1V2_"
    assert ll_netlist.components[0].value == "TP"
    assert ll_netlist.components[1].footprint == "PAD07"
    assert ll_netlist.components[1].refdes == "12VH"
    assert ll_netlist.components[1].value == "Test point"


@pytest.mark.skipif(
    not _ARCHIMAJOR_NET_FIXTURE.exists(),
    reason="local Archimajor reference netlist fixture not available",
)
def test_archimajor_reference_netlist_parses_real_component_blocks() -> None:
    ll_netlist = NetlistCodec.read(_ARCHIMAJOR_NET_FIXTURE)

    assert ll_netlist.format_name == "OrCAD PCB II Netlist Format"
    assert len(ll_netlist.components) == 975
    assert sum(len(component.pins) for component in ll_netlist.components) == 2829

    component_by_refdes = {
        component.refdes: component for component in ll_netlist.components
    }
    assert component_by_refdes["C1"].footprint == "C0603"
    assert component_by_refdes["C1"].value == "100nF"
    assert [pin.net_name for pin in component_by_refdes["C1"].pins] == [
        "NetC1_1",
        "NetC1_2",
    ]
    assert component_by_refdes["J14"].footprint == "70543-01 PIN-IN-PASTE"
    assert (
        component_by_refdes["Q7A"].footprint == "SO8FL-Dual (DFN8 5x6, 1.26P Dual Flag)"
    )


@pytest.mark.skipif(
    not _LUMINOX_NET_FIXTURE.exists(),
    reason="local luminox reference netlist fixture not available",
)
def test_luminox_reference_netlist_preserves_refdes_when_value_matches() -> None:
    ll_netlist = NetlistCodec.read(_LUMINOX_NET_FIXTURE)

    component_by_refdes = {
        component.refdes: component for component in ll_netlist.components
    }
    assert component_by_refdes["ICSP"].footprint == "HDR2X3"
    assert component_by_refdes["ICSP"].value == "ICSP"
    assert len(component_by_refdes["ICSP"].pins) == 6


@pytest.mark.skipif(
    not _ARCHIMAJOR_NET_FIXTURE.exists(),
    reason="local Archimajor reference netlist fixture not available",
)
def test_archimajor_reference_netlist_projects_to_hl_terminal_sets() -> None:
    hl_netlist = convert_ll_to_hl(NetlistCodec.read(_ARCHIMAJOR_NET_FIXTURE))
    net_by_name = {net.name: net for net in hl_netlist.nets}

    assert len(hl_netlist.nets) == 685
    assert len(net_by_name["GND"].terminals) == 496
    assert len(net_by_name["3.3VCC"].terminals) == 163
    assert len(net_by_name["VPWR_IN"].terminals) == 34
