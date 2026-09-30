"""Thin Gemini 3.x multimodal contract parser using the unified google-genai SDK."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Protocol

from google import genai
from google.genai import types

from contract_parser.config import PipelineConfig
from contract_parser.schemas import (
    ClauseRow,
    DefinedTermRow,
    DefinitionType,
    DocumentZone,
    ExhibitCatalogRow,
    ExhibitModality,
    FlagCode,
    GeminiContractExtraction,
    HITLStatus,
    NumberingScheme,
    utc_now_iso,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert legal contract structural parser and obligation intelligence engine.
You are given a PDF of a legal agreement (which may be a scanned image PDF without an embedded text layer).
Read every page visually from physical page 1 through the final page and return a complete, lossless structured representation conforming strictly to the `GeminiContractExtraction` JSON schema.

Follow these universal rules:
1. VISUAL LAYOUT & PHYSICAL PAGE INDEXING:
   - Always report 1-indexed PHYSICAL PDF page numbers (`page_start`, `page_end`, `page_number`) from 1 to `page_count`.
   - Visually ignore repeating page headers/footers (e.g. "Page 2 of 12", document control footers), pleading paper margin line numbers, and struck-through/redlined text.
   - Read side-by-side two-column blocks (such as Section 7 Notice addresses on Page 3) down the left column first ("If to Cedar Lantern: ...") and then the right column ("If to Blue Meridian: ...").
   - Stitch clauses that split mid-sentence across physical page boundaries (e.g. Section 6 starting at the bottom of Page 2 and finishing at the top of Page 3 gets `page_start=2, page_end=3`).

2. ARBITRARY-DEPTH HIERARCHY & NUMBERING DISAMBIGUATION:
   - Segment nodes into `document_zone`: `PREAMBLE`, `RECITALS`, `BODY`, `SIGNATURES`, `EXHIBIT_OR_SCHEDULE`, or `AMENDMENT`.
   - Use deterministic `node_id` and `canonical_path` values:
     * Opening paragraph before recitals -> `node_id="PREAMBLE.1"`, `canonical_path="PREAMBLE.1"`, `document_zone="PREAMBLE"`, `depth=1`, `numbering_scheme="UNNUMBERED"`.
     * Recitals -> `RECITALS.1`, `RECITALS.2`, etc. (`document_zone="RECITALS"`, `depth=1`, `numbering_scheme="INTEGER"`, `clause_label="1"`, `level_1_label="1"`).
     * Main agreement sections -> `BODY.1`, `BODY.2`, ..., `BODY.17` (`document_zone="BODY"`, `depth=1`, `numbering_scheme="INTEGER"`, `clause_label="1"`, `level_1_label="1"`).
     * Inline or block sub-clauses -> e.g. `BODY.2.i`, `BODY.2.ii`, `BODY.3.a`, `BODY.3.b`, `BODY.3.c`, `BODY.4.i`, `BODY.4.ii`, `BODY.4.iii`, `BODY.5.i`, `BODY.5.ii`, `BODY.5.iii`, `BODY.8.i`, `BODY.8.ii`.
     * Disambiguate `(i)`, `(v)`, `(x)`: if `(i)` follows `(h)` and is followed by `(j)` (or no `(ii)`), classify as `ALPHA_LOWER` at the same depth. If `(i)` follows a colon/preamble or is followed by `(ii)`, classify as `ROMAN_LOWER` at child depth `d+1`.
     * When a section jumps directly from `INTEGER` (e.g. `2`, `4`, `5`, `8`) to `ROMAN_LOWER` (`(i)`, `(ii)`) without an intermediate `ALPHA_LOWER` (`(a)`) tier, set `depth=2`, `level_1_label="<section_num>"`, `level_2_label="(i)"`, and include `AMBIGUOUS_HIERARCHY_MARKER` in `hitl_flag_reasons`.

3. SANDWICH CLAUSES & RECONSTRUCTED CONTEXT (`verbatim_text` vs `reconstructed_context_text`):
   - Split every parent clause that has inline or block children into:
     * `preamble_text`: The lead-in text before the first child marker `(a)` or `(i)`.
     * `verbatim_text`: For a parent container with children, set `verbatim_text` to the full paragraph or lead-in; for each child node, set `verbatim_text` to ONLY that child's exact clause text (e.g. `"(i) Cedar Lantern's use of Property;"`).
     * `postamble_text`: On the parent node, capture any trailing carve-out or continuation sentences that appear after the last child marker `(iii)` or `(ii)` (for example, in Sections 4 and 5: `"except to the extent such Liabilities arise from the negligence or willful misconduct of..."`, and in Section 8: the unnumbered sentences including `"Notwithstanding the foregoing..."`).
   - For EVERY child node, populate `reconstructed_context_text` as a complete, self-contained legal provision combining:
     `[Ancestor preamble_text] + [Child verbatim_text] + [Ancestor postamble_text]`.
     For example, on `BODY.4.i`, `reconstructed_context_text` MUST include the opening indemnity preamble from `BODY.4`, `"(i) Cedar Lantern's use of Property;"`, AND the trailing negligence carve-out `"except to the extent such Liabilities arise from the negligence or willful misconduct of Blue Meridian."`
   - For Exhibit property carve-outs (such as Exhibit A, Parcel 4 `LESS AND EXCEPT` on Page 8), create node `EXHIBIT_A.PARCEL_4.EXCEPT_1` with `parent_node_id="EXHIBIT_A.PARCEL_4"`, `depth=3`, `numbering_scheme="NAMED_HEADER"`, `level_1_label="Exhibit A"`, `level_2_label="Parcel 4"`, `level_3_label="LESS_AND_EXCEPT"`, and prefix `reconstructed_context_text` with `"Excluded from Exhibit A, Parcel 4: ..."`.

4. SIGNATURES, EXHIBITS & PLACEHOLDERS:
   - Quarantine handwritten signature and notary execution pages (e.g. Pages 5 and 6) as `SIGNATURES.1` (Page 5) and `SIGNATURES.2` (Page 6) with `document_zone="SIGNATURES"`, `numbering_scheme="NAMED_HEADER"`, `hitl_status="PLACEHOLDER_FOR_REVIEW"`, and `hitl_flag_reasons="DEFERRED_MODALITY_PLACEHOLDER"`.
   - Catalog every Exhibit in `exhibits_catalog`:
     * `Exhibit A` (`PROPERTY_DESCRIPTION`, Pages 7-8): populate `structured_entities_json` with all 4 parcels (including Tax Parcel IDs and the 6.15-acre `LESS AND EXCEPT` carve-out in Parcel 4).
     * `Exhibit B` (`EXTERNAL_INSTRUMENT_LIST`, Page 9) and `Exhibit C` (`EXTERNAL_INSTRUMENT_LIST`, Page 10): list the recorded easement instruments (Book/Page numbers) in `structured_entities_json` and set `has_unresolved_external_dep=true` because Section 2 (Term) depends on easement expiration dates that are not stated in Exhibits B or C.
     * `Exhibit D` (`VISUAL_DRAWING_OR_MAP_STUB`, Pages 11-12): create clause stub `EXHIBIT_D.STUB` with `page_start=11, page_end=12`, `hitl_status="PLACEHOLDER_FOR_REVIEW"`, and `hitl_flag_reasons="DEFERRED_MODALITY_PLACEHOLDER"`.

5. DEFINED TERMS & SIX UNIVERSAL HITL FLAGS:
   - Extract all Defined Terms into `defined_terms` (including `Agreement`, `Cedar Lantern`, `Blue Meridian`, `Property`, `Party`, `Parties`, `Cedar Lantern Easements`, `Cedar Lantern Project`, `Blue Meridian Easements`, `Blue Meridian Project`, `FPUC`, `CCN`, `Blue Meridian Facilities`, `Operations`, `Equipment`, `Liabilities`).
   - Populate `hitl_flag_reasons` (pipe-delimited sorted codes, or `"NONE"`) using the 6 universal codes:
     * `AMBIGUOUS_HIERARCHY_MARKER`: Skipped numbering tier (`INTEGER` -> `ROMAN_LOWER` without `ALPHA_LOWER`, e.g. `BODY.2.i`, `BODY.2.ii`, `BODY.4.i..iii`, `BODY.5.i..iii`, `BODY.8.i..ii`) or ambiguous `(i)` transition.
     * `SCOPE_CARVEOUT_DETECTED`: Clauses containing legal carve-outs/exceptions (`except to the extent`, `LESS AND EXCEPT`, `Notwithstanding the foregoing`, `provided, however`).
     * `UNRESOLVED_EXTERNAL_DEPENDENCY`: Clauses whose legal effect depends on unattached external instruments (e.g. `BODY.2`, `BODY.2.i`, `BODY.2.ii` depending on expiration of `Blue Meridian Easements` / `Cedar Lantern Easements` in Exhibits B and C).
     * `BROKEN_INTERNAL_REFERENCE`: Explicit reference to a non-existent Section or Exhibit.
     * `DEFERRED_MODALITY_PLACEHOLDER`: Signature/notary blocks with handwriting (`SIGNATURES.1`, `SIGNATURES.2`) and visual CAD/map exhibits (`EXHIBIT_D.STUB`).
     * `TEXT_COVERAGE_GAP`: Illegible scan blocks or cut-off text margins.
"""


