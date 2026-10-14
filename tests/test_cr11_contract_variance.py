"""Comprehensive test suite for Contract Variance Engine, Legal Redline Studio & Risk Scoring (CR-11).

Verifies:
- UT-1: Alignment - Canonical path 1-to-1 matching immediately with score 1.0.
- UT-2: Alignment - Section insertion drift recovery (Stage 2 Jaccard token overlap >= 0.70).
- UT-3: Parameter Masking - Exact placeholder substitution detection (PARAMETER_ONLY, Risk: NONE, 0 redlines).
- UT-4: Parameter Masking - Boilerplate tampering with embedded parameters (MODIFIED with <ins>/<del>).
- UT-5: Word Diff - SequenceMatcher token tagging with preserved whitespace.
- UT-6: Risk Scoring - Material clause risk classification (HIGH on indemnity/liability/removal bond).
- UT-7: Template Affinity - Mathematical scoring formulation (Region 0.40, Tech 0.30, Form 0.20, Vintage 0.10).
- UT-8: Zero Pollution - Database integrity verification (documents and clauses row counts unmutated).
- IT-1: End-to-End API - Full execution via POST /api/v1/contracts/{id}/compare.
- IT-2: REST Endpoints - Variance retrieval and template listing endpoints.
- IT-3: RBAC Enforcement - Permission boundaries on comparison execution.
"""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from contract_parser.app import create_app
from contract_parser.auth import UserContext, UserRole
from contract_parser.config import PipelineConfig
from contract_parser.schemas import (
    ClauseVarianceDetailRow,
    ContractVarianceBundle,
    ContractVarianceRow,
    EnergyTechnology,
    FormType,
    TemplateExtractionBundle,
    VarianceRiskLevel,
    VarianceSummary,
    VarianceType,
)
from contract_parser.storage import ContractStorageService
from contract_parser.variance_engine import (
    calculate_template_affinity,
    compute_token_jaccard,
    evaluate_legal_risk,
    evaluate_parameter_substitution,
    generate_redline_html,
    rank_candidate_templates,
    run_contract_variance_comparison,
)


def create_dummy_pdf_bytes(page_count: int = 2) -> bytes:
    """Create a minimal valid multi-page PDF in-memory using pypdf."""
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=612, height=792)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


# ==============================================================================
# Unit Tests (UT-1 through UT-8)
# ==============================================================================

def test_ut_1_canonical_path_matching():
    """UT-1: Test canonical path 1-to-1 matching immediately with score 1.0."""
    template_clauses = [
        {
            "clause_id": "tpl_c1",
            "canonical_path": "BODY.1.1",
            "clause_title": "Grant of Lease",
            "section_text": "Owner hereby leases the Property to Operator for solar energy purposes.",
            "placeholders": [],
        }
    ]
    contract_clauses = [
        {
            "node_id": "doc_c1",
            "canonical_path": "BODY.1.1",
            "clause_title": "Grant of Lease",
            "verbatim_text": "Owner hereby leases the Property to Operator for solar energy purposes.",
            "reconstructed_context_text": "Owner hereby leases the Property to Operator for solar energy purposes.",
        }
    ]

    details, summary, dev_index = run_contract_variance_comparison(
        contract_meta={"document_id": "doc_test_1"},
        contract_clauses=contract_clauses,
        template_clauses=template_clauses,
    )

    assert len(details) == 1
    d = details[0]
    assert d.canonical_path == "BODY.1.1"
    assert d.variance_type == VarianceType.IDENTICAL
    assert d.risk_level == VarianceRiskLevel.NONE
    assert d.similarity_score == 1.0
    assert summary.identical_count == 1
    assert dev_index == 0.0


