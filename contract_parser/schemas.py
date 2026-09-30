"""Pydantic schemas for Gemini Structured Outputs, Append-Only BigQuery tables, and CSV exports."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from pydantic import BaseModel, ConfigDict, Field


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format compatible with BigQuery TIMESTAMP."""
    return datetime.now(timezone.utc).isoformat()


class DocumentZone(StrEnum):
    """High-level structural zone of a contract."""

    PREAMBLE = "PREAMBLE"
    RECITALS = "RECITALS"
    BODY = "BODY"
    SIGNATURES = "SIGNATURES"
    EXHIBIT_OR_SCHEDULE = "EXHIBIT_OR_SCHEDULE"
    AMENDMENT = "AMENDMENT"


class NumberingScheme(StrEnum):
    """Universal numbering or heading scheme for any contract node."""

    ROMAN_UPPER = "ROMAN_UPPER"
    INTEGER = "INTEGER"
    DECIMAL = "DECIMAL"
    ALPHA_LOWER = "ALPHA_LOWER"
    ALPHA_UPPER = "ALPHA_UPPER"
    ROMAN_LOWER = "ROMAN_LOWER"
    PAREN_INT = "PAREN_INT"
    NAMED_HEADER = "NAMED_HEADER"
    UNNUMBERED = "UNNUMBERED"


class ExhibitModality(StrEnum):
    """Modality classification for attached Exhibits, Schedules, or Appendices."""

    PROSE_CLAUSES = "PROSE_CLAUSES"
    TABULAR_SCHEDULE = "TABULAR_SCHEDULE"
    PROPERTY_DESCRIPTION = "PROPERTY_DESCRIPTION"
    EXTERNAL_INSTRUMENT_LIST = "EXTERNAL_INSTRUMENT_LIST"
    VISUAL_DRAWING_OR_MAP_STUB = "VISUAL_DRAWING_OR_MAP_STUB"


class DefinitionType(StrEnum):
    """How a Defined Term is introduced in the contract."""

    DEDICATED_DEFINITION_CLAUSE = "DEDICATED_DEFINITION_CLAUSE"
    INLINE_PARENTHETICAL = "INLINE_PARENTHETICAL"
    EXTERNAL_INCORPORATION = "EXTERNAL_INCORPORATION"


class HITLStatus(StrEnum):
    """Human-in-the-Loop verification lifecycle state."""

    VERIFIED_AUTO = "VERIFIED_AUTO"
    FLAGGED_FOR_REVIEW = "FLAGGED_FOR_REVIEW"
    PLACEHOLDER_FOR_REVIEW = "PLACEHOLDER_FOR_REVIEW"
    APPROVED_BY_HUMAN = "APPROVED_BY_HUMAN"


class FlagCode(StrEnum):
    """Six contract-agnostic anomaly codes triggering HITL review."""

    TEXT_COVERAGE_GAP = "TEXT_COVERAGE_GAP"
    AMBIGUOUS_HIERARCHY_MARKER = "AMBIGUOUS_HIERARCHY_MARKER"
    SCOPE_CARVEOUT_DETECTED = "SCOPE_CARVEOUT_DETECTED"
    UNRESOLVED_EXTERNAL_DEPENDENCY = "UNRESOLVED_EXTERNAL_DEPENDENCY"
    BROKEN_INTERNAL_REFERENCE = "BROKEN_INTERNAL_REFERENCE"
    DEFERRED_MODALITY_PLACEHOLDER = "DEFERRED_MODALITY_PLACEHOLDER"


class IngestionStatus(StrEnum):
    """Lifecycle status of an ingested contract document."""

    PROCESSING = "PROCESSING"
    NEEDS_HITL_REVIEW = "NEEDS_HITL_REVIEW"
    VERIFIED_COMPLETE = "VERIFIED_COMPLETE"
    FAILED = "FAILED"


