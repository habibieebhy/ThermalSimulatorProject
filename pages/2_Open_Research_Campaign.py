"""Domain-neutral, approval-gated evidence discovery campaigns."""

from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pandas as pd
import streamlit as st

from evidence_discovery import (
    CampaignDiscoveryResult,
    CampaignRequest,
    CandidateDecision,
    DiscoveryDirection,
    DomainPolicy,
)
from evidence_discovery.jobs import (
    ACTIVE_STATUSES,
    CampaignJob,
    CampaignJobStore,
)
from evidence_discovery.tasks import (
    enqueue_campaign_capture,
    enqueue_campaign_discovery,
)
from mattress_intelligence.settings import Settings

settings = Settings()
settings.ensure_directories()
job_store = CampaignJobStore.from_settings(settings)

DIRECTION_LABELS = {
    "Official company or organisation sources": DiscoveryDirection.OFFICIAL,
    "Government and regulatory records": DiscoveryDirection.GOVERNMENT,
    "Academic papers and theses": DiscoveryDirection.ACADEMIC,
    "Patents": DiscoveryDirection.PATENTS,
    "Technical manuals, specifications and case studies": DiscoveryDirection.TECHNICAL,
    "News releases and archives": DiscoveryDirection.NEWS_ARCHIVES,
    "Open web": DiscoveryDirection.OPEN_WEB,
}

POLICY_LABELS = {
    "Open web within the selected directions": DomainPolicy.OPEN_WEB,
    "Only supplied URLs and domains": DomainPolicy.SEED_DOMAINS_ONLY,
    "Supplied domains plus government/academic sources": (
        DomainPolicy.SEED_PLUS_AUTHORITY
    ),
}

STAGE_LABELS = {
    "queued": "Queued for the Celery worker",
    "initializing": "Initializing research services",
    "building_queries": "Building searches from the campaign brief",
    "searching": "Searching approved directions",
    "crawling_domains": "Exploring approved seed domains",
    "classifying": "Classifying candidate evidence",
    "writing_discovery": "Writing candidates and route decisions",
    "capturing": "Capturing approved evidence",
    "writing_bundle": "Writing portable BRIXTA evidence bundle",
    "completed": "Complete",
    "failed": "Failed",
}


def _lines(value: str) -> list[str]:
    return list(
        dict.fromkeys(
            line.strip().lstrip("-•").strip()
            for line in value.splitlines()
            if line.strip().lstrip("-•").strip()
        )
    )


def _urls(value: str) -> list[str]:
    urls = _lines(value)
    invalid = [
        item
        for item in urls
        if urlsplit(item).scheme not in {"http", "https"}
        or not urlsplit(item).netloc
    ]
    if invalid:
        raise ValueError(
            "These entries are not complete HTTP/HTTPS URLs: "
            + ", ".join(invalid)
        )
    return urls


def _domains(value: str) -> list[str]:
    values = _lines(value)
    invalid: list[str] = []
    domains: list[str] = []
    for item in values:
        parsed = urlsplit(
            item if "://" in item else f"https://{item}"
        )
        if not parsed.hostname:
            invalid.append(item)
            continue
        domains.append(parsed.hostname.removeprefix("www."))
    if invalid:
        raise ValueError("Invalid domains: " + ", ".join(invalid))
    return list(dict.fromkeys(domains))


def _load_json(path: str | None) -> dict:
    if not path:
        raise RuntimeError("The completed job has no result path.")
    source = Path(path)
    if not source.is_file():
        raise RuntimeError(f"Result file does not exist: {source}")
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Result file is not a JSON object: {source}")
    return payload


def _broker_available() -> bool:
    from mattress_intelligence.celery_app import celery_app

    try:
        with celery_app.connection_for_write() as connection:
            connection.ensure_connection(max_retries=1)
        return True
    except Exception:
        return False


def _worker_available() -> bool:
    from mattress_intelligence.celery_app import celery_app

    try:
        return bool(celery_app.control.inspect(timeout=1.5).ping())
    except Exception:
        return False


