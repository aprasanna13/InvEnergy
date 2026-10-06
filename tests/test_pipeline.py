"""End-to-end schema, post-processing, storage, and FastAPI pipeline verification tests."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from contract_parser.app import create_app, ingest_contract
from contract_parser.config import DEFAULT_BQ_DATA_AGENT_URN, PipelineConfig
from contract_parser.gemini_parser import (
    _make_landowner_id,
    normalize_and_enrich_extraction,
)
from contract_parser.schemas import (
    CLAUSE_CSV_COLUMNS,
    DEFINED_TERM_CSV_COLUMNS,
    DND_SIGNOFF_CSV_COLUMNS,
    DOCUMENT_REGISTRY_COLUMNS,
    EXHIBIT_CATALOG_CSV_COLUMNS,
    LANDOWNER_CSV_COLUMNS,
    PROJECT_CSV_COLUMNS,
    SPECIAL_CONDITION_CSV_COLUMNS,
    AgentChatRequest,
    AgentChatResponse,
    AgentDocumentLink,
    ClauseReviewRequest,
    ClauseRow,
    ConstraintCategory,
    ConstructionTrade,
    DefinedTermRow,
    DefinitionType,
    DNDDispatchClearance,
    DNDDispatchReadiness,
    DNDSeverityLevel,
    DocumentZone,
    EnergyTechnology,
    ExhibitCatalogRow,
    ExhibitModality,
    FlagCode,
    GeminiContractExtraction,
    HITLStatus,
    IngestionStatus,
    NumberingScheme,
    SpecialConditionRow,
    build_dnd_checklist_item,
    classify_dnd_condition,
    parse_data_agent_events,
)
from contract_parser.storage import (
    BQ_CLAUSES_SCHEMA,
    BQ_DEFINED_TERMS_SCHEMA,
    BQ_DND_SIGNOFFS_SCHEMA,
    BQ_DOCUMENTS_SCHEMA,
    BQ_EXHIBITS_CATALOG_SCHEMA,
    BQ_LANDOWNERS_SCHEMA,
    BQ_PROJECTS_SCHEMA,
    BQ_SPECIAL_CONDITIONS_SCHEMA,
    ContractStorageService,
)

SAMPLE_PDF_PATH = Path(
    "/usr/local/google/home/prasannaankem/Downloads/Synthetic_Accommodation_Agreement.pdf"
)


def build_synthetic_accommodation_fixture() -> GeminiContractExtraction:
    """Build the complete benchmark extraction fixture for Synthetic_Accommodation_Agreement.pdf."""
    clauses = [
        ClauseRow(
            node_id="PREAMBLE.1",
            parent_node_id=None,
            sibling_order=1,
            document_zone=DocumentZone.PREAMBLE,
            canonical_path="PREAMBLE.1",
            depth=1,
            clause_label="PREAMBLE",
            numbering_scheme=NumberingScheme.UNNUMBERED,
            clause_title="ACCOMMODATION AGREEMENT",
            is_inline_clause=False,
            verbatim_text='This ACCOMMODATION AGREEMENT (this "Agreement") is entered into as of October 14, 2024, by and between Cedar Lantern Wind, LLC ("Cedar Lantern") and Blue Meridian Transmission, LLC ("Blue Meridian") (each a "Party" and collectively, the "Parties").',
            reconstructed_context_text='This ACCOMMODATION AGREEMENT (this "Agreement") is entered into as of October 14, 2024, by and between Cedar Lantern Wind, LLC ("Cedar Lantern") and Blue Meridian Transmission, LLC ("Blue Meridian") (each a "Party" and collectively, the "Parties").',
            page_start=1,
            page_end=1,
        ),
        ClauseRow(
            node_id="RECITALS.1",
            parent_node_id=None,
            sibling_order=1,
            document_zone=DocumentZone.RECITALS,
            canonical_path="RECITALS.1",
            depth=1,
            clause_label="1",
            numbering_scheme=NumberingScheme.INTEGER,
            is_inline_clause=False,
            verbatim_text='1. Cedar Lantern has obtained certain rights to develop a wind energy project (the "Cedar Lantern Project") on that certain real property described in Exhibit A (the "Property") pursuant to the wind lease and easement agreements listed in Exhibit B (the "Cedar Lantern Easements").',
            reconstructed_context_text='1. Cedar Lantern has obtained certain rights to develop a wind energy project (the "Cedar Lantern Project") on that certain real property described in Exhibit A (the "Property") pursuant to the wind lease and easement agreements listed in Exhibit B (the "Cedar Lantern Easements").',
            page_start=1,
            page_end=1,
        ),
        ClauseRow(
            node_id="RECITALS.2",
            parent_node_id=None,
            sibling_order=2,
            document_zone=DocumentZone.RECITALS,
            canonical_path="RECITALS.2",
            depth=1,
            clause_label="2",
            numbering_scheme=NumberingScheme.INTEGER,
            is_inline_clause=False,
            verbatim_text='2. Blue Meridian holds transmission line easements across portions of the Property listed in Exhibit C (the "Blue Meridian Easements") for its transmission project (the "Blue Meridian Project") authorized by the Franklin Public Utilities Commission ("FPUC") Certificate of Convenience and Necessity ("CCN") to construct and operate the transmission facilities depicted in Exhibit D (the "Blue Meridian Facilities").',
            reconstructed_context_text='2. Blue Meridian holds transmission line easements across portions of the Property listed in Exhibit C (the "Blue Meridian Easements") for its transmission project (the "Blue Meridian Project") authorized by the Franklin Public Utilities Commission ("FPUC") Certificate of Convenience and Necessity ("CCN") to construct and operate the transmission facilities depicted in Exhibit D (the "Blue Meridian Facilities").',
            page_start=1,
            page_end=1,
        ),
        ClauseRow(
            node_id="BODY.1",
            parent_node_id=None,
            sibling_order=1,
            document_zone=DocumentZone.BODY,
            canonical_path="BODY.1",
            depth=1,
            clause_label="1",
            numbering_scheme=NumberingScheme.INTEGER,
            clause_title="Recitals",
            is_inline_clause=False,
            verbatim_text="1. Recitals. The foregoing recitals are true and correct and are incorporated herein by reference.",
            reconstructed_context_text="1. Recitals. The foregoing recitals are true and correct and are incorporated herein by reference.",
            page_start=2,
            page_end=2,
        ),
        ClauseRow(
            node_id="BODY.2",
            parent_node_id=None,
            sibling_order=2,
            document_zone=DocumentZone.BODY,
            canonical_path="BODY.2",
            depth=1,
            clause_label="2",
            numbering_scheme=NumberingScheme.INTEGER,
            clause_title="Term",
            is_inline_clause=False,
            preamble_text="This Agreement shall terminate upon the earlier of",
            verbatim_text="2. Term. This Agreement shall terminate upon the earlier of (i) expiration of the Blue Meridian Easements and removal of the Blue Meridian Facilities, or (ii) expiration of the Cedar Lantern Easements.",
            postamble_text=None,
            reconstructed_context_text="2. Term. This Agreement shall terminate upon the earlier of (i) expiration of the Blue Meridian Easements and removal of the Blue Meridian Facilities, or (ii) expiration of the Cedar Lantern Easements.",
            page_start=2,
            page_end=2,
        ),
        ClauseRow(
            node_id="BODY.2.i",
            parent_node_id="BODY.2",
            sibling_order=1,
            document_zone=DocumentZone.BODY,
            canonical_path="BODY.2.i",
            depth=2,
            clause_label="(i)",
            numbering_scheme=NumberingScheme.ROMAN_LOWER,
            is_inline_clause=True,
            verbatim_text="(i) expiration of the Blue Meridian Easements and removal of the Blue Meridian Facilities",
            reconstructed_context_text="",
            page_start=2,
            page_end=2,
        ),
        ClauseRow(
            node_id="BODY.2.ii",
            parent_node_id="BODY.2",
            sibling_order=2,
            document_zone=DocumentZone.BODY,
            canonical_path="BODY.2.ii",
            depth=2,
            clause_label="(ii)",
            numbering_scheme=NumberingScheme.ROMAN_LOWER,
            is_inline_clause=True,
            verbatim_text="(ii) expiration of the Cedar Lantern Easements.",
            reconstructed_context_text="",
            page_start=2,
            page_end=2,
        ),
        ClauseRow(
            node_id="BODY.3",
            parent_node_id=None,
            sibling_order=3,
            document_zone=DocumentZone.BODY,
            canonical_path="BODY.3",
            depth=1,
            clause_label="3",
            numbering_scheme=NumberingScheme.INTEGER,
            clause_title="Non-Interference",
            is_inline_clause=False,
            preamble_text="Each of the Parties covenants, acknowledges and agrees that",
            verbatim_text='3. Non-Interference. Each of the Parties covenants, acknowledges and agrees that (a) neither Party shall interfere with the other Party\'s construction, operation, and maintenance activities ("Operations") or installations ("Equipment"); (b) it shall, and shall cause its representatives to, exercise due care with respect to the Operations and Equipment of the other Party; and (c) all crossings shall comply with Exhibit D.',
            reconstructed_context_text='3. Non-Interference. Each of the Parties covenants, acknowledges and agrees that (a) neither Party shall interfere with the other Party\'s construction, operation, and maintenance activities ("Operations") or installations ("Equipment"); (b) it shall, and shall cause its representatives to, exercise due care with respect to the Operations and Equipment of the other Party; and (c) all crossings shall comply with Exhibit D.',
            page_start=2,
            page_end=2,
        ),
        ClauseRow(
            node_id="BODY.3.a",
            parent_node_id="BODY.3",
            sibling_order=1,
            document_zone=DocumentZone.BODY,
            canonical_path="BODY.3.a",
            depth=2,
            clause_label="(a)",
            numbering_scheme=NumberingScheme.ALPHA_LOWER,
            is_inline_clause=True,
            verbatim_text='(a) neither Party shall interfere with the other Party\'s construction, operation, and maintenance activities ("Operations") or installations ("Equipment");',
            reconstructed_context_text="",
            page_start=2,
            page_end=2,
        ),
        ClauseRow(
            node_id="BODY.3.b",
            parent_node_id="BODY.3",
            sibling_order=2,
            document_zone=DocumentZone.BODY,
            canonical_path="BODY.3.b",
            depth=2,
            clause_label="(b)",
            numbering_scheme=NumberingScheme.ALPHA_LOWER,
            is_inline_clause=True,
            verbatim_text="(b) it shall, and shall cause its representatives to, exercise due care with respect to the Operations and Equipment of the other Party;",
            reconstructed_context_text="",
            page_start=2,
            page_end=2,
        ),
        ClauseRow(
            node_id="BODY.4",
            parent_node_id=None,
            sibling_order=4,
            document_zone=DocumentZone.BODY,
            canonical_path="BODY.4",
            depth=1,
            clause_label="4",
            numbering_scheme=NumberingScheme.INTEGER,
            clause_title="Indemnification by Cedar Lantern",
            is_inline_clause=False,
            preamble_text='To the fullest extent permitted by law, Cedar Lantern shall indemnify, defend, and hold Blue Meridian harmless from and against any and all claims, losses, and liabilities ("Liabilities") arising out of:',
            verbatim_text='4. Indemnification by Cedar Lantern. To the fullest extent permitted by law, Cedar Lantern shall indemnify, defend, and hold Blue Meridian harmless from and against any and all claims, losses, and liabilities ("Liabilities") arising out of: (i) Cedar Lantern\'s use of Property; (ii) construction and operation of the Cedar Lantern Project; or (iii) any act or omission of Cedar Lantern, except to the extent such Liabilities arise from the negligence or willful misconduct of Blue Meridian.',
            postamble_text="except to the extent such Liabilities arise from the negligence or willful misconduct of Blue Meridian.",
            reconstructed_context_text='4. Indemnification by Cedar Lantern. To the fullest extent permitted by law, Cedar Lantern shall indemnify, defend, and hold Blue Meridian harmless from and against any and all claims, losses, and liabilities ("Liabilities") arising out of: (i) Cedar Lantern\'s use of Property; (ii) construction and operation of the Cedar Lantern Project; or (iii) any act or omission of Cedar Lantern, except to the extent such Liabilities arise from the negligence or willful misconduct of Blue Meridian.',
            page_start=2,
            page_end=2,
        ),
        ClauseRow(
            node_id="BODY.4.i",
            parent_node_id="BODY.4",
            sibling_order=1,
            document_zone=DocumentZone.BODY,
            canonical_path="BODY.4.i",
            depth=2,
            clause_label="(i)",
            numbering_scheme=NumberingScheme.ROMAN_LOWER,
            is_inline_clause=True,
            verbatim_text="(i) Cedar Lantern's use of Property;",
            reconstructed_context_text="",
            page_start=2,
            page_end=2,
        ),
        ClauseRow(
            node_id="BODY.5",
            parent_node_id=None,
            sibling_order=5,
            document_zone=DocumentZone.BODY,
            canonical_path="BODY.5",
            depth=1,
            clause_label="5",
            numbering_scheme=NumberingScheme.INTEGER,
            clause_title="Indemnification by Blue Meridian",
            is_inline_clause=False,
            preamble_text="To the fullest extent permitted by law, Blue Meridian shall indemnify, defend, and hold Cedar Lantern harmless from and against any and all Liabilities arising out of:",
            verbatim_text="5. Indemnification by Blue Meridian. To the fullest extent permitted by law, Blue Meridian shall indemnify, defend, and hold Cedar Lantern harmless from and against any and all Liabilities arising out of: (i) Blue Meridian's use of Property; (ii) construction and operation of the Blue Meridian Project; or (iii) any act or omission of Blue Meridian, except to the extent such Liabilities arise from the negligence or willful misconduct of Cedar Lantern.",
            postamble_text="except to the extent such Liabilities arise from the negligence or willful misconduct of Cedar Lantern.",
            reconstructed_context_text="5. Indemnification by Blue Meridian. To the fullest extent permitted by law, Blue Meridian shall indemnify, defend, and hold Cedar Lantern harmless from and against any and all Liabilities arising out of: (i) Blue Meridian's use of Property; (ii) construction and operation of the Blue Meridian Project; or (iii) any act or omission of Blue Meridian, except to the extent such Liabilities arise from the negligence or willful misconduct of Cedar Lantern.",
            page_start=2,
            page_end=2,
        ),
        ClauseRow(
            node_id="BODY.5.i",
            parent_node_id="BODY.5",
            sibling_order=1,
            document_zone=DocumentZone.BODY,
            canonical_path="BODY.5.i",
            depth=2,
            clause_label="(i)",
            numbering_scheme=NumberingScheme.ROMAN_LOWER,
            is_inline_clause=True,
            verbatim_text="(i) Blue Meridian's use of Property;",
            reconstructed_context_text="",
            page_start=2,
            page_end=2,
        ),
        ClauseRow(
            node_id="BODY.6",
            parent_node_id=None,
            sibling_order=6,
            document_zone=DocumentZone.BODY,
            canonical_path="BODY.6",
            depth=1,
            clause_label="6",
            numbering_scheme=NumberingScheme.INTEGER,
            clause_title="WAIVER OF CONSEQUENTIAL DAMAGES",
            is_inline_clause=False,
            verbatim_text="6. WAIVER OF CONSEQUENTIAL DAMAGES. NEITHER PARTY SHALL BE LIABLE TO THE OTHER FOR ANY SPECIAL, INDIRECT, INCIDENTAL, PUNITIVE, OR CONSEQUENTIAL DAMAGES ARISING OUT OF OR RELATING TO THIS AGREEMENT.",
            reconstructed_context_text="6. WAIVER OF CONSEQUENTIAL DAMAGES. NEITHER PARTY SHALL BE LIABLE TO THE OTHER FOR ANY SPECIAL, INDIRECT, INCIDENTAL, PUNITIVE, OR CONSEQUENTIAL DAMAGES ARISING OUT OF OR RELATING TO THIS AGREEMENT.",
            page_start=2,
            page_end=3,
        ),
        ClauseRow(
            node_id="BODY.8",
            parent_node_id=None,
            sibling_order=8,
            document_zone=DocumentZone.BODY,
            canonical_path="BODY.8",
            depth=1,
            clause_label="8",
            numbering_scheme=NumberingScheme.INTEGER,
            clause_title="Confidentiality",
            is_inline_clause=False,
            preamble_text="Neither Party shall disclose the terms of this Agreement except to",
            verbatim_text="8. Confidentiality. Neither Party shall disclose the terms of this Agreement except to (i) its affiliates, lenders, and advisors, or (ii) as required by law. Notwithstanding the foregoing, either Party may record a memorandum of this Agreement.",
            postamble_text="Notwithstanding the foregoing, either Party may record a memorandum of this Agreement.",
            reconstructed_context_text="8. Confidentiality. Neither Party shall disclose the terms of this Agreement except to (i) its affiliates, lenders, and advisors, or (ii) as required by law. Notwithstanding the foregoing, either Party may record a memorandum of this Agreement.",
            page_start=3,
            page_end=3,
        ),
        ClauseRow(
            node_id="BODY.8.i",
            parent_node_id="BODY.8",
            sibling_order=1,
            document_zone=DocumentZone.BODY,
            canonical_path="BODY.8.i",
            depth=2,
            clause_label="(i)",
            numbering_scheme=NumberingScheme.ROMAN_LOWER,
            is_inline_clause=True,
            verbatim_text="(i) its affiliates, lenders, and advisors",
            reconstructed_context_text="",
            page_start=3,
            page_end=3,
        ),
        ClauseRow(
            node_id="SIGNATURES.1",
            parent_node_id=None,
            sibling_order=1,
            document_zone=DocumentZone.SIGNATURES,
            canonical_path="SIGNATURES.1",
            depth=1,
            clause_label="SIG_1",
            numbering_scheme=NumberingScheme.NAMED_HEADER,
            is_inline_clause=False,
            verbatim_text="IN WITNESS WHEREOF... [Execution & Notary Block]",
            reconstructed_context_text="[Placeholder: Execution & Notary Block on Page 5]",
            page_start=5,
            page_end=5,
        ),
        ClauseRow(
            node_id="EXHIBIT_A",
            parent_node_id=None,
            sibling_order=1,
            document_zone=DocumentZone.EXHIBIT_OR_SCHEDULE,
            canonical_path="EXHIBIT_A",
            depth=1,
            clause_label="Exhibit A",
            numbering_scheme=NumberingScheme.NAMED_HEADER,
            clause_title="Legal Description of Property",
            is_inline_clause=False,
            verbatim_text="EXHIBIT A: Legal Description of Property",
            reconstructed_context_text="EXHIBIT A: Legal Description of Property",
            page_start=7,
            page_end=8,
        ),
        ClauseRow(
            node_id="EXHIBIT_A.PARCEL_4",
            parent_node_id="EXHIBIT_A",
            sibling_order=4,
            document_zone=DocumentZone.EXHIBIT_OR_SCHEDULE,
            canonical_path="EXHIBIT_A.PARCEL_4",
            depth=2,
            clause_label="Parcel 4",
            numbering_scheme=NumberingScheme.NAMED_HEADER,
            is_inline_clause=False,
            verbatim_text="Parcel 4 (Tax Parcel ID 14-08-300-004): All that part of Section 8...",
            reconstructed_context_text="Parcel 4 (Tax Parcel ID 14-08-300-004): All that part of Section 8...",
            page_start=7,
            page_end=8,
        ),
        ClauseRow(
            node_id="EXHIBIT_A.PARCEL_4.EXCEPT_1",
            parent_node_id="EXHIBIT_A.PARCEL_4",
            sibling_order=1,
            document_zone=DocumentZone.EXHIBIT_OR_SCHEDULE,
            canonical_path="EXHIBIT_A.PARCEL_4.EXCEPT_1",
            depth=3,
            clause_label="LESS_AND_EXCEPT",
            numbering_scheme=NumberingScheme.NAMED_HEADER,
            is_inline_clause=False,
            verbatim_text="LESS AND EXCEPT: A tract of land in the NW1/4 containing 6.15 acres recorded in Book 418, Page 127.",
            reconstructed_context_text="",
            page_start=8,
            page_end=8,
        ),
        ClauseRow(
            node_id="EXHIBIT_D.STUB",
            parent_node_id=None,
            sibling_order=2,
            document_zone=DocumentZone.EXHIBIT_OR_SCHEDULE,
            canonical_path="EXHIBIT_D.STUB",
            depth=1,
            clause_label="Exhibit D",
            numbering_scheme=NumberingScheme.NAMED_HEADER,
            is_inline_clause=False,
            verbatim_text="EXHIBIT D: Blue Meridian Facilities [Drawing]",
            reconstructed_context_text="[Placeholder: Visual/CAD Exhibit on Pages 11–12]",
            page_start=11,
            page_end=12,
        ),
    ]

    defined_terms = [
        DefinedTermRow(
            term_name=name,
            defined_in_node_id=node_id,
            definition_type=DefinitionType.INLINE_PARENTHETICAL,
            verbatim_definition=defn,
            page_number=page,
        )
        for name, node_id, defn, page in [
            ("Agreement", "PREAMBLE.1", 'This ACCOMMODATION AGREEMENT (this "Agreement")', 1),
            ("Cedar Lantern", "PREAMBLE.1", 'Cedar Lantern Wind, LLC ("Cedar Lantern")', 1),
            ("Blue Meridian", "PREAMBLE.1", 'Blue Meridian Transmission, LLC ("Blue Meridian")', 1),
            ("Party", "PREAMBLE.1", 'each a "Party"', 1),
            ("Parties", "PREAMBLE.1", 'collectively, the "Parties"', 1),
            ("Cedar Lantern Project", "RECITALS.1", 'a wind energy project (the "Cedar Lantern Project")', 1),
            ("Property", "RECITALS.1", 'that certain real property described in Exhibit A (the "Property")', 1),
            ("Cedar Lantern Easements", "RECITALS.1", 'wind lease and easement agreements listed in Exhibit B (the "Cedar Lantern Easements")', 1),
            ("Blue Meridian Easements", "RECITALS.2", 'transmission line easements across portions of the Property listed in Exhibit C (the "Blue Meridian Easements")', 1),
            ("Blue Meridian Project", "RECITALS.2", 'its transmission project (the "Blue Meridian Project")', 1),
            ("FPUC", "RECITALS.2", 'Franklin Public Utilities Commission ("FPUC")', 1),
            ("CCN", "RECITALS.2", 'Certificate of Convenience and Necessity ("CCN")', 1),
            ("Blue Meridian Facilities", "RECITALS.2", 'transmission facilities depicted in Exhibit D (the "Blue Meridian Facilities")', 1),
            ("Operations", "BODY.3.a", 'construction, operation, and maintenance activities ("Operations")', 2),
            ("Equipment", "BODY.3.a", 'installations ("Equipment")', 2),
            ("Liabilities", "BODY.4", 'claims, losses, and liabilities ("Liabilities")', 2),
        ]
    ]

    exhibits = [
        ExhibitCatalogRow(
            exhibit_id="Exhibit A",
            exhibit_title="Legal Description of Property",
            exhibit_modality=ExhibitModality.PROPERTY_DESCRIPTION,
            page_start=7,
            page_end=8,
            structured_entities_json=json.dumps(
                [
                    {"parcel": "Parcel 1", "tax_id": "14-08-100-001"},
                    {"parcel": "Parcel 2", "tax_id": "14-08-200-002"},
                    {"parcel": "Parcel 3", "tax_id": "14-08-200-003"},
                    {
                        "parcel": "Parcel 4",
                        "tax_id": "14-08-300-004",
                        "carveouts": ["6.15 acres in NW1/4, Book 418, Page 127"],
                    },
                ]
            ),
            has_unresolved_external_dep=False,
        ),
        ExhibitCatalogRow(
            exhibit_id="Exhibit B",
            exhibit_title="Cedar Lantern Easements",
            exhibit_modality=ExhibitModality.EXTERNAL_INSTRUMENT_LIST,
            page_start=9,
            page_end=9,
            structured_entities_json=json.dumps(
                [{"instrument": "Wind Lease", "book": "410", "page": "112"}]
            ),
            has_unresolved_external_dep=True,
        ),
        ExhibitCatalogRow(
            exhibit_id="Exhibit C",
            exhibit_title="Blue Meridian Easements",
            exhibit_modality=ExhibitModality.EXTERNAL_INSTRUMENT_LIST,
            page_start=10,
            page_end=10,
            structured_entities_json=json.dumps(
                [{"instrument": "Transmission Easement", "book": "412", "page": "188"}]
            ),
            has_unresolved_external_dep=True,
        ),
        ExhibitCatalogRow(
            exhibit_id="Exhibit D",
            exhibit_title="Blue Meridian Facilities Crossing Drawing",
            exhibit_modality=ExhibitModality.VISUAL_DRAWING_OR_MAP_STUB,
            page_start=11,
            page_end=12,
            structured_entities_json=json.dumps(
                [{"drawing_type": "PLS-CADD Crossing Plan & Profile"}]
            ),
            has_unresolved_external_dep=False,
        ),
    ]

    return GeminiContractExtraction(
        page_count=12,
        contracting_parties_json=json.dumps(
            [
                {"name": "Cedar Lantern Wind, LLC", "short_name": "Cedar Lantern", "role": "Wind Developer"},
                {"name": "Blue Meridian Transmission, LLC", "short_name": "Blue Meridian", "role": "Transmission Utility"},
            ]
        ),
        effective_date="October 14, 2024",
        clauses=clauses,
        defined_terms=defined_terms,
        exhibits_catalog=exhibits,
    )


class HermeticFixtureExtractor:
    """Hermetic extractor returning the normalized Synthetic Accommodation Agreement extraction."""

    def extract(
        self,
        *,
        document_id: str,
        gcs_pdf_uri: str,
        pdf_bytes: bytes | None = None,
    ) -> GeminiContractExtraction:
        del pdf_bytes
        return normalize_and_enrich_extraction(
            extraction=build_synthetic_accommodation_fixture(),
            document_id=document_id,
            gcs_pdf_uri=gcs_pdf_uri,
        )


def test_schema_parity_and_config_defaults() -> None:
    """Verify exact 8-table column counts, BigQuery schema parity, and PipelineConfig defaults (SA-1..SA-5, CR-1, CR-2, CR-3)."""
    assert len(PROJECT_CSV_COLUMNS) == 12
    assert len(LANDOWNER_CSV_COLUMNS) == 10
    assert len(CLAUSE_CSV_COLUMNS) == 32
    assert len(DEFINED_TERM_CSV_COLUMNS) == 8
    assert len(EXHIBIT_CATALOG_CSV_COLUMNS) == 10
    assert len(DOCUMENT_REGISTRY_COLUMNS) == 18
    assert len(SPECIAL_CONDITION_CSV_COLUMNS) == 18
    assert len(DND_SIGNOFF_CSV_COLUMNS) == 12

    assert [f.name for f in BQ_PROJECTS_SCHEMA] == PROJECT_CSV_COLUMNS
    assert [f.name for f in BQ_LANDOWNERS_SCHEMA] == LANDOWNER_CSV_COLUMNS
    assert [f.name for f in BQ_CLAUSES_SCHEMA] == CLAUSE_CSV_COLUMNS
    assert [f.name for f in BQ_DEFINED_TERMS_SCHEMA] == DEFINED_TERM_CSV_COLUMNS
    assert [f.name for f in BQ_EXHIBITS_CATALOG_SCHEMA] == EXHIBIT_CATALOG_CSV_COLUMNS
    assert set(f.name for f in BQ_DOCUMENTS_SCHEMA) == set(DOCUMENT_REGISTRY_COLUMNS)
    assert len(BQ_DOCUMENTS_SCHEMA) == len(DOCUMENT_REGISTRY_COLUMNS)
    assert set(f.name for f in BQ_SPECIAL_CONDITIONS_SCHEMA) == set(SPECIAL_CONDITION_CSV_COLUMNS)
    assert len(BQ_SPECIAL_CONDITIONS_SCHEMA) == len(SPECIAL_CONDITION_CSV_COLUMNS)
    assert [f.name for f in BQ_DND_SIGNOFFS_SCHEMA] == DND_SIGNOFF_CSV_COLUMNS

    cfg = PipelineConfig()
    assert cfg.google_cloud_project == "pr-tftest"
    assert cfg.google_cloud_location == "global"
    assert cfg.gemini_model == "gemini-3.1-pro-preview"
    assert cfg.gemini_fallback_model == "gemini-3.8-flash"
    assert cfg.use_enterprise is True


def test_deep_6_tier_hierarchy_and_context_normalization() -> None:
    """Verify arbitrary 6-level nesting into level_1..level_4 and level_5_plus_path (UT-1..UT-3)."""
    nodes = [
        ClauseRow(
            node_id="BODY.ART_I",
            parent_node_id=None,
            sibling_order=1,
            document_zone=DocumentZone.BODY,
            canonical_path="BODY.ART_I",
            depth=1,
            clause_label="Article I",
            numbering_scheme=NumberingScheme.ROMAN_UPPER,
            preamble_text="Under Article I,",
            verbatim_text="Article I",
            reconstructed_context_text="Article I",
            page_start=1,
            page_end=1,
        ),
        ClauseRow(
            node_id="BODY.ART_I.1_01",
            parent_node_id="BODY.ART_I",
            sibling_order=1,
            document_zone=DocumentZone.BODY,
            canonical_path="BODY.ART_I.1_01",
            depth=2,
            clause_label="Section 1.01",
            numbering_scheme=NumberingScheme.DECIMAL,
            preamble_text="Party A shall:",
            verbatim_text="Section 1.01",
            reconstructed_context_text="Section 1.01",
            page_start=1,
            page_end=1,
        ),
        ClauseRow(
            node_id="BODY.ART_I.1_01.a",
            parent_node_id="BODY.ART_I.1_01",
            sibling_order=1,
            document_zone=DocumentZone.BODY,
            canonical_path="BODY.ART_I.1_01.a",
            depth=3,
            clause_label="(a)",
            numbering_scheme=NumberingScheme.ALPHA_LOWER,
            verbatim_text="(a)",
            reconstructed_context_text="(a)",
            page_start=1,
            page_end=1,
        ),
        ClauseRow(
            node_id="BODY.ART_I.1_01.a.1",
            parent_node_id="BODY.ART_I.1_01.a",
            sibling_order=1,
            document_zone=DocumentZone.BODY,
            canonical_path="BODY.ART_I.1_01.a.1",
            depth=4,
            clause_label="(1)",
            numbering_scheme=NumberingScheme.PAREN_INT,
            verbatim_text="(1)",
            reconstructed_context_text="(1)",
            page_start=1,
            page_end=1,
        ),
        ClauseRow(
            node_id="BODY.ART_I.1_01.a.1.i",
            parent_node_id="BODY.ART_I.1_01.a.1",
            sibling_order=1,
            document_zone=DocumentZone.BODY,
            canonical_path="BODY.ART_I.1_01.a.1.i",
            depth=5,
            clause_label="(i)",
            numbering_scheme=NumberingScheme.ROMAN_LOWER,
            verbatim_text="(i)",
            reconstructed_context_text="(i)",
            page_start=1,
            page_end=1,
        ),
        ClauseRow(
            node_id="BODY.ART_I.1_01.a.1.i.A",
            parent_node_id="BODY.ART_I.1_01.a.1.i",
            sibling_order=1,
            document_zone=DocumentZone.BODY,
            canonical_path="BODY.ART_I.1_01.a.1.i.A",
            depth=6,
            clause_label="(A)",
            numbering_scheme=NumberingScheme.ALPHA_UPPER,
            verbatim_text="(A) maintain 50 feet clearance.",
            reconstructed_context_text="(A) maintain 50 feet clearance.",
            page_start=1,
            page_end=1,
        ),
    ]
    extracted = normalize_and_enrich_extraction(
        GeminiContractExtraction(page_count=1, clauses=nodes),
        document_id="doc_test6",
        gcs_pdf_uri="gs://test/doc.pdf",
    )
    leaf = extracted.clauses[-1]
    assert leaf.depth == 6
    assert leaf.level_1_label == "Article I"
    assert leaf.level_2_label == "Section 1.01"
    assert leaf.level_3_label == "(a)"
    assert leaf.level_4_label == "(1)"
    assert leaf.level_5_plus_path == "(i).(A)"
    assert "Under Article I," in leaf.reconstructed_context_text
    assert "Party A shall:" in leaf.reconstructed_context_text
    assert "(A) maintain 50 feet clearance." in leaf.reconstructed_context_text


def test_benchmark_sample_agreement_invariants() -> None:
    """Verify all 9 benchmark rows, 16 defined terms, 4 exhibits, and 0 false-positive physical constraints on Synthetic_Accommodation_Agreement.pdf (IT-1, IT-2, CR-1)."""
    extracted = normalize_and_enrich_extraction(
        build_synthetic_accommodation_fixture(),
        document_id="doc_bench",
        gcs_pdf_uri="gs://pr-tftest-contract-intelligence/raw/doc_bench/Synthetic_Accommodation_Agreement.pdf",
    )
    by_id = {c.node_id: c for c in extracted.clauses}

    # 1. RECITALS.1
    assert by_id["RECITALS.1"].document_zone == DocumentZone.RECITALS
    assert by_id["RECITALS.1"].hitl_flag_reasons == "NONE"

    # 2. BODY.1
    assert by_id["BODY.1"].document_zone == DocumentZone.BODY
    assert by_id["BODY.1"].hitl_status == HITLStatus.VERIFIED_AUTO

    # 3. BODY.2.i (Skipped tier INTEGER -> ROMAN_LOWER + 2-hop UNRESOLVED_EXTERNAL_DEPENDENCY via Blue Meridian Easements -> Exhibit C)
    b2i = by_id["BODY.2.i"]
    assert b2i.level_1_label == "2"
    assert b2i.level_2_label == "(i)"
    assert "This Agreement shall terminate upon the earlier of" in b2i.reconstructed_context_text
    assert FlagCode.AMBIGUOUS_HIERARCHY_MARKER.value in b2i.hitl_flag_reasons
    assert FlagCode.UNRESOLVED_EXTERNAL_DEPENDENCY.value in b2i.hitl_flag_reasons
    assert b2i.hitl_status == HITLStatus.FLAGGED_FOR_REVIEW

    # 4. BODY.3.b (Inline ALPHA_LOWER inheriting BODY.3 preamble + referencing Operations & Equipment)
    b3b = by_id["BODY.3.b"]
    assert b3b.level_1_label == "3"
    assert b3b.level_2_label == "(b)"
    assert b3b.is_inline_clause is True
    assert "Each of the Parties covenants, acknowledges and agrees that" in b3b.reconstructed_context_text
    assert "Operations" in b3b.defined_terms_used
    assert "Equipment" in b3b.defined_terms_used
    assert b3b.hitl_flag_reasons == "NONE"

    # 5. BODY.4.i (Sandwich clause: inherits indemnity preamble AND trailing negligence carve-out)
    b4i = by_id["BODY.4.i"]
    assert "Cedar Lantern shall indemnify" in b4i.reconstructed_context_text
    assert "(i) Cedar Lantern's use of Property;" in b4i.reconstructed_context_text
    assert "except to the extent such Liabilities arise from the negligence or willful misconduct of Blue Meridian." in b4i.reconstructed_context_text
    assert FlagCode.AMBIGUOUS_HIERARCHY_MARKER.value in b4i.hitl_flag_reasons
    assert FlagCode.SCOPE_CARVEOUT_DETECTED.value in b4i.hitl_flag_reasons
    assert FlagCode.LANDOWNER_SPECIAL_CONDITION.value not in b4i.hitl_flag_reasons
    assert b4i.has_special_condition is False

    # 6. BODY.6 (Cross-page stitched clause across pages 2 and 3)
    b6 = by_id["BODY.6"]
    assert (b6.page_start, b6.page_end) == (2, 3)
    assert b6.hitl_flag_reasons == "NONE"

    # 7. SIGNATURES.1 (Deferred modality placeholder on Page 5)
    sig1 = by_id["SIGNATURES.1"]
    assert sig1.hitl_status == HITLStatus.PLACEHOLDER_FOR_REVIEW
    assert sig1.hitl_flag_reasons == FlagCode.DEFERRED_MODALITY_PLACEHOLDER.value

    # 8. EXHIBIT_A.PARCEL_4.EXCEPT_1 (Depth 3 block-header carve-out synthesis — legal carve-out, NOT physical site constraint)
    ex_a_carve = by_id["EXHIBIT_A.PARCEL_4.EXCEPT_1"]
    assert ex_a_carve.depth == 3
    assert ex_a_carve.level_1_label == "Exhibit A"
    assert ex_a_carve.level_2_label == "Parcel 4"
    assert ex_a_carve.level_3_label == "LESS_AND_EXCEPT"
    assert ex_a_carve.reconstructed_context_text.startswith("Excluded from Exhibit A, Parcel 4:")
    assert FlagCode.SCOPE_CARVEOUT_DETECTED.value in ex_a_carve.hitl_flag_reasons
    assert FlagCode.LANDOWNER_SPECIAL_CONDITION.value not in ex_a_carve.hitl_flag_reasons
    assert ex_a_carve.has_special_condition is False

    # 9. EXHIBIT_D.STUB (CAD drawing placeholder on Pages 11-12)
    ex_d = by_id["EXHIBIT_D.STUB"]
    assert (ex_d.page_start, ex_d.page_end) == (11, 12)
    assert ex_d.hitl_status == HITLStatus.PLACEHOLDER_FOR_REVIEW
    assert ex_d.hitl_flag_reasons == FlagCode.DEFERRED_MODALITY_PLACEHOLDER.value

    # Verify 0 false-positive physical constraints across the entire standard Accommodation Agreement
    assert len(extracted.special_conditions) == 0
    assert len(extracted.defined_terms) == 16
    assert len(extracted.exhibits_catalog) == 4


def test_storage_append_only_deduplication_and_fastapi_endpoints(tmp_path: Path) -> None:
    """Verify append-only versioning, CSV round-trip, and FastAPI Web UI endpoints (IT-3, IT-4)."""
    cfg = PipelineConfig(
        local_data_dir=tmp_path / "data",
        use_cloud_storage=False,
        use_bigquery=False,
    )
    store = ContractStorageService(cfg)
    extractor = HermeticFixtureExtractor()
    client = TestClient(create_app(config=cfg, storage_service=store, extractor=extractor))

    # 1. GET / serves split-screen pdf.js HTML UI with Site Constraints tab
    html_res = client.get("/")
    assert html_res.status_code == 200
    assert "pdf.min.js" in html_res.text
    assert "Show Full Reconstructed Context" in html_res.text
    assert "Site Constraints" in html_res.text
    assert "special_conditions.csv" in html_res.text

    # 2. POST /api/v1/documents/upload
    pdf_bytes = SAMPLE_PDF_PATH.read_bytes() if SAMPLE_PDF_PATH.exists() else b"%PDF-1.4 synthetic contract test"
    up_res = client.post(
        "/api/v1/documents/upload",
        files={"file": ("Synthetic_Accommodation_Agreement.pdf", pdf_bytes, "application/pdf")},
    )
    assert up_res.status_code == 200
    bundle_json = up_res.json()
    doc_id = bundle_json["document"]["document_id"]
    initial_flagged = bundle_json["document"]["flagged_node_count"]
    assert initial_flagged > 0
    assert bundle_json["document"]["ingestion_status"] == IngestionStatus.NEEDS_HITL_REVIEW.value

    # 3. GET /api/v1/documents & GET /api/v1/documents/{doc_id}/pdf (with byte-range)
    list_res = client.get("/api/v1/documents")
    assert list_res.status_code == 200
    assert list_res.json()["count"] == 1

    range_res = client.get(
        f"/api/v1/documents/{doc_id}/pdf", headers={"Range": "bytes=0-15"}
    )
    assert range_res.status_code == 206
    assert len(range_res.content) == 16

    suffix_range_res = client.get(
        f"/api/v1/documents/{doc_id}/pdf", headers={"Range": "bytes=-16"}
    )
    assert suffix_range_res.status_code == 206
    assert len(suffix_range_res.content) == 16

    # 4. PATCH /api/v1/documents/{doc_id}/clauses/BODY.4.i (Append-only human approval)
    patch_res = client.patch(
        f"/api/v1/documents/{doc_id}/clauses/BODY.4.i",
        json={
            "hitl_status": "APPROVED_BY_HUMAN",
            "reviewed_by": "prasanna_reviewer",
            "review_notes": "Verified sandwich negligence carve-out on Page 2",
        },
    )
    assert patch_res.status_code == 200
    patched_payload = patch_res.json()
    assert patched_payload["clause"]["hitl_status"] == "APPROVED_BY_HUMAN"
    assert patched_payload["clause"]["reviewed_by"] == "prasanna_reviewer"
    assert patched_payload["document"]["flagged_node_count"] == initial_flagged - 1

    # Verify append-only audit trail in SQLite has 2 versions of BODY.4.i while get_bundle deduplicates to 1
    with sqlite3.connect(store.local_db_path) as conn:
        raw_versions = conn.execute(
            "SELECT COUNT(*) FROM clauses WHERE document_id = ? AND node_id = 'BODY.4.i'",
            (doc_id,),
        ).fetchone()[0]
    assert raw_versions == 2

    dedup_bundle = store.get_bundle(doc_id)
    b4i_latest = next(c for c in dedup_bundle.clauses if c.node_id == "BODY.4.i")
    assert b4i_latest.hitl_status == HITLStatus.APPROVED_BY_HUMAN
    assert b4i_latest.reviewed_by == "prasanna_reviewer"

    # 5. Verify utf-8-sig CSV exports (all 4 CSV files) & load_bundle_from_csv round-trip
    for csv_name in (
        "clauses.csv",
        "defined_terms.csv",
        "exhibits_catalog.csv",
        "special_conditions.csv",
    ):
        csv_res = client.get(f"/api/v1/documents/{doc_id}/export/{csv_name}")
        assert csv_res.status_code == 200
        assert csv_res.content.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM (utf-8-sig)

    reloaded = store.load_bundle_from_csv(
        store.local_exports_dir / doc_id, dedup_bundle.document
    )
    assert len(reloaded.clauses) == len(dedup_bundle.clauses)
    assert len(reloaded.defined_terms) == len(dedup_bundle.defined_terms)
    assert len(reloaded.exhibits_catalog) == len(dedup_bundle.exhibits_catalog)
    assert len(reloaded.special_conditions) == len(dedup_bundle.special_conditions)


def test_cr1_landowner_special_conditions_multi_constraint_and_hitl_cascade(tmp_path: Path) -> None:
    """Verify CR-1 multi-constraint decomposition, sandwich leaf deduplication, deterministic 2-signal fallback, and cascading HITL review approval."""
    clauses = [
        ClauseRow(
            node_id="BODY.7",
            parent_node_id=None,
            sibling_order=7,
            document_zone=DocumentZone.BODY,
            canonical_path="BODY.7",
            depth=1,
            clause_label="7",
            numbering_scheme=NumberingScheme.INTEGER,
            clause_title="Special Conditions and Site Restrictions",
            is_inline_clause=False,
            preamble_text="Grantee shall comply with the following landowner site restrictions:",
            verbatim_text="7. Special Conditions and Site Restrictions. Grantee shall comply with the following landowner site restrictions: (a) Grantee shall not cut or trim any pecan or oak trees in the Pecan Grove (subject to a $5,000 per tree liquidated damages penalty), and no construction or grading shall occur during deer hunting season from November 15 through December 1.",
            reconstructed_context_text="7. Special Conditions and Site Restrictions.",
            page_start=4,
            page_end=4,
        ),
        ClauseRow(
            node_id="BODY.7.a",
            parent_node_id="BODY.7",
            sibling_order=1,
            document_zone=DocumentZone.BODY,
            canonical_path="BODY.7.a",
            depth=2,
            clause_label="(a)",
            numbering_scheme=NumberingScheme.ALPHA_LOWER,
            is_inline_clause=True,
            verbatim_text="(a) Grantee shall not cut or trim any pecan or oak trees in the Pecan Grove (subject to a $5,000 per tree liquidated damages penalty), and no construction or grading shall occur during deer hunting season from November 15 through December 1.",
            reconstructed_context_text="",
            page_start=4,
            page_end=4,
        ),
        ClauseRow(
            node_id="BODY.9",
            parent_node_id=None,
            sibling_order=9,
            document_zone=DocumentZone.BODY,
            canonical_path="BODY.9",
            depth=1,
            clause_label="9",
            numbering_scheme=NumberingScheme.INTEGER,
            clause_title="Water Well Setback and Gate Protocol",
            is_inline_clause=False,
            verbatim_text="9. Water Well Setback and Gate Protocol. Grantee shall not disturb or clear within 250 feet of the homestead water well or barn, and all cattle gates must be kept closed and locked.",
            reconstructed_context_text="9. Water Well Setback and Gate Protocol. Grantee shall not disturb or clear within 250 feet of the homestead water well or barn, and all cattle gates must be kept closed and locked.",
            page_start=4,
            page_end=4,
        ),
    ]

    # Simulate Gemini returning:
    # 1. A duplicate condition on parent BODY.7 (which must be deduplicated in favor of leaf BODY.7.a)
    # 2. Two distinct SpecialConditionRow items on leaf BODY.7.a (tree protection + hunting blackout)
    # 3. No SpecialConditionRow on BODY.9 (so the deterministic 2-signal fallback regex catches it)
    raw_special_conditions = [
        SpecialConditionRow(
            node_id="BODY.7",
            constraint_category=ConstraintCategory.TREE_VEGETATION_PROTECTION,
            target_asset_or_area="pecan or oak trees in the Pecan Grove",
            actionable_obligation_summary="Do not cut or trim any pecan or oak trees in the Pecan Grove.",
            quantitative_metric=None,
            temporal_restriction=None,
            penalty_or_consequence="$5,000 per tree",
            verbatim_excerpt="Grantee shall not cut or trim any pecan or oak trees in the Pecan Grove (subject to a $5,000 per tree liquidated damages penalty)",
            page_number=4,
        ),
        SpecialConditionRow(
            node_id="BODY.7.a",
            constraint_category=ConstraintCategory.TREE_VEGETATION_PROTECTION,
            target_asset_or_area="pecan or oak trees in the Pecan Grove",
            actionable_obligation_summary="Do not cut or trim any pecan or oak trees in the Pecan Grove.",
            quantitative_metric=None,
            temporal_restriction=None,
            penalty_or_consequence="$5,000 per tree",
            verbatim_excerpt="Grantee shall not cut or trim any pecan or oak trees in the Pecan Grove (subject to a $5,000 per tree liquidated damages penalty)",
            page_number=4,
        ),
        SpecialConditionRow(
            node_id="BODY.7.a",
            constraint_category=ConstraintCategory.TIMING_NOISE_HUNTING_BLACKOUT,
            target_asset_or_area="construction or grading",
            actionable_obligation_summary="No construction or grading during deer hunting season from November 15 through December 1.",
            quantitative_metric=None,
            temporal_restriction="November 15 through December 1",
            penalty_or_consequence=None,
            verbatim_excerpt="no construction or grading shall occur during deer hunting season from November 15 through December 1.",
            page_number=4,
        ),
    ]

    class CR1FixtureExtractor:
        def extract(
            self,
            *,
            document_id: str,
            gcs_pdf_uri: str,
            pdf_bytes: bytes | None = None,
        ) -> GeminiContractExtraction:
            del pdf_bytes
            return normalize_and_enrich_extraction(
                extraction=GeminiContractExtraction(
                    page_count=4,
                    clauses=clauses,
                    special_conditions=raw_special_conditions,
                ),
                document_id=document_id,
                gcs_pdf_uri=gcs_pdf_uri,
            )

    cfg = PipelineConfig(
        local_data_dir=tmp_path / "cr1_data",
        use_cloud_storage=False,
        use_bigquery=False,
    )
    store = ContractStorageService(cfg)
    client = TestClient(create_app(config=cfg, storage_service=store, extractor=CR1FixtureExtractor()))

    up_res = client.post(
        "/api/v1/documents/upload",
        files={"file": ("Landowner_Special_Conditions_Lease.pdf", b"%PDF-1.4 cr1 test", "application/pdf")},
    )
    assert up_res.status_code == 200
    bundle = up_res.json()
    doc_id = bundle["document"]["document_id"]

    # Parent BODY.7 duplicate was dropped; leaf BODY.7.a has 2 conditions + BODY.9 has 1 fallback condition = 3 total
    assert bundle["document"]["special_conditions_count"] == 3
    assert len(bundle["special_conditions"]) == 3

    clauses_by_id = {c["node_id"]: c for c in bundle["clauses"]}
    assert clauses_by_id["BODY.7"]["has_special_condition"] is False
    assert clauses_by_id["BODY.7"]["special_condition_count"] == 0

    assert clauses_by_id["BODY.7.a"]["has_special_condition"] is True
    assert clauses_by_id["BODY.7.a"]["special_condition_count"] == 2
    assert FlagCode.LANDOWNER_SPECIAL_CONDITION.value in clauses_by_id["BODY.7.a"]["hitl_flag_reasons"]
    assert clauses_by_id["BODY.7.a"]["hitl_status"] == HITLStatus.FLAGGED_FOR_REVIEW.value

    # Verify deterministic 2-signal fallback caught BODY.9
    assert clauses_by_id["BODY.9"]["has_special_condition"] is True
    assert clauses_by_id["BODY.9"]["special_condition_count"] == 1
    assert FlagCode.LANDOWNER_SPECIAL_CONDITION.value in clauses_by_id["BODY.9"]["hitl_flag_reasons"]
    assert clauses_by_id["BODY.9"]["hitl_status"] == HITLStatus.FLAGGED_FOR_REVIEW.value

    sc_by_id = {sc["condition_id"]: sc for sc in bundle["special_conditions"]}
    assert set(sc_by_id.keys()) == {"sc_BODY.7.a_1", "sc_BODY.7.a_2", "sc_BODY.9_1"}
    assert sc_by_id["sc_BODY.7.a_1"]["penalty_or_consequence"] == "$5,000 per tree"
    assert sc_by_id["sc_BODY.7.a_2"]["temporal_restriction"] == "November 15 through December 1"
    assert sc_by_id["sc_BODY.9_1"]["quantitative_metric"] == "250 feet"
    assert sc_by_id["sc_BODY.9_1"]["extraction_confidence"] == 0.75
    assert (
        sc_by_id["sc_BODY.9_1"]["constraint_category"]
        == ConstraintCategory.STRUCTURE_BARN_WELL_SETBACK.value
    )

    # Verify cascading HITL approval when BODY.7.a is approved
    patch_res = client.patch(
        f"/api/v1/documents/{doc_id}/clauses/BODY.7.a",
        json={
            "hitl_status": "APPROVED_BY_HUMAN",
            "reviewed_by": "land_agent_reviewer",
            "review_notes": "Confirmed pecan grove penalty and hunting blackout dates",
        },
    )
    assert patch_res.status_code == 200
    patch_data = patch_res.json()
    assert len(patch_data["special_conditions"]) == 2
    for sc in patch_data["special_conditions"]:
        assert sc["hitl_status"] == "APPROVED_BY_HUMAN"
        assert sc["reviewed_by"] == "land_agent_reviewer"

    # Verify SQLite append-only audit trail has 2 versions for sc_BODY.7.a_1 and sc_BODY.7.a_2
    with sqlite3.connect(store.local_db_path) as conn:
        sc_versions = conn.execute(
            "SELECT COUNT(*) FROM special_conditions WHERE document_id = ? AND node_id = 'BODY.7.a'",
            (doc_id,),
        ).fetchone()[0]
    assert sc_versions == 4  # 2 initial rows + 2 appended human-approved rows

    # Verify special_conditions.csv export contains the approved rows and round-trips via load_bundle_from_csv
    csv_res = client.get(f"/api/v1/documents/{doc_id}/export/special_conditions.csv")
    assert csv_res.status_code == 200
    csv_text = csv_res.content.decode("utf-8-sig")
    assert "sc_BODY.7.a_1" in csv_text
    assert "sc_BODY.7.a_2" in csv_text
    assert "sc_BODY.9_1" in csv_text
    assert "land_agent_reviewer" in csv_text

    reloaded_cr1 = store.load_bundle_from_csv(
        store.local_exports_dir / doc_id, store.get_bundle(doc_id).document
    )
    assert len(reloaded_cr1.special_conditions) == 3
    assert reloaded_cr1.special_conditions[0].reviewed_by == "land_agent_reviewer"


def test_broken_internal_reference_detection() -> None:
    """Verify deterministic broken internal cross-reference flagging (FlagCode.BROKEN_INTERNAL_REFERENCE)."""
    extracted = normalize_and_enrich_extraction(
        GeminiContractExtraction(
            page_count=1,
            clauses=[
                ClauseRow(
                    node_id="BODY.1",
                    parent_node_id=None,
                    sibling_order=1,
                    document_zone=DocumentZone.BODY,
                    canonical_path="BODY.1",
                    depth=1,
                    clause_label="1",
                    numbering_scheme=NumberingScheme.INTEGER,
                    verbatim_text="1. Cross Reference. Subject to Section 99 and Exhibit Z, Party A shall comply.",
                    reconstructed_context_text="1. Cross Reference. Subject to Section 99 and Exhibit Z, Party A shall comply.",
                    page_start=1,
                    page_end=1,
                )
            ],
        ),
        document_id="doc_broken_ref",
        gcs_pdf_uri="gs://test/doc.pdf",
    )
    clause = extracted.clauses[0]
    assert FlagCode.BROKEN_INTERNAL_REFERENCE.value in clause.hitl_flag_reasons
    assert clause.hitl_status == HITLStatus.FLAGGED_FOR_REVIEW


def test_cr2_project_portfolio_hierarchy_and_centralized_search(tmp_path: Path) -> None:
    """Verify CR-2 5-technology project registry, automatic Grantor/QRM landowner entity binding, multi-parcel roll-up, and cross-portfolio search."""

    class CR2PortfolioFixtureExtractor:
        def extract(
            self,
            *,
            document_id: str,
            gcs_pdf_uri: str,
            pdf_bytes: bytes | None = None,
            project_id: str = "prj_cedar_lantern_wind",
            landowner_id: str | None = None,
        ) -> GeminiContractExtraction:
            del pdf_bytes
            clauses = [
                ClauseRow(
                    node_id="PREAMBLE.1",
                    parent_node_id=None,
                    sibling_order=1,
                    document_zone=DocumentZone.PREAMBLE,
                    canonical_path="PREAMBLE.1",
                    depth=1,
                    clause_label="Preamble",
                    numbering_scheme=NumberingScheme.UNNUMBERED,
                    verbatim_text='This Solar Lease Agreement is entered into by Arthur & Martha Pendelton ("Owner" or "Landowner") and Sun Ridge Solar Farm LLC ("Grantee").',
                    reconstructed_context_text='This Solar Lease Agreement is entered into by Arthur & Martha Pendelton ("Owner" or "Landowner") and Sun Ridge Solar Farm LLC ("Grantee").',
                    page_start=1,
                    page_end=1,
                ),
                ClauseRow(
                    node_id="BODY.8",
                    parent_node_id=None,
                    sibling_order=2,
                    document_zone=DocumentZone.BODY,
                    canonical_path="BODY.8",
                    depth=1,
                    clause_label="8",
                    numbering_scheme=NumberingScheme.INTEGER,
                    clause_title="Special Conditions",
                    verbatim_text="8. Special Conditions. Grantee shall maintain a 300 feet setback from the homestead barn and shall not trim any heritage pecan trees.",
                    reconstructed_context_text="8. Special Conditions. Grantee shall maintain a 300 feet setback from the homestead barn and shall not trim any heritage pecan trees.",
                    page_start=2,
                    page_end=2,
                ),
                ClauseRow(
                    node_id="EXHIBIT_A.PARCEL_1",
                    parent_node_id=None,
                    sibling_order=3,
                    document_zone=DocumentZone.EXHIBIT_A,
                    canonical_path="EXHIBIT_A.PARCEL_1",
                    depth=1,
                    clause_label="Parcel 1",
                    numbering_scheme=NumberingScheme.BLOCK_HEADER,
                    clause_title="Tract 1 - North 160 Acres",
                    verbatim_text="Parcel 1: North 160 Acres in Section 14.",
                    reconstructed_context_text="Parcel 1: North 160 Acres in Section 14.",
                    page_start=3,
                    page_end=3,
                ),
                ClauseRow(
                    node_id="EXHIBIT_A.PARCEL_2",
                    parent_node_id=None,
                    sibling_order=4,
                    document_zone=DocumentZone.EXHIBIT_A,
                    canonical_path="EXHIBIT_A.PARCEL_2",
                    depth=1,
                    clause_label="Parcel 2",
                    numbering_scheme=NumberingScheme.BLOCK_HEADER,
                    clause_title="Tract 2 - South 80 Acres",
                    verbatim_text="Parcel 2: South 80 Acres in Section 15.",
                    reconstructed_context_text="Parcel 2: South 80 Acres in Section 15.",
                    page_start=3,
                    page_end=3,
                ),
            ]
            raw_scs = [
                SpecialConditionRow(
                    node_id="BODY.8",
                    constraint_category=ConstraintCategory.SETBACK_OR_BUFFER,
                    target_asset_or_area="homestead barn",
                    actionable_obligation_summary="Maintain a 300 feet setback from the homestead barn.",
                    quantitative_metric="300 feet",
                    verbatim_excerpt="Grantee shall maintain a 300 feet setback from the homestead barn",
                    page_number=2,
                ),
                SpecialConditionRow(
                    node_id="BODY.8",
                    constraint_category=ConstraintCategory.CROP_OR_TIMBER_COMPENSATION,
                    target_asset_or_area="heritage pecan trees",
                    actionable_obligation_summary="Do not trim any heritage pecan trees.",
                    verbatim_excerpt="shall not trim any heritage pecan trees.",
                    page_number=2,
                ),
            ]
            return normalize_and_enrich_extraction(
                extraction=GeminiContractExtraction(
                    page_count=3,
                    clauses=clauses,
                    special_conditions=raw_scs,
                ),
                document_id=document_id,
                gcs_pdf_uri=gcs_pdf_uri,
                project_id=project_id,
                landowner_id=landowner_id,
            )

    cfg = PipelineConfig(
        local_data_dir=tmp_path / "cr2_data",
        use_cloud_storage=False,
        use_bigquery=False,
    )
    store = ContractStorageService(cfg)
    client = TestClient(
        create_app(config=cfg, storage_service=store, extractor=CR2PortfolioFixtureExtractor())
    )

    # 1. Verify all 5 default ERP projects across all 5 EnergyTechnology values are seeded
    proj_res = client.get("/api/v1/projects")
    assert proj_res.status_code == 200
    projects = proj_res.json()["projects"]
    assert len(projects) == 5
    seeded_techs = {p["energy_technology"] for p in projects}
    assert seeded_techs == {t.value for t in EnergyTechnology}

    # 2. Verify POST /api/v1/projects creates a new project in the registry
    create_res = client.post(
        "/api/v1/projects",
        json={
            "project_name": "Prairie Wind Expansion II",
            "erp_project_code": "ERP-WND-909",
            "energy_technology": "ONSHORE_WIND",
            "state_province": "IA",
            "county": "Story",
            "operating_llc_name": "Prairie Wind Expansion LLC",
        },
    )
    assert create_res.status_code == 200
    created_proj = create_res.json()
    assert created_proj["project_id"] == "prj_prairie_wind_expansion_ii"
    assert created_proj["erp_project_code"] == "ERP-WND-909"

    # 3. Upload Contract #1 for Arthur & Martha Pendelton under prj_sun_ridge_solar (SOLAR)
    up1 = client.post(
        "/api/v1/documents/upload",
        data={"project_id": "prj_sun_ridge_solar"},
        files={"file": ("Pendelton_Solar_Lease_Tract_A.pdf", b"%PDF-1.4 solar contract 1", "application/pdf")},
    )
    assert up1.status_code == 200
    b1 = up1.json()
    assert b1["document"]["project_id"] == "prj_sun_ridge_solar"
    assert b1["document"]["energy_technology"] == EnergyTechnology.SOLAR.value
    assert b1["document"]["grantor_landowner_name"] == "Arthur & Martha Pendelton"
    assert b1["document"]["grantee_entity_name"] == "Sun Ridge Solar Farm LLC"
    expected_lnd_id = _make_landowner_id("prj_sun_ridge_solar", "Arthur & Martha Pendelton")
    assert b1["document"]["landowner_id"] == expected_lnd_id

    # 4. Upload Contract #2 (Amendment/Easement) for the same Grantor under prj_sun_ridge_solar
    up2 = client.post(
        "/api/v1/documents/upload",
        data={"project_id": "prj_sun_ridge_solar"},
        files={"file": ("Pendelton_Solar_Access_Easement_Tract_B.pdf", b"%PDF-1.4 solar contract 2", "application/pdf")},
    )
    assert up2.status_code == 200

    # Verify Landowner roll-up (1 Landowner -> 2 Contracts, multi-parcel detected)
    lnd_res = client.get("/api/v1/projects/prj_sun_ridge_solar/landowners")
    assert lnd_res.status_code == 200
    lnds = lnd_res.json()["landowners"]
    assert len(lnds) == 1
    pendelton = lnds[0]
    assert pendelton["landowner_id"] == expected_lnd_id
    assert pendelton["landowner_name"] == "Arthur & Martha Pendelton"
    assert pendelton["contract_count"] == 2
    assert pendelton["is_multi_parcel"] is True
    assert "Parcel 1" in (pendelton["parcel_summary"] or "")
    assert "Parcel 2" in (pendelton["parcel_summary"] or "")

    # Verify Project roll-up counters
    solar_proj = next(
        p for p in client.get("/api/v1/projects").json()["projects"]
        if p["project_id"] == "prj_sun_ridge_solar"
    )
    assert solar_proj["landowner_count"] == 1
    assert solar_proj["document_count"] == 2
    assert solar_proj["special_conditions_count"] == 4
    assert solar_proj["flagged_node_count"] == 2

    # Verify re-ingesting Contract #1 is idempotent and does not inflate roll-up counts
    up1_repeat = client.post(
        "/api/v1/documents/upload",
        data={"project_id": "prj_sun_ridge_solar"},
        files={"file": ("Pendelton_Solar_Lease_Tract_A.pdf", b"%PDF-1.4 solar contract 1", "application/pdf")},
    )
    assert up1_repeat.status_code == 200
    solar_proj_after_repeat = next(
        p for p in client.get("/api/v1/projects").json()["projects"]
        if p["project_id"] == "prj_sun_ridge_solar"
    )
    assert solar_proj_after_repeat["document_count"] == 2

    # 5. Verify Centralized Portfolio Search across technology, project, category (including CR-1/CR-2 alias), and keyword
    search_all_solar = client.get(
        "/api/v1/portfolio/search", params={"energy_technology": "SOLAR"}
    )
    assert search_all_solar.status_code == 200
    assert search_all_solar.json()["count"] == 4

    search_setback = client.get(
        "/api/v1/portfolio/search",
        params={
            "project_id": "prj_sun_ridge_solar",
            "constraint_category": "SETBACK_OR_BUFFER",
        },
    )
    assert search_setback.status_code == 200
    setback_results = search_setback.json()["results"]
    assert len(setback_results) == 2
    assert setback_results[0]["quantitative_metric"] == "300 feet"
    assert setback_results[0]["landowner_name"] == "Arthur & Martha Pendelton"
    assert setback_results[0]["erp_project_code"] == "ERP-SOL-002"

    # Verify bidirectional CR-1 <-> CR-2 category alias matching
    search_cr1_alias = client.get(
        "/api/v1/portfolio/search",
        params={
            "project_id": "prj_sun_ridge_solar",
            "constraint_category": "STRUCTURE_BARN_WELL_SETBACK",
        },
    )
    assert search_cr1_alias.status_code == 200
    assert search_cr1_alias.json()["count"] == 2

    search_keyword = client.get("/api/v1/portfolio/search", params={"q": "pecan"})
    assert search_keyword.status_code == 200
    kw_results = search_keyword.json()["results"]
    assert len(kw_results) == 2
    assert kw_results[0]["constraint_category"] == ConstraintCategory.CROP_OR_TIMBER_COMPENSATION.value

    # 6. Verify PATCH clause review cascades to ProjectRow.flagged_node_count and SpecialConditionRow.hitl_status
    doc1_id = b1["document"]["document_id"]
    patch_res = client.patch(
        f"/api/v1/documents/{doc1_id}/clauses/BODY.8",
        json={"hitl_status": "APPROVED_BY_HUMAN", "reviewed_by": "solar_pm"},
    )
    assert patch_res.status_code == 200
    solar_proj_after_patch = next(
        p for p in client.get("/api/v1/projects").json()["projects"]
        if p["project_id"] == "prj_sun_ridge_solar"
    )
    assert solar_proj_after_patch["flagged_node_count"] == 1
    approved_search = client.get(
        "/api/v1/portfolio/search",
        params={"project_id": "prj_sun_ridge_solar", "hitl_status": "APPROVED_BY_HUMAN"},
    )
    assert approved_search.status_code == 200
    assert approved_search.json()["count"] == 2

    # 7. Verify canonical corporate suffix matching, Multi-Contract Stack transition, and batch _list_special_conditions_for_documents
    from contract_parser.gemini_parser import canonical_party_key

    assert canonical_party_key("Tallulah Pines Timber Co., LLC") == canonical_party_key(
        "Tallulah Pines Timber"
    )
    doc2_id = up2.json()["document"]["document_id"]
    batch_scs = store._list_special_conditions_for_documents({doc1_id, doc2_id})
    assert len(batch_scs.get(doc1_id, [])) == 2
    assert len(batch_scs.get(doc2_id, [])) == 2

    b_llc = store.get_bundle(doc1_id).model_copy(deep=True)
    b_llc.document = b_llc.document.model_copy(
        update={
            "document_id": "doc_timber_1",
            "project_id": "prj_cedar_lantern_wind",
            "landowner_id": "lnd_unassigned",
            "grantor_landowner_name": "Tallulah Pines Timber, LLC",
        }
    )
    b_llc.clauses = [
        c for c in b_llc.clauses if not c.node_id.startswith("EXHIBIT_A.PARCEL_")
    ]
    store.persist_bundle(b_llc)
    _, _, lnd_1 = store.bind_document_to_portfolio(
        b_llc, project_id="prj_cedar_lantern_wind"
    )
    assert lnd_1.parcel_summary == "Single Parcel / Standard Agreement"

    b_plain = b_llc.model_copy(deep=True)
    b_plain.document = b_plain.document.model_copy(
        update={
            "document_id": "doc_timber_2",
            "grantor_landowner_name": "Tallulah Pines Timber",
        }
    )
    store.persist_bundle(b_plain)
    _, _, lnd_2 = store.bind_document_to_portfolio(
        b_plain, project_id="prj_cedar_lantern_wind"
    )
    assert lnd_2.landowner_id == lnd_1.landowner_id
    assert lnd_2.contract_count == 2
    assert lnd_2.parcel_summary == "Multi-Contract Stack (2 Agreements)"

    b_third = b_llc.model_copy(deep=True)
    b_third.document = b_third.document.model_copy(
        update={
            "document_id": "doc_timber_3",
            "grantor_landowner_name": "Tallulah Pines Timber Co., LLC",
        }
    )
    store.persist_bundle(b_third)
    _, _, lnd_3 = store.bind_document_to_portfolio(
        b_third, project_id="prj_cedar_lantern_wind"
    )
    assert lnd_3.landowner_id == lnd_1.landowner_id
    assert lnd_3.contract_count == 3
    assert lnd_3.parcel_summary == "Multi-Contract Stack (3 Agreements)"


def test_cr3_subcontractor_dnd_checklist_and_signoff(tmp_path: Path) -> None:
    """Verify CR-3 Subcontractor Field Crew DND Checklist synthesis, HITL safety gate, and tailgate sign-off (CR3-UT-1, CR3-IT-1, CR3-IT-2)."""
    # 1. CR3-UT-1: Verify deterministic trade & severity classification across all 17 ConstraintCategory values
    all_categories = list(ConstraintCategory)
    assert len(all_categories) == 17
    for idx, cat in enumerate(all_categories, start=1):
        sc = SpecialConditionRow(
            condition_id=f"SC_{idx:03d}",
            document_id="doc_ut",
            project_id="prj_cedar_lantern_wind",
            landowner_id="lnd_ut",
            node_id="BODY.3",
            canonical_path="BODY.3",
            constraint_category=cat,
            target_asset_or_area="historic stone barn",
            actionable_obligation_summary="Do not grade within 250 feet of the historic stone barn.",
            quantitative_metric="250 feet" if idx % 2 == 1 else None,
            temporal_restriction="October 15 - December 1" if idx % 3 == 0 else None,
            penalty_or_consequence="$5,000 per tree" if idx % 5 == 0 else None,
            verbatim_excerpt="Grantee shall not grade within 250 feet of the historic stone barn.",
            page_number=2,
            hitl_status=HITLStatus.FLAGGED_FOR_REVIEW if idx % 2 == 1 else HITLStatus.APPROVED_BY_HUMAN,
        )
        trade, severity, directive_title = classify_dnd_condition(sc)
        assert isinstance(trade, ConstructionTrade)
        assert isinstance(severity, DNDSeverityLevel)
        assert directive_title.startswith(
            ("DO NOT DISTURB:", "SEASONAL BLACKOUT:", "MANDATORY PROTOCOL:")
        )

        item = build_dnd_checklist_item(sc)
        assert item.construction_trade == trade
        assert item.severity_level == severity
        assert item.field_directive_title == directive_title
        if sc.hitl_status == HITLStatus.APPROVED_BY_HUMAN:
            assert item.dispatch_clearance == DNDDispatchClearance.CLEARED_FOR_DISPATCH
        else:
            assert item.dispatch_clearance == DNDDispatchClearance.HOLD_VERIFY_WITH_LAND_AGENT

    # Verify specific trade and severity mappings
    fencing_sc = SpecialConditionRow(
        condition_id="SC_FENCE",
        node_id="BODY.3.b",
        canonical_path="BODY.3.b",
        constraint_category=ConstraintCategory.GATES_FENCING_OR_LIVESTOCK,
        target_asset_or_area="North Pasture Gate",
        actionable_obligation_summary="Keep North Pasture Gate locked at all times to prevent cattle escape.",
        verbatim_excerpt="Keep North Pasture Gate locked at all times.",
        page_number=2,
    )
    f_trade, f_sev, f_dir = classify_dnd_condition(fencing_sc)
    assert f_trade == ConstructionTrade.ACCESS_FENCING_GATES
    assert f_sev == DNDSeverityLevel.MANDATORY_PROTOCOL
    assert f_dir.startswith("MANDATORY PROTOCOL:")

    seasonal_sc = SpecialConditionRow(
        condition_id="SC_SEASONAL",
        node_id="BODY.3.b",
        canonical_path="BODY.3.b",
        constraint_category=ConstraintCategory.CONSTRUCTION_OR_BLACKOUT_WINDOW,
        target_asset_or_area="Deer Hunting Season Parcel 4",
        actionable_obligation_summary="Suspend heavy equipment operations during November rifle season.",
        temporal_restriction="November 15 - November 30",
        verbatim_excerpt="No construction during November 15 - November 30 rifle hunting window.",
        page_number=2,
    )
    s_trade, s_sev, s_dir = classify_dnd_condition(seasonal_sc)
    assert s_sev == DNDSeverityLevel.SEASONAL_BLACKOUT
    assert s_dir.startswith("SEASONAL BLACKOUT:")

    penalty_sc = SpecialConditionRow(
        condition_id="SC_PENALTY",
        node_id="BODY.3.c",
        canonical_path="BODY.3.c",
        constraint_category=ConstraintCategory.FINANCIAL_PENALTY_LIQUIDATED_DAMAGES,
        target_asset_or_area="merchantable oak timber",
        actionable_obligation_summary="Compensate landowner for any merchantable oak timber cut.",
        penalty_or_consequence="$1,500 per oak tree",
        verbatim_excerpt="Grantee shall pay $1,500 per oak tree removed.",
        page_number=2,
    )
    p_trade, p_sev, p_dir = classify_dnd_condition(penalty_sc)
    assert p_trade == ConstructionTrade.CLEARING_VEGETATION
    assert p_sev == DNDSeverityLevel.RED_ZONE_NO_GO
    assert p_dir.startswith("DO NOT DISTURB:")

    # 2. CR3-IT-1 & CR3-IT-2: End-to-end API, HITL Safety Gate Interlock, and Tailgate Sign-Off Persistence
    cr3_clauses = build_synthetic_accommodation_fixture().clauses + [
        ClauseRow(
            node_id="BODY.7.a",
            parent_node_id=None,
            sibling_order=7,
            document_zone=DocumentZone.BODY,
            canonical_path="BODY.7.a",
            depth=1,
            clause_label="7(a)",
            numbering_scheme=NumberingScheme.ALPHA_LOWER,
            is_inline_clause=False,
            verbatim_text="7(a) Grantee shall not cut or trim any pecan or oak trees in the Pecan Grove (subject to a $5,000 per tree liquidated damages penalty), and no construction or grading shall occur during deer hunting season from November 15 through December 1.",
            reconstructed_context_text="7(a) Grantee shall not cut or trim any pecan or oak trees in the Pecan Grove (subject to a $5,000 per tree liquidated damages penalty), and no construction or grading shall occur during deer hunting season from November 15 through December 1.",
            page_start=4,
            page_end=4,
        ),
        ClauseRow(
            node_id="BODY.9",
            parent_node_id=None,
            sibling_order=9,
            document_zone=DocumentZone.BODY,
            canonical_path="BODY.9",
            depth=1,
            clause_label="9",
            numbering_scheme=NumberingScheme.INTEGER,
            clause_title="Water Well Setback and Gate Protocol",
            is_inline_clause=False,
            verbatim_text="9. Water Well Setback and Gate Protocol. Grantee shall not disturb or clear within 250 feet of the homestead water well or barn, and all cattle gates must be kept closed and locked.",
            reconstructed_context_text="9. Water Well Setback and Gate Protocol. Grantee shall not disturb or clear within 250 feet of the homestead water well or barn, and all cattle gates must be kept closed and locked.",
            page_start=4,
            page_end=4,
        ),
    ]
    cr3_raw_scs = [
        SpecialConditionRow(
            node_id="BODY.7.a",
            constraint_category=ConstraintCategory.TREE_VEGETATION_PROTECTION,
            target_asset_or_area="pecan or oak trees in the Pecan Grove",
            actionable_obligation_summary="Do not cut or trim any pecan or oak trees in the Pecan Grove.",
            penalty_or_consequence="$5,000 per tree",
            verbatim_excerpt="Grantee shall not cut or trim any pecan or oak trees in the Pecan Grove",
            page_number=4,
        ),
        SpecialConditionRow(
            node_id="BODY.7.a",
            constraint_category=ConstraintCategory.TIMING_NOISE_HUNTING_BLACKOUT,
            target_asset_or_area="construction or grading",
            actionable_obligation_summary="No construction or grading during deer hunting season from November 15 through December 1.",
            temporal_restriction="November 15 through December 1",
            verbatim_excerpt="no construction or grading shall occur during deer hunting season from November 15 through December 1.",
            page_number=4,
        ),
    ]

    class CR3DndFixtureExtractor:
        def extract(
            self,
            *,
            document_id: str,
            gcs_pdf_uri: str,
            pdf_bytes: bytes | None = None,
            project_id: str = "prj_cedar_lantern_wind",
            landowner_id: str = "lnd_unassigned",
        ) -> GeminiContractExtraction:
            del pdf_bytes
            return normalize_and_enrich_extraction(
                extraction=GeminiContractExtraction(
                    page_count=4,
                    clauses=cr3_clauses,
                    special_conditions=cr3_raw_scs,
                ),
                document_id=document_id,
                gcs_pdf_uri=gcs_pdf_uri,
                project_id=project_id,
                landowner_id=landowner_id,
            )

    cfg = PipelineConfig(
        local_data_dir=tmp_path / "cr3_data",
        use_cloud_storage=False,
        use_bigquery=False,
    )
    store = ContractStorageService(cfg)
    extractor = CR3DndFixtureExtractor()
    client = TestClient(
        create_app(config=cfg, storage_service=store, extractor=extractor)
    )
    up_res = client.post(
        "/api/v1/documents/upload",
        data={"project_id": "prj_cedar_lantern_wind"},
        files={"file": ("Cedar_Lantern_DND_Lease.pdf", b"%PDF-1.4 cr3 dnd test", "application/pdf")},
    )
    assert up_res.status_code == 200
    doc_id = up_res.json()["document"]["document_id"]

    # Verify index.html serves the Field Crew DND tab button
    ui_res = client.get("/")
    assert ui_res.status_code == 200
    assert 'id="tab-dnd"' in ui_res.text
    assert 'data-tab-id="tabBtnDndChecklist"' in ui_res.text
    assert "Field Crew DND" in ui_res.text

    # Initial DND checklist should be in HOLD_PENDING_HITL because special conditions start as FLAGGED_FOR_REVIEW
    dnd_res = client.get(f"/api/v1/documents/{doc_id}/dnd-checklist")
    assert dnd_res.status_code == 200
    dnd_data = dnd_res.json()
    assert dnd_data["document_id"] == doc_id
    assert dnd_data["project_id"] == "prj_cedar_lantern_wind"
    assert dnd_data["erp_project_code"] == "ERP-WND-001"
    assert dnd_data["dispatch_readiness"] == DNDDispatchReadiness.HOLD_PENDING_HITL.value
    assert dnd_data["total_items"] == 3
    assert dnd_data["cleared_count"] == 0
    assert dnd_data["hold_count"] == 3
    assert len(dnd_data["items"]) == 3
    assert all(
        item["dispatch_clearance"] == DNDDispatchClearance.HOLD_VERIFY_WITH_LAND_AGENT.value
        for item in dnd_data["items"]
    )

    # Filter by construction_trade=BLASTING_TRENCHING_FOUNDATION: items list is filtered, but contract-level readiness & counts stay unfiltered
    dnd_filtered = client.get(
        f"/api/v1/documents/{doc_id}/dnd-checklist",
        params={"construction_trade": "BLASTING_TRENCHING_FOUNDATION"},
    )
    assert dnd_filtered.status_code == 200
    filtered_data = dnd_filtered.json()
    assert len(filtered_data["items"]) == 1
    assert filtered_data["items"][0]["construction_trade"] == "BLASTING_TRENCHING_FOUNDATION"
    assert filtered_data["total_items"] == 3
    assert filtered_data["hold_count"] == 3
    assert filtered_data["dispatch_readiness"] == DNDDispatchReadiness.HOLD_PENDING_HITL.value

    # Filter by severity_level=SEASONAL_BLACKOUT while preserving unfiltered contract readiness
    dnd_sev_filtered = client.get(
        f"/api/v1/documents/{doc_id}/dnd-checklist",
        params={"severity_level": "SEASONAL_BLACKOUT"},
    )
    assert dnd_sev_filtered.status_code == 200
    sev_data = dnd_sev_filtered.json()
    assert sev_data["filtered_items_count"] == 1
    assert sev_data["items"][0]["severity_level"] == "SEASONAL_BLACKOUT"
    assert sev_data["dispatch_readiness"] == DNDDispatchReadiness.HOLD_PENDING_HITL.value

    # Verify HTTP 400 rejection on unknown condition_id per CR3-IT-2
    bad_signoff = client.post(
        f"/api/v1/documents/{doc_id}/dnd-checklist:signoff",
        json={
            "construction_trade": "CLEARING_VEGETATION",
            "subcontractor_company": "Apex Civil & Grading LLC",
            "foreman_name": "Travis Miller",
            "acknowledged_condition_ids": ["sc_UNKNOWN_999"],
        },
    )
    assert bad_signoff.status_code == 400
    assert "sc_UNKNOWN_999" in bad_signoff.json()["detail"]

    # Approve BODY.7.a via PATCH -> clears 2 conditions on BODY.7.a, 1 hold remains on BODY.9
    patch_7a = client.patch(
        f"/api/v1/documents/{doc_id}/clauses/BODY.7.a",
        json={"hitl_status": "APPROVED_BY_HUMAN", "reviewed_by": "land_agent_1"},
    )
    assert patch_7a.status_code == 200

    dnd_partial = client.get(f"/api/v1/documents/{doc_id}/dnd-checklist").json()
    assert dnd_partial["cleared_count"] == 2
    assert dnd_partial["hold_count"] == 1
    assert dnd_partial["dispatch_readiness"] == DNDDispatchReadiness.HOLD_PENDING_HITL.value

    # Record a pre-job tailgate briefing sign-off while 1 hold item is still present
    signoff_1 = client.post(
        f"/api/v1/documents/{doc_id}/dnd-checklist:signoff",
        json={
            "construction_trade": "CLEARING_VEGETATION",
            "subcontractor_company": "Apex Civil & Grading LLC",
            "foreman_name": "Travis Miller",
            "acknowledged_condition_ids": ["sc_BODY.7.a_1", "sc_BODY.7.a_2"],
            "briefing_notes": "Staked pecan grove perimeter and briefed November hunting blackout.",
        },
    )
    assert signoff_1.status_code == 200
    s1_bundle = signoff_1.json()
    assert len(s1_bundle["signoffs"]) == 1
    s1_row = s1_bundle["signoffs"][0]
    assert s1_row["subcontractor_company"] == "Apex Civil & Grading LLC"
    assert s1_row["foreman_name"] == "Travis Miller"
    assert s1_row["construction_trade"] == "CLEARING_VEGETATION"
    assert s1_row["dispatch_readiness_at_signoff"] == DNDDispatchReadiness.HOLD_PENDING_HITL.value
    assert s1_row["acknowledged_count"] == 2
    assert s1_row["briefing_notes"] == "Staked pecan grove perimeter and briefed November hunting blackout."

    # Approve remaining clause BODY.9 -> all 3 DND items are now CLEARED_FOR_DISPATCH and contract is READY_FOR_DISPATCH
    patch_9 = client.patch(
        f"/api/v1/documents/{doc_id}/clauses/BODY.9",
        json={"hitl_status": "APPROVED_BY_HUMAN", "reviewed_by": "land_agent_1"},
    )
    assert patch_9.status_code == 200

    dnd_ready = client.get(f"/api/v1/documents/{doc_id}/dnd-checklist").json()
    assert dnd_ready["cleared_count"] == 3
    assert dnd_ready["hold_count"] == 0
    assert dnd_ready["dispatch_readiness"] == DNDDispatchReadiness.READY_FOR_DISPATCH.value
    assert len(dnd_ready["signoffs"]) == 1

    # Record a full-clearance tailgate sign-off
    signoff_2 = client.post(
        f"/api/v1/documents/{doc_id}/dnd-checklist:signoff",
        json={
            "construction_trade": "GENERAL_SITE_OPERATIONS",
            "subcontractor_company": "Midwest Wind Erectors Inc.",
            "foreman_name": "Elena Rostova",
            "acknowledged_condition_ids": ["sc_BODY.7.a_1", "sc_BODY.7.a_2", "sc_BODY.9_1"],
            "briefing_notes": "All three DND conditions cleared and briefed at morning tailgate.",
        },
    )
    assert signoff_2.status_code == 200
    s2_bundle = signoff_2.json()
    assert len(s2_bundle["signoffs"]) == 2
    s2_row = next(
        s for s in s2_bundle["signoffs"]
        if s["subcontractor_company"] == "Midwest Wind Erectors Inc."
    )
    assert s2_row["dispatch_readiness_at_signoff"] == DNDDispatchReadiness.READY_FOR_DISPATCH.value
    assert s2_row["acknowledged_count"] == 3

    # Verify dnd_checklist_signoffs.csv export endpoint
    csv_res = client.get(f"/api/v1/documents/{doc_id}/export/dnd_checklist_signoffs.csv")
    assert csv_res.status_code == 200
    csv_text = csv_res.text
    assert "signoff_id,document_id,project_id,landowner_id,subcontractor_company" in csv_text
    assert "Apex Civil & Grading LLC" in csv_text
    assert "Midwest Wind Erectors Inc." in csv_text


def test_cr4_bigquery_data_agent_urn_and_chat_proxy(
    tmp_path: Path, monkeypatch
) -> None:
    """Verify CR-4 BigQuery Data Agent URN resolution, event stream normalization, reverse filename links, and FastAPI proxy routes (CR4-UT-1, CR4-UT-2, CR4-IT-1)."""
    # 1. CR4-UT-1: Verify URN and resource path resolution
    default_cfg = PipelineConfig()
    proj, loc, resource = default_cfg.resolve_data_agent_resource()
    assert proj == "255093976233"
    assert loc == "us"
    assert (
        resource
        == "projects/255093976233/locations/us/dataAgents/agent_f8454b44-a4aa-4c94-accf-245b5e6b1f11"
    )

    res_cfg = PipelineConfig(
        bq_data_agent_urn="projects/255093976233/locations/us/dataAgents/agent_f8454b44-a4aa-4c94-accf-245b5e6b1f11"
    )
    assert res_cfg.resolve_data_agent_resource() == (
        "255093976233",
        "us",
        "projects/255093976233/locations/us/dataAgents/agent_f8454b44-a4aa-4c94-accf-245b5e6b1f11",
    )

    # 2. CR4-UT-2: Verify parse_data_agent_events on multi-event geminidataanalytics stream
    raw_events = [
        {
            "systemMessage": {
                "text": {
                    "parts": ["Inspecting documents_latest and special_conditions_latest views..."],
                    "textType": "THOUGHT",
                }
            }
        },
        {
            "systemMessage": {
                "data": {
                    "generatedSql": (
                        "SELECT document_id, filename, special_conditions_count "
                        "FROM `pr-tftest.contract_intelligence.documents_latest` "
                        "ORDER BY special_conditions_count DESC"
                    )
                }
            }
        },
        {
            "systemMessage": {
                "data": {
                    "result": {
                        "schema": {
                            "fields": [
                                {"name": "document_id"},
                                {"name": "filename"},
                                {"name": "special_conditions_count"},
                            ]
                        },
                        "data": [
                            {
                                "document_id": "doc_demo_dnd_pendelton",
                                "filename": "Pendelton_Wind_Lease.pdf",
                                "special_conditions_count": 4,
                            }
                        ],
                    }
                }
            }
        },
        {
            "systemMessage": {
                "text": {
                    "parts": [
                        "Contract doc_demo_dnd_pendelton (Pendelton_Wind_Lease.pdf) has 4 special conditions."
                    ],
                    "textType": "FINAL_RESPONSE",
                }
            }
        },
        {
            "systemMessage": {
                "text": {
                    "parts": [
                        "Which special conditions have financial penalties?",
                        "Show all Red Zone DND items for Pendelton_Wind_Lease.pdf",
                    ],
                    "textType": "FOLLOWUP_QUESTIONS",
                }
            }
        },
    ]

    parsed = parse_data_agent_events(
        raw_events,
        question="Which contract has the most special conditions?",
        agent_urn=DEFAULT_BQ_DATA_AGENT_URN,
        data_agent_resource=resource,
    )
    assert (
        parsed.answer
        == "Contract doc_demo_dnd_pendelton (Pendelton_Wind_Lease.pdf) has 4 special conditions."
    )
    assert "documents_latest" in (parsed.generated_sql or "")
    assert parsed.columns == [
        "document_id",
        "filename",
        "special_conditions_count",
    ]
    assert len(parsed.rows) == 1
    assert parsed.thoughts == [
        "Inspecting documents_latest and special_conditions_latest views..."
    ]
    assert len(parsed.followup_questions) == 2
    assert len(parsed.document_links) == 1
    assert parsed.document_links[0].document_id == "doc_demo_dnd_pendelton"
    assert parsed.document_links[0].filename == "Pendelton_Wind_Lease.pdf"
    assert (
        parsed.document_links[0].pdf_url
        == "/api/v1/documents/doc_demo_dnd_pendelton/pdf"
    )

    # Verify reverse filename-to-document_id lookup and fallback row summary when FINAL_RESPONSE is omitted
    filename_only_events = [
        {
            "systemMessage": {
                "data": {
                    "generatedSql": "SELECT filename FROM `pr-tftest.contract_intelligence.documents_latest`",
                    "result": {
                        "schema": {"fields": [{"name": "filename"}]},
                        "data": [{"filename": "Cedar_Lantern_DND_Lease.pdf"}],
                    },
                }
            }
        }
    ]
    parsed_fn_only = parse_data_agent_events(
        filename_only_events,
        question="List all filenames.",
        agent_urn=DEFAULT_BQ_DATA_AGENT_URN,
        data_agent_resource=resource,
        doc_filename_lookup={"doc_cedar_001": "Cedar_Lantern_DND_Lease.pdf"},
    )
    assert parsed_fn_only.answer == "Returned 1 row(s) from BigQuery."
    assert len(parsed_fn_only.document_links) == 1
    assert parsed_fn_only.document_links[0].document_id == "doc_cedar_001"
    assert (
        parsed_fn_only.document_links[0].filename
        == "Cedar_Lantern_DND_Lease.pdf"
    )
    assert (
        parsed_fn_only.document_links[0].pdf_url
        == "/api/v1/documents/doc_cedar_001/pdf"
    )

    # 3. CR4-IT-1: Verify FastAPI /api/v1/agent/info, /api/v1/agent/chat, and index.html launcher
    cfg = PipelineConfig(
        local_data_dir=tmp_path / "cr4_data",
        use_cloud_storage=False,
        use_bigquery=False,
    )
    store = ContractStorageService(cfg)

    class CR4FixtureExtractor:
        def extract(
            self,
            *,
            document_id: str,
            gcs_pdf_uri: str,
            pdf_bytes: bytes | None = None,
            project_id: str = "prj_cedar_lantern_wind",
            landowner_id: str = "lnd_unassigned",
        ) -> GeminiContractExtraction:
            del pdf_bytes
            return normalize_and_enrich_extraction(
                extraction=build_synthetic_accommodation_fixture(),
                document_id=document_id,
                gcs_pdf_uri=gcs_pdf_uri,
                project_id=project_id,
                landowner_id=landowner_id,
            )

    client = TestClient(
        create_app(
            config=cfg,
            storage_service=store,
            extractor=CR4FixtureExtractor(),
        )
    )
    up_res = client.post(
        "/api/v1/documents/upload",
        data={"project_id": "prj_cedar_lantern_wind"},
        files={
            "file": (
                "Synthetic_Accommodation_Agreement.pdf",
                b"%PDF-1.4 cr4 agent test",
                "application/pdf",
            )
        },
    )
    assert up_res.status_code == 200
    uploaded_doc_id = up_res.json()["document"]["document_id"]

    # Verify index.html contains the login overlay, bottom-right launcher and chat window
    ui_res = client.get("/")
    assert ui_res.status_code == 200
    assert 'id="login-overlay"' in ui_res.text
    assert 'id="login-username"' in ui_res.text
    assert 'id="login-password"' in ui_res.text
    assert "google123" in ui_res.text
    assert 'id="bq-agent-fab"' in ui_res.text
    assert 'id="bq-agent-window"' in ui_res.text
    assert "Ask Agent" in ui_res.text
    assert "Contract Intelligence Agent" in ui_res.text

    # Verify GET /api/v1/agent/info
    info_res = client.get("/api/v1/agent/info")
    assert info_res.status_code == 200
    info_json = info_res.json()
    assert info_json["project"] == "255093976233"
    assert info_json["location"] == "us"
    assert info_json["data_agent_resource"] == resource

    # Verify POST /api/v1/agent/chat rejects blank questions with HTTP 400
    blank_res = client.post("/api/v1/agent/chat", json={"question": "   "})
    assert blank_res.status_code == 400

    # Mock google.auth.default and requests.post to verify full query_bigquery_data_agent execution
    import google.auth
    import requests as http_requests

    class DummyCreds:
        token = "mock-adc-bearer-token"

        def refresh(self, req: object) -> None:
            del req

    class DummyHttpResponse:
        status_code = 200
        text = "OK"

        def json(self) -> list[dict[str, object]]:
            return [
                {
                    "systemMessage": {
                        "data": {
                            "generatedSql": "SELECT filename FROM `pr-tftest.contract_intelligence.documents_latest`",
                            "result": {
                                "schema": {"fields": [{"name": "filename"}]},
                                "data": [
                                    {
                                        "filename": "Synthetic_Accommodation_Agreement.pdf"
                                    }
                                ],
                            },
                        }
                    }
                },
                {
                    "systemMessage": {
                        "text": {
                            "parts": [
                                "Found 1 contract: Synthetic_Accommodation_Agreement.pdf."
                            ],
                            "textType": "FINAL_RESPONSE",
                        }
                    }
                },
            ]

    monkeypatch.setattr(
        google.auth, "default", lambda scopes=None: (DummyCreds(), "pr-tftest")
    )
    monkeypatch.setattr(
        http_requests,
        "post",
        lambda url, headers=None, json=None, timeout=None: DummyHttpResponse(),
    )

    chat_res = client.post(
        "/api/v1/agent/chat",
        json={"question": "List all documents and their filenames."},
    )
    assert chat_res.status_code == 200
    chat_json = chat_res.json()
    assert (
        chat_json["answer"]
        == "Found 1 contract: Synthetic_Accommodation_Agreement.pdf."
    )
    assert len(chat_json["document_links"]) == 1
    assert chat_json["document_links"][0]["document_id"] == uploaded_doc_id
    assert (
        chat_json["document_links"][0]["filename"]
        == "Synthetic_Accommodation_Agreement.pdf"
    )
    assert (
        chat_json["document_links"][0]["pdf_url"]
        == f"/api/v1/documents/{uploaded_doc_id}/pdf"
    )


def test_clause_hierarchical_document_ordering() -> None:
    """Verify clauses are ordered in strict document tree reading order rather than random or grouped by depth."""
    from contract_parser.schemas import (
        DocumentRegistryRow,
        ParsedContractBundle,
        sort_clauses_in_document_order,
    )

    c_sec2 = ClauseRow(
        document_id="doc_order_test",
        node_id="BODY.2",
        canonical_path="BODY.2",
        clause_label="Section 2",
        clause_title="Taxes",
        numbering_scheme=NumberingScheme.INTEGER,
        depth=1,
        sibling_order=2,
        parent_node_id=None,
        document_zone=DocumentZone.BODY,
        page_start=2,
        page_end=2,
        verbatim_text="Section 2 text.",
        reconstructed_context_text="Section 2 text.",
    )
    c_sec1 = ClauseRow(
        document_id="doc_order_test",
        node_id="BODY.1",
        canonical_path="BODY.1",
        clause_label="Section 1",
        clause_title="Lease Term",
        numbering_scheme=NumberingScheme.INTEGER,
        depth=1,
        sibling_order=1,
        parent_node_id=None,
        document_zone=DocumentZone.BODY,
        page_start=1,
        page_end=1,
        verbatim_text="Section 1 text.",
        reconstructed_context_text="Section 1 text.",
    )
    c_sub1_1 = ClauseRow(
        document_id="doc_order_test",
        node_id="BODY.1.1",
        canonical_path="BODY.1.1",
        clause_label="1.1",
        clause_title="Initial Term",
        numbering_scheme=NumberingScheme.DECIMAL,
        depth=2,
        sibling_order=1,
        parent_node_id=None,  # Missing parent_node_id, should be inferred from canonical_path
        document_zone=DocumentZone.BODY,
        page_start=1,
        page_end=1,
        verbatim_text="1.1 text.",
        reconstructed_context_text="1.1 text.",
    )
    c_sub1_1_a = ClauseRow(
        document_id="doc_order_test",
        node_id="BODY.1.1.a",
        canonical_path="BODY.1.1.a",
        clause_label="(a)",
        clause_title="Notice",
        numbering_scheme=NumberingScheme.ALPHA_LOWER,
        depth=3,
        sibling_order=1,
        parent_node_id="BODY.1.1",
        document_zone=DocumentZone.BODY,
        page_start=1,
        page_end=1,
        verbatim_text="(a) notice text.",
        reconstructed_context_text="(a) notice text.",
    )
    c_sub1_2 = ClauseRow(
        document_id="doc_order_test",
        node_id="BODY.1.2",
        canonical_path="BODY.1.2",
        clause_label="1.2",
        clause_title="Renewal Term",
        numbering_scheme=NumberingScheme.DECIMAL,
        depth=2,
        sibling_order=2,
        parent_node_id="BODY.1",
        document_zone=DocumentZone.BODY,
        page_start=1,
        page_end=2,
        verbatim_text="1.2 text.",
        reconstructed_context_text="1.2 text.",
    )
    c_preamble = ClauseRow(
        document_id="doc_order_test",
        node_id="PREAMBLE.1",
        canonical_path="PREAMBLE.1",
        clause_label="Preamble",
        numbering_scheme=NumberingScheme.UNNUMBERED,
        depth=1,
        sibling_order=1,
        parent_node_id=None,
        document_zone=DocumentZone.PREAMBLE,
        page_start=1,
        page_end=1,
        verbatim_text="This agreement is entered into...",
        reconstructed_context_text="This agreement is entered into...",
    )
    c_recital_a = ClauseRow(
        document_id="doc_order_test",
        node_id="RECITALS.A",
        canonical_path="RECITALS.A",
        clause_label="Recital A",
        numbering_scheme=NumberingScheme.ALPHA_UPPER,
        depth=1,
        sibling_order=1,
        parent_node_id=None,
        document_zone=DocumentZone.RECITALS,
        page_start=1,
        page_end=1,
        verbatim_text="WHEREAS...",
        reconstructed_context_text="WHEREAS...",
    )
    c_exhibit_a = ClauseRow(
        document_id="doc_order_test",
        node_id="EXHIBIT_A",
        canonical_path="EXHIBIT_A",
        clause_label="Exhibit A",
        numbering_scheme=NumberingScheme.NAMED_HEADER,
        depth=1,
        sibling_order=1,
        parent_node_id=None,
        document_zone=DocumentZone.EXHIBIT_OR_SCHEDULE,
        page_start=3,
        page_end=3,
        verbatim_text="Legal Description...",
        reconstructed_context_text="Legal Description...",
    )

    scrambled = [
        c_sub1_1_a,
        c_sec2,
        c_exhibit_a,
        c_sub1_2,
        c_recital_a,
        c_sec1,
        c_preamble,
        c_sub1_1,
    ]

    ordered = sort_clauses_in_document_order(scrambled)
    ordered_ids = [c.node_id for c in ordered]

    expected_ids = [
        "PREAMBLE.1",
        "RECITALS.A",
        "BODY.1",
        "BODY.1.1",
        "BODY.1.1.a",
        "BODY.1.2",
        "BODY.2",
        "EXHIBIT_A",
    ]
    assert ordered_ids == expected_ids, f"Expected {expected_ids} but got {ordered_ids}"

    bundle = ParsedContractBundle(
        document=DocumentRegistryRow(
            document_id="doc_order_test",
            filename="order_test.pdf",
            gcs_pdf_uri="gs://bucket/raw/doc_order_test/order_test.pdf",
            gcs_export_prefix="gs://bucket/exports/doc_order_test/",
            page_count=3,
            contracting_parties_json="[]",
        ),
        clauses=scrambled,
        defined_terms=[],
        exhibits_catalog=[],
    )
    assert [c.node_id for c in bundle.clauses] == expected_ids
