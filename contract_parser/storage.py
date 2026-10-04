"""Thin GCS, Append-Only BigQuery (Option 3B QUALIFY deduplication), CSV, and local SQLite persistence glue."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import sqlite3
from pathlib import Path

from google.cloud import bigquery, storage

from contract_parser.config import PipelineConfig
from contract_parser.gemini_parser import _make_landowner_id, canonical_party_key
from contract_parser.schemas import (
    CLAUSE_CSV_COLUMNS,
    DEFINED_TERM_CSV_COLUMNS,
    DND_SIGNOFF_CSV_COLUMNS,
    DOCUMENT_REGISTRY_COLUMNS,
    EXHIBIT_CATALOG_CSV_COLUMNS,
    LANDOWNER_CSV_COLUMNS,
    PROJECT_CSV_COLUMNS,
    SPECIAL_CONDITION_CSV_COLUMNS,
    ClauseReviewRequest,
    ClauseRow,
    ConstructionTrade,
    CreateDNDSignoffRequest,
    DefinedTermRow,
    DNDChecklistBundle,
    DNDChecklistItem,
    DNDChecklistSignoffRow,
    DNDDispatchClearance,
    DNDDispatchReadiness,
    DNDSeverityLevel,
    DocumentRegistryRow,
    EnergyTechnology,
    ExhibitCatalogRow,
    HITLStatus,
    IngestionStatus,
    LandownerRow,
    ParsedContractBundle,
    ProjectRow,
    SpecialConditionRow,
    build_dnd_checklist_item,
    utc_now_iso,
)

logger = logging.getLogger(__name__)


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


class ContractStorageService:
    """Manages GCS artifacts, Append-Only BigQuery tables (`QUALIFY ROW_NUMBER() = 1`), CSV exports, and local SQLite."""

    def __init__(self, config: PipelineConfig | None = None) -> None:
        self.config = config or PipelineConfig()
        self.local_root = Path(self.config.local_data_dir)
        self.local_raw_dir = self.local_root / "raw"
        self.local_exports_dir = self.local_root / "exports"
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
                    gcs_pdf_uri TEXT NOT NULL,
                    gcs_export_prefix TEXT NOT NULL,
                    page_count INTEGER NOT NULL,
                    contracting_parties_json TEXT,
                    effective_date TEXT,
                    flagged_node_count INTEGER NOT NULL,
                    special_conditions_count INTEGER NOT NULL DEFAULT 0,
                    ingestion_status TEXT NOT NULL,
                    error_message TEXT,
                    ingested_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    project_id TEXT NOT NULL DEFAULT 'prj_cedar_lantern_wind',
                    landowner_id TEXT NOT NULL DEFAULT 'lnd_unassigned',
                    energy_technology TEXT NOT NULL DEFAULT 'ONSHORE_WIND',
                    grantor_landowner_name TEXT,
                    grantee_entity_name TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS clauses (
                    document_id TEXT NOT NULL,
                    gcs_pdf_uri TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    parent_node_id TEXT,
                    sibling_order INTEGER NOT NULL,
                    document_zone TEXT NOT NULL,
                    canonical_path TEXT NOT NULL,
                    depth INTEGER NOT NULL,
                    clause_label TEXT NOT NULL,
                    numbering_scheme TEXT NOT NULL,
                    level_1_label TEXT,
                    level_2_label TEXT,
                    level_3_label TEXT,
                    level_4_label TEXT,
                    level_5_plus_path TEXT,
                    clause_title TEXT,
                    is_inline_clause INTEGER NOT NULL,
                    preamble_text TEXT,
                    verbatim_text TEXT NOT NULL,
                    postamble_text TEXT,
                    reconstructed_context_text TEXT NOT NULL,
                    defined_terms_used TEXT NOT NULL,
                    cross_references TEXT NOT NULL,
                    page_start INTEGER NOT NULL,
                    page_end INTEGER NOT NULL,
                    has_special_condition INTEGER NOT NULL DEFAULT 0,
                    special_condition_count INTEGER NOT NULL DEFAULT 0,
                    hitl_status TEXT NOT NULL,
                    hitl_flag_reasons TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
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
        self._local_db_initialized = True

    def ensure_bq_tables(self) -> None:
        """Auto-create dataset, 8 append-only tables (evolving existing schemas), and deduplicated views in BigQuery."""
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

            self._bq_tables_ensured = True
        except Exception as exc:
            logger.warning("BigQuery table creation warning: %s", exc)

    def archive_raw_pdf(
        self, pdf_bytes: bytes, filename: str, document_id: str | None = None
    ) -> tuple[str, str]:
        """Archive the source PDF to local disk and gs://<bucket>/raw/<document_id>/<filename>."""
        self._ensure_local_dirs_and_db()
        doc_id = document_id or compute_document_id(pdf_bytes)
        safe_name = Path(filename).name
        local_path = self.local_raw_dir / doc_id / safe_name
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(pdf_bytes)

        gcs_uri = f"gs://{self.config.gcs_bucket_name}/raw/{doc_id}/{safe_name}"
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
        self._ensure_local_dirs_and_db()
        with sqlite3.connect(self.local_db_path) as conn:
            cols = ", ".join(columns)
            placeholders = ", ".join(["?"] * len(columns))
            conn.executemany(
                f"INSERT INTO {table_name} ({cols}) VALUES ({placeholders})",
                [[r[c] for c in columns] for r in rows_json],
            )
        if self.config.use_bigquery and not sqlite_only:
            self.ensure_bq_tables()
            table_id = f"{self.config.bq_dataset_fqn}.{table_name}"
            errors = self.bq_client.insert_rows_json(table_id, rows_json)
            if errors:
                logger.warning("BigQuery insert_rows_json errors on %s: %s", table_name, errors)

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
        self._append_bundle_rows(bundle, sqlite_only=False)
        return self.export_csvs(bundle)

    def export_csvs(self, bundle: ParsedContractBundle) -> dict[str, str]:
        """Write utf-8-sig CSV files (`clauses.csv`, `defined_terms.csv`, `exhibits_catalog.csv`, `special_conditions.csv`, `dnd_checklist_signoffs.csv`) to disk and GCS."""
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
        return val

    @staticmethod
    def _coalesce_sc_field(k: str, val: object) -> object:
        if k == "project_id" and not val:
            return "prj_cedar_lantern_wind"
        if k == "landowner_id" and not val:
            return "lnd_unassigned"
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
                ORDER BY page_start ASC, depth ASC, sibling_order ASC
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
                        k: (
                            bool(r[k])
                            if k in ("is_inline_clause", "has_special_condition")
                            else (0 if k == "special_condition_count" and r[k] is None else r[k])
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
            ORDER BY page_start ASC, depth ASC, sibling_order ASC
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
            p = ProjectRow.model_validate({k: r[k] for k in PROJECT_CSV_COLUMNS})
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



