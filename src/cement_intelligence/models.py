"""Data contracts for cement discovery, selection, and evidence intake."""

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
    return f"{prefix}_{sha256(normalized.encode('utf-8')).hexdigest()[:16]}"


class ResearchMode(StrEnum):
    KNOWN_PLANT = "known_plant"
    LOCATION_SCAN = "location_scan"
    BEST_DOCUMENTED = "best_documented"


class DocumentType(StrEnum):
    EIA_EMP = "eia_emp"
    ENVIRONMENTAL_CLEARANCE = "environmental_clearance"
    PRE_FEASIBILITY = "pre_feasibility"
    TERMS_OF_REFERENCE = "terms_of_reference"
    EAC_MINUTES = "eac_minutes"
    COMPLIANCE_REPORT = "compliance_report"
    PUBLIC_HEARING = "public_hearing"
    COMPANY_REPORT = "company_report"
    SUSTAINABILITY_REPORT = "sustainability_report"
    BEE_PAT = "bee_pat"
    OEM_TECHNICAL = "oem_technical"
    TENDER = "tender"
    PATENT = "patent"
    ACADEMIC = "academic"
    TECHNICAL_REFERENCE = "technical_reference"
    NEWS = "news"
    OTHER = "other"


class ConfidenceStatus(StrEnum):
    VERIFIED = "verified"
    CORROBORATED = "corroborated"
    DERIVED = "derived"
    ESTIMATED = "estimated"
    ASSUMED = "assumed"
    UNKNOWN = "unknown"


class EvidenceScope(StrEnum):
    EXACT_PLANT = "exact_plant"
    EXACT_COMPANY = "exact_company"
    CEMENT_SECTOR_REFERENCE = "cement_sector_reference"
    EQUIPMENT_REFERENCE = "equipment_reference"
    REGULATORY_REFERENCE = "regulatory_reference"
    UNRESOLVED = "unresolved"
    IRRELEVANT = "irrelevant"