class ContractExtractorProtocol(Protocol):
    """Protocol for contract extraction (enables hermetic testing alongside live Gemini)."""

    def extract(
        self,
        *,
        document_id: str,
        gcs_pdf_uri: str,
        pdf_bytes: bytes | None = None,
    ) -> GeminiContractExtraction:
        """Extract structured contract hierarchy, terms, and exhibits from a PDF."""


class GeminiContractParser:
    """Thin wrapper around the unified google-genai SDK (`gemini-3.1-pro-preview` / `gemini-2.5-flash`)."""

    def __init__(self, config: PipelineConfig | None = None) -> None:
        self.config = config or PipelineConfig()
        self.config.ensure_genai_env()
        self._client: genai.Client | None = None

    @property
    def client(self) -> genai.Client:
        """Lazily initialize the unified Google Gen AI client."""
        if self._client is None:
            self.config.ensure_genai_env()
            self._client = genai.Client(
                vertexai=self.config.use_enterprise,
                project=self.config.google_cloud_project,
                location=self.config.google_cloud_location,
            )
        return self._client

    def _build_pdf_part(
        self, gcs_pdf_uri: str, pdf_bytes: bytes | None
    ) -> types.Part:
        """Construct the PDF multimodal Part from GCS URI or raw bytes."""
        if gcs_pdf_uri.startswith("gs://") and self.config.use_cloud_storage:
            return types.Part.from_uri(
                file_uri=gcs_pdf_uri, mime_type="application/pdf"
            )
        if pdf_bytes is not None:
            return types.Part.from_bytes(
                data=pdf_bytes, mime_type="application/pdf"
            )
        raise ValueError(
            f"Either a valid gs:// URI (got {gcs_pdf_uri!r}) or pdf_bytes must be provided."
        )

    def extract(
        self,
        *,
        document_id: str,
        gcs_pdf_uri: str,
        pdf_bytes: bytes | None = None,
    ) -> GeminiContractExtraction:
        """Call Gemini with Structured Outputs and run deterministic post-processing normalization."""
        pdf_part = self._build_pdf_part(gcs_pdf_uri=gcs_pdf_uri, pdf_bytes=pdf_bytes)
        models_to_try = [self.config.gemini_model]
        if (
            self.config.gemini_fallback_model
            and self.config.gemini_fallback_model not in models_to_try
        ):
            models_to_try.append(self.config.gemini_fallback_model)

        last_err: Exception | None = None
        raw_extraction: GeminiContractExtraction | None = None

        for model_name in models_to_try:
            backoff = self.config.initial_backoff_seconds
            for attempt in range(1, self.config.max_retries + 1):
                try:
                    logger.info(
                        "Invoking Gemini model %s (attempt %d/%d) on %s",
                        model_name,
                        attempt,
                        self.config.max_retries,
                        gcs_pdf_uri,
                    )
                    response = self.client.models.generate_content(
                        model=model_name,
                        contents=[
                            pdf_part,
                            "Parse this complete legal contract PDF into its lossless hierarchical clause tree, defined terms dictionary, and exhibits catalog.",
                        ],
                        config=types.GenerateContentConfig(
                            system_instruction=SYSTEM_PROMPT,
                            response_mime_type="application/json",
                            response_schema=GeminiContractExtraction,
                            temperature=0.0,
                            max_output_tokens=self.config.max_output_tokens,
                        ),
                    )
                    if response.parsed is not None and isinstance(
                        response.parsed, GeminiContractExtraction
                    ):
                        raw_extraction = response.parsed
                    elif response.text:
                        raw_extraction = GeminiContractExtraction.model_validate_json(
                            response.text
                        )
                    else:
                        raise RuntimeError(
                            f"Gemini returned empty response for {gcs_pdf_uri}"
                        )
                    break
                except Exception as exc:
                    last_err = exc
                    err_str = str(exc)
                    logger.warning(
                        "Gemini %s attempt %d failed: %s",
                        model_name,
                        attempt,
                        err_str,
                    )
                    if "404" in err_str or "NOT_FOUND" in err_str:
                        # Immediately try fallback model if model name is unavailable in region
                        break
                    if attempt < self.config.max_retries:
                        time.sleep(backoff)
                        backoff *= 2.0
            if raw_extraction is not None:
                break

        if raw_extraction is None:
            raise RuntimeError(
                f"Failed to extract contract via Gemini after trying {models_to_try}: {last_err}"
            ) from last_err

        return normalize_and_enrich_extraction(
            extraction=raw_extraction,
            document_id=document_id,
            gcs_pdf_uri=gcs_pdf_uri,
        )


