"""Multi-pass contract extraction bundle assembler and reconciler."""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

from contract_parser.schemas import (
    ClauseRow,
    ConstraintCategory,
    ContractStructureIndex,
    DefinedTermRow,
    DefinitionType,
    DocumentZone,
    ExhibitCatalogRow,
    ExhibitModality,
    FlagCode,
    GeminiContractExtraction,
    HITLStatus,
    NumberingScheme,
    SpecialConditionRow,
    utc_now_iso,
)

if TYPE_CHECKING:
    from contract_parser.schemas import BodyPassExtraction, ExhibitsPassExtraction

logger = logging.getLogger(__name__)


def clean_exhibit_token(exhibit_id: str) -> str:
    """Normalize exhibit identifier into a clean node suffix (e.g. 'Exhibit A' -> 'A', 'Exhibit A-1' -> 'A_1')."""
    token = exhibit_id.strip()
    token = re.sub(r"(?i)^exhibit[\s_]*", "", token)
    token = re.sub(r"(?i)^schedule[\s_]*", "", token)
    token = re.sub(r"(?i)^appendix[\s_]*", "", token)
    token = re.sub(r"[^A-Za-z0-9]+", "_", token).strip("_").upper()
    return token or "A"


def assemble_multi_pass_extraction(
    *,
    index: ContractStructureIndex,
    body_pass: BodyPassExtraction,
    exhibits_pass: ExhibitsPassExtraction,
    total_pages: int,
) -> GeminiContractExtraction:
    """Assemble raw structural extraction from Pass 1, Pass 2, and Pass 3 into a GeminiContractExtraction container.

    Enforces:
    - Preamble harmonization (preserves Pass 2 verbatim preamble; synthesizes fallback only on omission)
    - Signature placeholders synthesis with DEFERRED_MODALITY_PLACEHOLDER
    - Zone-guard filtering and collision resolution
    - Exhibits catalog reconciliation and visual CAD/map stub synthesis
    - Defined terms deduplication prioritizing dedicated definitions and body definitions
    """
    now_iso = utc_now_iso()
    raw_clauses: list[ClauseRow] = []

    body_clauses: list[ClauseRow] = [
        c.to_clause_row() if hasattr(c, "to_clause_row") else c
        for c in body_pass.clauses
    ]
    exhibit_clauses: list[ClauseRow] = [
        c.to_clause_row() if hasattr(c, "to_clause_row") else c
        for c in exhibits_pass.clauses
    ]

    # 1. Preamble Harmonization (DF-3)
    preamble_nodes = [
        c
        for c in body_clauses
        if c.document_zone == DocumentZone.PREAMBLE or c.node_id == "PREAMBLE.1"
    ]
    if not preamble_nodes:
        logger.info("Pass 2 omitted PREAMBLE.1; synthesizing fallback preamble from structural index.")
        parties_desc = ""
        if index.grantor_landowner_name and index.grantee_entity_name:
            parties_desc = f" by and between {index.grantor_landowner_name} and {index.grantee_entity_name}"
        elif index.grantor_landowner_name:
            parties_desc = f" by {index.grantor_landowner_name}"
        date_desc = f", dated {index.effective_date}" if index.effective_date else ""
        verbatim_preamble = f"{index.document_title}{parties_desc}{date_desc}."

        fallback_preamble = ClauseRow(
            node_id="PREAMBLE.1",
            canonical_path="PREAMBLE.1",
            document_zone=DocumentZone.PREAMBLE,
            depth=1,
            clause_title="Preamble",
            clause_label="Preamble",
            numbering_scheme=NumberingScheme.UNNUMBERED,
            page_start=1,
            page_end=1,
            verbatim_text=verbatim_preamble,
            reconstructed_context_text=verbatim_preamble,
            preamble_text=verbatim_preamble,
            postamble_text=None,
            hitl_status=HITLStatus.VERIFIED_AUTO,
            hitl_flag_reasons="NONE",
            created_at=now_iso,
            updated_at=now_iso,
        )
        raw_clauses.append(fallback_preamble)

    # 2. Body Clauses & Zone Guard (DF-4, DF-5)
    # Discard any exhibit/amendment clauses that leaked into Pass 2
    for c in body_clauses:
        if c.document_zone in (
            DocumentZone.PREAMBLE,
            DocumentZone.RECITALS,
            DocumentZone.BODY,
        ):
            raw_clauses.append(c)

    # 3. Signature Execution Placeholders (DF-7)
    sig_start = index.signature_start_page
    sig_end = index.signature_end_page or sig_start
    if sig_start is not None and sig_start >= 1:
        signer_names = [
            f"{s.party_name}: {s.signer_name} ({s.signer_title})"
            for s in index.signers
            if s.signer_name
        ]
        summary_text = (
            f"[Signatures and Acknowledgments: {'; '.join(signer_names)}]"
            if signer_names
            else "[Signatures, Execution Blocks, and Notary Acknowledgments]"
        )

        for p in range(sig_start, sig_end + 1):
            idx = p - sig_start + 1
            node_id = f"SIGNATURES.{idx}"
            raw_clauses.append(
                ClauseRow(
                    node_id=node_id,
                    canonical_path=node_id,
                    document_zone=DocumentZone.SIGNATURES,
                    depth=1,
                    clause_title=f"Signatures & Acknowledgments (Page {p})",
                    clause_label=f"Signatures {idx}",
                    numbering_scheme=NumberingScheme.NAMED_HEADER,
                    page_start=p,
                    page_end=p,
                    verbatim_text=summary_text,
                    reconstructed_context_text=summary_text,
                    preamble_text=summary_text,
                    postamble_text=None,
                    hitl_status=HITLStatus.PLACEHOLDER_FOR_REVIEW,
                    hitl_flag_reasons=FlagCode.DEFERRED_MODALITY_PLACEHOLDER.value,
                    created_at=now_iso,
                    updated_at=now_iso,
                )
            )

    # 4. Exhibits Clauses & Zone Guard (DF-4, DF-5)
    # Discard any body/preamble clauses that leaked into Pass 3
    for c in exhibit_clauses:
        if c.document_zone in (
            DocumentZone.EXHIBIT_OR_SCHEDULE,
            DocumentZone.AMENDMENT,
        ):
            raw_clauses.append(c)

    # Deduplicate clauses on node_id to resolve any shared boundary page collision
    seen_nodes: set[str] = set()
    deduped_clauses: list[ClauseRow] = []
    for c in raw_clauses:
        if c.node_id not in seen_nodes:
            seen_nodes.add(c.node_id)
            deduped_clauses.append(c)
        else:
            logger.warning("Duplicate node_id '%s' resolved; retaining primary instance.", c.node_id)

    # 5. Exhibits Catalog Reconciliation & CAD/Map Stub Synthesis (DF-7)
    catalog_by_id: dict[str, ExhibitCatalogRow] = {
        e.exhibit_id.strip(): e for e in exhibits_pass.exhibits_catalog
    }

    # Ensure every exhibit discovered in Pass 1 is present in catalog
    for entry in index.exhibits:
        ex_id_key = entry.exhibit_id.strip()
        if ex_id_key not in catalog_by_id:
            catalog_by_id[ex_id_key] = ExhibitCatalogRow(
                document_id="doc_placeholder",
                exhibit_id=entry.exhibit_id,
                exhibit_title=entry.exhibit_title,
                exhibit_modality=entry.exhibit_modality,
                page_start=entry.page_start,
                page_end=entry.page_end,
                has_unresolved_external_dep=False,
                referenced_by_nodes="NONE",
                created_at=now_iso,
                updated_at=now_iso,
            )

    # Synthesize stub clauses for visual CAD/map exhibits or exhibits lacking clauses
    clause_node_ids = {c.node_id for c in deduped_clauses}
    for entry in index.exhibits:
        clean_id = clean_exhibit_token(entry.exhibit_id)
        has_matching_clause = any(
            c.node_id.startswith(f"EXHIBIT_{clean_id}") for c in deduped_clauses
        )
        if not has_matching_clause or entry.exhibit_modality == ExhibitModality.VISUAL_DRAWING_OR_MAP_STUB:
            stub_node_id = f"EXHIBIT_{clean_id}.STUB"
            if stub_node_id not in clause_node_ids:
                clause_node_ids.add(stub_node_id)
                stub_text = f"[{entry.exhibit_id}: {entry.exhibit_title} (Visual Plat Map / CAD Drawing / Non-Prose Schedule)]"
                deduped_clauses.append(
                    ClauseRow(
                        node_id=stub_node_id,
                        canonical_path=stub_node_id,
                        parent_node_id=f"EXHIBIT_{clean_id}",
                        document_zone=DocumentZone.EXHIBIT_OR_SCHEDULE,
                        depth=2,
                        clause_title=f"{entry.exhibit_id} Visual / Map Stub",
                        clause_label="Stub",
                        numbering_scheme=NumberingScheme.NAMED_HEADER,
                        level_1_label=entry.exhibit_id,
                        level_2_label="STUB",
                        page_start=entry.page_start,
                        page_end=entry.page_end,
                        verbatim_text=stub_text,
                        reconstructed_context_text=stub_text,
                        preamble_text=stub_text,
                        postamble_text=None,
                        hitl_status=HITLStatus.PLACEHOLDER_FOR_REVIEW,
                        hitl_flag_reasons=FlagCode.DEFERRED_MODALITY_PLACEHOLDER.value,
                        created_at=now_iso,
                        updated_at=now_iso,
                    )
                )

    # 6. Defined Terms Deduplication (DF-9)
    # Rule: DEDICATED_DEFINITION_CLAUSE > INLINE_PARENTHETICAL > earlier page number
    # Body definitions take precedence over exhibit definitions for equal priority
    terms_by_name: dict[str, DefinedTermRow] = {}

    body_terms: list[DefinedTermRow] = [
        t.to_defined_term_row() if hasattr(t, "to_defined_term_row") else t
        for t in body_pass.defined_terms
    ]
    exhibit_terms: list[DefinedTermRow] = [
        t.to_defined_term_row() if hasattr(t, "to_defined_term_row") else t
        for t in exhibits_pass.defined_terms
    ]

    # 6. Defined Terms Deduplication (DF-9)
    # Rule: DEDICATED_DEFINITION_CLAUSE > INLINE_PARENTHETICAL > earlier page number
    # Body definitions take precedence over exhibit definitions for equal priority
    ranked_terms: dict[str, tuple[tuple[int, int, int], DefinedTermRow]] = {}

    def term_rank(t: DefinedTermRow, is_body: bool) -> tuple[int, int, int]:
        def_score = 2 if t.definition_type == DefinitionType.DEDICATED_DEFINITION_CLAUSE else 1
        zone_score = 1 if is_body else 0
        page_score = -t.page_number
        return (def_score, zone_score, page_score)

    for term in body_terms:
        key = term.term_name.strip().upper()
        rank = term_rank(term, is_body=True)
        if key not in ranked_terms or rank > ranked_terms[key][0]:
            ranked_terms[key] = (rank, term)

    for term in exhibit_terms:
        key = term.term_name.strip().upper()
        rank = term_rank(term, is_body=False)
        if key not in ranked_terms or rank > ranked_terms[key][0]:
            ranked_terms[key] = (rank, term)

    terms_by_name: dict[str, DefinedTermRow] = {k: v[1] for k, v in ranked_terms.items()}

    # 7. Special Conditions Merging
    combined_conditions: list[SpecialConditionRow] = []
    seen_condition_hashes: set[str] = set()

    body_conditions: list[SpecialConditionRow] = [
        s.to_special_condition_row() if hasattr(s, "to_special_condition_row") else s
        for s in body_pass.special_conditions
    ]
    exhibit_conditions: list[SpecialConditionRow] = [
        s.to_special_condition_row() if hasattr(s, "to_special_condition_row") else s
        for s in exhibits_pass.special_conditions
    ]

    for sc in body_conditions + exhibit_conditions:
        # Deduplicate identical conditions on (node_id, target_asset_or_area, verbatim_excerpt[:60])
        sig = f"{sc.node_id}:{sc.target_asset_or_area}:{sc.verbatim_excerpt[:60].strip().lower()}"
        if sig not in seen_condition_hashes:
            seen_condition_hashes.add(sig)
            combined_conditions.append(sc)

    # 8. Contracting Parties JSON
    parties_list = []
    if index.grantor_landowner_name:
        parties_list.append({
            "name": index.grantor_landowner_name,
            "short_name": index.grantor_landowner_name.split()[0],
            "role": "Landowner / Owner",
        })
    if index.grantee_entity_name:
        parties_list.append({
            "name": index.grantee_entity_name,
            "short_name": index.grantee_entity_name.split()[0],
            "role": "Grantee / Developer",
        })
    parties_json = json.dumps(parties_list) if parties_list else "[]"

    # Assemble raw GeminiContractExtraction container (DF-5)
    return GeminiContractExtraction(
        page_count=total_pages,
        contracting_parties_json=parties_json,
        effective_date=index.effective_date,
        grantor_landowner_name=index.grantor_landowner_name,
        grantee_entity_name=index.grantee_entity_name,
        clauses=deduped_clauses,
        defined_terms=list(terms_by_name.values()),
        exhibits_catalog=list(catalog_by_id.values()),
        special_conditions=combined_conditions,
    )
