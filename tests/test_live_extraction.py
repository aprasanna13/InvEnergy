"""Live End-to-End Extraction Testing on Real-World Scanned 33-Page Solar Lease (E2E-1..E2E-6)."""

from __future__ import annotations

import os
import time
from pathlib import Path
import pytest

from contract_parser.config import PipelineConfig
from contract_parser.gemini_parser import GeminiContractParser


@pytest.mark.live
def test_sokgrn0003_multipass_live_extraction():
    """Execute live multi-pass extraction on SOKGRN0003_Solar Lease and Easement Agreement_Green Bandit LLC_111418.pdf."""
    sample_path = Path("samples/SOKGRN0003_Solar Lease and Easement Agreement_Green Bandit LLC_111418.pdf")
    if not sample_path.exists():
        pytest.skip(f"Sample PDF not found at {sample_path}")

    pdf_bytes = sample_path.read_bytes()
    config = PipelineConfig(
        use_cloud_storage=False,
        use_bigquery=False,
        multi_pass_enabled=True,
    )
    parser = GeminiContractParser(config=config)

    t0 = time.time()
    bundle = parser.extract(
        document_id="doc_sokgrn0003_live",
        gcs_pdf_uri=f"gs://mock/{sample_path.name}",
        pdf_bytes=pdf_bytes,
        project_id="prj_nowata",
        landowner_id="lnd_green_bandit",
    )
    elapsed = time.time() - t0

    print(f"\nLive Multi-Pass Extraction completed in {elapsed:.2f}s")
    print(f"Total clauses extracted: {len(bundle.clauses)}")
    print(f"Total defined terms: {len(bundle.defined_terms)}")
    print(f"Total exhibits in catalog: {len(bundle.exhibits_catalog)}")
    print(f"Total special conditions: {len(bundle.special_conditions)}")

    # [E2E-1] Total clause recall > 65
    assert len(bundle.clauses) > 65, f"Expected >65 clauses, got {len(bundle.clauses)}"

    # [E2E-2] Agreement body coverage across Sections 1 through 14
    body_nodes = {c.node_id for c in bundle.clauses}
    for sec_num in range(1, 15):
        assert any(
            nid == f"BODY.{sec_num}" or nid.startswith(f"BODY.{sec_num}.")
            for nid in body_nodes
        ), f"Missing Section {sec_num} in body clauses"

    # [E2E-3] Full exhibits coverage
    assert any(nid.startswith("EXHIBIT_A") for nid in body_nodes), "Missing Exhibit A clauses"
    assert any(nid.startswith("EXHIBIT_B") for nid in body_nodes), "Missing Exhibit B clauses"
    assert any(nid.startswith("EXHIBIT_C") for nid in body_nodes), "Missing Exhibit C clauses"
    assert any(nid.startswith("EXHIBIT_D") for nid in body_nodes), "Missing Exhibit D clauses"

    # [E2E-4] Defined terms recall > 30
    assert len(bundle.defined_terms) > 30, f"Expected >30 defined terms, got {len(bundle.defined_terms)}"

    # [E2E-5] Landowner special conditions
    assert len(bundle.special_conditions) >= 5, f"Expected >=5 special conditions, got {len(bundle.special_conditions)}"

    # [E2E-6] Wall-clock latency < 360s (pro reasoning model on 33 scanned pages)
    assert elapsed < 360.0, f"Expected wall-clock time < 360s, got {elapsed:.2f}s"
