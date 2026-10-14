"""Contract Variance Engine and Legal Redline Studio.

Implements two-stage hybrid alignment, parameter substitution masking,
token-level Myers diff with HTML redlines, mathematical template affinity matching,
and legal risk triage.
"""

from __future__ import annotations

import difflib
import html
import re
from typing import Any

from contract_parser.schemas import (
    ClauseVarianceDetailRow,
    ContractVarianceBundle,
    ContractVarianceRow,
    TemplateClauseRow,
    TemplateRegistryRow,
    VarianceCandidateTemplate,
    VarianceRiskLevel,
    VarianceSummary,
    VarianceType,
    utc_now_iso,
)


class AffinityScore(float):
    """Float subclass carrying explainable affinity bonus factors."""

    reasons: list[str]

    def __new__(cls, value: float, reasons: list[str] | None = None):
        obj = super().__new__(cls, value)
        obj.reasons = reasons or []
        return obj

    def __iter__(self):
        yield float(self)
        yield self.reasons


class ParameterSubstitutions(dict):
    """Dictionary subclass supporting size comparison operators."""

    def __ge__(self, other: Any) -> bool:
        if isinstance(other, (int, float)):
            return len(self) >= other
        return dict(self) >= other

    def __gt__(self, other: Any) -> bool:
        if isinstance(other, (int, float)):
            return len(self) > other
        return dict(self) > other

    def __le__(self, other: Any) -> bool:
        if isinstance(other, (int, float)):
            return len(self) <= other
        return dict(self) <= other

    def __lt__(self, other: Any) -> bool:
        if isinstance(other, (int, float)):
            return len(self) < other
        return dict(self) < other

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, (int, float)):
            return len(self) == other
        return super().__eq__(other)

    def __int__(self) -> int:
        return len(self)


class RiskEvaluation(tuple):
    """3-tuple subclass (risk_level, risk_category, risk_rationale) with score attributes."""

    risk_level: VarianceRiskLevel
    risk_category: str
    risk_rationale: str
    risk_score: float
    score: float

    def __new__(
        cls,
        risk_level: VarianceRiskLevel,
        risk_category: str,
        risk_rationale: str,
        score: float | None = None,
    ):
        if score is None:
            if risk_level == VarianceRiskLevel.HIGH:
                score = 1.0
            elif risk_level == VarianceRiskLevel.MEDIUM:
                score = 0.6
            elif risk_level == VarianceRiskLevel.LOW:
                score = 0.2
            else:
                score = 0.0
        obj = super().__new__(cls, (risk_level, risk_category, risk_rationale))
        obj.risk_level = risk_level
        obj.risk_category = risk_category
        obj.risk_rationale = risk_rationale
        obj.risk_score = score
        obj.score = score
        return obj

    @property
    def level(self) -> VarianceRiskLevel:
        return self.risk_level

    @property
    def category(self) -> str:
        return self.risk_category

    @property
    def rationale(self) -> str:
        return self.risk_rationale


