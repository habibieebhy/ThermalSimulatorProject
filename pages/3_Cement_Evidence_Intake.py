"""Celery-backed discovery and approval-gated cement evidence intake."""

from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pandas as pd
import streamlit as st

from cement_intelligence import CementDiscoveryRequest, CementDiscoveryResult, ResearchMode
from cement_intelligence.jobs import ACTIVE_STATUSES, CementJob, CementJobStore
from cement_intelligence.queries import OBJECTIVE_GUIDANCE, OBJECTIVE_OPTIONS
from cement_intelligence.tasks import (
    enqueue_cement_capture,
    enqueue_cement_discovery,
)
from mattress_intelligence.settings import Settings

settings = Settings()
settings.ensure_directories()
job_store = CementJobStore.from_settings(settings)

STAGE_LABELS = {
    "queued": "Queued for the Celery worker",
    "initializing": "Initializing cement evidence services",
    "generating_queries": "Generating authoritative cement searches",
    "searching": "Searching public cement evidence",
    "classifying": "Classifying and scoping candidate documents",
    "grouping_plants": "Grouping exact plant evidence",
    "building_bundles": "Building complementary evidence bundles",
    "capturing": "Capturing approved evidence",
    "writing_manifests": "Validating captures and writing manifests",
    "completed": "Complete",
    "failed": "Failed",
}

MODE_LABELS = {
    "Find the best-documented plant": ResearchMode.BEST_DOCUMENTED,
    "Search by location and plant type": ResearchMode.LOCATION_SCAN,
    "I know the company or plant": ResearchMode.KNOWN_PLANT,
}

MODE_GUIDANCE = {
    "Find the best-documented plant": (
        "Use this when you have no target yet. The system searches the chosen geography "
        "and recommends the plant with the strongest public evidence trail."
    ),
    "Search by location and plant type": (
        "Use this to compare plants of one type inside a state or region. Company and "
        "plant names remain optional."
    ),
    "I know the company or plant": (
        "Use this for focused research. Enter the company, plant, or both; exact-name "
        "matches receive stronger plant-specific treatment."
    ),
}


def parse_manual_urls(value: str) -> list[str]:
    """Validate one user-submitted evidence URL per line."""

    urls: list[str] = []
    invalid: list[str] = []
    for raw_line in value.splitlines():
        url = raw_line.strip().lstrip("-•").strip()
        if not url:
            continue
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            invalid.append(raw_line.strip())
            continue
        if url not in urls:
            urls.append(url)
    if invalid:
        raise ValueError(
            "These manual entries are not complete HTTP/HTTPS URLs: "
            + ", ".join(invalid)
        )
    return urls


def broker_is_available() -> bool:
    from mattress_intelligence.celery_app import celery_app

    try:
        with celery_app.connection_for_write() as connection:
            connection.ensure_connection(max_retries=1)
        return True
    except Exception:
        return False


def worker_is_available() -> bool:
    from mattress_intelligence.celery_app import celery_app

    try:
        replies = celery_app.control.inspect(timeout=1.5).ping()
        return bool(replies)
    except Exception:
        return False


def require_worker_path() -> None:
    if not settings.celery_enabled:
        raise RuntimeError("Celery is disabled. Set CELERY_ENABLED=true in .env.")
    if not broker_is_available():
        raise RuntimeError(
            "Redis is unavailable. Start Redis before submitting."
        )
    if not worker_is_available():
        raise RuntimeError(
            "No Celery worker replied. Start or restart the worker before submitting."
        )


def load_json(path: str | None) -> dict:
    if not path:
        raise RuntimeError("The completed job has no result path.")
    source = Path(path)
    if not source.is_file():
        raise RuntimeError(f"Result file does not exist: {source}")
    value = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"Result file is not a JSON object: {source}")
    return value


def render_active_job(job: CementJob) -> None:
    label = STAGE_LABELS.get(job.stage, job.stage.replace("_", " ").title())
    with st.status(label, expanded=True, state="running"):
        st.progress(job.progress, text=f"{job.progress}% · {label}")
        if job.message:
            st.write(job.message)
        st.caption(f"Job {job.job_id} · output {job.output_dir}")
    time.sleep(2)
    st.rerun()


