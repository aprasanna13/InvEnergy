"""Gemini-First Hierarchical Contract Parsing & Context Preservation Platform (Session 1)."""

from contract_parser.config import PipelineConfig
from contract_parser.schemas import (
    ClauseReviewRequest,
    ClauseRow,
    ConstraintCategory,
    ConstructionTrade,
    CreateDNDSignoffRequest,
    CreateProjectRequest,
    DefinedTermRow,
    DefinitionType,
    DNDChecklistBundle,
    DNDChecklistItem,
    DNDChecklistSignoffRow,
    DNDDispatchClearance,
    DNDDispatchReadiness,
    DNDSeverityLevel,
    DocumentRegistryRow,
    DocumentZone,
    EnergyTechnology,
    ExhibitCatalogRow,
    ExhibitModality,
    FlagCode,
    GeminiContractExtraction,
    HITLStatus,
    IngestionStatus,
    LandownerRow,
    NumberingScheme,
    ParsedContractBundle,
    ProjectRow,
    SpecialConditionRow,
    build_dnd_checklist_item,
    classify_dnd_condition,
)


def parse_contract(
    pdf_path_or_uri: str,
    config: PipelineConfig | None = None,
    project_id: str = "prj_cedar_lantern_wind",
    landowner_id: str | None = None,
) -> ParsedContractBundle:
    """Ingest, parse, archive, and persist a legal agreement PDF end-to-end."""
    from contract_parser.app import ingest_contract

    return ingest_contract(
        pdf_path_or_uri=pdf_path_or_uri,
        config=config,
        project_id=project_id,
        landowner_id=landowner_id,
    )


__all__ = [
    "ClauseReviewRequest",
    "ClauseRow",
    "ConstraintCategory",
    "ConstructionTrade",
    "CreateDNDSignoffRequest",
    "CreateProjectRequest",
    "DefinedTermRow",
    "DefinitionType",
    "DNDChecklistBundle",
    "DNDChecklistItem",
    "DNDChecklistSignoffRow",
    "DNDDispatchClearance",
    "DNDDispatchReadiness",
    "DNDSeverityLevel",
    "DocumentRegistryRow",
    "DocumentZone",
    "EnergyTechnology",
    "ExhibitCatalogRow",
    "ExhibitModality",
    "FlagCode",
    "GeminiContractExtraction",
    "HITLStatus",
    "IngestionStatus",
    "LandownerRow",
    "NumberingScheme",
    "ParsedContractBundle",
    "PipelineConfig",
    "ProjectRow",
    "SpecialConditionRow",
    "build_dnd_checklist_item",
    "classify_dnd_condition",
    "parse_contract",
]