def calculate_template_affinity(
    contract_meta: dict[str, Any],
    template_row: TemplateRegistryRow | dict[str, Any],
) -> AffinityScore:
    """Calculate affinity score S in [0.0, 1.0] between a contract and a template.

    Formula:
      S = 0.40 * I_state + 0.30 * I_tech + 0.20 * I_form + 0.10 * max(0, 1 - |vintage - contract_year| / 10)
    """
    c_state_raw = (
        contract_meta.get("state")
        or contract_meta.get("state_province")
        or contract_meta.get("region")
        or ""
    )
    c_state = str(c_state_raw.value if hasattr(c_state_raw, "value") else c_state_raw).strip().upper()

    c_tech_raw = contract_meta.get("energy_technology") or contract_meta.get("technology") or ""
    c_tech = str(c_tech_raw.value if hasattr(c_tech_raw, "value") else c_tech_raw).strip().upper()

    c_form_raw = contract_meta.get("form_type") or ""
    c_form = str(c_form_raw.value if hasattr(c_form_raw, "value") else c_form_raw).strip().upper()

    c_year_val = (
        contract_meta.get("vintage_year")
        or contract_meta.get("effective_vintage_year")
        or contract_meta.get("execution_date")
        or contract_meta.get("created_at")
    )
    c_year: int | None = None
    if c_year_val:
        try:
            m = re.search(r"\b(19\d\d|20\d\d)\b", str(c_year_val))
            if m:
                c_year = int(m.group(1))
            else:
                c_year = int(str(c_year_val)[:4])
        except Exception:
            c_year = None

    if isinstance(template_row, dict):
        t_state_raw = (
            template_row.get("state")
            or template_row.get("state_province")
            or template_row.get("region")
            or ""
        )
        t_tech_raw = template_row.get("energy_technology") or template_row.get("technology") or ""
        t_form_raw = template_row.get("form_type") or ""
        t_year_val = (
            template_row.get("effective_vintage_year")
            or template_row.get("vintage_year")
        )
    else:
        t_state_raw = (
            getattr(template_row, "state", None)
            or getattr(template_row, "state_province", None)
            or getattr(template_row, "region", "")
        )
        t_tech_raw = getattr(template_row, "energy_technology", None) or getattr(template_row, "technology", "")
        t_form_raw = getattr(template_row, "form_type", "")
        t_year_val = (
            getattr(template_row, "effective_vintage_year", None)
            or getattr(template_row, "vintage_year", None)
        )

    t_state = str(t_state_raw.value if hasattr(t_state_raw, "value") else t_state_raw).strip().upper()
    t_tech = str(t_tech_raw.value if hasattr(t_tech_raw, "value") else t_tech_raw).strip().upper()
    t_form = str(t_form_raw.value if hasattr(t_form_raw, "value") else t_form_raw).strip().upper()

    t_year: int | None = None
    if t_year_val:
        try:
            m = re.search(r"\b(19\d\d|20\d\d)\b", str(t_year_val))
            if m:
                t_year = int(m.group(1))
            else:
                t_year = int(str(t_year_val)[:4])
        except Exception:
            t_year = None

    reasons: list[str] = []

    # State indicator
    is_state_match = bool(c_state and t_state and (c_state == t_state or t_state == "US" or c_state == "US"))
    i_state = 1.0 if is_state_match else 0.0
    if is_state_match:
        reasons.append(f"+0.40 State match ({t_state or c_state})")

    # Technology indicator
    def normalize_tech(tech_str: str) -> str:
        t = tech_str.replace(" ", "_")
        if t in ("WIND", "ONSHORE_WIND"):
            return "ONSHORE_WIND"
        if t in ("SOLAR", "UTILITY_SOLAR"):
            return "SOLAR"
        return t

    norm_c_tech = normalize_tech(c_tech)
    norm_t_tech = normalize_tech(t_tech)
    is_tech_match = bool(norm_c_tech and norm_t_tech and norm_c_tech == norm_t_tech)
    i_tech = 1.0 if is_tech_match else 0.0
    if is_tech_match:
        reasons.append(f"+0.30 Technology match ({norm_t_tech})")

    # Form type indicator
    def normalize_form(form_str: str) -> str:
        return form_str.replace(" ", "_")

    norm_c_form = normalize_form(c_form)
    norm_t_form = normalize_form(t_form)
    is_form_match = bool(
        norm_c_form
        and norm_t_form
        and (norm_c_form == norm_t_form or norm_c_form in norm_t_form or norm_t_form in norm_c_form)
    )
    i_form = 1.0 if is_form_match else 0.0
    if is_form_match:
        reasons.append(f"+0.20 Form type match ({norm_t_form})")

    # Vintage decay
    if c_year is not None and t_year is not None:
        diff_years = abs(t_year - c_year)
        vintage_factor = max(0.0, 1.0 - (diff_years / 10.0))
        vintage_score = 0.10 * vintage_factor
        if diff_years == 0:
            reasons.append("+0.10 Vintage exact match")
        elif vintage_factor > 0:
            reasons.append(f"+{round(vintage_score, 2):.2f} Vintage factor ({diff_years} yr drift)")
    else:
        vintage_score = 0.0

    score = 0.40 * i_state + 0.30 * i_tech + 0.20 * i_form + vintage_score
    return AffinityScore(round(score, 4), reasons)


