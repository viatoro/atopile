"""Tests for DeepPCBClient against recorded fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from atopile.autolayout.deeppcb.client import DeepPCBClient
from atopile.autolayout.deeppcb.exceptions import (
    DeepPCBClientError,
    DeepPCBServerError,
)
from atopile.autolayout.deeppcb.models import (
    BoardInputType,
    BoardStatus,
    ConfirmBoardRequest,
    CreateBoardRequest,
    JobType,
    ResumeBoardRequest,
    RoutingType,
)

_FIXTURES = Path(__file__).parent / "fixtures"
_BASE_URL = "https://test.deeppcb.ai"
_AUTH_TOKEN = "test-auth-token-123"


def _load_fixture(name: str) -> str:
    return (_FIXTURES / name).read_text()


def _load_fixture_json(name: str) -> dict | str:
    return json.loads(_load_fixture(name))


@pytest.fixture
def client() -> DeepPCBClient:
    return DeepPCBClient(auth_token=_AUTH_TOKEN, base_url=_BASE_URL)


class TestUploadBoardFile:
    @respx.mock
    def test_upload_returns_url(self, client: DeepPCBClient, tmp_path: Path) -> None:
        fixture = _load_fixture("upload_response.txt")
        respx.post(f"{_BASE_URL}/api/v1/files/uploads/board-file").mock(
            return_value=httpx.Response(200, text=fixture)
        )

        board_file = tmp_path / "test.deeppcb"
        board_file.write_text('{"name": "test"}')

        url = client.upload_board_file(board_file)
        assert url == "https://storage.deeppcb.ai/uploads/abc123/board.deeppcb"

    @respx.mock
    def test_upload_sends_auth_header(
        self, client: DeepPCBClient, tmp_path: Path
    ) -> None:
        route = respx.post(f"{_BASE_URL}/api/v1/files/uploads/board-file").mock(
            return_value=httpx.Response(200, text='"url"')
        )

        board_file = tmp_path / "test.deeppcb"
        board_file.write_text("{}")

        client.upload_board_file(board_file)
        assert route.called
        request = route.calls[0].request
        assert request.headers["authorization"] == f"Bearer {_AUTH_TOKEN}"


class TestCreateBoard:
    @respx.mock
    def test_create_board_with_url(self, client: DeepPCBClient) -> None:
        fixture = _load_fixture("create_board_response.json")
        respx.post(f"{_BASE_URL}/api/v1/boards").mock(
            return_value=httpx.Response(200, text=fixture)
        )

        req = CreateBoardRequest(
            routing_type=RoutingType.EMPTY_BOARD,
            board_input_type=BoardInputType.JSON,
            json_file_url="https://storage.deeppcb.ai/uploads/abc123/board.deeppcb",
            board_name="test-board",
        )
        board_id = client.create_board(req)
        assert board_id == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

    @respx.mock
    def test_create_board_with_file(
        self, client: DeepPCBClient, tmp_path: Path
    ) -> None:
        fixture = _load_fixture("create_board_response.json")
        respx.post(f"{_BASE_URL}/api/v1/boards").mock(
            return_value=httpx.Response(200, text=fixture)
        )

        board_file = tmp_path / "test.deeppcb"
        board_file.write_text('{"name": "test"}')

        board_id = client.create_board_with_file(
            file_path=board_file,
            board_input_type=BoardInputType.JSON,
            routing_type=RoutingType.EMPTY_BOARD,
        )
        assert board_id == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


class TestGetBoard:
    @respx.mock
    def test_get_board(self, client: DeepPCBClient) -> None:
        fixture = _load_fixture("get_board_response.json")
        board_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        respx.get(f"{_BASE_URL}/api/v1/boards/{board_id}").mock(
            return_value=httpx.Response(200, text=fixture)
        )

        board = client.get_board(board_id)
        assert board.board_id == board_id
        assert board.name == "test-board"
        assert board.board_status == BoardStatus.RUNNING
        assert board.total_air_wires == 20
        assert board.result is not None
        assert board.result.air_wires_connected == 18
        assert board.workflows is not None
        assert len(board.workflows) == 1
        assert board.workflows[0].revisions is not None
        assert len(board.workflows[0].revisions) == 1

    @respx.mock
    def test_get_board_details(self, client: DeepPCBClient) -> None:
        fixture = _load_fixture("board_details_response.json")
        board_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        respx.get(f"{_BASE_URL}/api/v1/boards/{board_id}/details").mock(
            return_value=httpx.Response(200, text=fixture)
        )

        details = client.get_board_details(board_id)
        assert details.board_id == board_id
        assert details.board_status == BoardStatus.DONE
        assert details.request_id == "req-123"


class TestBoardLifecycle:
    @respx.mock
    def test_confirm_board(self, client: DeepPCBClient) -> None:
        board_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        route = respx.patch(f"{_BASE_URL}/api/v1/boards/{board_id}/confirm").mock(
            return_value=httpx.Response(200)
        )

        req = ConfirmBoardRequest(
            job_type=JobType.ROUTING,
            routing_type=RoutingType.EMPTY_BOARD,
            timeout=30,
        )
        client.confirm_board(board_id, req)
        assert route.called

        # Verify JSON body
        request = route.calls[0].request
        body = json.loads(request.content)
        assert body["jobType"] == "Routing"
        assert body["routingType"] == "EmptyBoard"
        assert body["timeout"] == 30

    @respx.mock
    def test_stop_board(self, client: DeepPCBClient) -> None:
        board_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        route = respx.patch(f"{_BASE_URL}/api/v1/boards/{board_id}/stop").mock(
            return_value=httpx.Response(200)
        )
        client.stop_board(board_id)
        assert route.called

    @respx.mock
    def test_resume_board(self, client: DeepPCBClient) -> None:
        board_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        route = respx.patch(f"{_BASE_URL}/api/v1/boards/{board_id}/resume").mock(
            return_value=httpx.Response(200)
        )

        req = ResumeBoardRequest(
            job_type=JobType.ROUTING,
            routing_type=RoutingType.EMPTY_BOARD,
            timeout=15,
        )
        client.resume_board(board_id, req)
        assert route.called

        body = json.loads(route.calls[0].request.content)
        assert body["jobType"] == "Routing"
        assert body["timeout"] == 15


class TestCheckBoard:
    @respx.mock
    def test_check_board_with_url(self, client: DeepPCBClient) -> None:
        fixture = _load_fixture("check_board_response.json")
        respx.post(f"{_BASE_URL}/api/v1/boards/check-board").mock(
            return_value=httpx.Response(200, text=fixture)
        )

        result = client.check_board(
            json_file_url="https://storage.deeppcb.ai/uploads/abc123/board.deeppcb"
        )
        assert result.is_valid is True
        assert result.warnings == ["Minor overlap on layer 0"]

    @respx.mock
    def test_check_board_with_file(self, client: DeepPCBClient, tmp_path: Path) -> None:
        fixture = _load_fixture("check_board_response.json")
        respx.post(f"{_BASE_URL}/api/v1/boards/check-board").mock(
            return_value=httpx.Response(200, text=fixture)
        )

        board_file = tmp_path / "test.deeppcb"
        board_file.write_text('{"name": "test"}')

        result = client.check_board(json_file_path=board_file)
        assert result.is_valid is True

    def test_check_board_rejects_both_url_and_path(
        self, client: DeepPCBClient, tmp_path: Path
    ) -> None:
        board_file = tmp_path / "test.deeppcb"
        board_file.write_text("{}")
        with pytest.raises(ValueError, match="not both"):
            client.check_board(
                json_file_url="https://example.com/board.json",
                json_file_path=board_file,
            )

    def test_check_board_rejects_neither(self, client: DeepPCBClient) -> None:
        with pytest.raises(ValueError, match="Provide either"):
            client.check_board()


class TestCheckConstraints:
    @respx.mock
    def test_check_constraints_with_url(self, client: DeepPCBClient) -> None:
        fixture = _load_fixture("check_constraints_response.json")
        respx.post(f"{_BASE_URL}/api/v1/boards/check-constraints").mock(
            return_value=httpx.Response(200, text=fixture)
        )

        result = client.check_constraints(
            constraints_file_url="https://storage.deeppcb.ai/constraints.json"
        )
        assert result.is_valid is True

    @respx.mock
    def test_check_constraints_with_file(
        self, client: DeepPCBClient, tmp_path: Path
    ) -> None:
        fixture = _load_fixture("check_constraints_response.json")
        respx.post(f"{_BASE_URL}/api/v1/boards/check-constraints").mock(
            return_value=httpx.Response(200, text=fixture)
        )

        constraints_file = tmp_path / "constraints.json"
        constraints_file.write_text('{"decoupling_constraints": {}}')

        result = client.check_constraints(constraints_file_path=constraints_file)
        assert result.is_valid is True

    def test_check_constraints_rejects_both(
        self, client: DeepPCBClient, tmp_path: Path
    ) -> None:
        f = tmp_path / "c.json"
        f.write_text("{}")
        with pytest.raises(ValueError, match="not both"):
            client.check_constraints(
                constraints_file_url="https://example.com/c.json",
                constraints_file_path=f,
            )

    def test_check_constraints_rejects_neither(self, client: DeepPCBClient) -> None:
        with pytest.raises(ValueError, match="Provide either"):
            client.check_constraints()


class TestConvertToJson:
    @respx.mock
    def test_convert_kicad_to_json(self, client: DeepPCBClient, tmp_path: Path) -> None:
        respx.post(f"{_BASE_URL}/api/v1/boards/convert-to-json").mock(
            return_value=httpx.Response(200, text='"{\\"name\\": \\"converted\\"}"')
        )

        board_file = tmp_path / "test.kicad_pcb"
        board_file.write_text("(kicad_pcb ...)")

        result = client.convert_to_json(
            file_path=board_file,
            board_input_type=BoardInputType.KICAD,
            routing_type=RoutingType.EMPTY_BOARD,
        )
        assert "converted" in result


class TestGetBoardJson:
    @respx.mock
    def test_get_board_json(self, client: DeepPCBClient) -> None:
        board_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        respx.get(f"{_BASE_URL}/api/v1/boards/{board_id}/json").mock(
            return_value=httpx.Response(200, text='"{\\"name\\": \\"test\\"}"')
        )
        result = client.get_board_json(board_id)
        assert "test" in result


class TestGetBoardByRequestId:
    @respx.mock
    def test_get_board_by_request_id(self, client: DeepPCBClient) -> None:
        request_id = "my-req-123"
        respx.get(f"{_BASE_URL}/api/v1/boards/requests/{request_id}").mock(
            return_value=httpx.Response(
                200, text='"a1b2c3d4-e5f6-7890-abcd-ef1234567890"'
            )
        )
        board_id = client.get_board_by_request_id(request_id)
        assert board_id == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

    @respx.mock
    def test_get_board_by_request_id_not_found(self, client: DeepPCBClient) -> None:
        respx.get(f"{_BASE_URL}/api/v1/boards/requests/unknown").mock(
            return_value=httpx.Response(404, text='{"errorMessage": "Not found"}')
        )
        with pytest.raises(DeepPCBClientError) as exc_info:
            client.get_board_by_request_id("unknown")
        assert exc_info.value.status_code == 404


class TestDownloadRevisionArtifact:
    @respx.mock
    def test_download_revision_artifact(self, client: DeepPCBClient) -> None:
        board_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        respx.get(f"{_BASE_URL}/api/v1/boards/{board_id}/revision-artifact").mock(
            return_value=httpx.Response(200, text='"{\\"routed\\": true}"')
        )

        result = client.download_revision_artifact(
            board_id, revision=1, artifact_type="json"
        )
        assert "routed" in result


class TestRecommendedBatchTimeout:
    @respx.mock
    def test_get_recommended_max_batch_timeout(self, client: DeepPCBClient) -> None:
        board_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        respx.get(
            f"{_BASE_URL}/api/v1/boards/{board_id}/recommended-max-batch-timeout"
        ).mock(return_value=httpx.Response(200, text="120"))

        result = client.get_recommended_max_batch_timeout(board_id)
        assert result == 120


class TestSchemaEndpoints:
    @respx.mock
    def test_get_board_schema(self, client: DeepPCBClient) -> None:
        respx.get(f"{_BASE_URL}/api/v1/boards/board-schema").mock(
            return_value=httpx.Response(
                200, json={"title": "DeepPCB File Format", "type": "object"}
            )
        )
        schema = client.get_board_schema()
        assert schema["title"] == "DeepPCB File Format"

    @respx.mock
    def test_get_constraints_schema(self, client: DeepPCBClient) -> None:
        respx.get(f"{_BASE_URL}/api/v1/boards/constraints-schema").mock(
            return_value=httpx.Response(
                200, json={"title": "BoardConstraintsSchema", "type": "object"}
            )
        )
        schema = client.get_constraints_schema()
        assert schema["title"] == "BoardConstraintsSchema"


class TestWorkflowConstraints:
    @respx.mock
    def test_get_workflow_constraints(self, client: DeepPCBClient) -> None:
        board_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        workflow_id = "wf-001"
        respx.get(
            f"{_BASE_URL}/api/v1/boards/{board_id}/workflow/{workflow_id}/constraints"
        ).mock(
            return_value=httpx.Response(
                200, json={"decoupling_constraints": {}, "net_type_constraints": []}
            )
        )
        result = client.get_workflow_constraints(board_id, workflow_id)
        assert "decoupling_constraints" in result


class TestSubmitMetadata:
    @respx.mock
    def test_submit_metadata(self, client: DeepPCBClient) -> None:
        board_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        route = respx.post(f"{_BASE_URL}/api/v1/boards/{board_id}/metadata").mock(
            return_value=httpx.Response(200)
        )
        client.submit_metadata(board_id, {"event": "test", "source": "atopile"})
        assert route.called
        body = json.loads(route.calls[0].request.content)
        assert body["event"] == "test"


class TestCreditFlow:
    @respx.mock
    def test_get_credit_flow(self, client: DeepPCBClient) -> None:
        fixture = _load_fixture("credit_flow_response.json")
        respx.get(f"{_BASE_URL}/api/v1/apiuser/credit-flow").mock(
            return_value=httpx.Response(200, text=fixture)
        )

        result = client.get_credit_flow()
        assert result.balance == 100.5
        assert result.used_credits == 2.5
        assert result.created_boards == 1
        assert result.balance_changes is not None
        assert len(result.balance_changes) == 1

    @respx.mock
    def test_get_credit_flow_with_dates(self, client: DeepPCBClient) -> None:
        from datetime import datetime

        fixture = _load_fixture("credit_flow_response.json")
        route = respx.get(f"{_BASE_URL}/api/v1/apiuser/credit-flow").mock(
            return_value=httpx.Response(200, text=fixture)
        )

        client.get_credit_flow(
            start_date=datetime(2025, 1, 1),
            end_date=datetime(2025, 1, 31),
        )
        assert route.called
        request = route.calls[0].request
        assert "StartDate" in str(request.url)
        assert "EndDate" in str(request.url)


class TestFullWorkflow:
    """Test the complete upload -> create -> confirm -> poll -> download flow."""

    @respx.mock
    def test_full_routing_workflow(self, client: DeepPCBClient, tmp_path: Path) -> None:
        board_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

        # 1. Upload
        respx.post(f"{_BASE_URL}/api/v1/files/uploads/board-file").mock(
            return_value=httpx.Response(
                200,
                text="https://storage.deeppcb.ai/uploads/abc123/board.deeppcb",
            )
        )

        # 2. Create board
        respx.post(f"{_BASE_URL}/api/v1/boards").mock(
            return_value=httpx.Response(200, text=f'"{board_id}"')
        )

        # 3. Confirm
        respx.patch(f"{_BASE_URL}/api/v1/boards/{board_id}/confirm").mock(
            return_value=httpx.Response(200)
        )

        # 4. Poll status — first Running, then Done
        get_board_running = _load_fixture("get_board_response.json")
        get_board_done = json.loads(get_board_running)
        get_board_done["boardStatus"] = "Done"
        get_board_done_str = json.dumps(get_board_done)

        respx.get(f"{_BASE_URL}/api/v1/boards/{board_id}").mock(
            side_effect=[
                httpx.Response(200, text=get_board_running),
                httpx.Response(200, text=get_board_done_str),
            ]
        )

        # 5. Download artifact
        respx.get(f"{_BASE_URL}/api/v1/boards/{board_id}/download-artifact").mock(
            return_value=httpx.Response(200, content=b'{"routed": true}')
        )

        # Execute workflow
        board_file = tmp_path / "test.deeppcb"
        board_file.write_text('{"name": "test"}')

        url = client.upload_board_file(board_file)
        assert "storage.deeppcb.ai" in url

        created_id = client.create_board(
            CreateBoardRequest(
                routing_type=RoutingType.EMPTY_BOARD,
                board_input_type=BoardInputType.JSON,
                json_file_url=url,
            )
        )
        assert created_id == board_id

        client.confirm_board(
            board_id,
            ConfirmBoardRequest(
                job_type=JobType.ROUTING,
                routing_type=RoutingType.EMPTY_BOARD,
                timeout=30,
            ),
        )

        # Poll
        status1 = client.get_board(board_id)
        assert status1.board_status == BoardStatus.RUNNING

        status2 = client.get_board(board_id)
        assert status2.board_status == BoardStatus.DONE

        # Download
        artifact = client.download_artifact(board_id)
        assert b"routed" in artifact


class TestErrorHandling:
    @respx.mock
    def test_400_raises_client_error(self, client: DeepPCBClient) -> None:
        fixture = _load_fixture("error_400.json")
        board_id = "bad-id"
        respx.get(f"{_BASE_URL}/api/v1/boards/{board_id}").mock(
            return_value=httpx.Response(400, text=fixture)
        )

        with pytest.raises(DeepPCBClientError) as exc_info:
            client.get_board(board_id)
        assert exc_info.value.status_code == 400
        assert "INVALID_BOARD" in exc_info.value.body

    @respx.mock
    def test_500_raises_server_error(self, client: DeepPCBClient) -> None:
        fixture = _load_fixture("error_500.json")
        board_id = "some-id"
        respx.get(f"{_BASE_URL}/api/v1/boards/{board_id}").mock(
            return_value=httpx.Response(500, text=fixture)
        )

        with pytest.raises(DeepPCBServerError) as exc_info:
            client.get_board(board_id)
        assert exc_info.value.status_code == 500

    @respx.mock
    def test_409_raises_client_error(self, client: DeepPCBClient) -> None:
        board_id = "conflict-id"
        respx.patch(f"{_BASE_URL}/api/v1/boards/{board_id}/confirm").mock(
            return_value=httpx.Response(
                409, text='{"errorMessage": "Board already confirmed"}'
            )
        )

        with pytest.raises(DeepPCBClientError) as exc_info:
            client.confirm_board(
                board_id, ConfirmBoardRequest(job_type=JobType.ROUTING)
            )
        assert exc_info.value.status_code == 409