@dataclass(frozen=True, slots=True)
class CementDiscoveryRequest:
    mode: ResearchMode = ResearchMode.BEST_DOCUMENTED
    country: str = "India"
    state: str | None = None
    company_name: str | None = None
    plant_name: str | None = None
    plant_type: str = "integrated cement plant"
    objective: str = "quarry-to-dispatch reconstruction"
    max_queries: int = 8
    results_per_query: int = 10
    max_candidate_plants: int = 10
    max_bundle_documents: int = 8

    def __post_init__(self) -> None:
        if not self.country.strip():
            raise ValueError("country is required")
        if self.mode == ResearchMode.KNOWN_PLANT and not (
            (self.company_name or "").strip() or (self.plant_name or "").strip()
        ):
            raise ValueError("known-plant mode requires a company or plant name")
        for name in (
            "max_queries",
            "results_per_query",
            "max_candidate_plants",
            "max_bundle_documents",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be at least 1")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CementDiscoveryRequest":
        payload = dict(value)
        payload["mode"] = ResearchMode(str(payload.get("mode") or ResearchMode.BEST_DOCUMENTED))
        return cls(**payload)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["mode"] = self.mode.value
        return value


@dataclass(frozen=True, slots=True)
class RawSearchResult:
    query: str
    url: str
    title: str | None
    snippet: str
    provider: str
    rank: int


@dataclass(slots=True)
class DocumentCandidate:
    candidate_id: str
    url: str
    title: str | None
    snippet: str
    provider: str
    query: str
    document_type: DocumentType
    evidence_scope: EvidenceScope
    company_hint: str | None
    plant_hint: str | None
    location_hint: str | None
    publisher_domain: str
    is_government: bool
    is_probably_pdf: bool
    authority_score: float
    plant_specificity_score: float
    technical_value_score: float
    crawlability_score: float
    overall_score: float
    coverage_categories: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["document_type"] = self.document_type.value
        value["evidence_scope"] = self.evidence_scope.value
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "DocumentCandidate":
        payload = dict(value)
        payload["document_type"] = DocumentType(str(payload["document_type"]))
        payload["evidence_scope"] = EvidenceScope(
            str(payload.get("evidence_scope") or EvidenceScope.UNRESOLVED)
        )
        for name in ("coverage_categories", "reasons"):
            payload[name] = tuple(payload.get(name) or ())
        return cls(**payload)


@dataclass(slots=True)
class EvidenceBundle:
    bundle_id: str
    plant_candidate_id: str
    document_ids: list[str]
    expected_coverage: tuple[str, ...]
    missing_categories: tuple[str, ...]
    bundle_score: float
    estimated_documents: int
    rationale: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "EvidenceBundle":
        payload = dict(value)
        for name in ("expected_coverage", "missing_categories", "rationale"):
            payload[name] = tuple(payload.get(name) or ())
        return cls(**payload)


@dataclass(slots=True)
class PlantCandidate:
    plant_candidate_id: str
    company_name: str
    plant_name: str
    location: str | None
    plant_type: str
    document_ids: list[str]
    anchor_document_id: str | None
    researchability_score: float
    expected_coverage: tuple[str, ...]
    missing_categories: tuple[str, ...]
    reasons: tuple[str, ...]
    recommended: bool = False
    bundle: EvidenceBundle | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        if self.bundle is not None:
            value["bundle"] = self.bundle.to_dict()
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PlantCandidate":
        payload = dict(value)
        for name in ("expected_coverage", "missing_categories", "reasons"):
            payload[name] = tuple(payload.get(name) or ())
        if payload.get("bundle"):
            payload["bundle"] = EvidenceBundle.from_dict(payload["bundle"])
        return cls(**payload)


@dataclass(slots=True)
class CementDiscoveryResult:
    run_id: str
    request: CementDiscoveryRequest
    queries: list[str]
    documents: list[DocumentCandidate]
    plants: list[PlantCandidate]
    warnings: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        request = asdict(self.request)
        request["mode"] = self.request.mode.value
        return {
            "run_id": self.run_id,
            "request": request,
            "queries": self.queries,
            "documents": [item.to_dict() for item in self.documents],
            "plants": [item.to_dict() for item in self.plants],
            "warnings": self.warnings,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CementDiscoveryResult":
        return cls(
            run_id=str(value["run_id"]),
            request=CementDiscoveryRequest.from_dict(dict(value["request"])),
            queries=[str(item) for item in value.get("queries") or []],
            documents=[
                DocumentCandidate.from_dict(dict(item))
                for item in value.get("documents") or []
            ],
            plants=[
                PlantCandidate.from_dict(dict(item))
                for item in value.get("plants") or []
            ],
            warnings=[str(item) for item in value.get("warnings") or []],
            created_at=str(value.get("created_at") or utc_now_iso()),
        )


@dataclass(slots=True)
class CapturedEvidence:
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
    text_characters: int = 0
    text_preview: str | None = None
    text_preview_truncated: bool = False
    original_binary_preserved: bool = False
    validation_notes: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CementIntakeResult:
    intake_id: str
    discovery_run_id: str
    plant_candidate_id: str
    output_directory: str
    capture_provider: str
    captured: list[CapturedEvidence]
    approved_at: str = field(default_factory=utc_now_iso)

    @property
    def successful_count(self) -> int:
        return sum(item.status == "captured_usable" for item in self.captured)

    @property
    def failed_count(self) -> int:
        return sum(item.status == "failed" for item in self.captured)

    @property
    def review_count(self) -> int:
        return sum(
            item.status
            in {
                "captured_thin",
                "captured_wrong_content",
                "captured_unreadable",
            }
            for item in self.captured
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "intake_id": self.intake_id,
            "discovery_run_id": self.discovery_run_id,
            "plant_candidate_id": self.plant_candidate_id,
            "output_directory": self.output_directory,
            "successful_count": self.successful_count,
            "failed_count": self.failed_count,
            "review_count": self.review_count,
            "captured": [item.to_dict() for item in self.captured],
            "approved_at": self.approved_at,
        }
