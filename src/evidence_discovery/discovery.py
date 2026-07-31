"""Guided, bounded and explainable open-web evidence discovery."""

from __future__ import annotations

import heapq
import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

import lxml.etree as etree
import lxml.html as html

from mattress_intelligence.crawler import (
    FetchError,
    HttpFetcher,
    HybridBrowserFetcher,
)
from mattress_intelligence.normalization import canonicalize_url, clean_text
from mattress_intelligence.settings import Settings

from .models import (
    CampaignCandidate,
    CampaignDiscoveryResult,
    CampaignRequest,
    CandidateDecision,
    DiscoveryDirection,
    DomainPolicy,
    RouteEvent,
    SearchResult,
    stable_id,
    utc_now_iso,
)
from .providers import CampaignSearchClient, CampaignSearchError


STOPWORDS = {
    "about",
    "after",
    "and",
    "are",
    "for",
    "from",
    "how",
    "into",
    "of",
    "on",
    "the",
    "their",
    "this",
    "to",
    "what",
    "with",
}

AUTHORITY_SUFFIXES = (
    ".gov",
    ".gov.in",
    ".nic.in",
    ".edu",
    ".edu.in",
    ".ac.in",
)

REJECT_PATH_HINTS = (
    "/login",
    "/account",
    "/cart",
    "/checkout",
    "/privacy",
    "/terms",
    "/cookie",
    "/contact",
    "/careers",
    "/jobs",
    "/tag/",
)

DIRECTION_KEYWORDS: dict[DiscoveryDirection, tuple[str, ...]] = {
    DiscoveryDirection.OFFICIAL: (
        "official",
        "annual report",
        "technical report",
        "company",
    ),
    DiscoveryDirection.GOVERNMENT: (
        "government",
        "regulatory",
        "clearance",
        "compliance",
        "permit",
        "ministry",
    ),
    DiscoveryDirection.ACADEMIC: (
        "research",
        "journal",
        "paper",
        "thesis",
        "university",
        "study",
    ),
    DiscoveryDirection.PATENTS: (
        "patent",
        "inventor",
        "claims",
        "espacenet",
        "patentscope",
    ),
    DiscoveryDirection.TECHNICAL: (
        "technical",
        "manual",
        "datasheet",
        "specification",
        "process",
        "equipment",
        "case study",
    ),
    DiscoveryDirection.NEWS_ARCHIVES: (
        "news",
        "archive",
        "press release",
        "announcement",
        "history",
    ),
    DiscoveryDirection.OPEN_WEB: (),
}


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9+.-]{1,}", value.casefold())
        if token not in STOPWORDS
    }


def _domain(value: str) -> str:
    if "://" not in value:
        value = f"https://{value}"
    return (urlsplit(value).hostname or "").casefold().removeprefix("www.")


def _base_url(value: str) -> str:
    if "://" not in value:
        value = f"https://{value}"
    split = urlsplit(value)
    if not split.hostname:
        raise ValueError(f"Invalid domain or URL: {value}")
    return f"{split.scheme}://{split.netloc}/"


def _same_domain(candidate: str, expected: str) -> bool:
    candidate_domain = _domain(candidate)
    expected_domain = _domain(expected)
    return (
        candidate_domain == expected_domain
        or candidate_domain.endswith(f".{expected_domain}")
    )


def _is_authority(domain: str) -> bool:
    return domain.endswith(AUTHORITY_SUFFIXES)


def _direction_for(
    query: str,
    text: str,
    directions: tuple[DiscoveryDirection, ...],
) -> DiscoveryDirection:
    lowered = f"{query} {text}".casefold()
    scored = [
        (
            sum(keyword in lowered for keyword in DIRECTION_KEYWORDS[direction]),
            direction,
        )
        for direction in directions
        if direction != DiscoveryDirection.OPEN_WEB
    ]
    if scored:
        score, direction = max(scored, key=lambda item: item[0])
        if score:
            return direction
    return DiscoveryDirection.OPEN_WEB


