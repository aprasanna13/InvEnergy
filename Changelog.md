# Changelog

## [0.1.0] - 2026-09-30
- Initialized Gemini-First Hierarchical Contract Parsing & Context Preservation Platform (Session 1) in GCP project `pr-tftest` per `Design/DESIGN_DOC.md`.
- Provisioned Cloud Storage bucket `gs://pr-tftest-contract-intelligence` and BigQuery dataset `pr-tftest:contract_intelligence`.
- Added 10-file thin-glue implementation (`pyproject.toml`, `Changelog.md`, `contract_parser/__init__.py`, `contract_parser/config.py`, `contract_parser/schemas.py`, `contract_parser/gemini_parser.py`, `contract_parser/storage.py`, `contract_parser/app.py`, `contract_parser/static/index.html`, `tests/test_pipeline.py`).
- Executed live ingestion of `Synthetic_Accommodation_Agreement.pdf` (`doc_70fc075850990305`) into `gs://pr-tftest-contract-intelligence` and BigQuery `pr-tftest.contract_intelligence`, and hardened broken cross-reference flagging, authoritative BigQuery reads, lazy SQLite initialization, and suffix byte-range PDF streaming.
- Extracted Invenergy brand design tokens, typography, and component recipes from `https://invenergy.com/` into `DESIGN.md` (`Design/DESIGN.md`) and applied the Invenergy UI design system and official vector wordmark to `contract_parser/static/index.html`.

## [0.2.0] - 2026-10-02
- Implemented Change Request CR-1 (`R1`: Landowner "Special Conditions" & Physical Site Constraint Extraction) per `Design/CR1_LANDOWNER_SPECIAL_CONDITIONS.md`.
- Added `FlagCode.LANDOWNER_SPECIAL_CONDITION`, the 7-category `ConstraintCategory` taxonomy, and the 16-column `SpecialConditionRow` schema (`special_conditions` / `special_conditions.csv`) in `contract_parser/schemas.py`, plus roll-up fields on `ClauseRow` (`has_special_condition`, `special_condition_count`, 32 columns) and `DocumentRegistryRow` (`special_conditions_count`, 13 columns).
- Extended `contract_parser/gemini_parser.py` with Rule 6 multimodal extraction for physical/operational landowner constraints, parent-vs-child sandwich leaf deduplication, a deterministic two-signal regex fallback guardrail (`_PHYSICAL_CONSTRAINT_RESTRICTION_PATTERN` + `_PHYSICAL_CONSTRAINT_ASSET_OR_METRIC_PATTERN`), and mandatory `HITLStatus.FLAGGED_FOR_REVIEW` routing.
- Extended `contract_parser/storage.py` and `contract_parser/app.py` with the 5th normalized table (`special_conditions` + `special_conditions_latest` BigQuery view, SQLite table, and `special_conditions.csv` export), idempotent `ALTER TABLE` / BigQuery schema migrations, and cascading HITL review approval from `PATCH /api/v1/documents/{document_id}/clauses/{node_id}` to attached `SpecialConditionRow` records.
- Updated `contract_parser/static/index.html` with the `Site Constraints` tab, `⚡ N Site Constraint(s)` clause badges, `special_conditions.csv` download button, and structured constraint cards, and expanded `tests/test_pipeline.py` to cover all CR-1 invariants.

