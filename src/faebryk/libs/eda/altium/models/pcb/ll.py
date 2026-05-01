# This file is part of the faebryk project
# SPDX-License-Identifier: MIT

"""Dataclasses for low-level Altium PcbDoc record types."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AltiumNet:
    index: int
    name: str


@dataclass
class AltiumComponent:
    index: int
    designator: str
    footprint_name: str
    x: int  # Altium internal units
    y: int  # Altium internal units
    rotation: float  # degrees
    layer: int  # Altium layer number
    name_on: bool = True  # show designator text on silk
    comment_on: bool = False  # show comment text on silk


@dataclass
class AltiumPad:
    component: int  # component index (-1 for free pad)
    name: str
    x: int
    y: int
    top_size_x: int
    top_size_y: int
    mid_size_x: int
    mid_size_y: int
    bot_size_x: int
    bot_size_y: int
    hole_size: int  # for slots: minor axis (width)
    shape: int  # 1=round, 2=rect, 3=octagonal
    rotation: float
    net: int  # 0-based net index (-1 = no net, encoded as 0xFFFF)
    layer: int  # Altium layer number
    is_tht: bool = False
    plated: bool = True  # False for np_thru_hole pads
    slot_size: int = 0  # slot major axis (0 = round hole)
    slot_rotation: float = 0.0  # slot rotation in degrees (0=horiz, 90=vert)


@dataclass
class AltiumTrack:
    layer: int
    net: int  # 0-based net index (-1 = no net, encoded as 0xFFFF)
    x1: int
    y1: int
    x2: int
    y2: int
    width: int
    component: int = -1  # -1 = free


@dataclass
class AltiumArc:
    layer: int
    net: int = -1
    component: int = -1
    center_x: int = 0
    center_y: int = 0
    radius: int = 0
    start_angle: float = 0.0  # degrees, Altium convention
    end_angle: float = 360.0
    width: int = 0


@dataclass
class AltiumText:
    layer: int
    component: int = -1
    x: int = 0
    y: int = 0
    height: int = 0
    rotation: float = 0.0
    is_mirrored: bool = False
    stroke_width: int = 0
    text: str = ""
    stroke_font_type: int = 1
    is_comment: bool = False
    is_designator: bool = False
    font_type: int = 0
    is_bold: bool = False
    is_italic: bool = False
    font_name: str = ""
    is_inverted: bool = False
    is_inverted_rect: bool = False
    is_frame: bool = False
    is_offset_border: bool = False
    is_justification_valid: bool = False
    margin_border_width: int = 0
    textbox_rect_width: int = 0
    textbox_rect_height: int = 0
    text_offset_width: int = 0
    text_justification: int = 3


@dataclass
class AltiumFill:
    layer: int
    net: int = -1
    component: int = -1
    x1: int = 0
    y1: int = 0
    x2: int = 0
    y2: int = 0
    rotation: float = 0.0


@dataclass
class AltiumVia:
    x: int
    y: int
    diameter: int
    hole_size: int
    start_layer: int
    end_layer: int
    net: int  # 0-based net index (-1 = no net, encoded as 0xFFFF)


@dataclass
class AltiumRegion:
    """A filled copper/mask region (polygon outline with optional holes)."""

    layer: int
    net: int = -1
    component: int = -1
    outline: list[tuple[int, int]] = field(default_factory=list)  # [(x, y), ...]
    holes: list[list[tuple[int, int]]] = field(default_factory=list)
    is_keepout: bool = False
    keepout_restrictions: int = 0  # bitmask: 1=tracks, 2=vias, 4=copper


@dataclass
class AltiumBoardVertex:
    x: int
    y: int


@dataclass
class AltiumRule:
    kind: str
    name: str
    properties: dict[str, str] = field(default_factory=dict)


@dataclass
class AltiumPcbDoc:
    """Top-level container for all Altium PcbDoc data."""

    nets: list[AltiumNet] = field(default_factory=list)
    components: list[AltiumComponent] = field(default_factory=list)
    pads: list[AltiumPad] = field(default_factory=list)
    tracks: list[AltiumTrack] = field(default_factory=list)
    arcs: list[AltiumArc] = field(default_factory=list)
    texts: list[AltiumText] = field(default_factory=list)
    fills: list[AltiumFill] = field(default_factory=list)
    vias: list[AltiumVia] = field(default_factory=list)
    regions: list[AltiumRegion] = field(default_factory=list)
    board_vertices: list[AltiumBoardVertex] = field(default_factory=list)
    rules: list[AltiumRule] = field(default_factory=list)
    layer_count: int = 2
    board_thickness: int = 0  # Altium units
    layer_names: dict[int, str] = field(default_factory=dict)
    raw_streams: dict[str, bytes] = field(default_factory=dict)
    semantic_fingerprint: str | None = None
    stream_fingerprints: dict[str, str] = field(default_factory=dict)
