"""Hermetic Unit Tests for Option 3A Semantic Zone Multi-Pass Extraction (UT-1..UT-6)."""

from __future__ import annotations

import json
import pytest
from pydantic import ValidationError

from contract_parser.bundle_assembler import assemble_multi_pass_extraction
from contract_parser.schemas import (
    BodyPassExtraction,
    ClauseRow,
    ConstraintCategory,
    ContractStructureIndex,
    DefinedTermRow,
    DefinitionType,
    DocumentZone,
    ExhibitCatalogRow,
    ExhibitIndexEntry,
    ExhibitModality,
    ExhibitsPassExtraction,
    FlagCode,
    HITLStatus,
    NumberingScheme,
    SignerEntry,
    SpecialConditionRow,
    utc_now_iso,
)


def test_ut1_boundary_validation_and_clamping():
    """[UT-1] Verify ContractStructureIndex boundary validation and clamping."""
    # Test inverted body boundaries (start > end)
    idx = ContractStructureIndex(
        document_title="Sample Agreement",
        body_start_page=10,
        body_end_page=3,
        signature_start_page=12,
        signature_end_page=5,
    )
    assert idx.body_start_page == 10
    # Clamped to start page
    assert idx.body_end_page == 10
    assert idx.signature_end_page == 12

    # Test exhibit page clamping
    ex = ExhibitIndexEntry(
        exhibit_id="Exhibit A",
        exhibit_title="Legal Description",
        page_start=25,
        page_end=20,
    )
    assert ex.page_start == 25
    assert ex.page_end == 25


def test_ut2_zero_exhibits_guard():
    """[UT-2] Verify zero-exhibit handling in bundle assembler."""
    now_iso = utc_now_iso()
    idx = ContractStructureIndex(
        document_title="Simple Two-Party Agreement",
        grantor_landowner_name="Jane Doe",
        grantee_entity_name="Clean Energy LLC",
        effective_date="2024-01-01",
        has_recitals=False,
        body_start_page=1,
        body_end_page=3,
        signature_start_page=4,
        signature_end_page=4,
        signers=[
            SignerEntry(party_name="Jane Doe", signer_name="Jane Doe", page_number=4),
        ],
        exhibits=[],
    )

    body_pass = BodyPassExtraction(
        clauses=[
            ClauseRow(
                node_id="BODY.1",
                canonical_path="BODY.1",
                document_zone=DocumentZone.BODY,
                depth=1,
                clause_title="Grant of Rights",
                clause_label="1",
                numbering_scheme=NumberingScheme.INTEGER,
                page_start=1,
                page_end=2,
                verbatim_text="Section 1. Grant of Rights...",
                reconstructed_context_text="Section 1. Grant of Rights...",
                preamble_text="Section 1. Grant of Rights...",
                postamble_text=None,
                hitl_status=HITLStatus.VERIFIED_AUTO,
                hitl_flag_reasons="NONE",
                created_at=now_iso,
                updated_at=now_iso,
            )
        ],
        defined_terms=[],
        special_conditions=[],
    )

    exhibits_pass = ExhibitsPassExtraction(
        clauses=[],
        defined_terms=[],
        special_conditions=[],
        exhibits_catalog=[],
    )

    bundle = assemble_multi_pass_extraction(
        index=idx,
        body_pass=body_pass,
        exhibits_pass=exhibits_pass,
        total_pages=4,
    )

    assert bundle.page_count == 4
    assert len(bundle.exhibits_catalog) == 0
    # Synthesized PREAMBLE.1 + BODY.1 + SIGNATURES.1 = 3 clauses
    assert len(bundle.clauses) == 3
    node_ids = [c.node_id for c in bundle.clauses]
    assert "PREAMBLE.1" in node_ids
    assert "BODY.1" in node_ids
    assert "SIGNATURES.1" in node_ids


def test_ut3_pure_bundle_assembly_preamble_preservation():
    """[UT-3] Verify pure multi-pass bundle assembly preserves Pass 2 verbatim preamble."""
    now_iso = utc_now_iso()
    idx = ContractStructureIndex(
        document_title="Solar Lease Agreement",
        grantor_landowner_name="Green Farm LLC",
        grantee_entity_name="Invenergy Solar LLC",
        body_start_page=1,
        body_end_page=5,
        signature_start_page=6,
        signature_end_page=6,
    )

    verbatim_text = "SOLAR LEASE AGREEMENT between Green Farm LLC and Invenergy Solar LLC."
    body_pass = BodyPassExtraction(
        clauses=[
            ClauseRow(
                node_id="PREAMBLE.1",
                canonical_path="PREAMBLE.1",
                document_zone=DocumentZone.PREAMBLE,
                depth=1,
                clause_title="Preamble",
                clause_label="Preamble",
                numbering_scheme=NumberingScheme.UNNUMBERED,
                page_start=1,
                page_end=1,
                verbatim_text=verbatim_text,
                reconstructed_context_text=verbatim_text,
                preamble_text=verbatim_text,
                postamble_text=None,
                hitl_status=HITLStatus.VERIFIED_AUTO,
                hitl_flag_reasons="NONE",
                created_at=now_iso,
                updated_at=now_iso,
            ),
            ClauseRow(
                node_id="BODY.1",
                canonical_path="BODY.1",
                document_zone=DocumentZone.BODY,
                depth=1,
                clause_title="Lease Term",
                clause_label="1",
                numbering_scheme=NumberingScheme.INTEGER,
                page_start=1,
                page_end=2,
                verbatim_text="1. Term.",
                reconstructed_context_text="1. Term.",
                preamble_text="1. Term.",
                postamble_text=None,
                hitl_status=HITLStatus.VERIFIED_AUTO,
                hitl_flag_reasons="NONE",
                created_at=now_iso,
                updated_at=now_iso,
            ),
        ],
        defined_terms=[],
        special_conditions=[],
    )

    exhibits_pass = ExhibitsPassExtraction()

    bundle = assemble_multi_pass_extraction(
        index=idx,
        body_pass=body_pass,
        exhibits_pass=exhibits_pass,
        total_pages=6,
    )

    # Verbatim preamble from Pass 2 is preserved without being overwritten by synthesized fallback
    preamble = next(c for c in bundle.clauses if c.node_id == "PREAMBLE.1")
    assert preamble.verbatim_text == verbatim_text
    assert preamble.clause_title == "Preamble"


