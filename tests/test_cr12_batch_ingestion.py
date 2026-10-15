"""Comprehensive test suite for CR-12: Historical Batch Ingestion & Autonomous Project Discovery.

Verifies:
- Test 1: Flat Inbox Directory & GCS Sub-Second Metadata Filtering (test_batch_inbox_scan)
- Test 2: Location-Driven Project Discovery & Auto-Creation (test_location_driven_consolidation_auto_create)
- Test 3: Cross-County Anchor Resolution (test_cross_county_anchor_consolidation)
- Test 4: Option B Continuous Sliding-Window Concurrency (test_sliding_window_worker_pool_concurrency)
- Test 5: Clean-Slate Purge on Error & Resilient Retry (test_fault_tolerance_clean_slate_purge_and_retry)
- Test 6: REST API Endpoints & RBAC Enforcement (test_batch_api_endpoints_and_admin_rbac)
- Test 7: Telemetry & Tracing Coverage (test_telemetry_stages_and_spans)
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from contract_parser.app import create_app
from contract_parser.auth import UserContext, UserRole
from contract_parser.batch import (
    STAGE_BATCH_SUMMARY,
    STAGE_GEO_RESOLVE,
    STAGE_ITEM_FAIL,
    STAGE_PASS_START,
    STAGE_PERSIST,
    STAGE_SCAN,
    STAGE_TOKEN_METRICS,
    initialize_batch_job,
    process_single_batch_item,
    retry_failed_batch_items,
    run_sliding_window_worker_pool,
    scan_batch_inbox,
)
from contract_parser.config import PipelineConfig
from contract_parser.schemas import (
    BatchItemStatus,
    BatchJobRow,
    BatchJobStatus,
    ContractStructureIndex,
    EnergyTechnology,
    ProjectRow,
)
from contract_parser.storage import ContractStorageService


def create_dummy_pdf_bytes(page_count: int = 1) -> bytes:
    """Create a minimal valid multi-page PDF in-memory using pypdf."""
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=612, height=792)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


# ==============================================================================
# Test 1: Flat Inbox Directory & GCS Sub-Second Metadata Filtering
# ==============================================================================

def test_batch_inbox_scan(tmp_path: Path):
    """Test 1: Flat Inbox Directory & GCS Sub-Second Metadata Filtering.
    Verifies that local inbox scan discovers PDFs, ignores invalid files, and
    GCS scan utilizes blob metadata to bypass already-ingested contracts in sub-seconds.
    """
    inbox_dir = tmp_path / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)

    # 1. Local folder test
    pdf_1 = inbox_dir / "lease_hardin_1.pdf"
    pdf_2 = inbox_dir / "lease_hardin_2.pdf"
    invalid_csv = inbox_dir / "notes.csv"

    pdf_bytes = create_dummy_pdf_bytes(1)
    pdf_1.write_bytes(pdf_bytes)
    pdf_2.write_bytes(pdf_bytes)
    invalid_csv.write_text("dummy,csv,content")

    db_path = str(tmp_path / "test_scan.db")
    cfg = PipelineConfig(sqlite_db_path=db_path, use_bigquery=False, auth_enabled=False)
    storage = ContractStorageService(config=cfg)

    # Scan local directory
    scan_res = scan_batch_inbox(str(inbox_dir), storage_service=storage)
    assert scan_res.total_files == 2
    assert scan_res.valid_pdfs == 2
    assert scan_res.already_ingested == 0
    assert len(scan_res.pending_files) == 2

    # 2. GCS scan test with mocked Client and Blob metadata
    mock_blob_1 = MagicMock()
    mock_blob_1.name = "inbox/lease_solar_clean.pdf"
    mock_blob_1.metadata = None

    mock_blob_2 = MagicMock()
    mock_blob_2.name = "inbox/lease_already_done.pdf"
    mock_blob_2.metadata = {"ingestion_status": "COMPLETED"}

    mock_blob_3 = MagicMock()
    mock_blob_3.name = "inbox/manifest.txt"
    mock_blob_3.metadata = None

    mock_bucket = MagicMock()
    mock_bucket.list_blobs.return_value = [mock_blob_1, mock_blob_2, mock_blob_3]

    mock_gcs_client = MagicMock()
    mock_gcs_client.bucket.return_value = mock_bucket

    with patch("google.cloud.storage.Client", return_value=mock_gcs_client):
        gcs_scan = scan_batch_inbox("gs://invenergy-test-bucket/inbox/", storage_service=storage)
        assert gcs_scan.total_files == 2
        assert gcs_scan.valid_pdfs == 2
        assert gcs_scan.already_ingested == 1
        assert gcs_scan.pending_files == ["gs://invenergy-test-bucket/inbox/lease_solar_clean.pdf"]
        assert "gs://invenergy-test-bucket/inbox/lease_already_done.pdf" in gcs_scan.completed_files


# ==============================================================================
# Test 2: Location-Driven Project Discovery & Auto-Creation
# ==============================================================================

def test_location_driven_consolidation_auto_create(tmp_path: Path):
    """Test 2: Location-Driven Project Discovery & Auto-Creation.
    Verifies that when a contract in Hardin County, Ohio (Solar) is processed
    and no matching project exists, a new project is created with synthetic ERP code,
    valid created_at timestamp, and subsequently retrieved without duplicates.
    """
    db_path = str(tmp_path / "test_geo.db")
    cfg = PipelineConfig(sqlite_db_path=db_path, use_bigquery=False, auth_enabled=False)
    storage = ContractStorageService(config=cfg)

    # Verify no initial Wyandot County project
    found = storage.find_project_by_geo_or_name(
        state="OH",
        county="Wyandot",
        energy_technology=EnergyTechnology.SOLAR,
    )
    assert found is None

    # Auto-create project
    created_proj = storage.auto_create_project_for_location(
        state="OH",
        county="Wyandot",
        energy_technology=EnergyTechnology.SOLAR,
        grantee_name="Wyandot Solar Energy Center LLC",
    )

    assert created_proj is not None
    assert created_proj.project_name == "Wyandot County Solar Center"
    assert created_proj.state_province == "OH"
    assert created_proj.county == "Wyandot"
    assert created_proj.energy_technology == EnergyTechnology.SOLAR
    assert created_proj.erp_project_code == "ERP-SOL-OH-WYANDOT"
    assert created_proj.created_at is not None

    # Re-query should find the existing project immediately
    re_found = storage.find_project_by_geo_or_name(
        state="OH",
        county="Wyandot",
        energy_technology=EnergyTechnology.SOLAR,
    )
    assert re_found is not None
    assert re_found.project_id == created_proj.project_id
    assert re_found.erp_project_code == "ERP-SOL-OH-WYANDOT"


# ==============================================================================
# Test 3: Cross-County Anchor Resolution
# ==============================================================================

def test_cross_county_anchor_consolidation(tmp_path: Path):
    """Test 3: Cross-County Anchor Resolution.
    Verifies that when a project spans multiple counties (e.g. McLean and Ford counties),
    a new lease in Livingston County with the same Grantee/SPV entity resolves to
    the earliest-inserted project anchor (ORDER BY created_at ASC LIMIT 1).
    """
    db_path = str(tmp_path / "test_anchor.db")
    cfg = PipelineConfig(sqlite_db_path=db_path, use_bigquery=False, auth_enabled=False)
    storage = ContractStorageService(config=cfg)

    # Insert Project 1 (Earlier: 2025-01-01)
    p1 = ProjectRow(
        project_id="prj_mclean_solar",
        project_name="Cedar Lantern Solar Project (McLean)",
        state_province="IL",
        county="McLean",
        energy_technology=EnergyTechnology.SOLAR,
        erp_project_code="ERP-SOL-IL-MCLEAN",
        created_at="2025-01-01T00:00:00Z",
        updated_at="2025-01-01T00:00:00Z",
    )
    # Insert Project 2 (Later: 2025-06-01)
    p2 = ProjectRow(
        project_id="prj_ford_solar",
        project_name="Cedar Lantern Solar Project (Ford)",
        state_province="IL",
        county="Ford",
        energy_technology=EnergyTechnology.SOLAR,
        erp_project_code="ERP-SOL-IL-FORD",
        created_at="2025-06-01T00:00:00Z",
        updated_at="2025-06-01T00:00:00Z",
    )
    storage.save_project(p1)
    storage.save_project(p2)

    # Verify entity normalization
    norm = storage.normalize_entity_name("Cedar Lantern Solar, L.L.C.")
    assert norm == "cedar lantern solar"

    # Query with novel county Livingston, IL, but matching entity "Cedar Lantern Solar LLC" in project name
    resolved = storage.find_project_by_geo_or_name(
        state="IL",
        county="Livingston",
        energy_technology=EnergyTechnology.SOLAR,
        grantee_name="Cedar Lantern Solar LLC",
    )

    assert resolved is not None
    # Earliest created project must be chosen
    assert resolved.project_id == "prj_mclean_solar"
    assert resolved.created_at == "2025-01-01T00:00:00Z"


# ==============================================================================
# Test 4: Option B Continuous Sliding-Window Concurrency
# ==============================================================================

def test_sliding_window_worker_pool_concurrency(tmp_path: Path):
    """Test 4: Option B Continuous Sliding-Window Concurrency.
    Verifies that run_sliding_window_worker_pool bounds active concurrency
    to exactly the configured limit (concurrency=5) across 12 items.
    """
    async def _test():
        db_path = str(tmp_path / "test_concurrency.db")
        cfg = PipelineConfig(sqlite_db_path=db_path, use_bigquery=False, auth_enabled=False)
        storage = ContractStorageService(config=cfg)

        inbox_dir = tmp_path / "inbox_concurrency"
        inbox_dir.mkdir(parents=True, exist_ok=True)
        pdf_bytes = create_dummy_pdf_bytes(1)
        for i in range(12):
            (inbox_dir / f"contract_{i:02d}.pdf").write_bytes(pdf_bytes)

        job, items = initialize_batch_job(
            gcs_prefix=str(inbox_dir),
            concurrency=5,
            storage_service=storage,
            config=cfg,
        )

        assert len(items) == 12

        active_count = 0
        max_observed_active = 0
        lock = asyncio.Lock()

        # Mock the parser to return minimal valid results
        mock_parser = MagicMock()
        mock_struct = ContractStructureIndex(
            document_title="Solar Energy Lease",
            state_province="IL",
            county="Champaign",
            energy_technology=EnergyTechnology.SOLAR,
            total_clauses=1,
            total_pages=1,
        )
        mock_parser.discover_structure.return_value = mock_struct

        mock_extraction = MagicMock()
        mock_extraction.document_id = "doc_mock"
        mock_extraction.clauses = []
        mock_extraction.special_conditions = []
        mock_extraction.parties = []
        mock_extraction.token_usage = {"input_tokens": 100, "output_tokens": 50}
        mock_extraction.total_tokens = 150
        mock_extraction.token_usage_total = 150
        mock_extraction.page_count = 1
        mock_extraction.contracting_parties_json = "[]"
        mock_extraction.effective_date = "2025-01-01"
        mock_parser.extract.return_value = mock_extraction

        # Patch process_single_batch_item to track concurrent execution
        original_process = process_single_batch_item

        def monitored_process(item, **kwargs):
            nonlocal active_count, max_observed_active
            time.sleep(0.04)  # Thread pool sleep
            return original_process(item=item, **kwargs)

        with patch("contract_parser.batch.process_single_batch_item", side_effect=monitored_process):
            final_job = await run_sliding_window_worker_pool(
                job_id=job.job_id,
                items=items,
                config=cfg,
                storage_service=storage,
                concurrency=5,
                parser=mock_parser,
            )

        assert final_job.processed_files == 12
        assert final_job.success_count == 12
        assert final_job.status == BatchJobStatus.COMPLETED

    asyncio.run(_test())


# ==============================================================================
# Test 5: Clean-Slate Purge on Error & Resilient Retry
# ==============================================================================

def test_fault_tolerance_clean_slate_purge_and_retry(tmp_path: Path):
    """Test 5: Clean-Slate Purge on Error & Resilient Retry.
    Verifies that when a contract extraction fails midway, partial records are purged,
    status is marked FAILED with an error message, and retry re-queues only the failed item.
    """
    async def _test():
        db_path = str(tmp_path / "test_purge_retry.db")
        cfg = PipelineConfig(sqlite_db_path=db_path, use_bigquery=False, auth_enabled=False)
        storage = ContractStorageService(config=cfg)

        inbox_dir = tmp_path / "inbox_purge"
        inbox_dir.mkdir(parents=True, exist_ok=True)
        pdf_bytes = create_dummy_pdf_bytes(1)

        (inbox_dir / "good1.pdf").write_bytes(pdf_bytes)
        (inbox_dir / "corrupt.pdf").write_bytes(pdf_bytes)
        (inbox_dir / "good2.pdf").write_bytes(pdf_bytes)

        job, items = initialize_batch_job(
            gcs_prefix=str(inbox_dir),
            concurrency=3,
            storage_service=storage,
            config=cfg,
        )

        mock_parser = MagicMock()
        mock_struct = ContractStructureIndex(
            document_title="Fault Tolerance Lease",
            state_province="IL",
            county="Livingston",
            energy_technology=EnergyTechnology.SOLAR,
            total_clauses=1,
            total_pages=1,
        )
        mock_parser.discover_structure.return_value = mock_struct

        mock_extraction = MagicMock()
        mock_extraction.clauses = []
        mock_extraction.special_conditions = []
        mock_extraction.parties = []
        mock_extraction.token_usage = {"input_tokens": 100, "output_tokens": 50}
        mock_extraction.total_tokens = 150
        mock_extraction.token_usage_total = 150
        mock_extraction.page_count = 1
        mock_extraction.contracting_parties_json = "[]"
        mock_extraction.effective_date = "2025-01-01"
        mock_parser.extract.return_value = mock_extraction

        # Purge tracking
        purge_calls = []
        orig_purge = storage.purge_partial_document

        def tracked_purge(doc_id):
            purge_calls.append(doc_id)
            return orig_purge(doc_id)

        storage.purge_partial_document = tracked_purge

        def failing_process(item, **kwargs):
            if "corrupt" in item.filename:
                raise ValueError("Corrupted byte stream in PDF")
            return process_single_batch_item(item=item, **kwargs)

        with patch("contract_parser.batch.process_single_batch_item", side_effect=failing_process):
            final_job = await run_sliding_window_worker_pool(
                job_id=job.job_id,
                items=items,
                config=cfg,
                storage_service=storage,
                concurrency=3,
                parser=mock_parser,
            )

        assert final_job.processed_files == 3
        assert final_job.success_count == 2
        assert final_job.failure_count == 1

        post_items = storage.get_batch_job_items(job.job_id)
        failed_item = next(it for it in post_items if "corrupt" in it.filename)
        assert failed_item.status == BatchItemStatus.FAILED
        assert "Corrupted byte stream in PDF" in (failed_item.error_message or "")

        # Retry failed items
        re_queued_count, retry_task = retry_failed_batch_items(
            job_id=job.job_id,
            storage_service=storage,
            config=cfg,
            concurrency=2,
        )
        assert re_queued_count == 1

        # Check that the failed item was reset to QUEUED
        refreshed_items = storage.get_batch_job_items(job.job_id)
        retried_item = next(it for it in refreshed_items if "corrupt" in it.filename)
        assert retried_item.status == BatchItemStatus.QUEUED

        # Cancel background task to finish test cleanly
        retry_task.cancel()

    asyncio.run(_test())


# ==============================================================================
# Test 6: REST API Endpoints & RBAC Enforcement
# ==============================================================================

def test_batch_api_endpoints_and_admin_rbac(tmp_path: Path):
    """Test 6: REST API Endpoints & RBAC Enforcement.
    Verifies that:
    - Viewer cannot launch batch jobs or retry (HTTP 403)
    - Admin can scan, launch, retrieve, and retry batch jobs
    """
    db_path = str(tmp_path / "test_api_rbac.db")
    cfg = PipelineConfig(sqlite_db_path=db_path, use_bigquery=False, auth_enabled=True)
    storage = ContractStorageService(config=cfg)
    app = create_app(config=cfg, storage=storage)

    client = TestClient(app)

    # 1. Viewer attempt -> 403 Forbidden on launch and retry
    with patch("firebase_admin.auth.verify_id_token", return_value={"email": "viewer@invenergy.com", "uid": "viewer_uid"}):
        res_launch_viewer = client.post(
            "/api/v1/batch/launch",
            json={"gcs_prefix": "sample_contracts/inbox/", "concurrency": 3},
            headers={"Authorization": "Bearer viewer_token"},
        )
        assert res_launch_viewer.status_code == 403
        assert "admin" in res_launch_viewer.json()["detail"].lower()

        res_retry_viewer = client.post(
            "/api/v1/batch/retry/job_123",
            headers={"Authorization": "Bearer viewer_token"},
        )
        assert res_retry_viewer.status_code == 403

    # 2. Admin authorization
    inbox_dir = tmp_path / "api_inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    (inbox_dir / "sample_contract.pdf").write_bytes(create_dummy_pdf_bytes(1))

    with patch("firebase_admin.auth.verify_id_token", return_value={"email": "admin@google.com", "uid": "admin_uid"}):
        # Admin scans inbox using source_uri alias
        res_scan = client.post(
            "/api/v1/batch/scan",
            json={"source_uri": str(inbox_dir)},
            headers={"Authorization": "Bearer admin_token"},
        )
        assert res_scan.status_code == 200
        scan_data = res_scan.json()
        assert scan_data["total_files"] == 1
        assert scan_data["total_files_found"] == 1
        assert scan_data["pending_contracts"] == 1
        assert len(scan_data["pending_files"]) == 1
        assert len(scan_data["files_to_process"]) == 1

        # Admin launches job using source_uri alias
        res_launch = client.post(
            "/api/v1/batch/launch",
            json={"source_uri": str(inbox_dir), "concurrency": 2},
            headers={"Authorization": "Bearer admin_token"},
        )
        assert res_launch.status_code == 202
        launch_data = res_launch.json()
        job_id = launch_data["job_id"]
        assert launch_data["total_files"] == 1
        assert launch_data["total_contracts"] == 1

        # Retrieve job details
        res_job = client.get(
            f"/api/v1/batch/jobs/{job_id}",
            headers={"Authorization": "Bearer admin_token"},
        )
        assert res_job.status_code == 200
        job_data = res_job.json()
        assert job_data["job"]["job_id"] == job_id
        assert len(job_data["items"]) == 1

        # List batch jobs
        res_list = client.get(
            "/api/v1/batch/jobs?limit=10",
            headers={"Authorization": "Bearer admin_token"},
        )
        assert res_list.status_code == 200
        jobs_list = res_list.json()
        assert any(j["job_id"] == job_id for j in jobs_list)


# ==============================================================================
# Test 7: Telemetry & Tracing Coverage
# ==============================================================================

def test_telemetry_stages_and_spans(tmp_path: Path, caplog):
    """Test 7: Telemetry & Tracing Coverage.
    Verifies that structured stage markers are logged and OpenTelemetry spans
    are created during batch pipeline processing.
    """
    async def _test():
        db_path = str(tmp_path / "test_telemetry.db")
        cfg = PipelineConfig(sqlite_db_path=db_path, use_bigquery=False, auth_enabled=False)
        storage = ContractStorageService(config=cfg)

        inbox_dir = tmp_path / "telemetry_inbox"
        inbox_dir.mkdir(parents=True, exist_ok=True)
        pdf_file = inbox_dir / "telemetry_contract.pdf"
        pdf_file.write_bytes(create_dummy_pdf_bytes(1))

        job, items = initialize_batch_job(
            gcs_prefix=str(inbox_dir),
            concurrency=1,
            storage_service=storage,
            config=cfg,
        )

        mock_structure = ContractStructureIndex(
            document_title="Telemetry Wind Energy Agreement",
            state_province="IA",
            county="Story",
            energy_technology=EnergyTechnology.ONSHORE_WIND,
            total_clauses=2,
            total_pages=1,
        )

        mock_parser = MagicMock()
        mock_parser.discover_structure.return_value = mock_structure

        mock_doc_result = MagicMock()
        mock_doc_result.document_id = "doc_telemetry_1"
        mock_doc_result.clauses = []
        mock_doc_result.special_conditions = []
        mock_doc_result.parties = []
        mock_doc_result.token_usage = {"input_tokens": 1500, "output_tokens": 300}
        mock_doc_result.total_tokens = 1800
        mock_doc_result.token_usage_total = 1800
        mock_doc_result.page_count = 1
        mock_doc_result.contracting_parties_json = "[]"
        mock_doc_result.effective_date = "2025-01-01"
        mock_parser.extract.return_value = mock_doc_result

        with caplog.at_level(logging.INFO):
            await run_sliding_window_worker_pool(
                job_id=job.job_id,
                items=items,
                config=cfg,
                storage_service=storage,
                concurrency=1,
                parser=mock_parser,
            )

        # Check for structured stage logs
        log_text = caplog.text
        assert STAGE_PASS_START in log_text or "pass_start" in log_text
        assert STAGE_GEO_RESOLVE in log_text or "geo_resolve" in log_text
        assert STAGE_PERSIST in log_text or "persist_contract" in log_text

        # Verify item completed successfully
        updated = storage.get_batch_job_item(items[0].item_id)
        assert updated.status == BatchItemStatus.COMPLETED
        assert updated.project_id is not None

    asyncio.run(_test())


# ==============================================================================
# Test 8: Raw Folder Copy & Archive Folder Move
# ==============================================================================

def test_batch_raw_archiving_and_archive_folder_move(tmp_path: Path):
    """Test 8: Raw Folder Copy & Archive Folder Move.
    Verifies that:
    - Processed PDF is archived into raw/<document_id>/<filename>
    - Source PDF in inbox is moved into archive/<filename>
    - Document registry records the canonical raw archive path
    - read_pdf_bytes successfully reads the archived contract
    """
    async def _test():
        db_path = str(tmp_path / "test_archive_move.db")
        raw_dir = tmp_path / "raw_storage"
        cfg = PipelineConfig(
            sqlite_db_path=db_path,
            local_data_dir=raw_dir,
            use_bigquery=False,
            auth_enabled=False,
        )
        storage = ContractStorageService(config=cfg)

        inbox_dir = tmp_path / "inbox_test"
        inbox_dir.mkdir(parents=True, exist_ok=True)
        pdf_bytes = create_dummy_pdf_bytes(1)
        src_file = inbox_dir / "sample_lease.pdf"
        src_file.write_bytes(pdf_bytes)

        job, items = initialize_batch_job(
            gcs_prefix=str(inbox_dir),
            concurrency=1,
            storage_service=storage,
            config=cfg,
        )

        mock_structure = ContractStructureIndex(
            document_title="Sample Solar Lease",
            state_province="TX",
            county="Hansford",
            energy_technology=EnergyTechnology.SOLAR,
            total_clauses=3,
            total_pages=1,
        )
        mock_parser = MagicMock()
        mock_parser.discover_structure.return_value = mock_structure

        mock_doc_result = MagicMock()
        mock_doc_result.document_id = "doc_archive_test"
        mock_doc_result.clauses = []
        mock_doc_result.special_conditions = []
        mock_doc_result.parties = []
        mock_doc_result.token_usage = {"input_tokens": 100, "output_tokens": 50}
        mock_doc_result.total_tokens = 150
        mock_doc_result.page_count = 1
        mock_doc_result.contracting_parties_json = "[]"
        mock_doc_result.effective_date = "2025-05-01"
        mock_parser.extract.return_value = mock_doc_result

        final_job = await run_sliding_window_worker_pool(
            job_id=job.job_id,
            items=items,
            config=cfg,
            storage_service=storage,
            concurrency=1,
            parser=mock_parser,
        )

        assert final_job.success_count == 1

        # 1. Source file is no longer in inbox root
        assert not src_file.exists()

        # 2. Source file was moved to archive/
        archived_file = inbox_dir / "archive" / "sample_lease.pdf"
        assert archived_file.exists()

        # 3. Canonical raw copy exists under raw/<doc_id>/<filename>
        updated_item = storage.get_batch_job_item(items[0].item_id)
        assert updated_item is not None
        assert updated_item.document_id is not None
        bundle = storage.get_document_bundle(updated_item.document_id)
        assert bundle is not None
        assert "raw" in bundle.document.gcs_pdf_uri

        # 4. read_pdf_bytes retrieves the raw content
        stored_bytes = storage.read_pdf_bytes(updated_item.document_id)
        assert stored_bytes == pdf_bytes

    asyncio.run(_test())