def build_campaign_queries(request: CampaignRequest) -> list[str]:
    topic = clean_text(request.topic)
    geography = clean_text(request.geography)
    base = " ".join(part for part in (topic, geography) if part).strip()
    queries: list[str] = [clean_text(item) for item in request.manual_queries]
    seed_domains = list(request.seed_domains)
    seed_domains.extend(
        _domain(url)
        for url in request.seed_urls
        if _domain(url) not in seed_domains
    )
    for domain in seed_domains:
        queries.append(f"site:{_domain(domain)} {base}".strip())
    templates: dict[DiscoveryDirection, str] = {
        DiscoveryDirection.OFFICIAL: f"{base} official report filetype:pdf",
        DiscoveryDirection.GOVERNMENT: (
            f"{base} government regulatory compliance report filetype:pdf"
        ),
        DiscoveryDirection.ACADEMIC: (
            f"{base} research paper thesis technical study filetype:pdf"
        ),
        DiscoveryDirection.PATENTS: f"{base} patent process technology",
        DiscoveryDirection.TECHNICAL: (
            f"{base} technical manual specification case study filetype:pdf"
        ),
        DiscoveryDirection.NEWS_ARCHIVES: (
            f"{base} news archive press release"
        ),
        DiscoveryDirection.OPEN_WEB: base,
    }
    for direction in request.directions:
        if templates[direction].strip():
            queries.append(templates[direction])
    if base:
        queries.append(base)
    return list(dict.fromkeys(query for query in queries if query))[
        : request.max_queries
    ]


def _candidate_from_result(
    raw: SearchResult,
    request: CampaignRequest,
    *,
    source_kind: str = "search_result",
    parent_url: str | None = None,
    depth: int | None = None,
    force_accept: bool = False,
) -> CampaignCandidate | None:
    try:
        url = canonicalize_url(raw.url)
    except ValueError:
        return None
    if not url.startswith(("http://", "https://")):
        return None
    title = clean_text(raw.title) or None
    snippet = clean_text(raw.snippet)
    domain = _domain(url)
    text = clean_text(f"{url} {title or ''} {snippet}")
    topic_tokens = _tokens(request.topic)
    evidence_tokens = _tokens(text)
    overlap = (
        len(topic_tokens & evidence_tokens) / len(topic_tokens)
        if topic_tokens
        else 0.35
    )
    direction = _direction_for(raw.query, text, request.directions)
    direction_hits = sum(
        keyword in text.casefold()
        for keyword in DIRECTION_KEYWORDS.get(direction, ())
    )
    authority = _is_authority(domain)
    supplied_domain = any(
        _same_domain(url, item)
        for item in (*request.seed_domains, *request.seed_urls)
    )
    is_pdf = (
        urlsplit(url).path.casefold().endswith(".pdf")
        or "filetype:pdf" in raw.query.casefold()
        or "application/pdf" in snippet.casefold()
    )
    score = 0.12 + 0.50 * overlap
    if supplied_domain:
        score += 0.20
    if authority:
        score += 0.10
    if direction != DiscoveryDirection.OPEN_WEB:
        score += min(0.12, direction_hits * 0.04)
    if is_pdf:
        score += 0.08
    score -= min(0.18, max(0, raw.rank - 1) * 0.015)
    path = urlsplit(url).path.casefold()
    rejected_path = next(
        (item for item in REJECT_PATH_HINTS if item in path),
        None,
    )
    if rejected_path:
        score -= 0.45

    domain_allowed = True
    if request.domain_policy == DomainPolicy.SEED_DOMAINS_ONLY:
        domain_allowed = supplied_domain
    elif request.domain_policy == DomainPolicy.SEED_PLUS_AUTHORITY:
        domain_allowed = supplied_domain or authority

    reasons: list[str] = []
    if force_accept:
        reasons.append("User-supplied starting point")
    if supplied_domain:
        reasons.append("Matches an approved seed domain")
    if topic_tokens:
        reasons.append(
            f"Matches {len(topic_tokens & evidence_tokens)} of "
            f"{len(topic_tokens)} topic terms"
        )
    if direction != DiscoveryDirection.OPEN_WEB:
        reasons.append(
            f"Matches approved direction: {direction.value.replace('_', ' ')}"
        )
    if authority:
        reasons.append("Government or academic authority domain")
    if is_pdf:
        reasons.append("Likely downloadable PDF")
    if rejected_path:
        reasons.append(f"Rejected path signal: {rejected_path}")
    if not domain_allowed:
        reasons.append("Outside the approved domain policy")

    accepted = force_accept or (
        domain_allowed and score >= request.minimum_relevance and not rejected_path
    )
    if accepted:
        reasons.append(
            f"Accepted at relevance threshold {request.minimum_relevance:.2f}"
        )
    else:
        reasons.append(
            f"Rejected at relevance threshold {request.minimum_relevance:.2f}"
        )
    return CampaignCandidate(
        candidate_id=stable_id("candidate", url),
        url=url,
        title=title,
        snippet=snippet[:2_000],
        publisher_domain=domain,
        provider=raw.provider,
        query=raw.query,
        direction=direction.value,
        source_kind=source_kind,
        decision=(
            CandidateDecision.ACCEPTED
            if accepted
            else CandidateDecision.REJECTED
        ),
        relevance_score=round(max(0.0, min(1.0, score)), 4),
        is_probably_pdf=is_pdf,
        is_authority_source=authority,
        reasons=tuple(reasons),
        parent_url=parent_url,
        depth=depth,
    )


