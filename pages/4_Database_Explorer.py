"""Read-only live viewer for BRIXTA research and Celery SQL data."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from cement_intelligence.jobs import CementJobStore
from cement_intelligence.storage import (
    CEMENT_TABLES,
    build_cement_repository,
    import_existing_cement_outputs,
)
from evidence_discovery.jobs import CampaignJobStore
from mattress_intelligence.settings import Settings

MATTRESS_TABLES = (
    "research_runs",
    "sources",
    "assets",
    "products",
    "claims",
    "observations",
    "configurations",
    "graph_edges",
)
MAIN_TABLES = (*MATTRESS_TABLES, *CEMENT_TABLES)

settings = Settings()
settings.ensure_directories()
cement_repository = build_cement_repository(settings)
job_store = CementJobStore.from_settings(settings)
campaign_job_store = CampaignJobStore.from_settings(settings)


def _sqlite_read_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{path.resolve()}?mode=ro",
        uri=True,
        timeout=10.0,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


def _main_table_counts() -> dict[str, int]:
    if settings.postgres_enabled:
        import psycopg

        with psycopg.connect(settings.database_url or "") as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public'"""
                )
                available = {str(row[0]) for row in cursor.fetchall()}
                counts: dict[str, int] = {}
                for table in MAIN_TABLES:
                    if table not in available:
                        continue
                    cursor.execute(f"SELECT COUNT(*) FROM {table}")
                    count_row = cursor.fetchone()
                    counts[table] = (
                        int(count_row[0]) if count_row is not None else 0
                    )
                return counts

    with closing(_sqlite_read_connection(settings.database_path)) as connection:
        available = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        counts: dict[str, int] = {}
        for table in MAIN_TABLES:
            if table not in available:
                continue
            count_row = connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()
            counts[table] = int(count_row[0]) if count_row is not None else 0
        return counts


