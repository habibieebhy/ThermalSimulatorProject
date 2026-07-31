"""Domain-neutral contracts for guided evidence discovery campaigns."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(prefix: str, *parts: object) -> str:
    normalized = "|".join(str(part).strip().casefold() for part in parts)
    digest = sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


class DiscoveryDirection(StrEnum):
    OFFICIAL = "official_sources"
    GOVERNMENT = "government_and_regulatory"
    ACADEMIC = "academic_and_research"
    PATENTS = "patents"
    TECHNICAL = "technical_documents"
    NEWS_ARCHIVES = "news_and_archives"
    OPEN_WEB = "open_web"


class DomainPolicy(StrEnum):
    OPEN_WEB = "open_web"
    SEED_DOMAINS_ONLY = "seed_domains_only"
    SEED_PLUS_AUTHORITY = "seed_plus_authority"


class CandidateDecision(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class CampaignRequest:
    campaign_name: str
    topic: str
    seed_urls: tuple[str, ...] = ()
    seed_domains: tuple[str, ...] = ()
    manual_queries: tuple[str, ...] = ()
    directions: tuple[DiscoveryDirection, ...] = (
        DiscoveryDirection.OFFICIAL,
        DiscoveryDirection.GOVERNMENT,
        DiscoveryDirection.TECHNICAL,
    )
    domain_policy: DomainPolicy = DomainPolicy.OPEN_WEB
    geography: str | None = None
    max_queries: int = 8
    results_per_query: int = 8
    max_candidates: int = 150
    max_site_pages: int = 20
    max_depth: int = 2
    max_capture_documents: int = 20
    minimum_relevance: float = 0.28

    def __post_init__(self) -> None:
        if not self.campaign_name.strip():
            raise ValueError("campaign_name is required")
        if not (
            self.topic.strip()
            or self.seed_urls
            or self.seed_domains
            or self.manual_queries
        ):
            raise ValueError(
                "Provide a topic, URL, domain, or manual search query"
            )
        for name in (
            "max_queries",
            "results_per_query",
            "max_candidates",
            "max_capture_documents",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be at least 1")
        if self.max_site_pages < 0:
            raise ValueError("max_site_pages cannot be negative")
        if not 0 <= self.max_depth <= 8:
            raise ValueError("max_depth must be between 0 and 8")
        if not 0.0 <= self.minimum_relevance <= 1.0:
            raise ValueError("minimum_relevance must be between 0 and 1")
        if (
            self.domain_policy != DomainPolicy.OPEN_WEB
            and not self.seed_domains
            and not self.seed_urls
        ):
            raise ValueError(
                "A restricted domain policy requires a seed URL or domain"
            )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["directions"] = [item.value for item in self.directions]
        payload["domain_policy"] = self.domain_policy.value
        return payload

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CampaignRequest":
        payload = dict(value)
        payload["seed_urls"] = tuple(payload.get("seed_urls") or ())
        payload["seed_domains"] = tuple(payload.get("seed_domains") or ())
        payload["manual_queries"] = tuple(payload.get("manual_queries") or ())
        payload["directions"] = tuple(
            DiscoveryDirection(str(item))
            for item in payload.get("directions")
            or (
                DiscoveryDirection.OFFICIAL.value,
                DiscoveryDirection.GOVERNMENT.value,
                DiscoveryDirection.TECHNICAL.value,
            )
        )
        payload["domain_policy"] = DomainPolicy(
            str(payload.get("domain_policy") or DomainPolicy.OPEN_WEB)
        )
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class SearchResult:
    query: str
    url: str
    title: str | None
    snippet: str
    provider: str
    rank: int


@dataclass(slots=True)
class CampaignCandidate:
    candidate_id: str
    url: str
    title: str | None
    snippet: str
    publisher_domain: str
    provider: str
    query: str
    direction: str
    source_kind: str
    decision: CandidateDecision
    relevance_score: float
    is_probably_pdf: bool
    is_authority_source: bool
    reasons: tuple[str, ...] = ()
    parent_url: str | None = None
    depth: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["decision"] = self.decision.value
        return payload

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CampaignCandidate":
        payload = dict(value)
        payload["decision"] = CandidateDecision(str(payload["decision"]))
        payload["reasons"] = tuple(payload.get("reasons") or ())
        return cls(**payload)


@dataclass(slots=True)
class RouteEvent:
    sequence: int
    stage: str
    action: str
    reason: str
    query: str | None = None
    url: str | None = None
    parent_url: str | None = None
    domain: str | None = None
    direction: str | None = None
    score: float | None = None
    depth: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RouteEvent":
        return cls(**value)


@dataclass(slots=True)
class CampaignDiscoveryResult:
    campaign_id: str
    request: CampaignRequest
    queries: list[str]
    candidates: list[CampaignCandidate]
    route_events: list[RouteEvent]
    warnings: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now_iso)

    @property
    def accepted_count(self) -> int:
        return sum(
            item.decision == CandidateDecision.ACCEPTED
            for item in self.candidates
        )

    @property
    def rejected_count(self) -> int:
        return sum(
            item.decision == CandidateDecision.REJECTED
            for item in self.candidates
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "request": self.request.to_dict(),
            "queries": self.queries,
            "candidates": [item.to_dict() for item in self.candidates],
            "route_events": [item.to_dict() for item in self.route_events],
            "warnings": self.warnings,
            "created_at": self.created_at,
            "accepted_count": self.accepted_count,
            "rejected_count": self.rejected_count,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CampaignDiscoveryResult":
        return cls(
            campaign_id=str(value["campaign_id"]),
            request=CampaignRequest.from_dict(dict(value["request"])),
            queries=[str(item) for item in value.get("queries") or []],
            candidates=[
                CampaignCandidate.from_dict(dict(item))
                for item in value.get("candidates") or []
            ],
            route_events=[
                RouteEvent.from_dict(dict(item))
                for item in value.get("route_events") or []
            ],
            warnings=[str(item) for item in value.get("warnings") or []],
            created_at=str(value.get("created_at") or utc_now_iso()),
        )


@dataclass(slots=True)
class CampaignCapture:
    candidate_id: str
    url: str
    status: str
    http_status: int | None = None
    content_type: str | None = None
    content_sha256: str | None = None
    size_bytes: int | None = None
    artifact_path: str | None = None
    object_uri: str | None = None
    capture_method: str | None = None
    extracted_text_path: str | None = None
    bundle_document_path: str | None = None
    bundle_text_path: str | None = None
    text_characters: int = 0
    original_binary_preserved: bool = False
    validation_notes: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CampaignCapture":
        return cls(**value)


@dataclass(slots=True)
class CampaignBundleResult:
    bundle_id: str
    campaign_id: str
    capture_provider: str
    output_directory: str
    manifest_path: str
    archive_path: str
    captures: list[CampaignCapture]
    created_at: str = field(default_factory=utc_now_iso)

    @property
    def successful_count(self) -> int:
        return sum(item.status == "captured_usable" for item in self.captures)

    @property
    def review_count(self) -> int:
        return sum(
            item.status
            in {
                "captured_thin",
                "captured_unreadable",
                "captured_wrong_content",
            }
            for item in self.captures
        )

    @property
    def failed_count(self) -> int:
        return sum(item.status == "failed" for item in self.captures)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "campaign_id": self.campaign_id,
            "capture_provider": self.capture_provider,
            "output_directory": self.output_directory,
            "manifest_path": self.manifest_path,
            "archive_path": self.archive_path,
            "captures": [item.to_dict() for item in self.captures],
            "successful_count": self.successful_count,
            "review_count": self.review_count,
            "failed_count": self.failed_count,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CampaignBundleResult":
        return cls(
            bundle_id=str(value["bundle_id"]),
            campaign_id=str(value["campaign_id"]),
            capture_provider=str(value.get("capture_provider") or "services"),
            output_directory=str(value["output_directory"]),
            manifest_path=str(value["manifest_path"]),
            archive_path=str(value["archive_path"]),
            captures=[
                CampaignCapture.from_dict(dict(item))
                for item in value.get("captures") or []
            ],
            created_at=str(value.get("created_at") or utc_now_iso()),
        )
