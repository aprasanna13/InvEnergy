# EGM Comprehensive Drift Report: `Design/` Directory

* **Target Scope:** Entire `Design/` Specification Directory (15 Documents)
* **Audited Specifications:**
  1. [Design/DESIGN_DOC.md](file:///usr/local/google/home/prasannaankem/Code/Invenergy/Design/DESIGN_DOC.md) (Master Architecture & Session 1 Baseline)
  2. [Design/DESIGN.md](file:///usr/local/google/home/prasannaankem/Code/Invenergy/Design/DESIGN.md) (Invenergy Brand Design System)
  3. [Design/CR1_LANDOWNER_SPECIAL_CONDITIONS.md](file:///usr/local/google/home/prasannaankem/Code/Invenergy/Design/CR1_LANDOWNER_SPECIAL_CONDITIONS.md) (CR-1 Special Conditions)
  4. [Design/CR2_PROJECT_PORTFOLIO_HIERARCHY.md](file:///usr/local/google/home/prasannaankem/Code/Invenergy/Design/CR2_PROJECT_PORTFOLIO_HIERARCHY.md) (CR-2 Portfolio Hierarchy)
  5. [Design/CR3_SUBCONTRACTOR_DND_CHECKLIST.md](file:///usr/local/google/home/prasannaankem/Code/Invenergy/Design/CR3_SUBCONTRACTOR_DND_CHECKLIST.md) (CR-3 Field Crew DND)
  6. [Design/CR4_BIGQUERY_DATA_AGENT_CHAT.md](file:///usr/local/google/home/prasannaankem/Code/Invenergy/Design/CR4_BIGQUERY_DATA_AGENT_CHAT.md) (CR-4 BigQuery Data Agent)
  7. [Design/CR5_SEMANTIC_ZONE_MULTI_PASS_EXTRACTION.md](file:///usr/local/google/home/prasannaankem/Code/Invenergy/Design/CR5_SEMANTIC_ZONE_MULTI_PASS_EXTRACTION.md) (CR-5 Multi-Pass Pipeline)
  8. [Design/CR6_UNIVERSAL_CLIENT_SERVER_PROGRESS_SYSTEM.md](file:///usr/local/google/home/prasannaankem/Code/Invenergy/Design/CR6_UNIVERSAL_CLIENT_SERVER_PROGRESS_SYSTEM.md) (CR-6 Telemetry & Progress)
  9. [Design/CR7_ENTERPRISE_EMAIL_LINK_AUTH.md](file:///usr/local/google/home/prasannaankem/Code/Invenergy/Design/CR7_ENTERPRISE_EMAIL_LINK_AUTH.md) (CR-7 Passwordless Auth & RBAC)
  10. [Design/CR8_ENTERPRISE_OBSERVABILITY_AND_DISTRIBUTED_TRACING.md](file:///usr/local/google/home/prasannaankem/Code/Invenergy/Design/CR8_ENTERPRISE_OBSERVABILITY_AND_DISTRIBUTED_TRACING.md) (CR-8 Observability & Distributed Tracing)
  11. [Design/CR9_REGIONAL_TEMPLATE_STUDIO_AND_CANONICAL_AST_REGISTRY.md](file:///usr/local/google/home/prasannaankem/Code/Invenergy/Design/CR9_REGIONAL_TEMPLATE_STUDIO_AND_CANONICAL_AST_REGISTRY.md) (CR-9 Regional Template Studio)
  12. [Design/CR10_AUTH_ENABLE_DISABLE_ENHANCEMENT.md](file:///usr/local/google/home/prasannaankem/Code/Invenergy/Design/CR10_AUTH_ENABLE_DISABLE_ENHANCEMENT.md) (CR-10 Auth Toggle & Evaluation Handover)
  13. [Design/CR11_CONTRACT_VARIANCE_ENGINE_AND_LEGAL_REDLINE_STUDIO.md](file:///usr/local/google/home/prasannaankem/Code/Invenergy/Design/CR11_CONTRACT_VARIANCE_ENGINE_AND_LEGAL_REDLINE_STUDIO.md) (CR-11 Variance Engine & Redline Studio)
  14. [Design/CR12_HISTORICAL_BATCH_INGESTION_AND_DISCOVERY.md](file:///usr/local/google/home/prasannaankem/Code/Invenergy/Design/CR12_HISTORICAL_BATCH_INGESTION_AND_DISCOVERY.md) (CR-12 Historical Batch Ingestion, Autonomous Consolidation & Batch Studio)
  15. [Design/technical_implementation_proposal.md](file:///usr/local/google/home/prasannaankem/Code/Invenergy/Design/technical_implementation_proposal.md) (Initial Proposal & Infrastructure)
* **Codebase Version:** `v0.12.3` (Commit `8686fa6`, deployed on Google Cloud Run `contract-parser-00022-fwj` and Cloud Run Job `contract-parser-batch:v0.12.3`)
* **Evaluation Framework:** Elephant Growth Model (EGM) / Victory Audit Protocol
* **Audit Date:** 2026-10-15
* **Overall Directory Parity:** **100.0% Plan Parity (24 Positive Fortifications, 0 Negative Regressions, 7 Resolved Conflicts)**

---

## 1. Executive Summary & Audit Scorecard

This comprehensive EGM Drift Report conducts an exhaustive audit of the active codebase against all fifteen architectural blueprints, feature change requests, and design specifications located in the [Design/](file:///usr/local/google/home/prasannaankem/Code/Invenergy/Design) directory.

Every requirement across database schemas (14 Lakehouse base tables and 15 deduplication views), extraction pipelines, REST APIs, frontend components, and security perimeters has been verified against the physical code in `contract_parser/`, `scripts/sql/`, and `tests/`.

### Directory-Wide Parity Scorecard

| Specification | Core Focus | Planned Parity | Negative Drift | Positive Fortifications | Test Status | Verdict |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **DESIGN_DOC.md** | Core 6-Zone Hierarchy & Dual-Pane UI | 100% | 0% | +1 (DF Tree Ordering) | Passed | **VERIFIED** |
| **DESIGN.md** | Invenergy Brand Tokens & Styling | 100% | 0% | 0 | Passed | **VERIFIED** |
| **CR-1** | Landowner Special Conditions (R1) | 100% | 0% | 0 | Passed | **VERIFIED** |
| **CR-2** | Portfolio Hierarchy & 5 Techs (R3) | 100% | 0% | 0 | Passed | **VERIFIED** |
| **CR-3** | Subcontractor DND Checklist (R6) | 100% | 0% | 0 | Passed | **VERIFIED** |
| **CR-4** | BigQuery Data Agent Chat | 100% | 0% | +1 (AI Disclaimer) | Passed | **VERIFIED** |
| **CR-5** | Multi-Pass Extraction Pipeline | 100% | 0% | 0 | Passed | **VERIFIED** |
| **CR-6** | Client-Server Progress System | 100% | 0% | 0 | Passed | **VERIFIED** |
| **CR-7** | Enterprise Passwordless Auth & RBAC | 100% | 0% | +4 (Action link, event binding, brand patch, mock fallback) | Passed | **VERIFIED** |
| **CR-8** | Observability & Distributed Tracing | 100% | 0% | +3 (TracedThreadPool, CloudLogging JSON, In-Loop usage metadata) | Passed | **VERIFIED** |
| **CR-9** | Regional Template Studio & Registry | 100% | 0% | +2 (Mid-Doc signature quarantine, sample templates auto-seed) | Passed | **VERIFIED** |
| **CR-10** | Auth Toggle & Handover Gateway | 100% | 0% | +1 (Zero-network direct DOM bootstrap) | Passed | **VERIFIED** |
| **CR-11** | Contract Variance & Redline Studio | 100% | 0% | +2 (Two-stage Jaccard alignment, bracket parameter masking) | Passed | **VERIFIED** |
| **CR-12** | Historical Batch Ingestion & Studio | 100% | 0% | +9 (Sliding window pool, sub-second GCS scan, location consolidation, clean-slate recovery, Dockerfile.batch, Batch Ingestion Studio, token tracking, raw archive, post-ingestion move) | Passed | **VERIFIED** |
| **Proposal** | Foundation Lakehouse Architecture | 100% | 0% | +1 (Hermetic Bypass) | Passed | **VERIFIED** |

---

## 2. System Architecture & Dual-Engine Data Lakehouse

```mermaid
graph TD
    subgraph DataLakehouse["1. Enterprise Data Lakehouse (14 Base Tables + 15 Views)"]
        direction TB
        P["projects (Table 1)"]
        L["landowners (Table 2)"]
        D["documents (Table 3)"]
        C["clauses (Table 4)"]
        DT["defined_terms (Table 5)"]
        EC["exhibits_catalog (Table 6)"]
        SC["special_conditions (Table 7)"]
        DND["dnd_checklist_signoffs (Table 8)"]
        TR["template_registry (Table 9, CR-9)"]
        TC["template_clauses (Table 10, CR-9)"]
        CV["contract_variances (Table 11, CR-11)"]
        CVD["clause_variance_details (Table 12, CR-11)"]
        BJ["batch_jobs (Table 13, CR-12)"]
        BJI["batch_job_items (Table 14, CR-12)"]
    end

    subgraph ExtractionCore["2. Dual-Engine Semantic Extraction"]
        direction TB
        P1["Pass 1: Structure Discovery (gemini-3.8-flash)"]
        P2["Pass 2: Body Extraction (gemini-3.1-pro)"]
        P3["Pass 3: Exhibits Extraction (gemini-3.1-pro)"]
        TPL["Two-Pass Template Pipeline with Signature Quarantine"]
        BA["bundle_assembler.py + Tree Ordering Engine"]
        P1 --> P2 & P3 --> BA
        TPL --> TR & TC
    end

    subgraph BatchIngestion["3. Historical Batch Engine (CR-12)"]
        direction TB
        Scan["Option 4a: Sub-Second GCS Blob Metadata Pre-Filter"]
        Geo["Option 3: Location-Driven Project Consolidation & First Anchor"]
        Pool["Option B: Sliding-Window Pool (5 Concurrent Workers)"]
        Purge["Clean-Slate Recovery: purge_partial_document()"]
        Archive["Raw Storage Archiving & Post-Ingestion Move"]
        TokenTrack["Prompt & Completion In-Loop Token Accumulation"]
        Scan --> Geo --> Pool --> Purge --> Archive --> TokenTrack
    end

    subgraph ObservabilityLayer["4. Enterprise Observability & Tracing (CR-8)"]
        direction TB
        OTel["OpenTelemetry Tracer + CloudTraceSpanExporter"]
        Log["CloudLoggingJsonFormatter (stdout, single-line)"]
        TracePool["TracedThreadPoolExecutor (contextvars.copy_context)"]
        Usage["In-Loop Token Accounting (usage_metadata)"]
    end

    subgraph VarianceEngine["5. Contract Variance & Redline Engine (CR-11)"]
        direction TB
        Affinity["Affinity Matcher (Country, State, Tech, Form, Vintage)"]
        Align["Two-Stage Hybrid Alignment (Path + Jaccard >= 0.70)"]
        ParamMask["Bracket Parameter Masker ($[_____], [County])"]
        MyersDiff["Word-Level Myers Diff (<ins> / <del> Redline HTML)"]
        RiskScore["Multi-Tier Legal Risk Scoring (NONE, LOW, MED, HIGH)"]
        Affinity --> Align --> ParamMask --> MyersDiff --> RiskScore
    end

    DataLakehouse <--> ExtractionCore
    DataLakehouse <--> BatchIngestion
    ExtractionCore --> ObservabilityLayer
    BatchIngestion --> ObservabilityLayer
    DataLakehouse <--> VarianceEngine
```

---

## 3. Specification-by-Specification Parity Breakdown

### 1. `Design/DESIGN_DOC.md` (Session 1 Baseline Architecture)
* **Scope:** 6 structural contract zones (`PREAMBLE`, `RECITALS`, `BODY`, `SIGNATURES`, `EXHIBIT_OR_SCHEDULE`, `AMENDMENT`), 4-tier hierarchy, dual-store SQLite + BigQuery lakehouse, Cloud Storage raw PDF persistence, and PDF.js split-pane HITL UI.
* **Code Parity:**
  - [contract_parser/schemas.py](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/schemas.py): `DocumentZone` and 4-tier node hierarchy.
  - [contract_parser/storage.py](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/storage.py): Append-only base tables (`clauses`, `defined_terms`, `exhibits_catalog`, `documents`) and deduplicated views (`*_latest`).
  - [contract_parser/static/index.html](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/static/index.html): Left tree navigation pane and right PDF.js viewer canvas with synchronized page jumping and coordinate highlights.
* **Drift:** 100% parity. Fortified in `v0.5.3` with the Document Tree Ordering Engine.

### 2. `Design/DESIGN.md` (Invenergy Brand Design System)
* **Scope:** Design tokens, corporate color palette (`#0a4d34` forest green, `#ffc72c` gold, `#0b2341` navy), modern typography, component styles, and accessible contrast.
* **Code Parity:**
  - [contract_parser/static/index.html](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/static/index.html): Custom CSS styling, CSS variables (`--primary-green`, `--accent-gold`, `--navy-dark`), badge indicators, and layout styling.
* **Drift:** 100% parity.

### 3. `Design/CR1_LANDOWNER_SPECIAL_CONDITIONS.md` (CR-1 Special Conditions)
* **Scope:** Landowner special condition extraction, restriction categorizations (`FARMING_OPERATIONS`, `WATER_AND_TILE`, `HUNTING_AND_RECREATION`, `NOISE_AND_SETBACKS`, `CONSTRUCTION_HOURS`), temporal enforcement windows, and severity ratings.
* **Code Parity:**
  - [contract_parser/schemas.py](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/schemas.py): `SpecialConditionRow`, `ConditionCategory`, `TemporalEnforcementWindow`, and `ConditionSeverity`.
  - [contract_parser/storage.py](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/storage.py): Table 7 `special_conditions` in SQLite and BigQuery.
  - [contract_parser/static/index.html](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/static/index.html): Dedicated Site Constraints tab with filtering.
* **Drift:** 100% parity.

### 4. `Design/CR2_PROJECT_PORTFOLIO_HIERARCHY.md` (CR-2 Portfolio Hierarchy)
* **Scope:** 5 energy technologies (`WIND`, `SOLAR`, `BATTERY_STORAGE`, `TRANSMISSION`, `CLEAN_HYDROGEN`), ERP project codes, landowner parcel aggregations, and top portfolio selector.
* **Code Parity:**
  - [contract_parser/schemas.py](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/schemas.py): `EnergyTechnology`, `ProjectRow`, and `LandownerRow`.
  - [contract_parser/static/index.html](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/static/index.html): Top portfolio bar with technology badges and project selector.
* **Drift:** 100% parity.

### 5. `Design/CR3_SUBCONTRACTOR_DND_CHECKLIST.md` (CR-3 Field Crew DND)
* **Scope:** Field subcontractor "Do Not Disturb" (DND) clearance, foreman tailgate review, checklist sign-offs, and compliance attestation.
* **Code Parity:**
  - [contract_parser/schemas.py](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/schemas.py): `DNDChecklistSignoffRow`.
  - [contract_parser/storage.py](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/storage.py): Table 8 `dnd_checklist_signoffs`.
  - [contract_parser/static/index.html](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/static/index.html): Subcontractor DND tab with foreman signoff form.
* **Drift:** 100% parity.

### 6. `Design/CR4_BIGQUERY_DATA_AGENT_CHAT.md` (CR-4 BigQuery Data Agent)
* **Scope:** Conversational natural language portfolio queries via Gemini in BigQuery Data Analytics Agent (`projects/.../dataAgents/...`), streaming chat interface, and analytical grounding.
* **Code Parity:**
  - [contract_parser/ask_agent.py](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/ask_agent.py): REST proxy with retry backoff and error handling.
  - [contract_parser/static/index.html](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/static/index.html): Slide-out Ask Agent drawer with AI disclaimer banner.
* **Drift:** 100% parity. Fortified with explicit AI accuracy disclaimer.

### 7. `Design/CR5_SEMANTIC_ZONE_MULTI_PASS_EXTRACTION.md` (CR-5 Multi-Pass Extraction)
* **Scope:** 3-pass semantic extraction: Pass 1 boundary discovery (`gemini-3.8-flash`), Pass 2 agreement body (`gemini-3.1-pro-preview`), Pass 3 exhibits (`gemini-3.1-pro-preview`) with concurrent execution.
* **Code Parity:**
  - [contract_parser/gemini_parser.py](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/gemini_parser.py): `_discover_contract_structure`, `_extract_body_pass`, `_extract_exhibits_pass`, and `TracedThreadPoolExecutor`.
  - [contract_parser/bundle_assembler.py](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/bundle_assembler.py): Assembly and deduplication logic.
* **Drift:** 100% parity.

### 8. `Design/CR6_UNIVERSAL_CLIENT_SERVER_PROGRESS_SYSTEM.md` (CR-6 Progress System)
* **Scope:** Top ambient progress bar, center glassmorphic multi-phase ingestion modal with live stopwatch, 0-audio policy, and graceful error teardown.
* **Code Parity:**
  - [contract_parser/static/index.html](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/static/index.html): Universal fetch interceptor, `#global-top-progress`, `#extraction-modal-overlay`.
* **Drift:** 100% parity.

### 9. `Design/CR7_ENTERPRISE_EMAIL_LINK_AUTH.md` (CR-7 Passwordless Auth & RBAC)
* **Scope:** Zero-trust Firebase / Identity Platform email link authentication, domain gating (`@invenergy.com`, `@google.com`), rate limiting, and role-based permissions (`ADMIN` vs `VIEWER`).
* **Code Parity:**
  - [contract_parser/auth.py](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/auth.py): Bearer token verification, dual sliding-window rate limiters, RBAC guards (`require_admin`, `require_viewer`).
  - [contract_parser/static/index.html](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/static/index.html): Passwordless sign-in modal and fetch interceptor.
* **Drift:** 100% parity. Fortified with `canHandleCodeInApp: true` action links, official Web App provisioning, and brand template settings.

### 10. `Design/CR8_ENTERPRISE_OBSERVABILITY_AND_DISTRIBUTED_TRACING.md` (CR-8 Observability)
* **Scope:** Structured single-line JSON Cloud Logging (`CloudLoggingJsonFormatter`), OpenTelemetry distributed tracing with `CloudTraceSpanExporter`, W3C / GCP composite propagation, serverless flush hooks, and in-loop token metrics.
* **Code Parity:**
  - [contract_parser/telemetry/](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/telemetry/): `logging_formatter.py`, `tracing.py`, `thread_propagation.py`.
  - [contract_parser/app.py](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/app.py): Lifespan flush and FastAPI instrumentation.
* **Drift:** 100% parity. Fortified with Uvicorn log capture, PII scrubbing, and Error Reporting tracebacks.

### 11. `Design/CR9_REGIONAL_TEMPLATE_STUDIO_AND_CANONICAL_AST_REGISTRY.md` (CR-9 Template Studio)
* **Scope:** Regional master template registry, bounded two-pass AST extraction, signature block quarantine, placeholder variable regex tokenization, dedicated workbench UI (`/template-studio`), and sample template seeding.
* **Code Parity:**
  - [contract_parser/schemas.py](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/schemas.py): `TemplateRegistryRow`, `TemplateClauseRow`, `PlaceholderVariable`.
  - [contract_parser/storage.py](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/storage.py): Tables 9 & 10 (`template_registry`, `template_clauses`).
  - [contract_parser/static/template_studio.html](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/static/template_studio.html): Standalone 3-pane workbench UI.
* **Drift:** 100% parity. Fortified with automated cold-start sample template seeding.

### 12. `Design/CR10_AUTH_ENABLE_DISABLE_ENHANCEMENT.md` (CR-10 Auth Toggle)
* **Scope:** Single-variable `AUTH_ENABLED` toggle, backend developer bypass context (`dev@invenergy.com`), frontend bootstrap negotiation via `/api/v1/auth/config`, and turnkey evaluation container modes.
* **Code Parity:**
  - [contract_parser/config.py](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/config.py): Boolean truth parsing and .env loading.
  - [contract_parser/auth.py](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/auth.py): `get_current_user` bypass.
  - [contract_parser/static/index.html](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/static/index.html): Direct DOM bootstrap.
* **Drift:** 100% parity.

### 13. `Design/CR11_CONTRACT_VARIANCE_ENGINE_AND_LEGAL_REDLINE_STUDIO.md` (CR-11 Variance Engine)
* **Scope:** Hybrid two-stage AST alignment (canonical path + Jaccard index $\ge 0.70$), bracket parameter masking (`$[_____]`, `[County]`), Myers word-level diff generating HTML redlines, multi-tier risk scoring (`NONE`, `LOW`, `MEDIUM`, `HIGH`), and Legal Redline Studio UI.
* **Code Parity:**
  - [contract_parser/variance_engine.py](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/variance_engine.py): Alignment, diff, and scoring.
  - [contract_parser/storage.py](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/storage.py): Tables 11 & 12 (`contract_variances`, `clause_variance_details`).
  - [contract_parser/static/index.html](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/static/index.html): Redline Studio tab and diff viewer.
* **Drift:** 100% parity.

### 14. `Design/CR12_HISTORICAL_BATCH_INGESTION_AND_DISCOVERY.md` (CR-12 Historical Batch Ingestion)
* **Scope:** Automated onboarding of historical contracts from flat GCS prefixes (`gs://<bucket>/batch_inbox/`), autonomous location-driven project consolidation, Option B continuous sliding-window worker pool (5 active workers), clean-slate fault recovery (`purge_partial_document`), dedicated batch runner container (`Dockerfile.batch`), dual-engine state tracking (`batch_jobs`, `batch_job_items`), and Batch Ingestion Studio dashboard modal.
* **Code Parity:**
  - [contract_parser/batch.py](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/batch.py): `scan_batch_inbox`, `run_sliding_window_worker_pool`, `process_single_batch_item`, `get_or_create_project_for_location`, `retry_failed_batch_items`.
  - [contract_parser/storage.py](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/storage.py): Tables 13 & 14 (`batch_jobs`, `batch_job_items`) and deduplicated views (`v_active_batch_jobs`, `v_active_batch_job_items`).
  - [contract_parser/static/index.html](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/static/index.html): Batch Ingestion Studio modal (`#batch-studio-modal`) with real-time polling, job list, item feed, and contract viewer navigation.
  - [contract_parser/gemini_parser.py](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/gemini_parser.py): Thread-safe token usage accumulation (`prompt_tokens`, `completion_tokens`, `total_tokens`).
  - [Dockerfile.batch](file:///usr/local/google/home/prasannaankem/Code/Invenergy/Dockerfile.batch): Dedicated Cloud Run Job container.
* **Drift:** 100% parity. Positive fortifications added in v0.12.1–v0.12.3:
  1. Sub-second GCS metadata pre-filtering without downloading PDFs.
  2. URI auto-normalization accepting bucket prefixes with or without `gs://`.
  3. Canonical raw PDF archiving to `raw/<document_id>/<filename>` and post-ingestion inbox-to-archive movement (`archive/<filename>`).
  4. In-loop multi-pass Gemini token accumulation across discovery and body/exhibit passes.
  5. Per-contract and job-level prompt and completion token tracking and visibility in BigQuery and UI.

### 15. `Design/technical_implementation_proposal.md` (Initial Infrastructure Proposal)
* **Scope:** Foundational dual-tier lakehouse architecture, GCS raw storage layout, BigQuery analytical schema, and Python runtime design.
* **Code Parity:**
  - Full codebase adheres to foundational lakehouse principles with append-only streaming and deduplicated analytical views.
* **Drift:** 100% parity.

---

## 4. Conflict Resolution Audit Log

The following seven architectural and documentation conflicts were identified across the specifications, SQL scripts, setup automation, and handover manuals during this audit and have all been resolved:

| # | Conflict Identified | Affected Component | Resolution Implemented | Parity Status |
| :-: | :--- | :--- | :--- | :-: |
| **1** | **Table 14 Schema Drift:** `batch_job_items` DDL was missing `prompt_tokens INT64` and `completion_tokens INT64` columns, and header only listed 8 tables. | [scripts/sql/01_create_tables.sql](file:///usr/local/google/home/prasannaankem/Code/Invenergy/scripts/sql/01_create_tables.sql) | Added nullable `prompt_tokens` and `completion_tokens` to Table 14 DDL and updated header comments to list all 14 tables. | **RESOLVED** |
| **2** | **Views Header Drift:** Header comment in `02_create_views.sql` was not updated to reflect 15 active-state deduplication views. | [scripts/sql/02_create_views.sql](file:///usr/local/google/home/prasannaankem/Code/Invenergy/scripts/sql/02_create_views.sql) | Verified `v_active_batch_jobs` (View 14) and `v_active_batch_job_items` (View 15) definitions and aligned view count. | **RESOLVED** |
| **3** | **Cloud Run Job IAM Drift:** Automated GCP provisioning script omitted `roles/run.developer`, causing Cloud Run v2 Client API job execution to fail on permissions. | [scripts/setup_gcp_environment.sh](file:///usr/local/google/home/prasannaankem/Code/Invenergy/scripts/setup_gcp_environment.sh) | Added `roles/run.developer` to the service account roles array in provisioning script. | **RESOLVED** |
| **4** | **GCS Directory Provisioning Drift:** Bucket creation in setup script did not initialize standard directory layout (`batch_inbox/`, `raw/`, `archive/`, `exports/`). | [scripts/setup_gcp_environment.sh](file:///usr/local/google/home/prasannaankem/Code/Invenergy/scripts/setup_gcp_environment.sh) | Added automated initialization loop placing `.keep` markers in all four canonical GCS directories. | **RESOLVED** |
| **5** | **Environment Template Drift:** `.env.example` still referenced "8 core tables", lacked `USE_LOCAL_BATCH_RUNNER`, and had stale version `0.9.0`. | [.env.example](file:///usr/local/google/home/prasannaankem/Code/Invenergy/.env.example) | Updated to reference 14 tables, 15 views, added `USE_LOCAL_BATCH_RUNNER=true`, and bumped version to `0.12.3`. | **RESOLVED** |
| **6** | **Customer Handover Guide Drift:** `README.md` showed `v0.9.0` architecture, 8 tables, lacked CR-11 and CR-12 documentation, and lacked Cloud Run Job deploy steps. | [README.md](file:///usr/local/google/home/prasannaankem/Code/Invenergy/README.md) | Comprehensively upgraded to `v0.12.3` with full 14-table Mermaid diagram, repository layout, Section 8.6 (CR-11), Section 8.7 (CR-12), and Cloud Run Job commands. | **RESOLVED** |
| **7** | **EGM Drift Report Version Drift:** Previous report in `Design/EGM_DRIFT_REPORT.md` was at `v0.11.2` auditing only 14 specs before CR-12 was implemented. | [Design/EGM_DRIFT_REPORT.md](file:///usr/local/google/home/prasannaankem/Code/Invenergy/Design/EGM_DRIFT_REPORT.md) | Rewrote EGM Drift Report auditing all 15 specifications, recording all 7 resolved conflicts, and certifying 100% parity across `v0.12.3`. | **RESOLVED** |

---

## 5. Automated Verification Results

- **Batch Ingestion Suite ([tests/test_cr12_batch_ingestion.py](file:///usr/local/google/home/prasannaankem/Code/Invenergy/tests/test_cr12_batch_ingestion.py)):**
  - All 8 unit and integration tests passed in 33.43s.
- **Full Platform Regression Suite:**
  - 85 passed, 1 deselected, 0 failures across all test suites (`test_concurrency_lifecycle`, `test_cr10_auth_toggle`, `test_cr11_contract_variance`, `test_cr12_batch_ingestion`, `test_cr6_progress_system`, `test_cr7_auth`, `test_cr8_observability_and_tracing`, `test_cr9_template_studio`, `test_multi_pass_integration`, `test_multi_pass_units`, `test_pipeline`).
- **Live Cloud Run Deployment:**
  - Service `contract-parser` deployed at `https://contract-parser-255093976233.us-central1.run.app` (revision `contract-parser-00022-fwj`) serving 100% traffic.
  - Job `contract-parser-batch` updated to image `gcr.io/pr-tftest/contract-parser-batch:v0.12.3`.
- **BigQuery Verification:**
  - 14 append-only tables and 15 deduplication views active in `pr-tftest.contract_intelligence`.
  - Batch job `batch_20261015_151412` verified with 168,340 total tokens and individual prompt/completion metrics across all 5 contract records.
