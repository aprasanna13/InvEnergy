# Change Request CR-9: Regional Template Studio & Canonical AST Registry Architecture

* **CR ID:** `CR-009` (Regional Template Studio & Canonical AST Registry — Stage 1 of Deviation Intelligence)
* **Status:** Fortified per Architecture Audit & Critique
* **Parent Architecture:** [DESIGN_DOC.md](file:///usr/local/google/home/prasannaankem/Code/Invenergy/Design/DESIGN_DOC.md), [CR1_LANDOWNER_SPECIAL_CONDITIONS.md](file:///usr/local/google/home/prasannaankem/Code/Invenergy/Design/CR1_LANDOWNER_SPECIAL_CONDITIONS.md), [CR2_PROJECT_PORTFOLIO_HIERARCHY.md](file:///usr/local/google/home/prasannaankem/Code/Invenergy/Design/CR2_PROJECT_PORTFOLIO_HIERARCHY.md), [CR5_SEMANTIC_ZONE_MULTI_PASS_EXTRACTION.md](file:///usr/local/google/home/prasannaankem/Code/Invenergy/Design/CR5_SEMANTIC_ZONE_MULTI_PASS_EXTRACTION.md), [CR7_ENTERPRISE_EMAIL_LINK_AUTH.md](file:///usr/local/google/home/prasannaankem/Code/Invenergy/Design/CR7_ENTERPRISE_EMAIL_LINK_AUTH.md), [CR8_ENTERPRISE_OBSERVABILITY_AND_DISTRIBUTED_TRACING.md](file:///usr/local/google/home/prasannaankem/Code/Invenergy/Design/CR8_ENTERPRISE_OBSERVABILITY_AND_DISTRIBUTED_TRACING.md)
* **Confirmed Architectural Decisions (Fortified via EGM Review):**
  1. **Dedicated Lakehouse Base Tables & Deduplicated Views (`template_registry` & `template_clauses`):**
     Isolates canonical legal templates from operational executed contract event streams. Base tables use append-only semantics clustered by taxonomy dimensions (`country, state_province, energy_technology`) and primary AST nodes (`template_id, node_id`). Deduplication views (`template_registry_latest`, `template_clauses_latest`) resolve current active revisions using `QUALIFY ROW_NUMBER() OVER (...) = 1`, preventing query pollution in field construction queries, DND checklist calculations, and BigQuery Data Agent sessions.
  2. **Canonical Taxonomy Keying with Deterministic Semantic Slug & SHA-256 Digest:**
     Organizes master templates according to real-world renewable energy jurisdictions and legal forms (`Country -> State -> Energy Technology -> Form Type -> Vintage -> Revision`). Forms a deterministic primary key (`tpl_{country}_{state}_{tech}_{form}_{vintage}_{rev}`) backed by an immutable `sha256_hash` binary digest to guarantee collision-free deduplication and reproducible multi-version tracking.
  3. **Two-Pass Bounded Template Extraction Pipeline (Zone Decomposition):**
     Eliminates single-pass token exhaustion and context dilution across 35–60 page master templates by decomposing extraction into two bounded stages:
     - *Pass 1 (Template Boundary & Zone Discovery):* Rapidly extracts document zones (`PREAMBLE`, `RECITALS`, `BODY`, `EXHIBIT_OR_SCHEDULE`), substantive section page boundaries, attached exhibits, and the exact physical page bounds of execution/signature/notary blocks.
     - *Pass 2 (Substantive Article & Exhibit Extraction):* Concurrently extracts substantive legal covenants and attached exhibits while strictly bypassing quarantined signature ranges.
  4. **Strict Mid-Document Execution & Notary Quarantine (Not Truncation):**
     Resolves the critical legal document structural reality where execution and notary acknowledgment blocks appear in the *middle* of the agreement (between the substantive body and attached exhibits). Rather than naively truncating extraction at signature markers (which drops exhibits), the engine discovers the exact physical page boundaries of execution blocks (`IN WITNESS WHEREOF...`), signature tables, and notary certificates (`State of __, County of __, ss:`), isolates them under a quarantined `SIGNATURES` zone, and explicitly prevents node generation while continuing substantive extraction across subsequent exhibits.
  5. **Verbatim Fill-in Placeholder Variable Extraction (`placeholder_variables`):**
     Directs Gemini multimodal extraction to preserve exact fill-in-the-blank brackets (`$[_______] per megawatt`, `within [30/60] days`, `[County]`) without hallucinating synthetic figures. Extracts structured variable metadata into a JSON column (`placeholder_variables`), recording variable name, data type hint, bracketed text, and page offsets to support automated parameter matching in CR-10 deviation scoring.
  6. **Zero-Trust RBAC & Perimeter Security Enforcement (CR-7 Compliance):**
     Restricts all template ingestion, mutation, and seeding endpoints (`POST /api/v1/templates/upload`, `POST /api/v1/templates/seed`) to users with the `Admin` role (`@google.com` or whitelisted admin identities) via `require_admin(current_user)`. Enforces HTTP 403 Forbidden for `Viewer` role users (`@invenergy.com`), with client-side visual DOM disabling (`opacity: 0.45; cursor: not-allowed`) on template upload controls. Read-only template access (`GET /api/v1/templates`, `GET /api/v1/templates/{id}`) is open to all authenticated users via `Depends(auth_dep)`.
  7. **Enterprise Observability & OpenTelemetry Trace Propagation (CR-8 Compliance):**
     Instruments template ingestion with OpenTelemetry distributed tracing under `contract_parser.app` and `contract_parser.gemini_parser`. Ingestion spawns root span `POST /api/v1/templates/upload` with nested child spans for binary hashing, Pass 1 discovery, Pass 2 substantive extraction, and BigQuery persistence. Extracts token usage metadata (`gen_ai.usage.*`) in-loop from raw SDK responses and routes single-line structured JSON logs with isolated `contract.template_id` namespace tags.
  8. **Dedicated Standalone Template Studio UI (`template_studio.html`):**
     Avoids mixing complex template inspection state into the 3,500-line executed contracts UI (`index.html`) by introducing a dedicated standalone web application in [`contract_parser/static/template_studio.html`](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/static/template_studio.html) (served via `/template-studio`). Linked cleanly from the main app's top navigation bar via `[ 🏛️ Regional Template Studio ↗ ]`. Shares the same `localStorage` session token for instant RBAC role inheritance without re-login, enabling side-by-side browser window snapping for direct legal comparison.
  9. **Zero-Setup Cold Start via Local Samples Auto-Discovery (`seed_sample_templates`):**
     Provides an idempotent startup and CLI hook in `ContractStorageService` that automatically scans `samples/Templates/` for existing regional forms (Illinois Storage Easement, Michigan Participation Easement, Michigan Solar Easement, Oklahoma Land Option, Oklahoma Solar Lease, US General Wind Easement), verifies their SHA-256 hashes against `template_registry`, and provides pre-compiled baseline fixtures for hermetic test suites and offline local development.
  10. **Hermetic Testing & Invariant Verification Strategy:**
      Validates the template engine using mock-backed fixtures and real PDF sample assertions across 8 unit tests (`UT-1` to `UT-8`) and 3 integration tests (`IT-1` to `IT-3`), verifying zero operational query pollution in existing tables, complete signature suppression, and verbatim placeholder retention.

---

## 1. Operational Problem & Business Context

Invenergy manages approximately 300 active renewable energy projects across North America. For each project, development begins from a standardized **regional legal template** tailored to the governing state's statutory framework and technology type (such as an Illinois Wind Easement or a Michigan Solar Lease). During land acquisition, roughly 50 private landowners per project negotiate custom amendments, addenda, and special conditions before signing.

### Operational and Business Risks
1. **The Undetected Deviation Liability:**
   Developers and construction managers currently manage executed agreements as disconnected stacks of physical PDFs. Missing a single negotiated deviation (such as a 150-foot setback around a historic barn or a 48-hour drainage tile repair mandate) can cause catastrophic field delays, contractual breaches, and financial liquidated damages exceeding **$100,000 to $500,000 per incident**.
2. **Lack of a Canonical Reference Baseline:**
   Today, the contract parsing engine operates exclusively in single-document mode ([`CR-1`](file:///usr/local/google/home/prasannaankem/Code/Invenergy/Design/CR1_LANDOWNER_SPECIAL_CONDITIONS.md)). While it identifies explicit special conditions written in riders or exhibits, it has no baseline standard against which to compare the body clauses of an executed contract. It cannot programmatically detect when a standard boilerplate indemnity clause, payment escalator, or restoration covenant was silently altered during landowner negotiations.
3. **No Centralized Template Studio:**
   Regional legal templates currently reside in scattered file shares and legal document archives. There is no centralized catalog in BigQuery or Cloud Storage where developers can inspect the active canonical language for a given jurisdiction, view extracted clauses, or verify that the baseline language matches the physical PDF form.

---

## 2. Fortified Technical Architecture

```mermaid
flowchart TD
    subgraph ClientLayer["1. Invenergy Frontend Client (contract_parser/static/)"]
        MainApp["Main App (index.html)\n[ 📄 Executed Contracts ]"]
        StudioLink["Top Bar Link: 🏛️ Regional Template Studio ↗\n(Opens standalone template_studio.html in new tab)"]
        StudioPage["Template Studio (template_studio.html)\n- Shared localStorage JWT Token\n- Cascade Taxonomy Selector\n- Upload Master Template Modal (Admin Only)"]
        SplitViewer["Split-Screen Inspection Workspace:\nLeft: Canonical AST Tree (Placeholder Badges)\nRight: Synchronized PDF.js Viewport (Click-to-Page Jump)"]
        MainApp --> StudioLink --> StudioPage --> SplitViewer
    end

    subgraph SecurityLayer["2. Zero-Trust Security & RBAC Perimeter (contract_parser/auth.py)"]
        AuthDep{"Depends(auth_dep)\nUserContext Validation"}
        AdminCheck{"require_admin(user)\nRole == 'admin'"}
        AuthDep -->|Authenticated| StudioPage
        AuthDep --> AdminCheck
        AdminCheck -->|Admin: 200 OK| UploadEndpoint
        AdminCheck -->|Viewer: 403 Forbidden| BlockNotice["HTTP 403 Forbidden / DOM Disabled"]
    end

    subgraph APILayer["3. Template Service & Storage Layer (app.py & storage.py)"]
        UploadEndpoint["POST /api/v1/templates/upload\n(require_admin, OTel Traced)"]
        ListEndpoint["GET /api/v1/templates\n(Filtered by Taxonomy)"]
        GetEndpoint["GET /api/v1/templates/{template_id}\n(Full Metadata & Clause AST)"]
        PdfEndpoint["GET /api/v1/templates/{template_id}/pdf\n(Raw Binary Stream)"]
        SeedEndpoint["POST /api/v1/templates/seed\n(require_admin, Cold-Start Discovery)"]
        StorageSvc["ContractStorageService (storage.py):\n- SQLite / BigQuery lakehouse tables\n- PDF blob caching & GCS mirror"]
    end

    subgraph ExtractionCore["4. Two-Pass Bounded Template Pipeline (gemini_parser.py)"]
        P1["Pass 1: Structure Discovery (gemini-3.8-flash)\n• Title, State, Tech, Form, Vintage\n• Section Page Spans & Hierarchy\n• Quarantined Signature Page Range\n• Attached Exhibits Spans"]
        P2["Pass 2: Substantive AST Extraction (gemini-3.1-pro-preview)\n• Verbatim Covenants & Recitals\n• [_____] Placeholder Variable Extraction\n• Signature Zone Suppression Gate"]
        Assembly["Canonical AST Assembly & Document Tree Ordering\n(bundle_assembler.py: assemble_template_bundle)"]
        P1 --> P2 --> Assembly
    end

    subgraph LakehousePersistence["5. BigQuery & SQLite Dedicated Storage"]
        T_REG[(template_registry\nBase Table: Clustered by Taxonomy)]
        T_CLS[(template_clauses\nBase Table: Clustered by template_id, node_id)]
        V_REG[(View: template_registry_latest\nQUALIFY ROW_NUMBER() = 1)]
        V_CLS[(View: template_clauses_latest\nQUALIFY ROW_NUMBER() = 1)]
    end

    UploadEndpoint --> ExtractionCore
    ExtractionCore --> StorageSvc
    StorageSvc --> T_REG & T_CLS
    T_REG & T_CLS --> V_REG & V_CLS
    V_REG & V_CLS --> ListEndpoint & GetEndpoint
    GetEndpoint --> SplitViewer
    PdfEndpoint --> SplitViewer
```

---

## 3. Database Layer: Lakehouse DDL & Deduplication Views

### 3.1 BigQuery DDL Definitions (`scripts/sql/01_create_tables.sql`)

```sql
-- 9. Template Registry (Master Legal Forms Directory)
CREATE TABLE IF NOT EXISTS `contract_intelligence.template_registry` (
    template_id STRING NOT NULL,
    template_name STRING NOT NULL,
    country STRING NOT NULL,
    state_province STRING NOT NULL,
    energy_technology STRING NOT NULL,
    form_type STRING NOT NULL,
    vintage_year STRING NOT NULL,
    revision STRING NOT NULL,
    gcs_pdf_uri STRING NOT NULL,
    sha256_hash STRING NOT NULL,
    page_count INT64 NOT NULL,
    clause_count INT64 NOT NULL,
    is_active BOOL NOT NULL,
    created_by STRING NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(created_at)
CLUSTER BY country, state_province, energy_technology;

-- 10. Template Clauses (Canonical Boilerplate AST Nodes)
CREATE TABLE IF NOT EXISTS `contract_intelligence.template_clauses` (
    template_id STRING NOT NULL,
    node_id STRING NOT NULL,
    parent_node_id STRING,
    sibling_order INT64 NOT NULL,
    document_zone STRING NOT NULL,
    canonical_path STRING NOT NULL,
    depth INT64 NOT NULL,
    clause_label STRING NOT NULL,
    clause_title STRING,
    numbering_scheme STRING NOT NULL,
    verbatim_text STRING NOT NULL,
    placeholder_variables JSON,
    page_start INT64 NOT NULL,
    page_end INT64 NOT NULL,
    created_at TIMESTAMP NOT NULL
)
CLUSTER BY template_id, node_id;
```

### 3.2 BigQuery Deduplication Views (`scripts/sql/02_create_views.sql`)

```sql
-- View: template_registry_latest
CREATE OR REPLACE VIEW `contract_intelligence.template_registry_latest` AS
SELECT *
FROM `contract_intelligence.template_registry`
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY template_id
    ORDER BY updated_at DESC
) = 1;

-- View: template_clauses_latest
CREATE OR REPLACE VIEW `contract_intelligence.template_clauses_latest` AS
SELECT *
FROM `contract_intelligence.template_clauses`
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY template_id, node_id
    ORDER BY created_at DESC
) = 1;
```

### 3.3 SQLite Persistence Schemas (`contract_parser/storage.py`)

```python
SQLITE_CREATE_TEMPLATE_REGISTRY = """
CREATE TABLE IF NOT EXISTS template_registry (
    template_id TEXT PRIMARY KEY,
    template_name TEXT NOT NULL,
    country TEXT NOT NULL,
    state_province TEXT NOT NULL,
    energy_technology TEXT NOT NULL,
    form_type TEXT NOT NULL,
    vintage_year TEXT NOT NULL,
    revision TEXT NOT NULL,
    gcs_pdf_uri TEXT NOT NULL,
    sha256_hash TEXT NOT NULL,
    page_count INTEGER NOT NULL,
    clause_count INTEGER NOT NULL,
    is_active INTEGER NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

SQLITE_CREATE_TEMPLATE_CLAUSES = """
CREATE TABLE IF NOT EXISTS template_clauses (
    template_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    parent_node_id TEXT,
    sibling_order INTEGER NOT NULL,
    document_zone TEXT NOT NULL,
    canonical_path TEXT NOT NULL,
    depth INTEGER NOT NULL,
    clause_label TEXT NOT NULL,
    clause_title TEXT,
    numbering_scheme TEXT NOT NULL,
    verbatim_text TEXT NOT NULL,
    placeholder_variables TEXT,
    page_start INTEGER NOT NULL,
    page_end INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (template_id, node_id)
);
"""
```

### 3.4 Canonical Grounding & Mapping for Local Sample Templates (`samples/Templates/`)

To anchor the taxonomy and AST rules in real-world legal documents, the engine provides verified groundings for the 6 canonical forms present in the repository:

| Sample Relative Path | Country | State | Energy Technology | Form Type | Vintage | Canonical Template ID Slug |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `Illinois/Real Estate Form - Illinois - Storage Easement.pdf` | `US` | `IL` | `STORAGE` | `STORAGE_EASEMENT` | `2025` | `tpl_us_il_storage_storage_easement_2025_v1` |
| `Michigan/Real Estate Form - Michigan - Energy Project Participation Easement Agreement.pdf` | `US` | `MI` | `CROSS_TECHNOLOGY` | `PARTICIPATION_EASEMENT` | `2024` | `tpl_us_mi_cross_technology_participation_easement_2024_v1` |
| `Michigan/Real Estate Form - Michigan - Solar Easement Form.pdf` | `US` | `MI` | `SOLAR` | `SOLAR_EASEMENT` | `2024` | `tpl_us_mi_solar_solar_easement_2024_v1` |
| `Oklahoma/Real Estate Form - Oklahoma - Land Option Purchase Agreement - THERMAL.pdf` | `US` | `OK` | `THERMAL` | `LAND_OPTION_PURCHASE` | `2024` | `tpl_us_ok_thermal_land_option_purchase_2024_v1` |
| `Oklahoma/Real Estate Form - Oklahoma - Solar Lease and Easement Form.pdf` | `US` | `OK` | `SOLAR` | `SOLAR_LEASE` | `2024` | `tpl_us_ok_solar_solar_lease_2024_v1` |
| `US General/Real Estate Form - US - Wind Easement Form - 2020.Final. revised 05.28.2020.pdf` | `US` | `US` | `ONSHORE_WIND` | `WIND_EASEMENT` | `2020` | `tpl_us_us_onshore_wind_wind_easement_2020_v1` |

#### Taxonomy Resolution Strategy: Auto-Detection + Admin Confirmation
When ingesting templates, the system supports a dual-mode identification strategy:
1. **Automated Header & First-Page Inference:** During Pass 1 (or a lightweight pre-scan of pages 1–2), Gemini extracts the document title, governing jurisdiction (`State of ...`), technology references (`solar energy`, `energy storage facilities`, `wind resources`, `THERMAL`), and form type.
2. **Interactive Admin Confirmation / Override:** In the Upload Modal, the extracted metadata pre-populates cascading taxonomy dropdowns (`Country`, `State/Province`, `Technology`, `Form Type`, `Vintage Year`, `Revision`). The Admin reviews and confirms the values before ingestion. For multi-state templates (such as `US General` where the text has `State of [__________]`), the Admin can either retain `US` (General) or assign it to a specific target state.

---

## 4. Backend & Extraction Layer Specification

### 4.1 Pydantic Data Models (`contract_parser/schemas.py`)

```python
class FormType(StrEnum):
    """Legal instrument or form classification."""
    WIND_EASEMENT = "WIND_EASEMENT"
    SOLAR_LEASE = "SOLAR_LEASE"
    SOLAR_EASEMENT = "SOLAR_EASEMENT"
    STORAGE_EASEMENT = "STORAGE_EASEMENT"
    LAND_OPTION_PURCHASE = "LAND_OPTION_PURCHASE"
    PARTICIPATION_EASEMENT = "PARTICIPATION_EASEMENT"
    OTHER = "OTHER"


class PageSpan(BaseModel):
    """Physical 1-based page boundary span."""
    page_start: int = Field(ge=1, description="1-based starting physical page number")
    page_end: int = Field(ge=1, description="1-based ending physical page number")


class TemplateArticleSpan(BaseModel):
    """Section/Article discovered in Pass 1."""
    article_number: str = Field(description="Article/Section numbering (e.g. '1', 'Article II')")
    article_title: str = Field(description="Article/Section title (e.g. 'Easement', 'Taxes')")
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)


class TemplateExhibitSpan(BaseModel):
    """Exhibit or schedule attached to template discovered in Pass 1."""
    exhibit_id: str = Field(description="Identifier (e.g. 'Exhibit A', 'Schedule 1')")
    exhibit_title: str = Field(description="Title (e.g. 'Legal Description', 'Decommissioning Plan')")
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)


class TemplateStructureIndex(BaseModel):
    """Pass 1: Structured output model for template boundary discovery."""
    document_title: str = Field(description="Verbatim agreement title from header")
    country: str = Field(default="US")
    state_province: str = Field(description="Governing state/province or 'US' if general")
    energy_technology: str = Field(description="Primary technology (SOLAR, ONSHORE_WIND, STORAGE, THERMAL, CROSS_TECHNOLOGY)")
    form_type: FormType = Field(description="Classified form type")
    vintage_year: str = Field(description="Vintage year detected or '2024'")
    preamble_span: PageSpan | None = None
    recitals_span: PageSpan | None = None
    body_articles: list[TemplateArticleSpan] = Field(default_factory=list)
    quarantine_signatures_span: PageSpan | None = Field(
        default=None,
        description="Physical page span containing execution block, signature tables, and notary certificates to suppress from AST nodes",
    )
    exhibits: list[TemplateExhibitSpan] = Field(default_factory=list)


class PlaceholderVariable(BaseModel):
    """Extracted variable bracket from a template clause."""
    variable_name: str = Field(description="Name or description of placeholder, e.g., 'Payment Rate', 'County'")
    bracket_text: str = Field(description="Verbatim bracket token, e.g. '$[_____] per megawatt', '[County]'")
    data_type_hint: str = Field(default="STRING", description="Inferred type: CURRENCY, DURATION, DATE, STRING, NUMBER")


class RawTemplateClause(BaseModel):
    """Pass 2: Substantive extracted clause item returned by Gemini."""
    clause_label: str = Field(description="Label, e.g. '1.', '1.1', 'Section 2.3', 'Recital A'")
    clause_title: str | None = Field(default=None, description="Title/heading if present")
    numbering_scheme: NumberingScheme = Field(default=NumberingScheme.DECIMAL)
    document_zone: DocumentZone = Field(default=DocumentZone.BODY)
    verbatim_text: str = Field(description="100% verbatim clause text with fill-in brackets intact")
    placeholder_variables: list[PlaceholderVariable] = Field(default_factory=list)
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)


class TemplatePassExtraction(BaseModel):
    """Pass 2: Structured container of substantive boilerplate clauses."""
    clauses: list[RawTemplateClause] = Field(default_factory=list)


class TemplateRegistryRow(BaseModel):
    """Canonical metadata row for an approved regional master template."""
    model_config = ConfigDict(extra="ignore")

    template_id: str
    template_name: str
    country: str = "US"
    state_province: str
    energy_technology: EnergyTechnology
    form_type: FormType
    vintage_year: str
    revision: str = "v1"
    gcs_pdf_uri: str
    sha256_hash: str
    page_count: int
    clause_count: int = 0
    is_active: bool = True
    created_by: str
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class TemplateClauseRow(BaseModel):
    """Individual canonical AST clause node belonging to a master template."""
    model_config = ConfigDict(extra="ignore")

    template_id: str
    node_id: str
    parent_node_id: str | None = None
    sibling_order: int
    document_zone: DocumentZone
    canonical_path: str
    depth: int
    clause_label: str
    clause_title: str | None = None
    numbering_scheme: NumberingScheme
    verbatim_text: str
    placeholder_variables: list[PlaceholderVariable] = Field(default_factory=list)
    page_start: int
    page_end: int
    created_at: str = Field(default_factory=utc_now_iso)


class TemplateExtractionBundle(BaseModel):
    """Structured package returned by the two-pass template extraction engine."""
    metadata: TemplateRegistryRow
    clauses: list[TemplateClauseRow]
```

#### Canonical Path & Node ID Formatting Rules
- **Canonical Path (`canonical_path`):** Deterministic dot-delimited path indicating document hierarchy:
  - Preamble: `PREAMBLE`
  - Recitals: `RECITALS.A`, `RECITALS.B`
  - Body: `BODY.{article_clean}` (e.g. `BODY.1`), `BODY.{article_clean}.{section_clean}` (e.g. `BODY.1.1`, `BODY.2.3`)
  - Exhibits: `EXHIBIT_{id}.{section_clean}` (e.g. `EXHIBIT_A.1`, `EXHIBIT_B.2`)
- **Node ID (`node_id`):** Formed as `{template_id}#{canonical_path}` (e.g. `tpl_us_il_storage_storage_easement_2025_v1#BODY.1.1`).

---

### 4.2 Specialized Two-Pass Prompt Directives (`contract_parser/prompts.py`)

```python
TEMPLATE_STRUCTURE_DISCOVERY_PROMPT = """
You are an expert renewable energy legal analyst. Analyze this regional master contract template.
Return a structured JSON document index adhering strictly to the TemplateStructureIndex schema:
1. Document Title, Governing Jurisdiction (State/Country), Technology, and Form Type.
2. Exact page spans for PREAMBLE and RECITALS.
3. List of all numbered Articles/Sections in the main agreement body with starting and ending page numbers.
4. QUARANTINE SPAN: Identify the exact physical page bounds of the execution block, signature blocks, witness attestations, and notary public acknowledgment certificates (e.g. 'IN WITNESS WHEREOF' through 'State of __, County of __, ss:').
5. List of attached Exhibits, Schedules, or Addenda with page ranges.
Strictly ensure that attached exhibits appearing after signatures are captured as separate zones.
"""

TEMPLATE_SUBSTANTIVE_CLAUSE_PROMPT = """
You are an expert renewable energy legal draftsman. Extract 100% of the substantive boilerplate clauses from the designated pages adhering to the TemplatePassExtraction schema.
Rules:
1. Capture every article, section, subsection, and paragraph verbatim without summarizing.
2. PRESERVE FILL-IN PLACEHOLDERS: Retain all unpopulated brackets, underline blanks, and brackets exactly as written (e.g., '$[_____] per MW', 'within [30/60] days', '[County]').
3. Extract each bracket into the 'placeholder_variables' list with variable_name, bracket_text, and data_type_hint.
4. STRICT SIGNATURE SUPPRESSION: Do not generate any clause nodes for signature lines, officer titles, witness tables, or notary certificates falling within the quarantined signature span.
5. Accurately record page_start and page_end for every node.
"""
```

---

### 4.3 Multi-Pass Assembly & Extraction Coordinator

Rather than creating a detached microservice, template extraction integrates into the proven [contract_parser/gemini_parser.py](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/gemini_parser.py) and [contract_parser/bundle_assembler.py](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/bundle_assembler.py):

1. **`GeminiContractParser.extract_template(...)` in `gemini_parser.py`:**
   - Computes `sha256_hash` binary digest.
   - Executes Pass 1 with `TEMPLATE_STRUCTURE_DISCOVERY_PROMPT` using Gemini structured output returning `TemplateStructureIndex`.
   - Bounded Pass 2 invokes `TEMPLATE_SUBSTANTIVE_CLAUSE_PROMPT` across substantive page spans (skipping the quarantined signature span).
   - Calls `bundle_assembler.assemble_template_bundle(...)` to build hierarchy, assign `canonical_path`, compute depths, and assemble `TemplateExtractionBundle`.
2. **`bundle_assembler.assemble_template_bundle(...)` in `bundle_assembler.py`:**
   - Filters out any accidental signature/notary nodes.
   - Builds parent-child pointers (`parent_node_id`) based on numbering schemes and labels (`1.` -> `1.1` -> `1.1.1`).
   - Generates deterministic `node_id` keys (`{template_id}#{canonical_path}`).

---

### 4.4 Lakehouse Persistence in `ContractStorageService` (`contract_parser/storage.py`)

Methods added directly to `ContractStorageService`:

```python
def insert_template_bundle(self, bundle: TemplateExtractionBundle) -> None:
    """Idempotently persist template metadata and clauses into SQLite and BigQuery."""

def list_templates(
    self,
    country: str | None = None,
    state_province: str | None = None,
    energy_technology: str | None = None,
    form_type: str | None = None,
) -> list[TemplateRegistryRow]:
    """Retrieve active master templates filtered by taxonomy dimensions."""

def get_template_bundle(self, template_id: str) -> TemplateExtractionBundle | None:
    """Retrieve full template metadata and ordered clause AST by template_id."""

def get_template_pdf_path(self, template_id: str) -> Path | None:
    """Resolve physical template PDF path on local disk (under data/templates/ or samples/Templates/)."""

def seed_sample_templates(self, samples_dir: Path | None = None) -> list[TemplateRegistryRow]:
    """Cold-start auto-discovery scanning samples/Templates/ for baseline canonical forms."""
```

---

## 5. Security Perimeter & REST API Contracts

### 5.1 Route Inventory & RBAC Matrix

| Endpoint | Method | Role Allowed | Description | Telemetry Span |
| :--- | :---: | :---: | :--- | :--- |
| `/api/v1/templates/upload` | `POST` | `Admin` (`require_admin`) | Ingests PDF with taxonomy metadata, executes two-pass extraction, persists records. | `template.upload` |
| `/api/v1/templates` | `GET` | `Viewer`, `Admin` (`auth_dep`) | Lists templates filtered by country, state, tech, form. | `template.list` |
| `/api/v1/templates/{id}` | `GET` | `Viewer`, `Admin` (`auth_dep`) | Retrieves full template metadata and sorted clause AST. | `template.get` |
| `/api/v1/templates/{id}/pdf` | `GET` | `Viewer`, `Admin` (`auth_dep`) | Streams raw template PDF binary with range requests. | `template.stream_pdf` |
| `/api/v1/templates/seed` | `POST` | `Admin` (`require_admin`) | Discovers and seeds templates from `samples/Templates/`. | `template.seed` |
| `/template-studio` | `GET` | Public / Web | Serves the standalone Regional Template Studio HTML page. | N/A |

### 5.2 API Implementation in `contract_parser/app.py`

Adheres strictly to existing codebase authentication patterns:

```python
@app.post("/api/v1/templates/upload")
async def upload_template(
    file: UploadFile = File(...),
    country: str = Form("US"),
    state_province: str = Form(...),
    energy_technology: str = Form(...),
    form_type: str = Form(...),
    vintage_year: str = Form(...),
    revision: str = Form("v1"),
    current_user: UserContext = Depends(auth_dep),
):
    """Ingests a master legal template PDF, executes two-pass extraction, and persists records."""
    require_admin(current_user)
    pdf_bytes = await file.read()
    template_slug = f"tpl_{country.lower()}_{state_province.lower()}_{energy_technology.lower()}_{form_type.lower()}_{vintage_year}_{revision.lower()}"
    
    active_parser: GeminiContractParser = app.state.gemini_parser
    active_storage: ContractStorageService = app.state.storage

    with tracer.start_as_current_span("template.upload") as span:
        span.set_attribute("contract.template_id", template_slug)
        span.set_attribute("contract.state_province", state_province)
        span.set_attribute("contract.energy_technology", energy_technology)
        span.set_attribute("user.email", current_user.email)
        
        bundle = active_parser.extract_template(
            pdf_bytes=pdf_bytes,
            filename=file.filename or "template.pdf",
            template_id=template_slug,
            country=country,
            state_province=state_province,
            energy_technology=energy_technology,
            form_type=form_type,
            vintage_year=vintage_year,
            revision=revision,
            created_by=current_user.email,
        )
        active_storage.insert_template_bundle(bundle)
        return {"status": "SUCCESS", "template_id": bundle.metadata.template_id, "clause_count": len(bundle.clauses)}
```

---

## 6. Frontend: Dedicated Standalone Template Studio (`template_studio.html`)

### 6.1 Clean Architecture via Standalone UI
Rather than injecting hundreds of lines of template logic into the already massive 3,500-line `index.html` (which risks state contamination and DOM event collisions), the Template Studio is implemented as a **dedicated standalone web page**:
- File: [`contract_parser/static/template_studio.html`](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/static/template_studio.html)
- Route: `GET /template-studio`
- Navigation: A clean button added to the top navigation header of [contract_parser/static/index.html](file:///usr/local/google/home/prasannaankem/Code/Invenergy/contract_parser/static/index.html):
  ```html
  <a href="/template-studio" target="_blank" class="nav-link-btn" title="Open Regional Template Studio in a new window">
      <span>🏛️</span> Regional Template Studio ↗
  </a>
  ```
- **Session Sharing:** Automatically retrieves `localStorage.getItem("contract_parser_auth_token")` on load. If the user is logged in on `index.html`, they are instantly authenticated in the Template Studio with identical `Admin` / `Viewer` role permissions.
- **Multitasking Advantage:** Enables legal and land administration staff to snap the Executed Contracts window on the left half of their monitor and the Canonical Template Studio on the right half for frictionless side-by-side verification.

### 6.2 Studio Layout & Features
1. **Taxonomy Filter Bar:** Cascading dropdowns (`Country -> State -> Technology -> Form Type -> Vintage`) populated via `GET /api/v1/templates`.
2. **Left Panel: Canonical AST Hierarchy Tree:**
   - Search input filtering clause titles and verbatim text in real-time.
   - Pre-order recursive AST tree display with depth tags, canonical path chips (`BODY.1.1`), and placeholder badges (`[ 2 Variables ]`).
   - Clicking a placeholder badge expands a drawer showing variable names and data type hints.
3. **Right Panel: Synchronized PDF.js Viewport:**
   - Streams PDF binary from `/api/v1/templates/{id}/pdf` with Bearer auth token.
   - Clicking any clause card automatically scrolls the PDF viewport directly to `page_start`.
4. **Upload Master Template Modal (Admin Only):**
   - File picker supporting drag & drop.
   - Pre-fills detected taxonomy upon PDF selection.
   - If `profile.role !== 'admin'`, upload button is visually disabled (`opacity: 0.45; cursor: not-allowed`) with tooltip warning.

---

## 7. Automated Verification & Testing Strategy

### Test Suite: `tests/test_cr9_template_studio.py`

| Test ID | Test Function Name | Scope & Assertion Target |
| :--- | :--- | :--- |
| **UT-1** | `test_template_slug_and_pydantic_models` | Validates slug generation, regex normalization, and serialization of `TemplateRegistryRow` and `TemplateClauseRow`. |
| **UT-2** | `test_placeholder_variable_extraction_regex_and_schema` | Verifies that `$[_______]` and `[County]` brackets extract with accurate names and type hints without synthetically completing text. |
| **UT-3** | `test_signature_block_quarantine_and_suppression` | Asserts that zero nodes in `template_clauses` contain execution headers (`IN WITNESS WHEREOF`), signature lines, or notary certificates (`ss:`). |
| **UT-4** | `test_attached_exhibits_preservation_after_signatures` | Asserts that exhibits appearing physically after the signature quarantine range are preserved in the clause tree. |
| **UT-5** | `test_sqlite_template_storage_roundtrip` | Asserts that `insert_template_bundle`, `list_templates`, and `get_template_with_clauses` persist and retrieve records with tree ordering. |
| **UT-6** | `test_zero_operational_query_pollution` | Asserts that ingesting templates results in zero rows inserted into `clauses` or `special_conditions` tables. |
| **UT-7** | `test_template_api_rbac_enforcement` | Asserts that `POST /api/v1/templates/upload` returns HTTP 403 for `Viewer` tokens and HTTP 200 for `Admin` tokens. |
| **UT-8** | `test_local_samples_cold_start_seeding` | Verifies that `seed_sample_templates()` auto-discovers and indexes all 6 PDF forms in `samples/Templates/`. |
| **IT-1** | `test_end_to_end_template_ingestion_and_pdf_stream` | Simulates uploading a template PDF, verifies extraction bundle, and tests `/api/v1/templates/{id}/pdf` binary streaming. |
| **IT-2** | `test_template_studio_ui_markup_and_mode_switching` | Verifies DOM elements for mode switcher, taxonomy dropdowns, and viewer role disabling in `contract_parser/static/index.html`. |
| **IT-3** | `test_opentelemetry_template_trace_attributes` | Uses `InMemorySpanExporter` to verify span hierarchy (`template.upload` -> `template.extract`) and token metrics. |

---

## 8. Rollout & Migration Plan

1. **Phase 1: DDL & Database Migration:**
   Apply BigQuery DDL updates (`template_registry`, `template_clauses`, and `*_latest` views) in `scripts/sql/`. Update SQLite initialization in `storage.py`.
2. **Phase 2: Schemas & Extraction Pipeline:**
   Implement `FormType`, `TemplateRegistryRow`, `TemplateClauseRow` in `schemas.py`. Implement two-pass extraction directives in `prompts.py` and `gemini_parser.py`.
3. **Phase 3: Storage & REST Endpoints:**
   Implement template storage methods in `storage.py`. Implement REST routes in `app.py` with Zero-Trust RBAC guards.
4. **Phase 4: Frontend Template Studio & PDF Viewport:**
   Implement workspace mode switcher, taxonomy bar, and clause viewer in `index.html`. Wire PDF.js viewport jumping.
5. **Phase 5: Automated Verification & Documentation:**
   Run `tests/test_cr9_template_studio.py` and regression suite. Append to `Changelog.md`.
