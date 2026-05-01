from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from atopile.data_models import (
    AgentEventRow,
    Build,
    BuildStatus,
    LogRow,
    ResolvedBuildTarget,
    TestLogRow,
)
from atopile.logging import get_logger
from faebryk.libs.paths import get_log_dir
from faebryk.libs.util import robustly_rm_dir

LOG_VER = 3

BUILD_HISTORY_DB = get_log_dir() / Path("build_history.db")
TEST_LOGS_DB = get_log_dir() / Path("test_logs.db")
BUILD_LOGS_DB = get_log_dir() / Path("build_logs.db")
AGENT_LOGS_DB = get_log_dir() / Path("agent_logs.db")

LOG_DBS = (BUILD_HISTORY_DB, TEST_LOGS_DB, BUILD_LOGS_DB, AGENT_LOGS_DB)

# Soft FIFO cap. File size can briefly exceed this between trim passes and
# stays pinned at its high-water mark (no VACUUM); freed pages are reused by
# future inserts so the file stops growing past the cap in steady state.
LOG_DB_CAP_BYTES = 10 * 1024**3
_TRIM_CHUNK_ROWS = 10_000

logger = get_logger(__name__)


def _db_size_bytes(conn: sqlite3.Connection) -> int:
    # Live (non-freelist) bytes. We intentionally don't VACUUM, so page_count
    # never shrinks after a delete — use freelist_count to measure actual
    # data in use instead of file size on disk.
    page_count = conn.execute("PRAGMA page_count").fetchone()[0]
    freelist_count = conn.execute("PRAGMA freelist_count").fetchone()[0]
    page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    return (page_count - freelist_count) * page_size


def _enforce_size_cap(conn: sqlite3.Connection, table: str) -> None:
    if _db_size_bytes(conn) <= LOG_DB_CAP_BYTES:
        return

    logger.warning(
        "%s exceeded %.1f GiB cap; trimming oldest rows",
        table,
        LOG_DB_CAP_BYTES / 1024**3,
    )
    while _db_size_bytes(conn) > LOG_DB_CAP_BYTES:
        cursor = conn.execute(
            f"DELETE FROM {table} WHERE id IN ("
            f"SELECT id FROM {table} ORDER BY id ASC LIMIT ?)",
            (_TRIM_CHUNK_ROWS,),
        )
        if cursor.rowcount == 0:
            break


def _sqlite_artifact_paths(db_path: Path) -> tuple[Path, ...]:
    return (
        db_path,
        db_path.with_name(f"{db_path.name}-wal"),
        db_path.with_name(f"{db_path.name}-shm"),
    )


def _reset_log_storage_if_schema_mismatched(db_path: Path) -> None:
    if not db_path.exists():
        return

    try:
        with closing(sqlite3.connect(db_path)) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.DatabaseError:
        delete_log_storage((db_path,))
        return
    if version != LOG_VER:
        delete_log_storage((db_path,))
        return


def delete_log_storage(db_paths: tuple[Path, ...] | None = None) -> None:
    for db_path in db_paths or LOG_DBS:
        for artifact in _sqlite_artifact_paths(db_path):
            try:
                artifact.unlink(missing_ok=True)
            except PermissionError as exc:
                raise RuntimeError(
                    f"Could not reset locked log database: {artifact}. "
                    "Close other atopile processes and try again."
                ) from exc

    log_dir = get_log_dir()
    if log_dir.exists() and not any(log_dir.iterdir()):
        robustly_rm_dir(log_dir)


def initialize_log_storage() -> None:
    BuildHistory.init_db()
    Logs.init_db()
    TestLogs.init_db()
    AgentSessions.init_db()
    AgentLogs.init_db()


@contextmanager
def _get_connection(
    db_path: Path, timeout: float = 30.0
) -> Iterator[sqlite3.Connection]:
    """Open a fresh connection for each access and close it on exit."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=timeout)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def _get_init_connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Open a connection for init_db and warn if init creates a new empty DB."""
    _reset_log_storage_if_schema_mismatched(db_path)
    created_empty_db = not db_path.exists()
    with _get_connection(db_path) as conn:
        yield conn
    if created_empty_db:
        logger.warning("Created new empty SQLite database during init: %s", db_path)


