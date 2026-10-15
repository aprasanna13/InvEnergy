"""FastAPI web application and CLI entrypoint for the Gemini-First Contract Parser."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel

from contract_parser.auth import (
    ALLOWED_DOMAINS,
    UserContext,
    UserRole,
    dispatch_email_sign_in_link,
    get_client_ip,
    make_auth_dependency,
    require_admin,
)

from contract_parser.batch import (
    initialize_batch_job,
    retry_failed_batch_items,
    run_local_batch_pipeline,
    scan_batch_inbox,
)
from contract_parser.config import PipelineConfig
from contract_parser.gemini_parser import (
    ContractExtractorProtocol,
    GeminiContractParser,
    make_landowner_id as _make_landowner_id,
)
from contract_parser.schemas import (
    AgentChatRequest,
    AgentChatResponse,
    BatchJobDetailResponse,
    BatchJobItemRow,
    BatchJobRow,
    BatchJobStatus,
    BatchLaunchRequest,
    BatchLaunchResponse,
    BatchRetryResponse,
    BatchScanRequest,
    BatchScanResponse,
    ClauseReviewRequest,
    CreateDNDSignoffRequest,
    CreateProjectRequest,
    DocumentRegistryRow,
    EnergyTechnology,
    FormType,
    HITLStatus,
    IngestionStatus,
    ParsedContractBundle,
    ProjectRow,
    TemplateClauseRow,
    TemplateExtractionBundle,
    TemplateRegistryRow,
    ContractVarianceBundle,
    parse_data_agent_events,
    utc_now_iso,
)
from contract_parser.storage import ContractStorageService, compute_document_id
from contract_parser.variance_engine import (
    calculate_template_affinity,
    rank_candidate_templates,
    run_contract_variance_comparison,
)
from contract_parser.telemetry import (

    flush_telemetry,
    get_tracer,
    setup_structured_logging,
    setup_telemetry,
    shutdown_telemetry,
)

logger = logging.getLogger(__name__)
tracer = get_tracer("contract_parser.app")


class RequestLinkPayload(BaseModel):
    """Payload for POST /api/v1/auth/request-link."""

    email: str
    continue_url: str | None = None


class BatchIngestRequest(BaseModel):
    """Payload for POST /api/v1/documents/ingest-gcs."""

    gcs_uri_or_prefix: str = "incoming/"
    project_id: str = "prj_cedar_lantern_wind"
    landowner_id: str | None = None


def ingest_pdf_bytes(
    *,
    pdf_bytes: bytes,
    filename: str,
    project_id: str = "prj_cedar_lantern_wind",
    landowner_id: str | None = None,
    config: PipelineConfig | None = None,
    storage_service: ContractStorageService | None = None,
    extractor: ContractExtractorProtocol | None = None,
) -> ParsedContractBundle:
    """Execute the end-to-end ingestion, Gemini extraction, portfolio binding, BigQuery append, and CSV export pipeline."""
    cfg = config or PipelineConfig()
    store = storage_service or ContractStorageService(cfg)
    parser = extractor or GeminiContractParser(cfg)

    doc_id = compute_document_id(pdf_bytes)
    with tracer.start_as_current_span("pipeline.ingest_pdf") as span:
        span.set_attribute("contract.document_id", doc_id)
        span.set_attribute("contract.project_id", project_id)
        span.set_attribute("contract.filename", filename)

        store.ensure_portfolio_seed()
        try:
            project = store.get_project(project_id)
        except KeyError:
            project = store.get_project("prj_cedar_lantern_wind")

        doc_id, gcs_pdf_uri = store.archive_raw_pdf(
            pdf_bytes=pdf_bytes, filename=filename, document_id=doc_id
        )
        gcs_export_prefix = f"gs://{cfg.gcs_bucket_name}/exports/{doc_id}/"
        ingested_ts = utc_now_iso()

        # Register initial PROCESSING version
        initial_doc = DocumentRegistryRow(
            document_id=doc_id,
            filename=Path(filename).name,
            gcs_pdf_uri=gcs_pdf_uri,
            gcs_export_prefix=gcs_export_prefix,
            page_count=1,
            contracting_parties_json="[]",
            effective_date=None,
            flagged_node_count=0,
            special_conditions_count=0,
            ingestion_status=IngestionStatus.PROCESSING,
            error_message=None,
            ingested_at=ingested_ts,
            updated_at=ingested_ts,
            project_id=project.project_id,
            landowner_id=landowner_id or "lnd_unassigned",
            energy_technology=project.energy_technology,
        )
        store.append_document_row(initial_doc)

        try:
            try:
                extraction = parser.extract(
                    document_id=doc_id,
                    gcs_pdf_uri=gcs_pdf_uri,
                    pdf_bytes=pdf_bytes,
                    project_id=project.project_id,
                    landowner_id=landowner_id,
                )
            except TypeError:
                extraction = parser.extract(
                    document_id=doc_id,
                    gcs_pdf_uri=gcs_pdf_uri,
                    pdf_bytes=pdf_bytes,
                )

            grantor_name = (
                extraction.grantor_landowner_name
                or Path(filename).stem.replace("_", " ")
            )
            grantee_name = extraction.grantee_entity_name
            resolved_landowner_id = (
                landowner_id
                if (landowner_id and landowner_id != "lnd_unassigned")
                else _make_landowner_id(project.project_id, grantor_name)
            )
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
            completed_doc = DocumentRegistryRow(
                document_id=doc_id,
                filename=Path(filename).name,
                gcs_pdf_uri=gcs_pdf_uri,
                gcs_export_prefix=gcs_export_prefix,
                page_count=max(1, extraction.page_count),
                contracting_parties_json=extraction.contracting_parties_json or "[]",
                effective_date=extraction.effective_date,
                flagged_node_count=flagged_count,
                special_conditions_count=len(extraction.special_conditions),
                ingestion_status=final_status,
                error_message=None,
                ingested_at=ingested_ts,
                updated_at=utc_now_iso(),
                project_id=project.project_id,
                landowner_id=resolved_landowner_id,
                energy_technology=project.energy_technology,
                grantor_landowner_name=grantor_name,
                grantee_entity_name=grantee_name,
            )
            stamped_scs = [
                sc.model_copy(
                    update={
                        "project_id": project.project_id,
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
            store.persist_bundle(bundle)
            bundle, _, _ = store.bind_document_to_portfolio(
                bundle,
                project_id=project.project_id,
                landowner_id=resolved_landowner_id,
            )
            flush_telemetry(timeout_millis=2000)
            return bundle
        except Exception as exc:
            failed_doc = initial_doc.model_copy(
                update={
                    "ingestion_status": IngestionStatus.FAILED,
                    "error_message": str(exc),
                    "updated_at": utc_now_iso(),
                }
            )
            store.append_document_row(failed_doc)
            flush_telemetry(timeout_millis=2000)
            raise


def ingest_contract(
    pdf_path_or_uri: str,
    project_id: str = "prj_cedar_lantern_wind",
    landowner_id: str | None = None,
    config: PipelineConfig | None = None,
    storage_service: ContractStorageService | None = None,
    extractor: ContractExtractorProtocol | None = None,
) -> ParsedContractBundle:
    """Ingest a contract from a local PDF path or gs:// URI."""
    cfg = config or PipelineConfig()
    store = storage_service or ContractStorageService(cfg)
    if pdf_path_or_uri.startswith("gs://"):
        pdf_bytes, filename = store.download_gcs_uri(pdf_path_or_uri)
    else:
        path = Path(pdf_path_or_uri)
        if not path.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path_or_uri}")
        pdf_bytes = path.read_bytes()
        filename = path.name
    return ingest_pdf_bytes(
        pdf_bytes=pdf_bytes,
        filename=filename,
        project_id=project_id,
        landowner_id=landowner_id,
        config=cfg,
        storage_service=store,
        extractor=extractor,
    )


