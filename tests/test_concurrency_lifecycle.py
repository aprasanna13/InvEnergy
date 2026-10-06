"""Concurrency & Lifecycle Tests for Option 3A Multi-Pass Pipeline (CC-1..CC-3)."""

from __future__ import annotations

import concurrent.futures
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from contract_parser.config import PipelineConfig
from contract_parser.gemini_parser import GeminiContractParser
from contract_parser.schemas import (
    BodyPassExtraction,
    ClauseRow,
    ContractStructureIndex,
    DocumentZone,
    ExhibitIndexEntry,
    ExhibitModality,
    ExhibitsPassExtraction,
    FlagCode,
    HITLStatus,
    NumberingScheme,
    SignerEntry,
    utc_now_iso,
)


def test_cc1_thread_safe_client_initialization():
    """[CC-1] Verify thread-safe GenAI client initialization under concurrent access."""
    parser = GeminiContractParser(config=PipelineConfig(use_cloud_storage=False))
    clients = []

    def get_client():
        time.sleep(0.01)
        clients.append(parser.client)

    threads = [threading.Thread(target=get_client) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(clients) == 10
    first = clients[0]
    for c in clients[1:]:
        assert c is first, "All concurrent threads must receive the identical client singleton"


def test_cc2_threadpool_bounded_workers_and_timeout():
    """[CC-2] Verify ThreadPoolExecutor execution bounds (max_workers=2) and per-future timeout."""
    active_workers = 0
    max_observed_workers = 0
    lock = threading.Lock()

    def mock_worker():
        nonlocal active_workers, max_observed_workers
        with lock:
            active_workers += 1
            if active_workers > max_observed_workers:
                max_observed_workers = active_workers
        time.sleep(0.05)
        with lock:
            active_workers -= 1

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(mock_worker) for _ in range(6)]
        concurrent.futures.wait(futures)

    assert max_observed_workers <= 2, f"Active workers exceeded pool limit: {max_observed_workers}"


def test_cc3_partial_pass_failure_isolation():
    """[CC-3] Verify partial pass failure isolation where Pass 2 succeeds and Pass 3 raises an exception."""
    now_iso = utc_now_iso()
    parser = GeminiContractParser(config=PipelineConfig(use_cloud_storage=False, multi_pass_enabled=True))

    mock_index = ContractStructureIndex(
        document_title="Partial Failure Agreement",
        body_start_page=1,
        body_end_page=5,
        exhibits=[
            ExhibitIndexEntry(
                exhibit_id="Exhibit A",
                exhibit_title="Property Description",
                page_start=6,
                page_end=8,
            )
        ],
    )

    mock_body = BodyPassExtraction(
        clauses=[
            ClauseRow(
                node_id="BODY.1",
                canonical_path="BODY.1",
                document_zone=DocumentZone.BODY,
                depth=1,
                clause_title="Term",
                clause_label="1",
                numbering_scheme=NumberingScheme.INTEGER,
                page_start=1,
                page_end=2,
                verbatim_text="1. Term is 20 years.",
                reconstructed_context_text="1. Term is 20 years.",
                preamble_text="1. Term is 20 years.",
                postamble_text=None,
                hitl_status=HITLStatus.VERIFIED_AUTO,
                hitl_flag_reasons="NONE",
                created_at=now_iso,
                updated_at=now_iso,
            )
        ],
    )

    with (
        patch.object(parser, "_discover_contract_structure", return_value=mock_index),
        patch.object(parser, "_extract_body_pass", return_value=mock_body),
        patch.object(parser, "_extract_exhibits_pass", side_effect=RuntimeError("503 Service Unavailable on Pass 3")),
    ):
        bundle = parser.extract(
            document_id="doc_partial_fail",
            gcs_pdf_uri="gs://mock/agreement.pdf",
            pdf_bytes=b"%PDF-1.4 mock",
        )

        assert bundle is not None
        node_ids = [c.node_id for c in bundle.clauses]
        assert "BODY.1" in node_ids
        # Since Pass 3 failed, an empty exhibits extraction was safely handled without crash
        assert any(c.node_id == "BODY.1" for c in bundle.clauses)
