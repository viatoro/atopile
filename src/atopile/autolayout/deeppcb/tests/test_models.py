"""Tests for DeepPCB Pydantic models against real fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from atopile.autolayout.deeppcb.models import (
    BoardConstraints,
    DeepPCBBoard,
)

# Paths to fixture files
_FIXTURES_DIR = Path(__file__).resolve().parent / "resources"
_BOARD_FIXTURE = _FIXTURES_DIR / "Board-Example-icad-manual.deeppcb"
_CONSTRAINTS_FIXTURE = _FIXTURES_DIR / "Constraints-Example-kicad-manual.json"


@pytest.fixture
def board_json() -> dict:
    return json.loads(_BOARD_FIXTURE.read_text())


@pytest.fixture
def constraints_json() -> dict:
    return json.loads(_CONSTRAINTS_FIXTURE.read_text())


class TestDeepPCBBoard:
    def test_load_board_fixture(self, board_json: dict) -> None:
        board = DeepPCBBoard.model_validate(board_json)
        assert board.name is not None
        assert len(board.padstacks) > 0
        assert len(board.components) > 0
        assert len(board.nets) > 0
        assert len(board.layers) > 0
        assert board.resolution.unit == "mm"

    def test_board_round_trip(self, board_json: dict) -> None:
        board = DeepPCBBoard.model_validate(board_json)
        serialized = json.loads(board.model_dump_json(by_alias=True, exclude_none=True))
        board2 = DeepPCBBoard.model_validate(serialized)
        assert board2.name == board.name
        assert len(board2.components) == len(board.components)
        assert len(board2.nets) == len(board.nets)
        assert len(board2.padstacks) == len(board.padstacks)

    def test_board_boundary(self, board_json: dict) -> None:
        board = DeepPCBBoard.model_validate(board_json)
        assert board.boundary.shape.type == "polyline"
        assert board.boundary.shape.points is not None
        assert len(board.boundary.shape.points) > 0

    def test_board_net_classes(self, board_json: dict) -> None:
        board = DeepPCBBoard.model_validate(board_json)
        assert len(board.net_classes) >= 1
        for nc in board.net_classes:
            assert nc.id
            assert nc.clearance >= 0

    def test_board_rules(self, board_json: dict) -> None:
        board = DeepPCBBoard.model_validate(board_json)
        assert board.rules is not None
        assert len(board.rules) > 0
        rule_types = {r.type for r in board.rules}
        assert "rotateFirst" in rule_types or "allowViaAtSmd" in rule_types


class TestBoardConstraints:
    def test_load_constraints_fixture(self, constraints_json: dict) -> None:
        constraints = BoardConstraints.model_validate(constraints_json)
        assert constraints.net_type_constraints is not None
        assert len(constraints.net_type_constraints) > 0

    def test_constraints_round_trip(self, constraints_json: dict) -> None:
        constraints = BoardConstraints.model_validate(constraints_json)
        serialized = json.loads(
            constraints.model_dump_json(by_alias=True, exclude_none=True)
        )
        constraints2 = BoardConstraints.model_validate(serialized)
        assert constraints.net_type_constraints is not None
        assert constraints2.net_type_constraints is not None
        assert len(constraints2.net_type_constraints) == len(
            constraints.net_type_constraints
        )

    def test_net_type_constraints(self, constraints_json: dict) -> None:
        constraints = BoardConstraints.model_validate(constraints_json)
        assert constraints.net_type_constraints is not None
        types = {c.type for c in constraints.net_type_constraints}
        assert "high_speed" in types
        assert "power" in types
        assert "ground" in types

    def test_decoupling_constraints(self, constraints_json: dict) -> None:
        constraints = BoardConstraints.model_validate(constraints_json)
        assert constraints.decoupling_constraints is not None
        # The extra fields contain the pin-keyed constraints
        extra = constraints.decoupling_constraints.model_extra
        assert extra is not None
        assert len(extra) > 0