def _build_local_doc_filename_lookup(
    storage: ContractStorageService | None,
) -> dict[str, str]:
    """Build `{document_id: filename}` from local SQLite first to avoid an extra BigQuery round-trip per chat turn."""
    if storage is None:
        return {}
    if hasattr(storage, "get_document_filename_lookup"):
        return storage.get_document_filename_lookup()
    try:
        return {d.document_id: d.filename for d in storage.list_documents()}
    except Exception as exc:
        logger.debug("list_documents filename lookup fallback: %s", exc)
        return {}


def query_bigquery_data_agent(
    question: str,
    config: PipelineConfig | None = None,
    storage: ContractStorageService | None = None,
) -> AgentChatResponse:
    """Execute a stateless single-turn query against the registered BigQuery Conversational Analytics Data Agent."""
    cleaned = (question or "").strip()
    if not cleaned:
        raise ValueError("Question must not be empty.")

    cfg = config or PipelineConfig()
    proj, loc, data_agent_resource = cfg.resolve_data_agent_resource()

    import google.auth
    import google.auth.transport.requests
    import requests as http_requests

    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    creds.refresh(google.auth.transport.requests.Request())

    url = f"https://geminidataanalytics.googleapis.com/v1beta/projects/{proj}/locations/{loc}:chat"
    headers = {
        "Authorization": f"Bearer {creds.token}",
        "Content-Type": "application/json",
        "x-goog-user-project": cfg.google_cloud_project,
    }
    payload = {
        "messages": [{"userMessage": {"text": cleaned}}],
        "dataAgentContext": {"dataAgent": data_agent_resource},
    }

    resp = http_requests.post(url, headers=headers, json=payload, timeout=90)
    if resp.status_code >= 400:
        raise RuntimeError(
            f"BigQuery Data Agent HTTP {resp.status_code}: {resp.text[:500]}"
        )

    raw_json = resp.json()
    events: list[dict[str, object]] = (
        raw_json
        if isinstance(raw_json, list)
        else ([raw_json] if isinstance(raw_json, dict) else [])
    )
    doc_lookup = _build_local_doc_filename_lookup(storage)
    return parse_data_agent_events(
        events,
        question=cleaned,
        agent_urn=cfg.bq_data_agent_urn,
        data_agent_resource=data_agent_resource,
        doc_filename_lookup=doc_lookup,
    )


