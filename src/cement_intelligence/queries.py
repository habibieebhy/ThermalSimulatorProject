"""Deterministic query packs for cement-plant evidence discovery."""

from __future__ import annotations

from .models import CementDiscoveryRequest, ResearchMode


OBJECTIVE_GUIDANCE: dict[str, dict[str, str]] = {
    "quarry-to-dispatch reconstruction": {
        "purpose": (
            "Reconstruct the complete material and process flow from limestone extraction "
            "through crushing, raw grinding, pyroprocessing, cement grinding, storage, "
            "packing and dispatch."
        ),
        "best_evidence": (
            "EIA/EMP reports, pre-feasibility reports, plant layouts, process-flow "
            "descriptions and expansion documents."
        ),
        "deliverable": (
            "A chronological plant map showing what enters each stage, the major equipment "
            "used and where the material moves next."
        ),
    },
    "equipment and process-route reconstruction": {
        "purpose": (
            "Identify the plant's actual production route and major machinery: crushers, "
            "mills, preheaters, calciners, kilns, coolers, separators, silos and packers."
        ),
        "best_evidence": (
            "OEM case studies, tenders, technical specifications, EIA/EMP reports and "
            "modernisation announcements."
        ),
        "deliverable": (
            "An evidence-backed equipment register and process sequence, with unknown "
            "machines left explicitly unresolved."
        ),
    },
    "energy and capacity benchmarking": {
        "purpose": (
            "Compare production capacity, electrical and thermal energy intensity, fuel "
            "mix, waste-heat recovery and major energy consumers."
        ),
        "best_evidence": (
            "BEE PAT records, energy audits, sustainability reports, compliance reports "
            "and capacity filings."
        ),
        "deliverable": (
            "A benchmark sheet for MTPA/TPD capacity, kWh per tonne, thermal consumption, "
            "fuel use and WHR where the evidence provides them."
        ),
    },
    "packing, loading, weighbridge and dispatch reconstruction": {
        "purpose": (
            "Focus on finished-cement storage and outbound logistics: silos, packers, bulk "
            "loading, bag loading, truck/rail movement, weighbridges and dispatch controls."
        ),
        "best_evidence": (
            "Plant layouts, logistics tenders, equipment specifications, EIA traffic "
            "sections and packing-plant case studies."
        ),
        "deliverable": (
            "A dispatch-side flow from cement silo to the gate, including the equipment "
            "and logistics nodes that can be verified."
        ),
    },
    "cement formulation and plant-compatibility preparation": {
        "purpose": (
            "Collect evidence about clinker, gypsum, fly ash, slag and other additions, "
            "plus grinding, blending and storage capabilities relevant to OPC/PPC/PSC."
        ),
        "best_evidence": (
            "Product declarations, standards, raw-material sections, product-mix filings, "
            "patents and grinding-system documentation."
        ),
        "deliverable": (
            "A preparation dataset for later formulation and compatibility analysis; it "
            "does not invent a cement recipe from missing evidence."
        ),
    },
}

OBJECTIVE_OPTIONS = tuple(OBJECTIVE_GUIDANCE)


def _quoted(value: str | None) -> str:
    cleaned = " ".join((value or "").split())
    return f'"{cleaned}"' if cleaned else ""


def build_discovery_queries(request: CementDiscoveryRequest) -> list[str]:
    """Build complementary searches instead of relying on one broad query."""

    company = _quoted(request.company_name)
    plant = _quoted(request.plant_name)
    state = " ".join((request.state or "").split())
    geography = " ".join(part for part in (state, request.country.strip()) if part)
    plant_type = request.plant_type.strip() or "cement plant"
    known_target = " ".join(part for part in (company, plant) if part)

    if request.mode == ResearchMode.KNOWN_PLANT:
        subject = known_target
        anchor_queries = [
            f"{subject} EIA EMP cement filetype:pdf",
            f"{subject} environmental clearance clinker cement",
            f"{subject} pre-feasibility report cement filetype:pdf",
        ]
        supporting_queries = [
            f"{subject} EAC meeting minutes expansion cement",
            f"{subject} certified compliance report cement",
            f"{subject} annual report sustainability energy WHRS",
            f'site:parivesh.nic.in {subject} cement',
            f'site:environmentclearance.nic.in {subject} cement',
        ]
    else:
        subject = f'"{plant_type}" {geography}'.strip()
        anchor_queries = [
            f'{subject} "EIA/EMP" filetype:pdf',
            f'{subject} "environmental clearance" clinker capacity',
            f'{subject} "pre-feasibility report" filetype:pdf',
        ]
        supporting_queries = [
            f'{subject} "compliance report" cement filetype:pdf',
            f'site:parivesh.nic.in cement clinker "{state or request.country}"',
            f'site:environmentclearance.nic.in cement "{state or request.country}" EIA',
            f'{subject} annual report sustainability WHRS alternative fuel',
            f'{subject} public hearing EAC minutes expansion',
        ]

    objective_tokens = request.objective.casefold()
    target = known_target or subject
    if "quarry-to-dispatch" in objective_tokens:
        objective_queries = [
            f"{target} quarry crusher raw mill kiln cement mill packing dispatch filetype:pdf",
            f"{target} process flow plant layout material handling weighbridge",
        ]
    elif "equipment" in objective_tokens:
        objective_queries = [
            f"{target} kiln preheater calciner cooler raw mill cement mill equipment",
            f"{target} OEM case study tender technical specification Loesche KHD Pfeiffer",
        ]
    elif "energy" in objective_tokens:
        objective_queries = [
            f"{target} specific electrical thermal energy consumption PAT WHRS",
            f"{target} energy audit fuel mix alternative fuel capacity utilisation",
        ]
    elif "packing" in objective_tokens:
        objective_queries = [
            f"{target} cement silo packer truck loading rail loading weighbridge dispatch",
            f"{target} packing plant logistics tender bulk loading filetype:pdf",
        ]
    elif "formulation" in objective_tokens:
        objective_queries = [
            f"{target} OPC PPC PSC product mix clinker gypsum fly ash slag",
            f"{target} cement grinding blending additives storage product specification",
        ]
    else:
        objective_queries = []

    # Keep objective-specific searches inside the query budget. Previously they were
    # appended after ten generic queries and were silently cut off by common limits.
    built = [
        *anchor_queries[:2],
        *objective_queries,
        *anchor_queries[2:],
        *supporting_queries,
    ]
    return list(dict.fromkeys(" ".join(query.split()) for query in built))[
        : request.max_queries
    ]