def _require_worker() -> None:
    if not settings.celery_enabled:
        raise RuntimeError("Celery is disabled. Set CELERY_ENABLED=true.")
    if not _broker_available():
        raise RuntimeError("Redis is unavailable.")
    if not _worker_available():
        raise RuntimeError(
            "No Celery worker replied. Start the worker with the solo pool on macOS."
        )


def _render_active_job(job: CampaignJob) -> None:
    label = STAGE_LABELS.get(
        job.stage,
        job.stage.replace("_", " ").title(),
    )
    with st.status(label, expanded=True, state="running"):
        st.progress(job.progress, text=f"{job.progress}% · {label}")
        if job.message:
            st.write(job.message)
        st.caption(f"Job {job.job_id} · output {job.output_dir}")
    time.sleep(2)
    st.rerun()


def _available_providers() -> list[str]:
    providers: list[str] = []
    if (
        settings.firecrawl_api_key
        or settings.jina_api_key
        or settings.tavily_api_key
    ):
        providers.append("services")
    if settings.firecrawl_api_key:
        providers.append("firecrawl")
    if settings.jina_api_key is not None:
        providers.append("jina")
    if settings.tavily_api_key:
        providers.append("tavily")
    return providers or ["services"]


st.title("Open Research Campaign")
st.caption(
    "Start from a topic, URL, domain or manual searches. BRIXTA records every "
    "search and crawl direction, explains accepted and rejected candidates, "
    "waits for your approval, then exports a portable evidence bundle."
)

with st.expander("What this mode does", expanded=False):
    st.write(
        "This is a bounded evidence-discovery engine, not an unrestricted "
        "internet crawler. Search directions, domains, depth, pages, candidate "
        "count and final captures all have explicit limits."
    )
    st.write(
        "The resulting ZIP contains original captures, extracted text, the "
        "candidate ledger, the route ledger and a provenance manifest for "
        "manual ingestion into BRIXTA RAG."
    )

with st.form("open-research-campaign"):
    basics_left, basics_right = st.columns(2)
    with basics_left:
        campaign_name = st.text_input(
            "Campaign name",
            placeholder="Example: Low-clinker cement grinding evidence",
        )
        topic = st.text_area(
            "Topic or research objective",
            placeholder=(
                "Describe what you want to find. This becomes the relevance "
                "anchor and query source."
            ),
            height=120,
        )
        geography = st.text_input(
            "Geography, if relevant",
            placeholder="Example: India or Meghalaya",
        )
    with basics_right:
        seed_urls_text = st.text_area(
            "Starting URLs",
            placeholder="One complete HTTP/HTTPS URL per line",
            height=90,
        )
        seed_domains_text = st.text_area(
            "Domains to explore",
            placeholder="example.org\ncompany.example",
            height=90,
        )
        manual_queries_text = st.text_area(
            "Exact manual search queries",
            placeholder="One query per line; optional",
            height=90,
        )

    selected_direction_labels = st.multiselect(
        "Approved discovery directions",
        options=list(DIRECTION_LABELS),
        default=[
            "Official company or organisation sources",
            "Government and regulatory records",
            "Technical manuals, specifications and case studies",
            "Academic papers and theses",
        ],
        help=(
            "These directions generate complementary queries and label why a "
            "candidate was pursued."
        ),
    )
    policy_label = st.selectbox(
        "Domain boundary",
        options=list(POLICY_LABELS),
        help=(
            "Restricted policies reject search results outside the supplied "
            "domains, unless authority sources are explicitly allowed."
        ),
    )
    provider = st.selectbox(
        "Search and capture provider",
        options=_available_providers(),
        help=(
            "The selected provider remains locked between discovery and "
            "capture. Direct verified PDF downloads remain preferred."
        ),
    )

    with st.expander("Campaign and capture limits", expanded=True):
        first, second, third = st.columns(3)
        with first:
            max_queries = st.slider("Search queries", 1, 20, 8)
            results_per_query = st.slider(
                "Results per query",
                1,
                25,
                8,
            )
        with second:
            max_site_pages = st.slider(
                "Seed-domain pages to explore",
                0,
                100,
                20,
                help="Total across all supplied domains.",
            )
            max_depth = st.slider("Maximum link depth", 0, 5, 2)
        with third:
            max_candidates = st.slider(
                "Candidates retained",
                10,
                500,
                150,
                step=10,
            )
            max_capture_documents = st.slider(
                "Maximum approved captures",
                1,
                100,
                20,
            )
        minimum_relevance = st.slider(
            "Automatic acceptance threshold",
            0.0,
            1.0,
            0.28,
            0.01,
            help=(
                "Lower values explore more broadly. Rejected candidates remain "
                "visible and can still be manually selected."
            ),
        )

    submit_discovery = st.form_submit_button(
        "Queue research discovery",
        type="primary",
        use_container_width=True,
    )

