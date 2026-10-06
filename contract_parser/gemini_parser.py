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
    ConstraintCategory,
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

5. DEFINED TERMS & UNIVERSAL HITL FLAGS:
   - Extract all Defined Terms into `defined_terms` (including `Agreement`, `Cedar Lantern`, `Blue Meridian`, `Property`, `Party`, `Parties`, `Cedar Lantern Easements`, `Cedar Lantern Project`, `Blue Meridian Easements`, `Blue Meridian Project`, `FPUC`, `CCN`, `Blue Meridian Facilities`, `Operations`, `Equipment`, `Liabilities`).
   - Populate `hitl_flag_reasons` (pipe-delimited sorted codes, or `"NONE"`) using the universal codes:
     * `AMBIGUOUS_HIERARCHY_MARKER`: Skipped numbering tier (`INTEGER` -> `ROMAN_LOWER` without `ALPHA_LOWER`, e.g. `BODY.2.i`, `BODY.2.ii`, `BODY.4.i..iii`, `BODY.5.i..iii`, `BODY.8.i..ii`) or ambiguous `(i)` transition.
     * `SCOPE_CARVEOUT_DETECTED`: Clauses containing legal carve-outs/exceptions (`except to the extent`, `LESS AND EXCEPT`, `Notwithstanding the foregoing`, `provided, however`).
     * `UNRESOLVED_EXTERNAL_DEPENDENCY`: Clauses whose legal effect depends on unattached external instruments (e.g. `BODY.2`, `BODY.2.i`, `BODY.2.ii` depending on expiration of `Blue Meridian Easements` / `Cedar Lantern Easements` in Exhibits B and C).
     * `BROKEN_INTERNAL_REFERENCE`: Explicit reference to a non-existent Section or Exhibit.
     * `DEFERRED_MODALITY_PLACEHOLDER`: Signature/notary blocks with handwriting (`SIGNATURES.1`, `SIGNATURES.2`) and visual CAD/map exhibits (`EXHIBIT_D.STUB`).
     * `TEXT_COVERAGE_GAP`: Illegible scan blocks or cut-off text margins.
     * `LANDOWNER_SPECIAL_CONDITION`: Any clause containing one or more physical or operational Landowner Special Conditions / Site Constraints.

6. LANDOWNER "SPECIAL CONDITIONS" & PHYSICAL SITE CONSTRAINTS (`special_conditions`):
   - Extract every bespoke physical or operational landowner restriction, special condition, or site constraint into `special_conditions` (1 `SpecialConditionRow` per distinct constraint).
   - Always attach each `SpecialConditionRow` to the MOST SPECIFIC LEAF `node_id` where the constraint appears (do not duplicate the same condition on both a parent container clause and its child sub-clause).
   - If a single clause contains multiple distinct physical constraints (e.g., a 150-foot barn setback, a locked gate rule, and a seasonal hunting blackout), decompose them into separate `SpecialConditionRow` entries sharing that `node_id`.
   - Classify each constraint using one of the 7 `ConstraintCategory` values:
     * `TREE_VEGETATION_PROTECTION` (trees, groves, orchards, windbreaks, timber)
     * `STRUCTURE_BARN_WELL_SETBACK` (setbacks/buffers around barns, sheds, water wells, residences, fences, septic)
     * `ACCESS_ROAD_GATE_PROTOCOL` (locked gates, designated entry roads, culvert weight limits, speed limits, advance notice)
     * `LIVESTOCK_AGRICULTURE` (cattle, livestock, grazing, drainage tiles, irrigation pivots, crops)
     * `TIMING_NOISE_HUNTING_BLACKOUT` (hunting season blackouts, harvest windows, work hours, noise/blasting curfews)
     * `FINANCIAL_PENALTY_LIQUIDATED_DAMAGES` (explicit dollar penalties or liquidated damages for site violations)
     * `OTHER_CUSTOM_RIDER` (other bespoke operational landowner rules)
   - Strictly separate legal liability/indemnification carve-outs (`SCOPE_CARVEOUT_DETECTED`, such as "except for negligence or willful misconduct") and pure surveyor metes-and-bounds bearings from operational/physical site constraints.

