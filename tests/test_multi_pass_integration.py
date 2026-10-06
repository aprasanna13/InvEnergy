"""Hermetic Integration Tests for Multi-Pass Extraction and Normalization (IT-1..IT-5)."""

from __future__ import annotations

from unittest.mock import patch
import pytest

from contract_parser.config import PipelineConfig
from contract_parser.gemini_parser import GeminiContractParser
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


@pytest.fixture
def parser() -> GeminiContractParser:
    return GeminiContractParser(
        config=PipelineConfig(use_cloud_storage=False, multi_pass_enabled=True)
    )


def test_it1_and_it2_end_to_end_assembly_and_tree_ordering(parser: GeminiContractParser):
    """[IT-1, IT-2] Verify end-to-end multi-pass assembly and strict pre-order document tree ordering."""
    now_iso = utc_now_iso()
    mock_index = ContractStructureIndex(
        document_title="Solar Lease and Easement Agreement",
        grantor_landowner_name="Prairie Land LLC",
        grantee_entity_name="Invenergy Solar Development LLC",
        effective_date="2024-06-01",
        has_recitals=True,
        body_start_page=1,
        body_end_page=4,
        signature_start_page=5,
        signature_end_page=5,
        signers=[
            SignerEntry(party_name="Prairie Land LLC", signer_name="Bob Rancher", page_number=5)
        ],
        exhibits=[
            ExhibitIndexEntry(
                exhibit_id="Exhibit A",
                exhibit_title="Property Description",
                page_start=6,
                page_end=7,
                exhibit_modality=ExhibitModality.PROPERTY_DESCRIPTION,
            ),
            ExhibitIndexEntry(
                exhibit_id="Exhibit B",
                exhibit_title="Payment Terms",
                page_start=8,
                page_end=9,
                exhibit_modality=ExhibitModality.PROSE_CLAUSES,
            ),
        ],
    )

    mock_body = BodyPassExtraction(
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
                verbatim_text="Solar Lease Agreement by and between Prairie Land LLC and Invenergy Solar Development LLC.",
                reconstructed_context_text="Solar Lease Agreement by and between Prairie Land LLC and Invenergy Solar Development LLC.",
                preamble_text="Solar Lease Agreement...",
                postamble_text=None,
                hitl_status=HITLStatus.VERIFIED_AUTO,
                hitl_flag_reasons="NONE",
                created_at=now_iso,
                updated_at=now_iso,
            ),
            ClauseRow(
                node_id="RECITALS.1",
                canonical_path="RECITALS.1",
                document_zone=DocumentZone.RECITALS,
                depth=1,
                clause_title="Recital A",
                clause_label="A",
                numbering_scheme=NumberingScheme.ALPHA_UPPER,
                page_start=1,
                page_end=1,
                verbatim_text="WHEREAS Owner owns real property described in Exhibit A;",
                reconstructed_context_text="WHEREAS Owner owns real property described in Exhibit A;",
                preamble_text="WHEREAS...",
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
                verbatim_text="1. Term and Rent as specified in Exhibit B.",
                reconstructed_context_text="1. Term and Rent as specified in Exhibit B.",
                preamble_text="1. Term...",
                postamble_text=None,
                hitl_status=HITLStatus.VERIFIED_AUTO,
                hitl_flag_reasons="NONE",
                created_at=now_iso,
                updated_at=now_iso,
            ),
            ClauseRow(
                node_id="BODY.1.1",
                canonical_path="BODY.1.1",
                parent_node_id="BODY.1",
                document_zone=DocumentZone.BODY,
                depth=2,
                clause_title="Development Period",
                clause_label="1.1",
                numbering_scheme=NumberingScheme.DECIMAL,
                page_start=2,
                page_end=2,
                verbatim_text="1.1 Development Period shall be 5 years.",
                reconstructed_context_text="1. Term... 1.1 Development Period shall be 5 years.",
                preamble_text="1.1 Development Period...",
                postamble_text=None,
                hitl_status=HITLStatus.VERIFIED_AUTO,
                hitl_flag_reasons="NONE",
                created_at=now_iso,
                updated_at=now_iso,
            ),
            ClauseRow(
                node_id="BODY.2",
                canonical_path="BODY.2",
                document_zone=DocumentZone.BODY,
                depth=1,
                clause_title="Operations",
                clause_label="2",
                numbering_scheme=NumberingScheme.INTEGER,
                page_start=2,
                page_end=4,
                verbatim_text="2. Operations.",
                reconstructed_context_text="2. Operations.",
                preamble_text="2. Operations.",
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

    mock_exhibits = ExhibitsPassExtraction(
        clauses=[
            ClauseRow(
                node_id="EXHIBIT_A.1",
                canonical_path="EXHIBIT_A.1",
                document_zone=DocumentZone.EXHIBIT_OR_SCHEDULE,
                depth=1,
                clause_title="Tract 1 Legal Description",
                clause_label="Tract 1",
                numbering_scheme=NumberingScheme.NAMED_HEADER,
                page_start=6,
                page_end=7,
                verbatim_text="Tract 1: 160 acres in Section 10...",
                reconstructed_context_text="Tract 1: 160 acres in Section 10...",
                preamble_text="Tract 1...",
                postamble_text=None,
                hitl_status=HITLStatus.VERIFIED_AUTO,
                hitl_flag_reasons="NONE",
                created_at=now_iso,
                updated_at=now_iso,
            ),
            ClauseRow(
                node_id="EXHIBIT_B.1",
                canonical_path="EXHIBIT_B.1",
                document_zone=DocumentZone.EXHIBIT_OR_SCHEDULE,
                depth=1,
                clause_title="Operating Rent",
                clause_label="1",
                numbering_scheme=NumberingScheme.INTEGER,
                page_start=8,
                page_end=9,
                verbatim_text="1. Operating rent shall be $1,000 per MW.",
                reconstructed_context_text="1. Operating rent shall be $1,000 per MW.",
                preamble_text="1. Operating rent...",
                postamble_text=None,
                hitl_status=HITLStatus.VERIFIED_AUTO,
                hitl_flag_reasons="NONE",
                created_at=now_iso,
                updated_at=now_iso,
            ),
        ],
        defined_terms=[],
        special_conditions=[],
        exhibits_catalog=[
            ExhibitCatalogRow(
                document_id="doc_it_test",
                exhibit_id="Exhibit A",
                exhibit_title="Property Description",
                exhibit_modality=ExhibitModality.PROPERTY_DESCRIPTION,
                page_start=6,
                page_end=7,
                referenced_by_nodes="NONE",
                created_at=now_iso,
                updated_at=now_iso,
            ),
            ExhibitCatalogRow(
                document_id="doc_it_test",
                exhibit_id="Exhibit B",
                exhibit_title="Payment Terms",
                exhibit_modality=ExhibitModality.PROSE_CLAUSES,
                page_start=8,
                page_end=9,
                referenced_by_nodes="NONE",
                created_at=now_iso,
                updated_at=now_iso,
            ),
        ],
    )

    with (
        patch.object(parser, "_discover_contract_structure", return_value=mock_index),
        patch.object(parser, "_extract_body_pass", return_value=mock_body),
        patch.object(parser, "_extract_exhibits_pass", return_value=mock_exhibits),
    ):
        bundle = parser.extract(
            document_id="doc_it_test",
            gcs_pdf_uri="gs://mock/solar_lease.pdf",
            pdf_bytes=b"%PDF-1.4 mock",
        )

        assert bundle is not None
        node_ids = [c.node_id for c in bundle.clauses]

        # Verify strict document tree sequence:
        # PREAMBLE -> RECITALS -> BODY.1 -> BODY.1.1 (child follows parent) -> BODY.2 -> SIGNATURES.1 -> EXHIBIT_A.1 -> EXHIBIT_B.1
        expected_sequence = [
            "PREAMBLE.1",
            "RECITALS.1",
            "BODY.1",
            "BODY.1.1",
            "BODY.2",
            "SIGNATURES.1",
            "EXHIBIT_A.1",
            "EXHIBIT_B.1",
        ]
        assert node_ids == expected_sequence, f"Actual sequence: {node_ids}"


def test_it3_and_it4_dependency_linking_and_exhibit_cross_references(parser: GeminiContractParser):
    """[IT-3, IT-4] Verify cross-zone exhibit reference linking (referenced_by_nodes) and external dependency preservation."""
    now_iso = utc_now_iso()
    mock_index = ContractStructureIndex(
        document_title="Interconnect Agreement",
        body_start_page=1,
        body_end_page=3,
        exhibits=[
            ExhibitIndexEntry(
                exhibit_id="Exhibit C",
                exhibit_title="Easement Instruments",
                page_start=4,
                page_end=5,
                exhibit_modality=ExhibitModality.EXTERNAL_INSTRUMENT_LIST,
            )
        ],
    )

    mock_body = BodyPassExtraction(
        clauses=[
            ClauseRow(
                node_id="BODY.2",
                canonical_path="BODY.2",
                document_zone=DocumentZone.BODY,
                depth=1,
                clause_title="Easement Terms",
                clause_label="2",
                numbering_scheme=NumberingScheme.INTEGER,
                page_start=1,
                page_end=2,
                verbatim_text="2. Subject to recorded instruments in Exhibit C.",
                reconstructed_context_text="2. Subject to recorded instruments in Exhibit C.",
                preamble_text="2. Subject to...",
                postamble_text=None,
                hitl_status=HITLStatus.VERIFIED_AUTO,
                hitl_flag_reasons="NONE",
                created_at=now_iso,
                updated_at=now_iso,
            ),
            ClauseRow(
                node_id="BODY.2.i",
                canonical_path="BODY.2.i",
                parent_node_id="BODY.2",
                document_zone=DocumentZone.BODY,
                depth=2,
                clause_title="Subordinate Easements",
                clause_label="(i)",
                numbering_scheme=NumberingScheme.ROMAN_LOWER,
                page_start=2,
                page_end=2,
                verbatim_text="(i) Subject to the Blue Meridian Easements referenced in Exhibit C.",
                reconstructed_context_text="2. Subject to... (i) Subject to the Blue Meridian Easements referenced in Exhibit C.",
                preamble_text="(i)...",
                postamble_text=None,
                hitl_status=HITLStatus.VERIFIED_AUTO,
                hitl_flag_reasons="NONE",
                created_at=now_iso,
                updated_at=now_iso,
            ),
        ],
        defined_terms=[
            DefinedTermRow(
                document_id="doc_dep",
                term_name="Blue Meridian Easements",
                defined_in_node_id="BODY.2.i",
                page_number=2,
                definition_type=DefinitionType.INLINE_PARENTHETICAL,
                verbatim_definition="Blue Meridian Easements (as listed in Exhibit C)",
                referenced_in_nodes="BODY.2.i",
                created_at=now_iso,
                updated_at=now_iso,
            )
        ],
        special_conditions=[],
    )

    mock_exhibits = ExhibitsPassExtraction(
        clauses=[],
        defined_terms=[],
        special_conditions=[],
        exhibits_catalog=[
            ExhibitCatalogRow(
                document_id="doc_dep",
                exhibit_id="Exhibit C",
                exhibit_title="Easement Instruments",
                exhibit_modality=ExhibitModality.EXTERNAL_INSTRUMENT_LIST,
                page_start=4,
                page_end=5,
                has_unresolved_external_dep=True,
                referenced_by_nodes="NONE",
                created_at=now_iso,
                updated_at=now_iso,
            )
        ],
    )

    with (
        patch.object(parser, "_discover_contract_structure", return_value=mock_index),
        patch.object(parser, "_extract_body_pass", return_value=mock_body),
        patch.object(parser, "_extract_exhibits_pass", return_value=mock_exhibits),
    ):
        bundle = parser.extract(
            document_id="doc_dep",
            gcs_pdf_uri="gs://mock/dep.pdf",
            pdf_bytes=b"%PDF-1.4 mock",
        )

        ex_c = next(e for e in bundle.exhibits_catalog if e.exhibit_id == "Exhibit C")
        # Body clauses referencing Exhibit C are linked into referenced_by_nodes
        assert "BODY.2" in ex_c.referenced_by_nodes or "BODY.2.i" in ex_c.referenced_by_nodes

        # 2-hop external dependency linking on governing clauses
        body_2_i = next(c for c in bundle.clauses if c.node_id == "BODY.2.i")
        assert FlagCode.UNRESOLVED_EXTERNAL_DEPENDENCY.value in body_2_i.hitl_flag_reasons


def test_it5_special_conditions_leaf_attachment_and_deduplication(parser: GeminiContractParser):
    """[IT-5] Verify SpecialConditionRow deduplication, condition_id normalization, and leaf attachment."""
    now_iso = utc_now_iso()
    mock_index = ContractStructureIndex(
        document_title="Wind Energy Agreement",
        body_start_page=1,
        body_end_page=3,
        exhibits=[
            ExhibitIndexEntry(
                exhibit_id="Exhibit C",
                exhibit_title="Special Conditions",
                page_start=4,
                page_end=5,
                exhibit_modality=ExhibitModality.PROSE_CLAUSES,
            )
        ],
    )

    mock_body = BodyPassExtraction(
        clauses=[
            ClauseRow(
                node_id="BODY.8",
                canonical_path="BODY.8",
                document_zone=DocumentZone.BODY,
                depth=1,
                clause_title="Site Access",
                clause_label="8",
                numbering_scheme=NumberingScheme.INTEGER,
                page_start=2,
                page_end=3,
                verbatim_text="8. Access roads.",
                reconstructed_context_text="8. Access roads.",
                preamble_text="8. Access roads.",
                postamble_text=None,
                hitl_status=HITLStatus.VERIFIED_AUTO,
                hitl_flag_reasons="NONE",
                created_at=now_iso,
                updated_at=now_iso,
            ),
            ClauseRow(
                node_id="BODY.8.1",
                canonical_path="BODY.8.1",
                parent_node_id="BODY.8",
                document_zone=DocumentZone.BODY,
                depth=2,
                clause_title="Fencing and Gates",
                clause_label="8.1",
                numbering_scheme=NumberingScheme.DECIMAL,
                page_start=3,
                page_end=3,
                verbatim_text="8.1 Grantee shall keep all pasture gates locked at all times.",
                reconstructed_context_text="8. Access roads... 8.1 Grantee shall keep all pasture gates locked at all times.",
                preamble_text="8.1...",
                postamble_text=None,
                hitl_status=HITLStatus.VERIFIED_AUTO,
                hitl_flag_reasons="NONE",
                created_at=now_iso,
                updated_at=now_iso,
            ),
        ],
        special_conditions=[
            SpecialConditionRow(
                condition_id="sc_placeholder_1",
                document_id="doc_sc",
                node_id="BODY.8.1",
                canonical_path="BODY.8.1",
                constraint_category=ConstraintCategory.ACCESS_ROAD_GATE_PROTOCOL,
                target_asset_or_area="Pasture Gates",
                quantitative_metric=None,
                temporal_restriction="At all times",
                penalty_or_consequence=None,
                actionable_obligation_summary="Keep all pasture gates closed and locked.",
                verbatim_excerpt="Grantee shall keep all pasture gates locked at all times.",
                created_at=now_iso,
                updated_at=now_iso,
            )
        ],
    )

    mock_exhibits = ExhibitsPassExtraction(
        clauses=[
            ClauseRow(
                node_id="EXHIBIT_C.1",
                canonical_path="EXHIBIT_C.1",
                document_zone=DocumentZone.EXHIBIT_OR_SCHEDULE,
                depth=1,
                clause_title="Setback",
                clause_label="1",
                numbering_scheme=NumberingScheme.INTEGER,
                page_start=4,
                page_end=4,
                verbatim_text="1. 200 feet setback from all occupied dwellings.",
                reconstructed_context_text="1. 200 feet setback from all occupied dwellings.",
                preamble_text="1. 200 feet...",
                postamble_text=None,
                hitl_status=HITLStatus.VERIFIED_AUTO,
                hitl_flag_reasons="NONE",
                created_at=now_iso,
                updated_at=now_iso,
            )
        ],
        special_conditions=[
            SpecialConditionRow(
                condition_id="sc_placeholder_2",
                document_id="doc_sc",
                node_id="EXHIBIT_C.1",
                canonical_path="EXHIBIT_C.1",
                constraint_category=ConstraintCategory.STRUCTURE_BARN_WELL_SETBACK,
                target_asset_or_area="Occupied Dwellings",
                quantitative_metric="200 feet",
                temporal_restriction=None,
                penalty_or_consequence=None,
                actionable_obligation_summary="Maintain 200 feet setback from all occupied residences.",
                verbatim_excerpt="200 feet setback from all occupied dwellings.",
                created_at=now_iso,
                updated_at=now_iso,
            )
        ],
        exhibits_catalog=[
            ExhibitCatalogRow(
                document_id="doc_sc",
                exhibit_id="Exhibit C",
                exhibit_title="Special Conditions",
                exhibit_modality=ExhibitModality.PROSE_CLAUSES,
                page_start=4,
                page_end=5,
                referenced_by_nodes="NONE",
                created_at=now_iso,
                updated_at=now_iso,
            )
        ],
    )

    with (
        patch.object(parser, "_discover_contract_structure", return_value=mock_index),
        patch.object(parser, "_extract_body_pass", return_value=mock_body),
        patch.object(parser, "_extract_exhibits_pass", return_value=mock_exhibits),
    ):
        bundle = parser.extract(
            document_id="doc_sc",
            gcs_pdf_uri="gs://mock/sc.pdf",
            pdf_bytes=b"%PDF-1.4 mock",
        )

        assert len(bundle.special_conditions) == 2
        # Normalizer re-indexes condition IDs deterministically as sc_<node_id>_<idx>
        cond_ids = [sc.condition_id for sc in bundle.special_conditions]
        assert "sc_BODY.8.1_1" in cond_ids
        assert "sc_EXHIBIT_C.1_1" in cond_ids

        # Both source clauses get tagged with LANDOWNER_SPECIAL_CONDITION
        c_body = next(c for c in bundle.clauses if c.node_id == "BODY.8.1")
        assert FlagCode.LANDOWNER_SPECIAL_CONDITION.value in c_body.hitl_flag_reasons
        c_ex = next(c for c in bundle.clauses if c.node_id == "EXHIBIT_C.1")
        assert FlagCode.LANDOWNER_SPECIAL_CONDITION.value in c_ex.hitl_flag_reasons