if submit_discovery:
    try:
        _require_worker()
        request = CampaignRequest(
            campaign_name=campaign_name,
            topic=topic,
            seed_urls=tuple(_urls(seed_urls_text)),
            seed_domains=tuple(_domains(seed_domains_text)),
            manual_queries=tuple(_lines(manual_queries_text)),
            directions=tuple(
                DIRECTION_LABELS[label]
                for label in selected_direction_labels
            ),
            domain_policy=POLICY_LABELS[policy_label],
            geography=geography or None,
            max_queries=max_queries,
            results_per_query=results_per_query,
            max_candidates=max_candidates,
            max_site_pages=max_site_pages,
            max_depth=max_depth,
            max_capture_documents=max_capture_documents,
            minimum_relevance=minimum_relevance,
        )
        job_id = uuid4().hex
        output_dir = (
            settings.output_dir / "research_campaigns" / job_id / "discovery"
        )
        job = job_store.create(
            job_type="discovery",
            request={**request.to_dict(), "provider": provider},
            output_dir=output_dir,
            job_id=job_id,
        )
        task = enqueue_campaign_discovery(
            request,
            provider=provider,
            output_dir=output_dir,
            job_id=job_id,
        )
        if str(task.id) != job_id:
            job_store.update_task_id(job_id, str(task.id))
        st.session_state["campaign_discovery_job_id"] = job.job_id
        st.rerun()
    except Exception as exc:
        st.error(f"Campaign discovery could not be queued: {exc}")

jobs = job_store.list(limit=100)
discovery_jobs = [item for item in jobs if item.job_type == "discovery"]
if not discovery_jobs:
    st.info(
        "Create your first campaign. A topic is optional when you provide a URL, "
        "domain, or exact manual query."
    )
    st.stop()

st.subheader("Research campaigns")
st.dataframe(
    pd.DataFrame(
        [
            {
                "Session": item.job_id[:8],
                "Campaign": item.request.get("campaign_name"),
                "Status": item.status.upper(),
                "Stage": STAGE_LABELS.get(item.stage, item.stage),
                "Progress": f"{item.progress}%",
                "Submitted UTC": item.submitted_at.strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
            }
            for item in discovery_jobs
        ]
    ),
    hide_index=True,
    use_container_width=True,
)

job_labels = {
    item.job_id: (
        f"{item.request.get('campaign_name') or 'Campaign'} · "
        f"{item.status.upper()} · {item.job_id[:8]}"
    )
    for item in discovery_jobs
}
default_id = st.session_state.get(
    "campaign_discovery_job_id",
    discovery_jobs[0].job_id,
)
if default_id not in job_labels:
    default_id = discovery_jobs[0].job_id
selected_job_id = st.selectbox(
    "Open a campaign",
    options=list(job_labels),
    index=list(job_labels).index(default_id),
    format_func=lambda item: job_labels[item],
)
st.session_state["campaign_discovery_job_id"] = selected_job_id
discovery_job = job_store.get(selected_job_id)
if discovery_job.status in ACTIVE_STATUSES:
    _render_active_job(discovery_job)
