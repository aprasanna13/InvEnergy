# Technical Implementation Proposal: Gemini-First Hierarchical Contract Parser (Session 1)

## 1. Executive Summary & Gemini-First Scope

This proposal implements the approved [Design Document](file:///usr/local/google/home/prasannaankem/Code/Invenergy/Design/DESIGN_DOC.md) using **Option 1: 100% Gemini-First Multimodal Architecture** powered by `gemini-3.1-pro-preview` / `gemini-2.5-flash` via the unified `google-genai` SDK, paired with **Option 3B: Append-Only BigQuery Streaming (`insert_rows_json` + `QUALIFY ROW_NUMBER() = 1`)** and **Option 4B: Mozilla `pdf.js` Split-Screen Review UI**.

* **Gemini Does 100% of the Parsing & QA (Zero External OCR or PyMuPDF):** The PDF stored in Google Cloud Storage (`gs://<bucket>/raw/<document_id>/<filename>.pdf`) is passed directly to `gemini-3.1-pro-preview` (with a 1M-token input context window and `response_schema=GeminiContractExtraction`). Gemini visually reads the scanned pages, strips headers/footers and struck-through text, untangles two-column blocks, records physical 1-indexed PDF page numbers, builds the $N$-tier hierarchy tree, disambiguates `(i)` Roman vs. Alpha markers, synthesizes full `reconstructed_context_text` (`Ancestor Preambles + Sub-Clause + Interleaved/Trailing Carve-Outs`) for single and multi-level sandwich clauses, extracts contracting parties, effective date, `defined_terms`, and `exhibits_catalog`, quarantines handwritten signature blocks and CAD drawings as `PLACEHOLDER_FOR_REVIEW`, and assigns the 6 HITL flag codes.
* **Append-Only BigQuery Streaming (`insert_rows_json` + `updated_at` Versioning):** Every ingestion and every human review approval/edit streams a new immutable row version into BigQuery (`updated_at`, `reviewed_by`, `review_notes`). Reads and CSV exports deduplicate via `QUALIFY ROW_NUMBER() OVER (PARTITION BY document_id, node_id ORDER BY updated_at DESC) = 1`, eliminating BigQuery's 90-minute streaming buffer lock on DML `UPDATE`s while preserving a full audit trail.
* **Python Is Strictly Thin Glue (10 Files Total):** Python handles only file upload to GCS, calling `google-genai` (with retry/backoff and page-windowed continuation for oversized contracts), streaming the 4 tables (`documents`, `clauses`, `defined_terms`, `exhibits_catalog`) to BigQuery and `utf-8-sig` CSV files, and serving the FastAPI + `pdf.js` split-screen Web UI.

---

## 2. Lean Repository Structure (10 Files)

```text
/usr/local/google/home/prasannaankem/Code/Invenergy/
├── pyproject.toml                        # 1. Dependencies: google-genai, google-cloud-storage, google-cloud-bigquery, fastapi, uvicorn, pydantic
├── Changelog.md                          # 2. Append-only project change log
├── contract_parser/
│   ├── __init__.py                       # 3. Package marker & exports
│   ├── config.py                         # 4. Env config (Project, Location="global", Model="gemini-3.1-pro-preview", GCS Bucket, BQ Dataset)
│   ├── schemas.py                        # 5. Pydantic models for Gemini Structured Output, Append-Only BQ & CSVs
│   ├── gemini_parser.py                  # 6. Thin google-genai wrapper passing gs:// PDF + prompt + GeminiContractExtraction schema
│   ├── storage.py                        # 7. Thin GCS + Append-Only BigQuery (QUALIFY dedup) + CSV persistence glue
│   ├── app.py                            # 8. FastAPI server & CLI entrypoint
│   └── static/
│       └── index.html                    # 9. Two-pane HITL Review Web UI (Hierarchy Tree & Inline Edits on left, pdf.js Viewer on right)
└── tests/
    └── test_pipeline.py                  # 10. End-to-end schema & pipeline verification tests
```

---

## 3. Core Glue Modules

1. **`contract_parser/config.py`:**
   * Configures the unified Google Gen AI SDK (`GOOGLE_GENAI_USE_ENTERPRISE=true`, `location="global"`, `gemini_model="gemini-3.1-pro-preview"` with configurable option for `"gemini-2.5-flash"`), GCS bucket name, and BigQuery dataset ID.
2. **`contract_parser/schemas.py`:**
   * Defines `ClauseRow` (30 columns: 27 structural/context columns + `updated_at`, `reviewed_by`, `review_notes`), `DefinedTermRow` (8 columns), `ExhibitCatalogRow` (10 columns), `DocumentRegistryRow` (12 columns including `contracting_parties_json` and `effective_date`), `ClauseReviewRequest`, and `GeminiContractExtraction`.
3. **`contract_parser/gemini_parser.py`:**
   * Initializes `genai.Client()` and calls `client.models.generate_content(model=config.gemini_model, contents=[pdf_part, prompt], config=GenerateContentConfig(response_mime_type="application/json", response_schema=GeminiContractExtraction, temperature=0.0))` with exponential backoff and chunked page-window continuation if `FinishReason.MAX_TOKENS` is encountered on large contracts.
4. **`contract_parser/storage.py`:**
   * Uploads the source PDF to `gs://<bucket>/raw/<document_id>/<filename>.pdf`.
   * Streams append-only rows into `documents`, `clauses`, `defined_terms`, and `exhibits_catalog` in BigQuery (`insert_rows_json`) and local SQLite mirror.
   * Queries deduplicated latest rows (`QUALIFY ROW_NUMBER() OVER (PARTITION BY document_id, node_id ORDER BY updated_at DESC) = 1`) and exports `clauses.csv`, `defined_terms.csv`, and `exhibits_catalog.csv` (`utf-8-sig`) to `gs://<bucket>/exports/<document_id>/`.
   * On human review in the Web UI, appends a new `ClauseRow` version with `hitl_status = APPROVED_BY_HUMAN` (plus any reviewer text edits, `reviewed_by`, `review_notes`, and fresh `updated_at`) and refreshes `clauses.csv`.
5. **`contract_parser/app.py` & `contract_parser/static/index.html`:**
   * FastAPI backend and two-pane Web UI supporting drag-and-drop PDF upload, GCS batch ingestion, hierarchy exploration, `verbatim_text` vs. `reconstructed_context_text` toggling, inline HITL text editing and approval, CSV downloads, and smooth physical-page scrolling via Mozilla `pdf.js`.