def _main_table_rows(table: str, limit: int) -> list[dict[str, Any]]:
    if table not in MAIN_TABLES:
        raise ValueError(f"Unsupported table: {table}")
    resolved_limit = max(1, min(limit, 2_000))
    if settings.postgres_enabled:
        import psycopg

        with psycopg.connect(settings.database_url or "") as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT * FROM {table} LIMIT %s",
                    (resolved_limit,),
                )
                columns = [item.name for item in cursor.description or ()]
                return [
                    dict(zip(columns, row, strict=True))
                    for row in cursor.fetchall()
                ]

    with closing(_sqlite_read_connection(settings.database_path)) as connection:
        rows = connection.execute(
            f"SELECT * FROM {table} LIMIT ?",
            (resolved_limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def _display_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, str) and len(value) > 500:
        return f"{value[:500]}…"
    return value


def _display_rows(rows: list[dict[str, Any]], table: str) -> None:
    if not rows:
        st.info(f"`{table}` currently contains no rows.")
        return
    frame = pd.DataFrame(
        [
            {key: _display_value(value) for key, value in row.items()}
            for row in rows
        ]
    )
    st.dataframe(frame, hide_index=True, use_container_width=True)
    st.download_button(
        "Download visible rows as CSV",
        data=frame.to_csv(index=False).encode("utf-8"),
        file_name=f"{table}.csv",
        mime="text/csv",
        key=f"download-{table}",
    )
    row_number = st.number_input(
        "Inspect complete row",
        min_value=1,
        max_value=len(rows),
        value=1,
        step=1,
        key=f"row-{table}",
    )
    st.json(rows[int(row_number) - 1], expanded=False)


st.title("BRIXTA Database Explorer")
st.caption(
    "Read-only live inspection of research SQL. Celery can continue writing while "
    "this page refreshes."
)

connection = cement_repository.check_connection()
backend_columns = st.columns(3)
backend_columns[0].metric("Main database", str(connection.get("backend", "unknown")))
backend_columns[1].metric(
    "Research database",
    str(connection.get("path") or connection.get("database") or "configured"),
)
backend_columns[2].metric("Job ledger", str(settings.job_database_path))

with st.expander("Import existing cement JSON sessions into SQL"):
    st.write(
        "This is idempotent: existing run and intake IDs are updated instead of duplicated."
    )
    if st.button("Backfill existing cement sessions", type="primary"):
        with st.spinner("Importing existing cement discovery and intake manifests…"):
            report = import_existing_cement_outputs(settings)
        st.success(
            f"Imported {report['discoveries']} discovery sessions and "
            f"{report['intakes']} intake sessions."
        )
        if report["errors"]:
            st.warning(f"{len(report['errors'])} files could not be imported.")
            st.code("\n".join(report["errors"]))

auto_refresh = st.toggle("Refresh automatically every 5 seconds", value=True)


@st.fragment(run_every="5s" if auto_refresh else None)
def live_database_view() -> None:
    counts = _main_table_counts()
    st.subheader("Table inventory")
    count_frame = pd.DataFrame(
        [
            {
                "Domain": "Cement" if table.startswith("cement_") else "Mattress",
                "Table": table,
                "Rows": count,
            }
            for table, count in counts.items()
        ]
    )
    st.dataframe(count_frame, hide_index=True, use_container_width=True)

    research_tab, jobs_tab, campaign_jobs_tab = st.tabs(
        (
            "Research database",
            "Cement job ledger",
            "Open-campaign job ledger",
        )
    )
    with research_tab:
        available = [table for table in MAIN_TABLES if table in counts]
        if not available:
            st.info("No supported research tables are available yet.")
        else:
            selected_table = st.selectbox(
                "Table",
                available,
                index=available.index("cement_discovery_runs")
                if "cement_discovery_runs" in available
                else 0,
            )
            row_limit = st.slider("Maximum rows", 25, 1_000, 200, step=25)
            _display_rows(
                _main_table_rows(selected_table, row_limit),
                selected_table,
            )

    with jobs_tab:
        jobs = [job.model_dump() for job in job_store.list(limit=500)]
        if not jobs:
            st.info("No cement jobs have been recorded.")
        else:
            job_frame = pd.DataFrame(
                [
                    {
                        "job_id": item["job_id"],
                        "job_type": item["job_type"],
                        "status": item["status"],
                        "stage": item["stage"],
                        "progress": item["progress"],
                        "submitted_at": item["submitted_at"],
                        "completed_at": item["completed_at"],
                        "output_dir": item["output_dir"],
                        "error": item["error"],
                    }
                    for item in jobs
                ]
            )
            st.dataframe(job_frame, hide_index=True, use_container_width=True)
            selected_job = st.selectbox(
                "Inspect complete job",
                options=[item["job_id"] for item in jobs],
            )
            st.json(
                next(item for item in jobs if item["job_id"] == selected_job),
                expanded=False,
            )

    with campaign_jobs_tab:
        campaign_jobs = [
            job.model_dump()
            for job in campaign_job_store.list(limit=500)
        ]
        if not campaign_jobs:
            st.info("No open research campaign jobs have been recorded.")
        else:
            campaign_frame = pd.DataFrame(
                [
                    {
                        "job_id": item["job_id"],
                        "job_type": item["job_type"],
                        "campaign": item["request"].get("campaign_name")
                        or item["request"].get("campaign_id"),
                        "status": item["status"],
                        "stage": item["stage"],
                        "progress": item["progress"],
                        "submitted_at": item["submitted_at"],
                        "completed_at": item["completed_at"],
                        "output_dir": item["output_dir"],
                        "error": item["error"],
                    }
                    for item in campaign_jobs
                ]
            )
            st.dataframe(
                campaign_frame,
                hide_index=True,
                use_container_width=True,
            )
            selected_campaign_job = st.selectbox(
                "Inspect complete campaign job",
                options=[item["job_id"] for item in campaign_jobs],
            )
            st.json(
                next(
                    item
                    for item in campaign_jobs
                    if item["job_id"] == selected_campaign_job
                ),
                expanded=False,
            )


live_database_view()
