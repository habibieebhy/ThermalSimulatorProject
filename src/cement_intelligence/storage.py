"""SQLite/PostgreSQL persistence for cement discovery and evidence capture."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Protocol, cast

from mattress_intelligence.settings import Settings

from .models import CapturedEvidence, CementDiscoveryResult, CementIntakeResult


CEMENT_TABLES = (
    "cement_discovery_runs",
    "cement_documents",
    "cement_plants",
    "cement_plant_documents",
    "cement_intakes",
    "cement_captures",
)

SQLITE_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS cement_discovery_runs (
    run_id TEXT PRIMARY KEY,
    job_id TEXT,
    provider TEXT NOT NULL,
    mode TEXT NOT NULL,
    country TEXT NOT NULL,
    state TEXT,
    company_name TEXT,
    plant_name TEXT,
    created_at TEXT NOT NULL,
    warning_count INTEGER NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cement_documents (
    candidate_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES cement_discovery_runs(run_id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    title TEXT,
    provider TEXT NOT NULL,
    document_type TEXT NOT NULL,
    evidence_scope TEXT NOT NULL,
    company_hint TEXT,
    plant_hint TEXT,
    location_hint TEXT,
    publisher_domain TEXT NOT NULL,
    overall_score REAL NOT NULL,
    authority_score REAL NOT NULL,
    plant_specificity_score REAL NOT NULL,
    technical_value_score REAL NOT NULL,
    is_government INTEGER NOT NULL,
    is_probably_pdf INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (candidate_id, run_id)
);

CREATE TABLE IF NOT EXISTS cement_plants (
    plant_candidate_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES cement_discovery_runs(run_id) ON DELETE CASCADE,
    company_name TEXT NOT NULL,
    plant_name TEXT NOT NULL,
    location TEXT,
    plant_type TEXT NOT NULL,
    researchability_score REAL NOT NULL,
    recommended INTEGER NOT NULL,
    anchor_document_id TEXT,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (plant_candidate_id, run_id)
);

CREATE TABLE IF NOT EXISTS cement_plant_documents (
    run_id TEXT NOT NULL,
    plant_candidate_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    included_in_bundle INTEGER NOT NULL,
    PRIMARY KEY (run_id, plant_candidate_id, candidate_id),
    FOREIGN KEY (plant_candidate_id, run_id)
        REFERENCES cement_plants(plant_candidate_id, run_id) ON DELETE CASCADE,
    FOREIGN KEY (candidate_id, run_id)
        REFERENCES cement_documents(candidate_id, run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS cement_intakes (
    intake_id TEXT PRIMARY KEY,
    job_id TEXT,
    discovery_run_id TEXT NOT NULL
        REFERENCES cement_discovery_runs(run_id) ON DELETE CASCADE,
    plant_candidate_id TEXT NOT NULL,
    capture_provider TEXT NOT NULL,
    output_directory TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    successful_count INTEGER NOT NULL,
    review_count INTEGER NOT NULL,
    failed_count INTEGER NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cement_captures (
    intake_id TEXT NOT NULL REFERENCES cement_intakes(intake_id) ON DELETE CASCADE,
    candidate_id TEXT NOT NULL,
    status TEXT NOT NULL,
    url TEXT NOT NULL,
    http_status INTEGER,
    content_type TEXT,
    content_sha256 TEXT,
    size_bytes INTEGER,
    artifact_path TEXT,
    object_uri TEXT,
    capture_method TEXT,
    text_characters INTEGER NOT NULL,
    extracted_text_path TEXT,
    extracted_text TEXT,
    original_binary_preserved INTEGER NOT NULL,
    error TEXT,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (intake_id, candidate_id)
);

CREATE INDEX IF NOT EXISTS cement_documents_scope_idx
    ON cement_documents(evidence_scope, document_type);
CREATE INDEX IF NOT EXISTS cement_documents_url_idx
    ON cement_documents(url);
CREATE INDEX IF NOT EXISTS cement_plants_company_idx
    ON cement_plants(company_name, plant_name);
CREATE INDEX IF NOT EXISTS cement_captures_status_idx
    ON cement_captures(status, capture_method);
CREATE INDEX IF NOT EXISTS cement_captures_sha_idx
    ON cement_captures(content_sha256);
"""

POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS cement_discovery_runs (
    run_id TEXT PRIMARY KEY,
    job_id TEXT,
    provider TEXT NOT NULL,
    mode TEXT NOT NULL,
    country TEXT NOT NULL,
    state TEXT,
    company_name TEXT,
    plant_name TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    warning_count INTEGER NOT NULL,
    payload_json JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS cement_documents (
    candidate_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES cement_discovery_runs(run_id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    title TEXT,
    provider TEXT NOT NULL,
    document_type TEXT NOT NULL,
    evidence_scope TEXT NOT NULL,
    company_hint TEXT,
    plant_hint TEXT,
    location_hint TEXT,
    publisher_domain TEXT NOT NULL,
    overall_score DOUBLE PRECISION NOT NULL,
    authority_score DOUBLE PRECISION NOT NULL,
    plant_specificity_score DOUBLE PRECISION NOT NULL,
    technical_value_score DOUBLE PRECISION NOT NULL,
    is_government BOOLEAN NOT NULL,
    is_probably_pdf BOOLEAN NOT NULL,
    payload_json JSONB NOT NULL,
    PRIMARY KEY (candidate_id, run_id)
);

CREATE TABLE IF NOT EXISTS cement_plants (
    plant_candidate_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES cement_discovery_runs(run_id) ON DELETE CASCADE,
    company_name TEXT NOT NULL,
    plant_name TEXT NOT NULL,
    location TEXT,
    plant_type TEXT NOT NULL,
    researchability_score DOUBLE PRECISION NOT NULL,
    recommended BOOLEAN NOT NULL,
    anchor_document_id TEXT,
    payload_json JSONB NOT NULL,
    PRIMARY KEY (plant_candidate_id, run_id)
);

CREATE TABLE IF NOT EXISTS cement_plant_documents (
    run_id TEXT NOT NULL,
    plant_candidate_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    included_in_bundle BOOLEAN NOT NULL,
    PRIMARY KEY (run_id, plant_candidate_id, candidate_id),
    FOREIGN KEY (plant_candidate_id, run_id)
        REFERENCES cement_plants(plant_candidate_id, run_id) ON DELETE CASCADE,
    FOREIGN KEY (candidate_id, run_id)
        REFERENCES cement_documents(candidate_id, run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS cement_intakes (
    intake_id TEXT PRIMARY KEY,
    job_id TEXT,
    discovery_run_id TEXT NOT NULL
        REFERENCES cement_discovery_runs(run_id) ON DELETE CASCADE,
    plant_candidate_id TEXT NOT NULL,
    capture_provider TEXT NOT NULL,
    output_directory TEXT NOT NULL,
    approved_at TIMESTAMPTZ NOT NULL,
    successful_count INTEGER NOT NULL,
    review_count INTEGER NOT NULL,
    failed_count INTEGER NOT NULL,
    payload_json JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS cement_captures (
    intake_id TEXT NOT NULL REFERENCES cement_intakes(intake_id) ON DELETE CASCADE,
    candidate_id TEXT NOT NULL,
    status TEXT NOT NULL,
    url TEXT NOT NULL,
    http_status INTEGER,
    content_type TEXT,
    content_sha256 TEXT,
    size_bytes BIGINT,
    artifact_path TEXT,
    object_uri TEXT,
    capture_method TEXT,
    text_characters INTEGER NOT NULL,
    extracted_text_path TEXT,
    extracted_text TEXT,
    original_binary_preserved BOOLEAN NOT NULL,
    error TEXT,
    payload_json JSONB NOT NULL,
    PRIMARY KEY (intake_id, candidate_id)
);

CREATE INDEX IF NOT EXISTS cement_documents_scope_idx
    ON cement_documents(evidence_scope, document_type);
CREATE INDEX IF NOT EXISTS cement_documents_url_idx
    ON cement_documents(url);
CREATE INDEX IF NOT EXISTS cement_plants_company_idx
    ON cement_plants(company_name, plant_name);
CREATE INDEX IF NOT EXISTS cement_captures_status_idx
    ON cement_captures(status, capture_method);
CREATE INDEX IF NOT EXISTS cement_captures_sha_idx
    ON cement_captures(content_sha256);
"""


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str)


def _extracted_text(path: str | None) -> str | None:
    if not path:
        return None
    source = Path(path)
    if not source.is_file():
        return None
    return source.read_text(encoding="utf-8", errors="replace")


def _capture_text_path(item: Any) -> str | None:
    """Read the optional field safely from current or legacy capture records."""

    value = getattr(item, "extracted_text_path", None)
    return str(value) if value else None


def _required_scalar(row: Any | None, *, operation: str) -> Any:
    if row is None:
        raise RuntimeError(f"Database returned no row while {operation}")
    return row[0]


def _validated_table_name(table: str) -> str:
    """Allow only cement tables managed by this repository."""

    if table not in CEMENT_TABLES:
        raise ValueError(f"Unsupported cement table: {table}")
    return table


def _quoted_table_name(table: str) -> str:
    """Validate and quote a cement table name for use as a SQL identifier."""

    escaped_table = _validated_table_name(table).replace('"', '""')
    return f'"{escaped_table}"'


def _construct_known_dataclass(model_type: Any, payload: dict[str, Any]) -> Any:
    """Construct current or legacy dataclasses using only declared fields."""

    fields = getattr(model_type, "__dataclass_fields__", {})
    accepted = {
        name: value
        for name, value in payload.items()
        if name in fields
    }
    return model_type(**accepted)


def _intake_result_from_dict(value: dict[str, Any]) -> CementIntakeResult:
    """Deserialize intake JSON across current and legacy model APIs."""

    from_dict = getattr(CementIntakeResult, "from_dict", None)
    if callable(from_dict):
        return cast(CementIntakeResult, from_dict(value))

    from_manifest = getattr(CementIntakeResult, "from_manifest", None)
    if callable(from_manifest):
        return cast(CementIntakeResult, from_manifest(value))

    captured = [
        _construct_known_dataclass(CapturedEvidence, dict(item))
        for item in value.get("captured") or []
    ]
    payload: dict[str, Any] = {
        "intake_id": str(value["intake_id"]),
        "discovery_run_id": str(value["discovery_run_id"]),
        "plant_candidate_id": str(value["plant_candidate_id"]),
        "output_directory": str(value["output_directory"]),
        "capture_provider": str(value.get("capture_provider") or "services"),
        "captured": captured,
    }
    if value.get("approved_at"):
        payload["approved_at"] = str(value["approved_at"])
    return _construct_known_dataclass(CementIntakeResult, payload)


class CementRepository(Protocol):
    def save_discovery(
        self,
        result: CementDiscoveryResult,
        *,
        provider: str,
        job_id: str | None = None,
    ) -> None: ...

    def save_intake(
        self,
        result: CementIntakeResult,
        *,
        job_id: str | None = None,
    ) -> None: ...

    def table_counts(self) -> dict[str, int]: ...

    def list_rows(self, table: str, *, limit: int = 200) -> list[dict[str, Any]]: ...

    def check_connection(self) -> dict[str, object]: ...


class SQLiteCementRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as connection:
            connection.executescript(SQLITE_SCHEMA)
            connection.commit()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def save_discovery(
        self,
        result: CementDiscoveryResult,
        *,
        provider: str,
        job_id: str | None = None,
    ) -> None:
        request = result.request
        with closing(self.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO cement_discovery_runs (
                    run_id, job_id, provider, mode, country, state,
                    company_name, plant_name, created_at, warning_count, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    job_id=COALESCE(excluded.job_id, cement_discovery_runs.job_id),
                    provider=excluded.provider,
                    mode=excluded.mode,
                    country=excluded.country,
                    state=excluded.state,
                    company_name=excluded.company_name,
                    plant_name=excluded.plant_name,
                    created_at=excluded.created_at,
                    warning_count=excluded.warning_count,
                    payload_json=excluded.payload_json""",
                (
                    result.run_id,
                    job_id,
                    provider,
                    request.mode.value,
                    request.country,
                    request.state,
                    request.company_name,
                    request.plant_name,
                    result.created_at,
                    len(result.warnings),
                    _json(result.to_dict()),
                ),
            )
            connection.execute(
                "DELETE FROM cement_plant_documents WHERE run_id = ?",
                (result.run_id,),
            )
            connection.execute(
                "DELETE FROM cement_plants WHERE run_id = ?",
                (result.run_id,),
            )
            connection.execute(
                "DELETE FROM cement_documents WHERE run_id = ?",
                (result.run_id,),
            )
            connection.executemany(
                """INSERT INTO cement_documents VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )""",
                [
                    (
                        item.candidate_id,
                        result.run_id,
                        item.url,
                        item.title,
                        item.provider,
                        item.document_type.value,
                        item.evidence_scope.value,
                        item.company_hint,
                        item.plant_hint,
                        item.location_hint,
                        item.publisher_domain,
                        item.overall_score,
                        item.authority_score,
                        item.plant_specificity_score,
                        item.technical_value_score,
                        int(item.is_government),
                        int(item.is_probably_pdf),
                        _json(item.to_dict()),
                    )
                    for item in result.documents
                ],
            )
            connection.executemany(
                """INSERT INTO cement_plants VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )""",
                [
                    (
                        item.plant_candidate_id,
                        result.run_id,
                        item.company_name,
                        item.plant_name,
                        item.location,
                        item.plant_type,
                        item.researchability_score,
                        int(item.recommended),
                        item.anchor_document_id,
                        _json(item.to_dict()),
                    )
                    for item in result.plants
                ],
            )
            plant_documents: list[tuple[object, ...]] = []
            for plant in result.plants:
                bundle_ids = set(plant.bundle.document_ids if plant.bundle else ())
                for candidate_id in dict.fromkeys(plant.document_ids):
                    plant_documents.append(
                        (
                            result.run_id,
                            plant.plant_candidate_id,
                            candidate_id,
                            int(candidate_id in bundle_ids),
                        )
                    )
            connection.executemany(
                "INSERT INTO cement_plant_documents VALUES (?, ?, ?, ?)",
                plant_documents,
            )
            connection.commit()

    def save_intake(
        self,
        result: CementIntakeResult,
        *,
        job_id: str | None = None,
    ) -> None:
        with closing(self.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO cement_intakes VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(intake_id) DO UPDATE SET
                    job_id=excluded.job_id,
                    discovery_run_id=excluded.discovery_run_id,
                    plant_candidate_id=excluded.plant_candidate_id,
                    capture_provider=excluded.capture_provider,
                    output_directory=excluded.output_directory,
                    approved_at=excluded.approved_at,
                    successful_count=excluded.successful_count,
                    review_count=excluded.review_count,
                    failed_count=excluded.failed_count,
                    payload_json=excluded.payload_json""",
                (
                    result.intake_id,
                    job_id,
                    result.discovery_run_id,
                    result.plant_candidate_id,
                    result.capture_provider,
                    result.output_directory,
                    result.approved_at,
                    result.successful_count,
                    result.review_count,
                    result.failed_count,
                    _json(result.to_dict()),
                ),
            )
            connection.execute(
                "DELETE FROM cement_captures WHERE intake_id = ?",
                (result.intake_id,),
            )
            connection.executemany(
                """INSERT INTO cement_captures VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )""",
                [
                    (
                        result.intake_id,
                        item.candidate_id,
                        item.status,
                        item.url,
                        item.http_status,
                        item.content_type,
                        item.content_sha256,
                        item.size_bytes,
                        item.artifact_path,
                        item.object_uri,
                        item.capture_method,
                        item.text_characters,
                        _capture_text_path(item),
                        _extracted_text(_capture_text_path(item)),
                        int(item.original_binary_preserved),
                        item.error,
                        _json(item.to_dict()),
                    )
                    for item in result.captured
                ],
            )
            connection.commit()

    def table_counts(self) -> dict[str, int]:
        with closing(self.connect()) as connection:
            return {
                table: int(
                    _required_scalar(
                        connection.execute(
                            f"SELECT COUNT(*) FROM {_quoted_table_name(table)}"
                        ).fetchone(),
                        operation=f"counting {table}",
                    )
                )
                for table in CEMENT_TABLES
            }

    def list_rows(
        self,
        table: str,
        *,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        quoted_table = _quoted_table_name(table)
        with closing(self.connect()) as connection:
            rows = connection.execute(
                f"SELECT * FROM {quoted_table} LIMIT ?",
                (max(1, min(limit, 2_000)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def check_connection(self) -> dict[str, object]:
        with closing(self.connect()) as connection:
            ok = (
                _required_scalar(
                    connection.execute("SELECT 1").fetchone(),
                    operation="checking the SQLite connection",
                )
                == 1
            )
        return {"backend": "sqlite", "path": str(self.path), "ok": ok}


class PostgresCementRepository:
    def __init__(self, database_url: str, direct_url: str | None = None) -> None:
        try:
            import psycopg
            from psycopg import sql
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "DATABASE_URL is configured but psycopg is not installed."
            ) from exc
        self.psycopg = psycopg
        self.sql = sql
        self.database_url = database_url
        self.schema_url = direct_url or database_url
        with self.psycopg.connect(self.schema_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(self.sql.SQL(POSTGRES_SCHEMA))
            connection.commit()

    def _connect(self):
        return self.psycopg.connect(self.database_url)

    def save_discovery(
        self,
        result: CementDiscoveryResult,
        *,
        provider: str,
        job_id: str | None = None,
    ) -> None:
        request = result.request
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO cement_discovery_runs (
                        run_id, job_id, provider, mode, country, state,
                        company_name, plant_name, created_at, warning_count, payload_json
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                    ON CONFLICT (run_id) DO UPDATE SET
                        job_id=COALESCE(
                            EXCLUDED.job_id,
                            cement_discovery_runs.job_id
                        ),
                        provider=EXCLUDED.provider,
                        mode=EXCLUDED.mode,
                        country=EXCLUDED.country,
                        state=EXCLUDED.state,
                        company_name=EXCLUDED.company_name,
                        plant_name=EXCLUDED.plant_name,
                        created_at=EXCLUDED.created_at,
                        warning_count=EXCLUDED.warning_count,
                        payload_json=EXCLUDED.payload_json""",
                    (
                        result.run_id,
                        job_id,
                        provider,
                        request.mode.value,
                        request.country,
                        request.state,
                        request.company_name,
                        request.plant_name,
                        result.created_at,
                        len(result.warnings),
                        _json(result.to_dict()),
                    ),
                )
                cursor.execute(
                    "DELETE FROM cement_plant_documents WHERE run_id = %s",
                    (result.run_id,),
                )
                cursor.execute(
                    "DELETE FROM cement_plants WHERE run_id = %s",
                    (result.run_id,),
                )
                cursor.execute(
                    "DELETE FROM cement_documents WHERE run_id = %s",
                    (result.run_id,),
                )
                cursor.executemany(
                    """INSERT INTO cement_documents VALUES (
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb
                    )""",
                    [
                        (
                            item.candidate_id,
                            result.run_id,
                            item.url,
                            item.title,
                            item.provider,
                            item.document_type.value,
                            item.evidence_scope.value,
                            item.company_hint,
                            item.plant_hint,
                            item.location_hint,
                            item.publisher_domain,
                            item.overall_score,
                            item.authority_score,
                            item.plant_specificity_score,
                            item.technical_value_score,
                            item.is_government,
                            item.is_probably_pdf,
                            _json(item.to_dict()),
                        )
                        for item in result.documents
                    ],
                )
                cursor.executemany(
                    """INSERT INTO cement_plants VALUES (
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb
                    )""",
                    [
                        (
                            item.plant_candidate_id,
                            result.run_id,
                            item.company_name,
                            item.plant_name,
                            item.location,
                            item.plant_type,
                            item.researchability_score,
                            item.recommended,
                            item.anchor_document_id,
                            _json(item.to_dict()),
                        )
                        for item in result.plants
                    ],
                )
                plant_documents: list[tuple[object, ...]] = []
                for plant in result.plants:
                    bundle_ids = set(
                        plant.bundle.document_ids if plant.bundle else ()
                    )
                    for candidate_id in dict.fromkeys(plant.document_ids):
                        plant_documents.append(
                            (
                                result.run_id,
                                plant.plant_candidate_id,
                                candidate_id,
                                candidate_id in bundle_ids,
                            )
                        )
                cursor.executemany(
                    "INSERT INTO cement_plant_documents VALUES (%s,%s,%s,%s)",
                    plant_documents,
                )
            connection.commit()

    def save_intake(
        self,
        result: CementIntakeResult,
        *,
        job_id: str | None = None,
    ) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO cement_intakes VALUES (
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb
                    )
                    ON CONFLICT (intake_id) DO UPDATE SET
                        job_id=EXCLUDED.job_id,
                        discovery_run_id=EXCLUDED.discovery_run_id,
                        plant_candidate_id=EXCLUDED.plant_candidate_id,
                        capture_provider=EXCLUDED.capture_provider,
                        output_directory=EXCLUDED.output_directory,
                        approved_at=EXCLUDED.approved_at,
                        successful_count=EXCLUDED.successful_count,
                        review_count=EXCLUDED.review_count,
                        failed_count=EXCLUDED.failed_count,
                        payload_json=EXCLUDED.payload_json""",
                    (
                        result.intake_id,
                        job_id,
                        result.discovery_run_id,
                        result.plant_candidate_id,
                        result.capture_provider,
                        result.output_directory,
                        result.approved_at,
                        result.successful_count,
                        result.review_count,
                        result.failed_count,
                        _json(result.to_dict()),
                    ),
                )
                cursor.execute(
                    "DELETE FROM cement_captures WHERE intake_id = %s",
                    (result.intake_id,),
                )
                cursor.executemany(
                    """INSERT INTO cement_captures VALUES (
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb
                    )""",
                    [
                        (
                            result.intake_id,
                            item.candidate_id,
                            item.status,
                            item.url,
                            item.http_status,
                            item.content_type,
                            item.content_sha256,
                            item.size_bytes,
                            item.artifact_path,
                            item.object_uri,
                            item.capture_method,
                            item.text_characters,
                            _capture_text_path(item),
                            _extracted_text(_capture_text_path(item)),
                            item.original_binary_preserved,
                            item.error,
                            _json(item.to_dict()),
                        )
                        for item in result.captured
                    ],
                )
            connection.commit()

    def table_counts(self) -> dict[str, int]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                counts: dict[str, int] = {}
                for table in CEMENT_TABLES:
                    cursor.execute(
                        self.sql.SQL("SELECT COUNT(*) FROM {}").format(
                            self.sql.Identifier(table)
                        )
                    )
                    counts[table] = int(
                        _required_scalar(
                            cursor.fetchone(),
                            operation=f"counting {table}",
                        )
                    )
        return counts

    def list_rows(
        self,
        table: str,
        *,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        validated_table = _validated_table_name(table)
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self.sql.SQL("SELECT * FROM {} LIMIT %s").format(
                        self.sql.Identifier(validated_table)
                    ),
                    (max(1, min(limit, 2_000)),),
                )
                columns = [item.name for item in cursor.description or ()]
                rows = cursor.fetchall()
        return [dict(zip(columns, row, strict=True)) for row in rows]

    def check_connection(self) -> dict[str, object]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_database(), version()")
                row = cursor.fetchone()
        return {
            "backend": "postgresql",
            "database": row[0] if row else None,
            "version": row[1] if row else None,
            "ok": row is not None,
        }


def build_cement_repository(settings: Settings) -> CementRepository:
    if settings.postgres_enabled:
        return PostgresCementRepository(
            settings.database_url or "",
            settings.database_direct_url,
        )
    return SQLiteCementRepository(settings.database_path)


def import_existing_cement_outputs(settings: Settings) -> dict[str, Any]:
    """Idempotently backfill discovery/intake JSON sessions into SQL."""

    from .jobs import CementJobStore

    repository = build_cement_repository(settings)
    jobs = CementJobStore.from_settings(settings).list(limit=10_000)
    jobs_by_result = {
        str(Path(job.result_json_path).resolve()): job
        for job in jobs
        if job.result_json_path
    }
    imported_discoveries = 0
    imported_intakes = 0
    errors: list[str] = []

    discovery_paths = sorted(
        settings.output_dir.rglob("cement_discovery_result.json")
    )
    for path in discovery_paths:
        try:
            result = CementDiscoveryResult.from_dict(
                json.loads(path.read_text(encoding="utf-8"))
            )
            job = jobs_by_result.get(str(path.resolve()))
            provider = (
                str(job.request.get("provider") or "unknown")
                if job is not None
                else "unknown"
            )
            repository.save_discovery(
                result,
                provider=provider,
                job_id=job.job_id if job else None,
            )
            imported_discoveries += 1
        except Exception as exc:
            errors.append(f"{path}: {exc}")

    intake_paths = sorted(settings.output_dir.rglob("intake_manifest.json"))
    for path in intake_paths:
        try:
            result = _intake_result_from_dict(
                json.loads(path.read_text(encoding="utf-8"))
            )
            job = jobs_by_result.get(str(path.resolve()))
            repository.save_intake(
                result,
                job_id=job.job_id if job else None,
            )
            imported_intakes += 1
        except Exception as exc:
            errors.append(f"{path}: {exc}")

    return {
        "discoveries": imported_discoveries,
        "intakes": imported_intakes,
        "errors": errors,
    }