def rank_candidate_templates(
    contract_meta: dict[str, Any],
    templates: list[TemplateRegistryRow | dict[str, Any]],
) -> list[VarianceCandidateTemplate]:
    """Rank templates by affinity score descending."""
    candidates: list[VarianceCandidateTemplate] = []
    c_state_raw = (
        contract_meta.get("state")
        or contract_meta.get("state_province")
        or contract_meta.get("region")
        or ""
    )
    c_state = str(c_state_raw.value if hasattr(c_state_raw, "value") else c_state_raw).strip().upper()

    c_tech_raw = contract_meta.get("energy_technology") or contract_meta.get("technology") or ""
    c_tech = str(c_tech_raw.value if hasattr(c_tech_raw, "value") else c_tech_raw).strip().upper()

    def normalize_tech(tech_str: str) -> str:
        t = tech_str.replace(" ", "_")
        if t in ("WIND", "ONSHORE_WIND"):
            return "ONSHORE_WIND"
        if t in ("SOLAR", "UTILITY_SOLAR"):
            return "SOLAR"
        return t

    norm_c_tech = normalize_tech(c_tech)

    for tpl in templates:
        aff = calculate_template_affinity(contract_meta, tpl)
        score = float(aff)
        if isinstance(tpl, dict):
            tpl_id = tpl["template_id"]
            tpl_name = tpl.get("form_name") or tpl.get("template_name") or tpl_id
            t_state = str(tpl.get("state") or tpl.get("state_province") or tpl.get("region") or "").strip().upper()
            t_tech = str(tpl.get("technology") or tpl.get("energy_technology") or "").strip().upper()
            t_form = str(tpl.get("form_type") or "").strip().upper()
            t_year = str(tpl.get("effective_vintage_year") or tpl.get("vintage_year") or "2024")
        else:
            tpl_id = tpl.template_id
            tpl_name = getattr(tpl, "form_name", None) or tpl.template_name or tpl_id
            t_state = str(getattr(tpl, "state", None) or tpl.state_province or "").strip().upper()
            t_tech = str(getattr(tpl, "technology", None) or tpl.energy_technology or "").strip().upper()
            t_form = str(tpl.form_type or "").strip().upper()
            t_year = str(getattr(tpl, "effective_vintage_year", None) or tpl.vintage_year or "2024")

        norm_t_tech = normalize_tech(t_tech)
        is_exact_state = bool(c_state and t_state and (c_state == t_state or t_state == "US"))
        is_exact_tech = bool(norm_c_tech and norm_t_tech and norm_c_tech == norm_t_tech)

        candidates.append(
            VarianceCandidateTemplate(
                template_id=tpl_id,
                template_name=tpl_name,
                affinity_score=score,
                is_exact_state_match=is_exact_state,
                is_exact_tech_match=is_exact_tech,
                energy_technology=norm_t_tech or t_tech,
                region=t_state,
                state_province=t_state,
                form_type=t_form,
                vintage_year=t_year,
            )
        )

    candidates.sort(key=lambda c: c.affinity_score, reverse=True)
    return candidates


def compute_token_jaccard(text_a: str, text_b: str) -> float:
    """Compute token-level Jaccard similarity between two text strings."""
    tokens_a = set(re.findall(r"\w+", (text_a or "").lower()))
    tokens_b = set(re.findall(r"\w+", (text_b or "").lower()))
    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a.intersection(tokens_b)
    union = tokens_a.union(tokens_b)
    return len(intersection) / len(union)


def generate_redline_html(template_text: str, contract_text: str) -> str:
    """Generate inline HTML redlines using SequenceMatcher word tokens."""
    tpl_words = re.findall(r"\S+|\s+", template_text or "")
    ctr_words = re.findall(r"\S+|\s+", contract_text or "")

    matcher = difflib.SequenceMatcher(None, tpl_words, ctr_words)
    html_chunks: list[str] = []

    def _wrap(tag: str, cls_name: str, raw_text: str) -> str:
        if not raw_text:
            return ""
        if raw_text.isspace():
            return raw_text
        leading = raw_text[: len(raw_text) - len(raw_text.lstrip())]
        trailing = raw_text[len(raw_text.rstrip()) :]
        core = raw_text.strip()
        return f'{leading}<{tag} class="{cls_name}">{html.escape(core)}</{tag}>{trailing}'

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            html_chunks.append(html.escape("".join(ctr_words[j1:j2])))
        elif tag == "delete":
            deleted_text = "".join(tpl_words[i1:i2])
            html_chunks.append(_wrap("del", "diff-del", deleted_text))
        elif tag == "insert":
            inserted_text = "".join(ctr_words[j1:j2])
            html_chunks.append(_wrap("ins", "diff-ins", inserted_text))
        elif tag == "replace":
            deleted_text = "".join(tpl_words[i1:i2])
            inserted_text = "".join(ctr_words[j1:j2])
            html_chunks.append(_wrap("del", "diff-del", deleted_text))
            html_chunks.append(_wrap("ins", "diff-ins", inserted_text))

    return "".join(html_chunks)