def test_ut_2_section_insertion_drift_recovery():
    """UT-2: Test Section insertion drift recovery (Stage 2 Jaccard token overlap >= 0.70)."""
    # Baseline template has:
    # BODY.2.1: Term and Rental Payments
    # BODY.2.2: Taxes and Assessments
    template_clauses = [
        {
            "clause_id": "tpl_c1",
            "canonical_path": "BODY.2.1",
            "clause_title": "Term and Rental Payments",
            "section_text": "Operator shall pay an annual rent of thirty thousand dollars per megawatt.",
            "placeholders": [],
        },
        {
            "clause_id": "tpl_c2",
            "canonical_path": "BODY.2.2",
            "clause_title": "Taxes and Assessments",
            "section_text": "Operator shall pay all personal property and real estate taxes directly attributable to the project.",
            "placeholders": [],
        },
    ]

    # In the executed agreement, a custom clause was inserted at Section 2.1:
    # Contract BODY.2.1: Hunting and Recreational Rights (inserted custom clause)
    # Contract BODY.2.2: Term and Rental Payments (drifted from template BODY.2.1)
    # Contract BODY.2.3: Taxes and Assessments (drifted from template BODY.2.2)
    contract_clauses = [
        {
            "node_id": "doc_c1",
            "canonical_path": "BODY.2.1",
            "clause_title": "Hunting and Recreational Rights",
            "verbatim_text": "Owner retains exclusive hunting and fishing rights on non-developed parcels.",
            "reconstructed_context_text": "Owner retains exclusive hunting and fishing rights on non-developed parcels.",
        },
        {
            "node_id": "doc_c2",
            "canonical_path": "BODY.2.2",
            "clause_title": "Term and Rental Payments",
            "verbatim_text": "Operator shall pay an annual rent of thirty thousand dollars per megawatt.",
            "reconstructed_context_text": "Operator shall pay an annual rent of thirty thousand dollars per megawatt.",
        },
        {
            "node_id": "doc_c3",
            "canonical_path": "BODY.2.3",
            "clause_title": "Taxes and Assessments",
            "verbatim_text": "Operator shall pay all personal property and real estate taxes directly attributable to the project.",
            "reconstructed_context_text": "Operator shall pay all personal property and real estate taxes directly attributable to the project.",
        },
    ]

    details, summary, dev_index = run_contract_variance_comparison(
        contract_meta={"document_id": "doc_drift_test"},
        contract_clauses=contract_clauses,
        template_clauses=template_clauses,
    )

    # doc_c2 ("Term and Rental Payments") should align to tpl_c1 despite path shift (BODY.2.2 -> BODY.2.1)
    matched_c2 = next(d for d in details if d.contract_node_id == "doc_c2")
    assert matched_c2.template_node_id == "tpl_c1"
    assert matched_c2.variance_type == VarianceType.IDENTICAL

    # doc_c3 ("Taxes and Assessments") should align to tpl_c2 despite path shift (BODY.2.3 -> BODY.2.2)
    matched_c3 = next(d for d in details if d.contract_node_id == "doc_c3")
    assert matched_c3.template_node_id == "tpl_c2"
    assert matched_c3.variance_type == VarianceType.IDENTICAL

    # doc_c1 ("Hunting and Recreational Rights") should be recognized as ADDED
    added_c1 = next(d for d in details if d.contract_node_id == "doc_c1")
    assert added_c1.variance_type == VarianceType.ADDED


def test_ut_3_exact_placeholder_substitution():
    """UT-3: Test placeholder substitution detection (PARAMETER_ONLY, Risk: NONE, 0 redlines)."""
    tpl_text = (
        "Operator shall pay Owner the sum of $[_____] per year for the property situated in [County] County, "
        "State of [State] commencing on [Effective Date]."
    )
    contract_text = (
        "Operator shall pay Owner the sum of $15,000 per year for the property situated in McLean County, "
        "State of Illinois commencing on May 1, 2024."
    )
    placeholders = [
        {"token_name": "_____", "inferred_type": "CURRENCY"},
        {"token_name": "County", "inferred_type": "JURISDICTION"},
        {"token_name": "State", "inferred_type": "JURISDICTION"},
        {"token_name": "Effective Date", "inferred_type": "DATE"},
    ]

    is_param_match, sub_count = evaluate_parameter_substitution(tpl_text, contract_text, placeholders)
    assert is_param_match is True
    assert sub_count >= 3

    # Full comparison pipeline evaluation
    details, summary, dev_index = run_contract_variance_comparison(
        contract_meta={"document_id": "doc_param_test"},
        contract_clauses=[
            {
                "node_id": "doc_c1",
                "canonical_path": "BODY.3.1",
                "clause_title": "Rent Payment Terms",
                "verbatim_text": contract_text,
                "reconstructed_context_text": contract_text,
            }
        ],
        template_clauses=[
            {
                "clause_id": "tpl_c1",
                "canonical_path": "BODY.3.1",
                "clause_title": "Rent Payment Terms",
                "section_text": tpl_text,
                "placeholders": placeholders,
            }
        ],
    )

    assert len(details) == 1
    d = details[0]
    assert d.variance_type == VarianceType.PARAMETER_ONLY
    assert d.risk_level == VarianceRiskLevel.NONE
    assert "<ins" not in d.redline_html
    assert "<del" not in d.redline_html
    assert summary.parameter_only_count == 1
    assert dev_index == 0.0


