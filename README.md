# BRIXTA Research and Engineering Intelligence

BRIXTA is an evidence-acquisition, research, deterministic-analysis, and
simulation workspace. The repository currently supports:

1. mattress product and construction intelligence;
2. an interactive mattress thermal prototype simulator; and
3. a cement-plant evidence discovery and capture system under active
   development.

The repository is not yet a complete cement digital twin, OpenFOAM generator,
or autonomous engineering-design system. The cement branch currently builds the
evidence foundation required for those later layers.

Repository:
`https://github.com/habibieebhy/ThermalSimulatorProject`

Current package version: `1.6.1`

Recommended Python version: Python 3.12

---

## 1. Branches

### `main`

`main` is the mattress-intelligence baseline. It contains:

- public web and document discovery;
- official-site crawling and external evidence capture;
- PDF, HTML, image, OCR, and vision evidence acquisition;
- mattress product, variant, layer, and trademark-material extraction;
- deterministic entity resolution;
- a product knowledge graph;
- TF-IDF/cosine similarity;
- constraint-based configuration generation;
- Bayesian candidate ranking and confidence;
- SQLite or PostgreSQL/Neon persistence;
- local artifacts or MinIO object storage;
- Celery/Redis background jobs;
- Streamlit, CLI, and FastAPI interfaces;
- Excel, CSV, JSON, and graphical outputs; and
- the interactive five-prototype mattress thermal simulator.

### `feature/cement-digital-twin`

The cement branch is currently one commit ahead of `main`. It inherits the
complete mattress and thermal system and adds:

- cement evidence discovery modes;
- cement-specific deterministic search-query packs;
- five research objectives;
- document classification and evidence scoping;
- plant-candidate grouping and ranking;
- complementary evidence-bundle proposals;
- human approval and manual URL submission;
- provider-locked evidence capture;
- durable Celery discovery and capture jobs;
- cement discovery/intake SQL tables;
- content-addressed document preservation;
- extracted-text files and capture manifests; and
- a read-only Streamlit database explorer.

The cement implementation lives in a separate Python package:

```text
src/cement_intelligence/
```

It reuses shared infrastructure from:

```text
src/mattress_intelligence/
```

The shared Celery application still has the historical
`mattress_intelligence.celery_app` name, but it loads tasks from both packages.
This is package-naming debt, not a claim that cement research is mattress
research.

### Switching branches

```bash
git switch main
```

```bash
git switch feature/cement-digital-twin
```

To inspect the branch difference:

```bash
git diff --stat main..feature/cement-digital-twin
git log --oneline --decorate main..feature/cement-digital-twin
```

---

## 2. System Objective

BRIXTA is designed to turn scattered public evidence into traceable,
machine-readable engineering intelligence.

The reusable mental model is:

```text
Question
  ↓
Search
  ↓
Candidate sources
  ↓
Capture and preserve evidence
  ↓
Extract explicit facts
  ↓
Resolve entities
  ↓
Connect facts into a knowledge graph
  ↓
Apply constraints, similarity, and probabilistic inference
  ↓
Generate configurations, comparisons, and simulations
```

The critical design rule is:

> LLMs may discover, classify, and transcribe explicit evidence. Deterministic
> algorithms—not the LLM—produce engineering conclusions, candidate
> configurations, graph relationships, and confidence scores.

This rule is already applied in the mattress pipeline. The cement branch has
implemented discovery and capture; cement-specific structured extraction and
inference remain future work.

---

## 3. High-Level Architecture

