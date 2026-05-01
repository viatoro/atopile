"""Auto-detecting EDA file → HL model loaders.

Usage:
    from faebryk.libs.eda.hl.convert.eda_file_hl import PCB, Schematic, Netlist

    pcb_hl = PCB.decode(Path("board.PcbDoc"))       # or .kicad_pcb
    sch_hl = Schematic.decode(Path("sheet.SchDoc"))  # or .kicad_sch
    net_hl = Netlist.decode(Path("board.net"))       # Cadence netlist
"""

from __future__ import annotations

from pathlib import Path

from faebryk.libs.eda.hl.models import netlist as netlist_models
from faebryk.libs.eda.hl.models import pcb as pcb_models
from faebryk.libs.eda.hl.models import schematic as sch_models

_ALTIUM_PCB_SUFFIXES = {".pcbdoc"}
_KICAD_PCB_SUFFIXES = {".kicad_pcb"}
_ALTIUM_SCH_SUFFIXES = {".schdoc"}
_KICAD_SCH_SUFFIXES = {".kicad_sch"}
_CADENCE_NETLIST_SUFFIXES = {".net", ".cdl", ".netlist"}


class PCB:
    @staticmethod
    def decode(path: Path) -> pcb_models.PCB:
        suffix = path.suffix.lower()

        if suffix in _ALTIUM_PCB_SUFFIXES:
            from faebryk.libs.eda.altium.convert.pcb.file_ll import PcbDocCodec
            from faebryk.libs.eda.altium.convert.pcb.il_hl import (
                convert_pcb_il_to_hl,
            )
            from faebryk.libs.eda.altium.convert.pcb.il_ll import convert_ll_to_il

            ll_doc = PcbDocCodec.read(path)
            il_doc = convert_ll_to_il(ll_doc)
            return convert_pcb_il_to_hl(il_doc)

        if suffix in _KICAD_PCB_SUFFIXES:
            from faebryk.libs.eda.kicad.convert.pcb.il_hl import (
                convert_pcb_il_to_hl,
            )
            from faebryk.libs.kicad.fileformats import kicad

            pcb_file = kicad.loads(kicad.pcb.PcbFile, path)
            return convert_pcb_il_to_hl(pcb_file.kicad_pcb)

        expected = ", ".join(sorted(_ALTIUM_PCB_SUFFIXES | _KICAD_PCB_SUFFIXES))
        raise ValueError(
            f"Unsupported PCB file type: {path.suffix} (expected {expected})"
        )


class Schematic:
    @staticmethod
    def decode(path: Path) -> sch_models.Schematic:
        suffix = path.suffix.lower()

        if suffix in _ALTIUM_SCH_SUFFIXES:
            from faebryk.libs.eda.altium.convert.schematic.il_hl import (
                read_altium_schematic_to_hl,
            )

            return read_altium_schematic_to_hl(path)

        if suffix in _KICAD_SCH_SUFFIXES:
            from faebryk.libs.eda.kicad.convert.schematic.il_hl import (
                read_kicad_schematic_to_hl,
            )

            return read_kicad_schematic_to_hl(path)

        expected = ", ".join(sorted(_ALTIUM_SCH_SUFFIXES | _KICAD_SCH_SUFFIXES))
        raise ValueError(
            f"Unsupported schematic file type: {path.suffix} (expected {expected})"
        )


class Netlist:
    @staticmethod
    def decode(path: Path) -> netlist_models.Netlist:
        suffix = path.suffix.lower()

        if suffix in _CADENCE_NETLIST_SUFFIXES:
            from faebryk.libs.eda.cadence.convert.netlist.file_ll import NetlistCodec
            from faebryk.libs.eda.cadence.convert.netlist.ll_hl import convert_ll_to_hl

            ll_netlist = NetlistCodec.read(path)
            return convert_ll_to_hl(ll_netlist)

        expected = ", ".join(sorted(_CADENCE_NETLIST_SUFFIXES))
        raise ValueError(
            f"Unsupported netlist file type: {path.suffix} (expected {expected})"
        )
