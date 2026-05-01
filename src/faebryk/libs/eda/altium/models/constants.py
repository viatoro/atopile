# This file is part of the faebryk project
# SPDX-License-Identifier: MIT

"""Layer mapping and coordinate conversion for Altium PcbDoc export."""


def mm_to_altium(mm: float) -> int:
    """Convert mm to Altium internal units (1/10000 mil)."""
    return round(mm * 10_000 / 0.0254)


def altium_to_mm(units: int) -> float:
    """Convert Altium internal units to mm."""
    return units * 0.0254 / 10_000


def to_mil(altium_units: int) -> str:
    """Convert Altium internal units (1/10000 mil) to a mil string for properties.

    KiCad's ReadKicadUnit REQUIRES the "mil" suffix — values without it
    are silently read as 0.  The conversion is: mils = internal / 10000.
    """
    mils = altium_units / 10000.0
    return f"{mils}mil"


# KiCad layer name -> Altium V6 layer number
LAYER_MAP: dict[str, int] = {
    # Copper layers
    "F.Cu": 1,
    "In1.Cu": 2,
    "In2.Cu": 3,
    "In3.Cu": 4,
    "In4.Cu": 5,
    "In5.Cu": 6,
    "In6.Cu": 7,
    "In7.Cu": 8,
    "In8.Cu": 9,
    "In9.Cu": 10,
    "In10.Cu": 11,
    "In11.Cu": 12,
    "In12.Cu": 13,
    "In13.Cu": 14,
    "In14.Cu": 15,
    "In15.Cu": 16,
    "In16.Cu": 17,
    "In17.Cu": 18,
    "In18.Cu": 19,
    "In19.Cu": 20,
    "In20.Cu": 21,
    "In21.Cu": 22,
    "In22.Cu": 23,
    "In23.Cu": 24,
    "In24.Cu": 25,
    "In25.Cu": 26,
    "In26.Cu": 27,
    "In27.Cu": 28,
    "In28.Cu": 29,
    "In29.Cu": 30,
    "In30.Cu": 31,
    "B.Cu": 32,
    # Overlay (silkscreen)
    "F.SilkS": 33,
    "B.SilkS": 34,
    # Paste
    "F.Paste": 35,
    "B.Paste": 36,
    # Solder mask
    "F.Mask": 37,
    "B.Mask": 38,
    # Mechanical layers
    "Edge.Cuts": 57,
    "F.Fab": 58,
    "B.Fab": 59,
    "F.CrtYd": 60,
    "B.CrtYd": 61,
}

# Full Altium layer names (matching Altium Designer defaults / reference files).
# These are the LAYER{i}NAME values in Board6 properties.
# Layer numbering follows the ALTIUM_LAYER enum in KiCad's altium_parser_pcb.h.
ALTIUM_LAYER_NAMES: dict[int, str] = {
    # Copper (1-32)
    1: "Top Layer",
    **{i: f"Mid-Layer {i - 1}" for i in range(2, 32)},
    32: "Bottom Layer",
    # Overlay
    33: "Top Overlay",
    34: "Bottom Overlay",
    # Paste
    35: "Top Paste",
    36: "Bottom Paste",
    # Solder mask
    37: "Top Solder",
    38: "Bottom Solder",
    # Internal planes (39-54)
    **{i: f"Internal Plane {i - 38}" for i in range(39, 55)},
    # Special
    55: "Drill Guide",
    56: "Keep-Out Layer",
    # Mechanical (57-72)
    **{i: f"Mechanical {i - 56}" for i in range(57, 73)},
    # System
    73: "Drill Drawing",
    74: "Multi-Layer",
    75: "Connections",
    76: "Background",
    77: "DRC Error Markers",
    78: "Selections",
    79: "Visible Grid 1",
    80: "Visible Grid 2",
    81: "Pad Holes",
    82: "Via Holes",
}

# Short layer names used in component LAYER properties.
# KiCad's altium_layer_from_name() expects these exact strings.
ALTIUM_LAYER_SHORT_NAMES: dict[int, str] = {
    1: "TOP",
    **{i: f"MID{i - 1}" for i in range(2, 32)},
    32: "BOTTOM",
    33: "TOPOVERLAY",
    34: "BOTTOMOVERLAY",
    35: "TOPPASTE",
    36: "BOTTOMPASTE",
    37: "TOPSOLDER",
    38: "BOTTOMSOLDER",
    **{i: f"PLANE{i - 38}" for i in range(39, 55)},
    55: "DRILLGUIDE",
    56: "KEEPOUT",
    **{i: f"MECHANICAL{i - 56}" for i in range(57, 73)},
    73: "DRILLDRAWING",
    74: "MULTILAYER",
}

# Multi-layer constant for through-hole pads/vias
MULTI_LAYER = 74

# Total number of V6 layers in a standard Altium Board6 stackup
TOTAL_LAYER_COUNT = 82

# Altium pad shapes
PAD_SHAPE_ROUND = 1
PAD_SHAPE_RECT = 2
PAD_SHAPE_OCTAGONAL = 3

# KiCad pad shape -> Altium pad shape
PAD_SHAPE_MAP: dict[str, int] = {
    "circle": PAD_SHAPE_ROUND,
    "rect": PAD_SHAPE_RECT,
    "oval": PAD_SHAPE_ROUND,  # Altium doesn't have a direct oval; use round
    "roundrect": PAD_SHAPE_RECT,  # approximate
    "trapezoid": PAD_SHAPE_RECT,  # approximate
    "chamfered_rect": PAD_SHAPE_OCTAGONAL,  # approximate
    "custom": PAD_SHAPE_ROUND,
}
