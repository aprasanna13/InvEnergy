"""Gemini-First Hierarchical Contract Parsing & Context Preservation Platform (Session 1)."""

from contract_parser.config import PipelineConfig
from contract_parser.schemas import (
    ClauseReviewRequest,
    ClauseRow,
    DefinedTermRow,
    DefinitionType,
    DocumentRegistryRow,
    DocumentZone,
    ExhibitCatalogRow,
    ExhibitModality,
    FlagCode,
    GeminiContractExtraction,
    HITLStatus,
    IngestionStatus,
    NumberingScheme,
    ParsedContractBundle,
)


def parse_contract(
    pdf_path_or_uri: str,
    config: PipelineConfig | None = None,
) -> ParsedContractBundle:
    """Ingest, parse, archive, and persist a legal agreement PDF end-to-end."""
    from contract_parser.app import ingest_contract

    return ingest_contract(pdf_path_or_uri=pdf_path_or_uri, config=config)


__all__ = [
    "ClauseReviewRequest",
    "ClauseRow",
    "DefinedTermRow",
    "DefinitionType",
    "DocumentRegistryRow",
    "DocumentZone",
    "ExhibitCatalogRow",
    "ExhibitModality",
    "FlagCode",
    "GeminiContractExtraction",
    "HITLStatus",
    "IngestionStatus",
    "NumberingScheme",
    "ParsedContractBundle",
    "PipelineConfig",
    "parse_contract",
]