```text
┌─────────────────────────────────────────────────────────────────────┐
│ User interfaces                                                     │
│ Streamlit dashboard │ CLI │ FastAPI                                 │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│ Job control                                                         │
│ Celery tasks │ Redis broker/result backend │ durable SQLite ledger │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│ Discovery                                                           │
│ Firecrawl │ Jina │ Tavily │ official sitemap │ manual seed URLs    │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│ Acquisition                                                         │
│ verified HTTP │ browser/Playwright │ TLS contexts │ system curl    │
│ Firecrawl/Jina fallback │ PDF/HTML/image preservation              │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                ┌──────────────┴──────────────┐
                │                             │
┌───────────────▼────────────────┐ ┌──────────▼──────────────────────┐
│ Raw evidence storage           │ │ Structured persistence          │
│ local artifacts or MinIO       │ │ SQLite or PostgreSQL/Neon       │
│ content hashes and manifests   │ │ research and capture tables     │
└───────────────┬────────────────┘ └──────────┬──────────────────────┘
                │                             │
┌───────────────▼─────────────────────────────▼──────────────────────┐
│ Domain intelligence                                                │
│ Mattress: extraction → graph → similarity → constraints → Bayes   │
│ Cement today: discovery → approval → capture → validation          │
│ Cement next: OCR/NER → fact graph → confidence → digital twin      │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│ Outputs                                                             │
│ Excel │ CSV │ JSON │ SQL │ artifacts │ dashboards │ simulations   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 4. Repository Structure

```text
app.py
pages/
  0_Home.py
  1_Evidence_Collection_Lab.py
  2_Thermal_Prototype_Lab.py
  3_Cement_Evidence_Intake.py
  4_Database_Explorer.py

src/
  mattress_intelligence/
    api.py
    assets.py
    celery_app.py
    cli.py
    configurations.py
    crawler.py
    entities.py
    exporter.py
    extraction.py
    firecrawl.py
    graph.py
    inference.py
    jina.py
    jobs.py
    llm.py
    material_decoder.py
    materials.py
    models.py
    network.py
    normalization.py
    object_store.py
    pipeline.py
    search.py
    settings.py
    similarity.py
    storage.py
    streamlit_app.py
    tasks.py
    thermal.py

  mattress_thermal/
    simulation.py
    cli.py

  cement_intelligence/
    models.py
    queries.py
    discovery.py
    intake.py
    jobs.py
    storage.py
    tasks.py

