"""Approval-gated capture and portable evidence-bundle export."""

from __future__ import annotations

import csv
import json
import shutil
import zipfile
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

from .models import (
    CampaignBundleResult,
    CampaignCandidate,
    CampaignCapture,
    CampaignDiscoveryResult,
    CandidateDecision,
    stable_id,
    utc_now_iso,
)


@dataclass(slots=True)
class CampaignEvidenceCapture:
    settings: Settings

    def _fetcher(
        self,
        *,
        respect_robots_txt: bool,
        capture_provider: str,
    ) -> EvidenceFetcher:
        provider = capture_provider.strip().casefold()
        if provider not in {"services", "firecrawl", "jina", "tavily"}:
            raise ValueError(f"Unsupported capture provider: {capture_provider}")
        fetcher_class = (
            HybridBrowserFetcher
            if self.settings.render_javascript
            else HttpFetcher
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

    def capture(
        self,
        discovery: CampaignDiscoveryResult,
        *,
        selected_candidate_ids: list[str],
        manual_urls: list[str] | None = None,
        capture_provider: str = "services",
        respect_robots_txt: bool = True,
        output_directory: Path | None = None,
        progress_callback: Callable[..., None] | None = None,
    ) -> CampaignBundleResult:
        if not selected_candidate_ids and not manual_urls:
            raise ValueError(
                "Select a discovered candidate or provide a manual URL"
            )
        by_id = {item.candidate_id: item for item in discovery.candidates}
        selected: list[CampaignCandidate] = []
        for candidate_id in dict.fromkeys(selected_candidate_ids):
            candidate = by_id.get(candidate_id)
            if candidate is None:
                raise ValueError(f"Unknown campaign candidate: {candidate_id}")
            selected.append(candidate)

        existing_urls = {item.url: item for item in discovery.candidates}
        selected_ids = {item.candidate_id for item in selected}
        for raw_url in dict.fromkeys(manual_urls or []):
            url = canonicalize_url(raw_url)
            if not url.startswith(("http://", "https://")):
                raise ValueError(
                    f"Manual evidence URL must use HTTP or HTTPS: {raw_url}"
                )
            existing = existing_urls.get(url)
            if existing is not None:
                if existing.candidate_id not in selected_ids:
                    selected.append(existing)
                    selected_ids.add(existing.candidate_id)
                continue
            domain = (
                urlsplit(url).hostname or ""
            ).casefold().removeprefix("www.")
            candidate = CampaignCandidate(
                candidate_id=stable_id("manual_candidate", url),
                url=url,
                title=None,
                snippet="User-submitted capture lead",
                publisher_domain=domain,
                provider="manual",
                query="manual capture submission",
                direction="manual",
                source_kind="manual_capture",
                decision=CandidateDecision.ACCEPTED,
                relevance_score=0.0,
                is_probably_pdf=urlsplit(url).path.casefold().endswith(".pdf"),
                is_authority_source=False,
                reasons=(
                    "User-submitted lead; not algorithmically ranked",
                ),
            )
            selected.append(candidate)
            selected_ids.add(candidate.candidate_id)

        limit = discovery.request.max_capture_documents
        if len(selected) > limit:
            raise ValueError(
                f"This campaign allows at most {limit} captured documents; "
                f"{len(selected)} were selected"
            )

        bundle_id = stable_id(
            "evidence_bundle",
            discovery.campaign_id,
            *(item.candidate_id for item in selected),
            utc_now_iso(),
        )
        output_directory = output_directory or (
            self.settings.output_dir / "research_campaigns" / bundle_id
        )
        bundle_root = output_directory / "evidence_bundle"
        document_directory = bundle_root / "documents"
        text_directory = bundle_root / "extracted_text"
        document_directory.mkdir(parents=True, exist_ok=True)
        text_directory.mkdir(parents=True, exist_ok=True)

        fetcher = self._fetcher(
            respect_robots_txt=respect_robots_txt,
            capture_provider=capture_provider,
        )
        captures: list[CampaignCapture] = []
        try:
            for number, candidate in enumerate(selected, start=1):
                if progress_callback is not None:
                    progress_callback(
                        "capturing",
                        current=number,
                        total=len(selected),
                        message=f"Capturing approved evidence: {candidate.url}",
                        url=candidate.url,
                    )
                try:
                    document = fetcher.fetch(candidate.url)
                    text = document.extracted_text(max_characters=500_000)
                except (FetchError, RuntimeError, ValueError) as exc:
                    captures.append(
                        CampaignCapture(
                            candidate_id=candidate.candidate_id,
                            url=candidate.url,
                            status="failed",
                            error=str(exc),
                        )
                    )
                    continue

                source = Path(document.artifact_path)
                suffix = source.suffix or (
                    ".pdf"
                    if document.body.lstrip().startswith(b"%PDF")
                    else ".bin"
                )
                bundled_document = (
                    document_directory / f"{candidate.candidate_id}{suffix}"
                )
                if source.is_file():
                    shutil.copy2(source, bundled_document)
                else:
                    bundled_document.write_bytes(document.body)
                bundled_text = (
                    text_directory / f"{candidate.candidate_id}.txt"
                )
                bundled_text.write_text(text, encoding="utf-8")
                status, preserved, notes = self._validate(
                    candidate,
                    content_type=document.content_type,
                    body=document.body,
                    text=text,
                )
                captures.append(
                    CampaignCapture(
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
                        extracted_text_path=str(bundled_text),
                        bundle_document_path=str(
                            bundled_document.relative_to(bundle_root)
                        ),
                        bundle_text_path=str(
                            bundled_text.relative_to(bundle_root)
                        ),
                        text_characters=len(text),
                        original_binary_preserved=preserved,
                        validation_notes=notes,
                    )
                )
        finally:
            fetcher.close()

        if progress_callback is not None:
            progress_callback(
                "writing_bundle",
                message="Writing portable BRIXTA evidence bundle",
            )
        manifest_path = bundle_root / "campaign_manifest.json"
        archive_path = output_directory / "evidence_bundle.zip"
        result = CampaignBundleResult(
            bundle_id=bundle_id,
            campaign_id=discovery.campaign_id,
            capture_provider=capture_provider.strip().casefold(),
            output_directory=str(output_directory),
            manifest_path=str(manifest_path),
            archive_path=str(archive_path),
            captures=captures,
        )
        self._write_bundle_files(
            bundle_root,
            discovery=discovery,
            selected=selected,
            result=result,
        )
        with zipfile.ZipFile(
            archive_path,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            for path in sorted(bundle_root.rglob("*")):
                if path.is_file():
                    archive.write(
                        path,
                        arcname=path.relative_to(bundle_root),
                    )
        return result

    @staticmethod
    def _validate(
        candidate: CampaignCandidate,
        *,
        content_type: str,
        body: bytes,
        text: str,
    ) -> tuple[str, bool, list[str]]:
        media_type = content_type.split(";", 1)[0].strip().casefold()
        expected_pdf = (
            candidate.is_probably_pdf
            or urlsplit(candidate.url).path.casefold().endswith(".pdf")
        )
        actual_pdf = body.lstrip().startswith(b"%PDF")
        notes: list[str] = []
        if actual_pdf:
            if len(text.strip()) < 100:
                notes.append(
                    "Original PDF preserved, but OCR is required."
                )
                return "captured_unreadable", True, notes
            return "captured_usable", True, notes
        if expected_pdf and media_type in {
            "text/html",
            "application/xhtml+xml",
        }:
            notes.append("Expected a PDF but received HTML.")
            return "captured_wrong_content", False, notes
        if len(text.strip()) < 100:
            notes.append("Fewer than 100 readable characters were extracted.")
            return "captured_thin", False, notes
        return "captured_usable", False, notes

    @staticmethod
    def _write_bundle_files(
        bundle_root: Path,
        *,
        discovery: CampaignDiscoveryResult,
        selected: list[CampaignCandidate],
        result: CampaignBundleResult,
    ) -> None:
        manifest = {
            "bundle_schema": "brixta-evidence-bundle/v1",
            "bundle": result.to_dict(),
            "campaign": discovery.to_dict(),
            "selected_candidates": [item.to_dict() for item in selected],
            "ingestion_guidance": {
                "purpose": (
                    "Portable evidence for manual or API ingestion into BRIXTA RAG"
                ),
                "originals": "documents/",
                "extracted_text": "extracted_text/",
                "provenance": "campaign_manifest.json",
            },
        }
        (bundle_root / "campaign_manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        with (bundle_root / "candidate_log.csv").open(
            "w",
            encoding="utf-8",
            newline="",
        ) as handle:
            fieldnames = (
                "candidate_id",
                "decision",
                "score",
                "direction",
                "source_kind",
                "domain",
                "title",
                "url",
                "query",
                "reasons",
            )
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for item in discovery.candidates:
                writer.writerow(
                    {
                        "candidate_id": item.candidate_id,
                        "decision": item.decision.value,
                        "score": item.relevance_score,
                        "direction": item.direction,
                        "source_kind": item.source_kind,
                        "domain": item.publisher_domain,
                        "title": item.title,
                        "url": item.url,
                        "query": item.query,
                        "reasons": "; ".join(item.reasons),
                    }
                )
        with (bundle_root / "route_log.csv").open(
            "w",
            encoding="utf-8",
            newline="",
        ) as handle:
            fieldnames = (
                "sequence",
                "stage",
                "action",
                "reason",
                "query",
                "url",
                "parent_url",
                "domain",
                "direction",
                "score",
                "depth",
            )
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for event in discovery.route_events:
                writer.writerow(event.to_dict())
