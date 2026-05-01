"""Typed client for the DeepPCB V1 API."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import httpx

from .exceptions import DeepPCBClientError, DeepPCBServerError
from .models import (
    ApiBoardDto,
    ApiUserCreditFlowDto,
    BoardCheckedDto,
    BoardInputType,
    BoardWithRevisionsDto,
    ConfirmBoardRequest,
    ConstraintsResponseDto,
    CreateBoardRequest,
    JobType,
    ResumeBoardRequest,
    RoutingType,
)

_API_V1 = "/api/v1"


def _raise_for_status(response: httpx.Response) -> None:
    """Raise a typed exception for non-2xx responses."""
    if response.is_success:
        return
    body = response.text
    if 400 <= response.status_code < 500:
        raise DeepPCBClientError(response.status_code, body)
    if response.status_code >= 500:
        raise DeepPCBServerError(response.status_code, body)
    # Shouldn't happen, but cover 3xx etc.
    raise DeepPCBClientError(response.status_code, body)


class DeepPCBClient:
    """Typed client for the DeepPCB V1 API."""

    def __init__(
        self,
        *,
        auth_token: str,
        base_url: str = "https://api.deeppcb.ai",
        timeout: float = 60.0,
    ):
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {auth_token}"},
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> DeepPCBClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # File uploads
    # ------------------------------------------------------------------

    def upload_board_file(self, file_path: Path) -> str:
        """Upload a board file, return the storage URL."""
        with open(file_path, "rb") as f:
            response = self._client.post(
                f"{_API_V1}/files/uploads/board-file",
                files={"inputFile": (file_path.name, f)},
            )
        _raise_for_status(response)
        # API returns the URL as plain text
        return response.text.strip()

    # ------------------------------------------------------------------
    # Board CRUD
    # ------------------------------------------------------------------

    def create_board(self, request: CreateBoardRequest) -> str:
        """Submit a board for routing/placement, return board ID (UUID string)."""
        data = request.model_dump(by_alias=True, exclude_none=True)

        # The API uses multipart/form-data for this endpoint.
        # Non-file fields go as form data.
        response = self._client.post(
            f"{_API_V1}/boards",
            data=data,
        )
        _raise_for_status(response)
        return response.json()

    def create_board_with_file(
        self,
        file_path: Path,
        board_input_type: BoardInputType,
        routing_type: RoutingType | None = None,
        board_name: str | None = None,
        request_id: str | None = None,
    ) -> str:
        """Submit a board with a file upload, return board ID."""
        data: dict[str, str] = {"boardInputType": board_input_type.value}
        if routing_type:
            data["routingType"] = routing_type.value
        if board_name:
            data["boardName"] = board_name
        if request_id:
            data["requestId"] = request_id

        # Map input type to the correct form field name
        field_map = {
            BoardInputType.DSN: "dsnFile",
            BoardInputType.KICAD: "kicadBoardFile",
            BoardInputType.JSON: "jsonFile",
            BoardInputType.ALTIUM: "altiumFile",
            BoardInputType.ZUKEN: "zukenDesignFile",
        }
        field_name = field_map[board_input_type]

        with open(file_path, "rb") as f:
            response = self._client.post(
                f"{_API_V1}/boards",
                data=data,
                files={field_name: (file_path.name, f)},
            )
        _raise_for_status(response)
        return response.json()

    def get_board(self, board_id: str) -> BoardWithRevisionsDto:
        """Get full board info with revisions."""
        return BoardWithRevisionsDto.model_validate(self.get_board_raw(board_id))

    def get_board_raw(self, board_id: str) -> dict:
        """Get full board info as raw JSON.

        Used by the autolayout service when poll-time validation against
        the typed model would discard fields the consumer needs (e.g.
        forwards-compatible workflow keys). Prefer ``get_board`` when
        the typed surface is sufficient.
        """
        response = self._client.get(f"{_API_V1}/boards/{board_id}")
        _raise_for_status(response)
        return response.json()

    def get_board_details(self, board_id: str) -> ApiBoardDto:
        """Get board summary details."""
        response = self._client.get(f"{_API_V1}/boards/{board_id}/details")
        _raise_for_status(response)
        return ApiBoardDto.model_validate(response.json())

    def get_board_json(self, board_id: str) -> str:
        """Get the board JSON representation."""
        response = self._client.get(f"{_API_V1}/boards/{board_id}/json")
        _raise_for_status(response)
        return response.json()

    def get_board_by_request_id(self, request_id: str) -> str:
        """Find a board ID by your request ID."""
        response = self._client.get(f"{_API_V1}/boards/requests/{request_id}")
        _raise_for_status(response)
        return response.json()

    # ------------------------------------------------------------------
    # Board lifecycle
    # ------------------------------------------------------------------

    def confirm_board(self, board_id: str, request: ConfirmBoardRequest) -> None:
        """Start processing a board."""
        response = self._client.patch(
            f"{_API_V1}/boards/{board_id}/confirm",
            json=request.model_dump(by_alias=True, exclude_none=True, mode="json"),
        )
        _raise_for_status(response)

    def stop_board(self, board_id: str) -> None:
        """Stop processing a board."""
        response = self._client.patch(f"{_API_V1}/boards/{board_id}/stop")
        _raise_for_status(response)

    def resume_board(self, board_id: str, request: ResumeBoardRequest) -> None:
        """Resume processing a board."""
        response = self._client.patch(
            f"{_API_V1}/boards/{board_id}/resume",
            json=request.model_dump(by_alias=True, exclude_none=True, mode="json"),
        )
        _raise_for_status(response)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def check_board(
        self,
        json_file_url: str | None = None,
        json_file_path: Path | None = None,
        job_type: JobType | None = None,
        routing_type: RoutingType | None = None,
    ) -> BoardCheckedDto:
        """Pre-validate a board. Provide either a URL or a file path, not both."""
        if json_file_url and json_file_path:
            raise ValueError("Provide either json_file_url or json_file_path, not both")
        if not json_file_url and not json_file_path:
            raise ValueError("Provide either json_file_url or json_file_path")

        data: dict[str, str] = {}
        files: dict[str, tuple[str, bytes]] | None = None

        if json_file_url:
            data["jsonFileUrl"] = json_file_url
        if job_type:
            data["jobType"] = job_type.value
        if routing_type:
            data["routingType"] = routing_type.value

        if json_file_path:
            file_bytes = json_file_path.read_bytes()
            files = {"jsonFile": (json_file_path.name, file_bytes)}

        response = self._client.post(
            f"{_API_V1}/boards/check-board",
            data=data,
            files=files,
        )
        _raise_for_status(response)
        return BoardCheckedDto.model_validate(response.json())

    def check_constraints(
        self,
        constraints_file_url: str | None = None,
        constraints_file_path: Path | None = None,
    ) -> ConstraintsResponseDto:
        """Validate constraint specs. Provide URL or path, not both."""
        if constraints_file_url and constraints_file_path:
            raise ValueError(
                "Provide either constraints_file_url or constraints_file_path, not both"
            )
        if not constraints_file_url and not constraints_file_path:
            raise ValueError(
                "Provide either constraints_file_url or constraints_file_path"
            )

        data: dict[str, str] = {}
        files: dict[str, tuple[str, bytes]] | None = None

        if constraints_file_url:
            data["constraintsFileUrl"] = constraints_file_url
        if constraints_file_path:
            file_bytes = constraints_file_path.read_bytes()
            files = {"constraintsJsonFile": (constraints_file_path.name, file_bytes)}

        response = self._client.post(
            f"{_API_V1}/boards/check-constraints",
            data=data,
            files=files,
        )
        _raise_for_status(response)
        return ConstraintsResponseDto.model_validate(response.json())

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------

    def convert_to_json(
        self,
        file_path: Path,
        board_input_type: BoardInputType,
        routing_type: RoutingType | None = None,
        job_type: JobType | None = None,
    ) -> str:
        """Convert an uploaded board file to JSON. Returns the JSON string."""
        data: dict[str, str] = {"boardInputType": board_input_type.value}
        if routing_type:
            data["routingType"] = routing_type.value
        if job_type:
            data["jobType"] = job_type.value

        field_map = {
            BoardInputType.DSN: "dsnFile",
            BoardInputType.KICAD: "kicadBoardFile",
            BoardInputType.JSON: "jsonFile",
            BoardInputType.ALTIUM: "altiumFile",
            BoardInputType.ZUKEN: "zukenDesignFile",
        }
        field_name = field_map[board_input_type]

        with open(file_path, "rb") as f:
            response = self._client.post(
                f"{_API_V1}/boards/convert-to-json",
                data=data,
                files={field_name: (file_path.name, f)},
            )
        _raise_for_status(response)
        return response.json()

    # ------------------------------------------------------------------
    # Artifacts
    # ------------------------------------------------------------------

    def download_revision_artifact(
        self,
        board_id: str,
        revision: int | None = None,
        artifact_type: str | None = None,
        revision_type: str | None = None,
        include_rats_nest: bool = True,
    ) -> str:
        """Download a revision artifact as a string."""
        params: dict[str, str | int | bool] = {
            "includeRatsNest": include_rats_nest,
        }
        if revision is not None:
            params["revision"] = revision
        if artifact_type:
            params["type"] = artifact_type
        if revision_type:
            params["revisionType"] = revision_type

        response = self._client.get(
            f"{_API_V1}/boards/{board_id}/revision-artifact",
            params=params,
        )
        _raise_for_status(response)
        return response.text

    def download_artifact(
        self,
        board_id: str,
        artifact_type: str | None = None,
        include_rats_nest: bool = False,
    ) -> bytes:
        """Download the board artifact (binary)."""
        params: dict[str, str | bool] = {"includeRatsNest": include_rats_nest}
        if artifact_type:
            params["type"] = artifact_type

        response = self._client.get(
            f"{_API_V1}/boards/{board_id}/download-artifact",
            params=params,
        )
        _raise_for_status(response)
        return response.content

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def get_recommended_max_batch_timeout(self, board_id: str) -> int:
        """Get recommended max batch timeout for a board."""
        response = self._client.get(
            f"{_API_V1}/boards/{board_id}/recommended-max-batch-timeout"
        )
        _raise_for_status(response)
        return response.json()

    def get_board_schema(self) -> dict:
        """Retrieve the board JSON schema."""
        response = self._client.get(f"{_API_V1}/boards/board-schema")
        _raise_for_status(response)
        return response.json()

    def get_constraints_schema(self) -> dict:
        """Retrieve the constraints JSON schema."""
        response = self._client.get(f"{_API_V1}/boards/constraints-schema")
        _raise_for_status(response)
        return response.json()

    def get_workflow_constraints(self, board_id: str, workflow_id: str) -> dict:
        """Extract board constraints for a workflow."""
        response = self._client.get(
            f"{_API_V1}/boards/{board_id}/workflow/{workflow_id}/constraints"
        )
        _raise_for_status(response)
        return response.json()

    def submit_metadata(self, board_id: str, metadata: dict) -> None:
        """Submit a board-related metadata event."""
        response = self._client.post(
            f"{_API_V1}/boards/{board_id}/metadata",
            json=metadata,
        )
        _raise_for_status(response)

    # ------------------------------------------------------------------
    # Credit flow
    # ------------------------------------------------------------------

    def get_credit_flow(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> ApiUserCreditFlowDto:
        """Get the user's credit flow for a given period."""
        params: dict[str, str] = {}
        if start_date:
            params["StartDate"] = start_date.isoformat()
        if end_date:
            params["EndDate"] = end_date.isoformat()

        response = self._client.get(
            f"{_API_V1}/apiuser/credit-flow",
            params=params,
        )
        _raise_for_status(response)
        return ApiUserCreditFlowDto.model_validate(response.json())
