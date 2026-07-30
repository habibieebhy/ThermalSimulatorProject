"""Durable SQLite ledger for cement discovery and capture jobs."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from mattress_intelligence.settings import Settings


ACTIVE_STATUSES = frozenset({"queued", "running"})
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})

STAGE_PROGRESS = {
    "queued": 2,
    "initializing": 5,
    "generating_queries": 10,
    "searching": 20,
    "classifying": 70,
    "grouping_plants": 82,
    "building_bundles": 92,
    "capturing": 15,
    "writing_manifests": 95,
    "completed": 100,
    "failed": 100,
}

_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS cement_jobs (
    job_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE,
    job_type TEXT NOT NULL,
    parent_job_id TEXT,
    request_json TEXT NOT NULL,
    status TEXT NOT NULL,
    stage TEXT NOT NULL,
    progress INTEGER NOT NULL,
    message TEXT,
    submitted_at TEXT NOT NULL,
    started_at TEXT,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    output_dir TEXT NOT NULL,
    result_json_path TEXT,
    error TEXT,
    summary_json TEXT,
    execution_token TEXT,
    heartbeat_at TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS cement_jobs_status_idx
    ON cement_jobs(status, submitted_at DESC);
CREATE INDEX IF NOT EXISTS cement_jobs_parent_idx
    ON cement_jobs(parent_job_id, submitted_at DESC);
"""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class CementJob:
    job_id: str
    task_id: str
    job_type: str
    parent_job_id: str | None
    request_json: str
    status: str
    stage: str
    progress: int
    message: str | None
    submitted_at: datetime
    started_at: datetime | None
    updated_at: datetime
    completed_at: datetime | None
    output_dir: str
    result_json_path: str | None
    error: str | None
    summary_json: str | None
    execution_token: str | None
    heartbeat_at: datetime | None
    attempt_count: int

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "CementJob":
        return cls(
            job_id=str(row["job_id"]),
            task_id=str(row["task_id"]),
            job_type=str(row["job_type"]),
            parent_job_id=(
                str(row["parent_job_id"]) if row["parent_job_id"] is not None else None
            ),
            request_json=str(row["request_json"]),
            status=str(row["status"]),
            stage=str(row["stage"]),
            progress=int(row["progress"]),
            message=str(row["message"]) if row["message"] is not None else None,
            submitted_at=_parse_datetime(row["submitted_at"]) or utc_now(),
            started_at=_parse_datetime(row["started_at"]),
            updated_at=_parse_datetime(row["updated_at"]) or utc_now(),
            completed_at=_parse_datetime(row["completed_at"]),
            output_dir=str(row["output_dir"]),
            result_json_path=(
                str(row["result_json_path"])
                if row["result_json_path"] is not None
                else None
            ),
            error=str(row["error"]) if row["error"] is not None else None,
            summary_json=(
                str(row["summary_json"]) if row["summary_json"] is not None else None
            ),
            execution_token=(
                str(row["execution_token"])
                if row["execution_token"] is not None
                else None
            ),
            heartbeat_at=_parse_datetime(row["heartbeat_at"]),
            attempt_count=int(row["attempt_count"] or 0),
        )

    @property
    def request(self) -> dict[str, Any]:
        value = json.loads(self.request_json)
        return value if isinstance(value, dict) else {}

    @property
    def summary(self) -> dict[str, Any]:
        if not self.summary_json:
            return {}
        value = json.loads(self.summary_json)
        return value if isinstance(value, dict) else {}

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def model_dump(self) -> dict[str, Any]:
        payload = asdict(self)
        for field in (
            "submitted_at",
            "started_at",
            "updated_at",
            "completed_at",
            "heartbeat_at",
        ):
            value = payload[field]
            payload[field] = value.isoformat() if value else None
        payload["request"] = self.request
        payload["summary"] = self.summary
        payload.pop("request_json", None)
        payload.pop("summary_json", None)
        payload.pop("execution_token", None)
        return payload


