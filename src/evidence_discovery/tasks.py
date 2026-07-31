"""Celery tasks for open research discovery and bundle capture."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from celery import Task
from celery.exceptions import Ignore

from mattress_intelligence.celery_app import celery_app
from mattress_intelligence.settings import Settings

from .capture import CampaignEvidenceCapture
from .discovery import CampaignDiscoveryEngine
from .jobs import CampaignJob, CampaignJobStore, STAGE_PROGRESS
from .models import CampaignDiscoveryResult, CampaignRequest
from .providers import ProviderSearchClient


class CampaignAlreadyCompleted(Exception):
    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__("Campaign job already completed")
        self.payload = payload


def _completed_payload(job: CampaignJob) -> dict[str, Any]:
    return {
        **job.summary,
        "job_id": job.job_id,
        "result_json_path": job.result_json_path,
        "duplicate_delivery_ignored": True,
    }


def _claim(
    task: Any,
    settings: Settings,
    job_id: str,
) -> tuple[CampaignJobStore, str]:
    store = CampaignJobStore.from_settings(settings)
    token = uuid4().hex
    job, disposition = store.claim(
        job_id,
        task_id=str(task.request.id or job_id),
        execution_token=token,
        stale_after_seconds=settings.celery_job_lease_stale_seconds,
    )
    if disposition == "completed":
        raise CampaignAlreadyCompleted(_completed_payload(job))
    if disposition in {"already_running", "terminal"}:
        raise Ignore()
    return store, token


def _progress_callback(
    task: Any,
    store: CampaignJobStore,
    job_id: str,
    token: str,
):
    def update(stage: str, **metadata: object) -> None:
        message = str(
            metadata.get("message")
            or stage.replace("_", " ").title()
        )
        current = metadata.get("current")
        total = metadata.get("total")
        progress = STAGE_PROGRESS.get(stage, 50)
        if isinstance(current, int) and isinstance(total, int) and total > 0:
            start = STAGE_PROGRESS.get(stage, progress)
            span = {
                "searching": 35,
                "crawling_domains": 18,
                "capturing": 75,
            }.get(stage, 8)
            progress = min(93, start + round(span * current / total))
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
            execution_token=token,
        )

    return update


@celery_app.task(name="evidence_discovery.discover", bind=True)
def run_campaign_discovery_task(
    self,
    request_payload: dict[str, Any],
    provider: str,
    output_dir: str,
    job_id: str,
) -> dict[str, Any]:
    settings = Settings()
    try:
        store, token = _claim(self, settings, job_id)
    except CampaignAlreadyCompleted as completed:
        return completed.payload
    try:
        callback = _progress_callback(self, store, job_id, token)
        callback(
            "building_queries",
            message="Building searches from the campaign brief",
        )
        request = CampaignRequest.from_dict(request_payload)
        result = CampaignDiscoveryEngine(
            settings=settings,
            search_client=ProviderSearchClient(
                settings=settings,
                provider=provider,
            ),
        ).discover(request, progress_callback=callback)
        callback(
            "writing_discovery",
            message="Writing candidates and route decisions",
        )
        destination = Path(output_dir)
        destination.mkdir(parents=True, exist_ok=True)
        result_path = destination / "campaign_discovery.json"
        result_path.write_text(
            json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        summary = {
            "campaign_id": result.campaign_id,
            "accepted": result.accepted_count,
            "rejected": result.rejected_count,
            "candidates": len(result.candidates),
            "route_events": len(result.route_events),
            "warnings": len(result.warnings),
            "result_json_path": str(result_path),
        }
        store.mark_completed(
            job_id,
            result_json_path=str(result_path),
            summary=summary,
            execution_token=token,
        )
        return {
            **summary,
            "job_id": job_id,
            "duplicate_delivery_ignored": False,
        }
    except Ignore:
        raise
    except Exception as exc:
        store.mark_failed(job_id, str(exc), execution_token=token)
        raise


@celery_app.task(name="evidence_discovery.capture", bind=True)
def run_campaign_capture_task(
    self,
    discovery_json_path: str,
    selected_candidate_ids: list[str],
    manual_urls: list[str],
    respect_robots_txt: bool,
    capture_provider: str,
    output_dir: str,
    job_id: str,
) -> dict[str, Any]:
    settings = Settings()
    try:
        store, token = _claim(self, settings, job_id)
    except CampaignAlreadyCompleted as completed:
        return completed.payload
    try:
        discovery = CampaignDiscoveryResult.from_dict(
            json.loads(
                Path(discovery_json_path).read_text(encoding="utf-8")
            )
        )
        result = CampaignEvidenceCapture(settings).capture(
            discovery,
            selected_candidate_ids=selected_candidate_ids,
            manual_urls=manual_urls,
            capture_provider=capture_provider,
            respect_robots_txt=respect_robots_txt,
            output_directory=Path(output_dir),
            progress_callback=_progress_callback(
                self,
                store,
                job_id,
                token,
            ),
        )
        result_path = Path(result.manifest_path)
        summary = {
            "bundle_id": result.bundle_id,
            "captured_usable": result.successful_count,
            "needs_review": result.review_count,
            "failed": result.failed_count,
            "archive_path": result.archive_path,
            "result_json_path": str(result_path),
        }
        store.mark_completed(
            job_id,
            result_json_path=str(result_path),
            summary=summary,
            execution_token=token,
        )
        return {
            **summary,
            "job_id": job_id,
            "duplicate_delivery_ignored": False,
        }
    except Ignore:
        raise
    except Exception as exc:
        store.mark_failed(job_id, str(exc), execution_token=token)
        raise


discovery_task = cast(Task, run_campaign_discovery_task)
capture_task = cast(Task, run_campaign_capture_task)


def enqueue_campaign_discovery(
    request: CampaignRequest,
    *,
    provider: str,
    output_dir: Path,
    job_id: str,
) -> Any:
    return discovery_task.apply_async(
        args=[request.to_dict(), provider, str(output_dir), job_id],
        task_id=job_id,
    )


def enqueue_campaign_capture(
    *,
    discovery_json_path: str,
    selected_candidate_ids: list[str],
    manual_urls: list[str],
    respect_robots_txt: bool,
    capture_provider: str,
    output_dir: Path,
    job_id: str,
) -> Any:
    return capture_task.apply_async(
        args=[
            discovery_json_path,
            selected_candidate_ids,
            manual_urls,
            respect_robots_txt,
            capture_provider,
            str(output_dir),
            job_id,
        ],
        task_id=job_id,
    )
