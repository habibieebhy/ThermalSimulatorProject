"""Approval-gated capture of selected cement evidence documents."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from mattress_intelligence.crawler import (
    EvidenceFetcher,
    FetchError,
    HttpFetcher,
    HybridBrowserFetcher,
)
from mattress_intelligence.firecrawl import FirecrawlClient
from mattress_intelligence.jina import JinaReaderClient
from mattress_intelligence.normalization import canonicalize_url
from mattress_intelligence.settings import Settings

from .discovery import classify_document
from .models import (
    CapturedEvidence,
    CementDiscoveryResult,
    CementIntakeResult,
    DocumentCandidate,
    EvidenceScope,
    stable_id,
    utc_now_iso,
)


@dataclass(slots=True)
class CementEvidenceIntake:
    settings: Settings

    def _fetcher(
        self,
        *,
        respect_robots_txt: bool,
        capture_provider: str,
    ) -> EvidenceFetcher:
        provider = capture_provider.strip().casefold()
        if provider not in {"services", "firecrawl", "jina", "tavily"}:
            raise ValueError(f"Unsupported cement capture provider: {capture_provider}")
        fetcher_class = (
            HybridBrowserFetcher if self.settings.render_javascript else HttpFetcher
        )
        primary = fetcher_class(
            self.settings,
            respect_robots_txt=respect_robots_txt,
        )
        reader = (
            JinaReaderClient(
                self.settings.jina_api_key,
                timeout_seconds=self.settings.jina_timeout_seconds,
            )
            if provider in {"services", "jina"}
            and self.settings.jina_reader_enabled
            else None
        )
        firecrawl = (
            FirecrawlClient(
                self.settings.firecrawl_api_key,
                timeout_seconds=self.settings.firecrawl_timeout_seconds,
                wait_ms=self.settings.firecrawl_wait_ms,
            )
            if provider in {"services", "firecrawl"}
            and self.settings.firecrawl_enabled
            and self.settings.firecrawl_api_key
            else None
        )
        return EvidenceFetcher(
            primary,
            self.settings,
            reader=reader,
            firecrawl=firecrawl,
        )

    def capture_approved_bundle(
        self,
        discovery: CementDiscoveryResult,
        *,
        plant_candidate_id: str,
        selected_document_ids: list[str],
        manual_urls: list[str] | None = None,
        respect_robots_txt: bool = True,
        capture_provider: str = "services",
        output_directory: Path | None = None,
        progress_callback: Callable[..., None] | None = None,
    ) -> CementIntakeResult:
        if not selected_document_ids and not manual_urls:
            raise ValueError(
                "Select a discovered document or provide at least one manual URL"
            )
        plant = next(
            (
                item
                for item in discovery.plants
                if item.plant_candidate_id == plant_candidate_id
            ),
            None,
        )
        if plant is None:
            raise ValueError(f"Unknown plant candidate: {plant_candidate_id}")

        documents_by_id: dict[str, DocumentCandidate] = {
            item.candidate_id: item for item in discovery.documents
        }
        selected: list[DocumentCandidate] = []
        for document_id in dict.fromkeys(selected_document_ids):
            document = documents_by_id.get(document_id)
            if document is None:
                raise ValueError(f"Unknown document candidate: {document_id}")
            selected.append(document)

        documents_by_url = {item.url: item for item in discovery.documents}
        selected_ids = {item.candidate_id for item in selected}
        for raw_url in dict.fromkeys(manual_urls or []):
            url = canonicalize_url(raw_url)
            if not url.startswith(("http://", "https://")):
                raise ValueError(f"Manual evidence URL must use HTTP or HTTPS: {raw_url}")
            existing = documents_by_url.get(url)
            if existing is not None:
                if existing.candidate_id not in selected_ids:
                    selected.append(existing)
                    selected_ids.add(existing.candidate_id)
                continue
            domain = (urlsplit(url).hostname or "").casefold().removeprefix("www.")
            manual = DocumentCandidate(
                candidate_id=stable_id("manual_doc", url),
                url=url,
                title=None,
                snippet="Manually submitted evidence lead",
                provider="manual",
                query="manual user submission",
                document_type=classify_document(url, None, ""),
                evidence_scope=EvidenceScope.EXACT_PLANT,
                company_hint=plant.company_name,
                plant_hint=plant.plant_name,
                location_hint=plant.location,
                publisher_domain=domain,
                is_government=domain.endswith((".gov.in", ".nic.in", ".gov")),
                is_probably_pdf=urlsplit(url).path.casefold().endswith(".pdf"),
                authority_score=0.0,
                plant_specificity_score=0.0,
                technical_value_score=0.0,
                crawlability_score=1.0,
                overall_score=0.0,
                coverage_categories=(),
                reasons=(
                    "Manually submitted by the user; not algorithmically scored",
                ),
            )
            selected.append(manual)
            selected_ids.add(manual.candidate_id)

        intake_id = stable_id(
            "cement_intake",
            discovery.run_id,
            plant_candidate_id,
            *(item.candidate_id for item in selected),
            utc_now_iso(),
        )
        output_directory = output_directory or (
            self.settings.output_dir / "cement_intake" / intake_id
        )
        output_directory.mkdir(parents=True, exist_ok=True)
        fetcher = self._fetcher(
            respect_robots_txt=respect_robots_txt,
            capture_provider=capture_provider,
        )
        captured: list[CapturedEvidence] = []
        try:
            for document_number, candidate in enumerate(selected, start=1):
                if progress_callback is not None:
                    progress_callback(
                        "capturing",
                        current=document_number,
                        total=len(selected),
                        url=candidate.url,
                        message=f"Capturing approved evidence: {candidate.url}",
                    )
                try:
                    document = fetcher.fetch(candidate.url)
                    text_limit = 200_000
                    text = document.extracted_text(max_characters=text_limit + 1)
                except (FetchError, RuntimeError, ValueError) as exc:
                    captured.append(
                        CapturedEvidence(
                            candidate_id=candidate.candidate_id,
                            url=candidate.url,
                            status="failed",
                            error=str(exc),
                        )
                    )
                    continue
                text_was_truncated = len(text) > text_limit
                if text_was_truncated:
                    text = text[:text_limit]
                text_directory = output_directory / "extracted_text"
                text_directory.mkdir(parents=True, exist_ok=True)
                extracted_text_path = (
                    text_directory / f"{candidate.candidate_id}.txt"
                )
                extracted_text_path.write_text(text, encoding="utf-8")
                status, original_binary, validation_notes = self._validate_capture(
                    candidate,
                    content_type=document.content_type,
                    body=document.body,
                    text=text,
                )
                captured.append(
                    CapturedEvidence(
                        candidate_id=candidate.candidate_id,
                        url=document.url,
                        status=status,
                        http_status=document.status,
                        content_type=document.content_type,
                        content_sha256=document.sha256,
                        size_bytes=len(document.body),
                        artifact_path=document.artifact_path,
                        object_uri=document.object_uri,
                        capture_method=document.capture_method,
                        text_characters=len(text),
                        text_preview=text[:2_000] or None,
                        extracted_text_path=str(extracted_text_path),
                        text_preview_truncated=text_was_truncated or len(text) > 2_000,
                        original_binary_preserved=original_binary,
                        validation_notes=validation_notes,
                    )
                )
        finally:
            fetcher.close()

        if progress_callback is not None:
            progress_callback(
                "writing_manifests",
                message="Writing approved-bundle and capture-quality manifests",
            )
        result = CementIntakeResult(
            intake_id=intake_id,
            discovery_run_id=discovery.run_id,
            plant_candidate_id=plant_candidate_id,
            output_directory=str(output_directory),
            capture_provider=capture_provider.strip().casefold(),
            captured=captured,
        )
        self._write_manifests(
            output_directory,
            discovery=discovery,
            plant_candidate_id=plant_candidate_id,
            selected=selected,
            result=result,
        )
        return result

    @staticmethod
    def _validate_capture(
        candidate: DocumentCandidate,
        *,
        content_type: str,
        body: bytes,
        text: str,
    ) -> tuple[str, bool, list[str]]:
        media_type = content_type.split(";", 1)[0].strip().casefold()
        path = urlsplit(candidate.url).path.casefold()
        expected_pdf = (
            candidate.is_probably_pdf
            or path.endswith(".pdf")
            or "downloadpfdfile" in path
        )
        actual_pdf = body.lstrip().startswith(b"%PDF")
        notes: list[str] = []

        if actual_pdf:
            if len(text.strip()) < 100:
                notes.append(
                    "The original PDF was preserved but contains insufficient native text; "
                    "OCR is required."
                )
                return "captured_unreadable", True, notes
            return "captured_usable", True, notes

        if expected_pdf and media_type in {"text/html", "application/xhtml+xml"}:
            notes.append(
                "The source was expected to be a PDF, but the provider returned HTML."
            )
            return "captured_wrong_content", False, notes

        if expected_pdf and "markdown" in media_type:
            if len(text.strip()) < 100:
                notes.append(
                    "A readable-service fallback returned too little text for the expected PDF."
                )
                return "captured_thin", False, notes
            notes.append(
                "Readable text was captured through a service fallback; the original PDF "
                "binary was not preserved."
            )
            return "captured_usable", False, notes

        if len(text.strip()) < 100:
            notes.append("The captured response contains fewer than 100 readable characters.")
            return "captured_thin", False, notes

        return "captured_usable", False, notes

    @staticmethod
    def _write_manifests(
        output_directory: Path,
        *,
        discovery: CementDiscoveryResult,
        plant_candidate_id: str,
        selected: list[DocumentCandidate],
        result: CementIntakeResult,
    ) -> None:
        (output_directory / "discovery_snapshot.json").write_text(
            json.dumps(discovery.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        (output_directory / "approved_bundle.json").write_text(
            json.dumps(
                {
                    "discovery_run_id": discovery.run_id,
                    "plant_candidate_id": plant_candidate_id,
                    "selected_documents": [item.to_dict() for item in selected],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (output_directory / "intake_manifest.json").write_text(
            json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
