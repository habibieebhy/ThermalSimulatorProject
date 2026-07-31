"""Workflow landing page for BRIXTA Evidence Discovery."""

from __future__ import annotations

import streamlit as st

st.title("BRIXTA Evidence Discovery")
st.caption(
    "Guided public-evidence discovery • approval-gated capture • portable RAG bundles"
)
st.info(
    "Choose a specialised mattress or cement workflow, or start an Open Research "
    "Campaign from any topic, URL or domain. Discovery remains separate from "
    "capture, and every accepted or rejected direction is recorded."
)

mode_a, mode_b, mode_c = st.columns(3)
with mode_a:
    st.subheader("Mattress intelligence")
    st.write(
        "Catalogue, product, layer, material, image and trademark evidence with "
        "deterministic configuration analysis."
    )
with mode_b:
    st.subheader("Cement evidence")
    st.write(
        "Plant-focused discovery, evidence bundles and approval-gated capture "
        "for industrial reconstruction research."
    )
with mode_c:
    st.subheader("Open research")
    st.write(
        "Domain-neutral campaigns with manual seeds, approved directions, "
        "crawl budgets, route explanations and BRIXTA-ready ZIP exports."
    )

st.subheader("Mattress CLI example")
st.code(
    '''mattress-lab collect \\
  --company "The Sleep Company" \\
  --domain "https://thesleepcompany.in" \\
  --market "India" \\
  --max-pages 100 --max-external-pages 25 --max-depth 4 \\
  --llm openai --search-provider services \\
  --search --external --enqueue \\
  --output outputs/first_collection.xlsx''',
    language="bash",
)

st.subheader("Shared evidence pipeline")
st.write(
    "Research brief → provider search and bounded site exploration → visible route "
    "ledger → human approval → verified capture → original artifacts and extracted "
    "text → SQL/MinIO where supported → portable evidence output."
)