if discovery_job.status == "failed":
    st.error(discovery_job.error or "Campaign discovery failed.")
    st.stop()

result = CampaignDiscoveryResult.from_dict(
    _load_json(discovery_job.result_json_path)
)
for warning in result.warnings:
    st.warning(warning)

metric_a, metric_b, metric_c, metric_d = st.columns(4)
metric_a.metric("Queries", len(result.queries))
metric_b.metric("Accepted", result.accepted_count)
metric_c.metric("Rejected", result.rejected_count)
metric_d.metric("Route decisions", len(result.route_events))

with st.expander("Queries generated from the campaign brief"):
    for query in result.queries:
        st.code(query, language=None)

st.subheader("Campaign direction ledger")
route_rows = [
    {
        "Step": item.sequence,
        "Stage": item.stage,
        "Action": item.action,
        "Direction": item.direction,
        "Score": item.score,
        "Depth": item.depth,
        "Reason": item.reason,
        "Query": item.query,
        "Parent": item.parent_url,
        "URL": item.url,
    }
    for item in result.route_events
]
st.dataframe(
    pd.DataFrame(route_rows),
    hide_index=True,
    use_container_width=True,
    column_config={
        "URL": st.column_config.LinkColumn("URL"),
        "Parent": st.column_config.LinkColumn("Parent"),
    },
)

accepted_tab, rejected_tab = st.tabs(
    ("Accepted candidates", "Rejected candidates")
)
for tab, decision in (
    (accepted_tab, CandidateDecision.ACCEPTED),
    (rejected_tab, CandidateDecision.REJECTED),
):
    with tab:
        rows = [
            {
                "Score": item.relevance_score,
                "Direction": item.direction.replace("_", " "),
                "Source": item.source_kind.replace("_", " "),
                "Domain": item.publisher_domain,
                "Title": item.title,
                "Reason": "; ".join(item.reasons),
                "URL": item.url,
            }
            for item in result.candidates
            if item.decision == decision
        ]
        if rows:
            st.dataframe(
                pd.DataFrame(rows),
                hide_index=True,
                use_container_width=True,
                column_config={"URL": st.column_config.LinkColumn("URL")},
            )
        else:
            st.info(f"No {decision.value} candidates in this campaign.")

st.subheader("Approve evidence for capture")
st.caption(
    "Automatically accepted results begin selected. You may remove them, add "
    "a rejected lead you disagree with, or paste another URL. Your override "
    "remains visible in the exported provenance."
)
label_to_id = {
    (
        f"[{item.decision.value}] {item.relevance_score:.2f} · "
        f"{item.direction.replace('_', ' ')} · "
        f"{(item.title or item.url)[:100]} · {item.candidate_id[-6:]}"
    ): item.candidate_id
    for item in result.candidates
}
default_labels = [
    label
    for label, candidate_id in label_to_id.items()
    if next(
        item
        for item in result.candidates
        if item.candidate_id == candidate_id
    ).decision
    == CandidateDecision.ACCEPTED
][
    : result.request.max_capture_documents
]
selected_labels = st.multiselect(
    "Documents to capture",
    options=list(label_to_id),
    default=default_labels,
    max_selections=result.request.max_capture_documents,
)
manual_capture_text = st.text_area(
    "Additional manual URLs",
    placeholder="One complete HTTP/HTTPS URL per line",
)
respect_robots = st.checkbox("Respect robots.txt", value=True)
capture_provider = str(
    discovery_job.request.get("provider") or "services"
).casefold()
st.caption(
    f"Provider lock: `{capture_provider}`. Capture ceiling: "
    f"{result.request.max_capture_documents} documents."
)

