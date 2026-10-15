"""Historical Batch Ingestion & Discovery Engine (CR-12).

Provides:
1. Option B Sliding-Window Worker Pool (Continuous 5 active workers).
2. Location-Driven Autonomous Project Consolidation & First-Inserted Anchor Algorithm.
3. Option 4a Sub-Second GCS Blob Metadata Pre-flight Scanning & Tagging.
4. Clean-Slate Purge on Retries & Failures.
5. Structured 6-Stage Cloud Logging & OpenTelemetry Distributed Tracing.
6. CLI Entrypoint (`python -m contract_parser.batch`).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google.cloud import storage

from contract_parser.config import PipelineConfig
from contract_parser.gemini_parser import (
    GeminiContractParser,
    HierarchicalContractParser,
    _make_landowner_id,
)
from contract_parser.storage import compute_document_id
from contract_parser.schemas import (
    BatchItemStatus,
    BatchJobItemRow,
    BatchJobRow,
    BatchJobStatus,
    BatchScanResponse,
    ContractStructureIndex,
    DocumentRegistryRow,
    EnergyTechnology,
    HITLStatus,
    IngestionStatus,
    ParsedContractBundle,
    ProjectRow,
    normalize_inbox_uri,
    utc_now_iso,
)
from contract_parser.storage import ContractStorageService
from contract_parser.telemetry import flush_telemetry, get_tracer

logger = logging.getLogger("contract_parser.batch")
tracer = get_tracer("contract_parser.batch")

# Structured Cloud Logging Stages per Section 6 of CR-12
STAGE_SCAN = "STAGE_SCAN"
STAGE_GEO_RESOLVE = "STAGE_GEO_RESOLVE"
STAGE_PASS_START = "STAGE_PASS_START"
STAGE_TOKEN_METRICS = "STAGE_TOKEN_METRICS"
STAGE_PERSIST = "STAGE_PERSIST"
STAGE_ITEM_FAIL = "STAGE_ITEM_FAIL"
STAGE_BATCH_SUMMARY = "STAGE_BATCH_SUMMARY"


def emit_stage_log(stage: str, message: str, **kwargs: Any) -> None:
    """Emit structured JSON log message conforming to Google Cloud Logging conventions."""
    payload = {
        "stage": stage,
        "message": message,
        "timestamp": utc_now_iso(),
        **kwargs,
    }
    logger.info(json.dumps(payload))


def scan_batch_inbox(
    prefix_or_dir: str,
    storage_service: ContractStorageService,
    force_reprocess: bool = False,
) -> BatchScanResponse:
    """Pre-flight scan of GCS inbox prefix or local directory.

    Implements Option 4a: Reads GCS custom blob metadata (`ingestion_status: COMPLETED`)
    without downloading PDF payloads for sub-second pre-flight indexing.
    """
    prefix_or_dir = normalize_inbox_uri(prefix_or_dir)
    with tracer.start_as_current_span("batch.scan_inbox") as span:
        span.set_attribute("batch.prefix_or_dir", prefix_or_dir)
        span.set_attribute("batch.force_reprocess", force_reprocess)

        pending_files: list[str] = []
        completed_files: list[str] = []
        invalid_count = 0

        if prefix_or_dir.startswith("gs://"):
            bucket_name, prefix = storage_service._split_gcs_uri(prefix_or_dir)
            bucket = storage_service.gcs_client.bucket(bucket_name)
            blobs = list(bucket.list_blobs(prefix=prefix))
            pdf_blobs = []
            for b in blobs:
                if b.name.endswith("/"):
                    continue
                if b.name.lower().endswith(".pdf"):
                    pdf_blobs.append(b)
                else:
                    invalid_count += 1

            for b in pdf_blobs:
                gcs_uri = f"gs://{bucket_name}/{b.name}"
                meta = b.metadata or {}
                if not force_reprocess and meta.get("ingestion_status") == "COMPLETED":
                    completed_files.append(gcs_uri)
                else:
                    pending_files.append(gcs_uri)
        else:
            local_dir = Path(prefix_or_dir)
            if not local_dir.exists():
                raise FileNotFoundError(f"Local directory not found: {prefix_or_dir}")

            all_pdfs: list[Path] = []
            if local_dir.is_file():
                if local_dir.name.lower().endswith(".pdf"):
                    all_pdfs.append(local_dir)
                else:
                    invalid_count += 1
            else:
                for p in local_dir.rglob("*"):
                    if p.is_file():
                        if p.name.lower().endswith(".pdf"):
                            all_pdfs.append(p)
                        else:
                            invalid_count += 1

            # Deduplicate by path
            seen_paths: set[str] = set()
            distinct_pdfs: list[Path] = []
            for p in all_pdfs:
                abs_str = str(p.resolve())
                if abs_str not in seen_paths:
                    seen_paths.add(abs_str)
                    distinct_pdfs.append(p)

            # Check existing items in storage for local runs
            for p in distinct_pdfs:
                path_str = str(p)
                completed = False
                if not force_reprocess:
                    try:
                        doc_id = compute_document_id(p.read_bytes())
                        bundle = storage_service.get_document_bundle(doc_id)
                        if bundle and bundle.document.ingestion_status in (
                            IngestionStatus.VERIFIED_COMPLETE,
                            IngestionStatus.NEEDS_HITL_REVIEW,
                        ):
                            completed = True
                    except Exception:
                        completed = False

                if completed:
                    completed_files.append(path_str)
                else:
                    pending_files.append(path_str)

        total_files = len(pending_files) + len(completed_files)
        valid_pdfs = total_files
        already_ingested = len(completed_files)

        span.set_attribute("batch.total_files", total_files)
        span.set_attribute("batch.already_ingested", already_ingested)
        span.set_attribute("batch.pending_count", len(pending_files))

        emit_stage_log(
            STAGE_SCAN,
            f"Inbox scanned: {total_files} discovered ({already_ingested} already completed, {len(pending_files)} pending)",
            prefix=prefix_or_dir,
            total_files=total_files,
            valid_pdfs=valid_pdfs,
            already_ingested=already_ingested,
        )

        return BatchScanResponse(
            gcs_prefix=prefix_or_dir,
            total_files=total_files,
            valid_pdfs=valid_pdfs,
            already_ingested=already_ingested,
            pending_files=pending_files,
            completed_files=completed_files,
            invalid_extension=invalid_count,
        )


def process_single_batch_item(
    item: BatchJobItemRow,
    config: PipelineConfig,
    storage_service: ContractStorageService,
    parser: HierarchicalContractParser | None = None,
    default_project_id: str | None = None,
) -> BatchJobItemRow:
    """Process a single contract document inside the sliding-window worker pool.

    Implements:
    - Pass 1 discovery (structural and location metadata).
    - Location-driven consolidation & first-inserted cross-county anchor matching.
    - Clean-slate purge on retries.
    - Concurrent Pass 2 (Body) & Pass 3 (Exhibits).
    - Lakehouse persistence & Option 4a GCS metadata tagging.
    """
    t0 = time.time()
    active_parser = parser or HierarchicalContractParser(config)

    # Transition item to PROCESSING
    item = item.model_copy(
        update={
            "status": BatchItemStatus.PROCESSING,
            "started_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
        }
    )
    storage_service.save_batch_job_item(item)

    with tracer.start_as_current_span("batch.process_document") as span:
        span.set_attribute("batch.item_id", item.item_id)
        span.set_attribute("batch.job_id", item.job_id)
        span.set_attribute("batch.filename", item.filename)
        span.set_attribute("batch.gcs_uri", item.gcs_uri)

        try:
            # 1. Download or read PDF bytes
            if item.gcs_uri.startswith("gs://"):
                pdf_bytes, filename = storage_service.download_gcs_uri(item.gcs_uri)
            else:
                local_path = Path(item.gcs_uri)
                if not local_path.exists():
                    raise FileNotFoundError(f"Source PDF not found at {item.gcs_uri}")
                pdf_bytes = local_path.read_bytes()
                filename = local_path.name

            # 2. Compute canonical document_id
            doc_id = compute_document_id(pdf_bytes)
            span.set_attribute("contract.document_id", doc_id)

            # 3. Clean-slate purge of any partial records before extraction
            storage_service.purge_partial_document(doc_id)

            # 3b. Archive PDF to canonical raw/<document_id>/<filename> in storage
            doc_id, canonical_raw_gcs_uri = storage_service.archive_raw_pdf(
                pdf_bytes=pdf_bytes, filename=filename, document_id=doc_id
            )

            # 4. Pass 1: Structural & Geolocation Discovery
            emit_stage_log(
                STAGE_PASS_START,
                f"Pass 1 discovery started for {filename}",
                job_id=item.job_id,
                item_id=item.item_id,
                filename=filename,
            )
            with tracer.start_as_current_span("pass_1_structure_discovery") as p1_span:
                p1_span.set_attribute("pass.name", "pass_1_structure_discovery")
                p1_span.set_attribute("contract.document_id", doc_id)
                struct_index: ContractStructureIndex = active_parser.discover_structure(
                    gcs_pdf_uri=canonical_raw_gcs_uri,
                    pdf_bytes=pdf_bytes,
                )

            state_code = struct_index.state_province
            county_name = struct_index.county
            energy_tech = struct_index.energy_technology

            # 5. Geolocation Resolution Gate
            with tracer.start_as_current_span("geo_consolidation") as geo_span:
                geo_span.set_attribute("geo.state", state_code or "")
                geo_span.set_attribute("geo.county", county_name or "")
                geo_span.set_attribute("geo.tech", energy_tech.value)

                if default_project_id:
                    target_project = storage_service.get_project(default_project_id)
                    resolution_type = "EXPLICIT_OVERRIDE"
                else:
                    matched = storage_service.find_project_by_geo_or_name(
                        state=state_code,
                        county=county_name,
                        energy_technology=energy_tech,
                        grantee_name=struct_index.grantee_entity_name,
                        project_name=None,
                    )
                    if matched:
                        target_project = matched
                        resolution_type = "LINKED_EXISTING"
                        geo_span.add_event(
                            "cross_county_anchor_bound",
                            {
                                "project_id": target_project.project_id,
                                "created_at": target_project.created_at,
                            },
                        )
                    else:
                        target_project = storage_service.auto_create_project_for_location(
                            state=state_code,
                            county=county_name,
                            energy_technology=energy_tech,
                            grantee_name=struct_index.grantee_entity_name,
                        )
                        resolution_type = "AUTO_PROVISIONED"

                geo_span.set_attribute("geo.resolution", resolution_type)
                geo_span.set_attribute("geo.project_id", target_project.project_id)
                geo_span.set_attribute("geo.erp_code", target_project.erp_project_code)

                emit_stage_log(
                    STAGE_GEO_RESOLVE,
                    f"Resolved project '{target_project.project_id}' ({resolution_type}) for {filename} in {county_name or 'Regional'}, {state_code or 'US'}",
                    job_id=item.job_id,
                    item_id=item.item_id,
                    project_id=target_project.project_id,
                    resolution=resolution_type,
                    state=state_code,
                    county=county_name,
                    tech=energy_tech.value,
                )

            # 6. Resolve Landowner counterparty
            grantor_name = (
                struct_index.grantor_landowner_name
                or Path(filename).stem.replace("_", " ")
            )
            resolved_landowner_id = _make_landowner_id(target_project.project_id, grantor_name)

            # 7. Concurrent Pass 2 (Body) & Pass 3 (Exhibits)
            emit_stage_log(
                STAGE_PASS_START,
                f"Pass 2 & 3 extraction started for {filename} (project {target_project.project_id})",
                job_id=item.job_id,
                item_id=item.item_id,
                project_id=target_project.project_id,
            )
            extraction = active_parser.extract(
                document_id=doc_id,
                gcs_pdf_uri=canonical_raw_gcs_uri,
                pdf_bytes=pdf_bytes,
                project_id=target_project.project_id,
                landowner_id=resolved_landowner_id,
                structure_index=struct_index,
            )

            # 8. Lakehouse Persistence
            flagged_count = sum(
                1
                for c in extraction.clauses
                if c.hitl_status
                in (HITLStatus.FLAGGED_FOR_REVIEW, HITLStatus.PLACEHOLDER_FOR_REVIEW)
            )
            final_status = (
                IngestionStatus.NEEDS_HITL_REVIEW
                if flagged_count > 0
                else IngestionStatus.VERIFIED_COMPLETE
            )

            raw_pages = getattr(extraction, "page_count", 1)
            try:
                final_page_count = max(1, int(raw_pages))
            except (TypeError, ValueError):
                final_page_count = 1

            completed_doc = DocumentRegistryRow(
                document_id=doc_id,
                filename=Path(filename).name,
                gcs_pdf_uri=canonical_raw_gcs_uri,
                gcs_export_prefix=f"gs://{config.gcs_bucket_name}/exports/{doc_id}/",
                page_count=final_page_count,
                contracting_parties_json=extraction.contracting_parties_json or "[]",
                effective_date=extraction.effective_date,
                flagged_node_count=flagged_count,
                special_conditions_count=len(extraction.special_conditions),
                ingestion_status=final_status,
                error_message=None,
                ingested_at=utc_now_iso(),
                updated_at=utc_now_iso(),
                project_id=target_project.project_id,
                landowner_id=resolved_landowner_id,
                energy_technology=target_project.energy_technology,
                grantor_landowner_name=grantor_name,
                grantee_entity_name=struct_index.grantee_entity_name,
                state=state_code,
            )

            stamped_scs = [
                sc.model_copy(
                    update={
                        "project_id": target_project.project_id,
                        "landowner_id": resolved_landowner_id,
                    }
                )
                for sc in extraction.special_conditions
            ]

            bundle = ParsedContractBundle(
                document=completed_doc,
                clauses=extraction.clauses,
                defined_terms=extraction.defined_terms,
                exhibits_catalog=extraction.exhibits_catalog,
                special_conditions=stamped_scs,
            )

            with tracer.start_as_current_span("lakehouse.persist") as persist_span:
                storage_service.persist_bundle(bundle)
                storage_service.bind_document_to_portfolio(
                    bundle,
                    project_id=target_project.project_id,
                    landowner_id=resolved_landowner_id,
                )
                persist_span.set_attribute("lakehouse.clause_count", len(bundle.clauses))
                persist_span.set_attribute(
                    "lakehouse.special_conditions_count", len(bundle.special_conditions)
                )

            # 9. Move processed PDF from inbox to archive folder
            archived_uri = item.gcs_uri
            if item.gcs_uri.startswith("gs://"):
                try:
                    bucket_name, src_blob_name = storage_service._split_gcs_uri(item.gcs_uri)
                    bucket = storage_service.gcs_client.bucket(bucket_name)
                    src_blob = bucket.get_blob(src_blob_name)
                    if src_blob:
                        safe_filename = Path(src_blob_name).name
                        dest_blob_name = f"archive/{safe_filename}"
                        dest_blob = bucket.copy_blob(src_blob, bucket, dest_blob_name)
                        dest_blob.metadata = {
                            "ingestion_status": "COMPLETED",
                            "document_id": doc_id,
                            "job_id": item.job_id,
                            "completed_at": utc_now_iso(),
                            "original_inbox_uri": item.gcs_uri,
                            "raw_uri": canonical_raw_gcs_uri,
                        }
                        dest_blob.patch()
                        src_blob.delete()
                        archived_uri = f"gs://{bucket_name}/{dest_blob_name}"
                        logger.info("Archived source PDF from %s to %s", item.gcs_uri, archived_uri)
                except Exception as archive_exc:
                    logger.warning("Failed moving source PDF to archive folder for %s: %s", item.gcs_uri, archive_exc)
            else:
                try:
                    src_path = Path(item.gcs_uri)
                    if src_path.exists():
                        archive_dir = src_path.parent / "archive"
                        archive_dir.mkdir(parents=True, exist_ok=True)
                        dest_path = archive_dir / src_path.name
                        src_path.replace(dest_path)
                        archived_uri = str(dest_path)
                        logger.info("Archived local source PDF from %s to %s", src_path, dest_path)
                except Exception as archive_exc:
                    logger.warning("Failed moving local source PDF to archive directory for %s: %s", item.gcs_uri, archive_exc)

            duration = round(time.time() - t0, 2)
            raw_tokens = getattr(extraction, "total_tokens", 0)
            if isinstance(raw_tokens, (int, float)):
                total_tokens = int(raw_tokens)
            elif hasattr(extraction, "token_usage") and isinstance(extraction.token_usage, dict):
                in_tok = extraction.token_usage.get("input_tokens") or extraction.token_usage.get("prompt_tokens") or 0
                out_tok = extraction.token_usage.get("output_tokens") or extraction.token_usage.get("candidates_tokens") or 0
                total_tokens = (int(in_tok) if isinstance(in_tok, (int, float)) else 0) + (int(out_tok) if isinstance(out_tok, (int, float)) else 0)
            else:
                total_tokens = 0

            # 10. Update item record to COMPLETED
            item = item.model_copy(
                update={
                    "document_id": doc_id,
                    "project_id": target_project.project_id,
                    "landowner_id": resolved_landowner_id,
                    "status": BatchItemStatus.COMPLETED,
                    "gcs_uri": archived_uri,
                    "state_province": state_code,
                    "county": county_name,
                    "energy_technology": target_project.energy_technology.value,
                    "clause_count": len(bundle.clauses),
                    "special_conditions_count": len(bundle.special_conditions),
                    "duration_seconds": duration,
                    "token_usage_total": total_tokens,
                    "error_message": None,
                    "completed_at": utc_now_iso(),
                    "updated_at": utc_now_iso(),
                }
            )
            storage_service.save_batch_job_item(item)

            emit_stage_log(
                STAGE_PERSIST,
                f"Successfully ingested {filename} ({len(bundle.clauses)} clauses, {len(bundle.special_conditions)} special conditions in {duration}s)",
                job_id=item.job_id,
                item_id=item.item_id,
                document_id=doc_id,
                project_id=target_project.project_id,
                clauses=len(bundle.clauses),
                duration=duration,
            )
            flush_telemetry(timeout_millis=2000)
            return item

        except Exception as exc:
            duration = round(time.time() - t0, 2)
            err_msg = f"{type(exc).__name__}: {str(exc)}"
            tb_str = traceback.format_exc()
            logger.error("Batch item %s failed: %s\n%s", item.item_id, err_msg, tb_str)

            # Purge any partially persisted tables for this failed doc
            if "doc_id" in locals():
                try:
                    storage_service.purge_partial_document(doc_id)
                except Exception:
                    pass

            item = item.model_copy(
                update={
                    "status": BatchItemStatus.FAILED,
                    "duration_seconds": duration,
                    "error_message": err_msg,
                    "completed_at": utc_now_iso(),
                    "updated_at": utc_now_iso(),
                }
            )
            storage_service.save_batch_job_item(item)

            emit_stage_log(
                STAGE_ITEM_FAIL,
                f"Failed ingesting {item.filename}: {err_msg}",
                job_id=item.job_id,
                item_id=item.item_id,
                filename=item.filename,
                error=err_msg,
            )
            flush_telemetry(timeout_millis=2000)
            return item


async def run_sliding_window_worker_pool(
    job_id: str,
    items: list[BatchJobItemRow],
    config: PipelineConfig,
    storage_service: ContractStorageService,
    concurrency: int = 5,
    default_project_id: str | None = None,
    parser: HierarchicalContractParser | None = None,
) -> BatchJobRow:
    """Execute Option B continuous sliding-window worker pool using an asyncio Semaphore."""
    concurrency_limit = max(1, min(concurrency, 10))
    sem = asyncio.Semaphore(concurrency_limit)
    active_parser = parser or HierarchicalContractParser(config)

    job = storage_service.get_batch_job(job_id)
    job = job.model_copy(
        update={
            "status": BatchJobStatus.RUNNING,
            "updated_at": utc_now_iso(),
        }
    )
    storage_service.save_batch_job(job)

    # Mutex for thread-safe in-memory job counter updates
    counter_lock = asyncio.Lock()

    async def worker_task(item: BatchJobItemRow) -> None:
        nonlocal job
        async with sem:
            try:
                res_item = await asyncio.to_thread(
                    process_single_batch_item,
                    item=item,
                    config=config,
                    storage_service=storage_service,
                    parser=active_parser,
                    default_project_id=default_project_id,
                )
            except Exception as task_exc:
                logger.error("Unexpected worker exception processing %s: %s", item.filename, task_exc)
                res_item = item.model_copy(
                    update={
                        "status": BatchItemStatus.FAILED,
                        "error_message": str(task_exc),
                        "completed_at": utc_now_iso(),
                        "updated_at": utc_now_iso(),
                    }
                )
                try:
                    storage_service.save_batch_job_item(res_item)
                except Exception:
                    pass
            async with counter_lock:
                success_inc = 1 if res_item.status == BatchItemStatus.COMPLETED else 0
                failure_inc = 1 if res_item.status == BatchItemStatus.FAILED else 0
                raw_tokens = res_item.token_usage_total
                tokens_inc = int(raw_tokens) if isinstance(raw_tokens, (int, float)) else 0

                try:
                    current_job = storage_service.get_batch_job(job_id)
                except Exception:
                    current_job = job

                job = current_job.model_copy(
                    update={
                        "processed_files": int(current_job.processed_files) + 1,
                        "success_count": int(current_job.success_count) + success_inc,
                        "failure_count": int(current_job.failure_count) + failure_inc,
                        "total_tokens_consumed": int(current_job.total_tokens_consumed) + tokens_inc,
                        "updated_at": utc_now_iso(),
                    }
                )
                storage_service.save_batch_job(job)

    tasks = [asyncio.create_task(worker_task(it)) for it in items]
    await asyncio.gather(*tasks, return_exceptions=True)

    # Finalize job status
    final_job = storage_service.get_batch_job(job_id)
    final_status = (
        BatchJobStatus.COMPLETED
        if final_job.failure_count == 0
        else (BatchJobStatus.COMPLETED if final_job.success_count > 0 else BatchJobStatus.FAILED)
    )
    final_job = final_job.model_copy(
        update={
            "status": final_status,
            "completed_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
        }
    )
    storage_service.save_batch_job(final_job)

    emit_stage_log(
        STAGE_BATCH_SUMMARY,
        f"Batch job {job_id} finalized with status {final_status.value} (Success: {final_job.success_count}, Failed: {final_job.failure_count})",
        job_id=job_id,
        status=final_status.value,
        total=final_job.total_files,
        success=final_job.success_count,
        failures=final_job.failure_count,
    )
    return final_job


def initialize_batch_job(
    gcs_prefix: str,
    concurrency: int = 5,
    storage_service: ContractStorageService | None = None,
    config: PipelineConfig | None = None,
    force_reprocess: bool = False,
    triggered_by: str | None = None,
) -> tuple[BatchJobRow, list[BatchJobItemRow]]:
    """Scan inbox, create persistent BatchJobRow and queued BatchJobItemRows."""
    cfg = config or PipelineConfig()
    store = storage_service or ContractStorageService(cfg)

    now_iso = utc_now_iso()
    ts_slug = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    job_id = f"batch_{ts_slug}"

    scan_res = scan_batch_inbox(
        prefix_or_dir=gcs_prefix,
        storage_service=store,
        force_reprocess=force_reprocess,
    )

    items_to_queue = scan_res.pending_files
    total_files = len(items_to_queue)

    job = BatchJobRow(
        job_id=job_id,
        gcs_prefix=gcs_prefix,
        total_files=total_files,
        processed_files=0,
        success_count=0,
        failure_count=0,
        status=BatchJobStatus.PENDING,
        started_at=now_iso,
        triggered_by=triggered_by,
        updated_at=now_iso,
    )
    store.save_batch_job(job)

    item_rows: list[BatchJobItemRow] = []
    for idx, uri in enumerate(items_to_queue):
        item_id = f"itm_{job_id}_{idx + 1:04d}"
        filename = uri.split("/")[-1]
        it = BatchJobItemRow(
            item_id=item_id,
            job_id=job_id,
            gcs_uri=uri,
            filename=filename,
            status=BatchItemStatus.QUEUED,
            updated_at=now_iso,
        )
        store.save_batch_job_item(it)
        item_rows.append(it)

    return job, item_rows


async def run_local_batch_pipeline(
    job_id: str,
    config: PipelineConfig | None = None,
    storage_service: ContractStorageService | None = None,
    concurrency: int = 5,
    default_project_id: str | None = None,
    parser: HierarchicalContractParser | None = None,
) -> BatchJobRow:
    """Entrypoint for executing a registered batch job in-process."""
    cfg = config or PipelineConfig()
    store = storage_service or ContractStorageService(cfg)
    items = store.get_batch_job_items(job_id)
    queued_items = [it for it in items if it.status in (BatchItemStatus.QUEUED, BatchItemStatus.PROCESSING)]
    return await run_sliding_window_worker_pool(
        job_id=job_id,
        items=queued_items,
        config=cfg,
        storage_service=store,
        concurrency=concurrency,
        default_project_id=default_project_id,
        parser=parser,
    )


def retry_failed_batch_items(
    job_id: str,
    storage_service: ContractStorageService,
    config: PipelineConfig,
    concurrency: int = 5,
) -> tuple[int, asyncio.Task[BatchJobRow]]:
    """Clean-slate purge and re-queue failed documents for a batch job."""
    job = storage_service.get_batch_job(job_id)
    items = storage_service.get_batch_job_items(job_id)
    failed_items = [it for it in items if it.status == BatchItemStatus.FAILED]

    if not failed_items:
        raise ValueError(f"Batch job {job_id} has no failed items to retry.")

    re_queued: list[BatchJobItemRow] = []
    now_iso = utc_now_iso()

    for item in failed_items:
        if item.document_id:
            storage_service.purge_partial_document(item.document_id)

        updated_item = item.model_copy(
            update={
                "status": BatchItemStatus.QUEUED,
                "error_message": None,
                "started_at": None,
                "completed_at": None,
                "updated_at": now_iso,
            }
        )
        storage_service.save_batch_job_item(updated_item)
        re_queued.append(updated_item)

    job = job.model_copy(
        update={
            "status": BatchJobStatus.RUNNING,
            "processed_files": max(0, job.processed_files - len(failed_items)),
            "failure_count": max(0, job.failure_count - len(failed_items)),
            "updated_at": now_iso,
        }
    )
    storage_service.save_batch_job(job)

    task = asyncio.create_task(
        run_sliding_window_worker_pool(
            job_id=job_id,
            items=re_queued,
            config=config,
            storage_service=storage_service,
            concurrency=concurrency,
        )
    )
    return len(re_queued), task


def cli_main(argv: list[str] | None = None) -> int:
    """Command-line entrypoint for Cloud Run Jobs and manual CLI executions."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(
        prog="contract-parser-batch",
        description="Option B Sliding-Window Batch Contract Ingestion & Discovery Engine",
    )
    parser.add_argument("--job-id", default=None, help="Existing batch job_id to execute")
    parser.add_argument("--gcs-prefix", default=None, help="GCS prefix (gs://...) or local folder path")
    parser.add_argument("--concurrency", type=int, default=5, help="Number of concurrent workers (default 5)")
    parser.add_argument("--project-id", default=None, help="Optional explicit Project ID to force binding")
    parser.add_argument("--force-reprocess", action="store_true", help="Force re-processing of completed files")

    args = parser.parse_args(argv)
    cfg = PipelineConfig()
    store = ContractStorageService(cfg)

    job_id = args.job_id
    if not job_id:
        if not args.gcs_prefix:
            logger.error("Either --job-id or --gcs-prefix must be specified.")
            return 1
        job, items = initialize_batch_job(
            gcs_prefix=args.gcs_prefix,
            concurrency=args.concurrency,
            storage_service=store,
            config=cfg,
            force_reprocess=args.force_reprocess,
        )
        job_id = job.job_id
        logger.info("Initialized new batch job: %s with %d files", job_id, len(items))

    logger.info("Starting batch execution for job: %s (concurrency=%d)", job_id, args.concurrency)
    final_job = asyncio.run(
        run_local_batch_pipeline(
            job_id=job_id,
            config=cfg,
            storage_service=store,
            concurrency=args.concurrency,
            default_project_id=args.project_id,
        )
    )
    logger.info(
        "Batch job %s finished: %d succeeded, %d failed out of %d total",
        final_job.job_id,
        final_job.success_count,
        final_job.failure_count,
        final_job.total_files,
    )
    return 0 if final_job.failure_count == 0 else (0 if final_job.success_count > 0 else 1)


if __name__ == "__main__":
    sys.exit(cli_main())
