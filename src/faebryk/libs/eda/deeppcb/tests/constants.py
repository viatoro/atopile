"""Shared constants for DeepPCB tests."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from faebryk.libs.eda.hl.models.pcb import OutlineSegment, Point2D

# ---------------------------------------------------------------------------
# Directory roots
# ---------------------------------------------------------------------------

TESTS_DIR = Path(__file__).resolve().parent
DATA_DIR = TESTS_DIR.parent
FIXTURES_DIR = TESTS_DIR / "fixtures"
EXAMPLES_DIR = TESTS_DIR.parents[5] / "examples"

# ---------------------------------------------------------------------------
# DeepPCB example files
# ---------------------------------------------------------------------------

BOARD_EXAMPLE = DATA_DIR / "Board Example - kicad-maual-1 (1).deeppcb"
CONSTRAINTS_EXAMPLE = DATA_DIR / "Constraints Example - kicad-manual-1 (1).json"

# ---------------------------------------------------------------------------
# KiCad example boards
# ---------------------------------------------------------------------------

KICAD_TOP = EXAMPLES_DIR / "layout_reuse" / "layout" / "top" / "top.kicad_pcb"
KICAD_ESP32 = (
    EXAMPLES_DIR
    / "esp32_minimal"
    / "layouts"
    / "esp32_minimal"
    / "esp32_minimal.kicad_pcb"
)
KICAD_BADGE = EXAMPLES_DIR / "led_badge" / "layouts" / "badge" / "badge.kicad_pcb"

# ---------------------------------------------------------------------------
# Per-board expected minimums
# ---------------------------------------------------------------------------


@dataclass
class BoardExpectation:
    min_components: int
    min_geometries: int


BOARD_EXPECTATIONS: dict[Path, BoardExpectation] = {
    KICAD_TOP: BoardExpectation(min_components=3, min_geometries=2),
    KICAD_ESP32: BoardExpectation(min_components=15, min_geometries=100),
    KICAD_BADGE: BoardExpectation(min_components=200, min_geometries=500),
}

# ---------------------------------------------------------------------------
# Parametrize lists
# ---------------------------------------------------------------------------

ALL_BOARDS = [
    pytest.param(KICAD_TOP, id="top"),
    pytest.param(KICAD_ESP32, id="esp32"),
    pytest.param(KICAD_BADGE, id="badge"),
]

API_BOARDS = [
    pytest.param(KICAD_TOP, id="top"),
    pytest.param(KICAD_ESP32, id="esp32"),
]

REF_BOARDS = [
    pytest.param(
        FIXTURES_DIR / "esp32_reference.kicad_pcb",
        FIXTURES_DIR / "esp32_reference.deeppcb",
        id="esp32",
    ),
]

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

HAS_API_KEY = bool(os.environ.get("DEEPPCB_API_KEY"))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TOLERANCE = 1e-3  # 1 μm


def rect_outline(
    x0: float = 0, y0: float = 0, x1: float = 200, y1: float = 200
) -> list[OutlineSegment]:
    """Create a simple rectangular board outline (Edge.Cuts)."""
    corners = [
        Point2D(x=x0, y=y0),
        Point2D(x=x1, y=y0),
        Point2D(x=x1, y=y1),
        Point2D(x=x0, y=y1),
    ]
    return [
        OutlineSegment(start=corners[i], end=corners[(i + 1) % 4]) for i in range(4)
    ]