capture_submit = st.button(
    "Queue approved evidence capture",
    type="primary",
    use_container_width=True,
    disabled=not selected_labels and not manual_capture_text.strip(),
)
if capture_submit:
    try:
        _require_worker()
        selected_ids = [label_to_id[label] for label in selected_labels]
        manual_urls = _urls(manual_capture_text)
        if len(selected_ids) + len(manual_urls) > (
            result.request.max_capture_documents
        ):
            raise ValueError(
                "Selected and manual URLs exceed the campaign capture ceiling"
            )
        capture_job_id = uuid4().hex
        output_dir = (
            settings.output_dir
            / "research_campaigns"
            / capture_job_id
            / "capture"
        )
        capture_job = job_store.create(
            job_type="capture",
            request={
                "campaign_id": result.campaign_id,
                "selected_candidate_ids": selected_ids,
                "manual_urls": manual_urls,
                "respect_robots_txt": respect_robots,
                "capture_provider": capture_provider,
            },
            output_dir=output_dir,
            parent_job_id=discovery_job.job_id,
            job_id=capture_job_id,
        )
        task = enqueue_campaign_capture(
            discovery_json_path=str(discovery_job.result_json_path),
            selected_candidate_ids=selected_ids,
            manual_urls=manual_urls,
            respect_robots_txt=respect_robots,
            capture_provider=capture_provider,
            output_dir=output_dir,
            job_id=capture_job_id,
        )
        if str(task.id) != capture_job_id:
            job_store.update_task_id(capture_job_id, str(task.id))
        st.session_state["campaign_capture_job_id"] = capture_job.job_id
        st.rerun()
    except Exception as exc:
        st.error(f"Evidence capture could not be queued: {exc}")

capture_jobs = [
    item
    for item in jobs
    if item.job_type == "capture"
    and item.parent_job_id == discovery_job.job_id
]
if capture_jobs:
    capture_labels = {
        item.job_id: (
            f"{item.status.upper()} · "
            f"{item.submitted_at.strftime('%Y-%m-%d %H:%M UTC')} · "
            f"{item.job_id[:8]}"
        )
        for item in capture_jobs
    }
    selected_capture_id = st.selectbox(
        "Open a capture session",
        options=list(capture_labels),
        format_func=lambda item: capture_labels[item],
    )
    capture_job = job_store.get(selected_capture_id)
    if capture_job.status in ACTIVE_STATUSES:
        _render_active_job(capture_job)
    if capture_job.status == "failed":
        st.error(capture_job.error or "Campaign capture failed.")
    elif capture_job.status == "completed":
        manifest = _load_json(capture_job.result_json_path)
        bundle = manifest.get("bundle") or {}
        captures = bundle.get("captures") or []
        st.subheader("Portable BRIXTA evidence bundle")
        capture_metrics = st.columns(3)
        capture_metrics[0].metric(
            "Usable",
            bundle.get("successful_count", 0),
        )
        capture_metrics[1].metric(
            "Needs review",
            bundle.get("review_count", 0),
        )
        capture_metrics[2].metric("Failed", bundle.get("failed_count", 0))
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Status": item.get("status"),
                        "Method": item.get("capture_method"),
                        "Characters": item.get("text_characters"),
                        "Original": item.get("original_binary_preserved"),
                        "Bundle document": item.get("bundle_document_path"),
                        "Validation": "; ".join(
                            item.get("validation_notes") or []
                        ),
                        "URL": item.get("url"),
                        "Error": item.get("error"),
                    }
                    for item in captures
                ]
            ),
            hide_index=True,
            use_container_width=True,
            column_config={"URL": st.column_config.LinkColumn("URL")},
        )
        archive_path = Path(
            str(
                bundle.get("archive_path")
                or capture_job.summary.get("archive_path")
                or ""
            )
        )
        if archive_path.is_file():
            st.download_button(
                "Download BRIXTA evidence bundle ZIP",
                data=archive_path.read_bytes(),
                file_name=f"{bundle.get('bundle_id', 'evidence_bundle')}.zip",
                mime="application/zip",
                type="primary",
                use_container_width=True,
            )
        st.download_button(
            "Download bundle manifest",
            data=json.dumps(manifest, indent=2, ensure_ascii=False),
            file_name="campaign_manifest.json",
            mime="application/json",
            use_container_width=True,
        )