tests/
data/
outputs/
artifacts/
compose.yaml
Dockerfile
pyproject.toml
.env
```

### Important files

| File | Responsibility |
| --- | --- |
| `app.py` | Streamlit entry point |
| `settings.py` | Environment-backed runtime configuration |
| `celery_app.py` | Shared Celery application and Redis configuration |
| `crawler.py` | Direct/browser acquisition, TLS handling, and service fallback |
| `object_store.py` | Local artifact cache and optional MinIO mirroring |
| `pipeline.py` | Mattress end-to-end orchestration |
| `assets.py` | Image/PDF asset processing, OCR, and selected vision transcription |
| `storage.py` | SQLite/PostgreSQL repositories |
| `cement_intelligence/discovery.py` | Cement result classification, scoring, grouping, and bundles |
| `cement_intelligence/intake.py` | Approved cement evidence download and validation |
| `cement_intelligence/jobs.py` | Durable cement discovery/capture job ledger |
| `pages/4_Database_Explorer.py` | Live read-only SQL and job inspection |

---

## 5. Mattress Intelligence Data Flow (`main`)

### 5.1 Input

The user supplies some combination of:

- company name;
- official domain;
- market;
- brand aliases;
- seed URLs;
- search grounding;
- external-evidence permission;
- crawl depth and page limits;
- asset and vision limits; and
- provider selections.

### 5.2 Discovery

Configured providers generate candidate URLs. Depending on configuration, the
system may use:

- Jina Search;
- Firecrawl Search;
- Tavily;
- official-domain sitemap discovery;
- seed URLs; and
- deterministic search phrases.

Search results are leads, not facts.

### 5.3 Capture

The crawler:

1. normalizes and deduplicates URLs;
2. observes crawl depth and configured page limits;
3. optionally respects `robots.txt`;
4. attempts direct or browser-backed retrieval;
5. uses verified TLS certificate contexts;
6. can use the system `curl` command as a final transport fallback;
7. uses configured service capture when appropriate;
8. records HTTP and provider failures; and
9. saves content-addressed artifacts.

### 5.4 Document and asset processing

The mattress path can process:

- HTML;
- embedded JSON and JSON-LD;
- browser network JSON;
- PDF documents;
- catalogue pages;
- product images;
- diagrams;
- tables; and
- labels visible in images.

For PDFs and images:

1. preserve the original artifact;
2. extract native text;
3. render bounded PDF pages;
4. run local Tesseract OCR first;
5. send only selected high-value assets to the configured vision-capable LLM;
6. retain source references; and
7. convert explicit visible claims into atomic observations.

### 5.5 LLM boundary

OpenAI and Gemini adapters exist. The configured provider can assist with:

- query generation or evidence discovery;
- page/product classification;
- exact transcription of visible labels;
- identifying explicitly shown product names;
- reading explicitly shown layer order; and
- normalizing explicit evidence.

The LLM is not supposed to:

- invent foam densities;
- invent missing layers;
- generate posterior probabilities;
- decide graph truth;
- bypass constraints;
- manufacture confidence; or
- produce final engineering conclusions without evidence.

### 5.6 Deterministic analysis

The mattress pipeline applies:

- entity resolution for repeated products and variants;
- a material/trademark evidence decoder;
- a knowledge graph;
- TF-IDF and cosine similarity;
- thickness and construction constraints;
- OR-Tools CP-SAT when installed;
- a Python enumerator fallback;
- Bayesian candidate ranking;
- confidence scoring; and
- candidate configuration generation.

### 5.7 Outputs

Typical outputs include:

- `complete_research.xlsx`;
- displayed-products CSV and JSON;
- trademark-material CSV, JSON, and Excel;
- complete research-result JSON;
- raw source and asset artifacts; and
- SQL records.

The Excel workbook can contain:

- Products;
- Variants;
- Layers;
- Assets;
- Evidence Observations;
- Sources;
- Similar Products;
- Configurations;
- Graph Edges;
- Review Queue;
- Crawl and acquisition logs; and
- Run Metadata.

---

## 6. Mattress Thermal Prototype Lab

The thermal simulator is an interactive, object-oriented NumPy/Matplotlib
lumped-parameter model exposed through Streamlit.

It compares:

1. Aero-Natural PCM;
2. Eco-Battery radiator;
3. Core-Chiller Peltier controller;
4. Hyper-Conductive graphite; and
5. Dual-Zone Smart Mesh.

At each physics step:

```text
thermal capacity C = mass × specific heat
conductive heat q = conductivity × area × temperature difference / path length
temperature change = net heat flow × time step / thermal capacity
```

It reports:

- mattress-interface temperature;
- real-time electrical power;
- cumulative watt-hours;
- time inside the comfort zone;
- final stabilized temperature;
- animated graph formation;
- CSV data; and
- an investor dashboard.

This is a transparent comparative prototype model. It is not finite-element
analysis, CFD, a medical device calculation, or a certification model.

---

## 7. Cement Evidence System Data Flow

The cement branch currently implements:

```text
research question
→ deterministic query pack
→ web search
→ candidate-document classification
→ evidence scope
→ plant grouping
→ researchability ranking
→ proposed evidence bundle
→ human approval/manual URLs
→ capture
→ artifact + extracted text
→ capture validation
→ manifests + SQL
```

### 7.1 Research modes

#### Find the best-documented plant

Use when no company or plant is known. BRIXTA searches the geography and
recommends the plant with the strongest public evidence trail.

#### Search by location and plant type

Use to compare plants of a selected type within a state, region, or country.

#### I know the company or plant

Use for focused research. Exact company/plant terms receive stronger
plant-specific treatment.

### 7.2 Primary objectives

#### Quarry-to-dispatch reconstruction

Searches for the complete material flow:

```text
quarry → crusher → raw mill → preheater/calciner → kiln → cooler
→ clinker storage → cement mill → silo → packing → weighbridge → dispatch
```

#### Equipment and process-route reconstruction

Searches for:

- equipment type;
- manufacturer/OEM;
- number of units;
- equipment capacity;
- process sequence;
- technical specifications; and
- modernization history.

#### Energy and capacity benchmarking

Searches for:

- clinker/cement TPD and MTPA;
- electrical kWh per tonne;
- thermal energy per kg of clinker;
- fuel mix;
- BEE PAT data;
- waste-heat recovery;
- captive/renewable power; and
- utilization.

#### Packing, loading, weighbridge and dispatch reconstruction

Searches for:

- finished-cement silos;
- packers and packing rates;
- bulk loading;
- bag loading;
- truck and rail handling;
- weighbridges; and
- dispatch bottlenecks.

#### Cement formulation and plant-compatibility preparation

Searches for:

- OPC/PPC/PSC product mix;
- clinker, gypsum, fly ash, slag, and limestone;
- blending and additive feeding;
- grinding arrangements;
- separator capability; and
- product-specific storage.

The objective changes specialized search phrases and their order inside the
query budget. It does not declare evidence true and does not generate a cement
recipe.

### 7.3 Cement document types

Candidates can be classified as:

- EIA/EMP;
- pre-feasibility report;
- environmental clearance;
- terms of reference;
- EAC minutes;
- compliance report;
- public hearing;
- annual/company report;
- sustainability report;
- BEE PAT;
- OEM technical document;
- tender;
- patent;
- academic source;
- technical reference;
- news; or
- other.

### 7.4 Evidence scopes

Every candidate receives a scope:

- `exact_plant`;
- `exact_company`;
- `cement_sector_reference`;
- `equipment_reference`;
- `regulatory_reference`;
- `unresolved`; or
- `irrelevant`.

Scope is an admission/routing signal. It is not proof.

### 7.5 Candidate scoring

The discovery engine considers:

- publisher authority;
- technical value;
- plant specificity;
- crawlability;
- document type;
- likely evidence coverage;
- presence of an anchor document;
- diversity of document types; and
- corroboration across publisher domains.

The researchability percentage answers:

> How likely is this candidate plant to be reconstructable from public
> evidence?

It does not answer:

- whether the plant is efficient;
- whether every inferred name is correct;
- whether the plant is a good investment; or
- whether the document has already been downloaded.

### 7.6 Eleven cement evidence categories

The bundle builder tries to cover:

1. identity and location;
2. capacity;
3. process route;
4. equipment;
5. materials;
6. energy;
7. storage;
8. environment;
9. packing and dispatch;
10. product mix; and
11. expansion history.

`Coverage` means the selected document types are expected to discuss those
categories. It does not mean the system has already extracted verified facts.

### 7.7 Human approval

BRIXTA proposes a complementary document bundle, but the user remains in
control.

The user can:

- remove a recommended document;
- select any discovered lead;
- include reference or unresolved documents;
- paste complete manual URLs; and
- combine algorithmic and manual choices.

A manual lead is recorded as user-submitted. It is not silently converted into
verified evidence.

### 7.8 Capture behavior

For every approved URL, capture attempts:

1. direct verified retrieval;
2. original PDF/HTML preservation where available;
3. configured provider fallback;
4. text extraction;
5. content hashing;
6. capture-quality validation;
7. manifest creation; and
8. SQL persistence.

Even when Firecrawl is selected, a direct verified download remains the first
choice for an original PDF.

### 7.9 Capture statuses

| Status | Meaning |
| --- | --- |
| `captured_usable` | Sufficient readable evidence was captured |
| `captured_unreadable` | Original PDF exists but native text is insufficient; OCR is required |
| `captured_thin` | Too little readable text was returned |
| `captured_wrong_content` | A PDF was expected but HTML/error content was returned |
| `failed` | No usable artifact was acquired |

### 7.10 Cement output files

Discovery:

```text
outputs/cement_discovery/<job-id>/
  cement_discovery_result.json