7. PORTFOLIO COUNTERPARTY EXTRACTION (`grantor_landowner_name` & `grantee_entity_name` — CR-2):
   - Populate `grantor_landowner_name` with the full legal name of the Landowner / Grantor / Property Owner / Accommodating Party stated in the PREAMBLE, RECITALS, or SIGNATURES.
   - Populate `grantee_entity_name` with the full legal name of the Developer SPV / Grantee / Lessee / Project Company stated in the PREAMBLE, RECITALS, or SIGNATURES.
"""


class ContractExtractorProtocol(Protocol):
    """Protocol for contract extraction (enables hermetic testing alongside live Gemini)."""

    def extract(
        self,
        *,
        document_id: str,
        gcs_pdf_uri: str,
        pdf_bytes: bytes | None = None,
        project_id: str = "prj_cedar_lantern_wind",
        landowner_id: str | None = None,
    ) -> GeminiContractExtraction:
        """Extract structured contract hierarchy, terms, and exhibits from a PDF."""


class GeminiContractParser:
    """Thin wrapper around the unified google-genai SDK (`gemini-3.1-pro-preview` / `gemini-3.8-flash`)."""

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
        project_id: str = "prj_cedar_lantern_wind",
        landowner_id: str | None = None,
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
                            "Parse this complete legal contract PDF into its lossless hierarchical clause tree, defined terms dictionary, exhibits catalog, normalized landowner special conditions / site constraints, and counterparty names.",
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
            project_id=project_id,
            landowner_id=landowner_id,
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

# CR-1 Signal 1: Operational restriction, prohibition, buffer, gate/notice protocol, blackout, or penalty
_PHYSICAL_CONSTRAINT_RESTRICTION_PATTERN = re.compile(
    r"\b(?:shall\s+not|must\s+not|may\s+not|will\s+not|prohibit(?:ed|s)?|restrict(?:ed|ion)?|setbacks?|buffers?|no[-\s]?build|no[-\s]?disturbance|no\s+closer\s+than|minimum\s+distance|(?:keep|kept)\s+(?:\S+\s+){1,6}?locked|must\s+be\s+locked|shall\s+be\s+locked|padlock(?:ed)?|advance\s+notice|prior\s+(?:written\s+)?notice|blackouts?|curfews?|hunting\s+season|liquidated\s+damages|penalty\s+of)\b",
    re.IGNORECASE,
)

# CR-1 Signal 2: Physical site feature, structure, vegetation, livestock, or distance/weight/dollar metric
_PHYSICAL_CONSTRAINT_ASSET_OR_METRIC_PATTERN = re.compile(
    r"(?:\b(?:\d+(?:,\d{3})*(?:\.\d+)?[-\s]*(?:feet|foot|ft\.?|yards?|meters?|acres?|tons?|hours?|days?)|trees?|groves?|orchards?|timber|windbreaks?|barns?|sheds?|water\s+wells?|wells?|residences?|homesteads?|septic|gates?|culverts?|cattle|livestock|grazing|drainage\s+tiles?|irrigation|hunting|harvest|blasting|trenching)\b|\$\s*\d+(?:,\d{3})*(?:\.\d{2})?\b)",
    re.IGNORECASE,
)

_METRIC_EXTRACT_PATTERN = re.compile(
    r"\b\d+(?:,\d{3})*(?:\.\d+)?[-\s]*(?:feet|foot|ft\.?|yards?|meters?|acres?|tons?)\b",
    re.IGNORECASE,
)
_TEMPORAL_EXTRACT_PATTERN = re.compile(
    r"\b(?:\d+[-\s]*(?:hours?|days?)(?:\s+(?:advance|prior)(?:\s+written)?\s+notice)?|(?:january|february|march|april|may|june|july|august|september|october|november|december|nov\.?|dec\.?)\s+\d{1,2}(?:\s*(?:through|to|-|–)\s*(?:january|february|march|april|may|june|july|august|september|october|november|december|nov\.?|dec\.?)?\s*\d{1,2})?|hunting\s+season|harvest\s+season)\b",
    re.IGNORECASE,
)
_PENALTY_EXTRACT_PATTERN = re.compile(
    r"\$\s*\d+(?:,\d{3})*(?:\.\d{2})?(?:\s*(?:per\s+\w+|liquidated\s+damages|penalty))?",
    re.IGNORECASE,
)
_ASSET_EXTRACT_PATTERN = re.compile(
    r"\b(?:(?:northern|southern|eastern|western|main|historic|existing|homestead|red|oak|pecan)\s+)*(?:trees?|groves?|orchards?|timber|windbreaks?|barns?|sheds?|water\s+wells?|wells?|residences?|homesteads?|septic|gates?(?:\s*#?\s*[A-Z0-9]+)?|culverts?|cattle|livestock|grazing\s+area|drainage\s+tiles?|irrigation\s+pivot|creek\s+crossing)\b",
    re.IGNORECASE,
)

_CATEGORY_PATTERNS: tuple[tuple[ConstraintCategory, re.Pattern[str]], ...] = (
    (
        ConstraintCategory.TREE_VEGETATION_PROTECTION,
        re.compile(r"\b(?:trees?|groves?|orchards?|timber|windbreaks?|vegetation|pecan|oak)\b", re.IGNORECASE),
    ),
    (
        ConstraintCategory.STRUCTURE_BARN_WELL_SETBACK,
        re.compile(
            r"\b(?:barns?|sheds?|water\s+wells?|wells?|residences?|homesteads?|septic|setbacks?|buffers?|no[-\s]?build|no[-\s]?disturbance)\b",
            re.IGNORECASE,
        ),
    ),
    (
        ConstraintCategory.ACCESS_ROAD_GATE_PROTOCOL,
        re.compile(
            r"\b(?:gates?|padlocks?|padlocked|locked|culverts?|access\s+roads?|advance\s+notice|prior\s+(?:written\s+)?notice)\b",
            re.IGNORECASE,
        ),
    ),
    (
        ConstraintCategory.LIVESTOCK_AGRICULTURE,
        re.compile(r"\b(?:cattle|livestock|grazing|drainage\s+tiles?|irrigation|crops?)\b", re.IGNORECASE),
    ),
    (
        ConstraintCategory.TIMING_NOISE_HUNTING_BLACKOUT,
        re.compile(r"\b(?:hunting|harvest|blackouts?|curfews?|blasting|night\s+hours|seasons?)\b", re.IGNORECASE),
    ),
    (
        ConstraintCategory.FINANCIAL_PENALTY_LIQUIDATED_DAMAGES,
        re.compile(r"(?:\b(?:liquidated\s+damages|penalty)\b|\$\s*\d+)", re.IGNORECASE),
    ),
)


def _infer_constraint_category(text: str) -> ConstraintCategory:
    """Infer the 7-category operational ConstraintCategory from clause text using word-boundary patterns."""
    for category, pattern in _CATEGORY_PATTERNS:
        if pattern.search(text):
            return category
    return ConstraintCategory.OTHER_CUSTOM_RIDER


def _get_descendant_leaves(
    node_id: str, children_by_parent: dict[str, list[ClauseRow]]
) -> list[ClauseRow]:
    """Return all leaf ClauseRow descendants under node_id."""
    leaves: list[ClauseRow] = []
    stack = list(children_by_parent.get(node_id, []))
    visited_desc: set[str] = set()
    while stack:
        curr = stack.pop(0)
        if curr.node_id in visited_desc:
            continue
        visited_desc.add(curr.node_id)
        kids = children_by_parent.get(curr.node_id, [])
        if kids:
            stack.extend(kids)
        else:
            leaves.append(curr)
    return leaves


def _synthesize_fallback_condition(
    clause: ClauseRow, document_id: str, now_iso: str
) -> SpecialConditionRow | None:
    """Synthesize a fallback SpecialConditionRow when both CR-1 regex signals match on a leaf clause."""
    text_to_scan = clause.verbatim_text.strip()
    context_to_scan = (clause.reconstructed_context_text or text_to_scan).strip()
    if not (
        _PHYSICAL_CONSTRAINT_RESTRICTION_PATTERN.search(context_to_scan)
        and _PHYSICAL_CONSTRAINT_ASSET_OR_METRIC_PATTERN.search(text_to_scan)
    ):
        return None
    metric_match = _METRIC_EXTRACT_PATTERN.search(text_to_scan)
    temporal_match = _TEMPORAL_EXTRACT_PATTERN.search(text_to_scan)
    penalty_match = _PENALTY_EXTRACT_PATTERN.search(text_to_scan)
    asset_match = _ASSET_EXTRACT_PATTERN.search(text_to_scan)
    target_asset = (
        asset_match.group(0).strip()
        if asset_match
        else (clause.clause_title or f"Site Constraint ({clause.canonical_path})")
    )
    return SpecialConditionRow(
        condition_id="",
        document_id=document_id,
        node_id=clause.node_id,
        canonical_path=clause.canonical_path,
        constraint_category=_infer_constraint_category(text_to_scan),
        target_asset_or_area=target_asset,
        quantitative_metric=metric_match.group(0).strip() if metric_match else None,
        temporal_restriction=temporal_match.group(0).strip() if temporal_match else None,
        penalty_or_consequence=penalty_match.group(0).strip() if penalty_match else None,
        actionable_obligation_summary=text_to_scan[:240],
        verbatim_excerpt=text_to_scan,
        page_number=max(1, clause.page_start),
        extraction_confidence=0.75,
        hitl_status=HITLStatus.FLAGGED_FOR_REVIEW,
        updated_at=now_iso,
        reviewed_by=None,
    )


_CORPORATE_SUFFIX_RE = re.compile(
    r"(?:[\s,]+(?:l\.?\s*l\.?\s*c|l\.?\s*l\.?\s*p|l\.?\s*p|inc|incorporated|corp|corporation|company|co|ltd|limited|holdings?)\b\.?)+[\s,.]*$",
    re.IGNORECASE,
)


def canonical_party_key(name: str | None) -> str:
    """Return a normalized alphanumeric party key with trailing corporate suffixes removed."""
    if not name or not name.strip():
        return ""
    stripped = _CORPORATE_SUFFIX_RE.sub("", name.strip()).strip()
    return re.sub(r"[^a-z0-9]+", "_", (stripped or name).lower()).strip("_")


def make_landowner_id(project_id: str, landowner_name: str | None) -> str:
    """Create a deterministic QRM-compatible landowner_id slug from project_id and landowner_name."""
    if not landowner_name or not landowner_name.strip():
        return "lnd_unassigned"
    proj_slug = re.sub(r"^prj_", "", project_id.strip().lower())
    proj_slug = re.sub(r"[^a-z0-9]+", "_", proj_slug).strip("_") or "default"
    name_slug = re.sub(r"[^a-z0-9]+", "_", landowner_name.strip().lower()).strip("_")[:40]
    if not name_slug:
        return "lnd_unassigned"
    return f"lnd_{proj_slug}_{name_slug}"


_make_landowner_id = make_landowner_id


def _extract_parties_fallback(
    extraction: GeminiContractExtraction,
) -> tuple[str | None, str | None]:
    """Deterministically extract (grantor_landowner_name, grantee_entity_name) when omitted by the LLM."""
    grantor = (extraction.grantor_landowner_name or "").strip() or None
    grantee = (extraction.grantee_entity_name or "").strip() or None
    if grantor and grantee:
        return grantor, grantee

    if extraction.contracting_parties_json and extraction.contracting_parties_json.strip() not in ("", "[]"):
        try:
            parties = json.loads(extraction.contracting_parties_json)
            if isinstance(parties, list):
                for p in parties:
                    if not isinstance(p, dict):
                        continue
                    p_name = str(p.get("name") or "").strip()
                    p_role = str(p.get("role") or "").strip().lower()
                    if not p_name:
                        continue
                    if any(k in p_role for k in ("landowner", "grantor", "lessor", "owner")) and not grantor:
                        grantor = p_name
                    elif any(
                        k in p_role
                        for k in ("developer", "grantee", "lessee", "wind", "solar", "storage", "geothermal", "project")
                    ) and not grantee:
                        grantee = p_name
                    elif "transmission" in p_role and not grantor:
                        grantor = p_name
                if len(parties) >= 2 and isinstance(parties[0], dict) and isinstance(parties[1], dict):
                    if not grantee and not grantor:
                        grantee = str(parties[0].get("name") or "").strip() or None
                        grantor = str(parties[1].get("name") or "").strip() or None
                    elif not grantor:
                        other = [
                            str(p.get("name") or "").strip()
                            for p in parties
                            if isinstance(p, dict) and str(p.get("name") or "").strip() != grantee
                        ]
                        if other:
                            grantor = other[0]
                    elif not grantee:
                        other = [
                            str(p.get("name") or "").strip()
                            for p in parties
                            if isinstance(p, dict) and str(p.get("name") or "").strip() != grantor
                        ]
                        if other:
                            grantee = other[0]
        except Exception:  # noqa: BLE001
            pass

    if grantor and grantee:
        return grantor, grantee

    for clause in extraction.clauses:
        if clause.document_zone not in (DocumentZone.PREAMBLE, DocumentZone.RECITALS, DocumentZone.BODY):
            continue
        txt = f"{clause.preamble_text or ''} {clause.verbatim_text}".strip()
        m_between = re.search(
            r"(?:by\s+and\s+between|entered\s+into\s+by(?:\s+and\s+between)?)\s+([A-Z][A-Za-z0-9\s,&.'-]+?(?:LLC|Inc\.|LP|Corp\.|Trust|Family|Landowner|Owner)?)\s*(\([^)]*\))?\s*,?\s*(?:a\s+[A-Za-z\s]+,\s*)?and\s+([A-Z][A-Za-z0-9\s,&.'-]+?(?:LLC|Inc\.|LP|Corp\.|Trust|Family|Landowner|Owner)?)\s*(\([^)]*\))?(?:\s*,|\s*\.|$)",
            txt,
        )
        if m_between:
            p1 = m_between.group(1).strip(" ,.")
            r1 = (m_between.group(2) or "").lower()
            p2 = m_between.group(3).strip(" ,.")
            r2 = (m_between.group(4) or "").lower()
            dev_kws = (
                "wind",
                "solar",
                "storage",
                "transmission",
                "geothermal",
                "bess",
                "energy",
                "invenergy",
                "generation",
            )
            if any(k in r1 for k in ("owner", "landowner", "grantor", "lessor")) or any(
                k in r2 for k in ("grantee", "lessee", "developer")
            ):
                grantor = grantor or p1
                grantee = grantee or p2
            elif any(k in r1 for k in ("grantee", "lessee", "developer")) or any(
                k in r2 for k in ("owner", "landowner", "grantor", "lessor")
            ):
                grantee = grantee or p1
                grantor = grantor or p2
            elif any(k in p1.lower() for k in dev_kws) and not any(k in p2.lower() for k in dev_kws):
                grantee = grantee or p1
                grantor = grantor or p2
            else:
                grantor = grantor or p1
                grantee = grantee or p2
            break

    return grantor, grantee


def _normalize_special_conditions(
    extraction: GeminiContractExtraction,
    clauses_by_id: dict[str, ClauseRow],
    document_id: str,
    now_iso: str,
    project_id: str = "prj_cedar_lantern_wind",
    landowner_id: str = "lnd_unassigned",
) -> list[SpecialConditionRow]:
    """Normalize SpecialConditionRow items, deduplicate/re-home sandwich parent conditions, and run 2-signal leaf fallback."""
    parent_node_ids: set[str] = {
        c.parent_node_id for c in extraction.clauses if c.parent_node_id and c.parent_node_id in clauses_by_id
    }
    children_by_parent: dict[str, list[ClauseRow]] = {}
    for c in extraction.clauses:
        if c.parent_node_id and c.parent_node_id in clauses_by_id:
            children_by_parent.setdefault(c.parent_node_id, []).append(c)

    raw_conditions_by_node: dict[str, list[SpecialConditionRow]] = {}
    for sc in extraction.special_conditions:
        if sc.node_id not in clauses_by_id:
            continue
        src_clause = clauses_by_id[sc.node_id]
        sc.document_id = document_id
        sc.canonical_path = src_clause.canonical_path
        sc.page_number = max(1, sc.page_number if sc.page_number >= 1 else src_clause.page_start)
        sc.extraction_confidence = min(1.0, max(0.0, float(sc.extraction_confidence)))
        if sc.hitl_status != HITLStatus.APPROVED_BY_HUMAN:
            sc.hitl_status = HITLStatus.FLAGGED_FOR_REVIEW
        sc.updated_at = now_iso
        sc.project_id = project_id
        sc.landowner_id = landowner_id
        raw_conditions_by_node.setdefault(sc.node_id, []).append(sc)

    conditions_by_node: dict[str, list[SpecialConditionRow]] = {
        nid: list(conds) for nid, conds in raw_conditions_by_node.items() if nid not in parent_node_ids
    }
    for parent_id in parent_node_ids:
        parent_conds = raw_conditions_by_node.get(parent_id, [])
        if not parent_conds:
            continue
        desc_leaves = _get_descendant_leaves(parent_id, children_by_parent)
        kept_parent_conds: list[SpecialConditionRow] = []
        for p_sc in parent_conds:
            p_excerpt_lower = p_sc.verbatim_excerpt.strip().lower()
            p_asset_lower = p_sc.target_asset_or_area.strip().lower()
            matched_leaf: ClauseRow | None = None
            is_duplicate_of_leaf = False

            for leaf in desc_leaves:
                leaf_text_lower = leaf.verbatim_text.strip().lower()
                leaf_conds = conditions_by_node.get(leaf.node_id, [])
                for l_sc in leaf_conds:
                    l_excerpt_lower = l_sc.verbatim_excerpt.strip().lower()
                    l_asset_lower = l_sc.target_asset_or_area.strip().lower()
                    if (
                        p_excerpt_lower and (p_excerpt_lower in l_excerpt_lower or l_excerpt_lower in p_excerpt_lower)
                    ) or (
                        l_sc.constraint_category == p_sc.constraint_category
                        and p_asset_lower
                        and (p_asset_lower in l_asset_lower or l_asset_lower in p_asset_lower)
                    ):
                        is_duplicate_of_leaf = True
                        break
                if is_duplicate_of_leaf:
                    break
                if matched_leaf is None and (
                    (p_excerpt_lower and (p_excerpt_lower in leaf_text_lower or leaf_text_lower in p_excerpt_lower))
                    or (p_asset_lower and p_asset_lower in leaf_text_lower)
                ):
                    matched_leaf = leaf

            if is_duplicate_of_leaf:
                continue
            if matched_leaf is not None:
                p_sc.node_id = matched_leaf.node_id
                p_sc.canonical_path = matched_leaf.canonical_path
                p_sc.page_number = matched_leaf.page_start
                conditions_by_node.setdefault(matched_leaf.node_id, []).append(p_sc)
            else:
                kept_parent_conds.append(p_sc)
        if kept_parent_conds:
            conditions_by_node[parent_id] = kept_parent_conds

    for clause in extraction.clauses:
        if clause.node_id in parent_node_ids:
            continue
        if clause.document_zone in (
            DocumentZone.PREAMBLE,
            DocumentZone.RECITALS,
            DocumentZone.SIGNATURES,
        ):
            continue
        if "STUB" in clause.node_id.upper():
            continue
        if conditions_by_node.get(clause.node_id):
            continue

        fallback_sc = _synthesize_fallback_condition(clause, document_id, now_iso)
        if fallback_sc is not None:
            conditions_by_node.setdefault(clause.node_id, []).append(fallback_sc)

    normalized_special_conditions: list[SpecialConditionRow] = []
    for clause in extraction.clauses:
        node_conds = conditions_by_node.get(clause.node_id, [])
        for idx, sc in enumerate(node_conds, start=1):
            sc.condition_id = f"sc_{clause.node_id}_{idx}"
            sc.document_id = document_id
            sc.node_id = clause.node_id
            sc.canonical_path = clause.canonical_path
            sc.page_number = max(1, sc.page_number if sc.page_number >= 1 else clause.page_start)
            sc.updated_at = now_iso
            sc.project_id = project_id
            sc.landowner_id = landowner_id
            normalized_special_conditions.append(sc)
        clause.special_condition_count = len(node_conds)
        clause.has_special_condition = len(node_conds) > 0

    return normalized_special_conditions


def normalize_and_enrich_extraction(
    extraction: GeminiContractExtraction,
    document_id: str,
    gcs_pdf_uri: str,
    project_id: str = "prj_cedar_lantern_wind",
    landowner_id: str | None = None,
) -> GeminiContractExtraction:
    """Enforce deterministic hierarchy tiers, sandwich-clause context, 2-hop external deps, CR-1 special conditions, CR-2 portfolio keys, and HITL flags."""
    now_iso = utc_now_iso()

    # 0. CR-2: Populate grantor_landowner_name & grantee_entity_name with deterministic fallback
    grantor_name, grantee_name = _extract_parties_fallback(extraction)
    extraction.grantor_landowner_name = grantor_name
    extraction.grantee_entity_name = grantee_name
    resolved_landowner_id = (
        landowner_id.strip()
        if landowner_id and landowner_id.strip()
        else _make_landowner_id(project_id, grantor_name)
    )

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

    # 4b. CR-1 & CR-2: Normalize SpecialConditions, deduplicate parent-vs-child sandwich nodes, run 2-signal fallback, and stamp portfolio keys
    extraction.special_conditions = _normalize_special_conditions(
        extraction=extraction,
        clauses_by_id=clauses_by_id,
        document_id=document_id,
        now_iso=now_iso,
        project_id=project_id,
        landowner_id=resolved_landowner_id,
    )

    # 5. Evaluate the Universal HITL Flag Rules (including CR-1 LANDOWNER_SPECIAL_CONDITION) on every clause
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

        # Rule 6 (CR-1 Decision #4): LANDOWNER_SPECIAL_CONDITION mandatory HITL review gate
        if clause.has_special_condition or clause.special_condition_count > 0:
            flags.add(FlagCode.LANDOWNER_SPECIAL_CONDITION.value)
        else:
            flags.discard(FlagCode.LANDOWNER_SPECIAL_CONDITION.value)

        clause.hitl_flag_reasons = _format_pipe(flags, sort_items=True)
        if clause.hitl_status != HITLStatus.APPROVED_BY_HUMAN:
            if FlagCode.DEFERRED_MODALITY_PLACEHOLDER.value in flags:
                clause.hitl_status = HITLStatus.PLACEHOLDER_FOR_REVIEW
            elif flags:
                clause.hitl_status = HITLStatus.FLAGGED_FOR_REVIEW
            else:
                clause.hitl_status = HITLStatus.VERIFIED_AUTO

    return extraction

