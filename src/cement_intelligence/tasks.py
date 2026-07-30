"""Celery tasks for durable cement discovery and approved evidence capture."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from celery import Task
from celery.exceptions import Ignore

from mattress_intelligence.celery_app import celery_app
from mattress_intelligence.settings import Settings

from .discovery import CementDiscoveryEngine, ProviderSearchClient
from .intake import CementEvidenceIntake
from .jobs import CementJob, CementJobStore, STAGE_PROGRESS
from .models import CementDiscoveryRequest, CementDiscoveryResult
from .storage import build_cement_repository


def _completed_payload(job: CementJob) -> dict[str, Any]:
    return {
        **job.summary,
        "job_id": job.job_id,
        "result_json_path": job.result_json_path,
        "duplicate_delivery_ignored": True,
    }


def _claim(task: Any, settings: Settings, job_id: str) -> tuple[CementJobStore, str]:
    store = CementJobStore.from_settings(settings)
    execution_token = uuid4().hex
    job, disposition = store.claim(
        job_id,
        task_id=str(task.request.id or job_id),
        execution_token=execution_token,
        stale_after_seconds=settings.celery_job_lease_stale_seconds,
    )
    if disposition == "completed":
        raise CementJobAlreadyCompleted(_completed_payload(job))
    if disposition in {"already_running", "terminal"}:
        raise Ignore()
    return store, execution_token


class CementJobAlreadyCompleted(Exception):
    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__("Cement job already completed")
        self.payload = payload


def _progress_callback(
    task: Any,
    store: CementJobStore,
    job_id: str,
    execution_token: str,
):
    def update(stage: str, **metadata: object) -> None:
        message = str(metadata.get("message") or stage.replace("_", " ").title())
        current = metadata.get("current")
        total = metadata.get("total")
        progress = STAGE_PROGRESS.get(stage, 50)
        if isinstance(current, int) and isinstance(total, int) and total > 0:
            start = STAGE_PROGRESS.get(stage, progress)
            if stage == "searching":
                progress = start + round(45 * current / total)
            elif stage == "classifying":
                progress = start + round(10 * current / total)
            elif stage == "capturing":
                progress = start + round(70 * current / total)
            message = f"{message} ({current}/{total})"
        task.update_state(
            state="PROGRESS",
            meta={"stage": stage, "message": message, **metadata},
        )
        store.mark_progress(
            job_id,
            stage=stage,
            message=message,
            progress=progress,
            execution_token=execution_token,
        )

    return update


@celery_app.task(name="cement_intelligence.discover", bind=True)
def run_cement_discovery_task(
    self,
    request_payload: dict[str, Any],
    provider: str,
    output_dir: str,
    job_id: str,
) -> dict[str, Any]:
    settings = Settings()
    try:
        store, token = _claim(self, settings, job_id)
    except CementJobAlreadyCompleted as completed:
        return completed.payload
    try:
        callback = _progress_callback(self, store, job_id, token)
        callback("generating_queries", message="Generating authoritative cement searches")
        request = CementDiscoveryRequest.from_dict(request_payload)
        result = CementDiscoveryEngine(
            ProviderSearchClient(settings=settings, provider=provider)
        ).discover(request, progress_callback=callback)
        destination = Path(output_dir)
        destination.mkdir(parents=True, exist_ok=True)
        result_path = destination / "cement_discovery_result.json"
        result_path.write_text(
            json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        repository = build_cement_repository(settings)
        repository.save_discovery(
            result,
            provider=provider,
            job_id=job_id,
        )
        summary = {
            "run_id": result.run_id,
            "documents": len(result.documents),
            "plants": len(result.plants),
            "warnings": len(result.warnings),
            "sql_backend": repository.check_connection()["backend"],
            "result_json_path": str(result_path),
        }
        store.mark_completed(
            job_id,
            result_json_path=str(result_path),
            summary=summary,
            execution_token=token,
        )
        return {**summary, "job_id": job_id, "duplicate_delivery_ignored": False}
    except Ignore:
        raise
    except Exception as exc:
        store.mark_failed(job_id, str(exc), execution_token=token)
        raise


@celery_app.task(name="cement_intelligence.capture", bind=True)
def run_cement_capture_task(
    self,
    discovery_json_path: str,
    plant_candidate_id: str,
    selected_document_ids: list[str],
    respect_robots_txt: bool,
    capture_provider: str,
    output_dir: str,
    job_id: str,
    manual_urls: list[str] | None = None,
) -> dict[str, Any]:
    settings = Settings()
    try:
        store, token = _claim(self, settings, job_id)
    except CementJobAlreadyCompleted as completed:
        return completed.payload
    try:
        discovery = CementDiscoveryResult.from_dict(
            json.loads(Path(discovery_json_path).read_text(encoding="utf-8"))
        )
        result = CementEvidenceIntake(settings).capture_approved_bundle(
            discovery,
            plant_candidate_id=plant_candidate_id,
            selected_document_ids=selected_document_ids,
            manual_urls=manual_urls,
            respect_robots_txt=respect_robots_txt,
            capture_provider=capture_provider,
            output_directory=Path(output_dir),
            progress_callback=_progress_callback(self, store, job_id, token),
        )
        repository = build_cement_repository(settings)
        repository.save_discovery(
            discovery,
            provider=capture_provider,
        )
        repository.save_intake(result, job_id=job_id)
        result_path = Path(result.output_directory) / "intake_manifest.json"
        summary = {
            "intake_id": result.intake_id,
            "captured_usable": result.successful_count,
            "needs_review": result.review_count,
            "failed": result.failed_count,
            "capture_provider": result.capture_provider,
            "sql_backend": repository.check_connection()["backend"],
            "result_json_path": str(result_path),
        }
        store.mark_completed(
            job_id,
            result_json_path=str(result_path),
            summary=summary,
            execution_token=token,
        )
        return {**summary, "job_id": job_id, "duplicate_delivery_ignored": False}
    except Ignore:
        raise
    except Exception as exc:
        store.mark_failed(job_id, str(exc), execution_token=token)
        raise


discovery_task = cast(Task, run_cement_discovery_task)
capture_task = cast(Task, run_cement_capture_task)


def enqueue_cement_discovery(
    request: CementDiscoveryRequest,
    *,
    provider: str,
    output_dir: Path,
    job_id: str,
) -> Any:
    return discovery_task.apply_async(
        args=[request.to_dict(), provider, str(output_dir), job_id],
        task_id=job_id,
    )


def enqueue_cement_capture(
    *,
    discovery_json_path: str,
    plant_candidate_id: str,
    selected_document_ids: list[str],
    respect_robots_txt: bool,
    capture_provider: str,
    output_dir: Path,
    job_id: str,
    manual_urls: list[str] | None = None,
) -> Any:
    return capture_task.apply_async(
        args=[
            discovery_json_path,
            plant_candidate_id,
            selected_document_ids,
            respect_robots_txt,
            capture_provider,
            str(output_dir),
            job_id,
            manual_urls or [],
        ],
        task_id=job_id,
    )
