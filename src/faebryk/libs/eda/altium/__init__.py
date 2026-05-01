# This file is part of the faebryk project
# SPDX-License-Identifier: MIT

"""Altium IL exports and PcbDoc export surface.

Public API:
    export_altium_pcb(kicad_pcb, output_path) -> None
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from faebryk.libs.eda.altium.convert.pcb.file_ll import PcbDocCodec
from faebryk.libs.eda.altium.convert.pcb.il_kicad import (
    convert_altium_to_kicad,
    convert_kicad_to_altium,
    convert_pcb,
)
from faebryk.libs.eda.altium.convert.pcb.il_ll import (
    convert_il_to_ll,
    convert_ll_to_il,
)
from faebryk.libs.eda.altium.models.pcb.il import (
    AltiumArc,
    AltiumClass,
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
    AltiumPadShape,
    AltiumPcb,
    AltiumPolygonConnectStyle,
    AltiumPrimitiveKind,
    AltiumRegion,
    AltiumRule,
    AltiumRuleClearance,
    AltiumRuleHoleSize,
    AltiumRuleKind,
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
    BoardOutlineSegment,
    LayerReference,
    SourceMetadata,
)

if TYPE_CHECKING:  # pragma: no cover
    pass

logger = logging.getLogger(__name__)


def export_altium_pcb(kicad_pcb, output_path: Path) -> None:
    """Export a KicadPcb object to an Altium `.PcbDoc` file.

    Args:
        kicad_pcb: A KicadPcb object (from the Zig/Python PCB model).
        output_path: Path to write the .PcbDoc file.
    """
    logger.info("Converting KiCad PCB to Altium PcbDoc format")
    il_doc = convert_pcb(kicad_pcb)
    ll_doc = convert_il_to_ll(il_doc)

    if warnings := getattr(ll_doc, "translation_warnings", None):
        logger.warning("Altium IL→LL translation warnings (%d)", len(warnings))
        for warning in warnings:
            logger.debug("  - %s", warning)

    PcbDocCodec.write(ll_doc, output_path)
    logger.info("Wrote Altium PcbDoc to %s", output_path)


__all__ = [
    "AltiumArc",
    "AltiumClass",
    "AltiumClassComponent",
    "AltiumClassLayer",
    "AltiumClassNet",
    "AltiumClassPad",
    "BoardCopperOrdering",
    "AltiumFill",
    "AltiumLayerType",
    "AltiumPcb",
    "AltiumPolygonConnectStyle",
    "AltiumPad",
    "AltiumPadShape",
    "AltiumPrimitiveKind",
    "AltiumRegion",
    "AltiumRule",
    "AltiumRuleClearance",
    "AltiumRuleHoleSize",
    "AltiumRuleKind",
    "AltiumRulePasteMaskExpansion",
    "AltiumRulePolygonConnectStyle",
    "AltiumRuleRoutingVias",
    "AltiumRuleSolderMaskExpansion",
    "AltiumRuleWidth",
    "AltiumText",
    "AltiumTrack",
    "AltiumVia",
    "BoardConfig",
    "BoardLayer",
    "BoardOutlineSegment",
    "AltiumClassKind",
    "AltiumNet",
    "AltiumComponent",
    "SourceMetadata",
    "LayerReference",
    "convert_pcb",
    "convert_altium_to_kicad",
    "convert_kicad_to_altium",
    "convert_il_to_ll",
    "convert_ll_to_il",
    "export_altium_pcb",
]