def evaluate_parameter_substitution(
    template_text: str,
    contract_text: str,
    placeholders: list[dict[str, Any]] | list[Any] | None,
) -> tuple[bool, ParameterSubstitutions]:
    """Test whether contract_text differs from template_text strictly within placeholder spans.

    Returns (is_parameter_only, extracted_dict).
    """
    tpl = (template_text or "").strip()
    ctr = (contract_text or "").strip()

    if tpl == ctr:
        return True, ParameterSubstitutions()

    # Extract all bracket variables: [Variable Name] or $[_____] or [_____]
    pattern_brackets = r"(\$?\s*\[[^\]]+\])"
    bracket_matches = list(re.finditer(pattern_brackets, tpl))

    if not bracket_matches and not placeholders:
        return False, ParameterSubstitutions()

    # If bracket matches exist, construct non-greedy regex
    if bracket_matches:
        regex_parts: list[str] = []
        last_idx = 0
        names: list[str] = []

        for idx, match in enumerate(bracket_matches):
            start, end = match.span()
            literal_chunk = re.escape(tpl[last_idx:start])
            regex_parts.append(literal_chunk)
            p_name = match.group(1).strip("[]$ \t_") or f"var_{idx+1}"
            names.append(p_name)
            # Restrict match to non-bracket, line-bounded tokens up to 120 characters
            regex_parts.append(r"(\$?[^\[\]\r\n]{1,120}?)")
            last_idx = end

        regex_parts.append(re.escape(tpl[last_idx:]))
        full_pattern = "^" + "".join(regex_parts) + "$"

        try:
            match_obj = re.match(full_pattern, ctr)
            if match_obj:
                substitutions = ParameterSubstitutions()
                for i, val in enumerate(match_obj.groups()):
                    var_key = names[i] if i < len(names) else f"param_{i+1}"
                    substitutions[var_key] = val.strip()
                return True, substitutions
        except Exception:
            pass

    return False, ParameterSubstitutions()