def render_job_failure(job: CementJob) -> None:
    st.error(job.error or "The cement evidence job failed without a recorded error.")
    st.caption(f"Job {job.job_id} · output {job.output_dir}")


def available_providers() -> list[str]:
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
    if not providers:
        providers.append("firecrawl")
    return list(dict.fromkeys(providers))


st.title("Cement Evidence Intake")
st.caption(
    "Discovery and capture are separate durable Celery jobs. BRIXTA searches first, "
    "waits for your approval, and captures only the selected evidence."
)

if not settings.celery_enabled:
    st.warning(
        "Celery is disabled. Set CELERY_ENABLED=true, start Redis and the worker, "
        "then restart Streamlit."
    )

with st.form("cement-discovery-form"):
    left, middle, right = st.columns(3)
    with left:
        mode_label = st.selectbox("Research mode", list(MODE_LABELS))
        country = st.text_input("Country", value="India")
        state = st.text_input("State or region", placeholder="Example: Rajasthan")
    with middle:
        company_name = st.text_input("Company, if known", placeholder="Optional")
        plant_name = st.text_input("Plant, if known", placeholder="Optional")
        plant_type = st.selectbox(
            "Plant type",
            (
                "integrated cement plant",
                "cement grinding unit",
                "clinker grinding unit",
                "multi-kiln integrated cement plant",
                "brownfield modernised cement plant",
            ),
        )
    with right:
        objective = st.selectbox(
            "Primary objective",
            OBJECTIVE_OPTIONS,
            help=(
                "This changes the specialised search queries used inside the discovery "
                "budget. It does not manufacture facts or restrict later manual selection."
            ),
        )
        st.caption(OBJECTIVE_GUIDANCE[objective]["purpose"])
        provider = st.selectbox("Search provider", available_providers())
        maximum_plants = st.slider("Candidate plants", 3, 15, 8)
    with st.expander("What do the research controls actually do?"):
        st.markdown("**Research mode chooses where BRIXTA searches.**")
        for label, description in MODE_GUIDANCE.items():
            st.write(f"- **{label}:** {description}")
        st.markdown("**Primary objective chooses what BRIXTA searches for.**")
        for label, guidance in OBJECTIVE_GUIDANCE.items():
            st.write(f"- **{label}:** {guidance['purpose']}")
            st.caption(
                f"Best evidence: {guidance['best_evidence']} "
                f"Expected output: {guidance['deliverable']}"
            )
        st.info(
            "The objective changes search emphasis and query order. It does not declare "
            "a document true, and it does not prevent you from manually capturing another lead."
        )
    with st.expander("Discovery limits"):
        limit_left, limit_middle, limit_right = st.columns(3)
        with limit_left:
            maximum_queries = st.slider("Search queries", 3, 12, 8)
        with limit_middle:
            results_per_query = st.slider("Results per query", 3, 20, 8)
        with limit_right:
            maximum_bundle = st.slider("Documents per proposed bundle", 3, 12, 8)
    discover = st.form_submit_button(
        "Queue cement discovery",
        type="primary",
        use_container_width=True,
    )

if discover:
    try:
        require_worker_path()
        request = CementDiscoveryRequest(
            mode=MODE_LABELS[mode_label],
            country=country,
            state=state or None,
            company_name=company_name or None,
            plant_name=plant_name or None,
            plant_type=plant_type,
            objective=objective,
            max_queries=maximum_queries,
            results_per_query=results_per_query,
            max_candidate_plants=maximum_plants,
            max_bundle_documents=maximum_bundle,
        )
        job_id = uuid4().hex
        output_dir = settings.output_dir / "cement_discovery" / job_id
        job = job_store.create(
            job_type="discovery",
            request={**request.to_dict(), "provider": provider},
            output_dir=output_dir,
            job_id=job_id,
        )
        task = enqueue_cement_discovery(
            request,
            provider=provider,
            output_dir=output_dir,
            job_id=job_id,
        )
        if task.id != job_id:
            job_store.update_task_id(job_id, str(task.id))
        st.session_state["cement_discovery_job_id"] = job.job_id
        st.rerun()
    except Exception as exc:
        st.error(f"Discovery could not be queued: {exc}")