```

Capture:

```text
outputs/cement_intake/<job-id>/
  discovery_snapshot.json
  approved_bundle.json
  intake_manifest.json
  extracted_text/
    <candidate-id>.txt
```

Raw artifacts:

```text
artifacts/documents/<hash-prefix>/<content-hash>.<extension>
```

---

## 8. Persistence

### 8.1 Local development

When these are empty:

```dotenv
DATABASE_URL=
DATABASE_DIRECT_URL=
```

the project uses SQLite:

```text
data/mattress_intelligence.sqlite3
data/research_jobs.sqlite3
```

SQLite runs in WAL mode where configured, allowing the read-only database
explorer to refresh while Celery writes.

### 8.2 Hosted database

When `DATABASE_URL` is set, the repositories use PostgreSQL through Psycopg.
`DATABASE_DIRECT_URL` can be used for schema work when the main URL points to a
pooler.

Typical hosted targets include Neon, Supabase, Railway, or self-hosted
PostgreSQL.

Psycopg is dormant during normal SQLite-only local operation.

### 8.3 Mattress tables

```text
research_runs
sources
assets
products
claims
observations
configurations
graph_edges
```

### 8.4 Cement tables

```text
cement_discovery_runs
cement_documents
cement_plants
cement_plant_documents
cement_intakes
cement_captures
```

### 8.5 Job ledger

The job database tracks:

- job ID and parent job;
- discovery or capture type;
- request payload;
- Celery task ID;
- stage and percentage;
- timestamps;
- output directory;
- result path;
- summary;
- error; and
- duplicate-execution protection.

### 8.6 Artifacts

Without MinIO, raw evidence is stored in:

```text
artifacts/
```

When MinIO is configured, content is mirrored to S3-compatible object storage
while a local cache is retained when available.

Large binaries belong in artifact/object storage. Structured facts, metadata,
paths, hashes, and bounded extracted text belong in SQL.

---

## 9. Celery and Redis

Redis provides:

- the Celery task broker; and
- the Celery result backend.

Celery executes:

- long mattress collection/research jobs;
- cement discovery jobs; and
- cement capture jobs.

Tasks use:

- JSON serialization;
- late acknowledgement;
- worker-loss rejection;
- one-message prefetch;
- tracked progress;
- hard and soft time limits;
- visibility-timeout protection; and
- durable job-ledger state.

### macOS worker

Native OCR/browser/scientific libraries may crash under Celery's prefork pool
on macOS. Use the solo pool:

```bash
celery -A mattress_intelligence.celery_app:celery_app worker \
  -P solo \
  -c 1 \
  -l INFO
