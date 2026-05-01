import logging
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

from atopile.data_models import LogRow
from atopile.logging import AtoLogger, DBLogHandler
from atopile.model import sqlite as sqlite_module

pytestmark = [
    pytest.mark.ato_logging(kind=None, reset_root=True),
]


def _make_test_db_logger(
    captured: list[LogRow],
    *,
    identifier: str,
    context: str,
    logger_name_prefix: str,
):
    return AtoLogger._make_db_logger(
        identifier=identifier,
        context=context,
        writer=lambda rows: captured.extend(rows),
        row_class=LogRow,
        id_field="build_id",
        context_field="stage",
        logger_name=f"{logger_name_prefix}.{uuid.uuid4().hex}",
    )


@contextmanager
def _db_handler_context():
    root = logging.getLogger()
    db_handler = DBLogHandler(level=logging.DEBUG)
    root.addHandler(db_handler)
    root.setLevel(logging.DEBUG)
    try:
        yield
    finally:
        root.removeHandler(db_handler)


def test_non_db_logger_routes_to_active_db_context():
    captured: list[LogRow] = []
    active_logger = _make_test_db_logger(
        captured,
        identifier="build-1",
        context="stage-a",
        logger_name_prefix="atopile.db.test.active",
    )
    active_logger.setLevel(logging.INFO)

    AtoLogger._active_build_logger = active_logger
    AtoLogger._active_test_logger = None

    with _db_handler_context():
        plain = logging.getLogger(f"thirdparty.test.{uuid.uuid4().hex}")
        plain.setLevel(logging.INFO)
        plain.info("from third-party logger")
        active_logger.db_flush()

    assert len(captured) >= 1
    row = captured[-1]
    assert row.build_id == "build-1"
    assert row.stage == "stage-a"
    assert row.logger_name == plain.name
    assert row.message == "from third-party logger"


def test_db_handler_raises_with_multiple_active_contexts():
    captured: list[LogRow] = []
    build_logger = _make_test_db_logger(
        captured,
        identifier="build-x",
        context="build-stage",
        logger_name_prefix="atopile.db.test.multictx.build",
    )
    unscoped_logger = _make_test_db_logger(
        captured,
        identifier="",
        context="unscoped-stage",
        logger_name_prefix="atopile.db.test.multictx.unscoped",
    )
    test_logger = _make_test_db_logger(
        captured,
        identifier="test-x",
        context="test-stage",
        logger_name_prefix="atopile.db.test.multictx.test",
    )

    AtoLogger._active_build_logger = build_logger
    AtoLogger._active_test_logger = test_logger
    AtoLogger._active_unscoped_logger = unscoped_logger
    with _db_handler_context():
        failing_logger = logging.getLogger(f"multictx.test.{uuid.uuid4().hex}")
        failing_logger.setLevel(logging.INFO)
        with pytest.raises(
            RuntimeError,
            match="Build and test DB logging contexts active simultaneously",
        ):
            failing_logger.info("should fail")


def test_source_file_reports_original_callsite():
    captured: list[LogRow] = []
    logger = _make_test_db_logger(
        captured,
        identifier="",
        context="source",
        logger_name_prefix="atopile.db.test.source",
    )
    logger.setLevel(logging.INFO)

    AtoLogger._active_build_logger = AtoLogger._active_test_logger = None
    AtoLogger._active_unscoped_logger = logger

    with _db_handler_context():
        logger.info("callsite check")
        logger.db_flush()

    assert len(captured) >= 1
    rows = [r for r in captured if r.message == "callsite check"]
    assert rows
    for row in rows:
        assert row.source_file is not None
        assert Path(row.source_file).name == "test_logging_db.py"
        assert row.source_line is not None


def test_get_connection_opens_fresh_connection_each_time(tmp_path, monkeypatch):
    class FakeConnection:
        def __init__(self) -> None:
            self.closed = False
            self.executed: list[str] = []

        def execute(self, sql: str):
            self.executed.append(sql)
            return None

        def commit(self) -> None:
            return None

        def rollback(self) -> None:
            return None

        def close(self) -> None:
            self.closed = True

    created: list[FakeConnection] = []

    def fake_connect(*_args, **_kwargs):
        conn = FakeConnection()
        created.append(conn)
        return conn

    monkeypatch.setattr(sqlite_module, "LOG_DBS", ())
    monkeypatch.setattr(sqlite_module.sqlite3, "connect", fake_connect)

    db_path = tmp_path / "logs.db"
    with sqlite_module._get_connection(db_path):
        assert len(created) == 1
        assert created[0].closed is False

    assert created[0].closed is True

    with sqlite_module._get_connection(db_path):
        assert len(created) == 2
        assert created[1].closed is False

    assert created[1].closed is True
    assert [conn.executed for conn in created] == [
        ["PRAGMA journal_mode=WAL"],
        ["PRAGMA journal_mode=WAL"],
    ]


def test_delete_log_storage_unlinks_sqlite_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(sqlite_module, "get_log_dir", lambda: tmp_path / "log-dir")
    db_path = tmp_path / "logs.db"
    artifacts = (
        db_path,
        db_path.with_name(f"{db_path.name}-wal"),
        db_path.with_name(f"{db_path.name}-shm"),
    )
    for artifact in artifacts:
        artifact.write_text("")

    sqlite_module.delete_log_storage((db_path,))

    assert not any(artifact.exists() for artifact in artifacts)


def test_init_db_resets_only_its_own_stale_db(tmp_path, monkeypatch):
    build_history_db = tmp_path / "build_history.db"
    build_logs_db = tmp_path / "build_logs.db"

    with sqlite_module._get_connection(build_history_db) as conn:
        conn.execute("PRAGMA user_version = 1")
    with sqlite_module._get_connection(build_logs_db) as conn:
        conn.execute(f"PRAGMA user_version = {sqlite_module.LOG_VER}")

    monkeypatch.setattr(sqlite_module, "BUILD_HISTORY_DB", build_history_db)
    monkeypatch.setattr(sqlite_module, "BUILD_LOGS_DB", build_logs_db)

    sqlite_module.BuildHistory.init_db()

    assert build_logs_db.exists()