# build_history.db -> build_history schema helper
class BuildHistory:
    @staticmethod
    def init_db() -> None:
        with _get_init_connection(BUILD_HISTORY_DB) as conn:
            conn.executescript(f"""
                CREATE TABLE IF NOT EXISTS build_history (
                    build_id         TEXT PRIMARY KEY,
                    name             TEXT,
                    project_name     TEXT,
                    project_root     TEXT,
                    target           TEXT NOT NULL,
                    status           TEXT,
                    return_code      INTEGER,
                    error            TEXT,
                    started_at       REAL,
                    elapsed_seconds  REAL,
                    stages           TEXT,
                    total_stages     INTEGER,
                    warnings         INTEGER,
                    errors           INTEGER,
                    standalone       INTEGER,
                    frozen           INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_build_history_project_name
                    ON build_history(project_root, name, started_at DESC);
                PRAGMA user_version = {LOG_VER};
            """)

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Build:
        build = Build(
            build_id=row["build_id"],
            project_name=row["project_name"],
            project_root=row["project_root"],
            target=ResolvedBuildTarget.model_validate_json(row["target"]),
            status=BuildStatus(row["status"]),
            return_code=row["return_code"],
            error=row["error"],
            started_at=row["started_at"],
            elapsed_seconds=row["elapsed_seconds"] or 0.0,
            stages=json.loads(row["stages"]) if row["stages"] else [],
            total_stages=row["total_stages"],
            warnings=row["warnings"],
            errors=row["errors"],
            standalone=bool(row["standalone"]),
            frozen=bool(row["frozen"]),
        )
        if row["name"]:
            build.name = row["name"]
        return build

    @staticmethod
    def set(build: Build) -> None:
        """
        Persist a Build record to the history database.

        Merges with existing record - None values preserve existing data.
        This allows partial updates without losing previously set fields.
        """
        try:
            with _get_connection(BUILD_HISTORY_DB) as conn:
                conn.row_factory = sqlite3.Row
                # Get existing record to merge with
                existing_row = conn.execute(
                    "SELECT * FROM build_history WHERE build_id = ?",
                    (build.build_id,),
                ).fetchone()
                existing = (
                    BuildHistory._from_row(existing_row) if existing_row else None
                )

                # Don't allow a non-terminal status to overwrite a terminal one.
                # This prevents race conditions where a worker's stage update
                # (status=BUILDING) overwrites a CANCELLED/FAILED status.
                _TERMINAL = {"cancelled", "failed", "success", "warning"}
                if (
                    existing
                    and existing.status.value in _TERMINAL
                    and build.status.value not in _TERMINAL
                ):
                    return

                # Helper to pick new value or fall back to existing
                def pick(new_val, existing_val):
                    return new_val if new_val is not None else existing_val

                conn.execute(
                    """
                    INSERT OR REPLACE INTO build_history
                        (build_id, name, project_name,
                         project_root, target, status,
                         return_code, error, started_at,
                         elapsed_seconds, stages, total_stages, warnings,
                         errors, standalone, frozen)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        build.build_id,
                        pick(build.name, existing.name if existing else None),
                        pick(
                            build.project_name,
                            existing.project_name if existing else None,
                        ),
                        pick(
                            build.project_root,
                            existing.project_root if existing else None,
                        ),
                        pick(
                            build.target.model_dump_json(by_alias=True),
                            existing.target.model_dump_json(by_alias=True)
                            if existing
                            else None,
                        ),
                        build.status.value,  # status is always set
                        pick(
                            build.return_code,
                            existing.return_code if existing else None,
                        ),
                        pick(build.error, existing.error if existing else None),
                        pick(
                            build.started_at, existing.started_at if existing else None
                        ),
                        build.elapsed_seconds
                        if build.elapsed_seconds
                        else (existing.elapsed_seconds if existing else 0.0),
                        json.dumps(
                            [
                                stage.model_dump(mode="json", by_alias=True)
                                for stage in (
                                    build.stages
                                    or (existing.stages if existing else [])
                                )
                            ]
                        ),
                        pick(
                            build.total_stages,
                            existing.total_stages if existing else None,
                        ),
                        build.warnings
                        if build.warnings
                        else (existing.warnings if existing else 0),
                        build.errors
                        if build.errors
                        else (existing.errors if existing else 0),
                        int(bool(build.standalone))
                        if build.standalone
                        else (int(bool(existing.standalone)) if existing else 0),
                        int(bool(build.frozen))
                        if build.frozen
                        else (int(bool(existing.frozen)) if existing else 0),
                    ),
                )
        except Exception:
            logger.exception(
                f"Failed to save build {build.build_id} to history. "
                "Try running 'ato dev clear-logs'."
            )
            raise

    @staticmethod
    def get(build_id: str) -> Build | None:
        """Get a build by ID. Returns None if missing, exits on error."""
        try:
            with _get_connection(BUILD_HISTORY_DB) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM build_history WHERE build_id = ?",
                    (build_id,),
                ).fetchone()
                if row is None:
                    return None
                return BuildHistory._from_row(row)
        except Exception:
            logger.exception(
                f"Failed to get build {build_id} from history. "
                "Try running 'ato dev clear-logs'."
            )
            raise

    @staticmethod
    def get_all(limit: int = 50) -> list[Build]:
        """Get recent builds. Raises on error so callers can handle gracefully."""
        try:
            with _get_connection(BUILD_HISTORY_DB) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM build_history ORDER BY started_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                return [BuildHistory._from_row(r) for r in rows]
        except Exception as e:
            logger.exception(
                "Failed to load build history. Try running 'ato dev clear-logs'."
            )
            raise e

    @staticmethod
    def get_latest_finished_per_target(limit: int = 100) -> list[Build]:
        """Get the latest completed build per target root and target name."""
        try:
            with _get_connection(BUILD_HISTORY_DB) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT * FROM build_history
                    WHERE rowid IN (
                        SELECT rowid FROM (
                            SELECT rowid, ROW_NUMBER() OVER (
                                PARTITION BY
                                    json_extract(target, '$.root'),
                                    name,
                                    json_extract(target, '$.entry')
                                ORDER BY started_at DESC
                            ) AS rn
                            FROM build_history
                            WHERE status NOT IN ('queued', 'building')
                        ) WHERE rn = 1
                    )
                    ORDER BY started_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                return [BuildHistory._from_row(r) for r in rows]
        except Exception as e:
            logger.exception(
                "Failed to get latest finished builds per target. "
                "Try running 'ato dev clear-logs'."
            )
            raise e

    @staticmethod
    def get_queued(limit: int = 100) -> list[Build]:
        """Get queued builds in FIFO order."""
        try:
            with _get_connection(BUILD_HISTORY_DB) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT * FROM build_history
                    WHERE status = 'queued'
                    ORDER BY started_at ASC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                return [BuildHistory._from_row(r) for r in rows]
        except Exception as e:
            logger.exception(
                "Failed to get queued builds. Try running 'ato dev clear-logs'."
            )
            raise e

    @staticmethod
    def cleanup_stale() -> int:
        """Mark leftover 'building'/'queued' entries as failed.

        Called at server startup to clear entries from a previous crash.
        Returns the number of rows updated.
        """
        try:
            with _get_connection(BUILD_HISTORY_DB) as conn:
                cursor = conn.execute(
                    """
                    UPDATE build_history
                    SET status = 'failed',
                        error = 'Server restarted while build was in progress'
                    WHERE status IN ('queued', 'building')
                    """,
                )
                count = cursor.rowcount
                if count:
                    logger.info(
                        "Cleaned up %d stale build(s) from previous session", count
                    )
                return count
        except Exception as e:
            logger.warning("Failed to clean up stale builds: %s", e)
            return 0

    @staticmethod
    def get_building(limit: int = 100) -> list[Build]:
        """Get running builds."""
        try:
            with _get_connection(BUILD_HISTORY_DB) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT * FROM build_history
                    WHERE status = 'building'
                    ORDER BY started_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                return [BuildHistory._from_row(r) for r in rows]
        except Exception as e:
            logger.exception(
                "Failed to get running builds. Try running 'ato dev clear-logs'."
            )
            raise e

    @staticmethod
    def get_finished(limit: int = 100) -> list[Build]:
        """Get builds with status other than 'queued' or 'building'."""
        try:
            with _get_connection(BUILD_HISTORY_DB) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT * FROM build_history
                    WHERE status NOT IN ('queued', 'building')
                    ORDER BY started_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                return [BuildHistory._from_row(r) for r in rows]
        except Exception as e:
            logger.exception(
                "Failed to get finished builds. Try running 'ato dev clear-logs'."
            )
            raise e

    @staticmethod
    def get_latest_finished_for_target(
        target: ResolvedBuildTarget,
    ) -> Build | None:
        """Get the most recent completed build for a specific project/target."""
        try:
            with _get_connection(BUILD_HISTORY_DB) as conn:
                conn.row_factory = sqlite3.Row
                params: list[str] = [
                    target.name,
                    target.root,
                ]
                query = (
                    "SELECT * FROM build_history"
                    " WHERE name = ?"
                    " AND json_extract(target, '$.root') = ?"
                    " AND status NOT IN ('queued', 'building')"
                )
                query += " ORDER BY started_at DESC LIMIT 1"
                row = conn.execute(query, params).fetchone()
                if row is None:
                    return None
                return BuildHistory._from_row(row)
        except Exception as e:
            logger.exception(
                "Failed to get latest finished build for target. "
                "Try running 'ato dev clear-logs'."
            )
            raise e


# build_logs.db -> logs table helper
class Logs:
    @staticmethod
    def init_db() -> None:
        with _get_init_connection(BUILD_LOGS_DB) as conn:
            conn.executescript(f"""
                CREATE TABLE IF NOT EXISTS logs (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    build_id          TEXT,
                    timestamp         TEXT,
                    stage             TEXT,
                    level             TEXT,
                    message           TEXT,
                    logger_name       TEXT,
                    audience          TEXT DEFAULT 'developer',
                    source_file       TEXT,
                    source_line       INTEGER,
                    ato_traceback     TEXT,
                    python_traceback  TEXT,
                    objects           TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_logs_build_id ON logs(build_id);
                PRAGMA user_version = {LOG_VER};
            """)

    @staticmethod
    def _from_row(row: sqlite3.Row) -> dict[str, Any]:
        obj = None
        if row["objects"]:
            try:
                obj = json.loads(row["objects"])
            except json.JSONDecodeError:
                pass
        return {
            "id": row["id"],
            "timestamp": row["timestamp"],
            "stage": row["stage"],
            "level": row["level"],
            "audience": row["audience"],
            "logger_name": row["logger_name"],
            "message": row["message"],
            "source_file": row["source_file"],
            "source_line": row["source_line"],
            "ato_traceback": row["ato_traceback"],
            "python_traceback": row["python_traceback"],
            "objects": obj,
        }

    @staticmethod
    def append_chunk(entries: list[LogRow]) -> None:
        if not entries:
            return
        with _get_connection(BUILD_LOGS_DB) as conn:
            conn.executemany(
                """
                INSERT INTO logs
                    (build_id, timestamp, stage, level, message,
                     logger_name, audience, source_file, source_line,
                     ato_traceback, python_traceback, objects)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        e.build_id,
                        e.timestamp,
                        e.stage,
                        e.level,
                        e.message,
                        e.logger_name,
                        e.audience,
                        e.source_file,
                        e.source_line,
                        e.ato_traceback,
                        e.python_traceback,
                        e.objects,
                    )
                    for e in entries
                ],
            )
            _enforce_size_cap(conn, "logs")

    @staticmethod
    def fetch_chunk(
        build_id: str,
        *,
        stage: str | None = None,
        levels: list[str] | None = None,
        audience: str | None = None,
        after_id: int = 0,
        count: int = 1000,
        order: str = "ASC",
    ) -> tuple[list[dict[str, Any]], int]:
        if not BUILD_LOGS_DB.exists():
            return [], after_id

        where = ["build_id = ?"]
        params: list[Any] = [build_id]
        if after_id:
            where.append("id > ?")
            params.append(after_id)
        if stage:
            where.append("stage = ?")
            params.append(stage)
        if levels:
            where.append(f"level IN ({','.join('?' * len(levels))})")
            params.extend(levels)
        if audience:
            where.append("audience = ?")
            params.append(audience)
        params.append(min(count, 5000))
        order_dir = "DESC" if order.upper() == "DESC" else "ASC"

        with _get_connection(BUILD_LOGS_DB) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM logs"
                " WHERE " + " AND ".join(where) + f" ORDER BY id {order_dir} LIMIT ?",
                params,
            ).fetchall()
            last_id = after_id
            results = []
            for row in rows:
                last_id = row["id"]
                results.append(Logs._from_row(row))
            return results, last_id