def test_ut_4_boilerplate_tampering_with_embedded_parameters():
    """UT-4: Test boilerplate tampering around embedded parameters (MODIFIED with <ins>/<del>)."""
    tpl_text = (
        "Operator shall pay Owner $[_____] per acre and Operator shall indemnify and hold harmless Owner "
        "against all third-party environmental claims."
    )
    # The landowner edited the indemnity clause while filling in the parameter:
    contract_text = (
        "Operator shall pay Owner $250 per acre and Operator shall indemnify and hold harmless Owner "
        "against all third-party environmental claims, provided Operator total liability is capped at $10,000."
    )
    placeholders = [{"token_name": "_____", "inferred_type": "CURRENCY"}]

    is_param_match, sub_count = evaluate_parameter_substitution(tpl_text, contract_text, placeholders)
    assert is_param_match is False

    details, summary, dev_index = run_contract_variance_comparison(
        contract_meta={"document_id": "doc_tamper_test"},
        contract_clauses=[
            {
                "node_id": "doc_c1",
                "canonical_path": "BODY.5.1",
                "clause_title": "Rent and Environmental Indemnity",
                "verbatim_text": contract_text,
                "reconstructed_context_text": contract_text,
            }
        ],
        template_clauses=[
            {
                "clause_id": "tpl_c1",
                "canonical_path": "BODY.5.1",
                "clause_title": "Rent and Environmental Indemnity",
                "section_text": tpl_text,
                "placeholders": placeholders,
            }
        ],
    )

    assert len(details) == 1
    d = details[0]
    assert d.variance_type == VarianceType.MODIFIED
    assert d.risk_level == VarianceRiskLevel.HIGH
    assert "<ins" in d.redline_html
    assert "capped at $10,000" in d.redline_html
    assert summary.modified_count == 1
    assert summary.high_risk_count == 1
    assert dev_index > 0.0


def test_ut_5_word_diff_token_tagging():
    """UT-5: SequenceMatcher token tagging with preserved whitespace and valid HTML tags."""
    tpl_text = "Operator shall indemnify Owner against claims."
    con_text = "Operator shall fully indemnify Owner against third-party claims."

    html = generate_redline_html(tpl_text, con_text)
    assert '<ins class="diff-ins">fully </ins>' in html or '<ins class="diff-ins">fully</ins>' in html
    assert '<ins class="diff-ins">third-party </ins>' in html or '<ins class="diff-ins">third-party</ins>' in html
    assert "indemnify Owner against" in html
    assert "<del" not in html

    del_html = generate_redline_html(
        "Operator shall provide 60 days advance written notice.",
        "Operator shall provide 30 days advance written notice.",
    )
    assert '<del class="diff-del">60 </del>' in del_html or '<del class="diff-del">60</del>' in del_html
    assert '<ins class="diff-ins">30 </ins>' in del_html or '<ins class="diff-ins">30</ins>' in del_html