class ClauseRow(BaseModel):
    """Table 2: `clauses` / `clauses.csv` (30 columns: 27 core structural/context fields + 3 audit fields)."""

    model_config = ConfigDict(extra="ignore")

    document_id: str = Field(default="", description="Foreign key to documents.document_id")
    gcs_pdf_uri: str = Field(default="", description="Direct GCS URI of the source PDF")
    node_id: str = Field(description="Unique key for this structural node within the document (e.g. BODY.4.i)")
    parent_node_id: str | None = Field(default=None, description="Immediate parent node_id (None for root nodes)")
    sibling_order: int = Field(default=1, description="1-indexed sequential order among siblings under same parent")
    document_zone: DocumentZone = Field(description="PREAMBLE, RECITALS, BODY, SIGNATURES, EXHIBIT_OR_SCHEDULE, AMENDMENT")
    canonical_path: str = Field(description="Lossless dot-delimited path (e.g. RECITALS.1, BODY.4.i, EXHIBIT_A.PARCEL_4.EXCEPT_1)")
    depth: int = Field(ge=1, description="Structural nesting depth (1..N) relative to the zone root")
    clause_label: str = Field(description="Exact numbering or heading token of this node (e.g. 1, (a), (i), Parcel 4)")
    numbering_scheme: NumberingScheme = Field(description="Numbering scheme of clause_label")
    level_1_label: str | None = Field(default=None, description="Tier 1 ancestor label (e.g. 1, 4, Exhibit A)")
    level_2_label: str | None = Field(default=None, description="Tier 2 label if depth >= 2 (e.g. (a), (i), Parcel 4)")
    level_3_label: str | None = Field(default=None, description="Tier 3 label if depth >= 3 (e.g. LESS_AND_EXCEPT)")
    level_4_label: str | None = Field(default=None, description="Tier 4 label if depth >= 4")
    level_5_plus_path: str | None = Field(default=None, description="Overflow sub-path for depth >= 5")
    clause_title: str | None = Field(default=None, description="Extracted title/heading of the clause or section, if present")
    is_inline_clause: bool = Field(default=False, description="True if extracted from an inline run-in paragraph")
    preamble_text: str | None = Field(default=None, description="Lead-in text on a parent node preceding its first child")
    verbatim_text: str = Field(description="Exact atomic text belonging strictly to this node")
    postamble_text: str | None = Field(default=None, description="Trailing modifier or continuation sentences after children on a parent")
    reconstructed_context_text: str = Field(description="Complete legal text combining ancestor preambles, verbatim_text, and ancestor postambles")
    defined_terms_used: str = Field(default="NONE", description="Pipe-delimited Defined Terms used in this clause, or NONE")
    cross_references: str = Field(default="NONE", description="Pipe-delimited internal/external references, or NONE")
    page_start: int = Field(ge=1, description="Starting 1-indexed physical PDF page number")
    page_end: int = Field(ge=1, description="Ending 1-indexed physical PDF page number")
    hitl_status: HITLStatus = Field(default=HITLStatus.VERIFIED_AUTO, description="HITL verification status")
    hitl_flag_reasons: str = Field(default="NONE", description="Pipe-delimited FlagCode values or NONE")
    updated_at: str = Field(default_factory=utc_now_iso, description="UTC timestamp of this row version")
    reviewed_by: str | None = Field(default=None, description="Human reviewer identifier (None for initial AI version)")
    review_notes: str | None = Field(default=None, description="Optional reviewer notes")


class DefinedTermRow(BaseModel):
    """Table 3: `defined_terms` / `defined_terms.csv` (8 columns)."""

    model_config = ConfigDict(extra="ignore")

    document_id: str = Field(default="", description="Foreign key to documents.document_id")
    term_name: str = Field(description="Normalized defined term (e.g. Property, Operations, Equipment)")
    defined_in_node_id: str = Field(description="node_id where the term is formally defined")
    definition_type: DefinitionType = Field(description="DEDICATED_DEFINITION_CLAUSE, INLINE_PARENTHETICAL, or EXTERNAL_INCORPORATION")
    verbatim_definition: str = Field(description="Exact text defining the term")
    referenced_in_nodes: str = Field(default="NONE", description="Pipe-delimited list of node_ids that use this term")
    page_number: int = Field(ge=1, description="1-indexed physical PDF page number where defined")
    updated_at: str = Field(default_factory=utc_now_iso, description="UTC timestamp of this row version")


class ExhibitCatalogRow(BaseModel):
    """Table 4: `exhibits_catalog` / `exhibits_catalog.csv` (10 columns)."""

    model_config = ConfigDict(extra="ignore")

    document_id: str = Field(default="", description="Foreign key to documents.document_id")
    exhibit_id: str = Field(description="Identifier (e.g. Exhibit A, Exhibit B, Exhibit C, Exhibit D)")
    exhibit_title: str = Field(description="Extracted title of the exhibit/schedule")
    exhibit_modality: ExhibitModality = Field(description="Modality classification of the exhibit")
    page_start: int = Field(ge=1, description="Starting 1-indexed physical PDF page")
    page_end: int = Field(ge=1, description="Ending 1-indexed physical PDF page")
    referenced_by_nodes: str = Field(default="NONE", description="Pipe-delimited body node_ids that reference this exhibit")
    structured_entities_json: str = Field(default="[]", description="JSON array string of extracted parcels, carve-outs, or instruments")
    has_unresolved_external_dep: bool = Field(default=False, description="True if a governing clause depends on unattached external terms in this exhibit")
    updated_at: str = Field(default_factory=utc_now_iso, description="UTC timestamp of this row version")


