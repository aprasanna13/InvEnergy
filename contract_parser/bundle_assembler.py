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
    PageSpan,
    PlaceholderVariable,
    RawTemplateClause,
    SpecialConditionRow,
    TemplateClauseRow,
    TemplateExtractionBundle,
    TemplateRegistryRow,
    utc_now_iso,
)

if TYPE_CHECKING:
    from contract_parser.schemas import BodyPassExtraction, ExhibitsPassExtraction

logger = logging.getLogger(__name__)


def clean_exhibit_token(exhibit_id: str) -> str:
    """Normalize exhibit identifier into a clean node suffix (e.g. 'Exhibit A' -> 'A', 'Exhibit A-1' -> 'A_1')."""
    token = exhibit_id.strip()
    m = re.search(r"(?i)\b(?:exhibit|schedule|appendix)\s+([A-Z0-9]+(?:[-_][A-Z0-9]+)*)\b", token)
    if m:
        return m.group(1).replace("-", "_").upper()
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


# ---------------------------------------------------------------------------
# CR-9: Template Canonical AST Bundle Assembler & Placeholder Tokenizer
# ---------------------------------------------------------------------------

PLACEHOLDER_REGEX = re.compile(
    r"(?P<currency>\$)?\[(?P<inner>_{2,}|[A-Za-z0-9_ /.,-]+|\.\.\.)\]"
)


def infer_placeholder_type(bracket_text: str, has_dollar: bool = False) -> str:
    """Return inferred data type hint for bracket text."""
    clean_inner = bracket_text.strip("[]$ \t\n")
    lower_inner = clean_inner.lower()
    if (
        has_dollar
        or "$" in bracket_text
        or any(kw in lower_inner for kw in ("amount", "dollar", "rent", "payment", "fee", "cost", "sum", "price"))
        or set(clean_inner) == {"_"}
    ):
        return "CURRENCY"
    if any(kw in lower_inner for kw in ("county", "state", "jurisdiction", "city", "township", "parish")):
        return "JURISDICTION"
    if any(kw in lower_inner for kw in ("date", "commencement")):
        return "DATE"
    if any(kw in lower_inner for kw in ("year", "month", "day", "duration", "term", "period")):
        return "DURATION"
    if any(kw in lower_inner for kw in ("acre", "acreage", "sqft", "square feet", "hectare")):
        return "AREA"
    if any(kw in lower_inner for kw in ("mw", "megawatt", "kw", "kilowatt", "gw", "gigawatt", "capacity", "size")):
        return "CAPACITY"
    if clean_inner.isdigit() or any(kw in lower_inner for kw in ("count", "number")):
        return "NUMBER"
    return "TEXT"


def extract_template_placeholders(text: str) -> list[PlaceholderVariable]:
    """Scan verbatim template text for fill-in brackets and return structured PlaceholderVariables."""
    results: list[PlaceholderVariable] = []
    seen = set()
    for m in PLACEHOLDER_REGEX.finditer(text):
        full_bracket = m.group(0)
        has_dollar = bool(m.group("currency"))
        inner = m.group("inner")
        dtype = infer_placeholder_type(full_bracket, has_dollar)
        if inner not in seen:
            seen.add(inner)
            results.append(
                PlaceholderVariable(
                    variable_name=inner,
                    bracket_text=full_bracket,
                    data_type_hint=dtype,
                )
            )
    return results