jobs = job_store.list(limit=50)
discovery_jobs = [job for job in jobs if job.job_type == "discovery"]

if discovery_jobs:
    st.subheader("Cement discovery sessions")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Session": job.job_id[:8],
                    "Status": job.status.upper(),
                    "Stage": STAGE_LABELS.get(job.stage, job.stage),
                    "Progress": f"{job.progress}%",
                    "Submitted UTC": job.submitted_at.strftime("%Y-%m-%d %H:%M:%S"),
                }
                for job in discovery_jobs
            ]
        ),
        hide_index=True,
        use_container_width=True,
    )
    discovery_labels = {
        job.job_id: (
            f"{job.status.upper()} · {job.submitted_at.strftime('%Y-%m-%d %H:%M UTC')} "
            f"· {job.job_id[:8]}"
        )
        for job in discovery_jobs
    }
    default_job_id = st.session_state.get(
        "cement_discovery_job_id", discovery_jobs[0].job_id
    )
    if default_job_id not in discovery_labels:
        default_job_id = discovery_jobs[0].job_id
    selected_discovery_job_id = st.selectbox(
        "Open a discovery session",
        options=list(discovery_labels),
        index=list(discovery_labels).index(default_job_id),
        format_func=lambda value: discovery_labels[value],
    )
    st.session_state["cement_discovery_job_id"] = selected_discovery_job_id
    discovery_job = job_store.get(selected_discovery_job_id)
else:
    discovery_job = None

if discovery_job is None:
    st.info(
        "Start with “Find the best-documented plant” if you do not yet know a company "
        "or plant URL."
    )
    st.stop()

if discovery_job.status in ACTIVE_STATUSES:
    render_active_job(discovery_job)
if discovery_job.status == "failed":
    render_job_failure(discovery_job)
    st.stop()

discovery_payload = load_json(discovery_job.result_json_path)
result = CementDiscoveryResult.from_dict(discovery_payload)
st.caption(
    "SQL persistence: "
    f"`{discovery_job.summary.get('sql_backend', 'legacy JSON-only session')}`"
)

if result.warnings:
    for warning in result.warnings:
        st.warning(warning)

reference_documents = [
    item
    for item in result.documents
    if item.evidence_scope.value.endswith("_reference")
]
unresolved_documents = [
    item
    for item in result.documents
    if item.evidence_scope.value in {"unresolved", "irrelevant"}
]
metric_a, metric_b, metric_c, metric_d = st.columns(4)
metric_a.metric("Queries", len(result.queries))
metric_b.metric("Unique documents", len(result.documents))
metric_c.metric("Plant candidates", len(result.plants))
metric_d.metric("Reference-only", len(reference_documents))

with st.expander("Queries used"):
    for query in result.queries:
        st.code(query, language=None)

if not result.plants:
    st.error(
        "No documents passed the exact-plant admission gate. Add a company, plant, "
        "district, or state and run a narrower discovery."
    )
    st.download_button(
        "Download discovery diagnostics",
        data=json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
        file_name=f"{result.run_id}.json",
        mime="application/json",
    )
    if reference_documents or unresolved_documents:
        with st.expander("Excluded reference and unresolved documents"):
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Scope": item.evidence_scope.value,
                            "Type": item.document_type.value,
                            "Title": item.title or item.url,
                            "URL": item.url,
                        }
                        for item in [*reference_documents, *unresolved_documents]
                    ]
                ),
                hide_index=True,
                use_container_width=True,
                column_config={"URL": st.column_config.LinkColumn("URL")},
            )
    st.stop()

plant_rows = [
    {
        "Recommended": "Yes" if plant.recommended else "",
        "Company": plant.company_name,
        "Plant candidate": plant.plant_name,
        "Location": plant.location,
        "Researchability": round(plant.researchability_score * 100, 1),
        "Documents": len(plant.document_ids),
        "Coverage": len(plant.expected_coverage),
        "Missing": len(plant.missing_categories),
    }
    for plant in result.plants
]
st.subheader("Candidate plants")
st.dataframe(pd.DataFrame(plant_rows), hide_index=True, use_container_width=True)