def test_ut_6_risk_scoring_material_classifications():
    """UT-6: Material clause risk classification (HIGH on indemnity/liability/removal bond)."""
    # High risk: indemnity / liability modification
    risk_level, cat, rationale = evaluate_legal_risk(
        tpl_text="Operator shall defend and indemnify Owner against all claims.",
        con_text="Operator shall defend Owner, provided liability is capped at $5,000.",
        variance_type=VarianceType.MODIFIED,
        clause_title="Indemnity",
    )
    assert risk_level == VarianceRiskLevel.HIGH
    assert "INDEMNITY" in cat or "LIABILITY" in cat

    # High risk: decommissioning / removal bond modification
    risk_level_bond, cat_bond, _ = evaluate_legal_risk(
        tpl_text="Operator shall post a decommissioning bond in amount of $100,000.",
        con_text="Operator shall post a decommissioning bond only after year 15.",
        variance_type=VarianceType.MODIFIED,
        clause_title="Decommissioning and Removal Bond",
    )
    assert risk_level_bond == VarianceRiskLevel.HIGH

    # Medium risk: operational modification (drain tile, livestock gate)
    risk_level_op, cat_op, _ = evaluate_legal_risk(
        tpl_text="Operator shall repair all damaged drain tiles within 14 days.",
        con_text="Operator shall repair all damaged drain tiles within 45 days of notice.",
        variance_type=VarianceType.MODIFIED,
        clause_title="Drain Tile Protection",
    )
    assert risk_level_op == VarianceRiskLevel.MEDIUM
    assert "OPERATIONAL" in cat_op

    # None risk for identical or parameter only
    risk_none, _, _ = evaluate_legal_risk("Same text", "Same text", VarianceType.IDENTICAL)
    assert risk_none == VarianceRiskLevel.NONE


def test_ut_7_template_affinity_scoring_formula():
    """UT-7: Mathematical affinity scoring formulation (Region, Tech, Form, Vintage)."""
    contract_meta = {
        "state": "IL",
        "energy_technology": EnergyTechnology.WIND,
        "form_type": FormType.LEASE,
        "execution_date": "2024-06-15",
    }

    # Perfect match template
    tpl_perfect = {
        "template_id": "tpl_il_wind_2024",
        "form_name": "Illinois Wind Lease 2024",
        "state": "IL",
        "technology": EnergyTechnology.WIND,
        "form_type": FormType.LEASE,
        "effective_vintage_year": 2024,
    }

    score_perfect, reasons_perfect = calculate_template_affinity(contract_meta, tpl_perfect)
    assert pytest.approx(score_perfect, 0.01) == 1.0
    assert any("+0.40" in r for r in reasons_perfect)
    assert any("+0.30" in r for r in reasons_perfect)
    assert any("+0.20" in r for r in reasons_perfect)
    assert any("+0.10" in r for r in reasons_perfect)

    # Divergent template (OK Solar 2018)
    tpl_other = {
        "template_id": "tpl_ok_solar_2018",
        "form_name": "Oklahoma Solar Option 2018",
        "state": "OK",
        "technology": EnergyTechnology.SOLAR,
        "form_type": FormType.OPTION,
        "effective_vintage_year": 2018,
    }

    score_other, _ = calculate_template_affinity(contract_meta, tpl_other)
    assert score_other < 0.20

    # Ranking test
    ranked = rank_candidate_templates(contract_meta, [tpl_other, tpl_perfect])
    assert ranked[0]["template_id"] == "tpl_il_wind_2024"
    assert ranked[0]["affinity_score"] > ranked[1]["affinity_score"]