def normalize_template_canonical_path(
    arg1: DocumentZone | str | None = None,
    arg2: str | None = None,
    title: str | None = None,
    order: int = 1,
    *,
    zone: DocumentZone | str | None = None,
    label: str | None = None,
) -> str:
    """Derive canonical hierarchy path for a template AST node (e.g. 'PREAMBLE', 'RECITALS.A', 'BODY.1.1', 'EXHIBIT_A.1')."""
    target_zone = zone
    target_label = label

    if target_zone is None and target_label is None:
        if isinstance(arg1, DocumentZone):
            target_zone = arg1
            target_label = arg2 or ""
        elif isinstance(arg1, str) and arg1.upper() in DocumentZone.__members__:
            target_zone = DocumentZone(arg1.upper())
            target_label = arg2 or ""
        else:
            target_label = str(arg1 or "")
            context = str(arg2 or "")
            label_lower = target_label.lower()
            context_lower = context.lower()

            if "preamble" in label_lower or "preamble" in context_lower:
                return "PREAMBLE"
            if "recital" in label_lower or "recital" in context_lower:
                m = re.search(r"\b([A-Z])\b", target_label.upper())
                if m:
                    return f"RECITALS.{m.group(1)}"
                return f"RECITALS.{order}"
            if "exhibit" in label_lower or "exhibit" in context_lower or "schedule" in label_lower:
                token = clean_exhibit_token(target_label if "exhibit" in label_lower else f"{context} {target_label}")
                sec_m = re.search(r"(\d+(?:\.\d+)*)", target_label)
                sec_suffix = f".{sec_m.group(1)}" if sec_m else f".{order}"
                return f"EXHIBIT_{token}{sec_suffix}"

            target_zone = DocumentZone.BODY

    clean_label = (target_label or "").strip()
    if target_zone == DocumentZone.PREAMBLE or "preamble" in clean_label.lower():
        return "PREAMBLE"
    if target_zone == DocumentZone.RECITALS or "recital" in clean_label.lower():
        m = re.search(r"\b([A-Z])\b", clean_label.upper())
        if m:
            return f"RECITALS.{m.group(1)}"
        return f"RECITALS.{order}"
    if target_zone in (DocumentZone.EXHIBIT_OR_SCHEDULE, DocumentZone.EXHIBITS) or "exhibit" in clean_label.lower():
        token = clean_exhibit_token(clean_label)
        sec_m = re.search(r"(\d+(?:\.\d+)*)", clean_label)
        sec_suffix = f".{sec_m.group(1)}" if sec_m else f".{order}"
        return f"EXHIBIT_{token}{sec_suffix}"

    sec_num_match = re.search(r"(\d+(?:\.\d+)*)", clean_label)
    if sec_num_match:
        raw_num = sec_num_match.group(1)
        parts = [p.strip(".") for p in raw_num.split(".") if p.strip(".")]
        return "BODY." + ".".join(parts)

    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", clean_label).strip("_").upper()
    return f"BODY.{cleaned or order}"