class DocumentRegistryRow(BaseModel):
    """Table 1: `documents` Master Document Registry in BigQuery (12 columns)."""

    model_config = ConfigDict(extra="ignore")

    document_id: str = Field(description="Primary key (deterministic SHA-256 content hash)")
    filename: str = Field(description="Original uploaded filename")
    gcs_pdf_uri: str = Field(description="gs://<bucket>/raw/<document_id>/<filename>.pdf")
    gcs_export_prefix: str = Field(description="gs://<bucket>/exports/<document_id>/")
    page_count: int = Field(ge=1, description="Total physical pages in the PDF")
    contracting_parties_json: str = Field(default="[]", description="JSON array of contracting parties and roles")
    effective_date: str | None = Field(default=None, description="Extracted effective or execution date")
    flagged_node_count: int = Field(default=0, description="Count of clauses in FLAGGED_FOR_REVIEW or PLACEHOLDER_FOR_REVIEW")
    ingestion_status: IngestionStatus = Field(default=IngestionStatus.PROCESSING, description="Ingestion lifecycle status")
    error_message: str | None = Field(default=None, description="Populated if ingestion_status == FAILED")
    ingested_at: str = Field(default_factory=utc_now_iso, description="UTC timestamp of initial ingestion")
    updated_at: str = Field(default_factory=utc_now_iso, description="UTC timestamp of this row version")


class ClauseReviewRequest(BaseModel):
    """Payload for human review approvals and inline edits via PATCH endpoint."""

    hitl_status: HITLStatus = Field(default=HITLStatus.APPROVED_BY_HUMAN)
    verbatim_text: str | None = Field(default=None)
    reconstructed_context_text: str | None = Field(default=None)
    clause_label: str | None = Field(default=None)
    reviewed_by: str = Field(default="human_reviewer")
    review_notes: str | None = Field(default=None)


class GeminiContractExtraction(BaseModel):
    """Top-level structured output schema returned directly by Gemini 3.x."""

    page_count: int = Field(ge=1, description="Total physical pages in the PDF document")
    contracting_parties_json: str = Field(
        default="[]",
        description="JSON array string of contracting parties, e.g. '[{\"name\": \"Cedar Lantern Wind, LLC\", \"short_name\": \"Cedar Lantern\", \"role\": \"Wind Project Developer\"}]'",
    )
    effective_date: str | None = Field(
        default=None,
        description="Effective or execution date stated in the agreement (e.g. 'October 14, 2024')",
    )
    clauses: list[ClauseRow] = Field(
        default_factory=list,
        description="Ordered list of all hierarchical clause nodes across PREAMBLE, RECITALS, BODY, SIGNATURES, EXHIBIT_OR_SCHEDULE, and AMENDMENT zones",
    )
    defined_terms: list[DefinedTermRow] = Field(
        default_factory=list,
        description="All capitalized Defined Terms defined in dedicated clauses or inline parentheticals",
    )
    exhibits_catalog: list[ExhibitCatalogRow] = Field(
        default_factory=list,
        description="Catalog of all attached Exhibits, Schedules, or Appendices",
    )


class ParsedContractBundle(BaseModel):
    """Complete hydrated contract bundle returned by the pipeline and API."""

    document: DocumentRegistryRow
    clauses: list[ClauseRow]
    defined_terms: list[DefinedTermRow]
    exhibits_catalog: list[ExhibitCatalogRow]


CLAUSE_CSV_COLUMNS: list[str] = list(ClauseRow.model_fields.keys())
DEFINED_TERM_CSV_COLUMNS: list[str] = list(DefinedTermRow.model_fields.keys())
EXHIBIT_CATALOG_CSV_COLUMNS: list[str] = list(ExhibitCatalogRow.model_fields.keys())
DOCUMENT_REGISTRY_COLUMNS: list[str] = list(DocumentRegistryRow.model_fields.keys())