## [0.3.0] - 2026-10-02
- Implemented Change Request CR-2 (`R3`: Project-Level & Multi-Contract Portfolio Hierarchy across 5 Energy Technologies) per `Design/CR2_PROJECT_PORTFOLIO_HIERARCHY.md`.
- Expanded `contract_parser/schemas.py` to the 7-table portfolio schema: `EnergyTechnology` (`ONSHORE_WIND`, `SOLAR`, `STORAGE`, `TRANSMISSION`, `GEOTHERMAL`), cross-technology `ConstraintCategory` taxonomy, `ProjectRow` (`projects`, 12 columns), `LandownerRow` (`landowners`, 10 columns), `CreateProjectRequest`, and portfolio foreign keys on `DocumentRegistryRow` (18 columns) and `SpecialConditionRow` (18 columns).
- Extended `contract_parser/gemini_parser.py` with automatic Grantor (`grantor_landowner_name`) and Grantee (`grantee_entity_name`) counterparty extraction from contract preambles/recitals, deterministic QRM landowner entity resolution (`_make_landowner_id`), and portfolio stamping across `SpecialConditionRow` records.
- Extended `contract_parser/storage.py` and `contract_parser/app.py` with BigQuery (`pr-tftest.contract_intelligence`) and SQLite tables/views (`projects` / `projects_latest`, `landowners` / `landowners_latest`), 5 seeded Oracle ERP projects across all 5 technologies, multi-contract and multi-parcel (`EXHIBIT_A.PARCEL_*`) landowner roll-ups, and REST endpoints (`GET /api/v1/projects`, `POST /api/v1/projects`, `GET /api/v1/projects/{project_id}/landowners`, `GET /api/v1/landowners`, `GET /api/v1/portfolio/search`).
- Updated `contract_parser/static/index.html` with the Top Portfolio Hierarchy Bar (`Technology` -> `Project` -> `Landowner` -> `Contract`), Pre-Upload Target Project selector, New ERP Project modal, and cross-project `Portfolio Search` tab, and added `test_cr2_project_portfolio_hierarchy_and_centralized_search` in `tests/test_pipeline.py`.

## [0.4.0] - 2026-10-03
- Implemented Change Request CR-3 (`R6`: Subcontractor / Construction Field Crew "Do-Not-Disturb" Checklist) per `Design/CR3_SUBCONTRACTOR_DND_CHECKLIST.md`.
- Expanded `contract_parser/schemas.py` with `ConstructionTrade`, `DNDSeverityLevel`, `DNDDispatchClearance`, `DNDDispatchReadiness`, `DNDChecklistItem` (20 fields), `DNDChecklistSignoffRow` (`dnd_checklist_signoffs`, 12 columns), `DND_SIGNOFF_CSV_COLUMNS`, `CreateDNDSignoffRequest`, `DNDChecklistBundle`, and deterministic DND trade/severity synthesis helpers (`classify_dnd_condition` and `build_dnd_checklist_item`).
- Extended `contract_parser/storage.py` and `contract_parser/app.py` (`v0.4.0`) to the 8-table BigQuery (`pr-tftest.contract_intelligence.dnd_checklist_signoffs` + `dnd_checklist_signoffs_latest`) and SQLite (`./data/contract_intelligence.db`) architecture, adding `dnd_checklist_signoffs.csv` export, `GET /api/v1/documents/{document_id}/dnd-checklist` (with unfiltered contract-level `dispatch_readiness` interlock and optional trade/severity filtering), and `POST /api/v1/documents/{document_id}/dnd-checklist:signoff`.
- Updated `contract_parser/static/index.html` with the interactive `Field Crew DND` tab (`#tab-dnd`), Dispatch Readiness Interlock Banner (`HOLD_PENDING_HITL` vs `READY_FOR_DISPATCH`), construction trade and severity filters, 1-click Land Agent HITL clearance (`Approve & Clear for Field Dispatch`), and the Pre-Job Tailgate Briefing Sign-Off panel & audit log, and added `test_cr3_subcontractor_dnd_checklist_and_signoff` in `tests/test_pipeline.py`.