def evaluate_legal_risk(
    *args: Any,
    tpl_text: str | None = None,
    con_text: str | None = None,
    variance_type: VarianceType | str | None = None,
    clause_title: str | None = None,
    canonical_path: str | None = None,
    template_text: str | None = None,
    contract_text: str | None = None,
    **kwargs: Any,
) -> RiskEvaluation:
    """Classify legal risk tier, score, category, and rationale."""
    if len(args) == 3:
        tpl_text = args[0]
        con_text = args[1]
        variance_type = args[2]
    elif len(args) == 5:
        canonical_path = args[0]
        clause_title = args[1]
        variance_type = args[2]
        tpl_text = args[3]
        con_text = args[4]
    elif len(args) == 1:
        variance_type = args[0]

    tpl_text = tpl_text or template_text or ""
    con_text = con_text or contract_text or ""
    clause_title = clause_title or kwargs.get("title") or ""
    canonical_path = canonical_path or kwargs.get("path") or ""
    variance_type = variance_type or kwargs.get("var_type") or VarianceType.MODIFIED

    if variance_type in (
        VarianceType.IDENTICAL,
        "IDENTICAL",
        VarianceType.PARAMETER_ONLY,
        "PARAMETER_ONLY",
    ):
        return RiskEvaluation(
            VarianceRiskLevel.NONE,
            "STANDARD_BOILERPLATE",
            "Identical standard language or verified parameter fill-in.",
            0.0,
        )

    combined_text = f"{clause_title} {canonical_path} {tpl_text} {con_text}".lower()

    # High-Risk checks
    if re.search(r"\b(indemn(ify|ity|ification)|defense\s+and\s+indemnify|hold\s+harmless)\b", combined_text) or (
        "indemnity" in clause_title.lower() or "liability" in clause_title.lower()
    ):
        return RiskEvaluation(
            VarianceRiskLevel.HIGH,
            "INDEMNITY_AND_LIABILITY",
            "Landowner altered indemnification obligation, operator defense, or liability carve-outs.",
            1.0,
        )

    if re.search(r"\b(removal bond|decommissioning bond|surety bond|decommissioning)\b", combined_text):
        return RiskEvaluation(
            VarianceRiskLevel.HIGH,
            "DECOMMISSIONING_SECURITY",
            "Changes to decommissioning security requirements, bonding threshold, or timelines.",
            1.0,
        )

    if re.search(r"\b(gross negligence|willful misconduct)\b", combined_text):
        return RiskEvaluation(
            VarianceRiskLevel.HIGH,
            "LIABILITY_STANDARD",
            "Modification directly impacts standard of care, gross negligence, or willful misconduct standards.",
            1.0,
        )

    if re.search(r"\b(consequential|punitive|indirect damages)\b", combined_text):
        return RiskEvaluation(
            VarianceRiskLevel.HIGH,
            "DAMAGE_LIMITATION",
            "Carve-out or exclusion of consequential or special damage protections.",
            1.0,
        )

    if re.search(r"\b(sole discretion|termination for convenience|right to terminate)\b", combined_text):
        return RiskEvaluation(
            VarianceRiskLevel.HIGH,
            "TERMINATION_RIGHTS",
            "Custom termination triggers, convenience rights, or notice periods altered.",
            1.0,
        )

    if re.search(r"\b(environmental indemnity|hazardous substances?|superfund)\b", combined_text):
        return RiskEvaluation(
            VarianceRiskLevel.HIGH,
            "ENVIRONMENTAL_LIABILITY",
            "Deviation in environmental liability representation or hazardous substance indemnity.",
            1.0,
        )

    if re.search(r"\b(default|cure period|remedies? on default)\b", combined_text):
        return RiskEvaluation(
            VarianceRiskLevel.HIGH,
            "DEFAULT_REMEDIES",
            "Notice and cure timelines or legal remedies modified against standard form.",
            1.0,
        )

    if re.search(r"\b(assignment|consent to assign|mortgagee protection)\b", combined_text):
        return RiskEvaluation(
            VarianceRiskLevel.HIGH,
            "FINANCING_ASSIGNMENT",
            "Restrictions placed on leasehold financing, collateral assignments, or institutional lender protections.",
            1.0,
        )

    # Medium-Risk checks
    if re.search(r"\b(drain tile|livestock|crop compensation|gates?|drainage)\b", combined_text) or "drain" in clause_title.lower():
        return RiskEvaluation(
            VarianceRiskLevel.MEDIUM,
            "OPERATIONAL_ACCOMMODATION",
            "Custom agricultural, drain tile, livestock, or hunting accommodation rider attached.",
            0.6,
        )

    if re.search(r"\b(notice period|days prior notice|written notice)\b", combined_text):
        return RiskEvaluation(
            VarianceRiskLevel.MEDIUM,
            "OPERATIONAL_NOTICE",
            "Shift in notice timing or operational window.",
            0.6,
        )

    if re.search(r"\b(inspection|audit|books and records)\b", combined_text):
        return RiskEvaluation(
            VarianceRiskLevel.MEDIUM,
            "AUDIT_INSPECTION",
            "Landowner inserted custom audit inspection rights or access terms.",
            0.6,
        )

    if re.search(r"\b(insurance|additional insured|certificates? of insurance)\b", combined_text):
        return RiskEvaluation(
            VarianceRiskLevel.MEDIUM,
            "INSURANCE_COVENANT",
            "Adjustments to policy limits, additional insured endorsements, or delivery deadlines.",
            0.6,
        )

    return RiskEvaluation(
        VarianceRiskLevel.LOW,
        "CLERICAL_OR_STYLISTIC",
        "Minor textual modification, grammatical clarification, or section wording variation.",
        0.2,
    )


