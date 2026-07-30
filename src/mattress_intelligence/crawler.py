"""Polite, bounded catalogue discovery with service and local capture fallbacks."""

from __future__ import annotations

import gzip
import hashlib
import heapq
import io
import re
import shutil
import ssl
import subprocess
import tempfile
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

import lxml.etree as etree
import lxml.html as html
from pypdf import PdfReader

from .firecrawl import FirecrawlClient, FirecrawlError
from .jina import JinaError, JinaReaderClient
from .normalization import canonicalize_url, clean_text
from .object_store import ObjectStore, build_object_store
from .settings import Settings

if TYPE_CHECKING:
    from playwright.sync_api import Browser, Playwright


PRODUCT_HINTS = ("/products/", "/product/", "/mattress/", "/mattresses/")
REJECT_HINTS = (
    "/account", "/cart", "/checkout", "/login", "/privacy", "/terms", "/wishlist",
    "/search", "/collections/bedsheets", "/collections/recliners", "/collections/pillows",
    "/collections/chairs", "/collections/sofas",
)
NON_PRODUCT_PATH_RE = re.compile(
    r"/(?:collections?|pages?)/(?:mattress-in-[^/]+|mattress-for-[^/]+|"
    r"mattress-store-in-[^/]+|mattress-category-products|store-locator)(?:/|$)",
    re.IGNORECASE,
)
PRODUCT_DETAIL_PATH_RE = re.compile(r"/(?:products?|shop|mattress(?:es)?)/[^/?#]+", re.IGNORECASE)


def _ca_bundle_path() -> str | None:
    try:
        import certifi
    except ImportError:  # pragma: no cover - editable install supplies the dependency.
        return None
    return certifi.where()


