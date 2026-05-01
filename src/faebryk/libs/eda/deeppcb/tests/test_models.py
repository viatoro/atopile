"""Model validation tests for DeepPCB LL models against example data."""

from __future__ import annotations

import pytest

from faebryk.libs.eda.deeppcb.convert import file_ll
from faebryk.libs.eda.deeppcb.tests.constants import BOARD_EXAMPLE, CONSTRAINTS_EXAMPLE


@pytest.fixture()
def board():
    if not BOARD_EXAMPLE.exists():
        pytest.skip("example board not found")
    return file_ll.load(BOARD_EXAMPLE)


@pytest.fixture()
def constraints():
    if not CONSTRAINTS_EXAMPLE.exists():
        pytest.skip("example constraints not found")
    return file_ll.load_constraints(CONSTRAINTS_EXAMPLE)


# ---------------------------------------------------------------------------
# Board model validation
# ---------------------------------------------------------------------------


def test_board_resolution(board):
    assert board.resolution.unit in ("mm", "cm", "inch", "mil", "um")
    assert board.resolution.value > 0


def test_board_boundary(board):
    assert board.boundary.shape.type == "polyline"
    assert board.boundary.shape.points is not None
    assert len(board.boundary.shape.points) >= 4
    assert board.boundary.clearance >= 0


def test_board_padstacks(board):
    assert len(board.padstacks) > 0
    for ps in board.padstacks:
        assert ps.id
        # Basic padstack has shape + layers
        if ps.shape is not None:
            assert ps.shape.type in (
                "circle",
                "rectangle",
                "polyline",
                "polygon",
                "path",
                "multi",
            )
            assert ps.layers is not None
            assert len(ps.layers) > 0


def test_board_component_definitions(board):
    assert len(board.component_definitions) > 0
    for cd in board.component_definitions:
        assert cd.id
        assert isinstance(cd.pins, list)
        assert isinstance(cd.keepouts, list)
        for pin in cd.pins:
            assert pin.id
            assert pin.padstack  # references a padstack
            assert len(pin.position) == 2


def test_board_components(board):
    assert len(board.components) > 0
    comp_def_ids = {cd.id for cd in board.component_definitions}
    for comp in board.components:
        assert comp.id
        assert comp.definition in comp_def_ids, (
            f"Component {comp.id} references unknown definition {comp.definition}"
        )
        assert len(comp.position) == 2
        assert comp.side in ("FRONT", "BACK")


def test_board_layers(board):
    assert len(board.layers) > 0
    for layer in board.layers:
        assert layer.id
        assert isinstance(layer.keepouts, list)


def test_board_nets(board):
    assert len(board.nets) > 0
    for net in board.nets:
        assert net.id
        assert isinstance(net.pins, list)


def test_board_wires(board):
    assert len(board.wires) > 0
    for wire in board.wires:
        assert wire.net_id
        assert wire.type == "segment"
        assert len(wire.start) == 2
        assert len(wire.end) == 2
        assert wire.width > 0


def test_board_vias(board):
    assert len(board.vias) > 0
    padstack_ids = {ps.id for ps in board.padstacks}
    for via in board.vias:
        assert via.net_id
        assert len(via.position) == 2
        assert via.padstack in padstack_ids


def test_board_planes(board):
    assert len(board.planes) > 0
    for plane in board.planes:
        assert plane.net_id
        assert plane.shape.type in ("polygon", "polygonWithHoles", "multi", "polyline")


def test_board_net_classes(board):
    assert len(board.net_classes) > 0
    for nc in board.net_classes:
        assert nc.id
        assert nc.clearance >= 0


def test_board_rules(board):
    for rule in board.rules:
        assert rule.type in (
            "allowViaAtSmd",
            "allow90Degrees",
            "rotateFirst",
            "clearance",
            "routingDirection",
            "pinConnectionPoint",
            "directConnection",
        )


# ---------------------------------------------------------------------------
# Constraints model validation
# ---------------------------------------------------------------------------


def test_constraints_decoupling(constraints):
    assert len(constraints.decoupling_constraints) > 0
    for pin_id, targets in constraints.decoupling_constraints.items():
        assert isinstance(pin_id, str)
        for target in targets:
            assert target.type
            assert len(target.targets) > 0


def test_constraints_net_types(constraints):
    assert len(constraints.net_type_constraints) > 0
    valid_types = {
        "decoupled_by",
        "supported_by",
        "high_speed",
        "medium_speed",
        "low_speed",
        "analog",
        "power",
        "ground",
        "unknown",
    }
    for ntc in constraints.net_type_constraints:
        assert ntc.type in valid_types, f"Unknown net type: {ntc.type}"
        assert len(ntc.targets) > 0