plant_by_label = {
    (
        f"{'★ ' if plant.recommended else ''}{plant.company_name} — "
        f"{plant.plant_name} — {plant.researchability_score * 100:.1f}%"
    ): plant
    for plant in result.plants
}
selected_label = st.selectbox("Open a candidate", list(plant_by_label))
plant = plant_by_label[selected_label]

st.subheader(plant.plant_name)
summary_left, summary_right = st.columns(2)
with summary_left:
    st.markdown("**Why this candidate scored here**")
    for reason in plant.reasons:
        st.write(f"- {reason}")
with summary_right:
    st.markdown("**Expected evidence coverage**")
    st.write(", ".join(item.replace("_", " ") for item in plant.expected_coverage))
    if plant.missing_categories:
        st.markdown("**Known gaps before deep research**")
        st.write(", ".join(item.replace("_", " ") for item in plant.missing_categories))

documents_by_id = {item.candidate_id: item for item in result.documents}
candidate_documents = [
    documents_by_id[item]
    for item in plant.document_ids
    if item in documents_by_id
]
bundle_ids = set(plant.bundle.document_ids if plant.bundle else [])
st.subheader("Plant-specific evidence candidates")
st.dataframe(
    pd.DataFrame(
        [
            {
                "Proposed": item.candidate_id in bundle_ids,
                "Scope": item.evidence_scope.value,
                "Type": item.document_type.value.replace("_", " "),
                "Score": round(item.overall_score * 100, 1),
                "Title": item.title or item.url,
                "Domain": item.publisher_domain,
                "URL": item.url,
            }
            for item in candidate_documents
        ]
    ),
    hide_index=True,
    use_container_width=True,
    column_config={"URL": st.column_config.LinkColumn("URL")},
)

st.markdown("### Capture control")
st.caption(
    "BRIXTA preselects its proposed bundle. You can remove those choices, add any other "
    "discovered result, or paste a URL that the search did not find. A manual choice is "
    "recorded as your lead—not silently promoted to verified evidence."
)

label_to_id = {
    (
        f"[{item.evidence_scope.value.replace('_', ' ')}] "
        f"{item.document_type.value.replace('_', ' ').title()} · "
        f"{item.overall_score * 100:.1f} · "
        f"{(item.title or item.url)[:95]} · {item.candidate_id[-6:]}"
    ): item.candidate_id
    for item in result.documents
}
default_labels = [
    label for label, candidate_id in label_to_id.items() if candidate_id in bundle_ids
]
selected_labels = st.multiselect(
    "Choose discovered documents to capture",
    options=list(label_to_id),
    default=default_labels,
    help=(
        "The proposed plant bundle starts selected. You may also select reference, "
        "unresolved or cross-plant results when you believe they are useful leads."
    ),
)
with st.expander("Browse every discovered lead"):
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Proposed": item.candidate_id in bundle_ids,
                    "Scope": item.evidence_scope.value,
                    "Type": item.document_type.value.replace("_", " "),
                    "Score": round(item.overall_score * 100, 1),
                    "Title": item.title or item.url,
                    "Domain": item.publisher_domain,
                    "URL": item.url,
                }
                for item in result.documents
            ]
        ),
        hide_index=True,
        use_container_width=True,
        column_config={"URL": st.column_config.LinkColumn("URL")},
    )

manual_url_text = st.text_area(
    "Add manual evidence URLs",
    placeholder=(
        "Paste one complete URL per line.\n"
        "https://company.example/plant-report.pdf\n"
        "https://government.example/environmental-clearance"
    ),
    help=(
        "Use this for a lead you found yourself. BRIXTA will capture and validate it "
        "through the same provider-locked pipeline."
    ),
)
respect_robots = st.checkbox("Respect robots.txt", value=True)
locked_capture_provider = str(
    discovery_job.request.get("provider") or "services"
).casefold()
st.caption(
    f"Provider lock: `{locked_capture_provider}` will also be used for capture. "
    "Direct verified downloads remain the first choice for original PDFs."
)