class CementJobStore:
    """Durable job state with duplicate-delivery execution leases."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as connection:
            connection.executescript(_SCHEMA)
            connection.commit()

    @classmethod
    def from_settings(cls, settings: Settings) -> "CementJobStore":
        return cls(settings.job_database_path)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        return connection

    def create(
        self,
        *,
        job_type: str,
        request: dict[str, Any],
        output_dir: Path,
        parent_job_id: str | None = None,
        job_id: str | None = None,
    ) -> CementJob:
        resolved = job_id or uuid4().hex
        output_dir.mkdir(parents=True, exist_ok=True)
        now = utc_now().isoformat()
        with closing(self.connect()) as connection:
            connection.execute(
                """INSERT INTO cement_jobs (
                    job_id, task_id, job_type, parent_job_id, request_json,
                    status, stage, progress, message, submitted_at, updated_at, output_dir
                ) VALUES (?, ?, ?, ?, ?, 'queued', 'queued', ?, ?, ?, ?, ?)""",
                (
                    resolved,
                    resolved,
                    job_type,
                    parent_job_id,
                    json.dumps(request, separators=(",", ":"), default=str),
                    STAGE_PROGRESS["queued"],
                    "Waiting for a Celery worker",
                    now,
                    now,
                    str(output_dir),
                ),
            )
            connection.commit()
        return self.get(resolved)

    def get(self, job_id: str) -> CementJob:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM cement_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown cement job: {job_id}")
        return CementJob.from_row(row)

    def list(self, *, limit: int = 50) -> list[CementJob]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM cement_jobs ORDER BY submitted_at DESC LIMIT ?",
                (max(1, limit),),
            ).fetchall()
        return [CementJob.from_row(row) for row in rows]

    def update_task_id(self, job_id: str, task_id: str) -> CementJob:
        with closing(self.connect()) as connection:
            connection.execute(
                "UPDATE cement_jobs SET task_id = ?, updated_at = ? WHERE job_id = ?",
                (task_id, utc_now().isoformat(), job_id),
            )
            connection.commit()
        return self.get(job_id)

    def claim(
        self,
        job_id: str,
        *,
        task_id: str,
        execution_token: str,
        stale_after_seconds: int,
    ) -> tuple[CementJob, str]:
        now = utc_now()
        with closing(self.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM cement_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                connection.rollback()
                raise KeyError(f"Unknown cement job: {job_id}")
            current = CementJob.from_row(row)
            if current.status == "completed":
                connection.commit()
                return current, "completed"
            if current.status in {"failed", "cancelled"}:
                connection.commit()
                return current, "terminal"
            lease_time = current.heartbeat_at or current.updated_at
            stale = (
                current.status == "running"
                and current.execution_token is not None
                and (now - lease_time).total_seconds() >= max(60, stale_after_seconds)
            )
            if (
                current.status == "running"
                and current.execution_token is not None
                and not stale
            ):
                connection.commit()
                return current, "already_running"
            connection.execute(
                """UPDATE cement_jobs
                SET task_id = ?, status = 'running', stage = 'initializing',
                    progress = ?, message = 'Initializing cement evidence services',
                    started_at = COALESCE(started_at, ?), updated_at = ?,
                    execution_token = ?, heartbeat_at = ?,
                    attempt_count = attempt_count + 1, error = NULL
                WHERE job_id = ?""",
                (
                    task_id,
                    STAGE_PROGRESS["initializing"],
                    now.isoformat(),
                    now.isoformat(),
                    execution_token,
                    now.isoformat(),
                    job_id,
                ),
            )
            connection.commit()
        return self.get(job_id), "claimed"

    def mark_progress(
        self,
        job_id: str,
        *,
        stage: str,
        message: str,
        progress: int | None = None,
        execution_token: str | None = None,
    ) -> CementJob:
        resolved = max(
            0,
            min(100, progress if progress is not None else STAGE_PROGRESS.get(stage, 50)),
        )
        now = utc_now().isoformat()
        token_clause = "" if execution_token is None else " AND execution_token = ?"
        parameters: list[object] = [stage, resolved, message, now, now, job_id]
        if execution_token is not None:
            parameters.append(execution_token)
        with closing(self.connect()) as connection:
            connection.execute(
                """UPDATE cement_jobs
                SET status = 'running', stage = ?, progress = ?, message = ?,
                    updated_at = ?, heartbeat_at = ?
                WHERE job_id = ? AND status IN ('queued', 'running')"""
                + token_clause,
                parameters,
            )
            connection.commit()
        return self.get(job_id)

    def mark_completed(
        self,
        job_id: str,
        *,
        result_json_path: str,
        summary: dict[str, Any],
        execution_token: str | None = None,
    ) -> CementJob:
        now = utc_now().isoformat()
        token_clause = "" if execution_token is None else " AND execution_token = ?"
        parameters: list[object] = [
            now,
            now,
            result_json_path,
            json.dumps(summary, separators=(",", ":"), default=str),
            job_id,
        ]
        if execution_token is not None:
            parameters.append(execution_token)
        with closing(self.connect()) as connection:
            connection.execute(
                """UPDATE cement_jobs
                SET status = 'completed', stage = 'completed', progress = 100,
                    message = 'Cement evidence job complete', completed_at = ?,
                    updated_at = ?, result_json_path = ?, summary_json = ?,
                    error = NULL, execution_token = NULL, heartbeat_at = NULL
                WHERE job_id = ? AND status IN ('queued', 'running')"""
                + token_clause,
                parameters,
            )
            connection.commit()
        return self.get(job_id)

    def mark_failed(
        self,
        job_id: str,
        error: str,
        *,
        execution_token: str | None = None,
    ) -> CementJob:
        now = utc_now().isoformat()
        token_clause = "" if execution_token is None else " AND execution_token = ?"
        parameters: list[object] = [now, now, error, job_id]
        if execution_token is not None:
            parameters.append(execution_token)
        with closing(self.connect()) as connection:
            connection.execute(
                """UPDATE cement_jobs
                SET status = 'failed', stage = 'failed', progress = 100,
                    message = 'Cement evidence job failed', completed_at = ?,
                    updated_at = ?, error = ?, execution_token = NULL, heartbeat_at = NULL
                WHERE job_id = ? AND status IN ('queued', 'running')"""
                + token_clause,
                parameters,
            )
            connection.commit()
        return self.get(job_id)