def run_contract_variance_comparison(
    contract_doc: dict[str, Any] | None = None,
    contract_clauses: list[dict[str, Any]] | None = None,
    template_row: TemplateRegistryRow | dict[str, Any] | None = None,
    template_clauses: list[TemplateClauseRow | dict[str, Any]] | None = None,
    binding_mode: str = "AUTO_SUGGESTED",
    affinity_score: float | None = None,
    contract_meta: dict[str, Any] | None = None,
    **kwargs: Any,
) -> ContractVarianceBundle:
    """Execute end-to-end two-stage alignment, parameter masking, diffing, and risk evaluation."""
    meta = contract_meta or contract_doc or {}
    doc_id = meta.get("document_id") or "doc_unknown"

    ctr_clauses = contract_clauses or []
    tpl_clauses = template_clauses or []

    if template_row is None:
        first_tpl_id = (
            tpl_clauses[0].get("template_id")
            if (tpl_clauses and isinstance(tpl_clauses[0], dict) and "template_id" in tpl_clauses[0])
            else "tpl_baseline"
        )
        template_row = {
            "template_id": first_tpl_id,
            "template_name": "Standard Master Template",
            "revision": "1.0",
        }

    if isinstance(template_row, dict):
        tpl_id = template_row["template_id"]
        tpl_name = template_row.get("template_name") or template_row.get("form_name") or "Standard Master Template"
        tpl_ver = template_row.get("revision") or "1.0"
    else:
        tpl_id = template_row.template_id
        tpl_name = getattr(template_row, "template_name", None) or getattr(template_row, "form_name", None) or "Standard Master Template"
        tpl_ver = getattr(template_row, "revision", "1.0")

    if affinity_score is None:
        aff = calculate_template_affinity(meta, template_row)
        affinity_score = float(aff)

    variance_id = f"var_{doc_id}_{tpl_id}"

    # Index template clauses
    tpl_by_path: dict[str, dict[str, Any]] = {}
    tpl_by_zone: dict[str, list[dict[str, Any]]] = {}

    for tc in tpl_clauses:
        tc_dict = tc if isinstance(tc, dict) else tc.model_dump()
        c_path = tc_dict.get("canonical_path") or tc_dict.get("clause_label") or "UNKNOWN"
        tpl_by_path[c_path] = tc_dict
        zone = tc_dict.get("document_zone") or "BODY"
        tpl_by_zone.setdefault(zone, []).append(tc_dict)

    aligned_tpl_paths: set[str] = set()
    clause_details: list[ClauseVarianceDetailRow] = []

    # Map contract clauses to template clauses using Stage 1 and Stage 2
    for idx, cc in enumerate(ctr_clauses):
        cc_dict = cc if isinstance(cc, dict) else cc.model_dump()
        cc_id = cc_dict.get("node_id") or cc_dict.get("clause_id")
        c_path = cc_dict.get("canonical_path") or cc_dict.get("clause_label") or "UNKNOWN"
        c_title = cc_dict.get("clause_title") or ""
        c_text = (
            cc_dict.get("verbatim_text")
            or cc_dict.get("reconstructed_context_text")
            or cc_dict.get("section_text")
            or ""
        )
        c_label = cc_dict.get("clause_label") or c_path
        c_zone = cc_dict.get("document_zone") or "BODY"
        c_page = cc_dict.get("page_start")

        matched_tpl: dict[str, Any] | None = None

        # Stage 1: Exact Canonical Path check
        if c_path in tpl_by_path and c_path not in aligned_tpl_paths:
            cand = tpl_by_path[c_path]
            cand_title = cand.get("clause_title") or ""
            cand_text = cand.get("verbatim_text") or cand.get("section_text") or ""
            title_sim = compute_token_jaccard(c_title, cand_title)
            text_sim = compute_token_jaccard(c_text[:120], cand_text[:120])
            if (not c_title and not cand_title) or title_sim >= 0.50 or text_sim >= 0.60:
                matched_tpl = cand

        # Stage 2: Drift Recovery
        if not matched_tpl:
            zone_candidates = tpl_by_zone.get(c_zone, [])
            best_cand: dict[str, Any] | None = None
            best_sim = 0.0

            for cand in zone_candidates:
                t_path = cand.get("canonical_path") or cand.get("clause_label") or ""
                if t_path in aligned_tpl_paths:
                    continue
                cand_title = cand.get("clause_title") or ""
                cand_text = cand.get("verbatim_text") or cand.get("section_text") or ""

                title_sim = compute_token_jaccard(c_title, cand_title)
                text_sim = compute_token_jaccard(c_text, cand_text)
                lead_sim = compute_token_jaccard(c_text[:120], cand_text[:120])
                score = max(title_sim, text_sim, lead_sim)

                if score >= 0.70 and score > best_sim:
                    best_sim = score
                    best_cand = cand

            if best_cand:
                matched_tpl = best_cand

        # Process aligned or novel node
        if matched_tpl:
            t_path = matched_tpl.get("canonical_path") or matched_tpl.get("clause_label") or ""
            aligned_tpl_paths.add(t_path)
            t_id = matched_tpl.get("node_id") or matched_tpl.get("clause_id")
            t_text = matched_tpl.get("verbatim_text") or matched_tpl.get("section_text") or ""
            t_placeholders = matched_tpl.get("placeholder_variables") or matched_tpl.get("placeholders")

            is_param_only, param_dict = evaluate_parameter_substitution(t_text, c_text, t_placeholders)

            if t_text.strip() == c_text.strip():
                var_type = VarianceType.IDENTICAL
                risk_res = evaluate_legal_risk(c_path, c_title, var_type, t_text, c_text)
                clause_details.append(
                    ClauseVarianceDetailRow(
                        detail_id=f"vardet_{variance_id}_c{idx}_{c_path}",
                        variance_id=variance_id,
                        document_id=doc_id,
                        canonical_path=c_path,
                        template_clause_id=t_id,
                        contract_clause_id=cc_id,
                        clause_label=c_label,
                        clause_title=c_title or matched_tpl.get("clause_title"),
                        variance_type=var_type,
                        risk_level=risk_res.risk_level,
                        risk_score=risk_res.risk_score,
                        risk_category=risk_res.risk_category,
                        risk_rationale=risk_res.risk_rationale,
                        similarity_score=1.0,
                        template_text=t_text,
                        contract_text=c_text,
                        redline_html="",
                        page_start=c_page,
                    )
                )
            elif is_param_only:
                var_type = VarianceType.PARAMETER_ONLY
                clause_details.append(
                    ClauseVarianceDetailRow(
                        detail_id=f"vardet_{variance_id}_c{idx}_{c_path}",
                        variance_id=variance_id,
                        document_id=doc_id,
                        canonical_path=c_path,
                        template_clause_id=t_id,
                        contract_clause_id=cc_id,
                        clause_label=c_label,
                        clause_title=c_title or matched_tpl.get("clause_title"),
                        variance_type=var_type,
                        risk_level=VarianceRiskLevel.NONE,
                        risk_score=0.0,
                        risk_category="PARAMETER_SUBSTITUTION",
                        risk_rationale=(
                            f"Verified valid fill-in for parameters: {', '.join(param_dict.keys())}."
                            if param_dict
                            else "Verified valid parameter fill-in."
                        ),
                        parameter_substitutions=dict(param_dict),
                        similarity_score=1.0,
                        template_text=t_text,
                        contract_text=c_text,
                        redline_html="",
                        page_start=c_page,
                    )
                )
            else:
                var_type = VarianceType.MODIFIED
                redline = generate_redline_html(t_text, c_text)
                sim = compute_token_jaccard(t_text, c_text)
                risk_res = evaluate_legal_risk(c_path, c_title, var_type, t_text, c_text)
                clause_details.append(
                    ClauseVarianceDetailRow(
                        detail_id=f"vardet_{variance_id}_c{idx}_{c_path}",
                        variance_id=variance_id,
                        document_id=doc_id,
                        canonical_path=c_path,
                        template_clause_id=t_id,
                        contract_clause_id=cc_id,
                        clause_label=c_label,
                        clause_title=c_title or matched_tpl.get("clause_title"),
                        variance_type=var_type,
                        risk_level=risk_res.risk_level,
                        risk_score=risk_res.risk_score,
                        risk_category=risk_res.risk_category,
                        risk_rationale=risk_res.risk_rationale,
                        similarity_score=round(sim, 4),
                        template_text=t_text,
                        contract_text=c_text,
                        redline_html=redline,
                        page_start=c_page,
                    )
                )
        else:
            # Novel clause ADDED
            var_type = VarianceType.ADDED
            risk_res = evaluate_legal_risk(c_path, c_title, var_type, None, c_text)
            inserted_html = f'<ins class="diff-ins">{html.escape(c_text)}</ins>'
            clause_details.append(
                ClauseVarianceDetailRow(
                    detail_id=f"vardet_{variance_id}_c{idx}_{c_path}",
                    variance_id=variance_id,
                    document_id=doc_id,
                    canonical_path=c_path,
                    template_clause_id=None,
                    contract_clause_id=cc_id,
                    clause_label=c_label,
                    clause_title=c_title,
                    variance_type=var_type,
                    risk_level=risk_res.risk_level,
                    risk_score=risk_res.risk_score,
                    risk_category=risk_res.risk_category or "CUSTOM_ADDED_CLAUSE",
                    risk_rationale=risk_res.risk_rationale or "Custom rider or novel provision not found in master template.",
                    similarity_score=0.0,
                    template_text=None,
                    contract_text=c_text,
                    redline_html=inserted_html,
                    page_start=c_page,
                )
            )

    # Check for omitted template clauses (DELETED)
    for t_idx, (t_path, tc) in enumerate(tpl_by_path.items()):
        if t_path not in aligned_tpl_paths:
            t_text = tc.get("verbatim_text") or tc.get("section_text") or ""
            t_title = tc.get("clause_title") or ""
            t_label = tc.get("clause_label") or t_path
            t_id = tc.get("node_id") or tc.get("clause_id")
            var_type = VarianceType.DELETED
            risk_res = evaluate_legal_risk(t_path, t_title, var_type, t_text, None)
            deleted_html = f'<del class="diff-del">{html.escape(t_text)}</del>'
            clause_details.append(
                ClauseVarianceDetailRow(
                    detail_id=f"vardet_{variance_id}_t{t_idx}_{t_path}",
                    variance_id=variance_id,
                    document_id=doc_id,
                    canonical_path=t_path,
                    template_clause_id=t_id,
                    contract_clause_id=None,
                    clause_label=t_label,
                    clause_title=t_title,
                    variance_type=var_type,
                    risk_level=risk_res.risk_level,
                    risk_score=risk_res.risk_score,
                    risk_category=risk_res.risk_category or "OMITTED_STANDARD_CLAUSE",
                    risk_rationale=risk_res.risk_rationale or "Standard master template clause omitted from executed contract.",
                    similarity_score=0.0,
                    template_text=t_text,
                    contract_text=None,
                    redline_html=deleted_html,
                    page_start=tc.get("page_start"),
                )
            )

    # Calculate summaries
    total_tpl = len(tpl_clauses)
    total_ctr = len(ctr_clauses)
    ident_cnt = sum(1 for d in clause_details if d.variance_type == VarianceType.IDENTICAL)
    param_cnt = sum(1 for d in clause_details if d.variance_type == VarianceType.PARAMETER_ONLY)
    mod_cnt = sum(1 for d in clause_details if d.variance_type == VarianceType.MODIFIED)
    add_cnt = sum(1 for d in clause_details if d.variance_type == VarianceType.ADDED)
    del_cnt = sum(1 for d in clause_details if d.variance_type == VarianceType.DELETED)

    high_cnt = sum(1 for d in clause_details if d.risk_level == VarianceRiskLevel.HIGH)
    med_cnt = sum(1 for d in clause_details if d.risk_level == VarianceRiskLevel.MEDIUM)
    low_cnt = sum(1 for d in clause_details if d.risk_level == VarianceRiskLevel.LOW)

    deviated_count = mod_cnt + add_cnt + del_cnt
    if deviated_count == 0:
        overall_score = 0.0
    else:
        total_considered = max(1, total_tpl)
        base_dev_ratio = deviated_count / total_considered
        weighted_risk_factor = (high_cnt * 1.0 + med_cnt * 0.5 + low_cnt * 0.2) / max(1, total_considered)
        overall_score = min(100.0, round((base_dev_ratio * 70.0 + weighted_risk_factor * 30.0) * 100.0 / 100.0, 1))

    summary = VarianceSummary(
        total_template_clauses=total_tpl,
        total_contract_clauses=total_ctr,
        total_clauses_analyzed=total_ctr,
        identical_count=ident_cnt,
        parameter_only_count=param_cnt,
        modified_count=mod_cnt,
        added_count=add_cnt,
        deleted_count=del_cnt,
        overall_risk_score=overall_score,
        high_risk_count=high_cnt,
        medium_risk_count=med_cnt,
        low_risk_count=low_cnt,
        none_risk_count=ident_cnt + param_cnt,
    )

    parent_row = ContractVarianceRow(
        variance_id=variance_id,
        document_id=doc_id,
        template_id=tpl_id,
        template_name=tpl_name,
        template_version=tpl_ver,
        binding_mode=binding_mode,
        affinity_score=affinity_score,
        total_template_clauses=total_tpl,
        total_contract_clauses=total_ctr,
        identical_count=ident_cnt,
        parameter_only_count=param_cnt,
        modified_count=mod_cnt,
        added_count=add_cnt,
        deleted_count=del_cnt,
        overall_risk_score=overall_score,
        high_risk_count=high_cnt,
        medium_risk_count=med_cnt,
        low_risk_count=low_cnt,
        summary=summary,
        created_at=utc_now_iso(),
    )

    return ContractVarianceBundle(
        variance=parent_row,
        summary=summary,
        details=clause_details,
    )
