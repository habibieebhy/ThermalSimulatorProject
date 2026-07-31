"""Search-provider adapters shared by open research campaigns."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from mattress_intelligence.firecrawl import FirecrawlClient, FirecrawlError
from mattress_intelligence.jina import JinaError, JinaSearchClient
from mattress_intelligence.network import (
    RETRYABLE_TRANSPORT_ERRORS,
    http_error_detail,
)
from mattress_intelligence.normalization import clean_text
from mattress_intelligence.settings import Settings

from .models import SearchResult


class CampaignSearchError(RuntimeError):
    """A configured discovery service could not complete a query."""


class CampaignSearchClient(Protocol):
    @property
    def provider_name(self) -> str: ...

    def search(
        self,
        query: str,
        *,
        limit: int,
        location: str | None = None,
    ) -> list[SearchResult]: ...


@dataclass(slots=True)
class ProviderSearchClient:
    settings: Settings
    provider: str | None = None

    @property
    def provider_name(self) -> str:
        return (
            self.provider
            or self.settings.search_provider
            or "services"
        ).strip().casefold()

    def _firecrawl(
        self,
        query: str,
        *,
        limit: int,
        location: str | None,
    ) -> list[SearchResult]:
        if not self.settings.firecrawl_api_key:
            raise CampaignSearchError("FIRECRAWL_API_KEY is not configured")
        client = FirecrawlClient(
            self.settings.firecrawl_api_key,
            timeout_seconds=self.settings.firecrawl_timeout_seconds,
            wait_ms=self.settings.firecrawl_wait_ms,
        )
        try:
            results = client.search(query, limit=limit, location=location)
        except FirecrawlError as exc:
            raise CampaignSearchError(str(exc)) from exc
        return [
            SearchResult(
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
        self,
        query: str,
        *,
        limit: int,
        location: str | None,
    ) -> list[SearchResult]:
        del location
        client = JinaSearchClient(
            self.settings.jina_api_key,
            timeout_seconds=self.settings.jina_timeout_seconds,
        )
        try:
            results = client.search(query, limit=limit)
        except JinaError as exc:
            raise CampaignSearchError(str(exc)) from exc
        return [
            SearchResult(
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
        self,
        query: str,
        *,
        limit: int,
        location: str | None,
    ) -> list[SearchResult]:
        if not self.settings.tavily_api_key:
            raise CampaignSearchError("TAVILY_API_KEY is not configured")
        contextual_query = (
            f"{query} {location}"
            if location and location.casefold() not in query.casefold()
            else query
        )
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
                "X-Project-ID": "brixta-evidence-discovery",
            },
        )
        try:
            with urlopen(
                request,
                timeout=self.settings.request_timeout_seconds,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = http_error_detail(exc, limit=1_000)
            raise CampaignSearchError(
                f"Tavily HTTP {exc.code}: {detail}"
            ) from exc
        except RETRYABLE_TRANSPORT_ERRORS + (json.JSONDecodeError,) as exc:
            raise CampaignSearchError(f"Tavily request failed: {exc}") from exc
        return [
            SearchResult(
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
        self,
        query: str,
        *,
        limit: int,
        location: str | None = None,
    ) -> list[SearchResult]:
        provider = self.provider_name
        if provider == "firecrawl":
            return self._firecrawl(query, limit=limit, location=location)
        if provider == "jina":
            return self._jina(query, limit=limit, location=location)
        if provider == "tavily":
            return self._tavily(query, limit=limit, location=location)
        if provider not in {"services", "fallback"}:
            raise CampaignSearchError(
                f"Unsupported discovery provider: {provider}"
            )

        errors: list[str] = []
        if self.settings.firecrawl_api_key:
            try:
                results = self._firecrawl(
                    query,
                    limit=limit,
                    location=location,
                )
            except CampaignSearchError as exc:
                errors.append(f"Firecrawl: {exc}")
            else:
                if results:
                    return results
        if self.settings.jina_api_key is not None:
            try:
                results = self._jina(
                    query,
                    limit=limit,
                    location=location,
                )
            except CampaignSearchError as exc:
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
            except CampaignSearchError as exc:
                errors.append(f"Tavily: {exc}")
            else:
                if results:
                    return results
        raise CampaignSearchError(
            "; ".join(errors) or "No configured service returned search results"
        )