```

### Linux/Docker worker

The default prefork worker is appropriate inside Linux containers:

```bash
celery -A mattress_intelligence.celery_app:celery_app worker \
  --loglevel=INFO \
  --concurrency=2
```

Celery is not a database. Redis is not the permanent research store. Completed
research belongs in SQL, output files, and artifact storage.

---

## 10. Local Installation

```bash
git clone https://github.com/habibieebhy/ThermalSimulatorProject.git
cd ThermalSimulatorProject
git switch feature/cement-digital-twin
```

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
python -m pip install -e . --no-deps
```

Optional browser support:

```bash
python -m pip install -e ".[crawl]"
python -m playwright install chromium
```

Local OCR:

```bash
brew install tesseract
```

Create environment configuration:

```bash
cp .env.example .env
chmod 600 .env
```

Never commit `.env` or API keys.

---

## 11. Minimal Local Configuration

```dotenv
CELERY_ENABLED=true
CELERY_ALWAYS_EAGER=false
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1

DATABASE_URL=
DATABASE_DIRECT_URL=

MATTRESS_INTEL_DATABASE_PATH=./data/mattress_intelligence.sqlite3
MATTRESS_INTEL_JOB_DATABASE_PATH=./data/research_jobs.sqlite3
MATTRESS_INTEL_OUTPUT_DIR=./outputs
MATTRESS_INTEL_ARTIFACT_DIR=./artifacts

MATTRESS_INTEL_SEARCH_PROVIDER=services
MATTRESS_INTEL_CAPTURE_STRATEGY=services_first

FIRECRAWL_API_KEY=
JINA_API_KEY=
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5-nano
```

Only configure services you intend to use. Blank database URLs intentionally
select local SQLite.

---

## 12. Starting the Full Local System

### Terminal 1: Redis

```bash
redis-cli ping
```

Expected:

```text
PONG
```

If required:

```bash
brew services start redis
```

### Terminal 2: Celery on macOS