capture_submit = st.button(
    "Queue selected and manual evidence capture",
    type="primary",
    use_container_width=True,
    disabled=not selected_labels and not manual_url_text.strip(),
)
if capture_submit:
    try:
        require_worker_path()
        selected_ids = [label_to_id[label] for label in selected_labels]
        manual_urls = parse_manual_urls(manual_url_text)
        capture_provider = locked_capture_provider
        capture_job_id = uuid4().hex
        capture_output = settings.output_dir / "cement_intake" / capture_job_id
        capture_request = {
            "discovery_json_path": discovery_job.result_json_path,
            "plant_candidate_id": plant.plant_candidate_id,
            "selected_document_ids": selected_ids,
            "manual_urls": manual_urls,
            "respect_robots_txt": respect_robots,
            "capture_provider": capture_provider,
        }
        capture_job = job_store.create(
            job_type="capture",
            request=capture_request,
            output_dir=capture_output,
            parent_job_id=discovery_job.job_id,
            job_id=capture_job_id,
        )
        task = enqueue_cement_capture(
            discovery_json_path=str(discovery_job.result_json_path),
            plant_candidate_id=plant.plant_candidate_id,
            selected_document_ids=selected_ids,
            respect_robots_txt=respect_robots,
            capture_provider=capture_provider,
            output_dir=capture_output,
            job_id=capture_job_id,
            manual_urls=manual_urls,
        )
        if task.id != capture_job_id:
            job_store.update_task_id(capture_job_id, str(task.id))
        st.session_state["cement_capture_job_id"] = capture_job.job_id
        st.rerun()
    except Exception as exc:
        st.error(f"Capture could not be queued: {exc}")

capture_jobs = [
    job
    for job in jobs
    if job.job_type == "capture" and job.parent_job_id == discovery_job.job_id
]
if capture_jobs:
    capture_labels = {
        job.job_id: (
            f"{job.status.upper()} · {job.submitted_at.strftime('%Y-%m-%d %H:%M UTC')} "
            f"· {job.job_id[:8]}"
        )
        for job in capture_jobs
    }
    selected_capture_id = st.selectbox(
        "Open a capture session",
        options=list(capture_labels),
        format_func=lambda value: capture_labels[value],
    )
    capture_job = job_store.get(selected_capture_id)
    if capture_job.status in ACTIVE_STATUSES:
        render_active_job(capture_job)
    if capture_job.status == "failed":
        render_job_failure(capture_job)
    elif capture_job.status == "completed":
        intake_payload = load_json(capture_job.result_json_path)
        st.subheader("Capture quality")
        st.caption(
            "SQL persistence: "
            f"`{capture_job.summary.get('sql_backend', 'legacy JSON-only session')}`"
        )
        captured = intake_payload.get("captured") or []
        status_counts: dict[str, int] = {}
        for item in captured:
            status = str(item.get("status") or "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1
        columns = st.columns(max(1, len(status_counts)))
        for column, (status, count) in zip(columns, sorted(status_counts.items())):
            column.metric(status.replace("_", " ").title(), count)
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Status": item.get("status"),
                        "URL": item.get("url"),
                        "Capture": item.get("capture_method"),
                        "Content type": item.get("content_type"),
                        "Preview characters": item.get("text_characters"),
                        "Preview truncated": item.get("text_preview_truncated"),
                        "Original binary": item.get("original_binary_preserved"),
                        "Validation": "; ".join(item.get("validation_notes") or []),
                        "Artifact": item.get("artifact_path") or item.get("object_uri"),
                        "Error": item.get("error"),
                    }
                    for item in captured
                ]
            ),
            hide_index=True,
            use_container_width=True,
            column_config={"URL": st.column_config.LinkColumn("URL")},
        )
        st.code(capture_job.output_dir)
        st.download_button(
            "Download intake manifest",
            data=json.dumps(intake_payload, indent=2, ensure_ascii=False),
            file_name=Path(capture_job.result_json_path or "intake_manifest.json").name,
            mime="application/json",
        )

if reference_documents:
    with st.expander("Reference corpus candidates excluded from this plant bundle"):
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Scope": item.evidence_scope.value,
                        "Type": item.document_type.value,
                        "Title": item.title or item.url,
                        "URL": item.url,
                    }
                    for item in reference_documents
                ]
            ),
            hide_index=True,
            use_container_width=True,
            column_config={"URL": st.column_config.LinkColumn("URL")},
        )