def test_ut4_visual_cad_stub_synthesis():
    """[UT-4] Verify deterministic visual CAD / map exhibit clause stub synthesis."""
    now_iso = utc_now_iso()
    idx = ContractStructureIndex(
        document_title="Transmission Right of Way",
        body_start_page=1,
        body_end_page=5,
        exhibits=[
            ExhibitIndexEntry(
                exhibit_id="Exhibit D",
                exhibit_title="Transmission Crossings CAD Drawing",
                page_start=8,
                page_end=9,
                exhibit_modality=ExhibitModality.VISUAL_DRAWING_OR_MAP_STUB,
            )
        ],
    )

    body_pass = BodyPassExtraction()
    exhibits_pass = ExhibitsPassExtraction(
        clauses=[],
        defined_terms=[],
        special_conditions=[],
        exhibits_catalog=[],
    )

    bundle = assemble_multi_pass_extraction(
        index=idx,
        body_pass=body_pass,
        exhibits_pass=exhibits_pass,
        total_pages=9,
    )

    stub_clause = next((c for c in bundle.clauses if c.node_id == "EXHIBIT_D.STUB"), None)
    assert stub_clause is not None
    assert stub_clause.document_zone == DocumentZone.EXHIBIT_OR_SCHEDULE
    assert stub_clause.hitl_status == HITLStatus.PLACEHOLDER_FOR_REVIEW
    assert stub_clause.hitl_flag_reasons == FlagCode.DEFERRED_MODALITY_PLACEHOLDER.value
    assert "CAD Drawing" in stub_clause.verbatim_text

    catalog_entry = next((e for e in bundle.exhibits_catalog if e.exhibit_id == "Exhibit D"), None)
    assert catalog_entry is not None
    assert catalog_entry.exhibit_modality == ExhibitModality.VISUAL_DRAWING_OR_MAP_STUB


def test_ut5_defined_terms_deduplication():
    """[UT-5] Verify defined terms cross-pass deduplication and precedence rules."""
    now_iso = utc_now_iso()
    idx = ContractStructureIndex(
        document_title="Energy Agreement",
        body_start_page=1,
        body_end_page=10,
    )

    # In Body: dedicated definition clause on page 2
    body_term = DefinedTermRow(
        document_id="doc_test",
        term_name="Operating Term",
        defined_in_node_id="BODY.1.1",
        page_number=2,
        definition_type=DefinitionType.DEDICATED_DEFINITION_CLAUSE,
        verbatim_definition="Operating Term shall mean thirty (30) years.",
        referenced_in_nodes="BODY.1.1",
        created_at=now_iso,
        updated_at=now_iso,
    )

    # In Exhibits: inline parenthetical reference on page 12
    exhibit_term = DefinedTermRow(
        document_id="doc_test",
        term_name="Operating Term",
        defined_in_node_id="EXHIBIT_B.1",
        page_number=12,
        definition_type=DefinitionType.INLINE_PARENTHETICAL,
        verbatim_definition="the (Operating Term)",
        referenced_in_nodes="EXHIBIT_B.1",
        created_at=now_iso,
        updated_at=now_iso,
    )

    bundle = assemble_multi_pass_extraction(
        index=idx,
        body_pass=BodyPassExtraction(defined_terms=[body_term]),
        exhibits_pass=ExhibitsPassExtraction(defined_terms=[exhibit_term]),
        total_pages=15,
    )

    assert len(bundle.defined_terms) == 1
    term = bundle.defined_terms[0]
    assert term.definition_type == DefinitionType.DEDICATED_DEFINITION_CLAUSE
    assert term.page_number == 2
    assert "thirty (30) years" in term.verbatim_definition


def test_ut6_intermediate_schema_serialization():
    """[UT-6] Verify intermediate pass schemas JSON serialization round-trip."""
    idx = ContractStructureIndex(
        document_title="Wind Energy Agreement",
        grantor_landowner_name="John Smith",
        grantee_entity_name="Breeze Development LLC",
        effective_date="2025-05-01",
        has_recitals=True,
        body_start_page=1,
        body_end_page=15,
        signature_start_page=16,
        signature_end_page=17,
        signers=[
            SignerEntry(party_name="John Smith", signer_name="John Smith", signer_title="Owner", page_number=16),
            SignerEntry(party_name="Breeze Development LLC", signer_name="Alice Wang", signer_title="VP", page_number=17),
        ],
        exhibits=[
            ExhibitIndexEntry(
                exhibit_id="Exhibit A",
                exhibit_title="Property Description",
                page_start=18,
                page_end=20,
                exhibit_modality=ExhibitModality.PROPERTY_DESCRIPTION,
            )
        ],
    )

    json_str = idx.model_dump_json()
    reloaded = ContractStructureIndex.model_validate_json(json_str)
    assert reloaded.document_title == "Wind Energy Agreement"
    assert len(reloaded.signers) == 2
    assert len(reloaded.exhibits) == 1
    assert reloaded.exhibits[0].page_end == 20