def create_app(
    config: PipelineConfig | None = None,
    storage_service: ContractStorageService | None = None,
    extractor: ContractExtractorProtocol | None = None,
    storage: ContractStorageService | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application."""
    cfg = config or PipelineConfig()
    store = storage or storage_service or ContractStorageService(cfg)
    parser = extractor or GeminiContractParser(cfg)

    # CR-8: Structured logging & OpenTelemetry initialization
    if cfg.log_format == "json":
        setup_structured_logging(
            gcp_project_id=cfg.google_cloud_project,
            log_level=cfg.log_level,
            service_name=cfg.telemetry_service_name,
            service_version=cfg.telemetry_service_version,
        )
    setup_telemetry(
        project_id=cfg.google_cloud_project,
        service_name=cfg.telemetry_service_name,
        service_version=cfg.telemetry_service_version,
        sample_rate=cfg.telemetry_sample_rate,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        flush_telemetry(timeout_millis=2000)
        shutdown_telemetry()

    fastapi_app = FastAPI(
        title="Hierarchical Contract Parsing & Portfolio Obligation Intelligence",
        version="0.9.0",
        description="Gemini-First Multimodal Contract Hierarchy Parser, 8-Table Portfolio & Field Crew DND Store, BigQuery Data Agent Chat, and pdf.js HITL Review UI",
        lifespan=lifespan,
    )
    fastapi_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(fastapi_app)
    except Exception as exc:
        logger.warning("FastAPI OpenTelemetry instrumentation warning: %s", exc)

    fastapi_app.state.config = cfg
    fastapi_app.state.storage = store
    fastapi_app.state.extractor = parser

    auth_dep = make_auth_dependency(lambda: fastapi_app.state.config)

    static_dir = Path(__file__).parent / "static"

    @fastapi_app.get("/", response_class=HTMLResponse)
    async def serve_index() -> HTMLResponse:
        index_file = static_dir / "index.html"
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"))

    @fastapi_app.get("/favicon.ico")
    async def serve_favicon() -> FileResponse:
        fav_path = static_dir / "favicon.ico"
        if fav_path.exists():
            return FileResponse(fav_path, media_type="image/x-icon")
        return FileResponse(static_dir / "favicon-32x32.png", media_type="image/png")

    @fastapi_app.get("/favicon-32x32.png")
    async def serve_favicon_32() -> FileResponse:
        return FileResponse(static_dir / "favicon-32x32.png", media_type="image/png")

    @fastapi_app.get("/apple-touch-icon.png")
    async def serve_apple_touch_icon() -> FileResponse:
        return FileResponse(static_dir / "apple-touch-icon.png", media_type="image/png")

    @fastapi_app.get("/api/v1/auth/config")
    async def get_auth_config() -> dict[str, object]:
        active_cfg: PipelineConfig = fastapi_app.state.config
        return {
            "auth_enabled": active_cfg.auth_enabled,
            "firebase_api_key": active_cfg.firebase_api_key,
            "firebase_auth_domain": active_cfg.firebase_auth_domain,
            "project_id": active_cfg.google_cloud_project,
            "allowed_domains": sorted(list(ALLOWED_DOMAINS)),
        }

    @fastapi_app.post("/api/v1/auth/request-link")
    async def request_sign_in_link(
        req: RequestLinkPayload,
        request: Request,
    ) -> dict[str, str]:
        active_cfg: PipelineConfig = fastapi_app.state.config
        client_ip = get_client_ip(request)
        default_continue_url = str(request.base_url)
        continue_url = req.continue_url or default_continue_url
        return await dispatch_email_sign_in_link(
            email=req.email,
            continue_url=continue_url,
            config=active_cfg,
            client_ip=client_ip,
        )

    @fastapi_app.get("/api/v1/auth/me")
    async def get_current_user_profile(
        current_user: UserContext = Depends(auth_dep),
    ) -> dict[str, object]:
        return current_user.model_dump(mode="json")

    @fastapi_app.get("/api/v1/projects")
    async def list_projects_endpoint(
        energy_technology: str | None = None,
        current_user: UserContext = Depends(auth_dep),
    ) -> dict[str, object]:
        active_store: ContractStorageService = fastapi_app.state.storage
        projects = active_store.list_projects(energy_technology=energy_technology)
        return {
            "projects": [p.model_dump(mode="json") for p in projects],
            "count": len(projects),
        }

    @fastapi_app.post("/api/v1/projects")
    async def create_project_endpoint(
        req: CreateProjectRequest,
        current_user: UserContext = Depends(auth_dep),
    ) -> dict[str, object]:
        require_admin(current_user)
        active_store: ContractStorageService = fastapi_app.state.storage
        slug = re.sub(r"[^a-z0-9]+", "_", req.project_name.lower()).strip("_")[:32]
        pid = req.project_id or f"prj_{slug or 'custom'}"
        now_iso = utc_now_iso()
        proj = ProjectRow(
            project_id=pid,
            project_name=req.project_name,
            energy_technology=req.energy_technology,
            erp_project_code=req.erp_project_code,
            state_province=req.state_province,
            county=req.county,
            target_capacity_mw=req.target_capacity_mw,
            landowner_count=0,
            document_count=0,
            special_conditions_count=0,
            flagged_node_count=0,
            updated_at=now_iso,
        )
        active_store.upsert_project(proj)
        dumped = proj.model_dump(mode="json")
        return {**dumped, "project": dumped}

    @fastapi_app.get("/api/v1/projects/{project_id}/landowners")
    async def list_project_landowners_endpoint(
        project_id: str,
        current_user: UserContext = Depends(auth_dep),
    ) -> dict[str, object]:
        active_store: ContractStorageService = fastapi_app.state.storage
        try:
            proj = active_store.get_project(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        landowners = active_store.list_landowners(project_id=project_id)
        return {
            "project": proj.model_dump(mode="json"),
            "landowners": [l.model_dump(mode="json") for l in landowners],
            "count": len(landowners),
        }

    @fastapi_app.get("/api/v1/landowners")
    async def list_landowners_endpoint(
        project_id: str | None = None,
        current_user: UserContext = Depends(auth_dep),
    ) -> dict[str, object]:
        active_store: ContractStorageService = fastapi_app.state.storage
        landowners = active_store.list_landowners(project_id=project_id)
        return {
            "landowners": [l.model_dump(mode="json") for l in landowners],
            "count": len(landowners),
        }

    @fastapi_app.get("/api/v1/portfolio/search")
    async def search_portfolio_endpoint(
        project_id: str | None = None,
        landowner_id: str | None = None,
        energy_technology: str | None = None,
        constraint_category: str | None = None,
        hitl_status: str | None = None,
        q: str | None = None,
        current_user: UserContext = Depends(auth_dep),
    ) -> dict[str, object]:
        active_store: ContractStorageService = fastapi_app.state.storage
        return active_store.search_portfolio(
            project_id=project_id,
            landowner_id=landowner_id,
            energy_technology=energy_technology,
            constraint_category=constraint_category,
            hitl_status=hitl_status,
            q=q,
        )

    @fastapi_app.post("/api/v1/documents/upload")
    @fastapi_app.post("/api/v1/documents:ingest")
    async def upload_document(
        file: UploadFile = File(...),
        project_id: str = Form("prj_cedar_lantern_wind"),
        landowner_id: str | None = Form(None),
        current_user: UserContext = Depends(auth_dep),
    ) -> dict[str, object]:
        require_admin(current_user)
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="Only .pdf files are supported")
        pdf_bytes = await file.read()
        if not pdf_bytes:
            raise HTTPException(status_code=400, detail="Uploaded PDF file is empty")
        bundle = ingest_pdf_bytes(
            pdf_bytes=pdf_bytes,
            filename=file.filename,
            project_id=project_id,
            landowner_id=landowner_id,
            config=fastapi_app.state.config,
            storage_service=fastapi_app.state.storage,
            extractor=fastapi_app.state.extractor,
        )
        payload = bundle.model_dump(mode="json")
        payload["pdf_url"] = f"/api/v1/documents/{bundle.document.document_id}/pdf"
        return payload

    @fastapi_app.post("/api/v1/documents/ingest-gcs")
    async def ingest_from_gcs(
        req: BatchIngestRequest,
        current_user: UserContext = Depends(auth_dep),
    ) -> dict[str, object]:
        require_admin(current_user)
        active_store: ContractStorageService = fastapi_app.state.storage
        target = req.gcs_uri_or_prefix
        if target.startswith("gs://") and target.lower().endswith(".pdf"):
            uris = [target]
        elif Path(target).exists() and target.lower().endswith(".pdf"):
            uris = [target]
        else:
            uris = active_store.list_incoming_gcs_pdfs(prefix=target)

        ingested_docs: list[dict[str, object]] = []
        for uri in uris:
            bundle = ingest_contract(
                pdf_path_or_uri=uri,
                project_id=req.project_id,
                landowner_id=req.landowner_id,
                config=fastapi_app.state.config,
                storage_service=active_store,
                extractor=fastapi_app.state.extractor,
            )
            ingested_docs.append(bundle.document.model_dump(mode="json"))
        return {"ingested_count": len(ingested_docs), "documents": ingested_docs}

    @fastapi_app.get("/api/v1/documents")
    async def list_documents(
        project_id: str | None = None,
        landowner_id: str | None = None,
        energy_technology: str | None = None,
        current_user: UserContext = Depends(auth_dep),
    ) -> dict[str, object]:
        active_store: ContractStorageService = fastapi_app.state.storage
        active_store.ensure_portfolio_seed()
        docs = active_store.list_documents(
            project_id=project_id,
            landowner_id=landowner_id,
            energy_technology=energy_technology,
        )
        return {
            "documents": [d.model_dump(mode="json") for d in docs],
            "count": len(docs),
        }

    @fastapi_app.get("/api/v1/documents/{document_id}")
    async def get_document(
        document_id: str,
        current_user: UserContext = Depends(auth_dep),
    ) -> dict[str, object]:
        active_store: ContractStorageService = fastapi_app.state.storage
        try:
            bundle = active_store.get_bundle(document_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        payload = bundle.model_dump(mode="json")
        payload["pdf_url"] = f"/api/v1/documents/{document_id}/pdf"
        return payload

    @fastapi_app.get("/api/v1/documents/{document_id}/pdf")
    async def stream_document_pdf(
        document_id: str,
        request: Request,
        current_user: UserContext = Depends(auth_dep),
    ) -> Response:
        active_store: ContractStorageService = fastapi_app.state.storage
        try:
            pdf_bytes = active_store.read_pdf_bytes(document_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        total_len = len(pdf_bytes)
        range_header = request.headers.get("range")
        headers = {
            "Accept-Ranges": "bytes",
            "Access-Control-Allow-Origin": "*",
            "Content-Disposition": f'inline; filename="{document_id}.pdf"',
        }
        if range_header and range_header.startswith("bytes="):
            try:
                start_str, end_str = range_header.removeprefix("bytes=").split("-", 1)
                if not start_str and end_str:
                    start = max(0, total_len - int(end_str))
                    end = total_len - 1
                else:
                    start = int(start_str) if start_str else 0
                    end = min(int(end_str) if end_str else total_len - 1, total_len - 1)
                if start > end or start >= total_len:
                    raise ValueError("Unsatisfiable byte range")
                chunk = pdf_bytes[start : end + 1]
                headers["Content-Range"] = f"bytes {start}-{end}/{total_len}"
                headers["Content-Length"] = str(len(chunk))
                return Response(
                    content=chunk,
                    status_code=206,
                    media_type="application/pdf",
                    headers=headers,
                )
            except Exception:
                pass

        headers["Content-Length"] = str(total_len)
        return Response(
            content=pdf_bytes,
            status_code=200,
            media_type="application/pdf",
            headers=headers,
        )

    @fastapi_app.patch("/api/v1/documents/{document_id}/clauses/{node_id}")
    async def review_clause_endpoint(
        document_id: str,
        node_id: str,
        review: ClauseReviewRequest,
        current_user: UserContext = Depends(auth_dep),
    ) -> dict[str, object]:
        require_admin(current_user)
        if not review.reviewed_by:
            review.reviewed_by = current_user.email
        active_store: ContractStorageService = fastapi_app.state.storage
        try:
            updated_clause, updated_doc = active_store.review_clause(
                document_id=document_id, node_id=node_id, review=review
            )
            sqlite_bundle = active_store._get_bundle_from_sqlite(document_id)
            node_conditions = (
                [
                    sc.model_dump(mode="json")
                    for sc in sqlite_bundle.special_conditions
                    if sc.node_id == node_id
                ]
                if sqlite_bundle is not None
                else []
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "clause": updated_clause.model_dump(mode="json"),
            "document": updated_doc.model_dump(mode="json"),
            "special_conditions": node_conditions,
        }

    @fastapi_app.get("/api/v1/documents/{document_id}/dnd-checklist")
    async def get_document_dnd_checklist_endpoint(
        document_id: str,
        construction_trade: str | None = None,
        severity_level: str | None = None,
        current_user: UserContext = Depends(auth_dep),
    ) -> dict[str, object]:
        active_store: ContractStorageService = fastapi_app.state.storage
        try:
            checklist = active_store.get_contract_dnd_checklist(
                document_id=document_id,
                construction_trade=construction_trade,
                severity_level=severity_level,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return checklist.model_dump(mode="json")

    @fastapi_app.post("/api/v1/documents/{document_id}/dnd-checklist:signoff")
    async def record_document_dnd_signoff_endpoint(
        document_id: str,
        req: CreateDNDSignoffRequest,
        current_user: UserContext = Depends(auth_dep),
    ) -> dict[str, object]:
        if not req.foreman_name:
            req.foreman_name = current_user.email
        active_store: ContractStorageService = fastapi_app.state.storage
        try:
            checklist = active_store.record_dnd_checklist_signoff(
                document_id=document_id,
                req=req,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return checklist.model_dump(mode="json")

    @fastapi_app.get("/api/v1/documents/{document_id}/export/{csv_name}")
    async def export_csv_endpoint(
        document_id: str,
        csv_name: str,
        current_user: UserContext = Depends(auth_dep),
    ) -> Response:
        active_store: ContractStorageService = fastapi_app.state.storage
        try:
            csv_bytes = active_store.get_csv_bytes(document_id=document_id, csv_name=csv_name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(
            content=csv_bytes,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{document_id}_{csv_name}"'
            },
        )

    @fastapi_app.get("/api/v1/agent/info")
    async def get_bigquery_agent_info_endpoint(
        current_user: UserContext = Depends(auth_dep),
    ) -> dict[str, str]:
        active_cfg: PipelineConfig = fastapi_app.state.config
        proj, loc, data_agent_resource = active_cfg.resolve_data_agent_resource()
        return {
            "agent_urn": active_cfg.bq_data_agent_urn,
            "project": proj,
            "location": loc,
            "data_agent_resource": data_agent_resource,
        }

    @fastapi_app.post("/api/v1/agent/chat")
    async def chat_with_bigquery_agent_endpoint(
        req: AgentChatRequest,
        current_user: UserContext = Depends(auth_dep),
    ) -> dict[str, object]:
        active_cfg: PipelineConfig = fastapi_app.state.config
        active_store: ContractStorageService = fastapi_app.state.storage
        question = (req.question or "").strip()
        if not question:
            raise HTTPException(
                status_code=400, detail="Question must not be empty."
            )
        try:
            response = await asyncio.to_thread(
                query_bigquery_data_agent,
                question=question,
                config=active_cfg,
                storage=active_store,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("BigQuery Data Agent chat request failed")
            raise HTTPException(
                status_code=502,
                detail=f"BigQuery Data Agent request failed: {exc}",
            ) from exc
        return response.model_dump(mode="json")

    # =========================================================================
    # CR-9: Regional Template Studio & Canonical AST Registry Routes
    # =========================================================================

    @fastapi_app.get("/template-studio", response_class=HTMLResponse)
    async def serve_template_studio() -> HTMLResponse:
        ts_file = static_dir / "template_studio.html"
        if not ts_file.exists():
            raise HTTPException(status_code=404, detail="Template studio UI not found")
        return HTMLResponse(content=ts_file.read_text(encoding="utf-8"))

    @fastapi_app.post("/api/v1/templates/upload")
    async def upload_template_endpoint(
        file: UploadFile = File(...),
        country: str = Form("US"),
        state_province: str | None = Form(None),
        energy_technology: str | None = Form(None),
        form_type: str = Form(...),
        vintage_year: str = Form(...),
        revision: str = Form("v1"),
        template_name: str | None = Form(None),
        notes: str | None = Form(None),
        state: str | None = Form(None),
        technology: str | None = Form(None),
        form_name: str | None = Form(None),
        current_user: UserContext = Depends(auth_dep),
    ) -> dict[str, object]:
        require_admin(current_user)
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="Only .pdf files are supported")
        pdf_bytes = await file.read()
        if not pdf_bytes:
            raise HTTPException(status_code=400, detail="Uploaded template PDF file is empty")

        active_store: ContractStorageService = fastapi_app.state.storage
        sha256 = hashlib.sha256(pdf_bytes).hexdigest()

        # Deduplication by SHA-256
        existing = active_store.get_template_by_hash(sha256)
        if existing:
            bundle = active_store.get_template_bundle(existing.template_id)
            if bundle:
                return bundle.model_dump(mode="json")

        country_clean = (country or "US").strip().upper()
        state_clean = (state_province or state or "").strip().upper()
        tech_clean = (energy_technology or technology or "").strip()
        if not state_clean:
            raise HTTPException(status_code=400, detail="state_province or state is required")
        if not tech_clean:
            raise HTTPException(status_code=400, detail="energy_technology or technology is required")
        form_clean = form_type.strip()
        vintage_clean = str(vintage_year).strip()
        rev_clean = revision.strip().lower() or "v1"

        slug_tech = re.sub(r"[^a-z0-9]+", "_", tech_clean.lower()).strip("_")
        slug_form = re.sub(r"[^a-z0-9]+", "_", form_clean.lower()).strip("_")
        slug_state = re.sub(r"[^a-z0-9]+", "_", state_clean.lower()).strip("_")
        slug_country = re.sub(r"[^a-z0-9]+", "_", country_clean.lower()).strip("_")
        template_id = f"tpl_{slug_country}_{slug_state}_{slug_tech}_{slug_form}_{vintage_clean}_{rev_clean}"

        raw_name = template_name or form_name
        tpl_name = (
            raw_name.strip()
            if raw_name and raw_name.strip()
            else f"{state_clean} {form_clean.replace('_', ' ').title()} ({vintage_clean})"
        )

        tpl_dir = active_store.local_root / "templates" / template_id
        tpl_dir.mkdir(parents=True, exist_ok=True)
        (tpl_dir / file.filename).write_bytes(pdf_bytes)
        (tpl_dir / f"{template_id}.pdf").write_bytes(pdf_bytes)

        bundle: TemplateExtractionBundle | None = None
        extractor = fastapi_app.state.extractor
        if extractor is not None and hasattr(extractor, "extract_template"):
            try:
                bundle = await asyncio.to_thread(
                    extractor.extract_template,
                    pdf_bytes=pdf_bytes,
                    filename=file.filename,
                    template_id=template_id,
                    template_name=tpl_name,
                    country=country_clean,
                    state_province=state_clean,
                    energy_technology=tech_clean,
                    form_type=form_clean,
                    vintage_year=vintage_clean,
                    revision=rev_clean,
                    gcs_pdf_uri="",
                    created_by=current_user.email,
                )
            except Exception as exc:
                logger.warning(
                    "Gemini template extraction failed for %s, falling back to offline: %s",
                    template_id,
                    exc,
                )

        if bundle is None:
            bundle = active_store._extract_template_offline(
                pdf_bytes=pdf_bytes,
                filename=file.filename,
                template_id=template_id,
                template_name=tpl_name,
                country=country_clean,
                state_province=state_clean,
                energy_technology=tech_clean,
                form_type=form_clean,
                vintage_year=vintage_clean,
                revision=rev_clean,
                gcs_pdf_uri="",
            )
            bundle.metadata.created_by = current_user.email

        active_store.insert_template_bundle(bundle)
        return bundle.model_dump(mode="json")

    @fastapi_app.get("/api/v1/templates")
    async def list_templates_endpoint(
        country: str | None = None,
        state_province: str | None = None,
        state: str | None = None,
        energy_technology: str | None = None,
        technology: str | None = None,
        form_type: str | None = None,
        vintage_year: str | None = None,
        current_user: UserContext = Depends(auth_dep),
    ) -> list[dict[str, object]]:
        active_store: ContractStorageService = fastapi_app.state.storage
        st = state_province or state
        tech = energy_technology or technology
        templates = active_store.list_templates(
            country=country,
            state_province=st,
            energy_technology=tech,
            form_type=form_type,
            vintage_year=vintage_year,
        )
        return [t.model_dump(mode="json") for t in templates]

    @fastapi_app.get("/api/v1/templates/{template_id}")
    async def get_template_bundle_endpoint(
        template_id: str,
        current_user: UserContext = Depends(auth_dep),
    ) -> dict[str, object]:
        active_store: ContractStorageService = fastapi_app.state.storage
        bundle = active_store.get_template_bundle(template_id)
        if not bundle:
            raise HTTPException(status_code=404, detail=f"Template {template_id} not found")
        return bundle.model_dump(mode="json")

    @fastapi_app.get("/api/v1/templates/{template_id}/pdf")
    async def get_template_pdf_endpoint(
        template_id: str,
        current_user: UserContext = Depends(auth_dep),
    ) -> FileResponse:
        active_store: ContractStorageService = fastapi_app.state.storage
        pdf_path = active_store.get_template_pdf_path(template_id)
        if not pdf_path or not pdf_path.exists():
            raise HTTPException(status_code=404, detail=f"Template PDF for {template_id} not found")
        return FileResponse(
            pdf_path,
            media_type="application/pdf",
            filename=pdf_path.name,
            headers={"Content-Disposition": f'inline; filename="{pdf_path.name}"'},
        )

    @fastapi_app.post("/api/v1/templates/seed")
    async def seed_templates_endpoint(
        current_user: UserContext = Depends(auth_dep),
    ) -> dict[str, object]:
        require_admin(current_user)
        active_store: ContractStorageService = fastapi_app.state.storage
        seeded = active_store.seed_sample_templates(parser=None)
        return {
            "status": "ok",
            "seeded": [s.model_dump(mode="json") for s in seeded],
            "seeded_count": len(seeded),
            "count": len(seeded),
        }

    @fastapi_app.get("/api/v1/contracts/{document_id}/variance/templates")
    async def get_contract_variance_templates(
        document_id: str,
        current_user: UserContext = Depends(auth_dep),
    ) -> list[dict[str, object]]:
        """Rank active templates by affinity to the executed contract."""
        active_store: ContractStorageService = fastapi_app.state.storage
        doc_bundle = active_store.get_document_bundle(document_id)
        if not doc_bundle:
            raise HTTPException(status_code=404, detail=f"Contract document {document_id} not found")

        doc_dict = doc_bundle.document.model_dump(mode="json")
        templates = active_store.list_templates()
        if not templates:
            # Try auto-seeding if empty
            templates = active_store.seed_sample_templates(parser=None)

        candidates = rank_candidate_templates(doc_dict, templates)
        return [c.model_dump(mode="json") for c in candidates]

    @fastapi_app.post("/api/v1/contracts/{document_id}/compare")
    async def compare_contract_variance_endpoint(
        document_id: str,
        template_id: str | None = None,
        current_user: UserContext = Depends(auth_dep),
    ) -> dict[str, object]:
        """Run variance comparison against auto-suggested or specified baseline template."""
        if current_user.role not in (UserRole.ADMIN, "analyst"):
            raise HTTPException(status_code=403, detail="Only ADMIN or ANALYST users can execute variance comparison")

        active_store: ContractStorageService = fastapi_app.state.storage
        doc_bundle = active_store.get_document_bundle(document_id)
        if not doc_bundle:
            raise HTTPException(status_code=404, detail=f"Contract document {document_id} not found")

        doc_dict = doc_bundle.document.model_dump(mode="json")
        templates = active_store.list_templates()
        if not templates:
            templates = active_store.seed_sample_templates(parser=None)

        target_template_id = template_id
        binding_mode = "MANUAL_OVERRIDE" if template_id else "AUTO_SUGGESTED"

        if not target_template_id:
            candidates = rank_candidate_templates(doc_dict, templates)
            if not candidates:
                raise HTTPException(status_code=400, detail="No master templates available for comparison")
            target_template_id = candidates[0].template_id

        tpl_bundle = active_store.get_template_bundle(target_template_id)
        if not tpl_bundle:
            raise HTTPException(status_code=404, detail=f"Master template {target_template_id} not found")

        # Convert clauses to dicts
        ctr_clauses = [c.model_dump(mode="json") for c in doc_bundle.clauses]
        tpl_clauses = [tc.model_dump(mode="json") for tc in tpl_bundle.clauses]

        affinity_score = calculate_template_affinity(doc_dict, tpl_bundle.metadata)

        bundle = run_contract_variance_comparison(
            contract_doc=doc_dict,
            contract_clauses=ctr_clauses,
            template_row=tpl_bundle.metadata,
            template_clauses=tpl_clauses,
            binding_mode=binding_mode,
            affinity_score=affinity_score,
        )

        active_store.save_contract_variance(bundle)

        return {
            "variance_id": bundle.variance.variance_id,
            "document_id": bundle.variance.document_id,
            "template_id": bundle.variance.template_id,
            "binding_mode": bundle.variance.binding_mode,
            "affinity_score": bundle.variance.affinity_score,
            "variance": bundle.variance.model_dump(mode="json"),
            "summary": bundle.summary.model_dump(mode="json"),
            "details": [d.model_dump(mode="json") for d in bundle.details],
            "created_at": bundle.variance.created_at,
        }

    @fastapi_app.get("/api/v1/contracts/{document_id}/variance")
    async def get_contract_variance_endpoint(
        document_id: str,
        current_user: UserContext = Depends(auth_dep),
    ) -> dict[str, object]:
        """Fetch latest variance scorecard and redline details for a contract."""
        active_store: ContractStorageService = fastapi_app.state.storage
        result = active_store.get_latest_contract_variance(document_id)
        if not result:
            raise HTTPException(status_code=404, detail=f"No variance comparison found for document {document_id}")
        if hasattr(result, "model_dump"):
            return result.model_dump(mode="json")
        return result

    # -------------------------------------------------------------------------
    # CR-12: Batch Ingestion & Historical Discovery Endpoints
    # -------------------------------------------------------------------------

    @fastapi_app.post("/api/v1/batch/scan", response_model=BatchScanResponse)
    async def batch_scan_endpoint(
        request: BatchScanRequest,
        current_user: UserContext = Depends(auth_dep),
    ) -> BatchScanResponse:
        """Pre-flight scan of GCS inbox or local directory using Option 4a metadata."""
        active_store: ContractStorageService = fastapi_app.state.storage
        try:
            return scan_batch_inbox(request.gcs_prefix, active_store)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @fastapi_app.post("/api/v1/batch/launch", status_code=202, response_model=BatchLaunchResponse)
    async def batch_launch_endpoint(
        request: BatchLaunchRequest,
        current_user: UserContext = Depends(auth_dep),
    ) -> BatchLaunchResponse:
        """Launch asynchronous historical batch contract ingestion (Admin only)."""
        require_admin(current_user)
        active_store: ContractStorageService = fastapi_app.state.storage
        cfg: PipelineConfig = fastapi_app.state.config

        try:
            job, items = initialize_batch_job(
                gcs_prefix=request.gcs_prefix,
                concurrency=request.concurrency,
                storage_service=active_store,
                config=cfg,
                force_reprocess=request.force_reprocess,
                triggered_by=current_user.email,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed initializing batch job: {exc}")

        use_local = os.getenv("USE_LOCAL_BATCH_RUNNER", "true").lower() in ("true", "1", "yes")
        triggered_cloud_run = False

        if not use_local and request.gcs_prefix.startswith("gs://"):
            gcp_proj = cfg.google_cloud_project
            region = os.getenv("REGION", "us-central1")
            run_args = [
                "--job-id",
                job.job_id,
                "--gcs-prefix",
                request.gcs_prefix,
                "--concurrency",
                str(request.concurrency),
            ]
            # Primary execution path: Google Cloud Run v2 Client SDK
            try:
                import google.cloud.run_v2 as run_v2

                job_client = run_v2.JobsClient()
                run_req = run_v2.RunJobRequest(
                    name=f"projects/{gcp_proj}/locations/{region}/jobs/contract-parser-batch",
                    overrides=run_v2.RunJobRequest.Overrides(
                        container_overrides=[
                            run_v2.RunJobRequest.Overrides.ContainerOverride(
                                args=run_args
                            )
                        ]
                    ),
                )
                job_client.run_job(request=run_req)
                triggered_cloud_run = True
                logger.info("Cloud Run Job 'contract-parser-batch' triggered via SDK for %s", job.job_id)
            except Exception as cr_exc:
                logger.warning(
                    "Cloud Run Job SDK trigger failed (%s); attempting Cloud Run REST API v2 fallback...",
                    cr_exc,
                )
                # Resilient secondary execution path: Direct Cloud Run v2 REST API call
                try:
                    import google.auth
                    import google.auth.transport.requests
                    import httpx

                    creds, _ = google.auth.default(
                        scopes=["https://www.googleapis.com/auth/cloud-platform"]
                    )
                    auth_req = google.auth.transport.requests.Request()
                    creds.refresh(auth_req)
                    job_url = (
                        f"https://run.googleapis.com/v2/projects/{gcp_proj}/locations/"
                        f"{region}/jobs/contract-parser-batch:run"
                    )
                    rest_payload = {
                        "overrides": {
                            "containerOverrides": [
                                {
                                    "args": run_args
                                }
                            ]
                        }
                    }
                    async with httpx.AsyncClient() as client:
                        rest_resp = await client.post(
                            job_url,
                            headers={
                                "Authorization": f"Bearer {creds.token}",
                                "Content-Type": "application/json",
                            },
                            json=rest_payload,
                            timeout=30.0,
                        )
                        rest_resp.raise_for_status()
                    triggered_cloud_run = True
                    logger.info(
                        "Cloud Run Job 'contract-parser-batch' triggered via REST API for %s",
                        job.job_id,
                    )
                except Exception as rest_exc:
                    logger.error(
                        "Cloud Run Job REST API trigger also failed (%s); falling back to local runner",
                        rest_exc,
                    )

        if not triggered_cloud_run:
            asyncio.create_task(
                run_local_batch_pipeline(
                    job_id=job.job_id,
                    config=cfg,
                    storage_service=active_store,
                    concurrency=request.concurrency,
                )
            )

        return BatchLaunchResponse(
            job_id=job.job_id,
            status=job.status,
            gcs_prefix=job.gcs_prefix,
            total_files=job.total_files,
            concurrency=request.concurrency,
            message="Batch job launched in background",
        )

    @fastapi_app.get("/api/v1/batch/jobs/{job_id}", response_model=BatchJobDetailResponse)
    async def get_batch_job_detail_endpoint(
        job_id: str,
        current_user: UserContext = Depends(auth_dep),
    ) -> BatchJobDetailResponse:
        """Fetch live status and document items for a batch job."""
        active_store: ContractStorageService = fastapi_app.state.storage
        try:
            job = active_store.get_batch_job(job_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Batch job {job_id} not found")
        items = active_store.get_batch_job_items(job_id)
        return BatchJobDetailResponse(job=job, items=items)

    @fastapi_app.get("/api/v1/batch/jobs", response_model=list[BatchJobRow])
    async def list_batch_jobs_endpoint(
        limit: int = 50,
        current_user: UserContext = Depends(auth_dep),
    ) -> list[BatchJobRow]:
        """List historical batch execution runs."""
        active_store: ContractStorageService = fastapi_app.state.storage
        return active_store.list_batch_jobs(limit=limit)

    @fastapi_app.post("/api/v1/batch/retry/{job_id}", response_model=BatchRetryResponse)
    async def retry_batch_job_endpoint(
        job_id: str,
        concurrency: int = 5,
        current_user: UserContext = Depends(auth_dep),
    ) -> BatchRetryResponse:
        """Purge partial records and re-queue failed documents for a batch job (Admin only)."""
        require_admin(current_user)
        active_store: ContractStorageService = fastapi_app.state.storage
        cfg: PipelineConfig = fastapi_app.state.config
        try:
            re_queued_count, _ = retry_failed_batch_items(
                job_id=job_id,
                storage_service=active_store,
                config=cfg,
                concurrency=concurrency,
            )
            return BatchRetryResponse(
                job_id=job_id,
                re_queued_count=re_queued_count,
                status=BatchJobStatus.RUNNING,
                message=f"Re-queued {re_queued_count} failed document(s)",
            )
        except ValueError as val_err:
            raise HTTPException(status_code=400, detail=str(val_err))
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Batch job {job_id} not found")
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    return fastapi_app



app = create_app()


def cli_main(argv: list[str] | None = None) -> int:
    """Command-line entrypoint: `python -m contract_parser.app ingest <pdf_path_or_gcs_uri>`."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(
        prog="contract-parser",
        description="Gemini-First Hierarchical Contract Parsing & Portfolio Intelligence CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ingest_cmd = sub.add_parser("ingest", help="Ingest a PDF from local path or gs:// URI")
    ingest_cmd.add_argument("pdf_path_or_uri", help="Local PDF path or gs:// URI")
    ingest_cmd.add_argument(
        "--project-id",
        default="prj_cedar_lantern_wind",
        help="Target ERP Project ID (default: prj_cedar_lantern_wind)",
    )
    ingest_cmd.add_argument(
        "--landowner-id",
        default=None,
        help="Optional QRM Landowner ID (auto-bound from PDF Grantor if omitted)",
    )
    ingest_cmd.add_argument(
        "--output-dir",
        default=None,
        help="Optional directory to copy exported CSVs into",
    )

    serve_cmd = sub.add_parser("serve", help="Run the FastAPI + pdf.js HITL Review Web UI")
    serve_cmd.add_argument("--host", default="0.0.0.0")
    serve_cmd.add_argument("--port", type=int, default=8080)

    args = parser.parse_args(argv)
    cfg = PipelineConfig()

    if args.command == "ingest":
        store = ContractStorageService(cfg)
        bundle = ingest_contract(
            pdf_path_or_uri=args.pdf_path_or_uri,
            project_id=args.project_id,
            landowner_id=args.landowner_id,
            config=cfg,
            storage_service=store,
        )
        doc_export_dir = store.local_exports_dir / bundle.document.document_id
        exported = {
            name: str(doc_export_dir / name)
            for name in (
                "clauses.csv",
                "defined_terms.csv",
                "exhibits_catalog.csv",
                "special_conditions.csv",
                "dnd_checklist_signoffs.csv",
            )
        }
        if args.output_dir:
            out_dir = Path(args.output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            for name, src_path in exported.items():
                (out_dir / name).write_bytes(Path(src_path).read_bytes())
        summary = {
            "document_id": bundle.document.document_id,
            "filename": bundle.document.filename,
            "project_id": bundle.document.project_id,
            "landowner_id": bundle.document.landowner_id,
            "energy_technology": bundle.document.energy_technology.value,
            "grantor_landowner_name": bundle.document.grantor_landowner_name,
            "grantee_entity_name": bundle.document.grantee_entity_name,
            "gcs_pdf_uri": bundle.document.gcs_pdf_uri,
            "gcs_export_prefix": bundle.document.gcs_export_prefix,
            "page_count": bundle.document.page_count,
            "effective_date": bundle.document.effective_date,
            "clauses_count": len(bundle.clauses),
            "defined_terms_count": len(bundle.defined_terms),
            "exhibits_count": len(bundle.exhibits_catalog),
            "special_conditions_count": len(bundle.special_conditions),
            "flagged_node_count": bundle.document.flagged_node_count,
            "ingestion_status": bundle.document.ingestion_status.value,
            "exported_csvs": exported,
        }
        print(json.dumps(summary, indent=2))
        return 0

    if args.command == "serve":
        import uvicorn

        uvicorn.run("contract_parser.app:app", host=args.host, port=args.port, reload=False)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(cli_main())
