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
from contract_parser.schemas import (
    CLAUSE_CSV_COLUMNS,
    DEFINED_TERM_CSV_COLUMNS,
    DOCUMENT_REGISTRY_COLUMNS,
    EXHIBIT_CATALOG_CSV_COLUMNS,
    ClauseReviewRequest,
    ClauseRow,
    DefinedTermRow,
    DocumentRegistryRow,
    ExhibitCatalogRow,
    HITLStatus,
    IngestionStatus,
    ParsedContractBundle,
    utc_now_iso,
)

logger = logging.getLogger(__name__)


def compute_document_id(pdf_bytes: bytes) -> str:
    """Compute a deterministic SHA-256 content-addressed document_id."""
    digest = hashlib.sha256(pdf_bytes).hexdigest()[:16]
    return f"doc_{digest}"


BQ_DOCUMENTS_SCHEMA = [
    bigquery.SchemaField("document_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("filename", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("gcs_pdf_uri", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("gcs_export_prefix", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("page_count", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("contracting_parties_json", "JSON", mode="NULLABLE"),
    bigquery.SchemaField("effective_date", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("flagged_node_count", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("ingestion_status", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("error_message", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("ingested_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("updated_at", "TIMESTAMP", mode="REQUIRED"),
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


class ContractStorageService:
    """Manages GCS artifacts, Append-Only BigQuery tables (`QUALIFY ROW_NUMBER() = 1`), CSV exports, and local SQLite."""

    def __init__(self, config: PipelineConfig | None = None) -> None:
        self.config = config or PipelineConfig()
        self.local_root = Path(self.config.local_data_dir)
        self.local_raw_dir = self.local_root / "raw"
        self.local_exports_dir = self.local_root / "exports"
        self.local_db_path = self.local_root / "contract_intelligence.db"

        self._local_db_initialized: bool = False
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
        """Lazily initialize local directories and append-only SQLite tables on first use."""
        if self._local_db_initialized and self.local_db_path.exists():
            return
        self.local_raw_dir.mkdir(parents=True, exist_ok=True)
        self.local_exports_dir.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.local_db_path) as conn:
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
                    ingestion_status TEXT NOT NULL,
                    error_message TEXT,
                    ingested_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
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
        self._local_db_initialized = True

    def ensure_bq_tables(self) -> None:
        """Auto-create dataset, 4 append-only tables, and deduplicated views in BigQuery if enabled."""
        if not self.config.use_bigquery or self._bq_tables_ensured:
            return
        try:
            dataset_ref = bigquery.Dataset(self.config.bq_dataset_fqn)
            dataset_ref.location = "US"
            self.bq_client.create_dataset(dataset_ref, exists_ok=True)

            table_specs = {
                "documents": (BQ_DOCUMENTS_SCHEMA, "document_id"),
                "clauses": (BQ_CLAUSES_SCHEMA, "document_id, node_id"),
                "defined_terms": (BQ_DEFINED_TERMS_SCHEMA, "document_id, term_name"),
                "exhibits_catalog": (BQ_EXHIBITS_CATALOG_SCHEMA, "document_id, exhibit_id"),
            }
            for table_name, (schema, partition_keys) in table_specs.items():
                table_id = f"{self.config.bq_dataset_fqn}.{table_name}"
                table = bigquery.Table(table_id, schema=schema)
                self.bq_client.create_table(table, exists_ok=True)

                view_id = f"{self.config.bq_dataset_fqn}.{table_name}_latest"
                view_sql = (
                    f"SELECT * FROM `{table_id}` "
                    f"QUALIFY ROW_NUMBER() OVER (PARTITION BY {partition_keys} ORDER BY updated_at DESC) = 1"
                )
                view = bigquery.Table(view_id)
                view.view_query = view_sql
                try:
                    self.bq_client.create_table(view, exists_ok=True)
                except Exception:
                    pass

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

    def persist_bundle(self, bundle: ParsedContractBundle) -> dict[str, str]:
        """Stream all 4 tables in append-only mode and export utf-8-sig CSVs to local & GCS."""
        self.append_document_row(bundle.document)
        self.append_clauses(bundle.clauses)
        self.append_defined_terms(bundle.defined_terms)
        self.append_exhibits_catalog(bundle.exhibits_catalog)
        return self.export_csvs(bundle)

    def export_csvs(self, bundle: ParsedContractBundle) -> dict[str, str]:
        """Write utf-8-sig CSV files (`clauses.csv`, `defined_terms.csv`, `exhibits_catalog.csv`) to disk and GCS."""
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

        return exported_paths

    def list_documents(self) -> list[DocumentRegistryRow]:
        """Return deduplicated latest version of all documents ordered by updated_at DESC."""
        self._ensure_local_dirs_and_db()
        if self.config.use_bigquery:
            try:
                sql = f"""
                    SELECT {", ".join(DOCUMENT_REGISTRY_COLUMNS)}
                    FROM `{self.config.bq_dataset_fqn}.documents`
                    QUALIFY ROW_NUMBER() OVER (PARTITION BY document_id ORDER BY updated_at DESC) = 1
                    ORDER BY updated_at DESC
                """
                bq_rows = list(self.bq_client.query(sql).result())
                if bq_rows:
                    return [
                        DocumentRegistryRow.model_validate(
                            {
                                k: (
                                    r[k].isoformat()
                                    if hasattr(r[k], "isoformat")
                                    else (json.dumps(r[k]) if k == "contracting_parties_json" and not isinstance(r[k], str) else r[k])
                                )
                                for k in DOCUMENT_REGISTRY_COLUMNS
                            }
                        )
                        for r in bq_rows
                    ]
            except Exception as exc:
                logger.warning("BigQuery list_documents fallback to SQLite: %s", exc)

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
        return [
            DocumentRegistryRow.model_validate({k: r[k] for k in DOCUMENT_REGISTRY_COLUMNS})
            for r in rows
        ]

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

        return ParsedContractBundle(
            document=DocumentRegistryRow.model_validate(
                {k: doc_row[k] for k in DOCUMENT_REGISTRY_COLUMNS}
            ),
            clauses=[
                ClauseRow.model_validate(
                    {
                        k: (bool(r[k]) if k == "is_inline_clause" else r[k])
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
        )

    def get_bundle(self, document_id: str) -> ParsedContractBundle:
        """Retrieve the latest deduplicated ParsedContractBundle for document_id."""
        if self.config.use_bigquery:
            try:
                return self.get_bundle_from_bigquery(document_id)
            except KeyError:
                pass
            except Exception as exc:
                logger.warning("BigQuery get_bundle fallback to SQLite for %s: %s", document_id, exc)

        sqlite_bundle = self._get_bundle_from_sqlite(document_id)
        if sqlite_bundle is not None:
            return sqlite_bundle

        raise KeyError(f"Document {document_id!r} not found")

    def get_bundle_from_bigquery(self, document_id: str) -> ParsedContractBundle:
        """Query BigQuery directly using QUALIFY ROW_NUMBER() = 1 deduplication."""
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

        def _norm_row(r: bigquery.Row, cols: list[str]) -> dict[str, object]:
            out: dict[str, object] = {}
            for k in cols:
                val = r[k]
                if hasattr(val, "isoformat"):
                    val = val.isoformat()
                elif k in ("contracting_parties_json", "structured_entities_json") and val is not None and not isinstance(val, str):
                    val = json.dumps(val)
                out[k] = val
            return out

        return ParsedContractBundle(
            document=DocumentRegistryRow.model_validate(_norm_row(doc_res[0], DOCUMENT_REGISTRY_COLUMNS)),
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
        )

    def review_clause(
        self,
        document_id: str,
        node_id: str,
        review: ClauseReviewRequest,
    ) -> tuple[ClauseRow, DocumentRegistryRow]:
        """Append a new human-reviewed ClauseRow version and updated DocumentRegistryRow version (Option 3B)."""
        bundle = self.get_bundle(document_id)
        # If local SQLite didn't have the full bundle yet, hydrate it locally before appending the partial update
        if self._get_bundle_from_sqlite(document_id) is None:
            self._append_table_rows("documents", DOCUMENT_REGISTRY_COLUMNS, [bundle.document.model_dump(mode="json")], sqlite_only=True)
            self._append_table_rows("clauses", CLAUSE_CSV_COLUMNS, [c.model_dump(mode="json") for c in bundle.clauses], sqlite_only=True)
            self._append_table_rows("defined_terms", DEFINED_TERM_CSV_COLUMNS, [t.model_dump(mode="json") for t in bundle.defined_terms], sqlite_only=True)
            self._append_table_rows("exhibits_catalog", EXHIBIT_CATALOG_CSV_COLUMNS, [e.model_dump(mode="json") for e in bundle.exhibits_catalog], sqlite_only=True)

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
        )
        self.export_csvs(updated_bundle)
        return updated_clause, updated_doc

    def get_csv_bytes(self, document_id: str, csv_name: str) -> bytes:
        """Return the latest deduplicated CSV export bytes (`utf-8-sig`)."""
        self._ensure_local_dirs_and_db()
        if csv_name not in ("clauses.csv", "defined_terms.csv", "exhibits_catalog.csv"):
            raise ValueError(f"Unsupported CSV name: {csv_name!r}")
        local_csv = self.local_exports_dir / document_id / csv_name
        if not local_csv.exists():
            bundle = self.get_bundle(document_id)
            self.export_csvs(bundle)
        return local_csv.read_bytes()

    @staticmethod
    def load_bundle_from_csv(
        export_dir: Path, document_row: DocumentRegistryRow
    ) -> ParsedContractBundle:
        """Reload a ParsedContractBundle from exported utf-8-sig CSVs (verifies Session 2 compatibility)."""
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
        )