def assemble_template_bundle(
    *,
    template_row: TemplateRegistryRow | None = None,
    raw_clauses: list[RawTemplateClause] | None = None,
    quarantine_span: PageSpan | None = None,
    pass1_structure: TemplateStructureIndex | None = None,
    pass2_extraction: TemplatePassExtraction | None = None,
    struct_index: TemplateStructureIndex | None = None,
    **kwargs,
) -> TemplateExtractionBundle:
    """Assemble raw extracted template clauses into canonical AST nodes, filtering execution signatures."""
    p1 = pass1_structure or struct_index

    # Resolve template_row
    if template_row is None:
        t_id = kwargs.get("template_id") or "tpl_unknown"
        t_name = kwargs.get("template_name") or kwargs.get("form_name") or (p1.form_name if p1 else "Template")
        state = kwargs.get("state_province") or kwargs.get("state") or (p1.state if p1 else "US")
        country = kwargs.get("country") or (p1.country if p1 else "US")
        tech = kwargs.get("energy_technology") or kwargs.get("technology") or (p1.energy_technology if p1 else EnergyTechnology.SOLAR)
        f_type = kwargs.get("form_type") or (p1.form_type if p1 else FormType.OTHER)
        v_year = str(kwargs.get("vintage_year") or kwargs.get("effective_vintage_year") or (p1.vintage_year if p1 else "2024"))
        rev = kwargs.get("revision", "v1")
        pdf_uri = kwargs.get("gcs_pdf_uri") or kwargs.get("pdf_storage_path", "")
        h_hash = kwargs.get("sha256_hash") or kwargs.get("pdf_sha256", "")
        p_count = int(kwargs.get("page_count") or kwargs.get("total_pages") or 1)
        created_by = kwargs.get("created_by", "system")

        template_row = TemplateRegistryRow(
            template_id=t_id,
            template_name=t_name,
            country=country,
            state_province=state,
            energy_technology=tech,
            form_type=f_type,
            vintage_year=v_year,
            revision=rev,
            gcs_pdf_uri=pdf_uri,
            sha256_hash=h_hash,
            page_count=p_count,
            clause_count=0,
            is_active=True,
            created_by=created_by,
        )

    # Resolve raw clauses list
    clauses_in: list[RawTemplateClause] = []
    if raw_clauses is not None:
        clauses_in = raw_clauses
    elif pass2_extraction is not None:
        clauses_in = pass2_extraction.clauses
    elif "clauses" in kwargs:
        raw_items = kwargs["clauses"]
        for item in raw_items:
            if isinstance(item, RawTemplateClause):
                clauses_in.append(item)
            elif isinstance(item, dict):
                clauses_in.append(RawTemplateClause(**item))

    # Resolve quarantine span
    if quarantine_span is None and p1 is not None:
        if p1.quarantine_signatures_span is not None:
            quarantine_span = p1.quarantine_signatures_span

    retained_nodes: list[TemplateClauseRow] = []
    quarantined_count = 0
    now_iso = utc_now_iso()
    has_exhibits = bool(p1 and p1.exhibits)

    for idx, raw in enumerate(clauses_in):
        raw_start = getattr(raw, "page_start", getattr(raw, "start_page", 1))
        raw_end = getattr(raw, "page_end", getattr(raw, "end_page", raw_start))
        raw_text = getattr(raw, "verbatim_text", getattr(raw, "section_text", ""))
        raw_label = getattr(raw, "clause_label", getattr(raw, "section_number", f"1.{idx + 1}"))
        raw_title = getattr(raw, "clause_title", None)
        raw_scheme = getattr(raw, "numbering_scheme", NumberingScheme.DECIMAL)
        raw_zone = getattr(raw, "document_zone", DocumentZone.BODY)

        # Check signature zone or quarantine
        is_signatures_zone = (
            raw_zone == DocumentZone.SIGNATURES
            or "SIGNATURE" in (getattr(raw, "canonical_path", None) or "").upper()
            or "SIGNATURE" in str(getattr(raw, "clause_label", None) or "").upper()
            or "SIGNATURE" in str(getattr(raw, "article_number", None) or "").upper()
        )

        in_quarantine_range = False
        if quarantine_span:
            q_start = getattr(quarantine_span, "page_start", getattr(quarantine_span, "start_page", 0))
            q_end = getattr(quarantine_span, "page_end", getattr(quarantine_span, "end_page", 0))
            if q_start > 0 and q_end >= q_start:
                if raw_start <= q_end and raw_end >= q_start:
                    in_quarantine_range = True

        execution_kw = any(
            kw in raw_text.upper()
            for kw in ("IN WITNESS WHEREOF", "NOTARY PUBLIC", "SS:", "STATE OF", "COUNTY OF", "MY COMMISSION EXPIRES")
        )

        if is_signatures_zone or in_quarantine_range or (in_quarantine_range and execution_kw):
            quarantined_count += 1
            logger.info("Quarantined signature/notary block at pages %d-%d: %s", raw_start, raw_end, raw_label)
            continue

        placeholders = list(getattr(raw, "placeholder_variables", getattr(raw, "placeholders", [])))
        if not placeholders:
            placeholders = extract_template_placeholders(raw_text)
        else:
            existing_vars = {p.variable_name for p in placeholders}
            extra = extract_template_placeholders(raw_text)
            for ep in extra:
                if ep.variable_name not in existing_vars:
                    placeholders.append(ep)
                    existing_vars.add(ep.variable_name)

        if getattr(raw, "canonical_path", None):
            canonical_path = raw.canonical_path
        else:
            canonical_path = normalize_template_canonical_path(
                zone=raw_zone,
                label=raw_label,
                title=raw_title,
                order=len(retained_nodes) + 1,
            )

        if "EXHIBIT" in canonical_path.upper() or raw_zone in (DocumentZone.EXHIBIT_OR_SCHEDULE, DocumentZone.EXHIBITS):
            has_exhibits = True

        depth = canonical_path.count(".")
        node_id = f"{template_row.template_id}#{canonical_path}"
        parent_node_id = None
        if "." in canonical_path:
            parent_path = canonical_path.rsplit(".", 1)[0]
            parent_node_id = f"{template_row.template_id}#{parent_path}"

        node = TemplateClauseRow(
            template_id=template_row.template_id,
            node_id=node_id,
            parent_node_id=parent_node_id,
            sibling_order=len(retained_nodes) + 1,
            document_zone=raw_zone,
            canonical_path=canonical_path,
            depth=depth,
            clause_label=raw_label,
            clause_title=raw_title,
            numbering_scheme=raw_scheme,
            verbatim_text=raw_text,
            placeholder_variables=placeholders,
            page_start=raw_start,
            page_end=raw_end,
            created_at=now_iso,
        )
        retained_nodes.append(node)

    template_row.clause_count = len(retained_nodes)
    template_row.has_exhibits = has_exhibits
    template_row.updated_at = now_iso

    logger.info(
        "Assembled template bundle for %s: %d nodes retained, %d quarantined",
        template_row.template_id,
        len(retained_nodes),
        quarantined_count,
    )

    return TemplateExtractionBundle(
        metadata=template_row,
        clauses=retained_nodes,
    )


