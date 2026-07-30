"""Search, classify, group, and rank public cement-plant evidence."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Protocol
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from mattress_intelligence.firecrawl import FirecrawlClient, FirecrawlError
from mattress_intelligence.jina import JinaError, JinaSearchClient
from mattress_intelligence.network import (
    RETRYABLE_TRANSPORT_ERRORS,
    http_error_detail,
)
from mattress_intelligence.normalization import canonicalize_url, clean_text
from mattress_intelligence.settings import Settings

from .models import (
    CementDiscoveryRequest,
    CementDiscoveryResult,
    DocumentCandidate,
    DocumentType,
    EvidenceScope,
    EvidenceBundle,
    PlantCandidate,
    RawSearchResult,
    ResearchMode,
    stable_id,
    utc_now_iso,
)
from .queries import build_discovery_queries


CORE_COVERAGE = (
    "identity_location",
    "capacity",
    "process_route",
    "equipment",
    "materials",
    "energy",
    "storage",
    "environment",
    "packing_dispatch",
    "product_mix",
    "expansion_history",
)

DOCUMENT_COVERAGE: dict[DocumentType, tuple[str, ...]] = {
    DocumentType.EIA_EMP: (
        "identity_location",
        "capacity",
        "process_route",
        "equipment",
        "materials",
        "energy",
        "storage",
        "environment",
        "packing_dispatch",
        "product_mix",
    ),
    DocumentType.ENVIRONMENTAL_CLEARANCE: (
        "identity_location",
        "capacity",
        "materials",
        "environment",
        "expansion_history",
    ),
    DocumentType.PRE_FEASIBILITY: (
        "identity_location",
        "capacity",
        "process_route",
        "equipment",
        "materials",
        "energy",
        "storage",
        "packing_dispatch",
        "product_mix",
    ),
    DocumentType.TERMS_OF_REFERENCE: (
        "identity_location",
        "capacity",
        "environment",
        "expansion_history",
    ),
    DocumentType.EAC_MINUTES: (
        "identity_location",
        "capacity",
        "environment",
        "expansion_history",
    ),
    DocumentType.COMPLIANCE_REPORT: (
        "identity_location",
        "capacity",
        "environment",
        "expansion_history",
    ),
    DocumentType.PUBLIC_HEARING: (
        "identity_location",
        "environment",
        "packing_dispatch",
    ),
    DocumentType.COMPANY_REPORT: (
        "capacity",
        "energy",
        "product_mix",
        "expansion_history",
    ),
    DocumentType.SUSTAINABILITY_REPORT: (
        "energy",
        "environment",
        "materials",
        "product_mix",
    ),
    DocumentType.BEE_PAT: ("energy", "capacity", "product_mix"),
    DocumentType.OEM_TECHNICAL: ("equipment", "capacity", "energy", "process_route"),
    DocumentType.TENDER: (
        "equipment",
        "capacity",
        "energy",
        "process_route",
        "storage",
    ),
    DocumentType.PATENT: ("equipment", "process_route", "materials"),
    DocumentType.ACADEMIC: ("equipment", "energy", "materials", "process_route"),
    DocumentType.TECHNICAL_REFERENCE: ("process_route", "equipment", "energy"),
    DocumentType.NEWS: ("capacity", "expansion_history"),
    DocumentType.OTHER: (),
}

ANCHOR_TYPES = {
    DocumentType.EIA_EMP,
    DocumentType.PRE_FEASIBILITY,
    DocumentType.ENVIRONMENTAL_CLEARANCE,
}

GOVERNMENT_DOMAINS = (
    "parivesh.nic.in",
    "environmentclearance.nic.in",
    "moef.gov.in",
    "moefcc.gov.in",
    "beeindia.gov.in",
    "cpcb.nic.in",
    "gov.in",
    "nic.in",
    "epa.gov",
    "europa.eu",
)

OEM_DOMAINS = (
    "khd.com",
    "loesche.com",
    "gebr-pfeiffer.com",
    "thyssenkrupp.com",
    "polysius.com",
    "ikn.eu",
    "fivesgroup.com",
    "christianpfeiffer.com",
    "claudiuspeters.com",
    "abb.com",
    "siemens.com",
)

DOCUMENT_PATTERNS: tuple[tuple[DocumentType, tuple[str, ...]], ...] = (
    (DocumentType.EIA_EMP, ("eia/emp", "eia emp", "environment impact assessment")),
    (
        DocumentType.PRE_FEASIBILITY,
        ("pre-feasibility", "pre feasibility", "prefeasibility", "pfr"),
    ),
    (
        DocumentType.ENVIRONMENTAL_CLEARANCE,
        ("environmental clearance", "ec letter", "ec granted", "ec compliance"),
    ),
    (
        DocumentType.TERMS_OF_REFERENCE,
        ("terms of reference", "proposed tor", "standard tor", "tor letter"),
    ),
    (
        DocumentType.EAC_MINUTES,
        ("eac meeting", "eac minutes", "expert appraisal committee"),
    ),
    (
        DocumentType.COMPLIANCE_REPORT,
        ("compliance report", "certified compliance", "six monthly compliance"),
    ),
    (DocumentType.PUBLIC_HEARING, ("public hearing", "public consultation")),
    (
        DocumentType.SUSTAINABILITY_REPORT,
        ("sustainability report", "esg report", "integrated report"),
    ),
    (
        DocumentType.COMPANY_REPORT,
        ("annual report", "investor presentation", "investor relations"),
    ),
    (
        DocumentType.BEE_PAT,
        ("perform achieve trade", "bee pat", "pat cycle", "normalization document"),
    ),
    (
        DocumentType.TENDER,
        ("tender", "invitation for bid", "technical specification", "procurement"),
    ),
    (DocumentType.PATENT, ("patent", "patentscope", "espacenet")),
    (
        DocumentType.ACADEMIC,
        ("thesis", "dissertation", "journal", "research paper", "shodhganga"),
    ),
    (
        DocumentType.TECHNICAL_REFERENCE,
        ("ap-42", "bref", "best available techniques", "technical manual"),
    ),
    (
        DocumentType.NEWS,
        ("news", "press release", "announces", "commissioned", "inaugurated"),
    ),
)

TECHNICAL_TERMS = (
    "clinker",
    "kiln",
    "preheater",
    "precalciner",
    "raw mill",
    "cement mill",
    "cooler",
    "crusher",
    "silo",
    "packer",
    "weighbridge",
    "mtpa",
    "tpd",
    "material balance",
    "power requirement",
    "fuel consumption",
)


class CementSearchError(RuntimeError):
    pass


class QuerySearchClient(Protocol):
    @property
    def provider_name(self) -> str:
        """Stable provider identifier exposed by the search implementation."""
        ...

    def search(self, query: str, *, limit: int, location: str | None) -> list[RawSearchResult]:
        ...


@dataclass(slots=True)
class ProviderSearchClient:
    """Run one exact cement query through configured existing search integrations."""

    settings: Settings
    provider: str | None = None

    @property
    def provider_name(self) -> str:
        return (self.provider or self.settings.search_provider or "services").casefold()

    def _firecrawl(
        self, query: str, *, limit: int, location: str | None
    ) -> list[RawSearchResult]:
        if not self.settings.firecrawl_api_key:
            raise CementSearchError("FIRECRAWL_API_KEY is not configured")
        client = FirecrawlClient(
            self.settings.firecrawl_api_key,
            timeout_seconds=self.settings.firecrawl_timeout_seconds,
            wait_ms=self.settings.firecrawl_wait_ms,
        )
        try:
            results = client.search(query, limit=limit, location=location)
        except FirecrawlError as exc:
            raise CementSearchError(str(exc)) from exc
        return [
            RawSearchResult(
                query=query,
                url=item.url,
                title=item.title,
                snippet=item.description,
                provider="firecrawl",
                rank=rank,
            )
            for rank, item in enumerate(results, start=1)
        ]

    def _jina(
        self, query: str, *, limit: int, location: str | None
    ) -> list[RawSearchResult]:
        del location
        client = JinaSearchClient(
            self.settings.jina_api_key,
            timeout_seconds=self.settings.jina_timeout_seconds,
        )
        try:
            results = client.search(query, limit=limit)
        except JinaError as exc:
            raise CementSearchError(str(exc)) from exc
        return [
            RawSearchResult(
                query=query,
                url=item.url,
                title=item.title,
                snippet=item.content,
                provider="jina",
                rank=rank,
            )
            for rank, item in enumerate(results, start=1)
        ]

    def _tavily(
        self, query: str, *, limit: int, location: str | None
    ) -> list[RawSearchResult]:
        if not self.settings.tavily_api_key:
            raise CementSearchError("TAVILY_API_KEY is not configured")
        contextual_query = f"{query} {location}" if location and location not in query else query
        request = Request(
            "https://api.tavily.com/search",
            data=json.dumps(
                {
                    "query": contextual_query,
                    "topic": "general",
                    "search_depth": "basic",
                    "max_results": limit,
                    "include_answer": False,
                    "include_raw_content": False,
                    "include_images": False,
                    "auto_parameters": False,
                }
            ).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.settings.tavily_api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Project-ID": "brixta-cement-intelligence",
            },
        )
        try:
            with urlopen(request, timeout=self.settings.request_timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = http_error_detail(exc, limit=1_000)
            raise CementSearchError(f"Tavily HTTP {exc.code}: {detail}") from exc
        except RETRYABLE_TRANSPORT_ERRORS + (json.JSONDecodeError,) as exc:
            raise CementSearchError(f"Tavily request failed: {exc}") from exc
        return [
            RawSearchResult(
                query=query,
                url=str(item.get("url") or ""),
                title=clean_text(str(item.get("title") or "")) or None,
                snippet=clean_text(str(item.get("content") or "")),
                provider="tavily",
                rank=rank,
            )
            for rank, item in enumerate(payload.get("results") or [], start=1)
            if str(item.get("url") or "").startswith(("http://", "https://"))
        ]

    def search(
        self, query: str, *, limit: int, location: str | None = None
    ) -> list[RawSearchResult]:
        provider = self.provider_name
        if provider == "firecrawl":
            return self._firecrawl(query, limit=limit, location=location)
        if provider == "jina":
            return self._jina(query, limit=limit, location=location)
        if provider == "tavily":
            return self._tavily(query, limit=limit, location=location)
        if provider in {"services", "fallback"}:
            errors: list[str] = []
            if self.settings.firecrawl_api_key:
                try:
                    results = self._firecrawl(query, limit=limit, location=location)
                except CementSearchError as exc:
                    errors.append(f"Firecrawl: {exc}")
                else:
                    if results:
                        return results
            try:
                results = self._jina(query, limit=limit, location=location)
            except CementSearchError as exc:
                errors.append(f"Jina: {exc}")
            else:
                if results:
                    return results
            if self.settings.tavily_api_key:
                try:
                    results = self._tavily(
                        query,
                        limit=limit,
                        location=location,
                    )
                except CementSearchError as exc:
                    errors.append(f"Tavily: {exc}")
                else:
                    if results:
                        return results
            raise CementSearchError("; ".join(errors) or "No service returned search results")
        raise CementSearchError(
            f"Unsupported cement discovery provider: {provider}. "
            "Use services, firecrawl, jina, or tavily."
        )


def classify_document(url: str, title: str | None, snippet: str) -> DocumentType:
    text = clean_text(f"{url} {title or ''} {snippet}").casefold()
    host = (urlsplit(url).hostname or "").casefold()
    if any(domain in host for domain in OEM_DOMAINS):
        return DocumentType.OEM_TECHNICAL
    for document_type, patterns in DOCUMENT_PATTERNS:
        if any(pattern in text for pattern in patterns):
            return document_type
    if any(term in text for term in TECHNICAL_TERMS):
        return DocumentType.OEM_TECHNICAL if "case study" in text else DocumentType.OTHER
    return DocumentType.OTHER


def _domain(url: str) -> str:
    return (urlsplit(url).hostname or "").casefold().removeprefix("www.")


def _is_government(domain: str) -> bool:
    return any(domain == item or domain.endswith(f".{item}") for item in GOVERNMENT_DOMAINS)


def _clean_entity(value: str | None) -> str | None:
    cleaned = clean_text(value)
    if not cleaned:
        return None
    cleaned = re.sub(r"\s*[|–—:-]\s*(?:EIA|EMP|EC|PFR|Report).*$", "", cleaned, flags=re.I)
    return cleaned.strip(" ,.-")[:120] or None


def extract_company_hint(
    title: str | None, snippet: str, known_company: str | None
) -> str | None:
    if (known_company or "").strip():
        return clean_text(known_company)
    text = clean_text(f"{title or ''} {snippet[:600]}")
    patterns = (
        r"(?:M/s\.?\s*)?([A-Z][A-Za-z0-9&'().,\- ]{2,90}?"
        r"(?:Cements?|Cement Corporation|Cement Company|Industries)"
        r"(?:\s+(?:Limited|Ltd\.?))?)",
        r"(?:project proponent|proponent|company)\s*[:\-]\s*([^.;\n]{3,100})",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return _clean_entity(match.group(1))
    return None


def extract_plant_hint(
    title: str | None, snippet: str, known_plant: str | None
) -> str | None:
    if (known_plant or "").strip():
        return clean_text(known_plant)
    text = clean_text(f"{title or ''} {snippet[:800]}")
    patterns = (
        r"([A-Z][A-Za-z0-9&'().,\- ]{2,80}\s+(?:Cement|Clinker)\s+(?:Plant|Unit))",
        r"(?:plant|unit)\s*(?:at|located at|near|:|-)\s*"
        r"([A-Z][A-Za-z0-9&'().,\- ]{2,80})",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return _clean_entity(match.group(1))
    return None


def _authority_score(domain: str, document_type: DocumentType) -> float:
    if _is_government(domain):
        return 1.0
    if any(domain == item or domain.endswith(f".{item}") for item in OEM_DOMAINS):
        return 0.88
    if document_type in {DocumentType.PATENT, DocumentType.ACADEMIC}:
        return 0.78
    if document_type in {
        DocumentType.COMPANY_REPORT,
        DocumentType.SUSTAINABILITY_REPORT,
    }:
        return 0.76
    if document_type == DocumentType.NEWS:
        return 0.42
    return 0.55


def _technical_value(document_type: DocumentType, text: str) -> float:
    base = {
        DocumentType.EIA_EMP: 1.0,
        DocumentType.PRE_FEASIBILITY: 0.94,
        DocumentType.ENVIRONMENTAL_CLEARANCE: 0.78,
        DocumentType.OEM_TECHNICAL: 0.82,
        DocumentType.TENDER: 0.86,
        DocumentType.COMPLIANCE_REPORT: 0.68,
        DocumentType.EAC_MINUTES: 0.62,
        DocumentType.BEE_PAT: 0.70,
        DocumentType.ACADEMIC: 0.64,
        DocumentType.PATENT: 0.62,
        DocumentType.COMPANY_REPORT: 0.56,
        DocumentType.SUSTAINABILITY_REPORT: 0.58,
        DocumentType.TERMS_OF_REFERENCE: 0.48,
        DocumentType.PUBLIC_HEARING: 0.42,
        DocumentType.TECHNICAL_REFERENCE: 0.66,
        DocumentType.NEWS: 0.25,
        DocumentType.OTHER: 0.20,
    }[document_type]
    hits = sum(term in text for term in TECHNICAL_TERMS)
    return min(1.0, base + min(0.18, hits * 0.025))


def _specificity_score(
    text: str, request: CementDiscoveryRequest, company: str | None, plant: str | None
) -> float:
    score = 0.15
    if company:
        score += 0.25
    if plant:
        score += 0.30
    for value, weight in (
        (request.company_name, 0.15),
        (request.plant_name, 0.20),
        (request.state, 0.08),
    ):
        if value and value.casefold() in text:
            score += weight
    if re.search(r"\b\d+(?:\.\d+)?\s*(?:mtpa|tpd|million tonnes?)\b", text):
        score += 0.12
    return min(1.0, score)


def classify_evidence_scope(
    *,
    document_type: DocumentType,
    company_hint: str | None,
    plant_hint: str | None,
    request: CementDiscoveryRequest,
    text: str,
) -> EvidenceScope:
    """Separate plant evidence from sector and equipment reference material."""

    company_matches = bool(
        request.company_name
        and request.company_name.casefold() in text
    )
    plant_matches = bool(
        request.plant_name
        and request.plant_name.casefold() in text
    )
    location_matches = bool(
        request.state
        and request.state.casefold() in text
    )
    has_capacity = bool(
        re.search(r"\b\d+(?:\.\d+)?\s*(?:mtpa|tpd|million tonnes?)\b", text)
    )

    if request.mode == ResearchMode.KNOWN_PLANT:
        if request.plant_name and plant_matches:
            return EvidenceScope.EXACT_PLANT
        if request.company_name and company_matches:
            if plant_hint or location_matches or has_capacity:
                return EvidenceScope.EXACT_PLANT
            return EvidenceScope.EXACT_COMPANY

    if company_hint and plant_hint and (location_matches or has_capacity):
        return EvidenceScope.EXACT_PLANT

    if document_type == DocumentType.OEM_TECHNICAL:
        return EvidenceScope.EQUIPMENT_REFERENCE
    if document_type in {
        DocumentType.BEE_PAT,
        DocumentType.TECHNICAL_REFERENCE,
        DocumentType.ACADEMIC,
        DocumentType.PATENT,
    }:
        return EvidenceScope.CEMENT_SECTOR_REFERENCE
    if document_type in {
        DocumentType.TERMS_OF_REFERENCE,
        DocumentType.EAC_MINUTES,
        DocumentType.PUBLIC_HEARING,
    } and not company_hint:
        return EvidenceScope.REGULATORY_REFERENCE
    if document_type == DocumentType.NEWS and not company_hint:
        return EvidenceScope.IRRELEVANT
    return EvidenceScope.UNRESOLVED


def make_document_candidate(
    raw: RawSearchResult, request: CementDiscoveryRequest
) -> DocumentCandidate | None:
    try:
        url = canonicalize_url(raw.url)
    except ValueError:
        return None
    if not url.startswith(("http://", "https://")):
        return None
    title = clean_text(raw.title) or None
    snippet = clean_text(raw.snippet)
    text = clean_text(f"{url} {title or ''} {snippet}").casefold()
    document_type = classify_document(url, title, snippet)
    company = extract_company_hint(title, snippet, request.company_name)
    plant = extract_plant_hint(title, snippet, request.plant_name)
    evidence_scope = classify_evidence_scope(
        document_type=document_type,
        company_hint=company,
        plant_hint=plant,
        request=request,
        text=text,
    )
    domain = _domain(url)
    authority = _authority_score(domain, document_type)
    specificity = _specificity_score(text, request, company, plant)
    technical = _technical_value(document_type, text)
    crawlability = 0.90
    if any(token in text for token in ("login", "sign in", "subscribe", "paywall")):
        crawlability -= 0.45
    is_pdf = ".pdf" in url.casefold() or "filetype:pdf" in raw.query.casefold()
    if is_pdf:
        crawlability = min(1.0, crawlability + 0.08)
    overall = (
        0.30 * authority
        + 0.25 * technical
        + 0.25 * specificity
        + 0.10 * crawlability
        + 0.10 * min(1.0, len(DOCUMENT_COVERAGE[document_type]) / 8)
    )
    reasons = [
        f"Classified as {document_type.value.replace('_', ' ')}",
        f"Publisher authority {authority:.2f}",
        f"Plant specificity {specificity:.2f}",
    ]
    if document_type in ANCHOR_TYPES:
        reasons.append("Potential anchor document")
    if is_pdf:
        reasons.append("Likely downloadable PDF")
    return DocumentCandidate(
        candidate_id=stable_id("doc", url),
        url=url,
        title=title,
        snippet=snippet[:2_000],
        provider=raw.provider,
        query=raw.query,
        document_type=document_type,
        evidence_scope=evidence_scope,
        company_hint=company,
        plant_hint=plant,
        location_hint=clean_text(request.state) or None,
        publisher_domain=domain,
        is_government=_is_government(domain),
        is_probably_pdf=is_pdf,
        authority_score=round(authority, 4),
        plant_specificity_score=round(specificity, 4),
        technical_value_score=round(technical, 4),
        crawlability_score=round(max(0.0, crawlability), 4),
        overall_score=round(max(0.0, min(1.0, overall)), 4),
        coverage_categories=DOCUMENT_COVERAGE[document_type],
        reasons=tuple(reasons),
    )


def _plant_key(document: DocumentCandidate, request: CementDiscoveryRequest) -> str:
    company = document.company_hint or request.company_name or "unresolved company"
    if (
        request.mode == ResearchMode.KNOWN_PLANT
        and request.company_name
        and not request.plant_name
    ):
        return clean_text(f"{request.company_name}|company plant").casefold()
    plant = document.plant_hint or request.plant_name
    if not plant and request.mode != ResearchMode.KNOWN_PLANT:
        plant = document.location_hint or "plant candidate"
    return clean_text(f"{company}|{plant or 'company plant'}").casefold()


def _select_bundle(
    plant_id: str, documents: list[DocumentCandidate], max_documents: int
) -> EvidenceBundle:
    remaining = list(documents)
    selected: list[DocumentCandidate] = []
    covered: set[str] = set()
    used_types: set[DocumentType] = set()

    while remaining and len(selected) < max_documents:
        def utility(item: DocumentCandidate) -> tuple[float, float]:
            new_coverage = set(item.coverage_categories) - covered
            diversity = 0.12 if item.document_type not in used_types else 0.0
            anchor = 0.18 if item.document_type in ANCHOR_TYPES and not selected else 0.0
            value = len(new_coverage) / max(1, len(CORE_COVERAGE))
            return value + diversity + anchor + 0.30 * item.overall_score, item.overall_score

        best = max(remaining, key=utility)
        new_categories = set(best.coverage_categories) - covered
        if selected and not new_categories and best.document_type in used_types:
            break
        selected.append(best)
        remaining.remove(best)
        covered.update(best.coverage_categories)
        used_types.add(best.document_type)

    missing = tuple(category for category in CORE_COVERAGE if category not in covered)
    coverage_ratio = len(covered) / len(CORE_COVERAGE)
    mean_quality = (
        sum(item.overall_score for item in selected) / len(selected) if selected else 0.0
    )
    score = min(1.0, 0.68 * coverage_ratio + 0.32 * mean_quality)
    rationale = [
        f"Covers {len(covered)} of {len(CORE_COVERAGE)} core evidence categories",
        f"Uses {len(used_types)} complementary document types",
    ]
    if any(item.document_type in ANCHOR_TYPES for item in selected):
        rationale.append("Includes at least one potential anchor document")
    return EvidenceBundle(
        bundle_id=stable_id("bundle", plant_id, *(item.candidate_id for item in selected)),
        plant_candidate_id=plant_id,
        document_ids=[item.candidate_id for item in selected],
        expected_coverage=tuple(category for category in CORE_COVERAGE if category in covered),
        missing_categories=missing,
        bundle_score=round(score, 4),
        estimated_documents=len(selected),
        rationale=tuple(rationale),
    )


def build_plant_candidates(
    documents: list[DocumentCandidate], request: CementDiscoveryRequest
) -> list[PlantCandidate]:
    groups: dict[str, list[DocumentCandidate]] = defaultdict(list)
    for document in documents:
        admitted = document.evidence_scope == EvidenceScope.EXACT_PLANT
        if (
            request.mode == ResearchMode.KNOWN_PLANT
            and document.evidence_scope == EvidenceScope.EXACT_COMPANY
        ):
            admitted = True
        if not admitted:
            continue
        groups[_plant_key(document, request)].append(document)

    plants: list[PlantCandidate] = []
    for key, group in groups.items():
        group.sort(key=lambda item: item.overall_score, reverse=True)
        company = next(
            (item.company_hint for item in group if item.company_hint),
            request.company_name or "Unresolved company",
        )
        plant = next(
            (item.plant_hint for item in group if item.plant_hint),
            request.plant_name or f"{company} plant candidate",
        )
        location = next(
            (item.location_hint for item in group if item.location_hint),
            request.state or request.country,
        )
        plant_id = stable_id("plant", key, location)
        bundle = _select_bundle(plant_id, group, request.max_bundle_documents)
        anchor = next(
            (item for item in group if item.document_type in ANCHOR_TYPES),
            None,
        )
        authority = sum(item.authority_score for item in group[:6]) / min(len(group), 6)
        specificity = sum(item.plant_specificity_score for item in group[:6]) / min(
            len(group), 6
        )
        diversity = min(1.0, len({item.document_type for item in group}) / 6)
        corroboration = min(1.0, len({item.publisher_domain for item in group}) / 5)
        researchability = (
            0.34 * bundle.bundle_score
            + 0.18 * authority
            + 0.18 * specificity
            + 0.12 * diversity
            + 0.10 * corroboration
            + 0.08 * (1.0 if anchor else 0.0)
        )
        reasons = list(bundle.rationale)
        reasons.append(f"{len(group)} unique candidate documents")
        reasons.append(f"{len({item.publisher_domain for item in group})} publisher domains")
        plants.append(
            PlantCandidate(
                plant_candidate_id=plant_id,
                company_name=company,
                plant_name=plant,
                location=location,
                plant_type=request.plant_type,
                document_ids=[item.candidate_id for item in group],
                anchor_document_id=anchor.candidate_id if anchor else None,
                researchability_score=round(min(1.0, researchability), 4),
                expected_coverage=bundle.expected_coverage,
                missing_categories=bundle.missing_categories,
                reasons=tuple(reasons),
                bundle=bundle,
            )
        )

    plants.sort(key=lambda item: item.researchability_score, reverse=True)
    plants = plants[: request.max_candidate_plants]
    if plants:
        plants[0].recommended = True
    return plants


@dataclass(slots=True)
class CementDiscoveryEngine:
    search_client: QuerySearchClient

    def discover(
        self,
        request: CementDiscoveryRequest,
        *,
        progress_callback: Callable[..., None] | None = None,
    ) -> CementDiscoveryResult:
        queries = build_discovery_queries(request)
        raw_results: list[RawSearchResult] = []
        warnings: list[str] = []
        location = " ".join(
            part for part in (request.state or "", request.country) if part
        ).strip()

        for query_number, query in enumerate(queries, start=1):
            if progress_callback is not None:
                progress_callback(
                    "searching",
                    current=query_number,
                    total=len(queries),
                    message=f"Searching cement evidence: {query}",
                )
            try:
                raw_results.extend(
                    self.search_client.search(
                        query,
                        limit=request.results_per_query,
                        location=location,
                    )
                )
            except CementSearchError as exc:
                warnings.append(f"{query}: {exc}")

        if progress_callback is not None:
            progress_callback(
                "classifying",
                current=0,
                total=len(raw_results),
                message="Classifying discovered cement documents",
            )
        by_url: dict[str, DocumentCandidate] = {}
        for result_number, raw in enumerate(raw_results, start=1):
            if progress_callback is not None and (
                result_number == 1 or result_number % 10 == 0
            ):
                progress_callback(
                    "classifying",
                    current=result_number,
                    total=len(raw_results),
                    message="Classifying discovered cement documents",
                )
            candidate = make_document_candidate(raw, request)
            if candidate is None:
                continue
            previous = by_url.get(candidate.url)
            if previous is None or candidate.overall_score > previous.overall_score:
                by_url[candidate.url] = candidate

        documents = sorted(
            by_url.values(), key=lambda item: item.overall_score, reverse=True
        )
        if progress_callback is not None:
            progress_callback(
                "grouping_plants",
                message="Grouping exact plant evidence and separating reference material",
            )
        plants = build_plant_candidates(documents, request)
        if progress_callback is not None:
            progress_callback(
                "building_bundles",
                message="Building complementary evidence bundles",
            )
        run_id = stable_id(
            "cement_discovery",
            request.mode.value,
            request.country,
            request.state or "",
            request.company_name or "",
            request.plant_name or "",
            utc_now_iso(),
        )
        if not documents:
            warnings.append(
                "No candidate evidence was discovered. Check provider credentials or broaden "
                "the geography and plant type."
            )
        elif not any(item.anchor_document_id for item in plants):
            warnings.append(
                "No strong EIA, pre-feasibility, or environmental-clearance anchor was detected. "
                "Treat the proposed bundles as preliminary."
            )
        reference_count = sum(
            item.evidence_scope
            in {
                EvidenceScope.CEMENT_SECTOR_REFERENCE,
                EvidenceScope.EQUIPMENT_REFERENCE,
                EvidenceScope.REGULATORY_REFERENCE,
            }
            for item in documents
        )
        if documents and not plants:
            warnings.append(
                "Documents were discovered, but none passed the exact-plant admission gate. "
                "Add a company, plant, or state to narrow the investigation."
            )
        if reference_count:
            warnings.append(
                f"{reference_count} generic regulatory, sector, or equipment references were "
                "kept outside plant-specific bundles."
            )
        return CementDiscoveryResult(
            run_id=run_id,
            request=request,
            queries=queries,
            documents=documents,
            plants=plants,
            warnings=warnings,
        )
