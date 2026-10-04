"""FastAPI web application and CLI entrypoint for the Gemini-First Contract Parser."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel

from contract_parser.config import PipelineConfig
from contract_parser.gemini_parser import (
    ContractExtractorProtocol,
    GeminiContractParser,
    make_landowner_id as _make_landowner_id,
)
from contract_parser.schemas import (
    ClauseReviewRequest,
    CreateDNDSignoffRequest,
    CreateProjectRequest,
    DocumentRegistryRow,
    EnergyTechnology,
    HITLStatus,
    IngestionStatus,
    ParsedContractBundle,
    ProjectRow,
    utc_now_iso,
)
from contract_parser.storage import ContractStorageService, compute_document_id

logger = logging.getLogger(__name__)


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

    store.ensure_portfolio_seed()
    try:
        project = store.get_project(project_id)
    except KeyError:
        project = store.get_project("prj_cedar_lantern_wind")

    doc_id = compute_document_id(pdf_bytes)
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


def create_app(
    config: PipelineConfig | None = None,
    storage_service: ContractStorageService | None = None,
    extractor: ContractExtractorProtocol | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application."""
    cfg = config or PipelineConfig()
    store = storage_service or ContractStorageService(cfg)
    parser = extractor or GeminiContractParser(cfg)

    fastapi_app = FastAPI(
        title="Hierarchical Contract Parsing & Portfolio Obligation Intelligence",
        version="0.4.0",
        description="Gemini-First Multimodal Contract Hierarchy Parser, 8-Table Portfolio & Field Crew DND Store, and pdf.js HITL Review UI",
    )
    fastapi_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    fastapi_app.state.config = cfg
    fastapi_app.state.storage = store
    fastapi_app.state.extractor = parser

    static_dir = Path(__file__).parent / "static"

    @fastapi_app.get("/", response_class=HTMLResponse)
    async def serve_index() -> HTMLResponse:
        index_file = static_dir / "index.html"
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"))

    @fastapi_app.get("/api/v1/projects")
    async def list_projects_endpoint(
        energy_technology: str | None = None,
    ) -> dict[str, object]:
        active_store: ContractStorageService = fastapi_app.state.storage
        projects = active_store.list_projects(energy_technology=energy_technology)
        return {
            "projects": [p.model_dump(mode="json") for p in projects],
            "count": len(projects),
        }

    @fastapi_app.post("/api/v1/projects")
    async def create_project_endpoint(req: CreateProjectRequest) -> dict[str, object]:
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
    async def list_project_landowners_endpoint(project_id: str) -> dict[str, object]:
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
    ) -> dict[str, object]:
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
    async def ingest_from_gcs(req: BatchIngestRequest) -> dict[str, object]:
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
    async def get_document(document_id: str) -> dict[str, object]:
        active_store: ContractStorageService = fastapi_app.state.storage
        try:
            bundle = active_store.get_bundle(document_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        payload = bundle.model_dump(mode="json")
        payload["pdf_url"] = f"/api/v1/documents/{document_id}/pdf"
        return payload

    @fastapi_app.get("/api/v1/documents/{document_id}/pdf")
    async def stream_document_pdf(document_id: str, request: Request) -> Response:
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
        document_id: str, node_id: str, review: ClauseReviewRequest
    ) -> dict[str, object]:
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
    ) -> dict[str, object]:
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
    async def export_csv_endpoint(document_id: str, csv_name: str) -> Response:
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