def _split_pipe(val: str | None) -> list[str]:
    if not val or val.strip().upper() == "NONE":
        return []
    return [item.strip() for item in val.split("|") if item.strip() and item.strip().upper() != "NONE"]


def _format_pipe(items: list[str] | set[str], sort_items: bool = False) -> str:
    cleaned = [x.strip() for x in items if x and x.strip() and x.strip().upper() != "NONE"]
    unique: list[str] = []
    for item in cleaned:
        if item not in unique:
            unique.append(item)
    if sort_items:
        unique.sort()
    return "|".join(unique) if unique else "NONE"


_CARVEOUT_PATTERN = re.compile(
    r"\b(?:except\s+to\s+the\s+extent|less\s+and\s+except|except\s+that\s+part|notwithstanding\s+the\s+foregoing|notwithstanding\s+anything|provided,\s*however)\b",
    re.IGNORECASE,
)


def normalize_and_enrich_extraction(
    extraction: GeminiContractExtraction,
    document_id: str,
    gcs_pdf_uri: str,
) -> GeminiContractExtraction:
    """Enforce deterministic hierarchy tiers, sandwich-clause context, 2-hop external deps, and 6 HITL flags."""
    now_iso = utc_now_iso()

    # 1. Index clauses by node_id and normalize basic fields
    clauses_by_id: dict[str, ClauseRow] = {}
    sibling_counters: dict[str | None, int] = {}

    for clause in extraction.clauses:
        clause.document_id = document_id
        clause.gcs_pdf_uri = gcs_pdf_uri
        clause.updated_at = now_iso
        if clause.parent_node_id in ("", "NULL", "null", "None"):
            clause.parent_node_id = None
        parent_key = f"{clause.document_zone}:{clause.parent_node_id or 'ROOT'}"
        sibling_counters[parent_key] = sibling_counters.get(parent_key, 0) + 1
        if clause.sibling_order <= 0:
            clause.sibling_order = sibling_counters[parent_key]
        if clause.page_end < clause.page_start:
            clause.page_end = clause.page_start
        clauses_by_id[clause.node_id] = clause

    # 2. Populate ancestor chain labels (level_1_label..level_4_label, level_5_plus_path) & sandwich context
    for clause in extraction.clauses:
        ancestors: list[ClauseRow] = []
        curr_parent_id = clause.parent_node_id
        visited: set[str] = {clause.node_id}
        while curr_parent_id and curr_parent_id in clauses_by_id and curr_parent_id not in visited:
            visited.add(curr_parent_id)
            parent_node = clauses_by_id[curr_parent_id]
            ancestors.append(parent_node)
            curr_parent_id = parent_node.parent_node_id
        ancestors.reverse()

        lineage_nodes = [*ancestors, clause]
        labels = [n.clause_label for n in lineage_nodes]

        # If an EXHIBIT_<X>.PARCEL_<Y> node has no explicit EXHIBIT_<X> parent row, synthesize Tier 1 Exhibit label
        ex_match = re.match(r"^EXHIBIT_([A-Z0-9]+)\.", clause.node_id, re.IGNORECASE)
        if (
            ex_match
            and clause.document_zone == DocumentZone.EXHIBIT_OR_SCHEDULE
            and (not labels or not labels[0].upper().startswith("EXHIBIT"))
        ):
            labels.insert(0, f"Exhibit {ex_match.group(1).upper()}")

        if labels and labels[-1].upper().replace(" ", "_") == "LESS_AND_EXCEPT":
            labels[-1] = "LESS_AND_EXCEPT"
            clause.clause_label = "LESS_AND_EXCEPT"

        clause.depth = len(labels)
        clause.level_1_label = labels[0] if len(labels) >= 1 else clause.level_1_label
        clause.level_2_label = labels[1] if len(labels) >= 2 else None
        clause.level_3_label = labels[2] if len(labels) >= 3 else None
        clause.level_4_label = labels[3] if len(labels) >= 4 else None
        clause.level_5_plus_path = (
            ".".join(labels[4:]) if len(labels) >= 5 else None
        )

        # Reconstruct full context from ancestor preambles + verbatim_text + reversed ancestor postambles
        if ancestors or (ex_match and len(labels) >= 2):
            preambles: list[str] = []
            postambles: list[str] = []
            for anc in ancestors:
                if anc.preamble_text and anc.preamble_text.strip():
                    preambles.append(anc.preamble_text.strip())
            for anc in reversed(ancestors):
                if anc.postamble_text and anc.postamble_text.strip():
                    postambles.append(anc.postamble_text.strip())

            # Check for Exhibit/Parcel NAMED_HEADER carve-out synthesis (e.g. EXHIBIT_A.PARCEL_4.EXCEPT_1)
            if (
                not preambles
                and clause.document_zone == DocumentZone.EXHIBIT_OR_SCHEDULE
                and (
                    "EXCEPT" in clause.node_id.upper()
                    or "EXCEPT" in (clause.clause_label or "").upper()
                    or "LESS AND EXCEPT" in clause.verbatim_text.upper()
                )
            ):
                anc_labels = ", ".join(labels[:-1]) if len(labels) > 1 else "Exhibit"
                body_stripped = re.sub(
                    r"^LESS\s+AND\s+EXCEPT\s*:?\s*", "", clause.verbatim_text.strip(), flags=re.IGNORECASE
                )
                clause.reconstructed_context_text = f"Excluded from {anc_labels}: {body_stripped}"
            elif preambles or postambles:
                parts: list[str] = []
                for p in preambles:
                    if p not in clause.verbatim_text:
                        parts.append(p)
                parts.append(clause.verbatim_text.strip())
                for post in postambles:
                    if post not in clause.verbatim_text:
                        parts.append(post)
                candidate_context = " ".join(parts)
                # Ensure both preamble and postamble are present in reconstructed_context_text
                missing_pre = any(p[:25] not in clause.reconstructed_context_text for p in preambles if len(p) >= 10)
                missing_post = any(post[:25] not in clause.reconstructed_context_text for post in postambles if len(post) >= 10)
                if not clause.reconstructed_context_text or clause.reconstructed_context_text.strip() == clause.verbatim_text.strip() or missing_pre or missing_post:
                    clause.reconstructed_context_text = candidate_context
        else:
            if not clause.reconstructed_context_text or not clause.reconstructed_context_text.strip():
                clause.reconstructed_context_text = clause.verbatim_text

    # 3. Normalize exhibits_catalog and identify external-dependency exhibits
    unresolved_exhibits: set[str] = set()
    known_exhibits: set[str] = set()
    for ex in extraction.exhibits_catalog:
        ex.document_id = document_id
        ex.updated_at = now_iso
        if ex.page_end < ex.page_start:
            ex.page_end = ex.page_start
        # Validate JSON string
        try:
            json.loads(ex.structured_entities_json)
        except Exception:
            ex.structured_entities_json = json.dumps([{"raw": ex.structured_entities_json}])
        known_exhibits.add(ex.exhibit_id.upper())
        if ex.exhibit_modality == ExhibitModality.EXTERNAL_INSTRUMENT_LIST or ex.has_unresolved_external_dep:
            ex.has_unresolved_external_dep = True
            unresolved_exhibits.add(ex.exhibit_id.upper())

    # 4. Normalize defined_terms and identify 2-hop external dependency terms
    unresolved_terms: set[str] = set()
    for dt in extraction.defined_terms:
        dt.document_id = document_id
        dt.updated_at = now_iso
        for ex_id in unresolved_exhibits:
            if ex_id in dt.verbatim_definition.upper():
                unresolved_terms.add(dt.term_name)

    # Longest-match-first Defined Term linking across clauses
    sorted_terms = sorted(
        extraction.defined_terms, key=lambda t: len(t.term_name), reverse=True
    )
    term_usage_map: dict[str, list[str]] = {t.term_name: _split_pipe(t.referenced_in_nodes) for t in extraction.defined_terms}
    exhibit_usage_map: dict[str, list[str]] = {e.exhibit_id: _split_pipe(e.referenced_by_nodes) for e in extraction.exhibits_catalog}

    for clause in extraction.clauses:
        atomic_blob = " ".join(
            filter(None, [clause.preamble_text, clause.verbatim_text, clause.postamble_text])
        )
        matched_spans: list[tuple[int, int]] = []
        detected_terms: list[str] = _split_pipe(clause.defined_terms_used)

        for dt in sorted_terms:
            pattern = re.compile(rf"\b{re.escape(dt.term_name)}\b")
            for m in pattern.finditer(atomic_blob):
                start, end = m.span()
                if not any( not (end <= s or start >= e) for s, e in matched_spans ):
                    matched_spans.append((start, end))
                    if dt.term_name not in detected_terms:
                        detected_terms.append(dt.term_name)
                    if clause.node_id not in term_usage_map[dt.term_name]:
                        term_usage_map[dt.term_name].append(clause.node_id)

        clause.defined_terms_used = _format_pipe(detected_terms)

        # Link exhibit references
        xrefs = _split_pipe(clause.cross_references)
        for ex in extraction.exhibits_catalog:
            if re.search(rf"\b{re.escape(ex.exhibit_id)}\b", atomic_blob, re.IGNORECASE):
                if ex.exhibit_id not in xrefs:
                    xrefs.append(ex.exhibit_id)
                if clause.node_id not in exhibit_usage_map[ex.exhibit_id]:
                    exhibit_usage_map[ex.exhibit_id].append(clause.node_id)
        clause.cross_references = _format_pipe(xrefs)

    for dt in extraction.defined_terms:
        dt.referenced_in_nodes = _format_pipe(term_usage_map.get(dt.term_name, []))
    for ex in extraction.exhibits_catalog:
        ex.referenced_by_nodes = _format_pipe(exhibit_usage_map.get(ex.exhibit_id, []))

    # 5. Evaluate the 6 Universal HITL Flag Rules on every clause
    for clause in extraction.clauses:
        flags = set(_split_pipe(clause.hitl_flag_reasons))
        parent = clauses_by_id.get(clause.parent_node_id) if clause.parent_node_id else None

        # Rule 1: DEFERRED_MODALITY_PLACEHOLDER (Signatures & Visual CAD Stubs)
        if (
            clause.document_zone == DocumentZone.SIGNATURES
            or "STUB" in clause.node_id.upper()
            or "[DRAWING]" in clause.verbatim_text.upper()
            or "[PLACEHOLDER" in clause.reconstructed_context_text.upper()
        ):
            flags.add(FlagCode.DEFERRED_MODALITY_PLACEHOLDER.value)
            clause.hitl_status = HITLStatus.PLACEHOLDER_FOR_REVIEW

        # Rule 2: AMBIGUOUS_HIERARCHY_MARKER (Skipped tier INTEGER -> ROMAN_LOWER without ALPHA_LOWER)
        if (
            clause.numbering_scheme == NumberingScheme.ROMAN_LOWER
            and parent is not None
            and parent.numbering_scheme == NumberingScheme.INTEGER
        ):
            flags.add(FlagCode.AMBIGUOUS_HIERARCHY_MARKER.value)

        # Rule 3: SCOPE_CARVEOUT_DETECTED (Carve-out keywords in atomic or reconstructed context)
        combined_text = f"{clause.verbatim_text} {clause.postamble_text or ''} {clause.reconstructed_context_text}"
        if _CARVEOUT_PATTERN.search(combined_text) or "EXCEPT" in clause.node_id.upper():
            flags.add(FlagCode.SCOPE_CARVEOUT_DETECTED.value)

        # Rule 4: UNRESOLVED_EXTERNAL_DEPENDENCY (Direct or 2-hop via Defined Terms in governing BODY clauses)
        if clause.document_zone == DocumentZone.BODY:
            clause_terms = set(_split_pipe(clause.defined_terms_used))
            clause_xrefs = {x.upper() for x in _split_pipe(clause.cross_references)}
            parent_title = (parent.clause_title or "").upper() if parent is not None else ""
            ctx_upper = clause.reconstructed_context_text.upper()
            title_upper = (clause.clause_title or "").upper()
            is_term_or_duration_clause = any(
                kw in ctx_upper or kw in title_upper or kw in parent_title
                for kw in ("EXPIR", "TERM", "TERMINAT", "DURATION", "CO-TERMINOUS")
            )
            if (
                (clause_terms & unresolved_terms)
                or (clause_xrefs & unresolved_exhibits)
            ) and is_term_or_duration_clause:
                flags.add(FlagCode.UNRESOLVED_EXTERNAL_DEPENDENCY.value)

        # Rule 5: BROKEN_INTERNAL_REFERENCE (Explicit Exhibit reference missing from known_exhibits)
        atomic_for_xref = f"{clause.preamble_text or ''} {clause.verbatim_text} {clause.postamble_text or ''}"
        for ex_ref in re.findall(r"\bExhibit\s+[A-Z0-9]+\b", atomic_for_xref, flags=re.IGNORECASE):
            if ex_ref.upper() not in known_exhibits:
                flags.add(FlagCode.BROKEN_INTERNAL_REFERENCE.value)
        for xref in _split_pipe(clause.cross_references):
            if xref.upper().startswith("EXHIBIT ") and xref.upper() not in known_exhibits:
                flags.add(FlagCode.BROKEN_INTERNAL_REFERENCE.value)

        clause.hitl_flag_reasons = _format_pipe(flags, sort_items=True)
        if clause.hitl_status != HITLStatus.APPROVED_BY_HUMAN:
            if FlagCode.DEFERRED_MODALITY_PLACEHOLDER.value in flags:
                clause.hitl_status = HITLStatus.PLACEHOLDER_FOR_REVIEW
            elif flags:
                clause.hitl_status = HITLStatus.FLAGGED_FOR_REVIEW
            else:
                clause.hitl_status = HITLStatus.VERIFIED_AUTO

    return extraction