```bash
cd /Users/zaheerabbas/mattress-thermal-prototype
source .venv/bin/activate
celery -A mattress_intelligence.celery_app:celery_app worker \
  -P solo \
  -c 1 \
  -l INFO
```

Wait for:

```text
celery@<machine> ready.
```

### Terminal 3: Streamlit

```bash
cd /Users/zaheerabbas/mattress-thermal-prototype
source .venv/bin/activate
streamlit run app.py
```

Open:

```text
http://localhost:8501
```

### Terminal 4: prevent sleep during long local jobs

```bash
caffeinate -i
```

The screen can still be locked. Keep the terminal open.

### Optional FastAPI

```bash
uvicorn mattress_intelligence.api:app \
  --host 0.0.0.0 \
  --port 8000 \
  --reload
```

---

## 13. Docker Compose

The compose stack includes:

- Redis;
- MinIO;
- MinIO bucket initialization;
- Streamlit UI;
- FastAPI;
- Celery worker; and
- persistent Redis/MinIO volumes.

Start:

```bash
docker compose up --build
```

Services:

```text
Streamlit: http://localhost:8501
FastAPI:   http://localhost:8000
MinIO:     http://localhost:9000
Console:   http://localhost:9001
```

---

## 14. Database Explorer

The cement branch adds a read-only database page.

It can display:

- current backend;
- table inventory and row counts;
- mattress and cement research tables;
- complete selected rows;
- downloadable visible CSV rows;
- the Celery job ledger;
- job stages, errors, and output locations; and
- idempotent backfill of existing cement JSON sessions.

The explorer does not edit or delete research rows.

---

## 15. What Is Used Today

### Actively used

- Python 3.11–3.13, with 3.12 recommended;
- Streamlit;
- NumPy, Pandas, Matplotlib;
- Celery and Redis;
- SQLite locally;
- optional PostgreSQL/Neon through Psycopg;
- local content-addressed artifacts;
- optional MinIO;
- Firecrawl, Jina, and Tavily integrations when configured;
- verified HTTP and optional Playwright;
- pypdf and PyMuPDF;
- local Tesseract OCR in the mattress asset pipeline;
- optional OpenAI/Gemini transcription;
- scikit-learn TF-IDF/cosine similarity;
- optional OR-Tools CP-SAT;
- Excel, CSV, and JSON export; and
- deterministic cement document scoring and bundling.

### Present but optional

- FastAPI;
- Docker Compose;
- Playwright;
- MinIO;
- PostgreSQL/Neon;
- Gemini;
- Tavily;
- OR-Tools;
- hosted deployment.

### Not implemented yet for cement

- automatic OCR routing for scanned cement PDFs;
- cement equipment/material/capacity NER;
- unit normalization and engineering ontology;
- plant-level fact deduplication;
- evidence contradiction resolution;
- cement knowledge graph traversal;
- Bayesian estimation of missing plant parameters;
- plant-specific constraint solving;
- a quarry-to-dispatch digital twin;
- a cement process simulator;
- OpenFOAM case generation or execution;
- automatic C++ code modification;
- sensor/SCADA ingestion;
- live plant calibration;
- full hosted authentication/RBAC;
- multi-tenant production isolation; and
- certification-grade engineering calculations.

---

## 16. Honest Cement Maturity

Implemented:

```text
search → classify → scope → rank → bundle → human approve
→ download → validate → preserve → SQL
```

Not implemented:

```text
captured evidence → structured plant facts → equipment graph
→ calibrated digital twin → engineering simulation
```

Therefore:

- a downloaded PDF is not yet a reconstructed factory;
- expected category coverage is not extracted fact coverage;
- a researchability score is not confidence in every fact;
- a manual URL is not verified merely because the user selected it; and
- a capture marked `captured_usable` means readable evidence exists, not that
  every statement inside it is true.

---

## 17. Recommended Cement Roadmap

### Stage 1: evidence acquisition — implemented

- discovery;
- ranking;
- human approval;
- capture;
- artifacts;
- extracted text;
- SQL;
- manifests.

### Stage 2: document intelligence

