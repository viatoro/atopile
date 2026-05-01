"""Manual integration test against the live DeepPCB API.

Run with:
    DEEPPCB_API_KEY=your-key python -m \
    atopile.autolayout.deeppcb.tests.manual_integration

Gated behind DEEPPCB_API_KEY — never runs in CI.
Hits each endpoint once with minimal payloads. Target runtime: <30s.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from atopile.autolayout.deeppcb.client import DeepPCBClient
from atopile.autolayout.deeppcb.models import (
    BoardInputType,
    CreateBoardRequest,
    RoutingType,
)


def _minimal_board() -> dict:
    """Return a minimal valid .deeppcb board for testing."""
    return {
        "name": "integration-test",
        "resolution": {"unit": "mm", "value": 1000},
        "boundary": {
            "shape": {
                "type": "polyline",
                "points": [
                    [0, 0],
                    [10000, 0],
                    [10000, 10000],
                    [0, 10000],
                    [0, 0],
                ],
            }
        },
        "padstacks": [
            {
                "id": "ps1",
                "layers": [0],
                "shape": {"type": "circle", "center": [0, 0], "radius": 250},
            }
        ],
        "componentDefinitions": [
            {
                "id": "cd1",
                "keepouts": [],
                "pins": [
                    {
                        "id": "1",
                        "padstack": "ps1",
                        "position": [-500, 0],
                        "rotation": 0,
                    },
                    {"id": "2", "padstack": "ps1", "position": [500, 0], "rotation": 0},
                ],
            }
        ],
        "components": [
            {
                "id": "C1",
                "definition": "cd1",
                "position": [3000, 5000],
                "rotation": 0,
                "side": "FRONT",
            },
            {
                "id": "C2",
                "definition": "cd1",
                "position": [7000, 5000],
                "rotation": 0,
                "side": "FRONT",
            },
        ],
        "layers": [{"id": "F.Cu", "keepouts": []}],
        "nets": [{"id": "net1", "pins": ["C1-1", "C2-2"]}],
        "netClasses": [
            {
                "id": "default",
                "nets": ["net1"],
                "clearance": 200,
                "trackWidth": 250,
                "viaDefinition": "ps1",
            }
        ],
        "planes": [],
        "wires": [],
        "vias": [],
        "viaDefinitions": ["ps1"],
    }


def main() -> None:
    auth_token = os.environ.get("DEEPPCB_AUTH_TOKEN")
    if not auth_token:
        print("DEEPPCB_AUTH_TOKEN not set — skipping integration test")
        sys.exit(0)

    print("Starting DeepPCB integration test...")

    with DeepPCBClient(auth_token=auth_token) as client:
        # 1. Upload a board file
        print("[1/5] Uploading board file...")
        with tempfile.NamedTemporaryFile(
            suffix=".deeppcb",
            mode="w",
            delete=False,
        ) as f:
            json.dump(_minimal_board(), f)
            tmp_path = Path(f.name)

        try:
            url = client.upload_board_file(tmp_path)
            print(f"  Upload URL: {url[:80]}...")
        finally:
            tmp_path.unlink()

        # 2. Create a board
        print("[2/5] Creating board...")
        board_id = client.create_board(
            CreateBoardRequest(
                routing_type=RoutingType.EMPTY_BOARD,
                board_input_type=BoardInputType.JSON,
                json_file_url=url,
                board_name="integration-test",
            )
        )
        print(f"  Board ID: {board_id}")

        # 3. Get board details
        print("[3/5] Getting board details...")
        details = client.get_board_details(board_id)
        print(f"  Status: {details.board_status}, Name: {details.name}")

        # 4. Get full board info
        print("[4/5] Getting full board info...")
        board = client.get_board(board_id)
        print(f"  Status: {board.board_status}, Air wires: {board.total_air_wires}")

        # 5. Fetch schemas
        print("[5/5] Fetching board schema...")
        schema = client.get_board_schema()
        print(f"  Schema title: {schema.get('title', 'unknown')}")

    print("\nIntegration test passed!")


if __name__ == "__main__":
    main()
