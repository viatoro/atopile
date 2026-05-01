"""
Diff RPC session:
handles computePcbDiff / getDiffResult / getGitLog / getFileAtCommit /
getAutolayoutPreviewPath actions.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from atopile.server.domains.diff import DiffService
from atopile.server.domains.diff_models import DiffConfig, DiffResult
from faebryk.libs.git import (
    async_git_log_for_file,
    async_git_show_file_at_commit,
)

log = logging.getLogger(__name__)


if TYPE_CHECKING:
    from atopile.autolayout.service import AutolayoutService

DiffRpcSend = Callable[[dict[str, Any]], Awaitable[None]]


class DiffRpcAction(StrEnum):
    COMPUTE_PCB_DIFF = "computePcbDiff"
    GET_DIFF_RESULT = "getDiffResult"
    GET_GIT_LOG = "getGitLog"
    GET_FILE_AT_COMMIT = "getFileAtCommit"
    GET_AUTOLAYOUT_PREVIEW_PATH = "getAutolayoutPreviewPath"


DIFF_RPC_ACTIONS = frozenset(DiffRpcAction)


class DiffRpcSession:
    def __init__(
        self,
        service: DiffService,
        send: DiffRpcSend,
        autolayout_service: AutolayoutService | None = None,
    ) -> None:
        self._service = service
        self._send = send
        self._autolayout_service = autolayout_service
        self._last_result: DiffResult | None = None
        self._temp_dir: tempfile.TemporaryDirectory[str] | None = None

    @staticmethod
    def handles(action: str) -> bool:
        return action in DIFF_RPC_ACTIONS

    async def dispatch(self, msg: Mapping[str, Any]) -> bool:
        action = str(msg.get("action") or "")
        if not self.handles(action):
            return False

        request_id = str(msg.get("requestId") or "")
        if not request_id:
            raise ValueError(f"{action} requires requestId")

        match DiffRpcAction(action):
            case DiffRpcAction.COMPUTE_PCB_DIFF:
                result = await self._handle_compute_pcb_diff(msg)
            case DiffRpcAction.GET_DIFF_RESULT:
                result = await self._handle_get_diff_result(msg)
            case DiffRpcAction.GET_GIT_LOG:
                result = await self._handle_get_git_log(msg)
            case DiffRpcAction.GET_FILE_AT_COMMIT:
                result = await self._handle_get_file_at_commit(msg)
            case DiffRpcAction.GET_AUTOLAYOUT_PREVIEW_PATH:
                result = await self._handle_get_autolayout_preview_path(msg)

        await self._reply(request_id, action, result)
        return True

    async def _reply(self, request_id: str, action: str, result: Any) -> None:
        await self._send(
            {
                "type": "action_result",
                "requestId": request_id,
                "action": action,
                "ok": True,
                "result": result,
                "error": None,
            }
        )

    async def _handle_compute_pcb_diff(self, msg: Mapping[str, Any]) -> Any:
        path_a = Path(str(msg.get("pathA") or ""))
        path_b = Path(str(msg.get("pathB") or ""))
        log.info(
            "computePcbDiff: pathA=%s pathB=%s force=%s",
            path_a,
            path_b,
            msg.get("force"),
        )

        if not path_a.exists():
            raise FileNotFoundError(f"File not found: {path_a}")
        if not path_b.exists():
            raise FileNotFoundError(f"File not found: {path_b}")

        config = None
        config_data = msg.get("config")
        if config_data and isinstance(config_data, dict):
            config = DiffConfig(**config_data)

        if msg.get("force"):
            self._service.invalidate(path_a, path_b)

        result = await self._service.compute_diff_async(path_a, path_b, config)
        self._last_result = result
        return result.model_dump(mode="json")

    async def _handle_get_diff_result(self, msg: Mapping[str, Any]) -> Any:
        if self._last_result is None:
            raise RuntimeError("No diff result available; call computePcbDiff first")
        return self._last_result.model_dump(mode="json")

    async def _handle_get_git_log(self, msg: Mapping[str, Any]) -> Any:
        file_path = Path(str(msg.get("filePath") or ""))
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        commits = await async_git_log_for_file(file_path)
        return {
            "commits": [
                {
                    "hash": c.long_hash,
                    "shortHash": c.short_hash,
                    "date": c.date,
                    "message": c.message,
                    "authorName": c.author_name,
                }
                for c in commits
            ]
        }

    async def _handle_get_file_at_commit(self, msg: Mapping[str, Any]) -> Any:
        file_path = Path(str(msg.get("filePath") or ""))
        commit_hash = str(msg.get("commitHash") or "")
        if not commit_hash:
            raise ValueError("commitHash is required")

        extracted = await async_git_show_file_at_commit(file_path, commit_hash)
        # Move into session-owned temp dir so cleanup is automatic
        if self._temp_dir is None:
            self._temp_dir = tempfile.TemporaryDirectory(prefix="pcbdiff_session_")
        dest = Path(self._temp_dir.name) / extracted.name
        shutil.move(str(extracted), dest)
        return {"tempPath": str(dest)}

    async def _handle_get_autolayout_preview_path(self, msg: Mapping[str, Any]) -> Any:
        job_id = str(msg.get("jobId") or "")
        candidate_id = str(msg.get("candidateId") or "")
        if not job_id or not candidate_id:
            raise ValueError("jobId and candidateId are required")
        if self._autolayout_service is None:
            raise RuntimeError("Autolayout service not available")

        preview_path = await asyncio.to_thread(
            self._autolayout_service.preview_candidate, job_id, candidate_id
        )
        return {"previewPath": str(preview_path)}

    async def send_error(
        self,
        action: str,
        request_id: str,
        error: str,
    ) -> None:
        await self._send(
            {
                "type": "action_result",
                "requestId": request_id,
                "action": action,
                "ok": False,
                "result": None,
                "error": error,
            }
        )

    async def close(self) -> None:
        self._last_result = None
        if self._temp_dir is not None:
            self._temp_dir.cleanup()
            self._temp_dir = None