- OCR scanned PDFs;
- page/table/layout extraction;
- cement-domain NER;
- equipment, capacity, energy, material, storage, and dispatch schemas;
- units and aliases;
- citations down to page/section.

### Stage 3: evidence reasoning

- canonical plant and equipment identities;
- contradiction tracking;
- confidence levels;
- knowledge graph;
- similarity across plants;
- constraint validation;
- Bayesian inference for missing parameters.

### Stage 4: digital-twin assembly

- quarry-to-dispatch process graph;
- equipment nodes and connecting streams;
- material and energy balances;
- capacity constraints;
- unknown-parameter registry;
- calibration against public/observed data.

### Stage 5: simulation integrations

- low-code scenario editor;
- generated configuration files;
- OpenFOAM case templates where CFD is genuinely appropriate;
- process/energy models where CFD is unnecessary;
- queued remote execution;
- result ingestion, plots, comparison, and provenance.

---

## 18. Testing

Run everything:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

Cement-focused tests:

```bash
PYTHONPATH=src python -m unittest \
  tests.test_cement_discovery \
  tests.test_cement_intake \
  tests.test_cement_jobs \
  tests.test_cement_storage \
  -v
```

Compile critical cement modules:

```bash
python -m py_compile \
  src/cement_intelligence/models.py \
  src/cement_intelligence/queries.py \
  src/cement_intelligence/discovery.py \
  src/cement_intelligence/intake.py \
  src/cement_intelligence/storage.py \
  src/cement_intelligence/tasks.py
```

---

## 19. Troubleshooting

### Celery worker crashes with `SIGSEGV` on macOS

Use:

```bash
celery -A mattress_intelligence.celery_app:celery_app worker \
  -P solo -c 1 -l INFO
```

### Redis is unavailable

```bash
redis-cli ping
brew services start redis
```

### Pylance reports a missing dataclass parameter

Confirm the active interpreter and model file:

```bash
python - <<'PY'
import inspect
import sys
import cement_intelligence.models as models
from cement_intelligence.models import CapturedEvidence

print(sys.executable)
print(models.__file__)
print(inspect.signature(CapturedEvidence))
print(list(CapturedEvidence.__dataclass_fields__))
PY
```

Then:

```bash
python -m pip install -e .
```

Select `.venv/bin/python` in VS Code and reload the window.

### Certificate verification fails but system `curl` works

The crawler attempts verified Python TLS contexts before using system `curl` as
a final direct-download fallback. Do not disable TLS verification globally.

### Expected PDF returns HTML

The source may be a redirect wrapper, access-denied page, session-protected
download, or provider-rendered page. The capture should be marked
`captured_wrong_content`, not treated as a PDF.

### A task is `ignored`

This may indicate duplicate delivery or an already-claimed/completed durable
job. Inspect the job ledger and Celery output before resubmitting.

### Database selection is confusing

```text
DATABASE_URL blank → SQLite
DATABASE_URL set   → PostgreSQL through Psycopg
```

---

## 20. Safety, Evidence, and Claims

- Respect website terms, copyright, and applicable `robots.txt` requirements.
- Store the source URL, capture time, hash, and provenance for every claim.
- Never treat search snippets as verified evidence.
- Never treat an LLM transcription as stronger than its source.
- Do not make engineering, environmental, financial, or product claims from
  inferred values without measured validation.
- Do not expose API keys, database credentials, or `.env`.
- Validate all automatically generated engineering configurations before
  execution.

---

## 21. One-Minute Summary

`main` is a working mattress evidence-intelligence and thermal-prototyping
system. It discovers and captures public evidence, uses OCR/LLMs only for
explicit transcription, and applies deterministic graph, similarity,
constraint, Bayesian, and configuration algorithms.

`feature/cement-digital-twin` retains that system and adds the first cement
layer: plant/document discovery, evidence scoring, user approval, manual leads,
durable capture, artifact preservation, SQL persistence, Celery execution, and
database inspection.

The cement branch does not yet reconstruct the factory automatically. Its job
today is to build a reliable, traceable evidence library from which structured
plant facts, a knowledge graph, a digital twin, and later simulations can be
created.
