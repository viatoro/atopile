"""Tests for the standalone layout server websocket RPC contract."""

import asyncio
import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile

import pytest
from fastapi.testclient import TestClient

from atopile.layout_server.__main__ import create_app
from atopile.server.domains.layout import LayoutService
from atopile.server.domains.layout_models import MoveCommand, RedoCommand, UndoCommand

TEST_PCB = Path("test/common/resources/fileformats/kicad/v8/pcb/test.kicad_pcb")


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    pcb_path = tmp_path / TEST_PCB.name
    shutil.copy2(TEST_PCB, pcb_path)

    with TestClient(create_app(pcb_path)) as client:
        yield client


def _send_action(
    websocket,
    *,
    action: str,
    request_id: str | None = None,
    **payload,
) -> None:
    message = {"type": "action", "action": action, **payload}
    if request_id is not None:
        message["requestId"] = request_id
    websocket.send_json(message)


def test_index(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "PCB Layout Editor" in resp.text


def test_get_layout_render_model(client: TestClient) -> None:
    with client.websocket_connect("/ws") as websocket:
        _send_action(
            websocket,
            action="getLayoutRenderModel",
            request_id="render-model",
        )
        resp = websocket.receive_json()

    assert resp["type"] == "action_result"
    assert resp["requestId"] == "render-model"
    assert resp["action"] == "getLayoutRenderModel"
    assert resp["ok"] is True

    model = resp["result"]
    assert model["footprints"]
    assert model["drawings"] is not None
    assert model["layers"]
    assert model["texts"] is not None
    assert model["tracks"] is not None
    assert model["board"] is not None
    assert "edges" in model["board"]
    assert "Edge.Cuts" in {layer["id"] for layer in model["layers"]}


def test_execute_layout_action_move_undo_redo(client: TestClient) -> None:
    with client.websocket_connect("/ws") as websocket:
        _send_action(
            websocket,
            action="getLayoutRenderModel",
            request_id="before",
        )
        model_resp = websocket.receive_json()
        uuid = model_resp["result"]["footprints"][0]["uuid"]

        _send_action(
            websocket,
            action="executeLayoutAction",
            request_id="move",
            command="move",
            uuids=[uuid],
            dx=10.0,
            dy=20.0,
        )
        move_resp = websocket.receive_json()

        _send_action(
            websocket,
            action="executeLayoutAction",
            request_id="undo",
            command="undo",
        )
        undo_resp = websocket.receive_json()

        _send_action(
            websocket,
            action="executeLayoutAction",
            request_id="redo",
            command="redo",
        )
        redo_resp = websocket.receive_json()

    assert move_resp["ok"] is True
    assert move_resp["result"]["status"] == "ok"
    assert any(fp["uuid"] == uuid for fp in move_resp["result"]["delta"]["footprints"])

    assert undo_resp["ok"] is True
    assert undo_resp["result"]["status"] == "ok"
    assert undo_resp["result"]["delta"] is None

    assert redo_resp["ok"] is True
    assert redo_resp["result"]["status"] == "ok"
    assert redo_resp["result"]["delta"] is None


def test_execute_layout_action_validation_error(client: TestClient) -> None:
    with client.websocket_connect("/ws") as websocket:
        _send_action(
            websocket,
            action="executeLayoutAction",
            request_id="invalid",
            command="nonexistent",
        )
        resp = websocket.receive_json()

    assert resp["type"] == "action_result"
    assert resp["requestId"] == "invalid"
    assert resp["action"] == "executeLayoutAction"
    assert resp["ok"] is False
    assert resp["result"] is None
    assert "nonexistent" in resp["error"]


def test_subscribe_layout_receives_push_updates(client: TestClient) -> None:
    with (
        client.websocket_connect("/ws") as subscriber,
        client.websocket_connect("/ws") as actor,
    ):
        _send_action(subscriber, action="subscribeLayout")

        _send_action(
            actor,
            action="getLayoutRenderModel",
            request_id="render-model",
        )
        model_resp = actor.receive_json()
        uuid = model_resp["result"]["footprints"][0]["uuid"]

        _send_action(
            actor,
            action="executeLayoutAction",
            request_id="move",
            command="move",
            uuids=[uuid],
            dx=1.0,
            dy=0.0,
        )
        delta_msg = subscriber.receive_json()
        move_resp = actor.receive_json()

        _send_action(
            actor,
            action="executeLayoutAction",
            request_id="undo",
            command="undo",
        )
        updated_msg = subscriber.receive_json()
        undo_resp = actor.receive_json()

    assert delta_msg["type"] == "layout_delta"
    assert any(fp["uuid"] == uuid for fp in delta_msg["delta"]["footprints"])
    assert move_resp["ok"] is True
    assert move_resp["result"]["status"] == "ok"

    assert updated_msg["type"] == "layout_updated"
    assert updated_msg["model"]["footprints"]
    assert undo_resp["ok"] is True
    assert undo_resp["result"]["status"] == "ok"


def test_save_triggered_watcher_reload_preserves_undo_redo_history() -> None:
    with NamedTemporaryFile(suffix=".kicad_pcb", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    shutil.copy2(TEST_PCB, tmp_path)

    try:
        service = LayoutService()
        service.load(tmp_path)
        original = service.manager.get_render_model().footprints[0]
        uuid = original.uuid
        assert uuid is not None
        original_x = original.at.x
        original_y = original.at.y

        move_resp = asyncio.run(
            service.execute_action(
                MoveCommand(command="move", uuids=[uuid], dx=10.0, dy=20.0)
            )
        )
        asyncio.run(service._on_file_change(object()))
        undo_resp = asyncio.run(service.execute_action(UndoCommand(command="undo")))
        after_undo = next(
            fp
            for fp in service.manager.get_render_model().footprints
            if fp.uuid == uuid
        )
        redo_resp = asyncio.run(service.execute_action(RedoCommand(command="redo")))
        after_redo = next(
            fp
            for fp in service.manager.get_render_model().footprints
            if fp.uuid == uuid
        )

        assert move_resp.status == "ok"
        assert undo_resp.status == "ok"
        assert redo_resp.status == "ok"
        assert after_undo.at.x == pytest.approx(original_x)
        assert after_undo.at.y == pytest.approx(original_y)
        assert after_redo.at.x == pytest.approx(original_x + 10.0)
        assert after_redo.at.y == pytest.approx(original_y + 20.0)
    finally:
        asyncio.run(service.clear())
        tmp_path.unlink()