## [0.5.0] - 2026-10-04
- Implemented Change Request CR-4 (BigQuery Conversational Analytics Data Agent Chat Integration) per `Design/CR4_BIGQUERY_DATA_AGENT_CHAT.md`, and fortified CR-2 corporate suffix entity resolution (`canonical_party_key`), batch `_list_special_conditions_for_documents` portfolio search, and multi-contract `parcel_summary` roll-up transitions.
- Added `DEFAULT_BQ_DATA_AGENT_URN`, `bq_data_agent_urn`, and `resolve_data_agent_resource()` to `PipelineConfig` in `contract_parser/config.py` targeting `urn:agent:projects-255093976233:projects:255093976233:locations:us:geminidataanalytics:dataAgents:agent_f8454b44-a4aa-4c94-accf-245b5e6b1f11` (`projects/255093976233/locations/us/dataAgents/agent_f8454b44-a4aa-4c94-accf-245b5e6b1f11`).
- Added `AgentChatRequest`, `AgentDocumentLink`, `AgentChatResponse`, and `parse_data_agent_events()` in `contract_parser/schemas.py` to normalize `geminidataanalytics.googleapis.com/v1beta` `systemMessage` events (`THOUGHT`, `FINAL_RESPONSE`, `data.generatedSql`, `data.result`, `FOLLOWUP_QUESTIONS`, fallback row summaries, and bidirectional `document_id` / `filename` PDF link resolution).
- Added `_build_local_doc_filename_lookup`, `query_bigquery_data_agent`, `GET /api/v1/agent/info`, and non-blocking `POST /api/v1/agent/chat` (`asyncio.to_thread`) in `contract_parser/app.py` (`v0.5.0`).
- Updated `contract_parser/static/index.html` with the bottom-right `#bq-agent-fab` launcher button and `#bq-agent-window` floating chat window (with starter prompt chips, inline BigQuery result tables, direct `/api/v1/documents/{document_id}/pdf` links, 1-click `Load in Workbench` sync, and collapsible SQL/reasoning trace), and added `test_cr4_bigquery_data_agent_urn_and_chat_proxy` in `tests/test_pipeline.py`.