def _page_title(body: bytes, url: str) -> str | None:
    try:
        tree = html.fromstring(body, base_url=url)
        titles = [
            clean_text(str(item))
            for item in tree.xpath("//title/text() | //h1[1]//text()")
        ]
    except (etree.ParserError, ValueError):
        return None
    return next((item for item in titles if item), None)


def _links(body: bytes, url: str) -> list[str]:
    try:
        tree = html.fromstring(body, base_url=url)
    except (etree.ParserError, ValueError):
        return []
    links: list[str] = []
    for raw in tree.xpath("//a[@href]/@href | //link[@href]/@href"):
        if not isinstance(raw, str) or raw.startswith(
            ("mailto:", "tel:", "javascript:", "#")
        ):
            continue
        try:
            candidate = canonicalize_url(raw, url)
        except ValueError:
            continue
        if candidate.startswith(("http://", "https://")):
            links.append(candidate)
    return list(dict.fromkeys(links))


def _url_priority(url: str, request: CampaignRequest) -> float:
    path = urlsplit(url).path.casefold()
    score = 0.0
    for token in _tokens(request.topic):
        if token in url.casefold():
            score += 4.0
    if path.endswith(".pdf"):
        score += 10.0
    if any(
        keyword in path
        for keyword in (
            "report",
            "document",
            "publication",
            "research",
            "technical",
            "download",
            "resource",
            "archive",
        )
    ):
        score += 5.0
    if any(item in path for item in REJECT_PATH_HINTS):
        score -= 100.0
    return score


