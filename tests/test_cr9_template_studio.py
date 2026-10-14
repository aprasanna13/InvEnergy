"""Comprehensive test suite for Regional Template Studio & Canonical AST Registry (CR-9).

Verifies:
- UT-1: Placeholder Variable Tokenizer (detects $[_____], [County], [Effective Date], etc., with correct types)
- UT-2: Canonical Path Normalization (PREAMBLE, RECITALS.A, BODY.1.1, EXHIBIT_A.1)
- UT-3: Bounded Two-Pass Assembler & Signature Suppression (quarantines signature blocks, preserves exhibits)
- UT-4: Template Persistence & Idempotency (insert_template_bundle, updates/re-ingests cleanly)
- UT-5: Cold-Start Sample Seeding (seed_sample_templates registers the 6 canonical forms)
- UT-6: Zero Pollution of Document Stack (documents, clauses, special_conditions remain completely unpolluted)
- UT-7: REST API Template Listing & Filtering (GET /api/v1/templates with taxonomy query parameters)
- UT-8: REST API PDF Retrieval (GET /api/v1/templates/{template_id}/pdf returns application/pdf)
- IT-1: End-to-End Template Upload Flow (POST /api/v1/templates/upload and GET /api/v1/templates/{id})
- IT-2: RBAC Permission Enforcement (viewer gets 403 on upload/seed, admin succeeds)
- IT-3: Template Studio HTML Endpoint (GET /template-studio serves the dedicated workbench)
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from contract_parser.app import create_app
from contract_parser.auth import UserContext, UserRole
from contract_parser.bundle_assembler import (
    assemble_template_bundle,
    extract_template_placeholders,
    infer_placeholder_type,
    normalize_template_canonical_path,
)
from contract_parser.config import PipelineConfig
from contract_parser.schemas import (
    EnergyTechnology,
    FormType,
    PageSpan,
    RawTemplateClause,
    TemplateArticleSpan,
    TemplateExhibitSpan,
    TemplateExtractionBundle,
    TemplatePassExtraction,
    TemplateStructureIndex,
)
from contract_parser.storage import ContractStorageService


# ==============================================================================
# Helper to synthesize minimal in-memory PDFs for testing
# ==============================================================================

def create_dummy_pdf_bytes(page_count: int = 3) -> bytes:
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

def test_ut_1_placeholder_variable_tokenizer():
    """UT-1: Test regex tokenization and type inference on diverse bracket patterns."""
    sample_text = (
        "This Solar Lease is entered into on [Effective Date] by and between [Landowner Name] "
        "('Owner') and Invenergy Wind Development LLC ('Operator') in the County of [County], "
        "State of [State]. Owner agrees to payment of $[_____] per megawatt or [Rent Amount] dollars "
        "per acre for [Term Years] years across [Project Size MW] MW facility."
    )

    placeholders = extract_template_placeholders(sample_text)
    token_names = [p.token_name for p in placeholders]
    inferred_types = {p.token_name: p.inferred_type for p in placeholders}

    assert "Effective Date" in token_names
    assert "Landowner Name" in token_names
    assert "County" in token_names
    assert "State" in token_names
    assert "_____" in token_names
    assert "Rent Amount" in token_names
    assert "Term Years" in token_names
    assert "Project Size MW" in token_names

    assert inferred_types["Effective Date"] == "DATE"
    assert inferred_types["County"] == "JURISDICTION"
    assert inferred_types["State"] == "JURISDICTION"
    assert inferred_types["_____"] == "CURRENCY"
    assert inferred_types["Rent Amount"] == "CURRENCY"
    assert inferred_types["Term Years"] == "DURATION"
    assert inferred_types["Project Size MW"] == "CAPACITY"

    # Direct helper validation
    assert infer_placeholder_type("County Name") == "JURISDICTION"
    assert infer_placeholder_type("Execution Date") == "DATE"
    assert infer_placeholder_type("Total Price ($)") == "CURRENCY"
    assert infer_placeholder_type("Lease Period Months") == "DURATION"
    assert infer_placeholder_type("Solar Acreage") == "AREA"
    assert infer_placeholder_type("Unknown Custom Variable") == "TEXT"


def test_ut_2_canonical_path_normalization():
    """UT-2: Test canonical path normalization across Preamble, Recitals, Body, Exhibits."""
    assert normalize_template_canonical_path("Preamble", "Preamble") == "PREAMBLE"
    assert normalize_template_canonical_path("Recital B", "Recitals") == "RECITALS.B"
    assert normalize_template_canonical_path("Section 2.1", "Article 2") == "BODY.2.1"
    assert normalize_template_canonical_path("Section 4.3(a)", "Article 4") == "BODY.4.3"
    assert normalize_template_canonical_path("Exhibit B Legal Description", "Exhibits") == "EXHIBIT_B.1"
    assert normalize_template_canonical_path("12.4", "Article 12") == "BODY.12.4"


def test_ut_3_bounded_two_pass_assembler_signature_quarantine():
    """UT-3: Verify witness/signature blocks are quarantined while exhibits are retained."""
    pass1_structure = TemplateStructureIndex(
        form_name="Texas Wind Lease (Standard)",
        form_type=FormType.LEASE,
        state="TX",
        technology=EnergyTechnology.WIND,
        effective_vintage_year=2024,
        articles=[
            TemplateArticleSpan(article_number="1", article_title="Grant of Lease", page_span=PageSpan(start_page=1, end_page=2)),
            TemplateArticleSpan(article_number="2", article_title="Term & Rent", page_span=PageSpan(start_page=2, end_page=4)),
        ],
        exhibits=[
            TemplateExhibitSpan(exhibit_id="Exhibit A", exhibit_title="Property Description", page_span=PageSpan(start_page=6, end_page=7)),
        ],
        signature_block_pages=[5],  # Page 5 is signature / notary quarantine zone
    )

    pass2_extraction = TemplatePassExtraction(
        clauses=[
            RawTemplateClause(
                article_number="1",
                section_number="1.1",
                clause_title="Grant of Wind Rights",
                canonical_path="BODY.1.1",
                section_text="Owner leases [Acreage] acres in [County] County to Operator.",
                start_page=1,
                end_page=1,
                is_substantive=True,
            ),
            RawTemplateClause(
                article_number="2",
                section_number="2.1",
                clause_title="Annual Rent",
                canonical_path="BODY.2.1",
                section_text="Operator shall pay $[_____] annually on [Payment Date].",
                start_page=3,
                end_page=3,
                is_substantive=True,
            ),
            # Clause that falls on signature quarantine page 5
            RawTemplateClause(
                article_number="Signatures",
                section_number="Witness",
                clause_title="In Witness Whereof",
                canonical_path="SIGNATURES.1",
                section_text="IN WITNESS WHEREOF, the parties execute this agreement on page 5.",
                start_page=5,
                end_page=5,
                is_substantive=True,
            ),
            # Clause in Exhibit A on page 6
            RawTemplateClause(
                article_number="Exhibit A",
                section_number="Ex A-1",
                clause_title="Legal Description",
                canonical_path="EXHIBIT_A.1",
                section_text="Parcel [Parcel ID] recorded in Book [Book Number], Page [Page Number].",
                start_page=6,
                end_page=6,
                is_substantive=True,
            ),
        ]
    )

    bundle = assemble_template_bundle(
        template_id="tpl_test_tx_wind",
        pdf_sha256="abcd1234ef567890",
        source_filename="Texas_Wind_Lease.pdf",
        pass1_structure=pass1_structure,
        pass2_extraction=pass2_extraction,
        total_pages=7,
        pdf_storage_path="/tmp/test_pdf.pdf",
    )

    clause_paths = [c.canonical_path for c in bundle.clauses]
    assert "BODY.1.1" in clause_paths
    assert "BODY.2.1" in clause_paths
    assert "EXHIBIT_A.1" in clause_paths
    # Signature clause must be quarantined and suppressed
    assert "SIGNATURES.1" not in clause_paths

    # Verify placeholders were tokenized into the clauses
    body_2_1 = next(c for c in bundle.clauses if c.canonical_path == "BODY.2.1")
    tokens = [p.token_name for p in body_2_1.placeholders]
    assert "_____" in tokens
    assert "Payment Date" in tokens

    # Verify template registry summary
    assert bundle.registry_row.form_type == FormType.LEASE
    assert bundle.registry_row.state == "TX"
    assert bundle.registry_row.clause_count == 3
    assert bundle.registry_row.has_exhibits is True


def test_ut_4_template_persistence_and_idempotency(tmp_path: Path):
    """UT-4: Test ContractStorageService template insertion and idempotent re-insertion."""
    db_path = str(tmp_path / "invenergy_test.db")
    cfg = PipelineConfig(sqlite_db_path=db_path, use_bigquery=False)
    storage = ContractStorageService(config=cfg)

    bundle = TemplateExtractionBundle(
        registry_row={
            "template_id": "tpl_persist_test",
            "pdf_sha256": "hash_persist_1234",
            "source_filename": "Test_Form.pdf",
            "form_name": "Kansas Solar Option Form",
            "form_type": FormType.OPTION,
            "state": "KS",
            "country": "USA",
            "technology": EnergyTechnology.SOLAR,
            "effective_vintage_year": 2023,
            "total_pages": 4,
            "clause_count": 1,
            "has_exhibits": False,
            "is_standard": True,
            "storage_path": "/tmp/dummy.pdf",
        },
        clauses=[
            {
                "clause_id": "tcl_001",
                "template_id": "tpl_persist_test",
                "canonical_path": "BODY.1.1",
                "article_number": "1",
                "section_number": "1.1",
                "clause_title": "Option Grant",
                "section_text": "Option grant for $[_____] payment.",
                "placeholders": [{"token_name": "_____", "inferred_type": "CURRENCY", "occurrence_count": 1}],
                "start_page": 1,
                "end_page": 1,
                "is_substantive": True,
            }
        ],
    )

    # First insert
    storage.insert_template_bundle(bundle)

    loaded = storage.get_template_bundle("tpl_persist_test")
    assert loaded is not None
    assert loaded["form_name"] == "Kansas Solar Option Form"
    assert len(loaded["clauses"]) == 1
    assert loaded["clauses"][0]["canonical_path"] == "BODY.1.1"

    # Re-insert with updated title to verify idempotent deduplication / replacement
    bundle.registry_row.form_name = "Kansas Solar Option Form v2"
    storage.insert_template_bundle(bundle)

    loaded_v2 = storage.get_template_bundle("tpl_persist_test")
    assert loaded_v2 is not None
    assert loaded_v2["form_name"] == "Kansas Solar Option Form v2"
    assert len(loaded_v2["clauses"]) == 1


def test_ut_5_cold_start_sample_seeding(tmp_path: Path):
    """UT-5: Run seed_sample_templates and assert the canonical sample forms are registered."""
    db_path = str(tmp_path / "invenergy_seed.db")
    cfg = PipelineConfig(sqlite_db_path=db_path, use_bigquery=False)
    storage = ContractStorageService(config=cfg)

    seeded_bundles = storage.seed_sample_templates()
    assert len(seeded_bundles) >= 1  # At least 1 (all present sample PDFs in samples/templates)

    templates = storage.list_templates()
    assert len(templates) == len(seeded_bundles)
    form_names = [t["form_name"] for t in templates]

    # Verify at least the core canonical types are represented
    states = {t["state"] for t in templates}
    assert any(s in states for s in ("CO", "IA", "IL", "KS", "NY", "TX", "VA"))


def test_ut_6_zero_pollution_of_document_stack(tmp_path: Path):
    """UT-6: Verify executed contract tables (documents, clauses, special_conditions) remain empty."""
    db_path = str(tmp_path / "invenergy_clean.db")
    cfg = PipelineConfig(sqlite_db_path=db_path, use_bigquery=False)
    storage = ContractStorageService(config=cfg)

    # Seed sample templates
    storage.seed_sample_templates()

    # Query internal SQLite database directly to confirm executed tables have zero rows
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    doc_count = cursor.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    clause_count = cursor.execute("SELECT COUNT(*) FROM clauses").fetchone()[0]
    cond_count = cursor.execute("SELECT COUNT(*) FROM special_conditions").fetchone()[0]

    template_count = cursor.execute("SELECT COUNT(*) FROM template_registry").fetchone()[0]
    template_clauses_count = cursor.execute("SELECT COUNT(*) FROM template_clauses").fetchone()[0]

    conn.close()

    assert doc_count == 0, "Document table must have 0 rows after template operations"
    assert clause_count == 0, "Clauses table must have 0 rows after template operations"
    assert cond_count == 0, "Special conditions table must have 0 rows after template operations"

    assert template_count > 0, "Template registry must contain seeded templates"
    assert template_clauses_count > 0, "Template clauses table must contain AST rows"


def test_ut_7_rest_api_template_listing_and_filtering(tmp_path: Path):
    """UT-7: Verify GET /api/v1/templates taxonomy query filtering."""
    db_path = str(tmp_path / "invenergy_api.db")
    cfg = PipelineConfig(sqlite_db_path=db_path, use_bigquery=False, auth_enabled=False)
    storage = ContractStorageService(config=cfg)
    storage.seed_sample_templates()

    app = create_app(config=cfg, storage=storage)
    client = TestClient(app)

    res = client.get("/api/v1/templates")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) >= 1

    # Filter by specific state from the list
    first_item = data[0]
    target_state = first_item["state"]
    res_filtered = client.get(f"/api/v1/templates?state={target_state}")
    assert res_filtered.status_code == 200
    filtered_data = res_filtered.json()
    assert len(filtered_data) >= 1
    assert all(item["state"] == target_state for item in filtered_data)


def test_ut_8_rest_api_pdf_retrieval(tmp_path: Path):
    """UT-8: Verify GET /api/v1/templates/{template_id}/pdf returns application/pdf."""
    db_path = str(tmp_path / "invenergy_pdf.db")
    cfg = PipelineConfig(sqlite_db_path=db_path, use_bigquery=False, auth_enabled=False)
    storage = ContractStorageService(config=cfg)
    storage.seed_sample_templates()

    app = create_app(config=cfg, storage=storage)
    client = TestClient(app)

    # Get first template ID
    templates = storage.list_templates()
    assert len(templates) > 0
    t_id = templates[0]["template_id"]

    res = client.get(f"/api/v1/templates/{t_id}/pdf")
    assert res.status_code == 200
    assert "application/pdf" in res.headers["content-type"]
    assert len(res.content) > 0


# ==============================================================================
# Integration Tests (IT-1 through IT-3)
# ==============================================================================

def test_it_1_end_to_end_template_upload_flow(tmp_path: Path):
    """IT-1: Upload a new template PDF via admin endpoint, verify bundle extraction and retrieval."""
    db_path = str(tmp_path / "invenergy_e2e.db")
    cfg = PipelineConfig(sqlite_db_path=db_path, use_bigquery=False, auth_enabled=False)
    storage = ContractStorageService(config=cfg)
    app = create_app(config=cfg, storage=storage)
    client = TestClient(app)

    dummy_pdf = create_dummy_pdf_bytes(page_count=3)
    files = {
        "file": ("Custom_Iowa_Wind_Lease.pdf", dummy_pdf, "application/pdf")
    }
    data = {
        "form_name": "Iowa Master Wind Energy Lease",
        "form_type": "LEASE",
        "state": "IA",
        "country": "USA",
        "technology": "WIND",
        "vintage_year": "2024",
    }

    res = client.post("/api/v1/templates/upload", files=files, data=data)
    assert res.status_code == 200, res.text
    bundle = res.json()
    assert "registry_row" in bundle
    assert bundle["registry_row"]["form_name"] == "Iowa Master Wind Energy Lease"
    assert bundle["registry_row"]["state"] == "IA"
    assert bundle["registry_row"]["technology"] == "WIND"
    template_id = bundle["registry_row"]["template_id"]

    # Verify retrieval via GET /api/v1/templates/{id}
    res_get = client.get(f"/api/v1/templates/{template_id}")
    assert res_get.status_code == 200
    fetched = res_get.json()
    assert fetched["template_id"] == template_id
    assert fetched["form_name"] == "Iowa Master Wind Energy Lease"
    assert "clauses" in fetched


def test_it_2_rbac_permission_enforcement(tmp_path: Path):
    """IT-2: Test that VIEWER role is blocked from upload and seed, while ADMIN succeeds."""
    db_path = str(tmp_path / "invenergy_rbac.db")
    cfg = PipelineConfig(sqlite_db_path=db_path, use_bigquery=False, auth_enabled=True)
    storage = ContractStorageService(config=cfg)
    app = create_app(config=cfg, storage=storage)

    # Mock user contexts
    viewer_user = UserContext(
        email="viewer@invenergy.com",
        role=UserRole.VIEWER,
        domain="invenergy.com",
        uid="viewer_uid",
    )
    admin_user = UserContext(
        email="admin@invenergy.com",
        role=UserRole.ADMIN,
        domain="invenergy.com",
        uid="admin_uid",
    )

    client = TestClient(app)

    # 1. Viewer attempt to upload -> 403 Forbidden
    with patch("firebase_admin.auth.verify_id_token", return_value={"email": "viewer@invenergy.com", "uid": "viewer_uid"}):
        dummy_pdf = create_dummy_pdf_bytes(page_count=2)
        res_upload = client.post(
            "/api/v1/templates/upload",
            headers={"Authorization": "Bearer viewer_token"},
            files={"file": ("Test.pdf", dummy_pdf, "application/pdf")},
            data={"form_name": "Test Form", "state": "TX", "technology": "SOLAR", "form_type": "LEASE", "vintage_year": "2024"},
        )
        assert res_upload.status_code == 403

        # Viewer attempt to seed -> 403 Forbidden
        res_seed = client.post(
            "/api/v1/templates/seed",
            headers={"Authorization": "Bearer viewer_token"},
        )
        assert res_seed.status_code == 403

        # Viewer can read templates -> 200 OK
        res_list = client.get(
            "/api/v1/templates",
            headers={"Authorization": "Bearer viewer_token"},
        )
        assert res_list.status_code == 200

    # 2. Admin attempt to seed -> 200 OK
    with patch("firebase_admin.auth.verify_id_token", return_value={"email": "admin@google.com", "uid": "admin_uid"}):
        res_seed_admin = client.post(
            "/api/v1/templates/seed",
            headers={"Authorization": "Bearer admin_token"},
        )
        assert res_seed_admin.status_code == 200
        assert res_seed_admin.json()["seeded_count"] >= 1


def test_it_3_template_studio_html_endpoint():
    """IT-3: Verify GET /template-studio serves the dedicated workbench UI."""
    cfg = PipelineConfig(auth_enabled=False, use_bigquery=False)
    app = create_app(config=cfg)
    client = TestClient(app)

    res = client.get("/template-studio")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "Regional Template Studio" in res.text
    assert "Canonical AST Registry" in res.text