def test_ut_8_zero_pollution_database_integrity(tmp_path: Path):
    """UT-8: Database integrity verification (documents and clauses row counts unmutated)."""
    db_path = str(tmp_path / "invenergy_variance_zero_pollution.db")
    cfg = PipelineConfig(sqlite_db_path=db_path, use_bigquery=False)
    storage = ContractStorageService(config=cfg)

    # Seed sample template into template tables
    storage.seed_sample_templates()

    # Insert a test document and clause directly into SQLite
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO documents (
            document_id, project_id, landowner_id, filename, file_sha256,
            ingestion_status, energy_technology, state, country, form_type, page_count
        ) VALUES ('doc_test_iso', 'prj_test', 'lnd_test', 'Test_Lease.pdf', 'hash123', 'PROCESSED', 'ONSHORE_WIND', 'IL', 'USA', 'LEASE', 5)"""
    )
    cur.execute(
        """INSERT INTO clauses (
            node_id, document_id, canonical_path, clause_label, clause_title,
            verbatim_text, reconstructed_context_text, page_start, page_end, depth
        ) VALUES ('node_iso_1', 'doc_test_iso', 'BODY.1.1', '1.1', 'Grant of Lease', 'Test lease verbatim', 'Test lease reconstructed', 1, 1, 1)"""
    )
    conn.commit()

    cur.execute("SELECT COUNT(*) FROM documents")
    docs_before = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM clauses")
    clauses_before = cur.fetchone()[0]
    conn.close()

    # Save a contract variance bundle into the variance lakehouse tables
    bundle = ContractVarianceBundle(
        variance=ContractVarianceRow(
            variance_id="var_iso_001",
            document_id="doc_test_iso",
            template_id="tpl_sample_il_wind_lease",
            template_name="Illinois Wind Energy Lease",
            template_version="1.0",
            overall_deviation_index=15.0,
            summary=VarianceSummary(
                total_clauses_analyzed=1,
                identical_count=0,
                parameter_only_count=0,
                modified_count=1,
                added_count=0,
                deleted_count=0,
                high_risk_count=0,
                medium_risk_count=1,
                low_risk_count=0,
                none_risk_count=0,
            ),
        ),
        details=[
            ClauseVarianceDetailRow(
                detail_id="vdet_iso_001",
                variance_id="var_iso_001",
                document_id="doc_test_iso",
                contract_node_id="node_iso_1",
                template_node_id="tpl_c1",
                canonical_path="BODY.1.1",
                clause_title="Grant of Lease",
                variance_type=VarianceType.MODIFIED,
                risk_level=VarianceRiskLevel.MEDIUM,
                risk_categories="OPERATIONAL_ALTERATION",
                risk_rationale="Minor alteration to operational timeline.",
                similarity_score=0.92,
                template_baseline_text="Template text baseline",
                contract_executed_text="Contract text executed",
                redline_html="<ins class=\"diff-ins\">Contract</ins> text",
            )
        ],
    )
    storage.save_contract_variance(bundle)

    # Verify retrieval
    loaded = storage.get_latest_contract_variance("doc_test_iso")
    assert loaded is not None
    assert loaded.variance.variance_id == "var_iso_001"
    assert len(loaded.details) == 1

    # Verify ZERO pollution on documents and clauses
    conn2 = sqlite3.connect(db_path)
    cur2 = conn2.cursor()
    cur2.execute("SELECT COUNT(*) FROM documents")
    docs_after = cur2.fetchone()[0]
    cur2.execute("SELECT COUNT(*) FROM clauses")
    clauses_after = cur2.fetchone()[0]

    cur2.execute("SELECT COUNT(*) FROM contract_variances")
    variances_count = cur2.fetchone()[0]
    cur2.execute("SELECT COUNT(*) FROM clause_variance_details")
    details_count = cur2.fetchone()[0]
    conn2.close()

    assert docs_after == docs_before == 1
    assert clauses_after == clauses_before == 1
    assert variances_count == 1
    assert details_count == 1


# ==============================================================================
# Integration Tests (IT-1 through IT-3)
# ==============================================================================

def test_it_1_end_to_end_comparison_api(tmp_path: Path):
    """IT-1: Full end-to-end execution via POST /api/v1/contracts/{id}/compare."""
    db_path = str(tmp_path / "invenergy_it1_variance.db")
    cfg = PipelineConfig(sqlite_db_path=db_path, use_bigquery=False, auth_enabled=False)
    storage = ContractStorageService(config=cfg)
    app = create_app(config=cfg, storage=storage)

    # Seed master regional templates
    storage.seed_sample_templates()

    # Insert test contract with clauses into SQLite
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO documents (
            document_id, project_id, landowner_id, filename, file_sha256,
            ingestion_status, energy_technology, state, country, form_type, page_count
        ) VALUES ('doc_e2e_1', 'prj_cedar_lantern_wind', 'lnd_arthur', 'Cedar_Lantern_Lease.pdf', 'hash_e2e_1', 'PROCESSED', 'ONSHORE_WIND', 'IL', 'USA', 'LEASE', 4)"""
    )
    # Clause 1: Identical preamble
    cur.execute(
        """INSERT INTO clauses (
            node_id, document_id, canonical_path, clause_label, clause_title,
            verbatim_text, reconstructed_context_text, page_start, page_end, depth
        ) VALUES ('node_1', 'doc_e2e_1', 'PREAMBLE', 'PREAMBLE', 'Preamble',
            'This Wind Energy Lease is entered into by and between Owner and Invenergy Wind Development LLC.',
            'This Wind Energy Lease is entered into by and between Owner and Invenergy Wind Development LLC.', 1, 1, 1)"""
    )
    # Clause 2: Parameter-only substitution
    cur.execute(
        """INSERT INTO clauses (
            node_id, document_id, canonical_path, clause_label, clause_title,
            verbatim_text, reconstructed_context_text, page_start, page_end, depth
        ) VALUES ('node_2', 'doc_e2e_1', 'BODY.2.1', '2.1', 'Term and Rent',
            'Operator shall pay an annual rent of $15,000 per megawatt for a term of 30 years.',
            'Operator shall pay an annual rent of $15,000 per megawatt for a term of 30 years.', 1, 1, 1)"""
    )
    # Clause 3: High-risk modified indemnity
    cur.execute(
        """INSERT INTO clauses (
            node_id, document_id, canonical_path, clause_label, clause_title,
            verbatim_text, reconstructed_context_text, page_start, page_end, depth
        ) VALUES ('node_3', 'doc_e2e_1', 'BODY.14.1', '14.1', 'Indemnification',
            'Operator shall indemnify Owner against all claims, provided liability is capped at $50,000.',
            'Operator shall indemnify Owner against all claims, provided liability is capped at $50,000.', 2, 2, 1)"""
    )
    conn.commit()
    conn.close()

    client = TestClient(app)

    # 1. Compare against best template (auto-selected IL Wind Lease)
    res_compare = client.post("/api/v1/contracts/doc_e2e_1/compare")
    assert res_compare.status_code == 200, res_compare.text
    data = res_compare.json()

    assert "variance" in data
    assert "details" in data
    var = data["variance"]
    assert var["document_id"] == "doc_e2e_1"
    assert "Illinois" in var["template_name"] or "Wind" in var["template_name"]
    assert len(data["details"]) >= 3

    # Check high-risk identification on indemnification clause
    indem_detail = next((d for d in data["details"] if "14.1" in d["canonical_path"]), None)
    if indem_detail:
        assert indem_detail["risk_level"] == "HIGH"
        assert "<ins" in indem_detail["redline_html"]


