"""Log WebSocket routes for streaming build and test logs."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from atopile.data_models import Log
from atopile.logging import get_logger, read_build_logs

log = get_logger(__name__)

router = APIRouter(tags=["logs"])

STREAM_POLL_INTERVAL = 0.25


def _parse_filter_params(
    query: Log.BuildStreamQuery | Log.TestStreamQuery,
) -> tuple[list[str] | None, str | None]:
    levels = [str(level) for level in query.log_levels] if query.log_levels else None
    audience = str(query.audience) if query.audience else None
    return levels, audience


async def _push_build_stream(
    websocket: WebSocket,
    query: Log.BuildStreamQuery,
    after_id: int,
) -> int:
    levels, audience = _parse_filter_params(query)
    logs, new_last_id = read_build_logs(
        build_id=query.build_id,
        stage=query.stage,
        log_levels=levels,
        audience=audience,
        after_id=after_id,
        count=query.count,
        order="ASC",
        include_id=True,
    )
    if not logs:
        return after_id

    await websocket.send_json(
        Log.StreamResult(
            type="logs_stream",
            build_id=query.build_id,
            test_run_id="",
            stage=query.stage,
            logs=[Log.StreamEntryPydantic.model_validate(entry) for entry in logs],
            last_id=new_last_id,
        ).model_dump()
    )
    return new_last_id


async def _push_test_stream(
    websocket: WebSocket,
    query: Log.TestStreamQuery,
    after_id: int,
) -> int:
    from atopile.model.sqlite import TestLogs

    levels, audience = _parse_filter_params(query)
    logs, new_last_id = TestLogs.fetch_chunk(
        query.test_run_id,
        test_name=query.test_name,
        levels=levels,
        audience=audience,
        after_id=after_id,
        count=query.count,
        order="ASC",
    )
    if not logs:
        return after_id

    await websocket.send_json(
        Log.StreamResult(
            type="test_logs_stream",
            build_id="",
            test_run_id=query.test_run_id,
            test_name=query.test_name,
            logs=[Log.StreamEntryPydantic.model_validate(entry) for entry in logs],
            last_id=new_last_id,
        ).model_dump()
    )
    return new_last_id


@router.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket) -> None:
    await websocket.accept()
    log.info("Logs WebSocket client connected")

    stream_query: Log.BuildStreamQuery | Log.TestStreamQuery | None = None
    last_id = 0
    is_test_mode = False

    try:
        while True:
            try:
                if stream_query is not None:
                    data = await asyncio.wait_for(
                        websocket.receive_json(), timeout=STREAM_POLL_INTERVAL
                    )
                else:
                    data = await websocket.receive_json()
            except asyncio.TimeoutError:
                if stream_query is None:
                    continue
                if is_test_mode:
                    last_id = await _push_test_stream(
                        websocket,
                        stream_query,
                        last_id,
                    )
                else:
                    last_id = await _push_build_stream(
                        websocket,
                        stream_query,
                        last_id,
                    )
                continue

            if data.get("unsubscribe"):
                stream_query = None
                last_id = 0
                log.debug("Client unsubscribed from log streaming")
                continue

            has_build_id = "build_id" in data
            has_test_run_id = "test_run_id" in data
            if has_build_id and has_test_run_id:
                await websocket.send_json(
                    Log.Error(
                        error="Cannot specify both build_id and test_run_id"
                    ).model_dump()
                )
                continue

            if has_test_run_id:
                is_test_mode = True
                try:
                    stream_query = Log.TestStreamQuery.model_validate(data)
                except ValidationError as exc:
                    await websocket.send_json(Log.Error(error=str(exc)).model_dump())
                    continue

                last_id = stream_query.after_id
                log.debug(
                    "Client subscribed to test logs: %s",
                    stream_query.test_run_id,
                )
                last_id = await _push_test_stream(websocket, stream_query, last_id)
                continue

            is_test_mode = False
            try:
                stream_query = Log.BuildStreamQuery.model_validate(data)
            except ValidationError as exc:
                await websocket.send_json(Log.Error(error=str(exc)).model_dump())
                continue

            last_id = stream_query.after_id
            log.debug(
                "Client subscribed to build logs: %s",
                stream_query.build_id,
            )
            last_id = await _push_build_stream(websocket, stream_query, last_id)
    except WebSocketDisconnect:
        log.info("Logs WebSocket client disconnected")
    except Exception as exc:
        log.exception("Logs WebSocket error: %s", exc)
        raise
