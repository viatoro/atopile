"""LL round-trip tests for DeepPCB board and constraints formats."""

from __future__ import annotations

import pytest

from faebryk.libs.eda.deeppcb.convert import file_ll
from faebryk.libs.eda.deeppcb.models import ll
from faebryk.libs.eda.deeppcb.tests.constants import BOARD_EXAMPLE, CONSTRAINTS_EXAMPLE


@pytest.fixture()
def board():
    if not BOARD_EXAMPLE.exists():
        pytest.skip("example board not found")
    return file_ll.load(BOARD_EXAMPLE)


@pytest.fixture()
def constraints_obj():
    if not CONSTRAINTS_EXAMPLE.exists():
        pytest.skip("example constraints not found")
    return file_ll.load_constraints(CONSTRAINTS_EXAMPLE)


# ---------------------------------------------------------------------------
# Board round-trip
# ---------------------------------------------------------------------------


def test_board_load(board):
    """Verify the example board parses without error."""
    assert isinstance(board, ll.DeepPCBBoard)
    assert len(board.components) > 0
    assert len(board.nets) > 0
    assert len(board.padstacks) > 0
    assert len(board.layers) > 0


def test_board_round_trip(board):
    """Load → dump → load → assert structurally equal."""
    json_text = file_ll.dump(board)
    board2 = file_ll.load(json_text)

    # Compare top-level counts
    assert len(board.components) == len(board2.components)
    assert len(board.nets) == len(board2.nets)
    assert len(board.padstacks) == len(board2.padstacks)
    assert len(board.layers) == len(board2.layers)
    assert len(board.wires) == len(board2.wires)
    assert len(board.vias) == len(board2.vias)
    assert len(board.planes) == len(board2.planes)
    assert len(board.component_definitions) == len(board2.component_definitions)
    assert len(board.net_classes) == len(board2.net_classes)
    assert len(board.rules) == len(board2.rules)

    # Deep equality
    assert board == board2


# ---------------------------------------------------------------------------
# Constraints round-trip
# ---------------------------------------------------------------------------


def test_constraints_load(constraints_obj):
    """Verify the example constraints parse without error."""
    assert isinstance(constraints_obj, ll.DeepPCBConstraints)
    assert len(constraints_obj.decoupling_constraints) > 0
    assert len(constraints_obj.net_type_constraints) > 0


def test_constraints_round_trip(constraints_obj):
    """Load → dump → load → assert structurally equal."""
    json_text = file_ll.dump_constraints(constraints_obj)
    c2 = file_ll.load_constraints(json_text)
    assert constraints_obj == c2


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_board():
    """Verify we can create, dump, and reload a minimal board."""
    board = ll.DeepPCBBoard(
        name="empty",
        resolution=ll.Resolution(unit=ll.ResolutionUnit.MM, value=1000),
        boundary=ll.Boundary(
            shape=ll.Shape(
                type=ll.ShapeType.POLYLINE,
                points=[[0, 0], [1000, 0], [1000, 1000], [0, 1000], [0, 0]],
            ),
            clearance=200,
        ),
        padstacks=[],
        component_definitions=[],
        components=[],
        layers=[ll.Layer(id="F.Cu", keepouts=[])],
        nets=[],
        net_classes=[],
        planes=[],
        wires=[],
        vias=[],
        via_definitions=[],
    )
    json_text = file_ll.dump(board)
    board2 = file_ll.load(json_text)
    assert board == board2


def test_all_shape_types():
    """Verify round-trip for all shape types."""
    shapes = [
        ll.Shape(type=ll.ShapeType.CIRCLE, center=[0, 0], radius=100),
        ll.Shape(
            type=ll.ShapeType.RECTANGLE, lower_left=[-50, -50], upper_right=[50, 50]
        ),
        ll.Shape(type=ll.ShapeType.POLYLINE, points=[[0, 0], [100, 0], [100, 100]]),
        ll.Shape(type=ll.ShapeType.POLYGON, points=[[0, 0], [100, 0], [100, 100]]),
        ll.Shape(type=ll.ShapeType.PATH, points=[[0, 100], [0, -100]], width=50),
        ll.Shape(
            type=ll.ShapeType.POLYGON_WITH_HOLES,
            outline=[[0, 0], [100, 0], [100, 100], [0, 100]],
            holes=[[[25, 25], [75, 25], [75, 75], [25, 75]]],
        ),
        ll.Shape(
            type=ll.ShapeType.MULTI,
            shapes=[
                ll.Shape(type=ll.ShapeType.CIRCLE, center=[0, 0], radius=50),
                ll.Shape(
                    type=ll.ShapeType.RECTANGLE,
                    lower_left=[-10, -10],
                    upper_right=[10, 10],
                ),
            ],
        ),
    ]
    for shape in shapes:
        padstack = ll.Padstack(id=f"test_{shape.type}", shape=shape, layers=[0])
        board = ll.DeepPCBBoard(
            name="shapes",
            resolution=ll.Resolution(unit=ll.ResolutionUnit.MM, value=1000),
            boundary=ll.Boundary(
                shape=ll.Shape(
                    type=ll.ShapeType.POLYLINE, points=[[0, 0], [1, 0], [1, 1], [0, 0]]
                ),
                clearance=0,
            ),
            padstacks=[padstack],
            component_definitions=[],
            components=[],
            layers=[ll.Layer(id="F.Cu", keepouts=[])],
            nets=[],
            net_classes=[],
            planes=[],
            wires=[],
            vias=[],
            via_definitions=[],
        )
        json_text = file_ll.dump(board)
        board2 = file_ll.load(json_text)
        assert board == board2, f"Round-trip failed for shape type: {shape.type}"