def test_it_2_rest_endpoints_retrieval_and_candidate_listing(tmp_path: Path):
    """IT-2: Test GET /variance and GET /variance/templates endpoints."""
    db_path = str(tmp_path / "invenergy_it2_variance.db")
    cfg = PipelineConfig(sqlite_db_path=db_path, use_bigquery=False, auth_enabled=False)
    storage = ContractStorageService(config=cfg)
    app = create_app(config=cfg, storage=storage)

    storage.seed_sample_templates()

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO documents (
            document_id, project_id, landowner_id, filename, file_sha256,
            ingestion_status, energy_technology, state, country, form_type, page_count
        ) VALUES ('doc_it2_retrieval', 'prj_retrieval', 'lnd_smith', 'Smith_Solar_Lease.pdf', 'hash_it2', 'PROCESSED', 'SOLAR', 'TX', 'USA', 'LEASE', 3)"""
    )
    cur.execute(
        """INSERT INTO clauses (
            node_id, document_id, canonical_path, clause_label, clause_title,
            verbatim_text, reconstructed_context_text, page_start, page_end, depth
        ) VALUES ('node_it2_1', 'doc_it2_retrieval', 'BODY.1.1', '1.1', 'Grant of Lease',
            'Owner grants lease to Operator for solar energy development.', 'Owner grants lease to Operator for solar energy development.', 1, 1, 1)"""
    )
    conn.commit()
    conn.close()

    client = TestClient(app)

    # 1. Candidate templates endpoint: GET /api/v1/contracts/{id}/variance/templates
    res_candidates = client.get("/api/v1/contracts/doc_it2_retrieval/variance/templates")
    assert res_candidates.status_code == 200
    candidates = res_candidates.json()
    assert len(candidates) >= 1
    # First candidate should be TX Solar Lease
    assert candidates[0]["energy_technology"] in ["SOLAR", "UTILITY_SOLAR"] or candidates[0]["region"] == "TX"
    assert candidates[0]["affinity_score"] > 0.0

    # 2. Query variance prior to running comparison -> 404 Not Found
    res_not_found = client.get("/api/v1/contracts/doc_it2_retrieval/variance")
    assert res_not_found.status_code == 404

    # 3. Run comparison
    res_run = client.post("/api/v1/contracts/doc_it2_retrieval/compare")
    assert res_run.status_code == 200

    # 4. Query variance after running comparison -> 200 OK
    res_found = client.get("/api/v1/contracts/doc_it2_retrieval/variance")
    assert res_found.status_code == 200
    bundle_data = res_found.json()
    assert bundle_data["variance"]["document_id"] == "doc_it2_retrieval"
    assert len(bundle_data["details"]) >= 1
    assert any(d["contract_clause_id"] == "node_it2_1" for d in bundle_data["details"])


def test_it_3_rbac_permission_enforcement(tmp_path: Path):
    """IT-3: Test that VIEWER can read variance, but only ADMIN/ANALYST can trigger comparison."""
    db_path = str(tmp_path / "invenergy_it3_rbac.db")
    cfg = PipelineConfig(sqlite_db_path=db_path, use_bigquery=False, auth_enabled=True)
    storage = ContractStorageService(config=cfg)
    app = create_app(config=cfg, storage=storage)

    storage.seed_sample_templates()

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO documents (
            document_id, project_id, landowner_id, filename, file_sha256,
            ingestion_status, energy_technology, state, country, form_type, page_count
        ) VALUES ('doc_rbac_var', 'prj_rbac', 'lnd_rbac', 'RBAC_Lease.pdf', 'hash_rbac', 'PROCESSED', 'ONSHORE_WIND', 'IL', 'USA', 'LEASE', 2)"""
    )
    cur.execute(
        """INSERT INTO clauses (
            node_id, document_id, canonical_path, clause_label, clause_title,
            verbatim_text, reconstructed_context_text, page_start, page_end, depth
        ) VALUES ('node_rbac_1', 'doc_rbac_var', 'BODY.1.1', '1.1', 'Term',
            'Lease term is 30 years.', 'Lease term is 30 years.', 1, 1, 1)"""
    )
    conn.commit()
    conn.close()

    client = TestClient(app)

    # 1. Viewer token attempt:
    # - Reading candidates -> 200 OK
    # - Running comparison -> 403 Forbidden
    with patch("firebase_admin.auth.verify_id_token", return_value={"email": "viewer@invenergy.com", "uid": "viewer_uid"}):
        res_cand = client.get(
            "/api/v1/contracts/doc_rbac_var/variance/templates",
            headers={"Authorization": "Bearer viewer_token"},
        )
        assert res_cand.status_code == 200

        res_comp = client.post(
            "/api/v1/contracts/doc_rbac_var/compare",
            headers={"Authorization": "Bearer viewer_token"},
        )
        assert res_comp.status_code == 403

    # 2. Admin token attempt:
    # - Running comparison -> 200 OK
    with patch("firebase_admin.auth.verify_id_token", return_value={"email": "admin@google.com", "uid": "admin_uid"}):
        res_comp_admin = client.post(
            "/api/v1/contracts/doc_rbac_var/compare",
            headers={"Authorization": "Bearer admin_token"},
        )
        assert res_comp_admin.status_code == 200
        assert res_comp_admin.json()["variance"]["document_id"] == "doc_rbac_var"

        # Viewer can now read the computed variance
        with patch("firebase_admin.auth.verify_id_token", return_value={"email": "viewer@invenergy.com", "uid": "viewer_uid"}):
            res_get_var = client.get(
                "/api/v1/contracts/doc_rbac_var/variance",
                headers={"Authorization": "Bearer viewer_token"},
            )
            assert res_get_var.status_code == 200
            assert res_get_var.json()["variance"]["document_id"] == "doc_rbac_var"
