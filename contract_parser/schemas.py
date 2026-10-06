"""Pydantic schemas for Gemini Structured Outputs, Append-Only BigQuery tables, and CSV exports."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Self
import re
from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    EXHIBIT_A = "EXHIBIT_OR_SCHEDULE"
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
    BLOCK_HEADER = "NAMED_HEADER"
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
    """Contract-agnostic anomaly and operational risk codes triggering HITL review."""

    TEXT_COVERAGE_GAP = "TEXT_COVERAGE_GAP"
    AMBIGUOUS_HIERARCHY_MARKER = "AMBIGUOUS_HIERARCHY_MARKER"
    SCOPE_CARVEOUT_DETECTED = "SCOPE_CARVEOUT_DETECTED"
    UNRESOLVED_EXTERNAL_DEPENDENCY = "UNRESOLVED_EXTERNAL_DEPENDENCY"
    BROKEN_INTERNAL_REFERENCE = "BROKEN_INTERNAL_REFERENCE"
    DEFERRED_MODALITY_PLACEHOLDER = "DEFERRED_MODALITY_PLACEHOLDER"
    LANDOWNER_SPECIAL_CONDITION = "LANDOWNER_SPECIAL_CONDITION"


class ConstraintCategory(StrEnum):
    """Unified taxonomy for operational and physical site constraints across all 5 energy technologies (CR-1 & CR-2)."""

    # CR-1 core taxonomy values
    TREE_VEGETATION_PROTECTION = "TREE_VEGETATION_PROTECTION"
    STRUCTURE_BARN_WELL_SETBACK = "STRUCTURE_BARN_WELL_SETBACK"
    ACCESS_ROAD_GATE_PROTOCOL = "ACCESS_ROAD_GATE_PROTOCOL"
    LIVESTOCK_AGRICULTURE = "LIVESTOCK_AGRICULTURE"
    TIMING_NOISE_HUNTING_BLACKOUT = "TIMING_NOISE_HUNTING_BLACKOUT"
    FINANCIAL_PENALTY_LIQUIDATED_DAMAGES = "FINANCIAL_PENALTY_LIQUIDATED_DAMAGES"
    OTHER_CUSTOM_RIDER = "OTHER_CUSTOM_RIDER"

    # CR-2 generalized 10-category cross-technology taxonomy values
    SETBACK_OR_BUFFER = "SETBACK_OR_BUFFER"
    CONSTRUCTION_OR_BLACKOUT_WINDOW = "CONSTRUCTION_OR_BLACKOUT_WINDOW"
    CROP_OR_TIMBER_COMPENSATION = "CROP_OR_TIMBER_COMPENSATION"
    NOISE_OR_SHADOW_FLICKER = "NOISE_OR_SHADOW_FLICKER"
    DRAINAGE_OR_SOIL_RESTORATION = "DRAINAGE_OR_SOIL_RESTORATION"
    GATES_FENCING_OR_LIVESTOCK = "GATES_FENCING_OR_LIVESTOCK"
    ACCESS_ROAD_OR_PARCEL_RESTRICTION = "ACCESS_ROAD_OR_PARCEL_RESTRICTION"
    BLASTING_OR_EXCAVATION = "BLASTING_OR_EXCAVATION"
    DECOMMISSIONING_OR_BOND = "DECOMMISSIONING_OR_BOND"
    OTHER_SPECIAL_CONDITION = "OTHER_SPECIAL_CONDITION"


class EnergyTechnology(StrEnum):
    """Five Invenergy renewable energy technology classifications (CR-2 / R3)."""

    ONSHORE_WIND = "ONSHORE_WIND"
    SOLAR = "SOLAR"
    STORAGE = "STORAGE"
    TRANSMISSION = "TRANSMISSION"
    GEOTHERMAL = "GEOTHERMAL"


class IngestionStatus(StrEnum):
    """Lifecycle status of an ingested contract document."""

    PROCESSING = "PROCESSING"
    NEEDS_HITL_REVIEW = "NEEDS_HITL_REVIEW"
    VERIFIED_COMPLETE = "VERIFIED_COMPLETE"
    FAILED = "FAILED"


class ProjectRow(BaseModel):
    """Table 6: `projects` Master ERP Project Registry in BigQuery & SQLite (12 columns for CR-2 / R3)."""

    model_config = ConfigDict(extra="ignore")

    project_id: str = Field(description="Canonical project identifier (e.g. prj_cedar_lantern_wind)")
    project_name: str = Field(description="Human-readable project name (e.g. Cedar Lantern Wind Energy Center)")
    energy_technology: EnergyTechnology = Field(
        default=EnergyTechnology.ONSHORE_WIND,
        description="ONSHORE_WIND, SOLAR, STORAGE, TRANSMISSION, or GEOTHERMAL",
    )
    erp_project_code: str = Field(description="Downstream Oracle ERP project code (e.g. ERP-WND-001)")
    state_province: str | None = Field(default=None, description="Primary state or province jurisdiction (e.g. IL, OH, TX)")
    county: str | None = Field(default=None, description="Primary county jurisdiction")
    target_capacity_mw: float | None = Field(default=None, description="Target nameplate capacity in MW")
    landowner_count: int = Field(default=0, description="Total distinct landowners bound to this project")
    document_count: int = Field(default=0, description="Total deduplicated contract PDFs in this project's stack")
    special_conditions_count: int = Field(
        default=0,
        description="Total extracted SpecialConditionRow items across all contracts in this project",
    )
    flagged_node_count: int = Field(
        default=0,
        description="Total clauses awaiting HITL review across this project",
    )
    updated_at: str = Field(default_factory=utc_now_iso, description="UTC timestamp of this row version")


class LandownerRow(BaseModel):
    """Table 7: `landowners` Master QRM Landowner Registry in BigQuery & SQLite (10 columns for CR-2 / R3)."""

    model_config = ConfigDict(extra="ignore")

    landowner_id: str = Field(description="Deterministic or QRM-backed ID (lnd_<project_id>_<slug>)")
    project_id: str = Field(default="prj_cedar_lantern_wind", description="Parent ProjectRow.project_id")
    landowner_name: str = Field(description="Canonical Grantor / Landowner counterparty name")
    qrm_party_id: str | None = Field(default=None, description="External Oracle QRM landowner key (e.g. QRM-A1B2C3)")
    grantee_entity_name: str | None = Field(default=None, description="Project operating subsidiary / Grantee SPV")
    parcel_summary: str | None = Field(default=None, description="Pipe-delimited summary of parcels across contracts")
    is_multi_parcel: bool = Field(
        default=False,
        description="False for 90% 1:1 case; True when contract_count > 1 or multiple EXHIBIT_A.PARCEL_* nodes exist",
    )
    contract_count: int = Field(
        default=1,
        description="Number of deduplicated contracts linked to this landowner in this project",
    )
    special_conditions_count: int = Field(
        default=0,
        description="Total SpecialConditionRow constraints across this landowner's contracts",
    )
    updated_at: str = Field(default_factory=utc_now_iso, description="UTC timestamp of this row version")



class ClauseRow(BaseModel):
    """Table 2: `clauses` / `clauses.csv` (32 columns: 27 core structural/context fields + 2 CR-1 roll-ups + 3 audit fields)."""

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
    has_special_condition: bool = Field(
        default=False,
        description="True if one or more Landowner Special Conditions are attached to this node_id",
    )
    special_condition_count: int = Field(
        default=0,
        description="Number of normalized SpecialConditionRow items attached to this node_id",
    )
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


class SpecialConditionRow(BaseModel):
    """Table 5: `special_conditions` / `special_conditions.csv` (18 columns: 16 CR-1 fields + 2 CR-2 portfolio keys)."""

    model_config = ConfigDict(extra="ignore")

    condition_id: str = Field(
        default="",
        description="Deterministic ID (`sc_<node_id>_<1_based_index>`, e.g., `sc_BODY.12.a_1`)",
    )
    document_id: str = Field(
        default="",
        description="Parent contract SHA-256 document ID (`doc_<sha256[:16]>`)",
    )
    node_id: str = Field(
        description="Source ClauseRow.node_id (most specific leaf node where constraint appears)"
    )
    canonical_path: str = Field(
        default="",
        description="Lossless dot-delimited clause path from ClauseRow.canonical_path",
    )
    constraint_category: ConstraintCategory = Field(
        description="Unified ConstraintCategory enum value across CR-1 and CR-2 taxonomies"
    )
    target_asset_or_area: str = Field(
        description="Physical feature, structure, or parcel zone (e.g., 'Northern Red Barn', 'Gate #3')"
    )
    quantitative_metric: str | None = Field(
        default=None,
        description="Extracted buffer distance, weight limit, or measurement (e.g., '150 feet', '20 tons')",
    )
    temporal_restriction: str | None = Field(
        default=None,
        description="Extracted date window, notice period, or hours (e.g., 'Nov 15 - Dec 15', '48 hours notice')",
    )
    penalty_or_consequence: str | None = Field(
        default=None,
        description="Explicit financial penalty or remedy if violated (e.g., '$100,000 liquidated damages')",
    )
    actionable_obligation_summary: str = Field(
        description="Plain-English instruction for field/construction crews"
    )
    verbatim_excerpt: str = Field(
        description="Exact substring from the contract establishing the constraint"
    )
    page_number: int = Field(
        default=1,
        ge=1,
        description="1-indexed physical PDF page number for instant pdf.js jump",
    )
    extraction_confidence: float = Field(
        default=0.95,
        ge=0.0,
        le=1.0,
        description="Model confidence score ([0.0, 1.0], default 0.95 for LLM, 0.75 for regex fallback)",
    )
    hitl_status: HITLStatus = Field(
        default=HITLStatus.FLAGGED_FOR_REVIEW,
        description="Defaults to HITLStatus.FLAGGED_FOR_REVIEW per Decision #4 (APPROVED_BY_HUMAN after sign-off)",
    )
    updated_at: str = Field(
        default_factory=utc_now_iso,
        description="Append-only UTC timestamp",
    )
    reviewed_by: str | None = Field(
        default=None,
        description="Reviewer identifier once verified in the HITL Workbench",
    )
    project_id: str = Field(
        default="prj_cedar_lantern_wind",
        description="Parent ProjectRow.project_id for centralized project-level constraint search (CR-2)",
    )
    landowner_id: str = Field(
        default="lnd_unassigned",
        description="Parent LandownerRow.landowner_id for landowner-level constraint roll-up (CR-2)",
    )


class DocumentRegistryRow(BaseModel):
    """Table 1: `documents` Master Document Registry in BigQuery (18 columns: 13 core/CR-1 fields + 5 CR-2 portfolio fields)."""

    model_config = ConfigDict(extra="ignore")

    document_id: str = Field(description="Primary key (deterministic SHA-256 content hash)")
    filename: str = Field(description="Original uploaded filename")
    gcs_pdf_uri: str = Field(description="gs://<bucket>/raw/<document_id>/<filename>.pdf")
    gcs_export_prefix: str = Field(description="gs://<bucket>/exports/<document_id>/")
    page_count: int = Field(ge=1, description="Total physical pages in the PDF")
    contracting_parties_json: str = Field(default="[]", description="JSON array of contracting parties and roles")
    effective_date: str | None = Field(default=None, description="Extracted effective or execution date")
    flagged_node_count: int = Field(default=0, description="Count of clauses in FLAGGED_FOR_REVIEW or PLACEHOLDER_FOR_REVIEW")
    special_conditions_count: int = Field(
        default=0,
        description="Total number of normalized Landowner Special Conditions extracted across the document",
    )
    ingestion_status: IngestionStatus = Field(default=IngestionStatus.PROCESSING, description="Ingestion lifecycle status")
    error_message: str | None = Field(default=None, description="Populated if ingestion_status == FAILED")
    ingested_at: str = Field(default_factory=utc_now_iso, description="UTC timestamp of initial ingestion")
    updated_at: str = Field(default_factory=utc_now_iso, description="UTC timestamp of this row version")
    project_id: str = Field(
        default="prj_cedar_lantern_wind",
        description="Parent ProjectRow.project_id selected prior to upload (CR-2)",
    )
    landowner_id: str = Field(
        default="lnd_unassigned",
        description="Parent LandownerRow.landowner_id bound during ingestion (CR-2)",
    )
    energy_technology: EnergyTechnology = Field(
        default=EnergyTechnology.ONSHORE_WIND,
        description="Project energy technology classification (CR-2)",
    )
    grantor_landowner_name: str | None = Field(
        default=None,
        description="Grantor / Landowner counterparty name extracted from the contract (CR-2)",
    )
    grantee_entity_name: str | None = Field(
        default=None,
        description="Grantee / Developer SPV entity name extracted from the contract (CR-2)",
    )


class CreateProjectRequest(BaseModel):
    """Payload for creating or updating an ERP ProjectRow via POST /api/v1/projects."""

    model_config = ConfigDict(extra="ignore")

    project_id: str | None = Field(default=None, description="Optional explicit project_id; generated from project_name if omitted")
    project_name: str = Field(description="Human-readable project name")
    energy_technology: EnergyTechnology = Field(default=EnergyTechnology.ONSHORE_WIND)
    erp_project_code: str = Field(default="ERP-CUSTOM-001", description="Oracle ERP project code (e.g. ERP-WND-909)")
    state_province: str | None = Field(default=None)
    county: str | None = Field(default=None)
    target_capacity_mw: float | None = Field(default=None)
    operating_llc_name: str | None = Field(default=None)


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
    grantor_landowner_name: str | None = Field(
        default=None,
        description="Primary Landowner / Grantor / Property Owner counterparty name stated in the agreement preamble or recitals (CR-2)",
    )
    grantee_entity_name: str | None = Field(
        default=None,
        description="Primary Developer SPV / Grantee / Lessee counterparty name stated in the agreement preamble or recitals (CR-2)",
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
    special_conditions: list[SpecialConditionRow] = Field(
        default_factory=list,
        description="Normalized physical/operational landowner special conditions and site constraints (1 row per distinct constraint, linked to the most specific leaf node_id)",
    )


class ExhibitIndexEntry(BaseModel):
    """Pass 1: Exhibit summary entry in the structural index."""

    model_config = ConfigDict(extra="ignore")

    exhibit_id: str = Field(description="Identifier (e.g. 'Exhibit A', 'Exhibit B', 'Exhibit C')")
    exhibit_title: str = Field(description="Title or subject of the exhibit (e.g. 'Legal Description', 'Payment Terms')")
    page_start: int = Field(default=1, ge=1, description="First physical PDF page of this exhibit")
    page_end: int = Field(default=1, ge=1, description="Final physical PDF page of this exhibit")
    exhibit_modality: ExhibitModality = Field(
        default=ExhibitModality.PROSE_CLAUSES,
        description="Modality classification of the exhibit",
    )

    @model_validator(mode="after")
    def clamp_pages(self) -> Self:
        if self.page_start < 1:
            self.page_start = 1
        if self.page_end < self.page_start:
            self.page_end = self.page_start
        return self


class SignerEntry(BaseModel):
    """Pass 1: Signer or execution block entry."""

    model_config = ConfigDict(extra="ignore")

    party_name: str = Field(description="Counterparty name for this signature block")
    signer_name: str | None = Field(default=None, description="Printed or typed individual signer name")
    signer_title: str | None = Field(default=None, description="Official title of the signer")
    page_number: int = Field(default=1, ge=1, description="Physical page number containing this signature block")


class ContractStructureIndex(BaseModel):
    """Pass 1: High-level structural and zone boundary index returned by gemini-3.8-flash."""

    model_config = ConfigDict(extra="ignore")

    document_title: str = Field(description="Formal title of the agreement")
    grantor_landowner_name: str | None = Field(default=None, description="Canonical grantor counterparty name")
    grantee_entity_name: str | None = Field(default=None, description="Canonical grantee counterparty name")
    effective_date: str | None = Field(default=None, description="Execution or effective date string")
    has_recitals: bool = Field(default=False, description="True if agreement contains WHEREAS recitals")
    body_start_page: int = Field(default=1, ge=1, description="First physical page of the agreement body")
    body_end_page: int = Field(default=1, ge=1, description="Final physical page of the agreement body before signatures/exhibits")
    signature_start_page: int | None = Field(default=None, description="Physical page where signature blocks begin")
    signature_end_page: int | None = Field(default=None, description="Physical page where signature blocks end")
    signers: list[SignerEntry] = Field(default_factory=list, description="Extracted signer blocks")
    exhibits: list[ExhibitIndexEntry] = Field(default_factory=list, description="Attached exhibits and their page spans")

    @model_validator(mode="after")
    def clamp_boundaries(self) -> Self:
        if self.body_start_page < 1:
            self.body_start_page = 1
        if self.body_end_page < self.body_start_page:
            self.body_end_page = self.body_start_page
        if self.signature_start_page is not None and self.signature_start_page < 1:
            self.signature_start_page = 1
        if self.signature_end_page is not None and self.signature_start_page is not None:
            if self.signature_end_page < self.signature_start_page:
                self.signature_end_page = self.signature_start_page
        return self


class ExtractedClauseItem(BaseModel):
    """Lean clause extraction schema to keep LLM token generation payload small."""

    model_config = ConfigDict(extra="ignore")

    node_id: str = Field(description="Deterministic node identifier (e.g. 'PREAMBLE.1', 'BODY.1', 'BODY.2.i', 'EXHIBIT_A.1')")
    parent_node_id: str | None = Field(default=None, description="Parent node_id or null")
    depth: int = Field(default=1, ge=1, description="1-indexed hierarchical depth")
    clause_label: str | None = Field(default=None, description="Literal marker e.g. '1', '(a)', '(i)'")
    clause_title: str | None = Field(default=None, description="Title or heading, if present")
    numbering_scheme: NumberingScheme = Field(default=NumberingScheme.UNNUMBERED, description="Numbering scheme")
    document_zone: DocumentZone = Field(default=DocumentZone.BODY, description="Structural zone")
    page_start: int = Field(default=1, ge=1, description="Starting 1-indexed page")
    page_end: int = Field(default=1, ge=1, description="Ending 1-indexed page")
    verbatim_text: str = Field(description="Exact atomic text")
    hitl_status: HITLStatus = Field(default=HITLStatus.VERIFIED_AUTO, description="HITL status")
    hitl_flag_reasons: str = Field(default="NONE", description="Flag reasons or NONE")

    def to_clause_row(self) -> ClauseRow:
        now_iso = utc_now_iso()
        label = (
            self.clause_label.strip()
            if (self.clause_label is not None and self.clause_label.strip())
            else (self.clause_title or self.node_id)
        )
        return ClauseRow(
            node_id=self.node_id,
            canonical_path=self.node_id,
            parent_node_id=self.parent_node_id,
            depth=self.depth,
            clause_label=label,
            clause_title=self.clause_title or label,
            numbering_scheme=self.numbering_scheme,
            document_zone=self.document_zone,
            page_start=self.page_start,
            page_end=self.page_end,
            verbatim_text=self.verbatim_text,
            preamble_text=self.verbatim_text,
            postamble_text=None,
            reconstructed_context_text=self.verbatim_text,
            hitl_status=self.hitl_status,
            hitl_flag_reasons=self.hitl_flag_reasons,
            created_at=now_iso,
            updated_at=now_iso,
        )


class ExtractedDefinedTermItem(BaseModel):
    """Lean defined term extraction schema."""

    model_config = ConfigDict(extra="ignore")

    term_name: str = Field(description="Defined term name in canonical casing")
    defined_in_node_id: str = Field(description="Foreign key to clauses.node_id where this term was defined")
    page_number: int = Field(default=1, ge=1, description="Physical page number where the definition appears")
    definition_type: DefinitionType = Field(default=DefinitionType.INLINE_PARENTHETICAL, description="Classification of the definition site")
    verbatim_definition: str = Field(description="Exact verbatim definition text")

    def to_defined_term_row(self, document_id: str = "doc_placeholder") -> DefinedTermRow:
        now_iso = utc_now_iso()
        return DefinedTermRow(
            document_id=document_id,
            term_name=self.term_name,
            defined_in_node_id=self.defined_in_node_id,
            page_number=self.page_number,
            definition_type=self.definition_type,
            verbatim_definition=self.verbatim_definition,
            referenced_in_nodes="NONE",
            created_at=now_iso,
            updated_at=now_iso,
        )


class ExtractedSpecialConditionItem(BaseModel):
    """Lean special condition extraction schema."""

    model_config = ConfigDict(extra="ignore")

    node_id: str = Field(description="Enclosing leaf clause node_id")
    constraint_category: ConstraintCategory = Field(description="Classification of site constraint")
    target_asset_or_area: str = Field(description="Physical site feature, structure, or area")
    quantitative_metric: str | None = Field(default=None, description="Quantified buffer, distance, or metric")
    temporal_restriction: str | None = Field(default=None, description="Time window, curfew, or blackout period")
    penalty_or_consequence: str | None = Field(default=None, description="Financial penalty or consequence")
    actionable_obligation_summary: str = Field(description="Concise operational obligation")
    verbatim_excerpt: str = Field(description="Exact verbatim excerpt from contract")
    page_number: int | None = Field(default=None, description="Physical page number where constraint appears")

    def to_special_condition_row(self, document_id: str = "doc_placeholder") -> SpecialConditionRow:
        now_iso = utc_now_iso()
        return SpecialConditionRow(
            condition_id=f"sc_extracted_{self.node_id}",
            document_id=document_id,
            node_id=self.node_id,
            canonical_path=self.node_id,
            constraint_category=self.constraint_category,
            target_asset_or_area=self.target_asset_or_area,
            quantitative_metric=self.quantitative_metric,
            temporal_restriction=self.temporal_restriction,
            penalty_or_consequence=self.penalty_or_consequence,
            actionable_obligation_summary=self.actionable_obligation_summary,
            verbatim_excerpt=self.verbatim_excerpt,
            page_number=self.page_number or 1,
            created_at=now_iso,
            updated_at=now_iso,
        )


class BodyPassExtraction(BaseModel):
    """Pass 2: Extracted body sections, defined terms, and body special conditions."""

    model_config = ConfigDict(extra="ignore")

    clauses: list[ExtractedClauseItem | ClauseRow] = Field(default_factory=list, description="Body clauses (Sections 1..N and subsections)")
    defined_terms: list[ExtractedDefinedTermItem | DefinedTermRow] = Field(default_factory=list, description="Defined terms defined within the body")
    special_conditions: list[ExtractedSpecialConditionItem | SpecialConditionRow] = Field(default_factory=list, description="Site constraints located in the body")


class ExhibitsPassExtraction(BaseModel):
    """Pass 3: Extracted exhibit clauses, exhibit defined terms, and exhibit special conditions."""

    model_config = ConfigDict(extra="ignore")

    clauses: list[ExtractedClauseItem | ClauseRow] = Field(default_factory=list, description="Clauses extracted from exhibits and schedules")
    defined_terms: list[ExtractedDefinedTermItem | DefinedTermRow] = Field(default_factory=list, description="Defined terms defined in exhibits")
    special_conditions: list[ExtractedSpecialConditionItem | SpecialConditionRow] = Field(default_factory=list, description="Site constraints located in exhibits")
    exhibits_catalog: list[ExhibitCatalogRow] = Field(default_factory=list, description="Detailed exhibit catalog entries")


ZONE_ORDER_INDEX: dict[str, int] = {
    "PREAMBLE": 1,
    "RECITALS": 2,
    "BODY": 3,
    "SIGNATURES": 4,
    "EXHIBIT_OR_SCHEDULE": 5,
    "AMENDMENT": 6,
}


def sort_clauses_in_document_order(clauses: list[ClauseRow]) -> list[ClauseRow]:
    """Sort clauses into hierarchical document reading order via pre-order DFS tree traversal.

    Ensures top-level sections (e.g. Section 1) are immediately followed by their
    subsections (e.g. 1.1) and nested paragraphs (e.g. (a)) in sequential reading order,
    rather than grouping by depth or appearing out of sequence.
    """
    if not clauses:
        return []

    # Map original sequence to preserve extraction order among siblings
    orig_pos: dict[str, int] = {c.node_id: idx for idx, c in enumerate(clauses)}
    clauses_by_id: dict[str, ClauseRow] = {c.node_id: c for c in clauses}
    clauses_by_path: dict[str, ClauseRow] = {c.canonical_path: c for c in clauses}

    # Resolve parent_node_id if missing but implied by canonical_path
    for c in clauses:
        if not c.parent_node_id and "." in (c.canonical_path or ""):
            parent_path = c.canonical_path.rsplit(".", 1)[0]
            if parent_path in clauses_by_path and clauses_by_path[parent_path].node_id != c.node_id:
                c.parent_node_id = clauses_by_path[parent_path].node_id

    children_map: dict[str, list[ClauseRow]] = {}
    roots: list[ClauseRow] = []

    for c in clauses:
        parent_id = c.parent_node_id
        if parent_id and parent_id in clauses_by_id and parent_id != c.node_id:
            children_map.setdefault(parent_id, []).append(c)
        else:
            roots.append(c)

    def _sort_key(c: ClauseRow) -> tuple[int, int, int, int]:
        zone_str = (
            c.document_zone.value
            if hasattr(c.document_zone, "value")
            else str(c.document_zone)
        )
        zone_idx = ZONE_ORDER_INDEX.get(zone_str, 99)
        return (
            zone_idx,
            c.page_start or 1,
            c.sibling_order or 0,
            orig_pos.get(c.node_id, 0),
        )

    def _child_sort_key(c: ClauseRow) -> tuple[int, int, int]:
        return (
            c.page_start or 1,
            c.sibling_order or 0,
            orig_pos.get(c.node_id, 0),
        )

    roots.sort(key=_sort_key)
    for ch_list in children_map.values():
        ch_list.sort(key=_child_sort_key)

    ordered: list[ClauseRow] = []
    visited: set[str] = set()

    def _traverse(node: ClauseRow) -> None:
        if node.node_id in visited:
            return
        visited.add(node.node_id)
        ordered.append(node)
        for child in children_map.get(node.node_id, []):
            _traverse(child)

    for r in roots:
        _traverse(r)

    # Any unvisited nodes (e.g. cyclic references or disconnected fragments) appended safely
    for c in clauses:
        if c.node_id not in visited:
            visited.add(c.node_id)
            ordered.append(c)

    return ordered


class ParsedContractBundle(BaseModel):
    """Complete hydrated contract bundle returned by the pipeline and API."""

    document: DocumentRegistryRow
    clauses: list[ClauseRow]
    defined_terms: list[DefinedTermRow]
    exhibits_catalog: list[ExhibitCatalogRow]
    special_conditions: list[SpecialConditionRow] = Field(default_factory=list)

    @model_validator(mode="after")
    def ensure_document_order(self) -> Self:
        self.clauses = sort_clauses_in_document_order(self.clauses)
        return self


class ConstructionTrade(StrEnum):
    """Field crew construction trade routing for Do-Not-Disturb (DND) checklist items (CR-3 / R6)."""

    ACCESS_FENCING_GATES = "ACCESS_FENCING_GATES"
    CLEARING_VEGETATION = "CLEARING_VEGETATION"
    CIVIL_GRADING_SOIL = "CIVIL_GRADING_SOIL"
    BLASTING_TRENCHING_FOUNDATION = "BLASTING_TRENCHING_FOUNDATION"
    CRANE_TRANSPORT_ERECTION = "CRANE_TRANSPORT_ERECTION"
    GENERAL_SITE_OPERATIONS = "GENERAL_SITE_OPERATIONS"


class DNDSeverityLevel(StrEnum):
    """Field hazard severity classification for DND checklist items (CR-3 / R6)."""

    RED_ZONE_NO_GO = "RED_ZONE_NO_GO"
    SEASONAL_BLACKOUT = "SEASONAL_BLACKOUT"
    MANDATORY_PROTOCOL = "MANDATORY_PROTOCOL"


class DNDDispatchClearance(StrEnum):
    """Item-level safety interlock state for unverified vs. human-approved constraints (CR-3 Option 2A)."""

    CLEARED_FOR_DISPATCH = "CLEARED_FOR_DISPATCH"
    HOLD_VERIFY_WITH_LAND_AGENT = "HOLD_VERIFY_WITH_LAND_AGENT"


class DNDDispatchReadiness(StrEnum):
    """Contract-level field dispatch readiness gate (CR-3 Option 2A)."""

    READY_FOR_DISPATCH = "READY_FOR_DISPATCH"
    HOLD_PENDING_HITL = "HOLD_PENDING_HITL"
    NO_CONSTRAINTS_IDENTIFIED = "NO_CONSTRAINTS_IDENTIFIED"


class DNDChecklistItem(BaseModel):
    """Dynamically synthesized Field Crew Do-Not-Disturb (DND) checklist item for a contract (20 fields, CR-3)."""

    model_config = ConfigDict(extra="ignore")

    condition_id: str = Field(description="Source SpecialConditionRow.condition_id")
    document_id: str = Field(description="Parent contract document_id")
    project_id: str = Field(default="prj_cedar_lantern_wind", description="Parent ProjectRow.project_id")
    landowner_id: str = Field(default="lnd_unassigned", description="Parent LandownerRow.landowner_id")
    node_id: str = Field(description="Source ClauseRow.node_id for 1-click HITL approval")
    canonical_path: str = Field(default="", description="Dot-delimited clause path")
    constraint_category: ConstraintCategory = Field(description="Source legal/operational constraint category")
    construction_trade: ConstructionTrade = Field(description="Deterministically mapped field crew trade")
    severity_level: DNDSeverityLevel = Field(description="Deterministically mapped hazard severity")
    dispatch_clearance: DNDDispatchClearance = Field(
        description="CLEARED_FOR_DISPATCH if hitl_status == APPROVED_BY_HUMAN, else HOLD_VERIFY_WITH_LAND_AGENT"
    )
    field_directive_title: str = Field(description="Concise uppercase field callout")
    target_asset_or_area: str = Field(description="Physical structure, gate, well, or parcel zone")
    quantitative_metric: str | None = Field(default=None, description="Hard distance, setback, weight limit, or dimension")
    temporal_restriction: str | None = Field(default=None, description="Blackout dates, hours, or required notice window")
    penalty_or_consequence: str | None = Field(default=None, description="Financial penalty or liquidated damages exposure")
    actionable_obligation_summary: str = Field(description="Plain-English crew instruction")
    verbatim_excerpt: str = Field(description="Exact contract text for verification")
    page_number: int = Field(default=1, ge=1, description="1-indexed PDF page number for instant pdf.js jump")
    hitl_status: HITLStatus = Field(default=HITLStatus.FLAGGED_FOR_REVIEW, description="Underlying verification status")
    reviewed_by: str | None = Field(default=None, description="Reviewer who approved the constraint")


class DNDChecklistSignoffRow(BaseModel):
    """Table 8: `dnd_checklist_signoffs` Pre-Job Tailgate Briefing Sign-Off Audit Table (12 columns, CR-3 / R6)."""

    model_config = ConfigDict(extra="ignore")

    signoff_id: str = Field(description="Deterministic or timestamped ID (sig_<doc_short>_<hash8>)")
    document_id: str = Field(description="Foreign key to documents.document_id")
    project_id: str = Field(default="prj_cedar_lantern_wind", description="Foreign key to projects.project_id")
    landowner_id: str = Field(default="lnd_unassigned", description="Foreign key to landowners.landowner_id")
    subcontractor_company: str = Field(description="Subcontractor firm name (e.g. Apex Civil & Grading LLC)")
    foreman_name: str = Field(description="Field superintendent or crew foreman signing off")
    construction_trade: ConstructionTrade = Field(
        default=ConstructionTrade.GENERAL_SITE_OPERATIONS,
        description="Primary trade scope briefed",
    )
    acknowledged_condition_ids: str = Field(
        description="Pipe-delimited condition_id values acknowledged in the briefing"
    )
    acknowledged_count: int = Field(ge=1, description="Total number of DND items acknowledged")
    dispatch_readiness_at_signoff: DNDDispatchReadiness = Field(
        default=DNDDispatchReadiness.HOLD_PENDING_HITL,
        description="Snapshot of contract readiness at sign-off",
    )
    briefing_notes: str | None = Field(default=None, description="Optional tailgate briefing notes or field observations")
    signed_at: str = Field(default_factory=utc_now_iso, description="ISO-8601 UTC timestamp of sign-off")


class CreateDNDSignoffRequest(BaseModel):
    """Payload for POST /api/v1/documents/{document_id}/dnd-checklist:signoff."""

    model_config = ConfigDict(extra="ignore")

    subcontractor_company: str = Field(description="Non-empty subcontractor firm name")
    foreman_name: str = Field(description="Non-empty foreman or superintendent name")
    construction_trade: ConstructionTrade = Field(
        default=ConstructionTrade.GENERAL_SITE_OPERATIONS,
        description="Primary trade briefed",
    )
    acknowledged_condition_ids: list[str] | str = Field(
        default_factory=list,
        description="List of condition_id strings or pipe-delimited condition_id string",
    )
    briefing_notes: str | None = Field(default=None, description="Optional field briefing notes")


class DNDChecklistBundle(BaseModel):
    """Single-contract Field Crew Do-Not-Disturb Checklist response bundle (CR-3)."""

    model_config = ConfigDict(extra="ignore")

    document_id: str
    filename: str
    project_id: str
    project_name: str
    erp_project_code: str
    energy_technology: EnergyTechnology
    landowner_id: str
    landowner_name: str
    parcel_summary: str | None = None
    dispatch_readiness: DNDDispatchReadiness
    total_items: int = 0
    filtered_items_count: int = 0
    red_zone_count: int = 0
    seasonal_blackout_count: int = 0
    mandatory_protocol_count: int = 0
    cleared_count: int = 0
    hold_count: int = 0
    trade_breakdown: dict[str, int] = Field(default_factory=dict)
    items: list[DNDChecklistItem] = Field(default_factory=list)
    signoffs: list[DNDChecklistSignoffRow] = Field(default_factory=list)


_RED_ZONE_CATEGORIES: frozenset[ConstraintCategory] = frozenset(
    {
        ConstraintCategory.TREE_VEGETATION_PROTECTION,
        ConstraintCategory.CROP_OR_TIMBER_COMPENSATION,
        ConstraintCategory.STRUCTURE_BARN_WELL_SETBACK,
        ConstraintCategory.SETBACK_OR_BUFFER,
        ConstraintCategory.BLASTING_OR_EXCAVATION,
        ConstraintCategory.FINANCIAL_PENALTY_LIQUIDATED_DAMAGES,
    }
)

_SEASONAL_CATEGORIES: frozenset[ConstraintCategory] = frozenset(
    {
        ConstraintCategory.TIMING_NOISE_HUNTING_BLACKOUT,
        ConstraintCategory.CONSTRUCTION_OR_BLACKOUT_WINDOW,
    }
)


def classify_dnd_condition(sc: SpecialConditionRow) -> tuple[ConstructionTrade, DNDSeverityLevel, str]:
    """Deterministically map a SpecialConditionRow to ConstructionTrade, DNDSeverityLevel, and field_directive_title."""
    combined_text = f"{sc.target_asset_or_area} {sc.actionable_obligation_summary} {sc.verbatim_excerpt}".lower()
    cat = sc.constraint_category

    # 1. Trade Routing
    if cat in {
        ConstraintCategory.TREE_VEGETATION_PROTECTION,
        ConstraintCategory.CROP_OR_TIMBER_COMPENSATION,
    }:
        trade = ConstructionTrade.CLEARING_VEGETATION
    elif cat in {
        ConstraintCategory.ACCESS_ROAD_GATE_PROTOCOL,
        ConstraintCategory.GATES_FENCING_OR_LIVESTOCK,
        ConstraintCategory.ACCESS_ROAD_OR_PARCEL_RESTRICTION,
        ConstraintCategory.LIVESTOCK_AGRICULTURE,
    }:
        trade = ConstructionTrade.ACCESS_FENCING_GATES
    elif cat == ConstraintCategory.DRAINAGE_OR_SOIL_RESTORATION:
        trade = ConstructionTrade.CIVIL_GRADING_SOIL
    elif cat == ConstraintCategory.NOISE_OR_SHADOW_FLICKER:
        trade = ConstructionTrade.CRANE_TRANSPORT_ERECTION
    elif cat in {
        ConstraintCategory.BLASTING_OR_EXCAVATION,
        ConstraintCategory.STRUCTURE_BARN_WELL_SETBACK,
    }:
        if re.search(r"\b(crane|turbine|rig|heavy haul|overhead|laydown)\b", combined_text):
            trade = ConstructionTrade.CRANE_TRANSPORT_ERECTION
        else:
            trade = ConstructionTrade.BLASTING_TRENCHING_FOUNDATION
    else:
        if re.search(r"\b(tree|trees|oak|pecan|timber|grove|orchard|brush|clearing)\b", combined_text):
            trade = ConstructionTrade.CLEARING_VEGETATION
        elif re.search(r"\b(drainage|tile|tiles|topsoil|erosion|compaction|re-grade|grading)\b", combined_text):
            trade = ConstructionTrade.CIVIL_GRADING_SOIL
        elif re.search(r"\b(gate|gates|fence|fencing|cattle|livestock|pasture|culvert|haul road|speed limit)\b", combined_text):
            trade = ConstructionTrade.ACCESS_FENCING_GATES
        elif re.search(r"\b(crane|turbine|rig|heavy haul|overhead|laydown)\b", combined_text):
            trade = ConstructionTrade.CRANE_TRANSPORT_ERECTION
        elif re.search(
            r"\b(blast|blasting|trench|trenching|excavat\w*|well|wells|barn|shed|residence|homestead|septic|foundation|setback|buffer)\b",
            combined_text,
        ):
            trade = ConstructionTrade.BLASTING_TRENCHING_FOUNDATION
        else:
            trade = ConstructionTrade.GENERAL_SITE_OPERATIONS

    # 2. Severity Classification
    metric_text = (sc.quantitative_metric or "").strip()
    temporal_text = (sc.temporal_restriction or "").strip()
    penalty_text = (sc.penalty_or_consequence or "").strip()

    has_distance_metric = bool(
        metric_text and re.search(r"\b(feet|foot|ft|meter|meters|yard|yards|acre|acres)\b", metric_text.lower())
    )
    has_penalty = bool(penalty_text)
    has_temporal = bool(temporal_text)

    if (cat in _SEASONAL_CATEGORIES and not has_distance_metric) or (
        has_temporal and not has_penalty and not has_distance_metric and cat not in _RED_ZONE_CATEGORIES
    ):
        severity = DNDSeverityLevel.SEASONAL_BLACKOUT
    elif (
        has_penalty
        or cat in _RED_ZONE_CATEGORIES
        or (
            has_distance_metric
            and bool(re.search(r"(setback|buffer|do not disturb|no-build|no entry|prohibited|within)", combined_text))
        )
        or bool(
            re.search(
                r"(do not disturb|shall not cut|shall not clear|no-build|no entry|blasting prohibited)",
                combined_text,
            )
        )
    ):
        severity = DNDSeverityLevel.RED_ZONE_NO_GO
    else:
        severity = DNDSeverityLevel.MANDATORY_PROTOCOL

    # 3. Field Directive Title Synthesis
    if severity == DNDSeverityLevel.RED_ZONE_NO_GO:
        prefix = "DO NOT DISTURB"
        qualifier = metric_text or temporal_text
    elif severity == DNDSeverityLevel.SEASONAL_BLACKOUT:
        prefix = "SEASONAL BLACKOUT"
        qualifier = temporal_text or metric_text
    else:
        prefix = "MANDATORY PROTOCOL"
        qualifier = metric_text or temporal_text

    target = (sc.target_asset_or_area or "SITE CONSTRAINT").strip().upper()
    if qualifier:
        directive_title = f"{prefix}: {target} ({qualifier.upper()})"
    else:
        directive_title = f"{prefix}: {target}"

    return trade, severity, directive_title


def build_dnd_checklist_item(sc: SpecialConditionRow) -> DNDChecklistItem:
    """Synthesize a DNDChecklistItem dynamically from a SpecialConditionRow."""
    trade, severity, directive_title = classify_dnd_condition(sc)
    clearance = (
        DNDDispatchClearance.CLEARED_FOR_DISPATCH
        if sc.hitl_status == HITLStatus.APPROVED_BY_HUMAN
        else DNDDispatchClearance.HOLD_VERIFY_WITH_LAND_AGENT
    )
    return DNDChecklistItem(
        condition_id=sc.condition_id,
        document_id=sc.document_id,
        project_id=sc.project_id or "prj_cedar_lantern_wind",
        landowner_id=sc.landowner_id or "lnd_unassigned",
        node_id=sc.node_id,
        canonical_path=sc.canonical_path,
        constraint_category=sc.constraint_category,
        construction_trade=trade,
        severity_level=severity,
        dispatch_clearance=clearance,
        field_directive_title=directive_title,
        target_asset_or_area=sc.target_asset_or_area,
        quantitative_metric=sc.quantitative_metric,
        temporal_restriction=sc.temporal_restriction,
        penalty_or_consequence=sc.penalty_or_consequence,
        actionable_obligation_summary=sc.actionable_obligation_summary,
        verbatim_excerpt=sc.verbatim_excerpt,
        page_number=sc.page_number,
        hitl_status=sc.hitl_status,
        reviewed_by=sc.reviewed_by,
    )


CLAUSE_CSV_COLUMNS: list[str] = list(ClauseRow.model_fields.keys())
DEFINED_TERM_CSV_COLUMNS: list[str] = list(DefinedTermRow.model_fields.keys())
EXHIBIT_CATALOG_CSV_COLUMNS: list[str] = list(ExhibitCatalogRow.model_fields.keys())
SPECIAL_CONDITION_CSV_COLUMNS: list[str] = list(SpecialConditionRow.model_fields.keys())
DOCUMENT_REGISTRY_COLUMNS: list[str] = list(DocumentRegistryRow.model_fields.keys())
PROJECT_CSV_COLUMNS: list[str] = list(ProjectRow.model_fields.keys())
LANDOWNER_CSV_COLUMNS: list[str] = list(LandownerRow.model_fields.keys())
DND_SIGNOFF_CSV_COLUMNS: list[str] = list(DNDChecklistSignoffRow.model_fields.keys())


class AgentChatRequest(BaseModel):
    """Single-turn natural-language prompt sent to POST /api/v1/agent/chat (CR-4)."""

    model_config = ConfigDict(extra="ignore")

    question: str


class AgentDocumentLink(BaseModel):
    """Direct clickable PDF link for a contract referenced in a BigQuery Data Agent response."""

    model_config = ConfigDict(extra="ignore")

    document_id: str
    filename: str
    pdf_url: str


class AgentChatResponse(BaseModel):
    """Normalized single-turn response from the BigQuery Conversational Analytics Data Agent (CR-4)."""

    model_config = ConfigDict(extra="ignore")

    agent_urn: str
    data_agent_resource: str
    question: str
    answer: str
    generated_sql: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, object]] = Field(default_factory=list)
    thoughts: list[str] = Field(default_factory=list)
    followup_questions: list[str] = Field(default_factory=list)
    document_links: list[AgentDocumentLink] = Field(default_factory=list)


_DOC_ID_TOKEN_RE = re.compile(r"\b(doc_[a-zA-Z0-9_]+)\b")


def parse_data_agent_events(
    events: list[dict[str, object]],
    *,
    question: str,
    agent_urn: str,
    data_agent_resource: str,
    doc_filename_lookup: dict[str, str] | None = None,
) -> AgentChatResponse:
    """Normalize raw `geminidataanalytics` v1beta `:chat` event stream into `AgentChatResponse`."""
    answer_parts: list[str] = []
    thoughts: list[str] = []
    followup_questions: list[str] = []
    generated_sql: str | None = None
    columns: list[str] = []
    rows: list[dict[str, object]] = []

    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        sys_msg_raw = ev.get("systemMessage")
        sys_msg = sys_msg_raw if isinstance(sys_msg_raw, dict) else ev

        text_block = sys_msg.get("text")
        if isinstance(text_block, dict):
            raw_parts = text_block.get("parts")
            parts = raw_parts if isinstance(raw_parts, list) else []
            text_type = str(text_block.get("textType") or "")
            joined = "".join(str(p) for p in parts if p is not None).strip()
            if joined:
                if text_type == "THOUGHT":
                    thoughts.append(joined)
                elif text_type == "FOLLOWUP_QUESTIONS":
                    for p in parts:
                        q_str = str(p).strip()
                        if q_str and q_str not in followup_questions:
                            followup_questions.append(q_str)
                else:
                    answer_parts.append(joined)

        data_block = sys_msg.get("data")
        if isinstance(data_block, dict):
            sql_candidate = data_block.get("generatedSql")
            if isinstance(sql_candidate, str) and sql_candidate.strip():
                generated_sql = sql_candidate.strip()

            result_block = data_block.get("result")
            if isinstance(result_block, dict):
                schema_block = result_block.get("schema")
                if isinstance(schema_block, dict):
                    fields_raw = schema_block.get("fields")
                    if isinstance(fields_raw, list):
                        cols = [
                            str(f.get("name"))
                            for f in fields_raw
                            if isinstance(f, dict) and f.get("name")
                        ]
                        if cols:
                            columns = cols
                data_rows = result_block.get("data")
                if isinstance(data_rows, list):
                    rows = [
                        dict(r)
                        for r in data_rows[:100]
                        if isinstance(r, dict)
                    ]
                    if not columns and rows:
                        columns = list(rows[0].keys())

    answer = "\n\n".join(answer_parts).strip()
    if not answer:
        if rows:
            answer = f"Returned {len(rows)} row(s) from BigQuery."
        elif thoughts:
            answer = thoughts[-1]
        else:
            answer = "No response returned from BigQuery Data Agent."

    lookup: dict[str, str] = {
        str(k): str(v)
        for k, v in (doc_filename_lookup or {}).items()
        if k and v
    }
    for row in rows:
        row_doc_id = row.get("document_id")
        row_filename = row.get("filename")
        if isinstance(row_doc_id, str) and row_doc_id.startswith("doc_") and isinstance(row_filename, str) and row_filename.strip():
            lookup[row_doc_id] = row_filename.strip()

    reverse_filename_lookup: dict[str, str] = {
        fn.strip().lower(): doc_id
        for doc_id, fn in lookup.items()
        if fn and fn.strip()
    }

    seen_docs: dict[str, str] = {}

    for row in rows:
        row_doc_id = row.get("document_id")
        row_filename_val = row.get("filename")
        row_filename_str = (
            row_filename_val.strip()
            if isinstance(row_filename_val, str) and row_filename_val.strip()
            else None
        )
        for val in row.values():
            if not isinstance(val, str):
                continue
            for match in _DOC_ID_TOKEN_RE.findall(val):
                if match not in seen_docs:
                    seen_docs[match] = (
                        (row_filename_str if match == row_doc_id else None)
                        or lookup.get(match)
                        or f"{match}.pdf"
                    )
            val_clean = val.strip().lower()
            if val_clean in reverse_filename_lookup:
                matched_id = reverse_filename_lookup[val_clean]
                if matched_id not in seen_docs:
                    seen_docs[matched_id] = lookup.get(matched_id, val.strip())

    for match in _DOC_ID_TOKEN_RE.findall(answer):
        if match not in seen_docs:
            seen_docs[match] = lookup.get(match, f"{match}.pdf")

    answer_lower = answer.lower()
    for fn_lower, matched_id in reverse_filename_lookup.items():
        if fn_lower and matched_id not in seen_docs:
            pattern = rf"(?<![a-z0-9_.-]){re.escape(fn_lower)}(?![a-z0-9_.-])"
            if re.search(pattern, answer_lower):
                seen_docs[matched_id] = lookup.get(matched_id, f"{matched_id}.pdf")

    document_links = [
        AgentDocumentLink(
            document_id=doc_id,
            filename=filename,
            pdf_url=f"/api/v1/documents/{doc_id}/pdf",
        )
        for doc_id, filename in seen_docs.items()
    ]

    return AgentChatResponse(
        agent_urn=agent_urn,
        data_agent_resource=data_agent_resource,
        question=question.strip(),
        answer=answer,
        generated_sql=generated_sql,
        columns=columns,
        rows=rows,
        thoughts=thoughts,
        followup_questions=followup_questions,
        document_links=document_links,
    )
