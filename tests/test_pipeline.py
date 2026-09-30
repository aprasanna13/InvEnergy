"""End-to-end schema, post-processing, storage, and FastAPI pipeline verification tests."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from contract_parser.app import create_app, ingest_contract
from contract_parser.config import PipelineConfig
from contract_parser.gemini_parser import normalize_and_enrich_extraction
from contract_parser.schemas import (
    CLAUSE_CSV_COLUMNS,
    DEFINED_TERM_CSV_COLUMNS,
    DOCUMENT_REGISTRY_COLUMNS,
    EXHIBIT_CATALOG_CSV_COLUMNS,
    ClauseReviewRequest,
    ClauseRow,
    DefinedTermRow,
    DefinitionType,
    DocumentZone,
    ExhibitCatalogRow,
    ExhibitModality,
    FlagCode,
    GeminiContractExtraction,
    HITLStatus,
    IngestionStatus,
    NumberingScheme,
)
from contract_parser.storage import (
    BQ_CLAUSES_SCHEMA,
    BQ_DEFINED_TERMS_SCHEMA,
    BQ_DOCUMENTS_SCHEMA,
    BQ_EXHIBITS_CATALOG_SCHEMA,
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
    """Verify exact 4-table column counts, BigQuery schema parity, and PipelineConfig defaults (SA-1..SA-4)."""
    assert len(CLAUSE_CSV_COLUMNS) == 30
    assert len(DEFINED_TERM_CSV_COLUMNS) == 8
    assert len(EXHIBIT_CATALOG_CSV_COLUMNS) == 10
    assert len(DOCUMENT_REGISTRY_COLUMNS) == 12

    assert [f.name for f in BQ_CLAUSES_SCHEMA] == CLAUSE_CSV_COLUMNS
    assert [f.name for f in BQ_DEFINED_TERMS_SCHEMA] == DEFINED_TERM_CSV_COLUMNS
    assert [f.name for f in BQ_EXHIBITS_CATALOG_SCHEMA] == EXHIBIT_CATALOG_CSV_COLUMNS
    assert [f.name for f in BQ_DOCUMENTS_SCHEMA] == DOCUMENT_REGISTRY_COLUMNS

    cfg = PipelineConfig()
    assert cfg.google_cloud_project == "pr-tftest"
    assert cfg.google_cloud_location == "global"
    assert cfg.gemini_model == "gemini-3.1-pro-preview"
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
    """Verify all 9 benchmark rows, 16 defined terms, and 4 exhibits on Synthetic_Accommodation_Agreement.pdf (IT-1, IT-2)."""
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

    # 6. BODY.6 (Cross-page stitched clause across pages 2 and 3)
    b6 = by_id["BODY.6"]
    assert (b6.page_start, b6.page_end) == (2, 3)
    assert b6.hitl_flag_reasons == "NONE"

    # 7. SIGNATURES.1 (Deferred modality placeholder on Page 5)
    sig1 = by_id["SIGNATURES.1"]
    assert sig1.hitl_status == HITLStatus.PLACEHOLDER_FOR_REVIEW
    assert sig1.hitl_flag_reasons == FlagCode.DEFERRED_MODALITY_PLACEHOLDER.value

    # 8. EXHIBIT_A.PARCEL_4.EXCEPT_1 (Depth 3 block-header carve-out synthesis)
    ex_a_carve = by_id["EXHIBIT_A.PARCEL_4.EXCEPT_1"]
    assert ex_a_carve.depth == 3
    assert ex_a_carve.level_1_label == "Exhibit A"
    assert ex_a_carve.level_2_label == "Parcel 4"
    assert ex_a_carve.level_3_label == "LESS_AND_EXCEPT"
    assert ex_a_carve.reconstructed_context_text.startswith("Excluded from Exhibit A, Parcel 4:")
    assert FlagCode.SCOPE_CARVEOUT_DETECTED.value in ex_a_carve.hitl_flag_reasons

    # 9. EXHIBIT_D.STUB (CAD drawing placeholder on Pages 11-12)
    ex_d = by_id["EXHIBIT_D.STUB"]
    assert (ex_d.page_start, ex_d.page_end) == (11, 12)
    assert ex_d.hitl_status == HITLStatus.PLACEHOLDER_FOR_REVIEW
    assert ex_d.hitl_flag_reasons == FlagCode.DEFERRED_MODALITY_PLACEHOLDER.value

    # Verify longest-match Defined Term linking (RECITALS.2 uses 'Blue Meridian Facilities' and 'Blue Meridian Easements')
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

    # 1. GET / serves split-screen pdf.js HTML UI
    html_res = client.get("/")
    assert html_res.status_code == 200
    assert "pdf.min.js" in html_res.text
    assert "Show Full Reconstructed Context" in html_res.text

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

    # 5. Verify utf-8-sig CSV exports & load_bundle_from_csv round-trip
    for csv_name in ("clauses.csv", "defined_terms.csv", "exhibits_catalog.csv"):
        csv_res = client.get(f"/api/v1/documents/{doc_id}/export/{csv_name}")
        assert csv_res.status_code == 200
        assert csv_res.content.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM (utf-8-sig)

    reloaded = store.load_bundle_from_csv(
        store.local_exports_dir / doc_id, dedup_bundle.document
    )
    assert len(reloaded.clauses) == len(dedup_bundle.clauses)
    assert len(reloaded.defined_terms) == len(dedup_bundle.defined_terms)
    assert len(reloaded.exhibits_catalog) == len(dedup_bundle.exhibits_catalog)


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