def _verified_tls_contexts() -> tuple[tuple[str, ssl.SSLContext], ...]:
    """Build native-trust first, Certifi/OpenSSL second."""

    contexts: list[tuple[str, ssl.SSLContext]] = []
    try:
        import truststore
    except ImportError:  # pragma: no cover - editable install supplies it.
        pass
    else:
        contexts.append(
            ("native_trust", truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
        )
    contexts.append(
        ("certifi", ssl.create_default_context(cafile=_ca_bundle_path()))
    )
    return tuple(contexts)


class FetchError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FetchedDocument:
    url: str
    status: int
    content_type: str
    body: bytes
    retrieved_at_epoch: float
    artifact_path: str
    object_uri: str | None = None
    capture_method: str = "http"
    asset_manifest: tuple[dict[str, object], ...] = ()
    network_manifest: tuple[dict[str, object], ...] = ()

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()

    @property
    def text(self) -> str:
        charset = "utf-8"
        match = re.search(r"charset=([\w-]+)", self.content_type, re.IGNORECASE)
        if match:
            charset = match.group(1)
        return self.body.decode(charset, errors="replace")

    @property
    def is_html(self) -> bool:
        return "html" in self.content_type.casefold() or self.url.casefold().endswith((".htm", ".html"))

    @property
    def is_pdf(self) -> bool:
        return "pdf" in self.content_type.casefold() or self.url.casefold().endswith(".pdf")

    def extracted_text(self, max_characters: int = 200_000) -> str:
        if self.is_html:
            try:
                tree = html.fromstring(self.body, base_url=self.url)
                for node in tree.xpath("//script|//style|//noscript|//svg"):
                    parent = node.getparent()
                    if parent is not None:
                        parent.remove(node)
                return clean_text(" ".join(tree.xpath("//body//text()")))[:max_characters]
            except (etree.ParserError, ValueError):
                return ""
        if self.is_pdf:
            try:
                reader = PdfReader(io.BytesIO(self.body))
                chunks: list[str] = []
                length = 0
                for page_number, page in enumerate(reader.pages, start=1):
                    text = clean_text(page.extract_text() or "")
                    if not text:
                        continue
                    chunk = f"[PDF page {page_number}] {text}"
                    chunks.append(chunk)
                    length += len(chunk)
                    if length >= max_characters:
                        break
                return "\n".join(chunks)[:max_characters]
            except Exception as exc:
                raise FetchError(f"PDF text extraction failed: {exc}") from exc
        if self.content_type.casefold().startswith("text/"):
            return clean_text(self.text)[:max_characters]
        return ""


@dataclass(slots=True)
class CrawlReport:
    documents: list[FetchedDocument] = field(default_factory=list)
    discovered_urls: set[str] = field(default_factory=set)
    failed_urls: dict[str, str] = field(default_factory=dict)
    blocked_urls: set[str] = field(default_factory=set)
    sitemap_urls: set[str] = field(default_factory=set)
    crawl_log: list[dict[str, object]] = field(default_factory=list)

class CatalogueFetcher(Protocol):
    def robots_sitemaps(self, base_url: str) -> list[str]:
        ...

    def fetch(self, url: str) -> FetchedDocument:
        ...

    def close(self) -> None:
        ...


class HttpFetcher:
    """Standard-library fetcher with robots, delay, size limits, and object storage."""

    def __init__(self, settings: Settings, respect_robots_txt: bool = True) -> None:
        self.settings = settings
        self.respect_robots_txt = respect_robots_txt
        self.object_store: ObjectStore = build_object_store(settings)
        self._robots: dict[str, RobotFileParser] = {}
        self._last_request_at: dict[str, float] = {}
        self._ssl_contexts = _verified_tls_contexts()

    def _open_verified(self, request: Request) -> tuple[Any, str]:
        certificate_errors: list[str] = []
        for backend, context in self._ssl_contexts:
            try:
                response = urlopen(
                    request,
                    timeout=self.settings.request_timeout_seconds,
                    context=context,
                )
            except URLError as exc:
                if "CERTIFICATE_VERIFY_FAILED" not in str(exc):
                    raise
                certificate_errors.append(f"{backend}: {exc}")
                continue
            return response, backend
        raise URLError(
            "; ".join(certificate_errors)
            or "No verified Python TLS context was available"
        )

    def _origin(self, url: str) -> str:
        split = urlsplit(url)
        return f"{split.scheme}://{split.netloc}"

    def _load_robots(self, url: str) -> RobotFileParser:
        origin = self._origin(url)
        if origin in self._robots:
            return self._robots[origin]
        robots_url = f"{origin}/robots.txt"
        parser = RobotFileParser(robots_url)
        try:
            request = Request(
                robots_url,
                headers={"User-Agent": self.settings.user_agent, "Accept": "text/plain,*/*;q=0.1"},
            )
            response, _ = self._open_verified(request)
            with response:
                body = response.read(1_000_000).decode("utf-8", errors="replace")
            parser.parse(body.splitlines())
        except (HTTPError, URLError, TimeoutError, ValueError):
            parser.parse([])
        self._robots[origin] = parser
        return parser

    def allowed(self, url: str) -> bool:
        return True if not self.respect_robots_txt else self._load_robots(url).can_fetch(self.settings.user_agent, url)

    def _rate_limit(self, url: str) -> None:
        host = urlsplit(url).netloc
        elapsed = time.monotonic() - self._last_request_at.get(host, 0.0)
        remaining = self.settings.request_delay_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)
        self._last_request_at[host] = time.monotonic()

    def fetch_with_system_curl(self, url: str) -> FetchedDocument:
        """Retry verified HTTPS with the operating system's curl trust stack."""

        curl = shutil.which("curl")
        if curl is None:
            raise FetchError("System curl is unavailable")
        with tempfile.NamedTemporaryFile(prefix="brixta-fetch-", suffix=".bin") as handle:
            command = [
                curl,
                "--fail",
                "--location",
                "--silent",
                "--show-error",
                "--max-time",
                str(max(1, int(self.settings.request_timeout_seconds))),
                "--user-agent",
                self.settings.user_agent,
                "--header",
                (
                    "Accept: text/html,application/xhtml+xml,application/pdf,"
                    "application/xml;q=0.9,*/*;q=0.5"
                ),
                "--output",
                handle.name,
                "--write-out",
                "%{http_code}\n%{url_effective}\n%{content_type}",
                url,
            ]
            try:
                completed = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=self.settings.request_timeout_seconds + 5,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise FetchError(f"Verified curl fallback failed: {exc}") from exc
            if completed.returncode != 0:
                detail = clean_text(completed.stderr) or f"curl exit {completed.returncode}"
                raise FetchError(f"Verified curl fallback failed: {detail}")
            metadata = completed.stdout.splitlines()
            if len(metadata) < 3:
                raise FetchError("Verified curl fallback returned incomplete metadata")
            try:
                status = int(metadata[0])
            except ValueError as exc:
                raise FetchError("Verified curl fallback returned an invalid HTTP status") from exc
            final_url = canonicalize_url(metadata[1] or url)
            content_type = metadata[2].strip() or "application/octet-stream"
            handle.seek(0)
            body = handle.read(self.settings.max_download_bytes + 1)

        if len(body) > self.settings.max_download_bytes:
            raise FetchError(
                "Response exceeds "
                f"MATTRESS_INTEL_MAX_DOWNLOAD_BYTES={self.settings.max_download_bytes}"
            )
        stored = self.object_store.put_bytes(
            body,
            content_type=content_type,
            source_url=final_url,
            namespace="documents",
        )
        return FetchedDocument(
            url=final_url,
            status=status,
            content_type=content_type,
            body=body,
            retrieved_at_epoch=time.time(),
            artifact_path=stored.local_path,
            object_uri=stored.object_uri,
            capture_method="curl_verified_tls",
        )

    def fetch(self, url: str) -> FetchedDocument:
        canonical_url = canonicalize_url(url)
        if not self.allowed(canonical_url):
            raise FetchError("Blocked by robots.txt")
        self._rate_limit(canonical_url)
        request = Request(
            canonical_url,
            headers={
                "User-Agent": self.settings.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/pdf,application/xml;q=0.9,*/*;q=0.5",
                "Accept-Encoding": "identity",
            },
        )
        tls_backend = "unknown"
        try:
            response, tls_backend = self._open_verified(request)
            with response:
                status = int(getattr(response, "status", 200))
                final_url = canonicalize_url(response.geturl())
                content_type = response.headers.get_content_type()
                charset = response.headers.get_content_charset()
                if charset:
                    content_type = f"{content_type}; charset={charset}"
                body = response.read(self.settings.max_download_bytes + 1)
        except HTTPError as exc:
            raise FetchError(f"HTTP {exc.code}") from exc
        except URLError as exc:
            raise FetchError(str(exc)) from exc
        except (TimeoutError, ValueError) as exc:
            raise FetchError(str(exc)) from exc
        if len(body) > self.settings.max_download_bytes:
            raise FetchError(f"Response exceeds MATTRESS_INTEL_MAX_DOWNLOAD_BYTES={self.settings.max_download_bytes}")
        stored = self.object_store.put_bytes(
            body, content_type=content_type, source_url=final_url, namespace="documents"
        )
        return FetchedDocument(
            url=final_url,
            status=status,
            content_type=content_type,
            body=body,
            retrieved_at_epoch=time.time(),
            artifact_path=stored.local_path,
            object_uri=stored.object_uri,
            capture_method=f"http_{tls_backend}",
        )

    def robots_sitemaps(self, base_url: str) -> list[str]:
        parser = self._load_robots(base_url)
        return [canonicalize_url(url) for url in parser.site_maps() or []]

    def close(self) -> None:
        pass


class HybridBrowserFetcher(HttpFetcher):
    """Render JS shells and record image/JSON network endpoints."""

    def __init__(self, settings: Settings, respect_robots_txt: bool = True) -> None:
        super().__init__(settings, respect_robots_txt)
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    @staticmethod
    def _needs_browser(document: FetchedDocument) -> bool:
        if not document.is_html:
            return False
        lowered = document.text.casefold()
        visible_text = clean_text(re.sub(r"<[^>]+>", " ", lowered))
        shell_marker = any(marker in lowered for marker in ('id="__next"', "id='__next'", 'id="root"', 'id="app"'))
        has_product_json = '"@type":"product"' in lowered.replace(" ", "")
        return shell_marker and len(visible_text) < 800 and not has_product_json

    def _ensure_browser(self) -> Browser:
        if self._browser is not None:
            return self._browser
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise FetchError("Playwright is not installed. Install requirements and Chromium.") from exc
        self._playwright = sync_playwright().start()
        browser = self._playwright.chromium.launch(headless=True)
        self._browser = browser
        return browser

    def fetch(self, url: str) -> FetchedDocument:
        document = super().fetch(url)
        if not self._needs_browser(document):
            return document
        browser = self._ensure_browser()
        page = browser.new_page(user_agent=self.settings.user_agent)
        network: list[dict[str, object]] = []

        def record_response(response) -> None:
            try:
                content_type = response.headers.get("content-type", "")
                resource_type = response.request.resource_type
                if resource_type in {"image", "xhr", "fetch"} or "json" in content_type.casefold():
                    item: dict[str, object] = {
                        "url": response.url,
                        "resource_type": resource_type,
                        "content_type": content_type,
                        "status": response.status,
                    }
                    if resource_type in {"xhr", "fetch"} and "json" in content_type.casefold():
                        try:
                            payload = response.body()
                            if len(payload) <= 500_000:
                                stored_network = self.object_store.put_bytes(
                                    payload,
                                    content_type=content_type or "application/json",
                                    source_url=response.url,
                                    namespace="network",
                                )
                                item["body_excerpt"] = payload.decode("utf-8", errors="replace")[:20_000]
                                item["artifact_path"] = stored_network.local_path
                                item["object_uri"] = stored_network.object_uri
                        except Exception:
                            pass
                    network.append(item)
            except Exception:
                return

        page.on("response", record_response)
        try:
            response = page.goto(
                document.url,
                wait_until="networkidle",
                timeout=int(self.settings.request_timeout_seconds * 1_000),
            )
            rendered = page.content().encode("utf-8")
            if len(rendered) > self.settings.max_download_bytes:
                raise FetchError("Rendered page exceeds MATTRESS_INTEL_MAX_DOWNLOAD_BYTES.")
            stored = self.object_store.put_bytes(
                rendered,
                content_type="text/html; charset=utf-8",
                source_url=document.url,
                namespace="documents",
            )
            images = tuple(
                {"url": item["url"], "discovery_method": "playwright_network"}
                for item in network
                if item.get("resource_type") == "image"
            )
            return FetchedDocument(
                url=document.url,
                status=int(response.status if response else document.status),
                content_type="text/html; charset=utf-8",
                body=rendered,
                retrieved_at_epoch=time.time(),
                artifact_path=stored.local_path,
                object_uri=stored.object_uri,
                capture_method="playwright",
                asset_manifest=images,
                network_manifest=tuple(network[:500]),
            )
        finally:
            page.close()

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None


class EvidenceFetcher:
    """Service-first or local-first page capture with graceful fallback."""

    def __init__(
        self,
        primary: HttpFetcher,
        settings: Settings,
        reader: JinaReaderClient | None = None,
        firecrawl: FirecrawlClient | None = None,
    ) -> None:
        self.primary = primary
        self.settings = settings
        self.reader = reader
        self.firecrawl = firecrawl
        self.object_store = primary.object_store

    def robots_sitemaps(self, base_url: str) -> list[str]:
        return self.primary.robots_sitemaps(base_url)

    def _firecrawl_fetch(self, url: str) -> FetchedDocument:
        if self.firecrawl is None:
            raise FetchError("Firecrawl is not configured")

        if not self.primary.allowed(url):
            raise FetchError("Blocked by robots.txt")

        try:
            page = self.firecrawl.scrape(url)
        except FirecrawlError as exc:
            raise FetchError(str(exc)) from exc

        text = page.raw_html or page.html or page.markdown
        if not text:
            raise FetchError("Firecrawl returned empty content")

        content_type = (
            "text/html; charset=utf-8"
            if page.raw_html or page.html
            else "text/markdown; charset=utf-8"
        )

        body = text.encode("utf-8")

        stored = self.object_store.put_bytes(
            body,
            content_type=content_type,
            source_url=page.url,
            namespace="documents",
        )

        manifest: tuple[dict[str, object], ...] = tuple(
            {
                "url": image.url,
                "alt_text": image.alt_text,
                "width": image.width,
                "height": image.height,
                "discovery_method": "firecrawl",
            }
            for image in page.images
        )

        return FetchedDocument(
            url=canonicalize_url(page.url),
            status=200,
            content_type=content_type,
            body=body,
            retrieved_at_epoch=time.time(),
            artifact_path=stored.local_path,
            object_uri=stored.object_uri,
            capture_method="firecrawl",
            asset_manifest=manifest,
        )

    def _jina_fetch(self, url: str) -> FetchedDocument:
        if self.reader is None:
            raise FetchError("Jina Reader is not configured")
        if not self.primary.allowed(url):
            raise FetchError("Blocked by robots.txt")
        try:
            result = self.reader.read(url)
        except JinaError as exc:
            raise FetchError(str(exc)) from exc
        if not result.content.strip():
            raise FetchError("Jina Reader returned empty content")
        body = result.content.encode("utf-8")
        stored = self.object_store.put_bytes(
            body, content_type="text/markdown; charset=utf-8", source_url=result.url, namespace="documents"
        )
        return FetchedDocument(
            url=canonicalize_url(result.url),
            status=200,
            content_type="text/markdown; charset=utf-8",
            body=body,
            retrieved_at_epoch=time.time(),
            artifact_path=stored.local_path,
            object_uri=stored.object_uri,
            capture_method="jina_reader",
        )

    def fetch(self, url: str) -> FetchedDocument:
        lower = url.casefold()
        # Preserve XML fidelity through the local fetcher.
        if lower.endswith((".xml", ".xml.gz", "/robots.txt")):
            return self.primary.fetch(url)
        path = urlsplit(url).path.casefold()
        expected_pdf = lower.endswith(".pdf") or "downloadpfdfile" in path
        # Preserve the original binary whenever possible. Legacy regulatory
        # portals can have incomplete TLS chains, so readable-service fallbacks
        # are attempted without ever disabling certificate verification.
        service_attempts: list[
            tuple[str, Callable[[str], FetchedDocument]]
        ] = []
        if self.reader is not None:
            service_attempts.append(("jina", self._jina_fetch))
        if self.firecrawl is not None:
            service_attempts.append(("firecrawl", self._firecrawl_fetch))
        local_attempt = ("local", self.primary.fetch)
        if expected_pdf or self.settings.capture_strategy == "local_first":
            attempts = (local_attempt, *service_attempts)
        else:
            attempts = (*reversed(service_attempts), local_attempt)
        errors: list[str] = []
        local_certificate_failure = False
        for name, method in attempts:
            try:
                document = method(url)
            except FetchError as exc:
                errors.append(f"{name}: {exc}")
                if (
                    name == "local"
                    and "CERTIFICATE_VERIFY_FAILED" in str(exc)
                ):
                    local_certificate_failure = True
                continue
            try:
                text_length = len(document.extracted_text(max_characters=20_000))
            except FetchError as exc:
                errors.append(f"{name}: {exc}")
                continue
            if expected_pdf:
                actual_pdf = document.body.lstrip().startswith(b"%PDF")
                readable_fallback = (
                    "markdown" in document.content_type.casefold()
                    and text_length >= 100
                )
                if not actual_pdf and not readable_fallback:
                    errors.append(
                        f"{name}: expected PDF content but received "
                        f"{document.content_type or 'an unknown media type'}"
                    )
                    continue
            if (
                name == "local"
                and self.settings.jina_reader_on_thin_page
                and text_length < self.settings.jina_reader_min_characters
                and self.reader is not None
            ):
                try:
                    return self._jina_fetch(url)
                except FetchError:
                    pass
            return document
        if local_certificate_failure:
            try:
                document = self.primary.fetch_with_system_curl(url)
                if expected_pdf:
                    if not document.body.lstrip().startswith(b"%PDF"):
                        raise FetchError(
                            "expected PDF content but verified curl received "
                            f"{document.content_type or 'an unknown media type'}"
                        )
                    return document
                text_length = len(document.extracted_text(max_characters=20_000))
                if text_length < 1 and document.content_type.casefold().startswith("text/"):
                    raise FetchError("verified curl returned empty readable content")
                return document
            except FetchError as exc:
                errors.append(f"curl-last-resort: {exc}")
        raise FetchError("; ".join(errors) or "All capture methods failed")

    def close(self) -> None:
        self.primary.close()


def _same_company_host(url: str, base_url: str) -> bool:
    candidate = (urlsplit(url).hostname or "").removeprefix("www.")
    base = (urlsplit(base_url).hostname or "").removeprefix("www.")
    return candidate == base or candidate.endswith(f".{base}")


def _url_priority(url: str) -> int:
    lowered = url.casefold()
    path = urlsplit(url).path.casefold()
    if any(hint in lowered for hint in REJECT_HINTS) or NON_PRODUCT_PATH_RE.search(path):
        return -100
    if "/blogs/" in path or "/blog/" in path:
        return -20
    if PRODUCT_DETAIL_PATH_RE.search(path):
        score = 90
    elif path.rstrip("/") in {"/collections/mattress", "/collections/mattresses"}:
        score = 24
    elif "/collections/" in path:
        score = 4
    else:
        score = sum(10 for hint in PRODUCT_HINTS if hint in path)
    if lowered.endswith((".pdf", ".pdf?download=1")):
        score += 70
    if any(word in lowered for word in ("catalog", "catalogue", "brochure", "specification")):
        score += 25
    if any(word in lowered for word in ("patent", "teardown", "cut-open", "cutopen")):
        score += 20
    return score


def _html_links(document: FetchedDocument) -> list[str]:
    links: list[str] = []
    if document.is_html:
        try:
            tree = html.fromstring(document.body, base_url=document.url)
        except (etree.ParserError, ValueError):
            tree = None
        if tree is not None:
            for raw in tree.xpath("//a[@href]/@href | //link[@href]/@href"):
                if not isinstance(raw, str) or raw.startswith(("mailto:", "tel:", "javascript:", "#")):
                    continue
                try:
                    links.append(canonicalize_url(raw, document.url))
                except ValueError:
                    continue
    # Network JSON/XHR endpoints are evidence, but only same-host HTML-like routes are crawl candidates.
    for item in document.network_manifest:
        raw = str(item.get("url") or "")
        if raw.startswith(("http://", "https://")):
            try:
                links.append(canonicalize_url(raw))
            except ValueError:
                continue
    return list(dict.fromkeys(links))


def _sitemap_locations(document: FetchedDocument) -> tuple[list[str], bool]:
    raw = document.body
    if document.url.casefold().endswith(".gz") or "gzip" in document.content_type.casefold():
        try:
            raw = gzip.decompress(raw)
        except OSError:
            return [], False
    try:
        root = etree.fromstring(raw, parser=etree.XMLParser(resolve_entities=False, no_network=True))
    except etree.XMLSyntaxError:
        return [], False
    local_name = etree.QName(root).localname.casefold()
    locations = [clean_text(str(value)) for value in root.xpath("//*[local-name()='loc']/text()")]
    return [url for url in locations if url], local_name == "sitemapindex"


class CatalogueCrawler:
    """Priority-based, bounded official-site crawler with a decision log."""

    def __init__(self, fetcher: CatalogueFetcher) -> None:
        self.fetcher = fetcher

    def _discover_sitemaps(self, base_url: str, report: CrawlReport, limit: int = 25) -> set[str]:
        seeds = self.fetcher.robots_sitemaps(base_url)
        seeds.append(canonicalize_url("/sitemap.xml", base_url))
        pending = deque(dict.fromkeys(seeds))
        visited: set[str] = set()
        page_urls: set[str] = set()
        while pending and len(visited) < limit:
            sitemap_url = pending.popleft()
            if sitemap_url in visited:
                continue
            visited.add(sitemap_url)
            try:
                document = self.fetcher.fetch(sitemap_url)
            except FetchError as exc:
                report.crawl_log.append({"stage": "sitemap", "action": "failed", "url": sitemap_url, "reason": str(exc)})
                continue
            locations, is_index = _sitemap_locations(document)
            report.crawl_log.append(
                {"stage": "sitemap", "action": "parsed", "url": sitemap_url, "locations": len(locations), "is_index": is_index}
            )
            for location in locations:
                if not _same_company_host(location, base_url):
                    continue
                canonical = canonicalize_url(location)
                if is_index or canonical.casefold().endswith((".xml", ".xml.gz")):
                    pending.append(canonical)
                else:
                    page_urls.add(canonical)
        return page_urls

    def crawl(self, base_url: str, max_pages: int, extra_urls: list[str] | None = None, *, max_depth: int = 4) -> CrawlReport:
        if not urlsplit(base_url).scheme:
            base_url = f"https://{base_url}"
        base_url = canonicalize_url(base_url)
        report = CrawlReport()
        sitemap_pages = self._discover_sitemaps(base_url, report)
        report.sitemap_urls.update(sitemap_pages)
        report.discovered_urls.update(sitemap_pages)
        pending: list[tuple[int, int, int, str, str | None, str]] = []
        queued: set[str] = set()
        sequence = 0

        def enqueue(url: str, *, depth: int, parent_url: str | None, reason: str, force_priority: int | None = None) -> None:
            nonlocal sequence
            canonical = canonicalize_url(url, parent_url)
            if canonical in queued:
                return
            priority = force_priority if force_priority is not None else _url_priority(canonical)
            if priority < 0:
                report.crawl_log.append(
                    {"stage": "crawl", "action": "rejected", "url": canonical, "parent_url": parent_url, "depth": depth, "priority": priority, "reason": "URL matched a rejected path"}
                )
                return
            queued.add(canonical)
            report.discovered_urls.add(canonical)
            sequence += 1
            heapq.heappush(pending, (-priority, sequence, depth, canonical, parent_url, reason))
            report.crawl_log.append(
                {"stage": "crawl", "action": "queued", "url": canonical, "parent_url": parent_url, "depth": depth, "priority": priority, "reason": reason}
            )

        enqueue(base_url, depth=0, parent_url=None, reason="official homepage", force_priority=100)
        for sitemap_url in sorted(sitemap_pages, key=lambda item: (-_url_priority(item), item)):
            enqueue(sitemap_url, depth=0, parent_url=None, reason="official sitemap")
        for url in extra_urls or []:
            if _same_company_host(url, base_url):
                enqueue(url, depth=0, parent_url=None, reason="same-domain seed/search result")

        while pending and len(report.documents) < max_pages:
            negative_priority, _, depth, url, parent_url, reason = heapq.heappop(pending)
            priority = -negative_priority
            if depth > max_depth:
                report.crawl_log.append(
                    {"stage": "crawl", "action": "skipped", "url": url, "depth": depth, "priority": priority, "reason": f"maximum crawl depth {max_depth} exceeded"}
                )
                continue
            try:
                document = self.fetcher.fetch(url)
            except FetchError as exc:
                action = "blocked" if "robots.txt" in str(exc) else "failed"
                if action == "blocked":
                    report.blocked_urls.add(url)
                else:
                    report.failed_urls[url] = str(exc)
                report.crawl_log.append(
                    {"stage": "crawl", "action": action, "url": url, "parent_url": parent_url, "depth": depth, "priority": priority, "reason": str(exc)}
                )
                continue
            report.documents.append(document)
            report.crawl_log.append(
                {
                    "stage": "crawl", "action": "fetched", "url": document.url, "parent_url": parent_url,
                    "depth": depth, "priority": priority, "reason": reason, "status": document.status,
                    "content_type": document.content_type, "bytes": len(document.body),
                    "artifact_path": document.artifact_path, "object_uri": document.object_uri,
                    "capture_method": document.capture_method, "asset_manifest": len(document.asset_manifest),
                    "network_manifest": len(document.network_manifest),
                }
            )
            if depth >= max_depth:
                continue
            for link in _html_links(document):
                if not _same_company_host(link, base_url):
                    continue
                link_priority = _url_priority(link)
                early = len(report.documents) < max(10, max_pages // 5)
                if link_priority > 0 or early:
                    enqueue(
                        link,
                        depth=depth + 1,
                        parent_url=document.url,
                        reason="product/catalogue URL hint" if link_priority > 0 else "bounded early-site exploration",
                    )
        return report