# test_logs.db -> test_logs table helper
class TestLogs:
    @staticmethod
    def init_db() -> None:
        with _get_init_connection(TEST_LOGS_DB) as conn:
            conn.executescript(f"""
                CREATE TABLE IF NOT EXISTS test_runs (
                    test_run_id TEXT PRIMARY KEY,
                    created_at  TEXT
                );
                CREATE TABLE IF NOT EXISTS test_logs (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    test_run_id       TEXT,
                    timestamp         TEXT,
                    test_name         TEXT,
                    level             TEXT,
                    message           TEXT,
                    logger_name       TEXT,
                    audience          TEXT DEFAULT 'developer',
                    source_file       TEXT,
                    source_line       INTEGER,
                    ato_traceback     TEXT,
                    python_traceback  TEXT,
                    objects           TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_test_logs_test_run_id
                    ON test_logs(test_run_id);
                PRAGMA user_version = {LOG_VER};
            """)

    @staticmethod
    def register_run(test_run_id: str) -> None:
        with _get_connection(TEST_LOGS_DB) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO test_runs (test_run_id) VALUES (?)",
                (test_run_id,),
            )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> dict[str, Any]:
        obj = None
        if row["objects"]:
            try:
                obj = json.loads(row["objects"])
            except json.JSONDecodeError:
                pass
        return {
            "id": row["id"],
            "timestamp": row["timestamp"],
            "test_name": row["test_name"],
            "level": row["level"],
            "audience": row["audience"],
            "logger_name": row["logger_name"],
            "message": row["message"],
            "source_file": row["source_file"],
            "source_line": row["source_line"],
            "ato_traceback": row["ato_traceback"],
            "python_traceback": row["python_traceback"],
            "objects": obj,
        }

    @staticmethod
    def append_chunk(entries: list[TestLogRow]) -> None:
        if not entries:
            return
        with _get_connection(TEST_LOGS_DB) as conn:
            conn.executemany(
                """
                INSERT INTO test_logs
                    (test_run_id, timestamp, test_name, level, message,
                     logger_name, audience, source_file, source_line,
                     ato_traceback, python_traceback, objects)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        e.test_run_id,
                        e.timestamp,
                        e.test_name,
                        e.level,
                        e.message,
                        e.logger_name,
                        e.audience,
                        e.source_file,
                        e.source_line,
                        e.ato_traceback,
                        e.python_traceback,
                        e.objects,
                    )
                    for e in entries
                ],
            )
            _enforce_size_cap(conn, "test_logs")

    @staticmethod
    def fetch_chunk(
        test_run_id: str,
        *,
        test_name: str | None = None,
        levels: list[str] | None = None,
        audience: str | None = None,
        after_id: int = 0,
        count: int = 1000,
        order: str = "ASC",
    ) -> tuple[list[dict[str, Any]], int]:
        if not TEST_LOGS_DB.exists():
            return [], after_id

        where = ["test_run_id = ?"]
        params: list[Any] = [test_run_id]
        if after_id:
            where.append("id > ?")
            params.append(after_id)
        if test_name:
            where.append("test_name LIKE ?")
            params.append(f"%{test_name}%")
        if levels:
            where.append(f"level IN ({','.join('?' * len(levels))})")
            params.extend(levels)
        if audience:
            where.append("audience = ?")
            params.append(audience)
        params.append(min(count, 5000))
        order_dir = "DESC" if order.upper() == "DESC" else "ASC"

        with _get_connection(TEST_LOGS_DB) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM test_logs"
                " WHERE " + " AND ".join(where) + f" ORDER BY id {order_dir} LIMIT ?",
                params,
            ).fetchall()
            last_id = after_id
            results = []
            for row in rows:
                last_id = row["id"]
                results.append(TestLogs._from_row(row))
            return results, last_id


# agent_logs.db -> agent_events table helper
class AgentSessions:
    @staticmethod
    def init_db() -> None:
        with _get_init_connection(AGENT_LOGS_DB) as conn:
            conn.executescript(f"""
                CREATE TABLE IF NOT EXISTS agent_sessions_v2 (
                    session_id      TEXT PRIMARY KEY,
                    project_root    TEXT NOT NULL,
                    model           TEXT NOT NULL DEFAULT 'claude-opus-4-7',
                    provider_state  TEXT NOT NULL DEFAULT '{{}}',
                    messages        TEXT NOT NULL DEFAULT '[]',
                    checklist       TEXT NOT NULL DEFAULT '[]',
                    active_skills   TEXT NOT NULL DEFAULT '[]',
                    created_at      REAL NOT NULL,
                    updated_at      REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_sessions_v2_project
                    ON agent_sessions_v2(project_root, updated_at DESC);
                PRAGMA user_version = {LOG_VER};
            """)

    @staticmethod
    def upsert_many(rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        with _get_connection(AGENT_LOGS_DB) as conn:
            conn.executemany(
                """
                INSERT INTO agent_sessions_v2
                    (session_id, project_root, provider_state, messages,
                     checklist, active_skills, created_at, updated_at, model)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    project_root = excluded.project_root,
                    provider_state = excluded.provider_state,
                    messages = excluded.messages,
                    checklist = excluded.checklist,
                    active_skills = excluded.active_skills,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at,
                    model = excluded.model
                """,
                [
                    (
                        row["session_id"],
                        row["project_root"],
                        json.dumps(row.get("provider_state", {}), ensure_ascii=False),
                        json.dumps(row.get("messages", []), ensure_ascii=False),
                        json.dumps(row.get("checklist", []), ensure_ascii=False),
                        json.dumps(row.get("active_skills", []), ensure_ascii=False),
                        float(row.get("created_at", 0.0) or 0.0),
                        float(row.get("updated_at", 0.0) or 0.0),
                        str(row.get("model") or "claude-opus-4-7"),
                    )
                    for row in rows
                ],
            )

    @staticmethod
    def load_all(*, project_root: str | None = None) -> list[dict[str, Any]]:
        if not AGENT_LOGS_DB.exists():
            return []
        with _get_connection(AGENT_LOGS_DB) as conn:
            conn.row_factory = sqlite3.Row
            if project_root is not None:
                rows = conn.execute(
                    "SELECT * FROM agent_sessions_v2"
                    " WHERE project_root = ?"
                    " ORDER BY updated_at DESC",
                    (project_root,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM agent_sessions_v2 ORDER BY updated_at DESC"
                ).fetchall()

        def _decode(raw: str | None, fallback: Any) -> Any:
            if not raw:
                return fallback
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return fallback

        return [
            {
                "session_id": row["session_id"],
                "project_root": row["project_root"],
                "provider_state": _decode(row["provider_state"], {}),
                "messages": _decode(row["messages"], []),
                "checklist": _decode(row["checklist"], []),
                "active_skills": _decode(row["active_skills"], []),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "model": row["model"],
            }
            for row in rows
        ]


class AgentLogs:
    @staticmethod
    def init_db() -> None:
        with _get_init_connection(AGENT_LOGS_DB) as conn:
            conn.executescript(f"""
                CREATE TABLE IF NOT EXISTS agent_events (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id      TEXT NOT NULL,
                    run_id          TEXT,
                    timestamp       TEXT NOT NULL,
                    event           TEXT NOT NULL,
                    level           TEXT NOT NULL DEFAULT 'INFO',
                    phase           TEXT,
                    tool_name       TEXT,
                    project_root    TEXT,
                    summary         TEXT,
                    step_kind       TEXT,
                    loop            INTEGER,
                    tool_index      INTEGER,
                    tool_count      INTEGER,
                    call_id         TEXT,
                    item_id         TEXT,
                    model           TEXT,
                    response_id     TEXT,
                    previous_response_id TEXT,
                    input_tokens    INTEGER,
                    output_tokens   INTEGER,
                    total_tokens    INTEGER,
                    reasoning_tokens INTEGER,
                    cached_input_tokens INTEGER,
                    duration_ms     INTEGER,
                    payload         TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_agent_events_session
                    ON agent_events(session_id, id);
                CREATE INDEX IF NOT EXISTS idx_agent_events_run
                    ON agent_events(run_id, id);
                PRAGMA user_version = {LOG_VER};
            """)
            conn.row_factory = sqlite3.Row
            existing_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(agent_events)").fetchall()
            }
            required_columns = {
                "step_kind": "TEXT",
                "loop": "INTEGER",
                "tool_index": "INTEGER",
                "tool_count": "INTEGER",
                "call_id": "TEXT",
                "item_id": "TEXT",
                "model": "TEXT",
                "response_id": "TEXT",
                "previous_response_id": "TEXT",
                "input_tokens": "INTEGER",
                "output_tokens": "INTEGER",
                "total_tokens": "INTEGER",
                "reasoning_tokens": "INTEGER",
                "cached_input_tokens": "INTEGER",
                "duration_ms": "INTEGER",
            }
            for column, column_type in required_columns.items():
                if column in existing_columns:
                    continue
                conn.execute(
                    f"ALTER TABLE agent_events ADD COLUMN {column} {column_type}"
                )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> dict[str, Any]:
        payload = None
        if row["payload"]:
            try:
                payload = json.loads(row["payload"])
            except json.JSONDecodeError:
                payload = row["payload"]
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "run_id": row["run_id"],
            "timestamp": row["timestamp"],
            "event": row["event"],
            "level": row["level"],
            "phase": row["phase"],
            "tool_name": row["tool_name"],
            "project_root": row["project_root"],
            "summary": row["summary"],
            "step_kind": row["step_kind"] if "step_kind" in row.keys() else None,
            "loop": row["loop"] if "loop" in row.keys() else None,
            "tool_index": row["tool_index"] if "tool_index" in row.keys() else None,
            "tool_count": row["tool_count"] if "tool_count" in row.keys() else None,
            "call_id": row["call_id"] if "call_id" in row.keys() else None,
            "item_id": row["item_id"] if "item_id" in row.keys() else None,
            "model": row["model"] if "model" in row.keys() else None,
            "response_id": row["response_id"] if "response_id" in row.keys() else None,
            "previous_response_id": (
                row["previous_response_id"]
                if "previous_response_id" in row.keys()
                else None
            ),
            "input_tokens": row["input_tokens"]
            if "input_tokens" in row.keys()
            else None,
            "output_tokens": (
                row["output_tokens"] if "output_tokens" in row.keys() else None
            ),
            "total_tokens": row["total_tokens"]
            if "total_tokens" in row.keys()
            else None,
            "reasoning_tokens": (
                row["reasoning_tokens"] if "reasoning_tokens" in row.keys() else None
            ),
            "cached_input_tokens": (
                row["cached_input_tokens"]
                if "cached_input_tokens" in row.keys()
                else None
            ),
            "duration_ms": row["duration_ms"] if "duration_ms" in row.keys() else None,
            "payload": payload,
        }

    @staticmethod
    def append_chunk(entries: list[AgentEventRow]) -> None:
        if not entries:
            return
        with _get_connection(AGENT_LOGS_DB) as conn:
            conn.executemany(
                """
                INSERT INTO agent_events
                    (session_id, run_id, timestamp, event, level,
                     phase, tool_name, project_root, summary,
                     step_kind, loop, tool_index, tool_count, call_id, item_id,
                     model, response_id, previous_response_id,
                     input_tokens, output_tokens, total_tokens,
                     reasoning_tokens, cached_input_tokens,
                     duration_ms, payload)
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?
                )
                """,
                [
                    (
                        e.session_id,
                        e.run_id,
                        e.timestamp,
                        e.event,
                        e.level,
                        e.phase,
                        e.tool_name,
                        e.project_root,
                        e.summary,
                        e.step_kind,
                        e.loop,
                        e.tool_index,
                        e.tool_count,
                        e.call_id,
                        e.item_id,
                        e.model,
                        e.response_id,
                        e.previous_response_id,
                        e.input_tokens,
                        e.output_tokens,
                        e.total_tokens,
                        e.reasoning_tokens,
                        e.cached_input_tokens,
                        e.duration_ms,
                        e.payload,
                    )
                    for e in entries
                ],
            )
            _enforce_size_cap(conn, "agent_events")

    @staticmethod
    def fetch_chunk(
        session_id: str,
        *,
        run_id: str | None = None,
        events: list[str] | None = None,
        levels: list[str] | None = None,
        after_id: int = 0,
        count: int = 1000,
        order: str = "ASC",
    ) -> tuple[list[dict[str, Any]], int]:
        if not AGENT_LOGS_DB.exists():
            return [], after_id

        where = ["session_id = ?"]
        params: list[Any] = [session_id]
        if after_id:
            where.append("id > ?")
            params.append(after_id)
        if run_id:
            where.append("run_id = ?")
            params.append(run_id)
        if events:
            where.append(f"event IN ({','.join('?' * len(events))})")
            params.extend(events)
        if levels:
            where.append(f"level IN ({','.join('?' * len(levels))})")
            params.extend(levels)
        params.append(min(count, 5000))
        order_dir = "DESC" if order.upper() == "DESC" else "ASC"

        with _get_connection(AGENT_LOGS_DB) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM agent_events"
                " WHERE " + " AND ".join(where) + f" ORDER BY id {order_dir} LIMIT ?",
                params,
            ).fetchall()
            last_id = after_id
            results = []
            for row in rows:
                last_id = row["id"]
                results.append(AgentLogs._from_row(row))
            return results, last_id