## [0.5.1] - 2026-10-05
- Added a static authentication login overlay (`#login-overlay`) with Invenergy brand styling in `contract_parser/static/index.html` gating the workspace behind credentials (`google123` / `google123`).
- Added browser session state management (`sessionStorage`) with `checkAuthSession()`, `attemptLogin()`, and `handleLogout()`.
- Added user profile indicator (`👤 google123`) with a "Sign Out" button to the executive header toolbar.
- Wrapped the application workspace and floating agent launcher inside `#app-shell` to keep all platform features locked until authentication succeeds.
- Added UI test assertions in `tests/test_pipeline.py` verifying the presence of `#login-overlay`, input elements, and credential hints.
- Removed the visible demo credentials hint text from the bottom of `#login-overlay` in `contract_parser/static/index.html`.
- Created containerization manifests ([Dockerfile](file:///usr/local/google/home/prasannaankem/Code/Invenergy/Dockerfile), [.dockerignore](file:///usr/local/google/home/prasannaankem/Code/Invenergy/.dockerignore)) configured for Python 3.12, dynamic `$PORT` binding on `0.0.0.0`, and `/tmp/data` ephemeral local storage.
- Deployed the application to Google Cloud Run in project `pr-tftest` (region `us-central1`, service `contract-parser`) with service URL `https://contract-parser-255093976233.us-central1.run.app`.

## [0.5.2] - 2026-10-06
- Updated primary extraction model to `gemini-3.8-flash` in `contract_parser/config.py` with `location="global"` and fallback model set to `gemini-3.1-pro-preview`.
- Verified live multimodal PDF parsing on Vertex AI / Agent Platform with `GeminiContractExtraction` schema, reducing extraction latency from 180–300s to ~59s without truncation errors.
- Updated `contract_parser/gemini_parser.py` docstrings and `tests/test_pipeline.py` schema config assertions.
- Reverted default extraction model back to `gemini-3.1-pro-preview` in `contract_parser/config.py` and Cloud Run service environment variables, retaining `gemini-3.8-flash` as fallback engine.
- Deployed Cloud Run revision `contract-parser-00004-lml` serving 100% of traffic on `https://contract-parser-255093976233.us-central1.run.app`.

## [0.5.3] - 2026-10-06
- Implemented deterministic document tree ordering (`sort_clauses_in_document_order`) in `contract_parser/schemas.py` and automatic post-instantiation enforcement on `ParsedContractBundle`.
- Organized contract hierarchy traversal via depth-first pre-order sequencing (`PREAMBLE` -> `RECITALS` -> `BODY` -> `SIGNATURES` -> `EXHIBIT_OR_SCHEDULE` -> `AMENDMENT`), placing child clauses (`1.1`, `(a)`) immediately under their parent sections rather than grouping by depth.
- Added automatic parentage inference fallback from `canonical_path` hierarchy (e.g. `BODY.7.a` -> parent `BODY.7`) when `parent_node_id` is null.
- Refactored SQLite (`_get_bundle_from_sqlite`) and BigQuery (`get_bundle_from_bigquery`) queries in `contract_parser/storage.py` to sort by `page_start ASC, sibling_order ASC, node_id ASC` instead of `depth ASC`.
- Extended `contract_parser/static/index.html` with client-side tree ordering (`sortClausesInDocumentOrder`) in `renderLeftPane()` for consistent visual hierarchy in all tabs and filter states.
- Added comprehensive unit test `test_clause_hierarchical_document_ordering` in `tests/test_pipeline.py`.

## [0.6.0] - 2026-10-06
- Implemented Change Request CR-5: Semantic Zone Multi-Pass Extraction Pipeline per Option 3A architecture.
- Pass 1 (Structure & Zone Discovery): Uses fast model (`gemini-3.8-flash`, temperature 0.0) to extract document title, contracting counterparties, effective date, physical body page bounds (`body_start_page`..`body_end_page`), signature execution page spans, signer entities, and attached exhibits with physical page intervals and modality in ~16s.
- Pass 2 (Agreement Body Extraction) & Pass 3 (Exhibits & Schedules Extraction): Concurrent execution via `concurrent.futures.ThreadPoolExecutor(max_workers=2)` using primary reasoning model `gemini-3.1-pro-preview` (with `gemini-3.8-flash` fallback).
- Introduced lean extraction schemas (`ExtractedClauseItem`, `ExtractedDefinedTermItem`, `ExtractedSpecialConditionItem`) and bounded reasoning (`thinking_budget=1024`) with `max_output_tokens=65536`, eliminating output token exhaustion and accelerating extraction latency to ~219s.
- Deterministic Multi-Pass Bundle Assembly (`bundle_assembler.py`): Preamble preservation and fallback synthesis, signature execution block quarantine (`DEFERRED_MODALITY_PLACEHOLDER`), visual plat map / CAD drawing stubs (`EXHIBIT_D.STUB`), zone-guard filtering, and prioritized defined terms deduplication (Body > Exhibits).
- Live Benchmark on Scanned 33-Page Solar Lease (`SOKGRN0003`):
  * Total clauses extracted: 127 clauses (577% increase over 22-clause single-pass baseline, exceeding >65 target).
  * Body section coverage: 100% across Sections 1 through 14 (`PREAMBLE.1` through `BODY.14.17`).
  * Attached exhibits coverage: Complete extraction across all 5 exhibits (Exhibits A, A-1, B, C, D).
  * Defined terms dictionary: 57 terms (exceeding >30 target).
  * Landowner special conditions: 12 distinct physical site constraints and operational covenants.
  * Wall-clock latency: 219.69s (well within < 360s ceiling).
- Full regression suite passing: 22 unit, concurrency lifecycle, integration, and pipeline tests.
- Deployed Cloud Run revision `contract-parser-00006-jqd` in `pr-tftest` (region `us-central1`) serving 100% of traffic on `https://contract-parser-255093976233.us-central1.run.app`.

## [0.7.0] - 2026-10-06
- Implemented Change Request CR-6: Universal Client-Server Event Progress & Extraction Milestone System.
- Added native `window.fetch` telemetry interceptor in `contract_parser/static/index.html` scoped strictly to `/api/v1/` routes, establishing automatic progress tracking across 100% of internal server endpoints (document switching, portfolio search, DND signoff, clause editing, BigQuery agent queries, and contract uploads).
- Built atomic request reference counter (`activeServerRequests`) to coordinate concurrent background server dispatches and suppress false-positive alerts on debounced search cancellations (`AbortError`).
- Implemented Top-Edge Slim Ambient Progress Bar (`#global-top-progress`) with gradient `#118751` to `#FFCF0B`, smooth width creep, and subtle background pane dimming (`.left-pane, .right-pane.pane-dimmed`).
- Implemented Center Glassmorphism Extraction Modal (`#extraction-modal-overlay`) with `backdrop-filter: blur(14px)`, live millisecond stopwatch (`⏱ Elapsed: MM:SS / ~03:30 est.`), shimmering pulse track, and 4-phase milestone progression:
  * Milestone 1: Binary Integrity & Ingestion Staging (0-3.2s)
  * Milestone 2: Pass 1: Structure & Zone Discovery (3.2-20s)
  * Milestone 3: Pass 2 & 3: Parallel Zone Deep Extraction (20s until server resolution)
  * Milestone 4: Assembly, Tree Ordering & BigQuery Sync (resolution hold and auto-dismiss)
- Implemented deterministic error teardown protocol: immediately dismisses extraction modal, resets top progress bar (`abortGlobalProgress`), restores `#upload-btn`, undims workspace panes, and surfaces silent error toast banner (`#global-error-toast`) on HTTP 4xx/5xx, Cloud Run 504 timeouts, or network drops.
- Enforced strict zero-audio mandate across all frontend components (zero Web Audio API calls, zero `<audio>` tags, zero audio cues).
- Added comprehensive hermetic test suite `tests/test_cr6_progress_system.py` verifying DOM markup elements, CSS styling keyframes, telemetry interceptor logic, upload button parity, and zero audio enforcement.

## [0.7.1] - 2026-10-06
- Added official Invenergy icon assets (`favicon.ico`, `favicon-32x32.png`, `apple-touch-icon.png`) to `contract_parser/static/`.
- Configured browser favicon and touch icons via `<link rel="icon">` tags in `contract_parser/static/index.html`.
- Implemented dedicated FastAPI route handlers in `contract_parser/app.py` for `/favicon.ico`, `/favicon-32x32.png`, and `/apple-touch-icon.png` returning proper MIME types and Cache-Control headers.
- Integrated the Invenergy icon into the page navigation header brand title (`.brand-title`) and the authentication login modal in `contract_parser/static/index.html`.

## [0.7.2] - 2026-10-07
- Implemented role-based DOM disabling for new read-only viewer account (`invtest123` / `invtest123`).
- Added `applyUserPermissions(user)` in `contract_parser/static/index.html` to dynamically configure DOM elements based on authenticated session:
  * When logged in as `invtest123`:
    - Disables file input chooser (`#pdf-upload-input`) with `opacity: 0.45` and `cursor: not-allowed`.
    - Disables upload button (`#upload-btn`) with `opacity: 0.45` and `cursor: not-allowed`.
    - Updates executive header user badge to `👤 invtest123 (Read-Only)`.
    - Adds runtime guard in `uploadPdf()` and `hideExtractionModal()` preventing re-enablement or accidental execution.
  * When logged in as `google123` (Admin):
    - Restores full read/write capabilities, active file selector, and `Upload & Parse PDF` button.
    - Updates user badge to `👤 google123`.
- Added automated hermetic test `test_invtest123_read_only_dom_controls` in `tests/test_cr6_progress_system.py`.

## [0.7.3] - 2026-10-07
- Updated login failure notification message in `contract_parser/static/index.html` to: `"Please enter valid user name and pwd."`.
- Removed `google123` from UI screen display:
  * Updated top navigation header badge from `👤 google123` to `👤 Workspace User`.
  * Updated `applyUserPermissions` to set user badge to `👤 Workspace User`.
  * Removed credential reference from the login overlay markup comments.

## [0.7.4] - 2026-10-07
- Fixed Ask Agent floating action button (`#bq-agent-fab`) and chat window (`#bq-agent-window`) display toggle lifecycle:
  * Added `display: flex !important;` to `.bq-agent-window.open` CSS rule to prevent inline styles from suppressing modal rendering.
  * Synchronized `win.style.display = isOpen ? "flex" : "none"` directly in `toggleBqAgentWindow()` upon class toggle and set focus on `#bq-agent-input`.
  * Updated `handleLogout()` and `checkAuthSession()` to cleanly remove `.open` and set `style.display = "none"`, preventing stale hidden states across authentication lifecycles.
  * Added regression test `test_bq_agent_fab_window_toggle_lifecycle` in `tests/test_cr6_progress_system.py`.

## [0.7.5] - 2026-10-07
- Added persistent enterprise AI legal disclaimer in BigQuery Conversational Agent chat window (`#bq-agent-window`):
  * Added `.bq-agent-disclaimer` CSS component pinned beneath the input bar (`.bq-agent-input-bar`) styled with Invenergy design system tokens (`var(--neutrals-gray)` text, `var(--neutrals-white)` surface, 11px typography).
  * Embedded notice copy: `"AI can make mistakes. Please verify all terms and figures against original executed agreements."`
  * Added automated regression test `test_bq_agent_disclaimer_element` in `tests/test_cr6_progress_system.py`.


## [0.8.0] - 2026-10-07
- Implemented Enterprise Passwordless Email Link Authentication and Domain-Gated Access Control (CR-7).
- Replaced static demo credentials (`google123`/`invtest123`) with Google Cloud Identity Platform (Identity Toolkit REST API) passwordless magic link dispatch.
- Enforced strict corporate domain whitelisting allowing only `@invenergy.com` and `@google.com` identities with instant client-side regex feedback and server-side HTTP 403 gate.
- Built sliding-window in-memory rate limiting on link generation: max 5 requests per 10 minutes per client IP (parsed via `X-Forwarded-For`), and max 3 requests per 15 minutes per recipient email.
- Enforced Zero-Trust Bearer ID token validation on all `/api/v1/*` routes with public bypasses reserved strictly for `/api/v1/auth/config` and `/api/v1/auth/request-link`.
- Implemented Role-Based Access Control (RBAC):
  * Admin role: `@google.com` identities and `ADMIN_EMAIL_WHITELIST` accounts receive full write permissions (PDF upload, GCS ingestion, clause HITL review approvals).
  * Viewer role: `@invenergy.com` identities receive read-only access (search, PDF viewing, CSV exports, BigQuery Conversational Agent) and are blocked from mutations with HTTP 403.
- Integrated non-repudiation identity stamping: `reviewed_by` assigned from authenticated `current_user.email` on clause reviews, and `foreman_name` on DND tailgate signoffs.
- Overhauled frontend authentication and token injection in `contract_parser/static/index.html`:
  * Replaced static password form with passwordless email dispatch card and inline rate-limit feedback.
  * Added cross-device and incognito sign-in confirmation modal with URL parameter sanitization (`apiKey` and `oobCode` stripped via `window.history.replaceState`).
  * Implemented universal `window.fetch` interceptor automatically injecting active Bearer ID tokens and refreshing expiring tokens.
  * Integrated PDF.js Web Worker `httpHeaders` authorization passing for stream-protected contract PDFs.
  * Implemented authenticated fetch-blob download workflow for CSV exports.
  * Added dynamic role-based UI adaptation (`applyUserRolePermissions`) disabling file inputs, upload buttons, and displaying user role badges (`👤 email (Admin)` vs `👤 email (Viewer)`).
- Added comprehensive hermetic test suite `tests/test_cr7_auth.py` and updated regression test suites across the repository.

## [0.8.1] - 2026-10-07
- Fixed JavaScript syntax error in `contract_parser/static/index.html` where an unclosed brace in `initAuth()` caused an `Unexpected end of input` script compilation error.
- Restored event handling on the "Send Sign-In Link" button (`#login-submit-btn`), ensuring `requestSignInLink()` executes correctly.
- Added explicit `DOMContentLoaded` event listener binding to `#login-submit-btn` for event handling resilience.

## [0.8.2] - 2026-10-07
- Passed `"canHandleCodeInApp": True` in `accounts:sendOobCode` payload in `contract_parser/auth.py`. This instructs Google Identity Toolkit to construct the action link to return directly to the web application (`continueUrl`), preventing interception by the default Firebase Hosting action handler (`/__/auth/action`) which caused `ProjectConfigService.GetProjectConfig` credential errors.
- Provisioned the official Firebase Web App (`InvEnergy Contract Parser`) in project `pr-tftest` and configured dedicated Web App API Key (`AIzaSyAfls...`).




