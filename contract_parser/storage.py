"""Thin GCS, Append-Only BigQuery (Option 3B QUALIFY deduplication), CSV, and local SQLite persistence glue."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
import sqlite3
from pathlib import Path

from google.cloud import bigquery, storage

from contract_parser.bundle_assembler import (
    assemble_template_bundle,
    extract_template_placeholders,
)
from contract_parser.config import PipelineConfig
from contract_parser.gemini_parser import _make_landowner_id, canonical_party_key
from contract_parser.schemas import (
    BATCH_JOB_ITEMS_COLUMNS,
    BATCH_JOBS_COLUMNS,
    CLAUSE_CSV_COLUMNS,
    DEFINED_TERM_CSV_COLUMNS,
    DND_SIGNOFF_CSV_COLUMNS,
    DOCUMENT_REGISTRY_COLUMNS,
    EXHIBIT_CATALOG_CSV_COLUMNS,
    LANDOWNER_CSV_COLUMNS,
    PROJECT_CSV_COLUMNS,
    SPECIAL_CONDITION_CSV_COLUMNS,
    TEMPLATE_CLAUSES_COLUMNS,
    TEMPLATE_REGISTRY_COLUMNS,
    BatchItemStatus,
    BatchJobItemRow,
    BatchJobRow,
    BatchJobStatus,
    ClauseReviewRequest,
    ClauseRow,
    ClauseVarianceDetailRow,
    ConstructionTrade,
    ContractVarianceBundle,
    ContractVarianceRow,
    CreateDNDSignoffRequest,
    DefinedTermRow,
    DNDChecklistBundle,
    DNDChecklistItem,
    DNDChecklistSignoffRow,
    DNDDispatchClearance,
    DNDDispatchReadiness,
    DNDSeverityLevel,
    DocumentRegistryRow,
    DocumentZone,
    EnergyTechnology,
    ExhibitCatalogRow,
    FormType,
    HITLStatus,
    IngestionStatus,
    LandownerRow,
    NumberingScheme,
    PageSpan,
    ParsedContractBundle,
    PlaceholderVariable,
    ProjectRow,
    RawTemplateClause,
    SpecialConditionRow,
    TemplateClauseRow,
    TemplateExtractionBundle,
    TemplateRegistryRow,
    TemplateStructureIndex,
    VarianceRiskLevel,
    VarianceSummary,
    VarianceType,
    build_dnd_checklist_item,
    utc_now_iso,
)
from contract_parser.telemetry import get_tracer

logger = logging.getLogger(__name__)
tracer = get_tracer("contract_parser.storage")


def compute_document_id(pdf_bytes: bytes) -> str:
    """Compute a deterministic SHA-256 content-addressed document_id."""
    digest = hashlib.sha256(pdf_bytes).hexdigest()[:16]
    return f"doc_{digest}"


DEFAULT_ERP_PROJECTS: list[dict[str, object]] = [
    {
        "project_id": "prj_cedar_lantern_wind",
        "project_name": "Cedar Lantern Wind Energy Center",
        "energy_technology": EnergyTechnology.ONSHORE_WIND,
        "erp_project_code": "ERP-WND-001",
        "state_province": "IL",
        "county": "McLean",
        "target_capacity_mw": 250.0,
    },
    {
        "project_id": "prj_sun_ridge_solar",
        "project_name": "Sun Ridge Solar & Agrivoltaics",
        "energy_technology": EnergyTechnology.SOLAR,
        "erp_project_code": "ERP-SOL-002",
        "state_province": "OH",
        "county": "Hardin",
        "target_capacity_mw": 180.0,
    },
    {
        "project_id": "prj_prairie_vault_storage",
        "project_name": "Prairie Vault BESS Storage",
        "energy_technology": EnergyTechnology.STORAGE,
        "erp_project_code": "ERP-STR-003",
        "state_province": "TX",
        "county": "Pecos",
        "target_capacity_mw": 150.0,
    },
    {
        "project_id": "prj_blue_meridian_tx",
        "project_name": "Blue Meridian HVDC Transmission",
        "energy_technology": EnergyTechnology.TRANSMISSION,
        "erp_project_code": "ERP-TRN-004",
        "state_province": "KS",
        "county": "Finney",
        "target_capacity_mw": 800.0,
    },
    {
        "project_id": "prj_caldera_geothermal",
        "project_name": "Caldera Basin Geothermal",
        "energy_technology": EnergyTechnology.GEOTHERMAL,
        "erp_project_code": "ERP-GEO-005",
        "state_province": "NV",
        "county": "Churchill",
        "target_capacity_mw": 95.0,
    },
]

BQ_PROJECTS_SCHEMA = [
    bigquery.SchemaField("project_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("project_name", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("energy_technology", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("erp_project_code", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("state_province", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("county", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("target_capacity_mw", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("landowner_count", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("document_count", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("special_conditions_count", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("flagged_node_count", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("created_at", "TIMESTAMP", mode="NULLABLE"),
    bigquery.SchemaField("updated_at", "TIMESTAMP", mode="REQUIRED"),
]

BQ_LANDOWNERS_SCHEMA = [
    bigquery.SchemaField("landowner_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("project_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("landowner_name", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("qrm_party_id", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("grantee_entity_name", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("parcel_summary", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("is_multi_parcel", "BOOL", mode="REQUIRED"),
    bigquery.SchemaField("contract_count", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("special_conditions_count", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("updated_at", "TIMESTAMP", mode="REQUIRED"),
]

BQ_DOCUMENTS_SCHEMA = [
    bigquery.SchemaField("document_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("filename", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("gcs_pdf_uri", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("gcs_export_prefix", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("page_count", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("contracting_parties_json", "JSON", mode="NULLABLE"),
    bigquery.SchemaField("effective_date", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("flagged_node_count", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("special_conditions_count", "INT64", mode="NULLABLE"),
    bigquery.SchemaField("ingestion_status", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("error_message", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("ingested_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("updated_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("project_id", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("landowner_id", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("energy_technology", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("grantor_landowner_name", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("grantee_entity_name", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("state", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("country", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("form_type", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("file_sha256", "STRING", mode="NULLABLE"),
]

BQ_CLAUSES_SCHEMA = [
    bigquery.SchemaField("document_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("gcs_pdf_uri", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("node_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("parent_node_id", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("sibling_order", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("document_zone", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("canonical_path", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("depth", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("clause_label", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("numbering_scheme", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("level_1_label", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("level_2_label", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("level_3_label", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("level_4_label", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("level_5_plus_path", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("clause_title", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("is_inline_clause", "BOOL", mode="REQUIRED"),
    bigquery.SchemaField("preamble_text", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("verbatim_text", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("postamble_text", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("reconstructed_context_text", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("defined_terms_used", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("cross_references", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("page_start", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("page_end", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("has_special_condition", "BOOL", mode="NULLABLE"),
    bigquery.SchemaField("special_condition_count", "INT64", mode="NULLABLE"),
    bigquery.SchemaField("hitl_status", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("hitl_flag_reasons", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("updated_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("reviewed_by", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("review_notes", "STRING", mode="NULLABLE"),
]

BQ_DEFINED_TERMS_SCHEMA = [
    bigquery.SchemaField("document_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("term_name", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("defined_in_node_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("definition_type", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("verbatim_definition", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("referenced_in_nodes", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("page_number", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("updated_at", "TIMESTAMP", mode="REQUIRED"),
]

BQ_EXHIBITS_CATALOG_SCHEMA = [
    bigquery.SchemaField("document_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("exhibit_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("exhibit_title", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("exhibit_modality", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("page_start", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("page_end", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("referenced_by_nodes", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("structured_entities_json", "JSON", mode="NULLABLE"),
    bigquery.SchemaField("has_unresolved_external_dep", "BOOL", mode="REQUIRED"),
    bigquery.SchemaField("updated_at", "TIMESTAMP", mode="REQUIRED"),
]

BQ_SPECIAL_CONDITIONS_SCHEMA = [
    bigquery.SchemaField("condition_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("document_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("node_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("canonical_path", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("constraint_category", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("target_asset_or_area", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("quantitative_metric", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("temporal_restriction", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("penalty_or_consequence", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("actionable_obligation_summary", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("verbatim_excerpt", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("page_number", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("extraction_confidence", "FLOAT64", mode="REQUIRED"),
    bigquery.SchemaField("hitl_status", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("updated_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("reviewed_by", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("project_id", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("landowner_id", "STRING", mode="NULLABLE"),
]

BQ_DND_SIGNOFFS_SCHEMA = [
    bigquery.SchemaField("signoff_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("document_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("project_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("landowner_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("subcontractor_company", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("foreman_name", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("construction_trade", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("acknowledged_condition_ids", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("acknowledged_count", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("dispatch_readiness_at_signoff", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("briefing_notes", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("signed_at", "TIMESTAMP", mode="REQUIRED"),
]

BQ_TEMPLATE_REGISTRY_SCHEMA = [
    bigquery.SchemaField("template_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("template_name", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("country", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("state_province", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("energy_technology", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("form_type", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("vintage_year", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("revision", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("gcs_pdf_uri", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("sha256_hash", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("page_count", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("clause_count", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("is_active", "BOOL", mode="REQUIRED"),
    bigquery.SchemaField("created_by", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("created_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("updated_at", "TIMESTAMP", mode="REQUIRED"),
]

BQ_TEMPLATE_CLAUSES_SCHEMA = [
    bigquery.SchemaField("template_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("node_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("parent_node_id", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("sibling_order", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("document_zone", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("canonical_path", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("depth", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("clause_label", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("clause_title", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("numbering_scheme", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("verbatim_text", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("placeholder_variables", "JSON", mode="NULLABLE"),
    bigquery.SchemaField("page_start", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("page_end", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("created_at", "TIMESTAMP", mode="REQUIRED"),
]

BQ_CONTRACT_VARIANCES_SCHEMA = [
    bigquery.SchemaField("variance_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("document_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("template_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("binding_mode", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("affinity_score", "FLOAT64", mode="REQUIRED"),
    bigquery.SchemaField("total_template_clauses", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("total_contract_clauses", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("identical_count", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("parameter_only_count", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("modified_count", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("added_count", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("deleted_count", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("overall_risk_score", "FLOAT64", mode="REQUIRED"),
    bigquery.SchemaField("high_risk_count", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("medium_risk_count", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("low_risk_count", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("template_name", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("template_version", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("created_at", "TIMESTAMP", mode="REQUIRED"),
]

BQ_CLAUSE_VARIANCE_DETAILS_SCHEMA = [
    bigquery.SchemaField("detail_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("variance_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("document_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("canonical_path", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("template_clause_id", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("contract_clause_id", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("clause_label", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("clause_title", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("variance_type", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("risk_level", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("risk_score", "FLOAT64", mode="REQUIRED"),
    bigquery.SchemaField("risk_category", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("risk_rationale", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("parameter_substitutions", "JSON", mode="NULLABLE"),
    bigquery.SchemaField("template_text", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("contract_text", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("redline_html", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("page_start", "INT64", mode="NULLABLE"),
    bigquery.SchemaField("created_at", "TIMESTAMP", mode="REQUIRED"),
]

BQ_BATCH_JOBS_SCHEMA = [
    bigquery.SchemaField("job_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("gcs_prefix", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("total_files", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("processed_files", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("success_count", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("failure_count", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("status", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("started_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("completed_at", "TIMESTAMP", mode="NULLABLE"),
    bigquery.SchemaField("total_tokens_consumed", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("prompt_tokens_consumed", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("completion_tokens_consumed", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("triggered_by", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("error_summary", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("updated_at", "TIMESTAMP", mode="REQUIRED"),
]

BQ_BATCH_JOB_ITEMS_SCHEMA = [
    bigquery.SchemaField("item_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("job_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("gcs_uri", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("filename", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("document_id", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("project_id", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("landowner_id", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("status", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("state_province", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("county", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("energy_technology", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("clause_count", "INT64", mode="NULLABLE"),
    bigquery.SchemaField("special_conditions_count", "INT64", mode="NULLABLE"),
    bigquery.SchemaField("duration_seconds", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("token_usage_total", "INT64", mode="NULLABLE"),
    bigquery.SchemaField("error_message", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("started_at", "TIMESTAMP", mode="NULLABLE"),
    bigquery.SchemaField("completed_at", "TIMESTAMP", mode="NULLABLE"),
    bigquery.SchemaField("updated_at", "TIMESTAMP", mode="REQUIRED"),
]



def generate_template_slug(
    country: str,
    state_province: str,
    energy_technology: str,
    form_type: str,
    vintage_year: str,
    revision: str = "v1",
) -> str:
    """Generate canonical taxonomy slug: tpl_{country}_{state}_{tech}_{form}_{vintage}_{rev}."""
    def clean(s: str) -> str:
        return re.sub(r"[^a-zA-Z0-9]+", "_", str(s)).strip("_").lower()

    return f"tpl_{clean(country)}_{clean(state_province)}_{clean(energy_technology)}_{clean(form_type)}_{clean(vintage_year)}_{clean(revision)}"


class ContractStorageService:
    """Manages GCS artifacts, Append-Only BigQuery tables (`QUALIFY ROW_NUMBER() = 1`), CSV exports, and local SQLite."""

    def __init__(self, config: PipelineConfig | None = None) -> None:
        self.config = config or PipelineConfig()
        self.local_root = Path(self.config.local_data_dir)
        self.local_raw_dir = self.local_root / "raw"
        self.local_exports_dir = self.local_root / "exports"
        if self.config.sqlite_db_path:
            self.local_db_path = Path(self.config.sqlite_db_path)
        else:
            self.local_db_path = self.local_root / "contract_intelligence.db"

        self._local_db_initialized: bool = False
        self._portfolio_seeded: bool = False
        self._gcs_client: storage.Client | None = None
        self._bq_client: bigquery.Client | None = None
        self._bq_tables_ensured: bool = False

    @property
    def gcs_client(self) -> storage.Client:
        if self._gcs_client is None:
            self._gcs_client = storage.Client(project=self.config.google_cloud_project)
        return self._gcs_client

    @property
    def bq_client(self) -> bigquery.Client:
        if self._bq_client is None:
            self._bq_client = bigquery.Client(project=self.config.google_cloud_project)
        return self._bq_client

    def _ensure_local_dirs_and_db(self) -> None:
        """Lazily initialize local directories and append-only SQLite tables (with idempotent CR-1, CR-2 & CR-3 migrations)."""
        if self._local_db_initialized and self.local_db_path.exists():
            return
        self.local_raw_dir.mkdir(parents=True, exist_ok=True)
        self.local_exports_dir.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.local_db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT NOT NULL,
                    project_name TEXT NOT NULL,
                    energy_technology TEXT NOT NULL,
                    erp_project_code TEXT NOT NULL,
                    state_province TEXT,
                    county TEXT,
                    target_capacity_mw REAL,
                    landowner_count INTEGER NOT NULL DEFAULT 0,
                    document_count INTEGER NOT NULL DEFAULT 0,
                    special_conditions_count INTEGER NOT NULL DEFAULT 0,
                    flagged_node_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS landowners (
                    landowner_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    landowner_name TEXT NOT NULL,
                    qrm_party_id TEXT,
                    grantee_entity_name TEXT,
                    parcel_summary TEXT,
                    is_multi_parcel INTEGER NOT NULL DEFAULT 0,
                    contract_count INTEGER NOT NULL DEFAULT 1,
                    special_conditions_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    gcs_pdf_uri TEXT NOT NULL DEFAULT '',
                    gcs_export_prefix TEXT NOT NULL DEFAULT '',
                    page_count INTEGER NOT NULL DEFAULT 1,
                    contracting_parties_json TEXT,
                    effective_date TEXT,
                    flagged_node_count INTEGER NOT NULL DEFAULT 0,
                    special_conditions_count INTEGER NOT NULL DEFAULT 0,
                    ingestion_status TEXT NOT NULL DEFAULT 'PROCESSED',
                    error_message TEXT,
                    ingested_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT '',
                    project_id TEXT NOT NULL DEFAULT 'prj_cedar_lantern_wind',
                    landowner_id TEXT NOT NULL DEFAULT 'lnd_unassigned',
                    energy_technology TEXT NOT NULL DEFAULT 'ONSHORE_WIND',
                    grantor_landowner_name TEXT,
                    grantee_entity_name TEXT,
                    state TEXT,
                    country TEXT DEFAULT 'US',
                    form_type TEXT,
                    file_sha256 TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS clauses (
                    document_id TEXT NOT NULL DEFAULT '',
                    gcs_pdf_uri TEXT NOT NULL DEFAULT '',
                    node_id TEXT NOT NULL,
                    parent_node_id TEXT,
                    sibling_order INTEGER NOT NULL DEFAULT 0,
                    document_zone TEXT NOT NULL DEFAULT 'BODY',
                    canonical_path TEXT NOT NULL DEFAULT '',
                    depth INTEGER NOT NULL DEFAULT 1,
                    clause_label TEXT NOT NULL DEFAULT '',
                    numbering_scheme TEXT NOT NULL DEFAULT 'DECIMAL',
                    level_1_label TEXT,
                    level_2_label TEXT,
                    level_3_label TEXT,
                    level_4_label TEXT,
                    level_5_plus_path TEXT,
                    clause_title TEXT,
                    is_inline_clause INTEGER NOT NULL DEFAULT 0,
                    preamble_text TEXT,
                    verbatim_text TEXT NOT NULL DEFAULT '',
                    postamble_text TEXT,
                    reconstructed_context_text TEXT NOT NULL DEFAULT '',
                    defined_terms_used TEXT NOT NULL DEFAULT 'NONE',
                    cross_references TEXT NOT NULL DEFAULT 'NONE',
                    page_start INTEGER NOT NULL DEFAULT 1,
                    page_end INTEGER NOT NULL DEFAULT 1,
                    has_special_condition INTEGER NOT NULL DEFAULT 0,
                    special_condition_count INTEGER NOT NULL DEFAULT 0,
                    hitl_status TEXT NOT NULL DEFAULT 'APPROVED',
                    hitl_flag_reasons TEXT NOT NULL DEFAULT 'NONE',
                    updated_at TEXT NOT NULL DEFAULT '',
                    reviewed_by TEXT,
                    review_notes TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS defined_terms (
                    document_id TEXT NOT NULL,
                    term_name TEXT NOT NULL,
                    defined_in_node_id TEXT NOT NULL,
                    definition_type TEXT NOT NULL,
                    verbatim_definition TEXT NOT NULL,
                    referenced_in_nodes TEXT NOT NULL,
                    page_number INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS exhibits_catalog (
                    document_id TEXT NOT NULL,
                    exhibit_id TEXT NOT NULL,
                    exhibit_title TEXT NOT NULL,
                    exhibit_modality TEXT NOT NULL,
                    page_start INTEGER NOT NULL,
                    page_end INTEGER NOT NULL,
                    referenced_by_nodes TEXT NOT NULL,
                    structured_entities_json TEXT,
                    has_unresolved_external_dep INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS special_conditions (
                    condition_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    canonical_path TEXT NOT NULL,
                    constraint_category TEXT NOT NULL,
                    target_asset_or_area TEXT NOT NULL,
                    quantitative_metric TEXT,
                    temporal_restriction TEXT,
                    penalty_or_consequence TEXT,
                    actionable_obligation_summary TEXT NOT NULL,
                    verbatim_excerpt TEXT NOT NULL,
                    page_number INTEGER NOT NULL,
                    extraction_confidence REAL NOT NULL,
                    hitl_status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    reviewed_by TEXT,
                    project_id TEXT NOT NULL DEFAULT 'prj_cedar_lantern_wind',
                    landowner_id TEXT NOT NULL DEFAULT 'lnd_unassigned'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dnd_checklist_signoffs (
                    signoff_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    project_id TEXT NOT NULL DEFAULT 'prj_cedar_lantern_wind',
                    landowner_id TEXT NOT NULL DEFAULT 'lnd_unassigned',
                    subcontractor_company TEXT NOT NULL,
                    foreman_name TEXT NOT NULL,
                    construction_trade TEXT NOT NULL DEFAULT 'GENERAL_SITE_OPERATIONS',
                    acknowledged_condition_ids TEXT NOT NULL,
                    acknowledged_count INTEGER NOT NULL DEFAULT 1,
                    dispatch_readiness_at_signoff TEXT NOT NULL DEFAULT 'HOLD_PENDING_HITL',
                    briefing_notes TEXT,
                    signed_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS template_registry (
                    template_id TEXT NOT NULL,
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
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
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
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS contract_variances (
                    variance_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    template_id TEXT NOT NULL,
                    binding_mode TEXT NOT NULL,
                    affinity_score REAL NOT NULL,
                    total_template_clauses INTEGER NOT NULL,
                    total_contract_clauses INTEGER NOT NULL,
                    identical_count INTEGER NOT NULL,
                    parameter_only_count INTEGER NOT NULL,
                    modified_count INTEGER NOT NULL,
                    added_count INTEGER NOT NULL,
                    deleted_count INTEGER NOT NULL,
                    overall_risk_score REAL NOT NULL,
                    high_risk_count INTEGER NOT NULL,
                    medium_risk_count INTEGER NOT NULL,
                    low_risk_count INTEGER NOT NULL,
                    template_name TEXT,
                    template_version TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS clause_variance_details (
                    detail_id TEXT PRIMARY KEY,
                    variance_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    canonical_path TEXT NOT NULL,
                    template_clause_id TEXT,
                    contract_clause_id TEXT,
                    clause_label TEXT NOT NULL,
                    clause_title TEXT,
                    variance_type TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    risk_score REAL NOT NULL,
                    risk_category TEXT,
                    risk_rationale TEXT,
                    parameter_substitutions TEXT,
                    template_text TEXT,
                    contract_text TEXT,
                    redline_html TEXT,
                    page_start INTEGER,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS batch_jobs (
                    job_id TEXT NOT NULL,
                    gcs_prefix TEXT NOT NULL,
                    total_files INTEGER NOT NULL,
                    processed_files INTEGER NOT NULL,
                    success_count INTEGER NOT NULL,
                    failure_count INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    total_tokens_consumed INTEGER NOT NULL DEFAULT 0,
                    prompt_tokens_consumed INTEGER NOT NULL DEFAULT 0,
                    completion_tokens_consumed INTEGER NOT NULL DEFAULT 0,
                    triggered_by TEXT,
                    error_summary TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE VIEW IF NOT EXISTS v_active_batch_jobs AS
                SELECT * FROM (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY job_id ORDER BY updated_at DESC) AS rn
                    FROM batch_jobs
                ) WHERE rn = 1
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS batch_job_items (
                    item_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    gcs_uri TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    document_id TEXT,
                    project_id TEXT,
                    landowner_id TEXT,
                    status TEXT NOT NULL,
                    state_province TEXT,
                    county TEXT,
                    energy_technology TEXT,
                    clause_count INTEGER,
                    special_conditions_count INTEGER,
                    duration_seconds REAL,
                    token_usage_total INTEGER,
                    error_message TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE VIEW IF NOT EXISTS v_active_batch_job_items AS
                SELECT * FROM (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY item_id ORDER BY updated_at DESC) AS rn
                    FROM batch_job_items
                ) WHERE rn = 1
                """
            )


            # Idempotent ALTER TABLE migrations for pre-CR-1 & pre-CR-2 SQLite databases
            doc_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(documents)").fetchall()
            }
            if "special_conditions_count" not in doc_cols:
                conn.execute(
                    "ALTER TABLE documents ADD COLUMN special_conditions_count INTEGER NOT NULL DEFAULT 0"
                )
            if "project_id" not in doc_cols:
                conn.execute(
                    "ALTER TABLE documents ADD COLUMN project_id TEXT NOT NULL DEFAULT 'prj_cedar_lantern_wind'"
                )
            if "landowner_id" not in doc_cols:
                conn.execute(
                    "ALTER TABLE documents ADD COLUMN landowner_id TEXT NOT NULL DEFAULT 'lnd_unassigned'"
                )
            if "energy_technology" not in doc_cols:
                conn.execute(
                    "ALTER TABLE documents ADD COLUMN energy_technology TEXT NOT NULL DEFAULT 'ONSHORE_WIND'"
                )
            if "grantor_landowner_name" not in doc_cols:
                conn.execute(
                    "ALTER TABLE documents ADD COLUMN grantor_landowner_name TEXT"
                )
            if "grantee_entity_name" not in doc_cols:
                conn.execute(
                    "ALTER TABLE documents ADD COLUMN grantee_entity_name TEXT"
                )
            if "state" not in doc_cols:
                conn.execute("ALTER TABLE documents ADD COLUMN state TEXT")
            if "country" not in doc_cols:
                conn.execute("ALTER TABLE documents ADD COLUMN country TEXT DEFAULT 'US'")
            if "form_type" not in doc_cols:
                conn.execute("ALTER TABLE documents ADD COLUMN form_type TEXT")
            if "file_sha256" not in doc_cols:
                conn.execute("ALTER TABLE documents ADD COLUMN file_sha256 TEXT")

            cv_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(contract_variances)").fetchall()
            }
            if "template_name" not in cv_cols:
                conn.execute("ALTER TABLE contract_variances ADD COLUMN template_name TEXT")
            if "template_version" not in cv_cols:
                conn.execute("ALTER TABLE contract_variances ADD COLUMN template_version TEXT")

            clause_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(clauses)").fetchall()
            }
            if "has_special_condition" not in clause_cols:
                conn.execute(
                    "ALTER TABLE clauses ADD COLUMN has_special_condition INTEGER NOT NULL DEFAULT 0"
                )
            if "special_condition_count" not in clause_cols:
                conn.execute(
                    "ALTER TABLE clauses ADD COLUMN special_condition_count INTEGER NOT NULL DEFAULT 0"
                )

            sc_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(special_conditions)").fetchall()
            }
            if "project_id" not in sc_cols:
                conn.execute(
                    "ALTER TABLE special_conditions ADD COLUMN project_id TEXT NOT NULL DEFAULT 'prj_cedar_lantern_wind'"
                )
            if "landowner_id" not in sc_cols:
                conn.execute(
                    "ALTER TABLE special_conditions ADD COLUMN landowner_id TEXT NOT NULL DEFAULT 'lnd_unassigned'"
                )

            proj_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(projects)").fetchall()
            }
            if "created_at" not in proj_cols:
                conn.execute("ALTER TABLE projects ADD COLUMN created_at TEXT")
                conn.execute("UPDATE projects SET created_at = updated_at WHERE created_at IS NULL OR created_at = ''")
        self._local_db_initialized = True

    def ensure_bq_tables(self) -> None:
        """Auto-create dataset, 10 append-only tables (evolving existing schemas), and deduplicated views in BigQuery."""
        if not self.config.use_bigquery or self._bq_tables_ensured:
            return
        try:
            dataset_ref = bigquery.Dataset(self.config.bq_dataset_fqn)
            dataset_ref.location = "US"
            self.bq_client.create_dataset(dataset_ref, exists_ok=True)

            table_specs = {
                "projects": (BQ_PROJECTS_SCHEMA, "project_id", "updated_at"),
                "landowners": (BQ_LANDOWNERS_SCHEMA, "project_id, landowner_id", "updated_at"),
                "documents": (BQ_DOCUMENTS_SCHEMA, "document_id", "updated_at"),
                "clauses": (BQ_CLAUSES_SCHEMA, "document_id, node_id", "updated_at"),
                "defined_terms": (BQ_DEFINED_TERMS_SCHEMA, "document_id, term_name", "updated_at"),
                "exhibits_catalog": (BQ_EXHIBITS_CATALOG_SCHEMA, "document_id, exhibit_id", "updated_at"),
                "special_conditions": (BQ_SPECIAL_CONDITIONS_SCHEMA, "document_id, condition_id", "updated_at"),
                "dnd_checklist_signoffs": (BQ_DND_SIGNOFFS_SCHEMA, "document_id, signoff_id", "signed_at"),
                "template_registry": (BQ_TEMPLATE_REGISTRY_SCHEMA, "template_id", "updated_at"),
                "template_clauses": (BQ_TEMPLATE_CLAUSES_SCHEMA, "template_id, node_id", "created_at"),
                "contract_variances": (BQ_CONTRACT_VARIANCES_SCHEMA, "variance_id", "created_at"),
                "clause_variance_details": (BQ_CLAUSE_VARIANCE_DETAILS_SCHEMA, "detail_id", "created_at"),
                "batch_jobs": (BQ_BATCH_JOBS_SCHEMA, "job_id", "updated_at"),
                "batch_job_items": (BQ_BATCH_JOB_ITEMS_SCHEMA, "item_id", "updated_at"),
            }

            for table_name, (schema, partition_keys, order_by_col) in table_specs.items():
                table_id = f"{self.config.bq_dataset_fqn}.{table_name}"
                table = bigquery.Table(table_id, schema=schema)
                self.bq_client.create_table(table, exists_ok=True)

                # Evolve existing BigQuery table schema if new NULLABLE CR-1 / CR-2 / CR-3 columns were added
                missing_fields: list[bigquery.SchemaField] = []
                try:
                    live_table = self.bq_client.get_table(table_id)
                    existing_names = {f.name for f in live_table.schema}
                    missing_fields = [f for f in schema if f.name not in existing_names]
                    if missing_fields:
                        live_table.schema = list(live_table.schema) + missing_fields
                        self.bq_client.update_table(live_table, ["schema"])
                except Exception as schema_exc:
                    logger.warning("BigQuery schema update warning on %s: %s", table_id, schema_exc)

                view_id = f"{self.config.bq_dataset_fqn}.{table_name}_latest"
                view_sql = (
                    f"SELECT * FROM `{table_id}` "
                    f"QUALIFY ROW_NUMBER() OVER (PARTITION BY {partition_keys} ORDER BY {order_by_col} DESC) = 1"
                )
                view = bigquery.Table(view_id)
                view.view_query = view_sql
                try:
                    self.bq_client.create_table(view, exists_ok=True)
                    live_view = self.bq_client.get_table(view_id)
                    if (
                        live_view.view_query != view_sql
                        or missing_fields
                        or len(live_view.schema) < len(schema)
                    ):
                        live_view.view_query = view_sql
                        self.bq_client.update_table(live_view, ["view_query"])
                except Exception as view_exc:
                    logger.warning("BigQuery view update warning on %s: %s", view_id, view_exc)

            # Explicit view aliases for batch jobs and items (CR-12 specification)
            for v_name, t_name, p_key in [
                ("v_active_batch_jobs", "batch_jobs", "job_id"),
                ("v_active_batch_job_items", "batch_job_items", "item_id"),
            ]:
                alias_view_id = f"{self.config.bq_dataset_fqn}.{v_name}"
                target_table_id = f"{self.config.bq_dataset_fqn}.{t_name}"
                alias_sql = f"SELECT * FROM `{target_table_id}` QUALIFY ROW_NUMBER() OVER (PARTITION BY {p_key} ORDER BY updated_at DESC) = 1"
                alias_table = bigquery.Table(alias_view_id)
                alias_table.view_query = alias_sql
                try:
                    self.bq_client.create_table(alias_table, exists_ok=True)
                except Exception as alias_exc:
                    logger.warning("BigQuery view alias warning on %s: %s", alias_view_id, alias_exc)

            self._bq_tables_ensured = True
        except Exception as exc:
            logger.warning("BigQuery table creation warning: %s", exc)

    def archive_raw_pdf(
        self, pdf_bytes: bytes, filename: str, document_id: str | None = None
    ) -> tuple[str, str]:
        """Archive the source PDF to local disk and gs://<bucket>/raw/<document_id>/<filename>."""
        with tracer.start_as_current_span("storage.gcs.archive_pdf") as span:
            self._ensure_local_dirs_and_db()
            doc_id = document_id or compute_document_id(pdf_bytes)
            safe_name = Path(filename).name
            local_path = self.local_raw_dir / doc_id / safe_name
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(pdf_bytes)

            gcs_uri = f"gs://{self.config.gcs_bucket_name}/raw/{doc_id}/{safe_name}"
            span.set_attribute("contract.document_id", doc_id)
            span.set_attribute("gcs.uri", gcs_uri)
            span.set_attribute("byte_size", len(pdf_bytes))
            if self.config.use_cloud_storage:
                try:
                    bucket = self.gcs_client.bucket(self.config.gcs_bucket_name)
                    blob = bucket.blob(f"raw/{doc_id}/{safe_name}")
                    blob.upload_from_string(pdf_bytes, content_type="application/pdf")
                    logger.info("Uploaded raw PDF to %s", gcs_uri)
                except Exception as exc:
                    logger.warning("GCS upload fallback to local disk for %s: %s", gcs_uri, exc)
            return doc_id, gcs_uri

    def read_pdf_bytes(self, document_id: str) -> bytes:
        """Read PDF bytes from local mirror or GCS raw/<document_id>/."""
        self._ensure_local_dirs_and_db()
        local_doc_dir = self.local_raw_dir / document_id
        if local_doc_dir.exists():
            pdfs = list(local_doc_dir.glob("*.pdf"))
            if pdfs:
                return pdfs[0].read_bytes()

        if self.config.use_cloud_storage:
            bucket = self.gcs_client.bucket(self.config.gcs_bucket_name)
            blobs = list(bucket.list_blobs(prefix=f"raw/{document_id}/"))
            for blob in blobs:
                if blob.name.endswith(".pdf"):
                    data = blob.download_as_bytes()
                    local_path = self.local_raw_dir / document_id / Path(blob.name).name
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    local_path.write_bytes(data)
                    return data
        raise FileNotFoundError(f"PDF not found for document_id={document_id}")

    def list_incoming_gcs_pdfs(self, prefix: str = "incoming/") -> list[str]:
        """List gs:// URIs of PDFs waiting in gs://<bucket>/incoming/."""
        if not self.config.use_cloud_storage:
            return []
        bucket = self.gcs_client.bucket(self.config.gcs_bucket_name)
        return [
            f"gs://{self.config.gcs_bucket_name}/{blob.name}"
            for blob in bucket.list_blobs(prefix=prefix)
            if blob.name.lower().endswith(".pdf")
        ]

    @staticmethod
    def _split_gcs_uri(gcs_uri: str) -> tuple[str, str]:
        """Split a gs://bucket/path URI into (bucket_name, blob_name)."""
        clean = gcs_uri.removeprefix("gs://")
        if "/" in clean:
            bucket, blob = clean.split("/", 1)
            return bucket, blob
        return clean, ""

    def download_gcs_uri(self, gcs_uri: str) -> tuple[bytes, str]:
        """Download a gs://bucket/path.pdf URI and return (pdf_bytes, filename)."""
        without_scheme = gcs_uri.removeprefix("gs://")
        bucket_name, blob_path = without_scheme.split("/", 1)
        bucket = self.gcs_client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        return blob.download_as_bytes(), Path(blob_path).name

    def _append_table_rows(
        self,
        table_name: str,
        columns: list[str],
        rows_json: list[dict[str, object]],
        *,
        sqlite_only: bool = False,
    ) -> None:
        """Append rows to local SQLite and (when enabled) BigQuery via insert_rows_json."""
        if not rows_json:
            return
        with tracer.start_as_current_span(
            f"storage.bigquery.insert_rows.{table_name}"
        ) as span:
            span.set_attribute("bigquery.table_name", table_name)
            span.set_attribute("row_count", len(rows_json))
            self._ensure_local_dirs_and_db()
            def _fmt_sqlite(val: object) -> object:
                if isinstance(val, (dict, list)):
                    return json.dumps(val)
                if isinstance(val, bool):
                    return int(val)
                return val

            with sqlite3.connect(self.local_db_path) as conn:
                cols = ", ".join(columns)
                placeholders = ", ".join(["?"] * len(columns))
                conn.executemany(
                    f"INSERT INTO {table_name} ({cols}) VALUES ({placeholders})",
                    [[_fmt_sqlite(r.get(c)) for c in columns] for r in rows_json],
                )
            if self.config.use_bigquery and not sqlite_only:
                self.ensure_bq_tables()
                table_id = f"{self.config.bq_dataset_fqn}.{table_name}"
                span.set_attribute("bigquery.table_id", table_id)
                errors = self.bq_client.insert_rows_json(table_id, rows_json)
                if errors:
                    span.set_attribute("bigquery.insert_errors_count", len(errors))
                    logger.warning(
                        "BigQuery insert_rows_json errors on %s: %s",
                        table_name,
                        errors,
                    )

    def append_project_row(self, project: ProjectRow, *, sqlite_only: bool = False) -> None:
        """Append a new version of ProjectRow (6th normalized table) to SQLite and BigQuery."""
        self._append_table_rows(
            "projects",
            PROJECT_CSV_COLUMNS,
            [project.model_dump(mode="json")],
            sqlite_only=sqlite_only,
        )

    def append_landowner_row(self, landowner: LandownerRow, *, sqlite_only: bool = False) -> None:
        """Append a new version of LandownerRow (7th normalized table) to SQLite and BigQuery."""
        self._append_table_rows(
            "landowners",
            LANDOWNER_CSV_COLUMNS,
            [landowner.model_dump(mode="json")],
            sqlite_only=sqlite_only,
        )

    def append_document_row(self, doc: DocumentRegistryRow) -> None:
        """Append a new version of DocumentRegistryRow to SQLite and BigQuery."""
        self._append_table_rows("documents", DOCUMENT_REGISTRY_COLUMNS, [doc.model_dump(mode="json")])

    def append_clauses(self, clauses: list[ClauseRow]) -> None:
        """Append ClauseRow versions to SQLite and BigQuery via insert_rows_json."""
        self._append_table_rows("clauses", CLAUSE_CSV_COLUMNS, [c.model_dump(mode="json") for c in clauses])

    def append_defined_terms(self, terms: list[DefinedTermRow]) -> None:
        """Append DefinedTermRow versions to SQLite and BigQuery via insert_rows_json."""
        self._append_table_rows("defined_terms", DEFINED_TERM_CSV_COLUMNS, [t.model_dump(mode="json") for t in terms])

    def append_exhibits_catalog(self, exhibits: list[ExhibitCatalogRow]) -> None:
        """Append ExhibitCatalogRow versions to SQLite and BigQuery via insert_rows_json."""
        self._append_table_rows("exhibits_catalog", EXHIBIT_CATALOG_CSV_COLUMNS, [e.model_dump(mode="json") for e in exhibits])

    def append_special_conditions(self, conditions: list[SpecialConditionRow]) -> None:
        """Append SpecialConditionRow versions (5th normalized table) to SQLite and BigQuery via insert_rows_json."""
        self._append_table_rows(
            "special_conditions",
            SPECIAL_CONDITION_CSV_COLUMNS,
            [sc.model_dump(mode="json") for sc in conditions],
        )

    def append_dnd_signoff_row(
        self, signoff: DNDChecklistSignoffRow, *, sqlite_only: bool = False
    ) -> None:
        """Append DNDChecklistSignoffRow (8th normalized table) to SQLite and BigQuery via insert_rows_json."""
        self._append_table_rows(
            "dnd_checklist_signoffs",
            DND_SIGNOFF_CSV_COLUMNS,
            [signoff.model_dump(mode="json")],
            sqlite_only=sqlite_only,
        )

    def _export_dnd_signoffs_csv(
        self,
        document_id: str,
        signoffs: list[DNDChecklistSignoffRow] | None = None,
    ) -> str:
        """Write utf-8-sig `dnd_checklist_signoffs.csv` for document_id to local disk and GCS."""
        self._ensure_local_dirs_and_db()
        doc_export_dir = self.local_exports_dir / document_id
        doc_export_dir.mkdir(parents=True, exist_ok=True)
        rows = (
            [s.model_dump(mode="json") for s in signoffs]
            if signoffs is not None
            else [s.model_dump(mode="json") for s in self.list_dnd_signoffs(document_id)]
        )
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=DND_SIGNOFF_CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow({col: ("" if r.get(col) is None else r.get(col)) for col in DND_SIGNOFF_CSV_COLUMNS})
        csv_bytes = buf.getvalue().encode("utf-8-sig")

        local_file = doc_export_dir / "dnd_checklist_signoffs.csv"
        local_file.write_bytes(csv_bytes)

        if self.config.use_cloud_storage:
            try:
                bucket = self.gcs_client.bucket(self.config.gcs_bucket_name)
                blob = bucket.blob(f"exports/{document_id}/dnd_checklist_signoffs.csv")
                blob.upload_from_string(csv_bytes, content_type="text/csv; charset=utf-8")
            except Exception as exc:
                logger.warning("GCS CSV upload warning for dnd_checklist_signoffs.csv: %s", exc)
        return str(local_file)

    def _append_bundle_rows(
        self, bundle: ParsedContractBundle, *, sqlite_only: bool = False
    ) -> None:
        """Append all 5 document-level normalized tables for a bundle to SQLite and optionally BigQuery."""
        self._append_table_rows(
            "documents",
            DOCUMENT_REGISTRY_COLUMNS,
            [bundle.document.model_dump(mode="json")],
            sqlite_only=sqlite_only,
        )
        self._append_table_rows(
            "clauses",
            CLAUSE_CSV_COLUMNS,
            [c.model_dump(mode="json") for c in bundle.clauses],
            sqlite_only=sqlite_only,
        )
        self._append_table_rows(
            "defined_terms",
            DEFINED_TERM_CSV_COLUMNS,
            [t.model_dump(mode="json") for t in bundle.defined_terms],
            sqlite_only=sqlite_only,
        )
        self._append_table_rows(
            "exhibits_catalog",
            EXHIBIT_CATALOG_CSV_COLUMNS,
            [e.model_dump(mode="json") for e in bundle.exhibits_catalog],
            sqlite_only=sqlite_only,
        )
        self._append_table_rows(
            "special_conditions",
            SPECIAL_CONDITION_CSV_COLUMNS,
            [sc.model_dump(mode="json") for sc in bundle.special_conditions],
            sqlite_only=sqlite_only,
        )

    def persist_bundle(self, bundle: ParsedContractBundle) -> dict[str, str]:
        """Stream all 5 document-level tables in append-only mode and export utf-8-sig CSVs to local & GCS."""
        with tracer.start_as_current_span("storage.bigquery.persist_bundle") as span:
            span.set_attribute("contract.document_id", bundle.document.document_id)
            span.set_attribute("contract.project_id", bundle.document.project_id)
            span.set_attribute("bigquery.table_count", 8)
            self._append_bundle_rows(bundle, sqlite_only=False)
            return self.export_csvs(bundle)

    def export_csvs(self, bundle: ParsedContractBundle) -> dict[str, str]:
        """Write utf-8-sig CSV files (`clauses.csv`, `defined_terms.csv`, `exhibits_catalog.csv`, `special_conditions.csv`, `dnd_checklist_signoffs.csv`) to disk and GCS."""
        with tracer.start_as_current_span("storage.gcs.export_csvs") as span:
            span.set_attribute("contract.document_id", bundle.document.document_id)
            self._ensure_local_dirs_and_db()
            doc_id = bundle.document.document_id
            doc_export_dir = self.local_exports_dir / doc_id
            doc_export_dir.mkdir(parents=True, exist_ok=True)

            artifacts: dict[str, tuple[list[str], list[dict[str, object]]]] = {
                "clauses.csv": (
                    CLAUSE_CSV_COLUMNS,
                    [c.model_dump(mode="json") for c in bundle.clauses],
                ),
                "defined_terms.csv": (
                    DEFINED_TERM_CSV_COLUMNS,
                    [t.model_dump(mode="json") for t in bundle.defined_terms],
                ),
                "exhibits_catalog.csv": (
                    EXHIBIT_CATALOG_CSV_COLUMNS,
                    [e.model_dump(mode="json") for e in bundle.exhibits_catalog],
                ),
                "special_conditions.csv": (
                    SPECIAL_CONDITION_CSV_COLUMNS,
                    [sc.model_dump(mode="json") for sc in bundle.special_conditions],
                ),
            }

            exported_paths: dict[str, str] = {}
            for filename, (columns, rows) in artifacts.items():
                buf = io.StringIO()
                writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
                writer.writeheader()
                for r in rows:
                    writer.writerow({col: ("" if r.get(col) is None else r.get(col)) for col in columns})
                csv_bytes = buf.getvalue().encode("utf-8-sig")

                local_file = doc_export_dir / filename
                local_file.write_bytes(csv_bytes)
                exported_paths[filename] = str(local_file)

                if self.config.use_cloud_storage:
                    try:
                        bucket = self.gcs_client.bucket(self.config.gcs_bucket_name)
                        blob = bucket.blob(f"exports/{doc_id}/{filename}")
                        blob.upload_from_string(csv_bytes, content_type="text/csv; charset=utf-8")
                    except Exception as exc:
                        logger.warning("GCS CSV upload warning for %s: %s", filename, exc)

            exported_paths["dnd_checklist_signoffs.csv"] = self._export_dnd_signoffs_csv(doc_id)
            span.set_attribute("csv.files_count", len(exported_paths))
            return exported_paths

    @staticmethod
    def _coalesce_doc_field(k: str, val: object) -> object:
        if k == "special_conditions_count" and val is None:
            return 0
        if k == "project_id" and not val:
            return "prj_cedar_lantern_wind"
        if k == "landowner_id" and not val:
            return "lnd_unassigned"
        if k == "energy_technology" and not val:
            return EnergyTechnology.ONSHORE_WIND
        if k == "contracting_parties_json" and not val:
            return "[]"
        if k == "ingestion_status":
            if not val or str(val).upper() in ("PROCESSED", "COMPLETE"):
                return IngestionStatus.VERIFIED_COMPLETE
        if k == "gcs_pdf_uri" and not val:
            return ""
        if k == "gcs_export_prefix" and not val:
            return ""
        return val

    @staticmethod
    def _coalesce_sc_field(k: str, val: object) -> object:
        if k == "project_id" and not val:
            return "prj_cedar_lantern_wind"
        if k == "landowner_id" and not val:
            return "lnd_unassigned"
        return val

    @staticmethod
    def _coalesce_clause_field(k: str, val: object, node_id: str | None = None) -> object:
        if k in ("is_inline_clause", "has_special_condition"):
            return bool(val)
        if k == "special_condition_count":
            return int(val) if val is not None else 0
        if k in ("page_start", "page_end", "depth"):
            try:
                return int(val) if val is not None and int(val) >= 1 else 1
            except Exception:
                return 1
        if k in ("sibling_order",):
            try:
                return int(val) if val is not None else 1
            except Exception:
                return 1
        if k == "verbatim_text":
            return str(val) if val is not None else ""
        if k == "reconstructed_context_text":
            return str(val) if val is not None else ""
        if k == "canonical_path":
            if val is not None and str(val).strip():
                return str(val).strip()
            return f"BODY.{node_id}" if node_id else "BODY.1"
        if k == "clause_label":
            return str(val) if val is not None and str(val).strip() else "Section"
        if k == "document_zone":
            return str(val) if val is not None and str(val).strip() else "BODY"
        if k == "numbering_scheme":
            return str(val) if val is not None and str(val).strip() else "DECIMAL"
        if k == "hitl_status":
            if not val or str(val).upper() in ("APPROVED", "APPROVED_BY_HUMAN"):
                return HITLStatus.APPROVED_BY_HUMAN
            if str(val).upper() in ("VERIFIED", "VERIFIED_AUTO"):
                return HITLStatus.VERIFIED_AUTO
            return val
        return val

    def list_documents(
        self,
        *,
        project_id: str | None = None,
        landowner_id: str | None = None,
        energy_technology: str | None = None,
    ) -> list[DocumentRegistryRow]:
        """Return deduplicated latest version of all documents ordered by updated_at DESC, with optional portfolio filters."""
        self._ensure_local_dirs_and_db()
        docs_by_id: dict[str, DocumentRegistryRow] = {}

        with sqlite3.connect(self.local_db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY document_id ORDER BY updated_at DESC, rowid DESC) AS rn
                    FROM documents
                ) WHERE rn = 1
                ORDER BY updated_at DESC
                """
            ).fetchall()
        for r in rows:
            doc_obj = DocumentRegistryRow.model_validate(
                {
                    k: self._coalesce_doc_field(k, r[k] if k in r.keys() else None)
                    for k in DOCUMENT_REGISTRY_COLUMNS
                }
            )
            docs_by_id[doc_obj.document_id] = doc_obj

        if self.config.use_bigquery:
            try:
                self.ensure_bq_tables()
                sql = f"""
                    SELECT {", ".join(DOCUMENT_REGISTRY_COLUMNS)}
                    FROM `{self.config.bq_dataset_fqn}.documents`
                    QUALIFY ROW_NUMBER() OVER (PARTITION BY document_id ORDER BY updated_at DESC) = 1
                    ORDER BY updated_at DESC
                """
                bq_rows = list(self.bq_client.query(sql).result())
                for r in bq_rows:
                    row_dict: dict[str, object] = {}
                    for k in DOCUMENT_REGISTRY_COLUMNS:
                        val = r[k]
                        if hasattr(val, "isoformat"):
                            val = val.isoformat()
                        elif k == "contracting_parties_json" and val is not None and not isinstance(val, str):
                            val = json.dumps(val)
                        row_dict[k] = self._coalesce_doc_field(k, val)
                    bq_doc = DocumentRegistryRow.model_validate(row_dict)
                    existing_local = docs_by_id.get(bq_doc.document_id)
                    if existing_local is None or str(bq_doc.updated_at) >= str(existing_local.updated_at):
                        docs_by_id[bq_doc.document_id] = bq_doc
            except Exception as exc:
                logger.warning("BigQuery list_documents fallback to SQLite: %s", exc)

        out_docs = sorted(
            docs_by_id.values(), key=lambda d: str(d.updated_at), reverse=True
        )
        if project_id:
            out_docs = [d for d in out_docs if d.project_id == project_id]
        if landowner_id:
            out_docs = [d for d in out_docs if d.landowner_id == landowner_id]
        if energy_technology:
            out_docs = [
                d for d in out_docs if str(d.energy_technology) == str(energy_technology)
            ]
        return out_docs

    def get_document_filename_lookup(self) -> dict[str, str]:
        """Return `{document_id: filename}` from local SQLite first, falling back to `list_documents()` only if empty."""
        self._ensure_local_dirs_and_db()
        lookup: dict[str, str] = {}
        try:
            with sqlite3.connect(self.local_db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT document_id, filename FROM (
                        SELECT document_id, filename,
                               ROW_NUMBER() OVER (PARTITION BY document_id ORDER BY updated_at DESC, rowid DESC) AS rn
                        FROM documents
                    ) WHERE rn = 1
                    """
                ).fetchall()
            for r in rows:
                doc_id = r["document_id"]
                fn = r["filename"]
                if doc_id and fn:
                    lookup[str(doc_id)] = str(fn)
        except Exception as exc:
            logger.debug("SQLite filename lookup fallback: %s", exc)

        if not lookup:
            try:
                lookup = {d.document_id: d.filename for d in self.list_documents()}
            except Exception as exc:
                logger.debug("list_documents filename lookup fallback: %s", exc)
        return lookup

    def _get_bundle_from_sqlite(self, document_id: str) -> ParsedContractBundle | None:
        """Retrieve a complete bundle from local SQLite if present."""
        self._ensure_local_dirs_and_db()
        with sqlite3.connect(self.local_db_path) as conn:
            conn.row_factory = sqlite3.Row
            doc_row = conn.execute(
                """
                SELECT * FROM (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY document_id ORDER BY updated_at DESC, rowid DESC) AS rn
                    FROM documents WHERE document_id = ?
                ) WHERE rn = 1
                """,
                (document_id,),
            ).fetchone()
            if doc_row is None:
                return None

            clause_rows = conn.execute(
                """
                SELECT * FROM (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY document_id, node_id ORDER BY updated_at DESC, rowid DESC) AS rn
                    FROM clauses WHERE document_id = ?
                ) WHERE rn = 1
                ORDER BY page_start ASC, sibling_order ASC, node_id ASC
                """,
                (document_id,),
            ).fetchall()
            term_rows = conn.execute(
                """
                SELECT * FROM (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY document_id, term_name ORDER BY updated_at DESC, rowid DESC) AS rn
                    FROM defined_terms WHERE document_id = ?
                ) WHERE rn = 1
                ORDER BY page_number ASC, term_name ASC
                """,
                (document_id,),
            ).fetchall()
            exhibit_rows = conn.execute(
                """
                SELECT * FROM (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY document_id, exhibit_id ORDER BY updated_at DESC, rowid DESC) AS rn
                    FROM exhibits_catalog WHERE document_id = ?
                ) WHERE rn = 1
                ORDER BY page_start ASC, exhibit_id ASC
                """,
                (document_id,),
            ).fetchall()
            sc_rows = conn.execute(
                """
                SELECT * FROM (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY document_id, condition_id ORDER BY updated_at DESC, rowid DESC) AS rn
                    FROM special_conditions WHERE document_id = ?
                ) WHERE rn = 1
                ORDER BY page_number ASC, condition_id ASC
                """,
                (document_id,),
            ).fetchall()

        doc_keys = set(doc_row.keys())
        return ParsedContractBundle(
            document=DocumentRegistryRow.model_validate(
                {
                    k: self._coalesce_doc_field(k, doc_row[k] if k in doc_keys else None)
                    for k in DOCUMENT_REGISTRY_COLUMNS
                }
            ),
            clauses=[
                ClauseRow.model_validate(
                    {
                        k: self._coalesce_clause_field(
                            k,
                            r[k] if k in r.keys() else None,
                            node_id=str(r["node_id"]) if "node_id" in r.keys() and r["node_id"] else None,
                        )
                        for k in CLAUSE_CSV_COLUMNS
                    }
                )
                for r in clause_rows
            ],
            defined_terms=[
                DefinedTermRow.model_validate(
                    {k: r[k] for k in DEFINED_TERM_CSV_COLUMNS}
                )
                for r in term_rows
            ],
            exhibits_catalog=[
                ExhibitCatalogRow.model_validate(
                    {
                        k: (
                            bool(r[k])
                            if k == "has_unresolved_external_dep"
                            else r[k]
                        )
                        for k in EXHIBIT_CATALOG_CSV_COLUMNS
                    }
                )
                for r in exhibit_rows
            ],
            special_conditions=[
                SpecialConditionRow.model_validate(
                    {
                        k: self._coalesce_sc_field(k, r[k] if k in r.keys() else None)
                        for k in SPECIAL_CONDITION_CSV_COLUMNS
                    }
                )
                for r in sc_rows
            ],
        )

    def get_bundle(self, document_id: str) -> ParsedContractBundle:
        """Retrieve the latest deduplicated ParsedContractBundle for document_id."""
        sqlite_bundle = self._get_bundle_from_sqlite(document_id)
        if self.config.use_bigquery:
            try:
                bq_bundle = self.get_bundle_from_bigquery(document_id)
                if sqlite_bundle is None or str(bq_bundle.document.updated_at) >= str(sqlite_bundle.document.updated_at):
                    return bq_bundle
            except KeyError:
                pass
            except Exception as exc:
                logger.warning("BigQuery get_bundle fallback to SQLite for %s: %s", document_id, exc)

        if sqlite_bundle is not None:
            return sqlite_bundle

        raise KeyError(f"Document {document_id!r} not found")

    def get_document_bundle(self, document_id: str) -> ParsedContractBundle | None:
        """Retrieve the latest deduplicated ParsedContractBundle for document_id, returning None if not found."""
        try:
            return self.get_bundle(document_id)
        except KeyError:
            return None
        except Exception as exc:
            logger.warning("Error fetching document bundle for %s: %s", document_id, exc)
            return None

    def get_bundle_from_bigquery(self, document_id: str) -> ParsedContractBundle:
        """Query BigQuery directly using QUALIFY ROW_NUMBER() = 1 deduplication across all 5 document-level tables."""
        self.ensure_bq_tables()
        params = [bigquery.ScalarQueryParameter("doc_id", "STRING", document_id)]
        job_config = bigquery.QueryJobConfig(query_parameters=params)

        doc_sql = f"""
            SELECT {", ".join(DOCUMENT_REGISTRY_COLUMNS)}
            FROM `{self.config.bq_dataset_fqn}.documents`
            WHERE document_id = @doc_id
            QUALIFY ROW_NUMBER() OVER (PARTITION BY document_id ORDER BY updated_at DESC) = 1
        """
        doc_res = list(self.bq_client.query(doc_sql, job_config=job_config).result())
        if not doc_res:
            raise KeyError(f"Document {document_id!r} not found in BigQuery")

        clauses_sql = f"""
            SELECT {", ".join(CLAUSE_CSV_COLUMNS)}
            FROM `{self.config.bq_dataset_fqn}.clauses`
            WHERE document_id = @doc_id
            QUALIFY ROW_NUMBER() OVER (PARTITION BY document_id, node_id ORDER BY updated_at DESC) = 1
            ORDER BY page_start ASC, sibling_order ASC, node_id ASC
        """
        terms_sql = f"""
            SELECT {", ".join(DEFINED_TERM_CSV_COLUMNS)}
            FROM `{self.config.bq_dataset_fqn}.defined_terms`
            WHERE document_id = @doc_id
            QUALIFY ROW_NUMBER() OVER (PARTITION BY document_id, term_name ORDER BY updated_at DESC) = 1
            ORDER BY page_number ASC, term_name ASC
        """
        exhibits_sql = f"""
            SELECT {", ".join(EXHIBIT_CATALOG_CSV_COLUMNS)}
            FROM `{self.config.bq_dataset_fqn}.exhibits_catalog`
            WHERE document_id = @doc_id
            QUALIFY ROW_NUMBER() OVER (PARTITION BY document_id, exhibit_id ORDER BY updated_at DESC) = 1
            ORDER BY page_start ASC, exhibit_id ASC
        """
        special_conditions_sql = f"""
            SELECT {", ".join(SPECIAL_CONDITION_CSV_COLUMNS)}
            FROM `{self.config.bq_dataset_fqn}.special_conditions`
            WHERE document_id = @doc_id
            QUALIFY ROW_NUMBER() OVER (PARTITION BY document_id, condition_id ORDER BY updated_at DESC) = 1
            ORDER BY page_number ASC, condition_id ASC
        """

        def _norm_row(r: bigquery.Row, cols: list[str], *, is_doc: bool = False, is_sc: bool = False) -> dict[str, object]:
            out: dict[str, object] = {}
            for k in cols:
                val = r[k]
                if hasattr(val, "isoformat"):
                    val = val.isoformat()
                elif k in ("contracting_parties_json", "structured_entities_json") and val is not None and not isinstance(val, str):
                    val = json.dumps(val)
                elif k == "has_special_condition" and val is None:
                    val = False
                elif k in ("special_condition_count", "special_conditions_count") and val is None:
                    val = 0
                if is_doc:
                    val = self._coalesce_doc_field(k, val)
                elif is_sc:
                    val = self._coalesce_sc_field(k, val)
                out[k] = val
            return out

        return ParsedContractBundle(
            document=DocumentRegistryRow.model_validate(_norm_row(doc_res[0], DOCUMENT_REGISTRY_COLUMNS, is_doc=True)),
            clauses=[
                ClauseRow.model_validate(_norm_row(r, CLAUSE_CSV_COLUMNS))
                for r in self.bq_client.query(clauses_sql, job_config=job_config).result()
            ],
            defined_terms=[
                DefinedTermRow.model_validate(_norm_row(r, DEFINED_TERM_CSV_COLUMNS))
                for r in self.bq_client.query(terms_sql, job_config=job_config).result()
            ],
            exhibits_catalog=[
                ExhibitCatalogRow.model_validate(_norm_row(r, EXHIBIT_CATALOG_CSV_COLUMNS))
                for r in self.bq_client.query(exhibits_sql, job_config=job_config).result()
            ],
            special_conditions=[
                SpecialConditionRow.model_validate(_norm_row(r, SPECIAL_CONDITION_CSV_COLUMNS, is_sc=True))
                for r in self.bq_client.query(special_conditions_sql, job_config=job_config).result()
            ],
        )

    def _list_projects_raw(self) -> list[ProjectRow]:
        """Load deduplicated projects from SQLite and BigQuery."""
        self._ensure_local_dirs_and_db()
        projects_by_id: dict[str, ProjectRow] = {}
        with sqlite3.connect(self.local_db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY project_id ORDER BY updated_at DESC, rowid DESC) AS rn
                    FROM projects
                ) WHERE rn = 1
                ORDER BY project_id ASC
                """
            ).fetchall()
        for r in rows:
            data = {k: r[k] for k in PROJECT_CSV_COLUMNS if k in r.keys()}
            if "created_at" not in data or not data["created_at"]:
                data["created_at"] = data.get("updated_at") or utc_now_iso()
            p = ProjectRow.model_validate(data)
            projects_by_id[p.project_id] = p

        if self.config.use_bigquery:
            try:
                self.ensure_bq_tables()
                sql = f"""
                    SELECT {", ".join(PROJECT_CSV_COLUMNS)}
                    FROM `{self.config.bq_dataset_fqn}.projects`
                    QUALIFY ROW_NUMBER() OVER (PARTITION BY project_id ORDER BY updated_at DESC) = 1
                    ORDER BY project_id ASC
                """
                for r in self.bq_client.query(sql).result():
                    row_dict: dict[str, object] = {}
                    for k in PROJECT_CSV_COLUMNS:
                        val = r[k]
                        if hasattr(val, "isoformat"):
                            val = val.isoformat()
                        row_dict[k] = val
                    if "created_at" not in row_dict or not row_dict["created_at"]:
                        row_dict["created_at"] = row_dict.get("updated_at") or utc_now_iso()
                    bq_proj = ProjectRow.model_validate(row_dict)
                    local_proj = projects_by_id.get(bq_proj.project_id)
                    if local_proj is None or str(bq_proj.updated_at) >= str(local_proj.updated_at):
                        projects_by_id[bq_proj.project_id] = bq_proj
            except Exception as exc:
                logger.warning("BigQuery _list_projects_raw fallback to SQLite: %s", exc)

        return list(projects_by_id.values())

    def _list_landowners_raw(self, project_id: str | None = None) -> list[LandownerRow]:
        """Load deduplicated landowners from SQLite and BigQuery."""
        self._ensure_local_dirs_and_db()
        landowners_by_key: dict[tuple[str, str], LandownerRow] = {}
        with sqlite3.connect(self.local_db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY project_id, landowner_id ORDER BY updated_at DESC, rowid DESC) AS rn
                    FROM landowners
                ) WHERE rn = 1
                ORDER BY landowner_name ASC
                """
            ).fetchall()
        for r in rows:
            l_row = LandownerRow.model_validate(
                {
                    k: (bool(r[k]) if k == "is_multi_parcel" else r[k])
                    for k in LANDOWNER_CSV_COLUMNS
                }
            )
            landowners_by_key[(l_row.project_id, l_row.landowner_id)] = l_row

        if self.config.use_bigquery:
            try:
                self.ensure_bq_tables()
                sql = f"""
                    SELECT {", ".join(LANDOWNER_CSV_COLUMNS)}
                    FROM `{self.config.bq_dataset_fqn}.landowners`
                    QUALIFY ROW_NUMBER() OVER (PARTITION BY project_id, landowner_id ORDER BY updated_at DESC) = 1
                    ORDER BY landowner_name ASC
                """
                for r in self.bq_client.query(sql).result():
                    row_dict: dict[str, object] = {}
                    for k in LANDOWNER_CSV_COLUMNS:
                        val = r[k]
                        if hasattr(val, "isoformat"):
                            val = val.isoformat()
                        elif k == "is_multi_parcel":
                            val = bool(val)
                        row_dict[k] = val
                    bq_lnd = LandownerRow.model_validate(row_dict)
                    key = (bq_lnd.project_id, bq_lnd.landowner_id)
                    local_lnd = landowners_by_key.get(key)
                    if local_lnd is None or str(bq_lnd.updated_at) >= str(local_lnd.updated_at):
                        landowners_by_key[key] = bq_lnd
            except Exception as exc:
                logger.warning("BigQuery _list_landowners_raw fallback to SQLite: %s", exc)

        items = list(landowners_by_key.values())
        if project_id:
            items = [item for item in items if item.project_id == project_id]
        return sorted(items, key=lambda x: x.landowner_name.lower())

    def ensure_portfolio_seed(self) -> list[ProjectRow]:
        """Idempotently seed the 5 default Oracle ERP projects and bind any unassigned legacy documents."""
        self._ensure_local_dirs_and_db()
        existing_projects = {p.project_id: p for p in self._list_projects_raw()}
        now_iso = utc_now_iso()
        for seed in DEFAULT_ERP_PROJECTS:
            pid = str(seed["project_id"])
            if pid not in existing_projects:
                proj = ProjectRow(
                    project_id=pid,
                    project_name=str(seed["project_name"]),
                    energy_technology=EnergyTechnology(str(seed["energy_technology"])),
                    erp_project_code=str(seed["erp_project_code"]),
                    state_province=str(seed["state_province"]) if seed.get("state_province") else None,
                    county=str(seed["county"]) if seed.get("county") else None,
                    target_capacity_mw=float(seed["target_capacity_mw"]) if seed.get("target_capacity_mw") is not None else None,
                    landowner_count=0,
                    document_count=0,
                    special_conditions_count=0,
                    flagged_node_count=0,
                    updated_at=now_iso,
                )
                self.append_project_row(proj)
                existing_projects[pid] = proj

        if not self._portfolio_seeded:
            self._portfolio_seeded = True
            docs = self.list_documents()
            existing_lnds = {
                (l.project_id, l.landowner_id) for l in self._list_landowners_raw()
            }
            for doc in docs:
                if (
                    doc.landowner_id == "lnd_unassigned"
                    or not doc.grantor_landowner_name
                    or (doc.project_id, doc.landowner_id) not in existing_lnds
                ):
                    try:
                        bundle = self.get_bundle(doc.document_id)
                        self.bind_document_to_portfolio(
                            bundle,
                            project_id=doc.project_id or "prj_cedar_lantern_wind",
                            landowner_id=(
                                doc.landowner_id
                                if doc.landowner_id and doc.landowner_id != "lnd_unassigned"
                                else None
                            ),
                        )
                    except Exception as exc:
                        logger.warning(
                            "Portfolio auto-binding warning for %s: %s", doc.document_id, exc
                        )

        return self.list_projects()

    def list_projects(self, energy_technology: str | None = None) -> list[ProjectRow]:
        """Return deduplicated latest ProjectRow entries, seeding defaults if empty."""
        if not self._portfolio_seeded:
            self.ensure_portfolio_seed()
        projects = self._list_projects_raw()
        if not projects:
            self._portfolio_seeded = False
            self.ensure_portfolio_seed()
            projects = self._list_projects_raw()
        if energy_technology:
            projects = [
                p for p in projects if str(p.energy_technology) == str(energy_technology)
            ]
        return sorted(projects, key=lambda p: p.erp_project_code)

    def get_project(self, project_id: str) -> ProjectRow:
        """Retrieve a specific ProjectRow by project_id (or raise KeyError)."""
        projects = {p.project_id: p for p in self.list_projects()}
        if project_id in projects:
            return projects[project_id]
        raise KeyError(f"Project {project_id!r} not found")

    def upsert_project(self, project: ProjectRow) -> ProjectRow:
        """Create or update a ProjectRow in SQLite and BigQuery."""
        self.append_project_row(project)
        return project

    def list_landowners(self, project_id: str | None = None) -> list[LandownerRow]:
        """Return deduplicated latest LandownerRow entries, optionally filtered by project_id."""
        if not self._portfolio_seeded:
            self.ensure_portfolio_seed()
        return self._list_landowners_raw(project_id=project_id)

    @staticmethod
    def _extract_parties_from_bundle(
        bundle: ParsedContractBundle, project: ProjectRow
    ) -> tuple[str, str | None]:
        """Determine (grantor_landowner_name, grantee_entity_name) from bundle metadata or party JSON."""
        grantor = (bundle.document.grantor_landowner_name or "").strip() or None
        grantee = (bundle.document.grantee_entity_name or "").strip() or None
        if grantor and grantee:
            return grantor, grantee

        parties_raw = bundle.document.contracting_parties_json
        party_names: list[str] = []
        if parties_raw:
            try:
                parsed = json.loads(parties_raw)
                if isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, str) and item.strip():
                            party_names.append(item.strip())
                        elif isinstance(item, dict):
                            name = str(item.get("name") or item.get("party_name") or "").strip()
                            role = str(item.get("role") or "").lower()
                            if name:
                                if any(k in role for k in ("grantor", "landowner", "lessor", "owner")):
                                    grantor = grantor or name
                                elif any(k in role for k in ("grantee", "developer", "lessee", "company")):
                                    grantee = grantee or name
                                party_names.append(name)
            except Exception:
                pass

        if not grantor and len(party_names) >= 2:
            p0, p1 = party_names[0], party_names[1]
            proj_prefix = project.project_name.split()[0].lower()
            if proj_prefix in p0.lower() and proj_prefix not in p1.lower():
                grantee = grantee or p0
                grantor = p1
            elif proj_prefix in p1.lower() and proj_prefix not in p0.lower():
                grantee = grantee or p1
                grantor = p0
            else:
                dev_keywords = ("wind", "solar", "storage", "transmission", "geothermal", "energy", "invenergy")
                p0_dev = any(k in p0.lower() for k in dev_keywords)
                p1_dev = any(k in p1.lower() for k in dev_keywords)
                if p0_dev and not p1_dev:
                    grantee = grantee or p0
                    grantor = p1
                else:
                    grantor = p0
                    grantee = grantee or p1
        elif not grantor and len(party_names) == 1:
            grantor = party_names[0]

        if not grantor:
            grantor = Path(bundle.document.filename).stem.replace("_", " ")
        return grantor, grantee

    def bind_document_to_portfolio(
        self,
        bundle: ParsedContractBundle,
        project_id: str,
        landowner_id: str | None = None,
    ) -> tuple[ParsedContractBundle, ProjectRow, LandownerRow]:
        """Bind a ParsedContractBundle to a ProjectRow and auto-matched/created LandownerRow, updating rollups."""
        prev_project_id = (
            bundle.document.project_id
            if bundle.document.project_id and bundle.document.project_id != project_id
            else None
        )
        try:
            project = self.get_project(project_id)
        except KeyError:
            project = self.get_project("prj_cedar_lantern_wind")

        grantor_name, grantee_name = self._extract_parties_from_bundle(bundle, project)
        existing_landowners = self._list_landowners_raw(project_id=project.project_id)

        matched_landowner: LandownerRow | None = None
        if landowner_id and landowner_id != "lnd_unassigned":
            for lnd in existing_landowners:
                if lnd.landowner_id == landowner_id:
                    matched_landowner = lnd
                    break
        if matched_landowner is None:
            candidate_id = _make_landowner_id(project.project_id, grantor_name)
            grantor_canon = canonical_party_key(grantor_name)
            for lnd in existing_landowners:
                if (
                    lnd.landowner_id == candidate_id
                    or lnd.landowner_name.strip().lower() == grantor_name.strip().lower()
                    or (grantor_canon and canonical_party_key(lnd.landowner_name) == grantor_canon)
                ):
                    matched_landowner = lnd
                    break

        resolved_landowner_id = (
            matched_landowner.landowner_id
            if matched_landowner is not None
            else (
                landowner_id
                if (landowner_id and landowner_id != "lnd_unassigned")
                else _make_landowner_id(project.project_id, grantor_name)
            )
        )
        resolved_landowner_name = (
            matched_landowner.landowner_name
            if (matched_landowner is not None and not bundle.document.grantor_landowner_name)
            else grantor_name
        )

        parcel_clauses = [
            c
            for c in bundle.clauses
            if c.document_zone == "EXHIBIT_OR_SCHEDULE"
            and "EXCEPT" not in c.node_id.upper()
            and (
                ".PARCEL_" in c.node_id.upper()
                or (c.clause_label or "").strip().lower().startswith("parcel ")
            )
        ]
        has_multiple_parcels = len(parcel_clauses) >= 2
        if has_multiple_parcels:
            labels_str = ", ".join(
                f"{c.clause_label} ({c.node_id})" for c in parcel_clauses[:4]
            )
            parcel_summary = f"{len(parcel_clauses)} Parcels: {labels_str}"
        elif matched_landowner and matched_landowner.parcel_summary:
            parcel_summary = matched_landowner.parcel_summary
        else:
            parcel_summary = "Single Parcel / Standard Agreement"

        now_iso = utc_now_iso()
        updated_doc = bundle.document.model_copy(
            update={
                "project_id": project.project_id,
                "landowner_id": resolved_landowner_id,
                "energy_technology": project.energy_technology,
                "grantor_landowner_name": resolved_landowner_name,
                "grantee_entity_name": grantee_name,
                "updated_at": now_iso,
            }
        )
        updated_scs = [
            sc.model_copy(
                update={
                    "project_id": project.project_id,
                    "landowner_id": resolved_landowner_id,
                    "updated_at": now_iso,
                }
            )
            for sc in bundle.special_conditions
        ]

        doc_changed = (
            updated_doc.project_id != bundle.document.project_id
            or updated_doc.landowner_id != bundle.document.landowner_id
            or updated_doc.energy_technology != bundle.document.energy_technology
            or updated_doc.grantor_landowner_name != bundle.document.grantor_landowner_name
            or updated_doc.grantee_entity_name != bundle.document.grantee_entity_name
        )
        scs_changed = any(
            a.project_id != b.project_id or a.landowner_id != b.landowner_id
            for a, b in zip(updated_scs, bundle.special_conditions)
        )

        updated_bundle = ParsedContractBundle(
            document=updated_doc,
            clauses=bundle.clauses,
            defined_terms=bundle.defined_terms,
            exhibits_catalog=bundle.exhibits_catalog,
            special_conditions=updated_scs,
        )
        if doc_changed:
            self.append_document_row(updated_doc)
        if scs_changed:
            self.append_special_conditions(updated_scs)
            self.export_csvs(updated_bundle)

        qrm_party_id = (
            matched_landowner.qrm_party_id
            if (matched_landowner and matched_landowner.qrm_party_id)
            else f"QRM-{hashlib.sha256(resolved_landowner_id.encode('utf-8')).hexdigest()[:6].upper()}"
        )
        seed_landowner = LandownerRow(
            landowner_id=resolved_landowner_id,
            project_id=project.project_id,
            landowner_name=resolved_landowner_name,
            qrm_party_id=qrm_party_id,
            grantee_entity_name=grantee_name or (matched_landowner.grantee_entity_name if matched_landowner else None),
            parcel_summary=parcel_summary,
            is_multi_parcel=has_multiple_parcels or (matched_landowner.is_multi_parcel if matched_landowner else False),
            contract_count=matched_landowner.contract_count if matched_landowner else 1,
            special_conditions_count=updated_doc.special_conditions_count,
            updated_at=now_iso,
        )
        self.append_landowner_row(seed_landowner)

        updated_project, updated_landowners = self.recalculate_project_rollups(
            project.project_id
        )
        if prev_project_id and prev_project_id != project.project_id:
            try:
                self.recalculate_project_rollups(prev_project_id)
            except KeyError:
                pass
        final_landowner = next(
            (l for l in updated_landowners if l.landowner_id == resolved_landowner_id),
            seed_landowner,
        )
        return updated_bundle, updated_project, final_landowner

    def recalculate_project_rollups(
        self, project_id: str
    ) -> tuple[ProjectRow, list[LandownerRow]]:
        """Recompute ProjectRow and LandownerRow roll-up counters from deduplicated latest documents."""
        project = self.get_project(project_id)
        project_docs = self.list_documents(project_id=project_id)
        existing_landowners = {
            l.landowner_id: l for l in self._list_landowners_raw(project_id=project_id)
        }
        now_iso = utc_now_iso()

        docs_by_landowner: dict[str, list[DocumentRegistryRow]] = {}
        for d in project_docs:
            if d.landowner_id and d.landowner_id != "lnd_unassigned":
                docs_by_landowner.setdefault(d.landowner_id, []).append(d)

        all_landowner_ids = set(existing_landowners.keys()) | set(docs_by_landowner.keys())
        updated_landowners: list[LandownerRow] = []
        for lid in sorted(all_landowner_ids):
            l_docs = docs_by_landowner.get(lid, [])
            existing = existing_landowners.get(lid)
            contract_count = len(l_docs)
            sc_count = sum(d.special_conditions_count for d in l_docs)
            is_multi = (existing.is_multi_parcel if existing else False) or (contract_count > 1)
            l_name = (
                existing.landowner_name
                if existing
                else (l_docs[0].grantor_landowner_name or lid if l_docs else lid)
            )
            grantee = (
                existing.grantee_entity_name
                if (existing and existing.grantee_entity_name)
                else (l_docs[0].grantee_entity_name if l_docs else None)
            )
            qrm_id = (
                existing.qrm_party_id
                if (existing and existing.qrm_party_id)
                else f"QRM-{hashlib.sha256(lid.encode('utf-8')).hexdigest()[:6].upper()}"
            )
            base_summary = (
                existing.parcel_summary
                if (existing and existing.parcel_summary)
                else "Single Parcel / Standard Agreement"
            )
            is_generic_summary = (
                base_summary == "Single Parcel / Standard Agreement"
                or base_summary.startswith("Multi-Contract Stack (")
            )
            if contract_count > 1 and is_generic_summary:
                computed_summary = f"Multi-Contract Stack ({contract_count} Agreements)"
            elif contract_count <= 1 and is_generic_summary:
                computed_summary = "Single Parcel / Standard Agreement"
            else:
                computed_summary = base_summary
            new_lnd = LandownerRow(
                landowner_id=lid,
                project_id=project_id,
                landowner_name=l_name,
                qrm_party_id=qrm_id,
                grantee_entity_name=grantee,
                parcel_summary=computed_summary,
                is_multi_parcel=is_multi,
                contract_count=contract_count,
                special_conditions_count=sc_count,
                updated_at=now_iso,
            )
            if (
                existing is None
                or existing.contract_count != new_lnd.contract_count
                or existing.special_conditions_count != new_lnd.special_conditions_count
                or existing.is_multi_parcel != new_lnd.is_multi_parcel
                or existing.parcel_summary != new_lnd.parcel_summary
            ):
                self.append_landowner_row(new_lnd)
                updated_landowners.append(new_lnd)
            else:
                updated_landowners.append(existing)

        active_landowner_count = sum(1 for l in updated_landowners if l.contract_count > 0)
        new_proj = project.model_copy(
            update={
                "landowner_count": active_landowner_count,
                "document_count": len(project_docs),
                "special_conditions_count": sum(
                    d.special_conditions_count for d in project_docs
                ),
                "flagged_node_count": sum(d.flagged_node_count for d in project_docs),
                "updated_at": now_iso,
            }
        )
        if (
            project.landowner_count != new_proj.landowner_count
            or project.document_count != new_proj.document_count
            or project.special_conditions_count != new_proj.special_conditions_count
            or project.flagged_node_count != new_proj.flagged_node_count
        ):
            self.append_project_row(new_proj)
            return new_proj, updated_landowners
        return project, updated_landowners

    def _list_special_conditions_for_documents(
        self, document_ids: set[str]
    ) -> dict[str, list[SpecialConditionRow]]:
        """Batch-load deduplicated SpecialConditionRow items for the given document_ids without N+1 full-bundle queries."""
        if not document_ids:
            return {}
        self._ensure_local_dirs_and_db()
        scs_by_key: dict[tuple[str, str], SpecialConditionRow] = {}
        doc_id_list = sorted(document_ids)
        rows: list[sqlite3.Row] = []

        with sqlite3.connect(self.local_db_path) as conn:
            conn.row_factory = sqlite3.Row
            for i in range(0, len(doc_id_list), 900):
                chunk = doc_id_list[i : i + 900]
                placeholders = ", ".join(["?"] * len(chunk))
                rows.extend(
                    conn.execute(
                        f"""
                        SELECT * FROM (
                            SELECT *, ROW_NUMBER() OVER (PARTITION BY document_id, condition_id ORDER BY updated_at DESC, rowid DESC) AS rn
                            FROM special_conditions
                            WHERE document_id IN ({placeholders})
                        ) WHERE rn = 1
                        ORDER BY page_number ASC, condition_id ASC
                        """,
                        chunk,
                    ).fetchall()
                )
        for r in rows:
            r_keys = set(r.keys())
            sc_obj = SpecialConditionRow.model_validate(
                {
                    k: self._coalesce_sc_field(k, r[k] if k in r_keys else None)
                    for k in SPECIAL_CONDITION_CSV_COLUMNS
                }
            )
            scs_by_key[(sc_obj.document_id, sc_obj.condition_id)] = sc_obj

        if self.config.use_bigquery:
            try:
                self.ensure_bq_tables()
                params = [
                    bigquery.ArrayQueryParameter("doc_ids", "STRING", doc_id_list)
                ]
                job_config = bigquery.QueryJobConfig(query_parameters=params)
                sql = f"""
                    SELECT {", ".join(SPECIAL_CONDITION_CSV_COLUMNS)}
                    FROM `{self.config.bq_dataset_fqn}.special_conditions`
                    WHERE document_id IN UNNEST(@doc_ids)
                    QUALIFY ROW_NUMBER() OVER (PARTITION BY document_id, condition_id ORDER BY updated_at DESC) = 1
                    ORDER BY page_number ASC, condition_id ASC
                """
                for r in self.bq_client.query(sql, job_config=job_config).result():
                    row_dict: dict[str, object] = {}
                    for k in SPECIAL_CONDITION_CSV_COLUMNS:
                        val = r[k]
                        if hasattr(val, "isoformat"):
                            val = val.isoformat()
                        row_dict[k] = self._coalesce_sc_field(k, val)
                    bq_sc = SpecialConditionRow.model_validate(row_dict)
                    key = (bq_sc.document_id, bq_sc.condition_id)
                    existing = scs_by_key.get(key)
                    if existing is None or str(bq_sc.updated_at) >= str(existing.updated_at):
                        scs_by_key[key] = bq_sc
            except Exception as exc:
                logger.warning(
                    "BigQuery _list_special_conditions_for_documents fallback to SQLite: %s",
                    exc,
                )

        grouped: dict[str, list[SpecialConditionRow]] = {}
        for sc in scs_by_key.values():
            grouped.setdefault(sc.document_id, []).append(sc)
        for doc_scs in grouped.values():
            doc_scs.sort(key=lambda s: (s.page_number, s.condition_id))
        return grouped

    def search_portfolio(
        self,
        *,
        project_id: str | None = None,
        landowner_id: str | None = None,
        energy_technology: str | None = None,
        constraint_category: str | None = None,
        hitl_status: str | None = None,
        q: str | None = None,
    ) -> dict[str, object]:
        """Centralized cross-project & cross-category search across the 7-table portfolio hierarchy."""
        self.ensure_portfolio_seed()
        all_projects = {p.project_id: p for p in self.list_projects()}
        filtered_projects = self.list_projects(energy_technology=energy_technology)
        if project_id:
            filtered_projects = [p for p in filtered_projects if p.project_id == project_id]

        allowed_project_ids = {p.project_id for p in filtered_projects}
        all_landowners = {
            (l.project_id, l.landowner_id): l for l in self.list_landowners()
        }
        filtered_landowners = [
            l
            for l in all_landowners.values()
            if l.project_id in allowed_project_ids
            and (not landowner_id or l.landowner_id == landowner_id)
        ]

        docs = self.list_documents(
            project_id=project_id,
            landowner_id=landowner_id,
            energy_technology=energy_technology,
        )
        docs_by_id = {d.document_id: d for d in docs}
        scs_by_doc = self._list_special_conditions_for_documents(set(docs_by_id.keys()))

        query_lower = q.strip().lower() if q and q.strip() else None
        enriched_conditions: list[dict[str, object]] = []
        category_breakdown: dict[str, int] = {}
        technology_breakdown: dict[str, int] = {}
        category_aliases: dict[str, set[str]] = {
            "SETBACK_OR_BUFFER": {"SETBACK_OR_BUFFER", "STRUCTURE_BARN_WELL_SETBACK"},
            "STRUCTURE_BARN_WELL_SETBACK": {"SETBACK_OR_BUFFER", "STRUCTURE_BARN_WELL_SETBACK"},
            "CROP_OR_TIMBER_COMPENSATION": {"CROP_OR_TIMBER_COMPENSATION", "TREE_VEGETATION_PROTECTION"},
            "TREE_VEGETATION_PROTECTION": {"CROP_OR_TIMBER_COMPENSATION", "TREE_VEGETATION_PROTECTION"},
            "CONSTRUCTION_OR_BLACKOUT_WINDOW": {
                "CONSTRUCTION_OR_BLACKOUT_WINDOW",
                "TIMING_NOISE_HUNTING_BLACKOUT",
            },
            "TIMING_NOISE_HUNTING_BLACKOUT": {
                "CONSTRUCTION_OR_BLACKOUT_WINDOW",
                "TIMING_NOISE_HUNTING_BLACKOUT",
                "NOISE_OR_SHADOW_FLICKER",
                "BLASTING_OR_EXCAVATION",
            },
            "NOISE_OR_SHADOW_FLICKER": {"NOISE_OR_SHADOW_FLICKER", "TIMING_NOISE_HUNTING_BLACKOUT"},
            "BLASTING_OR_EXCAVATION": {"BLASTING_OR_EXCAVATION", "TIMING_NOISE_HUNTING_BLACKOUT"},
            "GATES_FENCING_OR_LIVESTOCK": {
                "GATES_FENCING_OR_LIVESTOCK",
                "LIVESTOCK_AGRICULTURE",
            },
            "LIVESTOCK_AGRICULTURE": {
                "GATES_FENCING_OR_LIVESTOCK",
                "LIVESTOCK_AGRICULTURE",
                "DRAINAGE_OR_SOIL_RESTORATION",
            },
            "DRAINAGE_OR_SOIL_RESTORATION": {"DRAINAGE_OR_SOIL_RESTORATION", "LIVESTOCK_AGRICULTURE"},
            "ACCESS_ROAD_OR_PARCEL_RESTRICTION": {"ACCESS_ROAD_OR_PARCEL_RESTRICTION", "ACCESS_ROAD_GATE_PROTOCOL"},
            "ACCESS_ROAD_GATE_PROTOCOL": {"ACCESS_ROAD_OR_PARCEL_RESTRICTION", "ACCESS_ROAD_GATE_PROTOCOL"},
            "DECOMMISSIONING_OR_BOND": {"DECOMMISSIONING_OR_BOND", "FINANCIAL_PENALTY_LIQUIDATED_DAMAGES"},
            "FINANCIAL_PENALTY_LIQUIDATED_DAMAGES": {
                "DECOMMISSIONING_OR_BOND",
                "FINANCIAL_PENALTY_LIQUIDATED_DAMAGES",
            },
            "OTHER_SPECIAL_CONDITION": {"OTHER_SPECIAL_CONDITION", "OTHER_CUSTOM_RIDER"},
            "OTHER_CUSTOM_RIDER": {"OTHER_SPECIAL_CONDITION", "OTHER_CUSTOM_RIDER"},
        }
        allowed_categories = (
            category_aliases.get(str(constraint_category), {str(constraint_category)})
            if constraint_category
            else None
        )

        for doc in docs:
            doc_conditions = scs_by_doc.get(doc.document_id, [])
            proj = all_projects.get(doc.project_id)
            lnd = all_landowners.get((doc.project_id, doc.landowner_id))
            proj_name = proj.project_name if proj else doc.project_id
            tech_str = str(proj.energy_technology if proj else doc.energy_technology)
            lnd_name = (
                lnd.landowner_name
                if lnd
                else (doc.grantor_landowner_name or doc.landowner_id)
            )

            for sc in doc_conditions:
                if allowed_categories and str(sc.constraint_category) not in allowed_categories:
                    continue
                if hitl_status and str(sc.hitl_status) != str(hitl_status):
                    continue
                if query_lower:
                    haystack = " ".join(
                        filter(
                            None,
                            [
                                sc.actionable_obligation_summary,
                                sc.verbatim_excerpt,
                                sc.target_asset_or_area,
                                sc.quantitative_metric,
                                sc.temporal_restriction,
                                sc.penalty_or_consequence,
                                sc.canonical_path,
                                str(sc.constraint_category),
                                proj_name,
                                lnd_name,
                                doc.filename,
                            ],
                        )
                    ).lower()
                    if query_lower not in haystack:
                        continue

                cat_key = str(sc.constraint_category)
                category_breakdown[cat_key] = category_breakdown.get(cat_key, 0) + 1
                technology_breakdown[tech_str] = technology_breakdown.get(tech_str, 0) + 1

                item = sc.model_dump(mode="json")
                item["project_name"] = proj_name
                item["erp_project_code"] = proj.erp_project_code if proj else ""
                item["energy_technology"] = tech_str
                item["landowner_name"] = lnd_name
                item["is_multi_parcel"] = lnd.is_multi_parcel if lnd else False
                item["filename"] = doc.filename
                enriched_conditions.append(item)

        if query_lower:
            matching_doc_ids = {str(c["document_id"]) for c in enriched_conditions}
            filtered_docs = [
                d
                for d in docs
                if d.document_id in matching_doc_ids
                or query_lower in d.filename.lower()
                or query_lower in (d.grantor_landowner_name or "").lower()
                or query_lower in (d.grantee_entity_name or "").lower()
            ]
        else:
            filtered_docs = docs

        return {
            "count": len(enriched_conditions),
            "total_projects": len(filtered_projects),
            "total_landowners": len(filtered_landowners),
            "total_documents": len(filtered_docs),
            "total_matching_conditions": len(enriched_conditions),
            "category_breakdown": category_breakdown,
            "technology_breakdown": technology_breakdown,
            "projects": [p.model_dump(mode="json") for p in filtered_projects],
            "landowners": [l.model_dump(mode="json") for l in filtered_landowners],
            "documents": [d.model_dump(mode="json") for d in filtered_docs],
            "results": enriched_conditions,
        }

    def review_clause(
        self,
        document_id: str,
        node_id: str,
        review: ClauseReviewRequest,
    ) -> tuple[ClauseRow, DocumentRegistryRow]:
        """Append a new human-reviewed ClauseRow version, cascade to attached SpecialConditionRow items, and update DocumentRegistryRow."""
        bundle = self.get_bundle(document_id)
        # If local SQLite didn't have the full bundle yet, hydrate it locally before appending the partial update
        if self._get_bundle_from_sqlite(document_id) is None:
            self._append_bundle_rows(bundle, sqlite_only=True)

        target_clause: ClauseRow | None = None
        for c in bundle.clauses:
            if c.node_id == node_id:
                target_clause = c
                break
        if target_clause is None:
            raise KeyError(f"Clause {node_id!r} not found in document {document_id!r}")

        now_iso = utc_now_iso()
        updated_clause = target_clause.model_copy(
            update={
                "hitl_status": review.hitl_status,
                "verbatim_text": (
                    review.verbatim_text
                    if review.verbatim_text is not None
                    else target_clause.verbatim_text
                ),
                "reconstructed_context_text": (
                    review.reconstructed_context_text
                    if review.reconstructed_context_text is not None
                    else target_clause.reconstructed_context_text
                ),
                "clause_label": (
                    review.clause_label
                    if review.clause_label is not None
                    else target_clause.clause_label
                ),
                "reviewed_by": review.reviewed_by,
                "review_notes": review.review_notes,
                "updated_at": now_iso,
            }
        )
        self.append_clauses([updated_clause])

        # CR-1: Cascade human review status & reviewer to all SpecialConditionRow items attached to node_id
        updated_node_conditions: list[SpecialConditionRow] = []
        updated_all_conditions: list[SpecialConditionRow] = []
        for sc in bundle.special_conditions:
            if sc.node_id == node_id:
                updated_sc = sc.model_copy(
                    update={
                        "hitl_status": review.hitl_status,
                        "reviewed_by": review.reviewed_by,
                        "updated_at": now_iso,
                    }
                )
                updated_node_conditions.append(updated_sc)
                updated_all_conditions.append(updated_sc)
            else:
                updated_all_conditions.append(sc)
        if updated_node_conditions:
            self.append_special_conditions(updated_node_conditions)

        updated_clauses = [
            updated_clause if c.node_id == node_id else c for c in bundle.clauses
        ]
        remaining_flags = sum(
            1
            for c in updated_clauses
            if c.hitl_status
            in (HITLStatus.FLAGGED_FOR_REVIEW, HITLStatus.PLACEHOLDER_FOR_REVIEW)
        )
        new_doc_status = (
            IngestionStatus.VERIFIED_COMPLETE
            if remaining_flags == 0
            else IngestionStatus.NEEDS_HITL_REVIEW
        )
        updated_doc = bundle.document.model_copy(
            update={
                "flagged_node_count": remaining_flags,
                "special_conditions_count": len(updated_all_conditions),
                "ingestion_status": new_doc_status,
                "updated_at": now_iso,
            }
        )
        self.append_document_row(updated_doc)

        updated_bundle = ParsedContractBundle(
            document=updated_doc,
            clauses=updated_clauses,
            defined_terms=bundle.defined_terms,
            exhibits_catalog=bundle.exhibits_catalog,
            special_conditions=updated_all_conditions,
        )
        self.export_csvs(updated_bundle)
        try:
            self.recalculate_project_rollups(updated_doc.project_id)
        except Exception as exc:
            logger.warning("Project roll-up update warning on review_clause: %s", exc)
        return updated_clause, updated_doc

    def get_csv_bytes(self, document_id: str, csv_name: str) -> bytes:
        """Return the latest deduplicated CSV export bytes (`utf-8-sig`)."""
        self._ensure_local_dirs_and_db()
        if csv_name not in (
            "clauses.csv",
            "defined_terms.csv",
            "exhibits_catalog.csv",
            "special_conditions.csv",
            "dnd_checklist_signoffs.csv",
        ):
            raise ValueError(f"Unsupported CSV name: {csv_name!r}")
        local_csv = self.local_exports_dir / document_id / csv_name
        if not local_csv.exists():
            bundle = self.get_bundle(document_id)
            self.export_csvs(bundle)
        return local_csv.read_bytes()

    def list_dnd_signoffs(self, document_id: str) -> list[DNDChecklistSignoffRow]:
        """Return deduplicated Pre-Job Tailgate Briefing Sign-Offs for document_id ordered by signed_at DESC."""
        self._ensure_local_dirs_and_db()
        signoffs_by_id: dict[str, DNDChecklistSignoffRow] = {}
        with sqlite3.connect(self.local_db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY document_id, signoff_id ORDER BY signed_at DESC, rowid DESC) AS rn
                    FROM dnd_checklist_signoffs
                    WHERE document_id = ?
                ) WHERE rn = 1
                ORDER BY signed_at DESC
                """,
                (document_id,),
            ).fetchall()
        for r in rows:
            row_obj = DNDChecklistSignoffRow.model_validate(
                {k: r[k] for k in DND_SIGNOFF_CSV_COLUMNS}
            )
            signoffs_by_id[row_obj.signoff_id] = row_obj

        if self.config.use_bigquery:
            try:
                self.ensure_bq_tables()
                params = [bigquery.ScalarQueryParameter("doc_id", "STRING", document_id)]
                job_config = bigquery.QueryJobConfig(query_parameters=params)
                sql = f"""
                    SELECT {", ".join(DND_SIGNOFF_CSV_COLUMNS)}
                    FROM `{self.config.bq_dataset_fqn}.dnd_checklist_signoffs`
                    WHERE document_id = @doc_id
                    QUALIFY ROW_NUMBER() OVER (PARTITION BY document_id, signoff_id ORDER BY signed_at DESC) = 1
                    ORDER BY signed_at DESC
                """
                for r in self.bq_client.query(sql, job_config=job_config).result():
                    row_dict: dict[str, object] = {}
                    for k in DND_SIGNOFF_CSV_COLUMNS:
                        val = r[k]
                        if hasattr(val, "isoformat"):
                            val = val.isoformat()
                        row_dict[k] = val
                    bq_obj = DNDChecklistSignoffRow.model_validate(row_dict)
                    existing = signoffs_by_id.get(bq_obj.signoff_id)
                    if existing is None or str(bq_obj.signed_at) >= str(existing.signed_at):
                        signoffs_by_id[bq_obj.signoff_id] = bq_obj
            except Exception as exc:
                logger.warning("BigQuery list_dnd_signoffs fallback to SQLite for %s: %s", document_id, exc)

        return sorted(
            signoffs_by_id.values(),
            key=lambda s: str(s.signed_at),
            reverse=True,
        )

    def get_contract_dnd_checklist(
        self,
        document_id: str,
        *,
        construction_trade: str | None = None,
        severity_level: str | None = None,
    ) -> DNDChecklistBundle:
        """Synthesize a single-contract Field Crew Do-Not-Disturb Checklist with safety interlocks and sign-off history (CR-3)."""
        self.ensure_portfolio_seed()
        bundle = self.get_bundle(document_id)
        doc = bundle.document
        proj_id = doc.project_id or "prj_cedar_lantern_wind"
        try:
            proj = self.get_project(proj_id)
        except KeyError:
            proj = ProjectRow(
                project_id=proj_id,
                project_name=proj_id,
                energy_technology=doc.energy_technology,
                erp_project_code="ERP-UNASSIGNED",
            )

        landowners = self.list_landowners(project_id=proj.project_id)
        matched_lnd = next(
            (l for l in landowners if l.landowner_id == doc.landowner_id),
            None,
        )
        landowner_id = (
            matched_lnd.landowner_id
            if matched_lnd
            else (doc.landowner_id or "lnd_unassigned")
        )
        landowner_name = (
            matched_lnd.landowner_name
            if matched_lnd
            else (doc.grantor_landowner_name or landowner_id)
        )
        parcel_summary = matched_lnd.parcel_summary if matched_lnd else None

        severity_priority = {
            DNDSeverityLevel.RED_ZONE_NO_GO: 0,
            DNDSeverityLevel.SEASONAL_BLACKOUT: 1,
            DNDSeverityLevel.MANDATORY_PROTOCOL: 2,
        }
        all_items = [build_dnd_checklist_item(sc) for sc in bundle.special_conditions]
        all_items.sort(
            key=lambda item: (
                severity_priority.get(item.severity_level, 99),
                item.page_number,
                item.condition_id,
            )
        )

        total_items = len(all_items)
        red_zone_count = sum(
            1 for i in all_items if i.severity_level == DNDSeverityLevel.RED_ZONE_NO_GO
        )
        seasonal_blackout_count = sum(
            1 for i in all_items if i.severity_level == DNDSeverityLevel.SEASONAL_BLACKOUT
        )
        mandatory_protocol_count = sum(
            1 for i in all_items if i.severity_level == DNDSeverityLevel.MANDATORY_PROTOCOL
        )
        cleared_count = sum(
            1
            for i in all_items
            if i.dispatch_clearance == DNDDispatchClearance.CLEARED_FOR_DISPATCH
        )
        hold_count = sum(
            1
            for i in all_items
            if i.dispatch_clearance == DNDDispatchClearance.HOLD_VERIFY_WITH_LAND_AGENT
        )

        trade_breakdown: dict[str, int] = {}
        for i in all_items:
            t_key = str(i.construction_trade)
            trade_breakdown[t_key] = trade_breakdown.get(t_key, 0) + 1

        if total_items == 0:
            dispatch_readiness = DNDDispatchReadiness.NO_CONSTRAINTS_IDENTIFIED
        elif hold_count == 0:
            dispatch_readiness = DNDDispatchReadiness.READY_FOR_DISPATCH
        else:
            dispatch_readiness = DNDDispatchReadiness.HOLD_PENDING_HITL

        filtered_items = all_items
        if construction_trade:
            filtered_items = [
                i
                for i in filtered_items
                if str(i.construction_trade) == str(construction_trade)
            ]
        if severity_level:
            filtered_items = [
                i
                for i in filtered_items
                if str(i.severity_level) == str(severity_level)
            ]

        signoffs = self.list_dnd_signoffs(document_id)
        return DNDChecklistBundle(
            document_id=doc.document_id,
            filename=doc.filename,
            project_id=proj.project_id,
            project_name=proj.project_name,
            erp_project_code=proj.erp_project_code,
            energy_technology=proj.energy_technology,
            landowner_id=landowner_id,
            landowner_name=landowner_name,
            parcel_summary=parcel_summary,
            dispatch_readiness=dispatch_readiness,
            total_items=total_items,
            filtered_items_count=len(filtered_items),
            red_zone_count=red_zone_count,
            seasonal_blackout_count=seasonal_blackout_count,
            mandatory_protocol_count=mandatory_protocol_count,
            cleared_count=cleared_count,
            hold_count=hold_count,
            trade_breakdown=trade_breakdown,
            items=filtered_items,
            signoffs=signoffs,
        )

    def record_dnd_checklist_signoff(
        self,
        document_id: str,
        req: CreateDNDSignoffRequest,
    ) -> DNDChecklistBundle:
        """Validate and persist a Subcontractor Pre-Job Tailgate Briefing Sign-Off (8th table) and return updated DNDChecklistBundle."""
        subcontractor_company = (req.subcontractor_company or "").strip()
        foreman_name = (req.foreman_name or "").strip()
        if not subcontractor_company:
            raise ValueError("subcontractor_company is required for DND checklist sign-off")
        if not foreman_name:
            raise ValueError("foreman_name is required for DND checklist sign-off")

        checklist = self.get_contract_dnd_checklist(document_id)
        valid_condition_ids = {item.condition_id for item in checklist.items}

        raw_ids = req.acknowledged_condition_ids
        if isinstance(raw_ids, str):
            parsed_ids = [
                token.strip()
                for token in raw_ids.replace(",", "|").split("|")
                if token.strip()
            ]
        else:
            parsed_ids = [str(token).strip() for token in raw_ids if str(token).strip()]

        dedup_ids: list[str] = []
        for cid in parsed_ids:
            if cid not in dedup_ids:
                dedup_ids.append(cid)

        if not dedup_ids:
            raise ValueError("At least one acknowledged condition_id must be provided")

        invalid_ids = [cid for cid in dedup_ids if cid not in valid_condition_ids]
        if invalid_ids:
            raise ValueError(
                f"Unknown condition_id(s) for document {document_id!r}: {', '.join(invalid_ids)}"
            )

        now_iso = utc_now_iso()
        doc_short = document_id.removeprefix("doc_")[:8]
        sig_hash = hashlib.sha256(
            f"{document_id}|{subcontractor_company}|{foreman_name}|{'|'.join(dedup_ids)}|{now_iso}".encode(
                "utf-8"
            )
        ).hexdigest()[:8]
        signoff_id = f"sig_{doc_short}_{sig_hash}"

        notes_clean = (
            req.briefing_notes.strip()
            if req.briefing_notes and req.briefing_notes.strip()
            else None
        )
        signoff_row = DNDChecklistSignoffRow(
            signoff_id=signoff_id,
            document_id=document_id,
            project_id=checklist.project_id,
            landowner_id=checklist.landowner_id,
            subcontractor_company=subcontractor_company,
            foreman_name=foreman_name,
            construction_trade=req.construction_trade,
            acknowledged_condition_ids="|".join(dedup_ids),
            acknowledged_count=len(dedup_ids),
            dispatch_readiness_at_signoff=checklist.dispatch_readiness,
            briefing_notes=notes_clean,
            signed_at=now_iso,
        )
        self.append_dnd_signoff_row(signoff_row)
        self._export_dnd_signoffs_csv(document_id)
        return self.get_contract_dnd_checklist(document_id)

    @staticmethod
    def load_bundle_from_csv(
        export_dir: Path, document_row: DocumentRegistryRow
    ) -> ParsedContractBundle:
        """Reload a ParsedContractBundle from exported utf-8-sig CSVs (verifies Session 2, CR-1 & CR-2 compatibility)."""
        def _read_csv(path: Path) -> list[dict[str, str | None]]:
            content = path.read_bytes().decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(content))
            rows: list[dict[str, str | None]] = []
            for r in reader:
                rows.append({k: (None if v == "" else v) for k, v in r.items()})
            return rows

        clause_dicts = _read_csv(export_dir / "clauses.csv")
        term_dicts = _read_csv(export_dir / "defined_terms.csv")
        exhibit_dicts = _read_csv(export_dir / "exhibits_catalog.csv")
        sc_path = export_dir / "special_conditions.csv"
        sc_dicts = _read_csv(sc_path) if sc_path.exists() else []

        return ParsedContractBundle(
            document=document_row,
            clauses=[
                ClauseRow.model_validate(
                    {
                        **r,
                        "sibling_order": int(r["sibling_order"] or 1),
                        "depth": int(r["depth"] or 1),
                        "is_inline_clause": str(r["is_inline_clause"]).lower() in ("true", "1"),
                        "page_start": int(r["page_start"] or 1),
                        "page_end": int(r["page_end"] or 1),
                        "has_special_condition": str(r.get("has_special_condition", "false")).lower() in ("true", "1"),
                        "special_condition_count": int(r.get("special_condition_count") or 0),
                    }
                )
                for r in clause_dicts
            ],
            defined_terms=[
                DefinedTermRow.model_validate(
                    {
                        **r,
                        "page_number": int(r["page_number"] or 1),
                    }
                )
                for r in term_dicts
            ],
            exhibits_catalog=[
                ExhibitCatalogRow.model_validate(
                    {
                        **r,
                        "page_start": int(r["page_start"] or 1),
                        "page_end": int(r["page_end"] or 1),
                        "has_unresolved_external_dep": str(r["has_unresolved_external_dep"]).lower() in ("true", "1"),
                    }
                )
                for r in exhibit_dicts
            ],
            special_conditions=[
                SpecialConditionRow.model_validate(
                    {
                        **r,
                        "page_number": int(r["page_number"] or 1),
                        "extraction_confidence": float(r["extraction_confidence"] or 0.95),
                        "project_id": r.get("project_id") or document_row.project_id or "prj_cedar_lantern_wind",
                        "landowner_id": r.get("landowner_id") or document_row.landowner_id or "lnd_unassigned",
                    }
                )
                for r in sc_dicts
            ],
        )

    def append_template_row(
        self, template: TemplateRegistryRow, *, sqlite_only: bool = False
    ) -> None:
        """Append a new version of TemplateRegistryRow to SQLite and BigQuery."""
        self._append_table_rows(
            "template_registry",
            TEMPLATE_REGISTRY_COLUMNS,
            [template.model_dump(mode="json")],
            sqlite_only=sqlite_only,
        )

    def append_template_clauses(
        self, clauses: list[TemplateClauseRow], *, sqlite_only: bool = False
    ) -> None:
        """Append template clause AST nodes to SQLite and BigQuery."""
        self._append_table_rows(
            "template_clauses",
            TEMPLATE_CLAUSES_COLUMNS,
            [c.model_dump(mode="json") for c in clauses],
            sqlite_only=sqlite_only,
        )

    def insert_template_bundle(
        self, bundle: TemplateExtractionBundle, *, sqlite_only: bool = False
    ) -> None:
        """Idempotently persist template metadata and clauses into SQLite and BigQuery."""
        self.append_template_row(bundle.metadata, sqlite_only=sqlite_only)
        self.append_template_clauses(bundle.clauses, sqlite_only=sqlite_only)

    def list_templates(
        self,
        country: str | None = None,
        state_province: str | None = None,
        energy_technology: str | None = None,
        form_type: str | None = None,
        is_active: bool | None = None,
        vintage_year: int | str | None = None,
    ) -> list[TemplateRegistryRow]:
        """Retrieve active master templates filtered by taxonomy dimensions."""
        self._ensure_local_dirs_and_db()
        query = """
        SELECT * FROM (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY template_id ORDER BY updated_at DESC, rowid DESC) AS rn
            FROM template_registry
        ) WHERE rn = 1
        """
        params: list[object] = []
        conditions: list[str] = []
        if country:
            conditions.append("UPPER(country) = UPPER(?)")
            params.append(country)
        if state_province:
            conditions.append("UPPER(state_province) = UPPER(?)")
            params.append(state_province)
        if energy_technology:
            tech_str = str(energy_technology).upper()
            if tech_str == "WIND":
                conditions.append("(UPPER(energy_technology) = 'WIND' OR UPPER(energy_technology) = 'ONSHORE_WIND')")
            else:
                conditions.append("UPPER(energy_technology) = UPPER(?)")
                params.append(energy_technology)
        if form_type:
            conditions.append("UPPER(form_type) = UPPER(?)")
            params.append(form_type)
        if vintage_year is not None:
            conditions.append("vintage_year = ?")
            params.append(int(vintage_year) if str(vintage_year).isdigit() else vintage_year)
        if is_active is not None:
            conditions.append("is_active = ?")
            params.append(1 if is_active else 0)

        if conditions:
            query += " AND " + " AND ".join(conditions)

        query += " ORDER BY state_province ASC, energy_technology ASC, form_type ASC, template_id ASC"

        with sqlite3.connect(self.local_db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()

        templates: list[TemplateRegistryRow] = []
        for r in rows:
            data = dict(r)
            data["is_active"] = bool(data.get("is_active", 1))
            templates.append(TemplateRegistryRow.model_validate(data))
        return templates

    def get_template_bundle(self, template_id: str) -> TemplateExtractionBundle | None:
        """Retrieve full template metadata and ordered clause AST by template_id."""
        self._ensure_local_dirs_and_db()
        with sqlite3.connect(self.local_db_path) as conn:
            conn.row_factory = sqlite3.Row
            meta_row = conn.execute(
                """
                SELECT * FROM (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY template_id ORDER BY updated_at DESC, rowid DESC) AS rn
                    FROM template_registry WHERE template_id = ?
                ) WHERE rn = 1
                """,
                (template_id,),
            ).fetchone()
            if meta_row is None:
                return None

            clause_rows = conn.execute(
                """
                SELECT * FROM (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY template_id, node_id ORDER BY created_at DESC, rowid DESC) AS rn
                    FROM template_clauses WHERE template_id = ?
                ) WHERE rn = 1
                ORDER BY page_start ASC, sibling_order ASC, canonical_path ASC
                """,
                (template_id,),
            ).fetchall()

        meta_dict = dict(meta_row)
        meta_dict["is_active"] = bool(meta_dict.get("is_active", 1))
        metadata = TemplateRegistryRow.model_validate(meta_dict)

        clauses: list[TemplateClauseRow] = []
        for cr in clause_rows:
            c_dict = dict(cr)
            pv = c_dict.get("placeholder_variables")
            if isinstance(pv, str):
                try:
                    c_dict["placeholder_variables"] = json.loads(pv)
                except Exception:
                    c_dict["placeholder_variables"] = []
            elif pv is None:
                c_dict["placeholder_variables"] = []
            clauses.append(TemplateClauseRow.model_validate(c_dict))

        return TemplateExtractionBundle(metadata=metadata, clauses=clauses)

    def get_template_by_hash(self, sha256_hash: str) -> TemplateRegistryRow | None:
        """Find an existing master template by its SHA-256 PDF binary hash."""
        self._ensure_local_dirs_and_db()
        with sqlite3.connect(self.local_db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT * FROM (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY template_id ORDER BY updated_at DESC, rowid DESC) AS rn
                    FROM template_registry WHERE sha256_hash = ?
                ) WHERE rn = 1
                """,
                (sha256_hash,),
            ).fetchone()
            if row is None:
                return None
            data = dict(row)
            data["is_active"] = bool(data.get("is_active", 1))
            return TemplateRegistryRow.model_validate(data)

    def get_template_pdf_path(self, template_id: str) -> Path | None:
        """Resolve physical template PDF path on local disk."""
        self._ensure_local_dirs_and_db()
        # 1. Local templates mirror directory
        tpl_dir = self.local_root / "templates" / template_id
        if tpl_dir.exists():
            pdfs = list(tpl_dir.glob("*.pdf"))
            if pdfs:
                return pdfs[0]

        # 2. Local raw directory
        raw_dir = self.local_raw_dir / template_id
        if raw_dir.exists():
            pdfs = list(raw_dir.glob("*.pdf"))
            if pdfs:
                return pdfs[0]

        # 3. Canonical samples mapping
        canonical_map = {
            "tpl_us_il_storage_storage_easement_2025_v1": "samples/Templates/Illinois/Real Estate Form - Illinois - Storage Easement.pdf",
            "tpl_us_mi_cross_technology_participation_easement_2024_v1": "samples/Templates/Michigan/Real Estate Form - Michigan - Energy Project Participation Easement Agreement.pdf",
            "tpl_us_mi_solar_solar_easement_2024_v1": "samples/Templates/Michigan/Real Estate Form - Michigan - Solar Easement Form.pdf",
            "tpl_us_ok_thermal_land_option_purchase_2024_v1": "samples/Templates/Oklahoma/Real Estate Form - Oklahoma - Land Option Purchase Agreement - THERMAL.pdf",
            "tpl_us_ok_solar_solar_lease_2024_v1": "samples/Templates/Oklahoma/Real Estate Form - Oklahoma - Solar Lease and Easement Form.pdf",
            "tpl_us_us_onshore_wind_wind_easement_2020_v1": "samples/Templates/US General/Real Estate Form - US - Wind Easement Form - 2020.Final. revised 05.28.2020.pdf",
        }
        if template_id in canonical_map:
            p = Path(canonical_map[template_id])
            if p.exists():
                return p

        # 4. Check metadata gcs_pdf_uri or direct relative path
        bundle = self.get_template_bundle(template_id)
        if bundle and bundle.metadata.gcs_pdf_uri:
            uri = bundle.metadata.gcs_pdf_uri
            if not uri.startswith("gs://"):
                p = Path(uri)
                if p.exists():
                    return p

        # 5. Search samples/Templates recursively by filename or template_id
        samples_root = Path("samples/Templates")
        if samples_root.exists():
            for p in samples_root.rglob("*.pdf"):
                if template_id in p.name.lower():
                    return p

        return None

    def seed_sample_templates(
        self,
        samples_dir: Path | str | None = None,
        parser: Any = None,
    ) -> list[TemplateRegistryRow]:
        """Cold-start auto-discovery scanning samples/Templates/ for baseline canonical forms."""
        self._ensure_local_dirs_and_db()
        base_dir = Path(samples_dir) if samples_dir else Path("samples/Templates")
        if not base_dir.exists():
            logger.warning("Template samples directory not found: %s", base_dir)
            return []

        specs = [
            (
                "Illinois/Real Estate Form - Illinois - Storage Easement.pdf",
                "US",
                "IL",
                EnergyTechnology.STORAGE,
                FormType.STORAGE_EASEMENT,
                "2025",
                "v1",
                "tpl_us_il_storage_storage_easement_2025_v1",
                "Illinois Storage Easement Form (2025)",
            ),
            (
                "Michigan/Real Estate Form - Michigan - Energy Project Participation Easement Agreement.pdf",
                "US",
                "MI",
                "CROSS_TECHNOLOGY",
                FormType.PARTICIPATION_EASEMENT,
                "2024",
                "v1",
                "tpl_us_mi_cross_technology_participation_easement_2024_v1",
                "Michigan Energy Project Participation Easement (2024)",
            ),
            (
                "Michigan/Real Estate Form - Michigan - Solar Easement Form.pdf",
                "US",
                "MI",
                EnergyTechnology.SOLAR,
                FormType.SOLAR_EASEMENT,
                "2024",
                "v1",
                "tpl_us_mi_solar_solar_easement_2024_v1",
                "Michigan Solar Easement Form (2024)",
            ),
            (
                "Oklahoma/Real Estate Form - Oklahoma - Land Option Purchase Agreement - THERMAL.pdf",
                "US",
                "OK",
                "THERMAL",
                FormType.LAND_OPTION_PURCHASE,
                "2024",
                "v1",
                "tpl_us_ok_thermal_land_option_purchase_2024_v1",
                "Oklahoma Land Option Purchase Agreement - Thermal (2024)",
            ),
            (
                "Oklahoma/Real Estate Form - Oklahoma - Solar Lease and Easement Form.pdf",
                "US",
                "OK",
                EnergyTechnology.SOLAR,
                FormType.SOLAR_LEASE,
                "2024",
                "v1",
                "tpl_us_ok_solar_solar_lease_2024_v1",
                "Oklahoma Solar Lease and Easement Form (2024)",
            ),
            (
                "US General/Real Estate Form - US - Wind Easement Form - 2020.Final. revised 05.28.2020.pdf",
                "US",
                "US",
                EnergyTechnology.ONSHORE_WIND,
                FormType.WIND_EASEMENT,
                "2020",
                "v1",
                "tpl_us_us_onshore_wind_wind_easement_2020_v1",
                "US General Wind Easement Form (2020)",
            ),
        ]

        seeded: list[TemplateRegistryRow] = []
        for rel_path, country, state, tech, form_type, vintage, rev, tpl_id, tpl_name in specs:
            pdf_path = base_dir / rel_path
            if not pdf_path.exists():
                logger.warning("Sample template PDF missing: %s", pdf_path)
                continue

            pdf_bytes = pdf_path.read_bytes()
            sha256_hash = hashlib.sha256(pdf_bytes).hexdigest()

            # Check if template is already stored by hash
            existing = self.get_template_by_hash(sha256_hash)
            if existing is not None:
                seeded.append(existing)
                continue

            # Check if existing by ID
            existing_bundle = self.get_template_bundle(tpl_id)
            if existing_bundle is not None:
                seeded.append(existing_bundle.metadata)
                continue

            # Mirror file to local templates directory
            tpl_local_dir = self.local_root / "templates" / tpl_id
            tpl_local_dir.mkdir(parents=True, exist_ok=True)
            (tpl_local_dir / pdf_path.name).write_bytes(pdf_bytes)

            gcs_uri = f"gs://{self.config.gcs_bucket_name}/templates/{tpl_id}/{pdf_path.name}"

            if parser is not None:
                try:
                    bundle = parser.extract_template(
                        pdf_bytes=pdf_bytes,
                        filename=pdf_path.name,
                        template_id=tpl_id,
                        template_name=tpl_name,
                        country=country,
                        state_province=state,
                        energy_technology=tech,
                        form_type=form_type,
                        vintage_year=vintage,
                        revision=rev,
                        gcs_pdf_uri=gcs_uri,
                        created_by="system_seeder",
                    )
                    self.insert_template_bundle(bundle)
                    seeded.append(bundle.metadata)
                    continue
                except Exception as ex:
                    logger.warning("Live parser extraction failed for %s, falling back to offline: %s", tpl_id, ex)

            # Offline extraction via pypdf & regex
            bundle = self._extract_template_offline(
                pdf_bytes=pdf_bytes,
                filename=pdf_path.name,
                template_id=tpl_id,
                template_name=tpl_name,
                country=country,
                state_province=state,
                energy_technology=tech,
                form_type=form_type,
                vintage_year=vintage,
                revision=rev,
                gcs_pdf_uri=gcs_uri,
            )
            self.insert_template_bundle(bundle)
            seeded.append(bundle.metadata)

        return seeded

    def _extract_template_offline(
        self,
        pdf_bytes: bytes,
        filename: str,
        template_id: str,
        template_name: str,
        country: str,
        state_province: str,
        energy_technology: EnergyTechnology | str,
        form_type: FormType | str,
        vintage_year: str,
        revision: str = "v1",
        gcs_pdf_uri: str = "",
    ) -> TemplateExtractionBundle:
        """Deterministic offline extraction of master template structure, substantive clauses, and placeholders."""
        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        total_pages = len(reader.pages)
        pages_text: list[str] = [page.extract_text() or "" for page in reader.pages]

        # 1. Identify Signature Quarantine Span
        sig_start: int | None = None
        sig_end: int | None = None
        first_exhibit_page: int | None = None

        witness_re = re.compile(
            r"(?:IN\s+WITNESS\s+WHEREOF|IN\s+WITNESS\s+WHERE\s*OF|EXECUTED\s+AND\s+DELIVERED)",
            re.IGNORECASE,
        )
        exhibit_header_re = re.compile(
            r"(?:^|\n)\s*(?:EXHIBIT|SCHEDULE)\s+([A-Z0-9]+)\b",
            re.IGNORECASE,
        )

        for p_idx, text in enumerate(pages_text, start=1):
            if sig_start is None and witness_re.search(text):
                sig_start = p_idx
            if sig_start is not None and first_exhibit_page is None and p_idx > sig_start:
                if exhibit_header_re.search(text):
                    first_exhibit_page = p_idx

        if sig_start is not None:
            sig_end = (first_exhibit_page - 1) if first_exhibit_page else total_pages

        quarantine_span = PageSpan(page_start=sig_start, page_end=sig_end) if (sig_start and sig_end and sig_end >= sig_start) else None

        # 2. Extract Substantive Clauses
        raw_clauses: list[RawTemplateClause] = []

        # Preamble (Page 1)
        p1_text = pages_text[0] if pages_text else ""
        recitals_start_m = re.search(r"\b(?:RECITALS|WITNESSETH)\b", p1_text, re.IGNORECASE)
        preamble_text = p1_text[:recitals_start_m.start()].strip() if recitals_start_m else p1_text[:400].strip()
        if preamble_text:
            raw_clauses.append(
                RawTemplateClause(
                    clause_label="PREAMBLE",
                    clause_title="Preamble and Parties",
                    numbering_scheme=NumberingScheme.DECIMAL,
                    document_zone=DocumentZone.PREAMBLE,
                    verbatim_text=preamble_text,
                    placeholder_variables=extract_template_placeholders(preamble_text),
                    page_start=1,
                    page_end=1,
                )
            )

        # Recitals
        if recitals_start_m:
            recitals_text = p1_text[recitals_start_m.start():]
            # Cut at Section 1 or Article 1
            body_start_m = re.search(r"(?:^|\n)\s*(?:SECTION\s+1|ARTICLE\s+1|1\.)\b", recitals_text, re.IGNORECASE)
            if body_start_m:
                recitals_text = recitals_text[:body_start_m.start()]
            recital_items = re.findall(r"(?:WHEREAS[^\n\r]+(?:(?:\n(?!\s*(?:WHEREAS|NOW\b))[^\n\r]+)*))", recitals_text, re.IGNORECASE)
            for idx, r_txt in enumerate(recital_items):
                r_clean = r_txt.strip()
                if not r_clean:
                    continue
                lbl = chr(65 + idx) if idx < 26 else str(idx + 1)
                raw_clauses.append(
                    RawTemplateClause(
                        clause_label=f"Recital {lbl}",
                        clause_title=None,
                        numbering_scheme=NumberingScheme.ALPHA_UPPER,
                        document_zone=DocumentZone.RECITALS,
                        verbatim_text=r_clean,
                        placeholder_variables=extract_template_placeholders(r_clean),
                        page_start=1,
                        page_end=1,
                    )
                )

        # Body Sections & Exhibits
        section_pattern = re.compile(
            r"(?m)^(?:(?:\s*)(?:SECTION\s+|ARTICLE\s+)?(\d+(?:\.\d+)*)\.?\s*([^\n\r]+)?)$",
            re.IGNORECASE,
        )

        for p_idx, text in enumerate(pages_text, start=1):
            # Strict signature suppression: skip pages in signature quarantine span
            if quarantine_span and quarantine_span.page_start <= p_idx <= quarantine_span.page_end:
                continue

            # Check if this page is in exhibits zone
            is_exhibit_zone = (first_exhibit_page is not None and p_idx >= first_exhibit_page)

            if is_exhibit_zone:
                ex_m = exhibit_header_re.search(text)
                if ex_m:
                    ex_id = ex_m.group(1).upper()
                    raw_clauses.append(
                        RawTemplateClause(
                            clause_label=f"Exhibit {ex_id}",
                            clause_title=f"Exhibit {ex_id}",
                            numbering_scheme=NumberingScheme.NAMED_HEADER,
                            document_zone=DocumentZone.EXHIBITS,
                            verbatim_text=text.strip(),
                            placeholder_variables=extract_template_placeholders(text),
                            page_start=p_idx,
                            page_end=p_idx,
                        )
                    )
                continue

            # Main body pages: extract sections
            matches = list(section_pattern.finditer(text))
            if not matches:
                continue

            for i, match in enumerate(matches):
                sec_num = match.group(1).strip()
                sec_heading = (match.group(2) or "").strip()
                start_pos = match.start()
                end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(text)
                sec_body = text[start_pos:end_pos].strip()

                if not sec_body or len(sec_body) < 15:
                    continue

                raw_clauses.append(
                    RawTemplateClause(
                        clause_label=sec_num,
                        clause_title=sec_heading if sec_heading else None,
                        numbering_scheme=NumberingScheme.DECIMAL,
                        document_zone=DocumentZone.BODY,
                        verbatim_text=sec_body,
                        placeholder_variables=extract_template_placeholders(sec_body),
                        page_start=p_idx,
                        page_end=p_idx,
                    )
                )

        if not raw_clauses:
            fallback_text = (
                p1_text.strip()
                if p1_text.strip()
                else f"This {template_name} is entered into as of the effective date."
            )
            raw_clauses.append(
                RawTemplateClause(
                    clause_label="1.0",
                    clause_title="General Provisions",
                    numbering_scheme=NumberingScheme.DECIMAL,
                    document_zone=DocumentZone.BODY,
                    verbatim_text=fallback_text,
                    placeholder_variables=extract_template_placeholders(fallback_text),
                    page_start=1,
                    page_end=1,
                )
            )

        try:
            tech_enum = (
                energy_technology
                if isinstance(energy_technology, EnergyTechnology)
                else EnergyTechnology(str(energy_technology))
            )
        except Exception:
            tech_enum = EnergyTechnology.ONSHORE_WIND if str(energy_technology).upper() == "WIND" else EnergyTechnology.CROSS_TECHNOLOGY

        try:
            form_enum = (
                form_type
                if isinstance(form_type, FormType)
                else FormType(str(form_type))
            )
        except Exception:
            form_enum = FormType.LEASE_AGREEMENT if str(form_type).upper() == "LEASE" else FormType.OTHER

        # 3. Assemble Template Extraction Bundle
        struct_index = TemplateStructureIndex(
            document_title=template_name,
            country=country,
            state_province=state_province,
            energy_technology=str(tech_enum),
            form_type=form_enum,
            vintage_year=str(vintage_year),
            quarantine_signatures_span=quarantine_span,
        )

        bundle = assemble_template_bundle(
            raw_clauses=raw_clauses,
            quarantine_span=quarantine_span,
            struct_index=struct_index,
            template_id=template_id,
            template_name=template_name,
            country=country,
            state_province=state_province,
            energy_technology=tech_enum,
            form_type=form_enum,
            vintage_year=str(vintage_year),
            revision=revision,
            gcs_pdf_uri=gcs_pdf_uri,
            sha256_hash=hashlib.sha256(pdf_bytes).hexdigest(),
            page_count=total_pages,
            created_by="system_seeder",
        )
        return bundle

    def save_contract_variance(self, bundle: Any) -> None:
        """Persist ContractVarianceRow and ClauseVarianceDetailRows to SQLite and BigQuery."""
        self._ensure_local_dirs_and_db()
        variance = bundle.variance if hasattr(bundle, "variance") else bundle["variance"]
        details = bundle.details if hasattr(bundle, "details") else bundle["details"]
        summary = bundle.summary if hasattr(bundle, "summary") else (bundle.get("summary") if isinstance(bundle, dict) else None)

        v_dict = variance.model_dump() if hasattr(variance, "model_dump") else dict(variance)
        s_dict = summary.model_dump() if hasattr(summary, "model_dump") else (dict(summary) if summary else {})

        # Extract/fallback counts
        identical_count = v_dict.get("identical_count") or s_dict.get("identical_count", 0)
        parameter_only_count = v_dict.get("parameter_only_count") or s_dict.get("parameter_only_count", 0)
        modified_count = v_dict.get("modified_count") or s_dict.get("modified_count", 0)
        added_count = v_dict.get("added_count") or s_dict.get("added_count", 0)
        deleted_count = v_dict.get("deleted_count") or s_dict.get("deleted_count", 0)
        high_risk_count = v_dict.get("high_risk_count") or s_dict.get("high_risk_count", 0)
        medium_risk_count = v_dict.get("medium_risk_count") or s_dict.get("medium_risk_count", 0)
        low_risk_count = v_dict.get("low_risk_count") or s_dict.get("low_risk_count", 0)
        total_template_clauses = v_dict.get("total_template_clauses") or s_dict.get("total_template_clauses") or s_dict.get("total_clauses_analyzed", 0)
        total_contract_clauses = v_dict.get("total_contract_clauses") or s_dict.get("total_contract_clauses") or s_dict.get("total_clauses_analyzed", len(details))
        overall_risk_score = v_dict.get("overall_risk_score")
        if overall_risk_score is None:
            overall_risk_score = v_dict.get("overall_deviation_index", 0.0)

        with sqlite3.connect(self.local_db_path) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO contract_variances (
                        variance_id, document_id, template_id, template_name, template_version,
                        binding_mode, affinity_score,
                        total_template_clauses, total_contract_clauses, identical_count,
                        parameter_only_count, modified_count, added_count, deleted_count,
                        overall_risk_score, high_risk_count, medium_risk_count, low_risk_count,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        v_dict["variance_id"],
                        v_dict["document_id"],
                        v_dict["template_id"],
                        v_dict.get("template_name") or "",
                        v_dict.get("template_version") or "1.0",
                        v_dict.get("binding_mode") or "AUTO_SUGGESTED",
                        float(v_dict.get("affinity_score") or 1.0),
                        int(total_template_clauses),
                        int(total_contract_clauses),
                        int(identical_count),
                        int(parameter_only_count),
                        int(modified_count),
                        int(added_count),
                        int(deleted_count),
                        float(overall_risk_score),
                        int(high_risk_count),
                        int(medium_risk_count),
                        int(low_risk_count),
                        v_dict.get("created_at") or utc_now_iso(),
                    ),
                )

                # Clean previous details for this variance run
                conn.execute("DELETE FROM clause_variance_details WHERE variance_id = ?", (v_dict["variance_id"],))

                detail_rows = []
                for d in details:
                    d_dict = d.model_dump() if hasattr(d, "model_dump") else dict(d)
                    p_sub = d_dict.get("parameter_substitutions")
                    p_sub_str = json.dumps(p_sub) if p_sub is not None else None
                    detail_rows.append(
                        (
                            d_dict["detail_id"],
                            d_dict["variance_id"],
                            d_dict["document_id"],
                            d_dict["canonical_path"],
                            d_dict.get("template_clause_id") or d_dict.get("template_node_id"),
                            d_dict.get("contract_clause_id") or d_dict.get("contract_node_id"),
                            d_dict.get("clause_label") or d_dict["canonical_path"],
                            d_dict.get("clause_title"),
                            str(d_dict["variance_type"]),
                            str(d_dict["risk_level"]),
                            float(d_dict.get("risk_score") or d_dict.get("similarity_score") or 0.0),
                            d_dict.get("risk_category") or d_dict.get("risk_categories"),
                            d_dict.get("risk_rationale"),
                            p_sub_str,
                            d_dict.get("template_text") or d_dict.get("template_baseline_text"),
                            d_dict.get("contract_text") or d_dict.get("contract_executed_text"),
                            d_dict.get("redline_html"),
                            d_dict.get("page_start") or 1,
                            d_dict.get("created_at") or utc_now_iso(),
                        )
                    )

                if detail_rows:
                    conn.executemany(
                        """
                        INSERT OR REPLACE INTO clause_variance_details (
                            detail_id, variance_id, document_id, canonical_path,
                            template_clause_id, contract_clause_id, clause_label, clause_title,
                            variance_type, risk_level, risk_score, risk_category, risk_rationale,
                            parameter_substitutions, template_text, contract_text, redline_html,
                            page_start, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        detail_rows,
                    )

        if self.config.use_bigquery:
            try:
                self.ensure_bq_tables()
                v_table = f"{self.config.bq_dataset_fqn}.contract_variances"
                self.bq_client.insert_rows_json(v_table, [v_dict])

                det_rows = []
                for d in details:
                    d_dict = d.model_dump() if hasattr(d, "model_dump") else dict(d)
                    det_rows.append(d_dict)
                det_table = f"{self.config.bq_dataset_fqn}.clause_variance_details"
                if det_rows:
                    self.bq_client.insert_rows_json(det_table, det_rows)
            except Exception as bq_err:
                logger.warning("BigQuery persistence warning for variance %s: %s", v_dict["variance_id"], bq_err)

    def get_latest_contract_variance(self, document_id: str) -> ContractVarianceBundle | None:
        """Fetch the most recent variance scorecard and clause details for a document."""
        self._ensure_local_dirs_and_db()
        with sqlite3.connect(self.local_db_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM contract_variances
                WHERE document_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (document_id,),
            )
            v_row = cur.fetchone()
            if not v_row:
                return None

            v_dict = dict(v_row)
            var_id = v_dict["variance_id"]

            cur.execute(
                """
                SELECT * FROM clause_variance_details
                WHERE variance_id = ?
                ORDER BY canonical_path ASC
                """,
                (var_id,),
            )
            d_rows = cur.fetchall()
            details: list[ClauseVarianceDetailRow] = []
            for r in d_rows:
                rd = dict(r)
                if rd.get("parameter_substitutions") and isinstance(rd["parameter_substitutions"], str):
                    try:
                        rd["parameter_substitutions"] = json.loads(rd["parameter_substitutions"])
                    except Exception:
                        pass
                detail_row = ClauseVarianceDetailRow(
                    detail_id=rd["detail_id"],
                    variance_id=rd["variance_id"],
                    document_id=rd["document_id"],
                    contract_node_id=rd.get("contract_clause_id") or "",
                    template_node_id=rd.get("template_clause_id") or "",
                    canonical_path=rd["canonical_path"],
                    clause_label=rd.get("clause_label") or rd["canonical_path"],
                    clause_title=rd.get("clause_title"),
                    variance_type=VarianceType(rd["variance_type"]),
                    risk_level=VarianceRiskLevel(rd["risk_level"]),
                    risk_categories=rd.get("risk_category") or "OPERATIONAL_ALTERATION",
                    risk_rationale=rd.get("risk_rationale") or "",
                    similarity_score=float(rd.get("risk_score") or 0.0),
                    template_baseline_text=rd.get("template_text") or "",
                    contract_executed_text=rd.get("contract_text") or "",
                    redline_html=rd.get("redline_html") or "",
                    page_start=int(rd.get("page_start") or 1),
                    parameter_substitutions=rd.get("parameter_substitutions"),
                    created_at=rd.get("created_at") or utc_now_iso(),
                )
                details.append(detail_row)

            summary = VarianceSummary(
                total_clauses_analyzed=v_dict.get("total_contract_clauses") or len(details),
                identical_count=v_dict.get("identical_count") or 0,
                parameter_only_count=v_dict.get("parameter_only_count") or 0,
                modified_count=v_dict.get("modified_count") or 0,
                added_count=v_dict.get("added_count") or 0,
                deleted_count=v_dict.get("deleted_count") or 0,
                high_risk_count=v_dict.get("high_risk_count") or 0,
                medium_risk_count=v_dict.get("medium_risk_count") or 0,
                low_risk_count=v_dict.get("low_risk_count") or 0,
                none_risk_count=v_dict.get("none_risk_count") or 0,
            )

            var_row = ContractVarianceRow(
                variance_id=v_dict["variance_id"],
                document_id=v_dict["document_id"],
                template_id=v_dict["template_id"],
                template_name=v_dict.get("template_name") or "",
                template_version=v_dict.get("template_version") or "1.0",
                binding_mode=v_dict.get("binding_mode") or "AUTO_SUGGESTED",
                affinity_score=float(v_dict.get("affinity_score") or 1.0),
                total_template_clauses=v_dict.get("total_template_clauses") or 0,
                total_contract_clauses=v_dict.get("total_contract_clauses") or 0,
                overall_deviation_index=float(v_dict.get("overall_risk_score") or 0.0),
                overall_risk_score=float(v_dict.get("overall_risk_score") or 0.0),
                summary=summary,
                created_at=v_dict.get("created_at") or utc_now_iso(),
            )

            return ContractVarianceBundle(
                variance=var_row,
                summary=summary,
                details=details,
            )

    # -------------------------------------------------------------------------
    # CR-12: Batch Ingestion & Historical Discovery
    # -------------------------------------------------------------------------

    def save_batch_job(self, job: BatchJobRow, *, sqlite_only: bool = False) -> None:
        """Append or update a BatchJobRow in SQLite and BigQuery."""
        self._append_table_rows(
            "batch_jobs",
            BATCH_JOBS_COLUMNS,
            [job.model_dump(mode="json")],
            sqlite_only=sqlite_only,
        )

    def get_batch_job(self, job_id: str) -> BatchJobRow:
        """Fetch the latest version of a BatchJobRow by job_id."""
        self._ensure_local_dirs_and_db()
        with sqlite3.connect(self.local_db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM v_active_batch_jobs WHERE job_id = ? LIMIT 1",
                (job_id,),
            ).fetchone()
            if row:
                data = {k: row[k] for k in BATCH_JOBS_COLUMNS if k in row.keys()}
                for int_f in ("total_files", "processed_files", "success_count", "failure_count", "total_tokens_consumed", "concurrency_limit"):
                    if int_f in data and data[int_f] is not None:
                        try:
                            data[int_f] = int(data[int_f])
                        except (ValueError, TypeError):
                            data[int_f] = 0
                return BatchJobRow.model_validate(data)

        if self.config.use_bigquery:
            try:
                self.ensure_bq_tables()
                sql = f"SELECT * FROM `{self.config.bq_dataset_fqn}.v_active_batch_jobs` WHERE job_id = @jid LIMIT 1"
                job_config = bigquery.QueryJobConfig(
                    query_parameters=[bigquery.ScalarQueryParameter("jid", "STRING", job_id)]
                )
                for r in self.bq_client.query(sql, job_config=job_config).result():
                    row_dict = {
                        k: (r[k].isoformat() if hasattr(r[k], "isoformat") else r[k])
                        for k in BATCH_JOBS_COLUMNS
                        if k in r.keys()
                    }
                    for int_f in ("total_files", "processed_files", "success_count", "failure_count", "total_tokens_consumed", "concurrency_limit"):
                        if int_f in row_dict and row_dict[int_f] is not None:
                            try:
                                row_dict[int_f] = int(row_dict[int_f])
                            except (ValueError, TypeError):
                                row_dict[int_f] = 0
                    return BatchJobRow.model_validate(row_dict)
            except Exception as exc:
                logger.warning("BigQuery get_batch_job fallback: %s", exc)

        raise KeyError(f"Batch job {job_id!r} not found")

    def list_batch_jobs(self, limit: int = 50) -> list[BatchJobRow]:
        """Fetch latest active batch jobs ordered by started_at DESC."""
        self._ensure_local_dirs_and_db()
        jobs: dict[str, BatchJobRow] = {}
        with sqlite3.connect(self.local_db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM v_active_batch_jobs ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            for r in rows:
                data = {k: r[k] for k in BATCH_JOBS_COLUMNS if k in r.keys()}
                for int_f in ("total_files", "processed_files", "success_count", "failure_count", "total_tokens_consumed", "concurrency_limit"):
                    if int_f in data and data[int_f] is not None:
                        try:
                            data[int_f] = int(data[int_f])
                        except (ValueError, TypeError):
                            data[int_f] = 0
                j = BatchJobRow.model_validate(data)
                jobs[j.job_id] = j

        if self.config.use_bigquery:
            try:
                self.ensure_bq_tables()
                sql = f"SELECT * FROM `{self.config.bq_dataset_fqn}.v_active_batch_jobs` ORDER BY started_at DESC LIMIT {limit}"
                for r in self.bq_client.query(sql).result():
                    row_dict = {
                        k: (r[k].isoformat() if hasattr(r[k], "isoformat") else r[k])
                        for k in BATCH_JOBS_COLUMNS
                        if k in r.keys()
                    }
                    for int_f in ("total_files", "processed_files", "success_count", "failure_count", "total_tokens_consumed", "concurrency_limit"):
                        if int_f in row_dict and row_dict[int_f] is not None:
                            try:
                                row_dict[int_f] = int(row_dict[int_f])
                            except (ValueError, TypeError):
                                row_dict[int_f] = 0
                    bj = BatchJobRow.model_validate(row_dict)
                    if bj.job_id not in jobs or str(bj.updated_at) >= str(jobs[bj.job_id].updated_at):
                        jobs[bj.job_id] = bj
            except Exception as exc:
                logger.warning("BigQuery list_batch_jobs fallback: %s", exc)

        return sorted(jobs.values(), key=lambda j: j.started_at, reverse=True)[:limit]

    def save_batch_job_item(self, item: BatchJobItemRow, *, sqlite_only: bool = False) -> None:
        """Append or update a BatchJobItemRow in SQLite and BigQuery."""
        self._append_table_rows(
            "batch_job_items",
            BATCH_JOB_ITEMS_COLUMNS,
            [item.model_dump(mode="json")],
            sqlite_only=sqlite_only,
        )

    def get_batch_job_items(self, job_id: str) -> list[BatchJobItemRow]:
        """Fetch all active items for a batch job."""
        self._ensure_local_dirs_and_db()
        items: dict[str, BatchJobItemRow] = {}
        with sqlite3.connect(self.local_db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM v_active_batch_job_items WHERE job_id = ? ORDER BY started_at ASC, item_id ASC",
                (job_id,),
            ).fetchall()
            for r in rows:
                data = {k: r[k] for k in BATCH_JOB_ITEMS_COLUMNS if k in r.keys()}
                for int_f in ("clause_count", "special_conditions_count", "token_usage_total"):
                    if int_f in data and data[int_f] is not None:
                        try:
                            data[int_f] = int(data[int_f])
                        except (ValueError, TypeError):
                            data[int_f] = 0
                it = BatchJobItemRow.model_validate(data)
                items[it.item_id] = it

        if self.config.use_bigquery:
            try:
                self.ensure_bq_tables()
                sql = f"SELECT * FROM `{self.config.bq_dataset_fqn}.v_active_batch_job_items` WHERE job_id = @jid ORDER BY started_at ASC, item_id ASC"
                job_config = bigquery.QueryJobConfig(
                    query_parameters=[bigquery.ScalarQueryParameter("jid", "STRING", job_id)]
                )
                for r in self.bq_client.query(sql, job_config=job_config).result():
                    row_dict = {
                        k: (r[k].isoformat() if hasattr(r[k], "isoformat") else r[k])
                        for k in BATCH_JOB_ITEMS_COLUMNS
                        if k in r.keys()
                    }
                    for int_f in ("clause_count", "special_conditions_count", "token_usage_total"):
                        if int_f in row_dict and row_dict[int_f] is not None:
                            try:
                                row_dict[int_f] = int(row_dict[int_f])
                            except (ValueError, TypeError):
                                row_dict[int_f] = 0
                    it = BatchJobItemRow.model_validate(row_dict)
                    if it.item_id not in items or str(it.updated_at) >= str(items[it.item_id].updated_at):
                        items[it.item_id] = it
            except Exception as exc:
                logger.warning("BigQuery get_batch_job_items fallback: %s", exc)

        return sorted(items.values(), key=lambda i: i.started_at or i.item_id)

    def get_batch_job_item(self, item_id: str) -> BatchJobItemRow:
        """Fetch a specific BatchJobItemRow by item_id."""
        self._ensure_local_dirs_and_db()
        with sqlite3.connect(self.local_db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM v_active_batch_job_items WHERE item_id = ? LIMIT 1",
                (item_id,),
            ).fetchone()
            if row:
                data = {k: row[k] for k in BATCH_JOB_ITEMS_COLUMNS if k in row.keys()}
                for int_f in ("clause_count", "special_conditions_count", "token_usage_total"):
                    if int_f in data and data[int_f] is not None:
                        try:
                            data[int_f] = int(data[int_f])
                        except (ValueError, TypeError):
                            data[int_f] = 0
                return BatchJobItemRow.model_validate(data)

        if self.config.use_bigquery:
            try:
                self.ensure_bq_tables()
                sql = f"SELECT * FROM `{self.config.bq_dataset_fqn}.v_active_batch_job_items` WHERE item_id = @iid LIMIT 1"
                job_config = bigquery.QueryJobConfig(
                    query_parameters=[bigquery.ScalarQueryParameter("iid", "STRING", item_id)]
                )
                for r in self.bq_client.query(sql, job_config=job_config).result():
                    row_dict = {
                        k: (r[k].isoformat() if hasattr(r[k], "isoformat") else r[k])
                        for k in BATCH_JOB_ITEMS_COLUMNS
                        if k in r
                    }
                    return BatchJobItemRow.model_validate(row_dict)
            except Exception as exc:
                logger.warning("BigQuery get_batch_job_item fallback: %s", exc)

        raise KeyError(f"Batch item {item_id!r} not found")

    def purge_partial_document(self, document_id: str) -> None:
        """Purge any partial or corrupted records for document_id across Lakehouse tables."""
        self._ensure_local_dirs_and_db()
        with sqlite3.connect(self.local_db_path) as conn:
            for tbl in (
                "documents",
                "clauses",
                "defined_terms",
                "exhibits_catalog",
                "special_conditions",
                "dnd_checklist_signoffs",
                "contract_variances",
                "clause_variance_details",
            ):
                try:
                    conn.execute(f"DELETE FROM {tbl} WHERE document_id = ?", (document_id,))
                except Exception:
                    pass

        if self.config.use_bigquery:
            try:
                for tbl in (
                    "documents",
                    "clauses",
                    "defined_terms",
                    "exhibits_catalog",
                    "special_conditions",
                    "dnd_checklist_signoffs",
                    "contract_variances",
                    "clause_variance_details",
                ):
                    query = f"DELETE FROM `{self.config.bq_dataset_fqn}.{tbl}` WHERE document_id = @doc_id"
                    job_config = bigquery.QueryJobConfig(
                        query_parameters=[bigquery.ScalarQueryParameter("doc_id", "STRING", document_id)]
                    )
                    self.bq_client.query(query, job_config=job_config).result()
            except Exception as exc:
                logger.warning("BigQuery purge_partial_document warning on %s: %s", document_id, exc)

    @staticmethod
    def normalize_entity_name(name: str | None) -> str:
        """Apply deterministic legal entity cleanup filter per Section 3 of CR-12."""
        if not name:
            return ""
        text = name.upper()
        # Punctuation Stripping first (e.g. L.L.C. -> L L C)
        text = re.sub(r"[,.\-_'\"]", " ", text)
        # Corporate & Phase Token Stripping
        entity_cleanup_regex = re.compile(
            r"\b(LLC|L\s+L\s+C|CORP|CORPORATION|INC|INCORPORATED|CO|COMPANY|HOLDINGS|PHASE\s*(?:[IVX\d]+))\b",
            re.IGNORECASE,
        )
        text = entity_cleanup_regex.sub(" ", text)
        # Whitespace Collapsing and lowercase canonical form
        return re.sub(r"\s+", " ", text).strip().lower()

    def find_project_by_geo_or_name(
        self,
        state: str | None,
        county: str | None,
        energy_technology: str | EnergyTechnology,
        grantee_name: str | None = None,
        project_name: str | None = None,
    ) -> ProjectRow | None:
        """Resolve project ownership using CR-12 deterministic resolution cascade.

        Check 1: Name match in State (ORDER BY created_at ASC LIMIT 1) - first-inserted cross-county anchor.
        Check 2: Location match (State + County + Technology).
        """
        all_projects = self.list_projects()
        norm_state = (state or "").strip().upper()
        norm_county = (county or "").strip().upper()
        norm_tech = (
            energy_technology.value
            if isinstance(energy_technology, EnergyTechnology)
            else str(energy_technology).strip().upper()
        )

        # Check 1: Root Name Anchor Match anywhere in the state
        candidate_names = [grantee_name, project_name]
        norm_candidates = [self.normalize_entity_name(n) for n in candidate_names if n]
        norm_candidates = [c for c in norm_candidates if len(c) >= 3]

        if norm_candidates and norm_state:
            state_projects = [
                p for p in all_projects
                if (p.state_province or "").strip().upper() == norm_state
            ]
            matches: list[ProjectRow] = []
            for proj in state_projects:
                proj_norm = self.normalize_entity_name(proj.project_name)
                for cand in norm_candidates:
                    if cand in proj_norm or proj_norm in cand:
                        matches.append(proj)
                        break
            if matches:
                # Option 3: Cross-county developments anchor strictly to earliest created_at project
                sorted_matches = sorted(matches, key=lambda p: p.created_at or p.updated_at)
                return sorted_matches[0]

        # Check 2: (State + County + Technology) Match
        if norm_state and norm_county:
            for proj in all_projects:
                p_state = (proj.state_province or "").strip().upper()
                p_county = (proj.county or "").strip().upper()
                p_tech = (
                    proj.energy_technology.value
                    if isinstance(proj.energy_technology, EnergyTechnology)
                    else str(proj.energy_technology).strip().upper()
                )
                if p_state == norm_state and p_county == norm_county and p_tech == norm_tech:
                    return proj

        return None

    def auto_create_project_for_location(
        self,
        state: str | None,
        county: str | None,
        energy_technology: str | EnergyTechnology,
        grantee_name: str | None = None,
    ) -> ProjectRow:
        """Create a new canonical ProjectRow with deterministic synthetic ERP code per CR-12 Section 3."""
        norm_tech = (
            energy_technology
            if isinstance(energy_technology, EnergyTechnology)
            else EnergyTechnology(str(energy_technology).strip().upper())
        )
        tech_val = norm_tech.value
        tech_abbr_map = {
            "ONSHORE_WIND": "WND",
            "SOLAR": "SOL",
            "STORAGE": "STR",
            "TRANSMISSION": "TRN",
            "GEOTHERMAL": "GEO",
        }
        tech_abbr = tech_abbr_map.get(tech_val, "ENG")
        tech_title_map = {
            "ONSHORE_WIND": "Wind Energy",
            "SOLAR": "Solar",
            "STORAGE": "BESS Storage",
            "TRANSMISSION": "Transmission",
            "GEOTHERMAL": "Geothermal",
        }
        tech_title = tech_title_map.get(tech_val, "Clean Energy")

        now_iso = utc_now_iso()

        if not state or not county or state.strip().upper() in ("UNKNOWN", "US", ""):
            # Missing Geolocation Fallback
            project_id = f"prj_us_regional_{tech_val.lower()}"
            existing = [p for p in self.list_projects() if p.project_id == project_id]
            if existing:
                return existing[0]
            new_proj = ProjectRow(
                project_id=project_id,
                project_name=f"US Regional {tech_title} Center",
                energy_technology=norm_tech,
                erp_project_code=f"ERP-{tech_abbr}-AUTO",
                state_province="US",
                county="REGIONAL",
                target_capacity_mw=100.0,
                created_at=now_iso,
                updated_at=now_iso,
            )
        else:
            clean_state = state.strip().upper()
            clean_county = county.strip().title()
            county_slug = re.sub(r"[^a-zA-Z0-9]+", "_", clean_county.lower()).strip("_")
            county_code = re.sub(r"[^a-zA-Z0-9]+", "", clean_county.upper())
            project_id = f"prj_{clean_state.lower()}_{county_slug}_{tech_abbr.lower()}"
            erp_code = f"ERP-{tech_abbr}-{clean_state}-{county_code}"
            proj_name = f"{clean_county} County {tech_title} Center"

            new_proj = ProjectRow(
                project_id=project_id,
                project_name=proj_name,
                energy_technology=norm_tech,
                erp_project_code=erp_code,
                state_province=clean_state,
                county=clean_county,
                target_capacity_mw=150.0,
                created_at=now_iso,
                updated_at=now_iso,
            )

        self.append_project_row(new_proj)
        return new_proj

    def save_project(self, project: ProjectRow) -> None:
        """Alias for append_project_row."""
        self.append_project_row(project)