@dataclass(slots=True)
class CampaignDiscoveryEngine:
    settings: Settings
    search_client: CampaignSearchClient

    def discover(
        self,
        request: CampaignRequest,
        *,
        progress_callback: Callable[..., None] | None = None,
    ) -> CampaignDiscoveryResult:
        queries = build_campaign_queries(request)
        candidates_by_url: dict[str, CampaignCandidate] = {}
        events: list[RouteEvent] = []
        warnings: list[str] = []
        sequence = 0

        def record(
            stage: str,
            action: str,
            reason: str,
            *,
            query: str | None = None,
            candidate: CampaignCandidate | None = None,
            url: str | None = None,
            parent_url: str | None = None,
            domain: str | None = None,
            depth: int | None = None,
        ) -> None:
            nonlocal sequence
            sequence += 1
            events.append(
                RouteEvent(
                    sequence=sequence,
                    stage=stage,
                    action=action,
                    reason=reason,
                    query=query,
                    url=candidate.url if candidate else url,
                    parent_url=(
                        candidate.parent_url if candidate else parent_url
                    ),
                    domain=(
                        candidate.publisher_domain if candidate else domain
                    ),
                    direction=candidate.direction if candidate else None,
                    score=candidate.relevance_score if candidate else None,
                    depth=candidate.depth if candidate else depth,
                )
            )

        def admit(candidate: CampaignCandidate) -> None:
            previous = candidates_by_url.get(candidate.url)
            if previous is None or (
                candidate.decision == CandidateDecision.ACCEPTED
                and previous.decision == CandidateDecision.REJECTED
            ) or candidate.relevance_score > previous.relevance_score:
                candidates_by_url[candidate.url] = candidate
            record(
                "classification",
                candidate.decision.value,
                "; ".join(candidate.reasons),
                query=candidate.query,
                candidate=candidate,
            )

        for raw_url in request.seed_urls:
            candidate = _candidate_from_result(
                SearchResult(
                    query="manual URL seed",
                    url=raw_url,
                    title=None,
                    snippet="User-supplied starting URL",
                    provider="manual",
                    rank=1,
                ),
                request,
                source_kind="manual_seed",
                force_accept=True,
            )
            if candidate is not None:
                admit(candidate)

        for query_number, query in enumerate(queries, start=1):
            if progress_callback is not None:
                progress_callback(
                    "searching",
                    current=query_number,
                    total=max(1, len(queries)),
                    message=f"Searching approved direction: {query}",
                )
            record(
                "search",
                "query",
                "Generated from the campaign brief and approved directions",
                query=query,
            )
            try:
                results = self.search_client.search(
                    query,
                    limit=request.results_per_query,
                    location=request.geography,
                )
            except CampaignSearchError as exc:
                warnings.append(f"{query}: {exc}")
                record("search", "failed", str(exc), query=query)
                continue
            for raw in results:
                candidate = _candidate_from_result(raw, request)
                if candidate is not None:
                    admit(candidate)

        crawl_seeds = list(request.seed_urls)
        crawl_seeds.extend(
            _base_url(domain)
            for domain in request.seed_domains
            if not any(_same_domain(url, domain) for url in crawl_seeds)
        )
        approved_sites = (*request.seed_domains, *request.seed_urls)
        if crawl_seeds and request.max_site_pages:
            if progress_callback is not None:
                progress_callback(
                    "crawling_domains",
                    current=0,
                    total=request.max_site_pages,
                    message="Exploring approved seed domains",
                )
            fetcher_class = (
                HybridBrowserFetcher
                if self.settings.render_javascript
                else HttpFetcher
            )
            fetcher = fetcher_class(
                self.settings,
                respect_robots_txt=True,
            )
            try:
                fetched_count = 0
                pending: list[tuple[float, int, int, str, str | None]] = []
                queued: set[str] = set()
                queue_sequence = 0
                for seed in crawl_seeds:
                    base = canonicalize_url(seed)
                    queue_sequence += 1
                    heapq.heappush(
                        pending,
                        (-100.0, queue_sequence, 0, base, None),
                    )
                    queued.add(base)
                    record(
                        "site_crawl",
                        "queued",
                        "Approved seed domain",
                        url=base,
                        domain=_domain(base),
                        depth=0,
                    )
                while pending and fetched_count < request.max_site_pages:
                    _, _, depth, url, parent_url = heapq.heappop(pending)
                    if depth > request.max_depth:
                        record(
                            "site_crawl",
                            "rejected",
                            f"Maximum depth {request.max_depth} exceeded",
                            url=url,
                            parent_url=parent_url,
                            domain=_domain(url),
                            depth=depth,
                        )
                        continue
                    try:
                        document = fetcher.fetch(url)
                    except FetchError as exc:
                        record(
                            "site_crawl",
                            "failed",
                            str(exc),
                            url=url,
                            parent_url=parent_url,
                            domain=_domain(url),
                            depth=depth,
                        )
                        continue
                    fetched_count += 1
                    if progress_callback is not None:
                        progress_callback(
                            "crawling_domains",
                            current=fetched_count,
                            total=request.max_site_pages,
                            message=f"Exploring approved domain: {document.url}",
                        )
                    text = document.extracted_text(max_characters=8_000)
                    candidate = _candidate_from_result(
                        SearchResult(
                            query="approved domain crawl",
                            url=document.url,
                            title=_page_title(document.body, document.url),
                            snippet=text,
                            provider="domain_crawl",
                            rank=fetched_count,
                        ),
                        request,
                        source_kind="domain_crawl",
                        parent_url=parent_url,
                        depth=depth,
                        force_accept=depth == 0,
                    )
                    if candidate is not None:
                        admit(candidate)
                    if depth >= request.max_depth or not document.is_html:
                        continue
                    for link in _links(document.body, document.url):
                        if not any(
                            _same_domain(link, seed)
                            for seed in approved_sites
                        ):
                            record(
                                "site_crawl",
                                "rejected",
                                "External link is outside approved seed domains",
                                url=link,
                                parent_url=document.url,
                                domain=_domain(link),
                                depth=depth + 1,
                            )
                            continue
                        if link in queued:
                            continue
                        priority = _url_priority(link, request)
                        if priority < 0:
                            record(
                                "site_crawl",
                                "rejected",
                                "URL matched a low-value navigation path",
                                url=link,
                                parent_url=document.url,
                                domain=_domain(link),
                                depth=depth + 1,
                            )
                            continue
                        queued.add(link)
                        queue_sequence += 1
                        heapq.heappush(
                            pending,
                            (
                                -priority,
                                queue_sequence,
                                depth + 1,
                                link,
                                document.url,
                            ),
                        )
                        record(
                            "site_crawl",
                            "queued",
                            "Same-domain link discovered on an approved page",
                            url=link,
                            parent_url=document.url,
                            domain=_domain(link),
                            depth=depth + 1,
                        )
            finally:
                fetcher.close()

        candidates = sorted(
            candidates_by_url.values(),
            key=lambda item: (
                item.decision == CandidateDecision.ACCEPTED,
                item.relevance_score,
            ),
            reverse=True,
        )
        if len(candidates) > request.max_candidates:
            warnings.append(
                f"{len(candidates) - request.max_candidates} lower-ranked "
                "candidates were omitted by the campaign candidate limit."
            )
            candidates = candidates[: request.max_candidates]
        if not candidates:
            warnings.append(
                "No candidates were discovered. Check service credentials or "
                "supply a starting URL/domain."
            )
        campaign_id = stable_id(
            "campaign",
            request.campaign_name,
            request.topic,
            utc_now_iso(),
        )
        return CampaignDiscoveryResult(
            campaign_id=campaign_id,
            request=request,
            queries=queries,
            candidates=candidates,
            route_events=events,
            warnings=warnings,
        )
