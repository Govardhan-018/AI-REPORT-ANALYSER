"""
Evidence Analyzer — Intermediate reasoning layer for audit-defensible compliance analysis.

Sits between vector retrieval and prompt building to:
  1. Classify evidence strength (Explicit / Strongly Implied / Partial / No Evidence)
  2. Detect what is directly stated vs. inferred
  3. Identify compliance gaps (policy vs. implementation vs. operational effectiveness)
  4. Produce structured evidence analysis for the constrained auditor prompt

This module does NOT call the LLM — it uses deterministic heuristics on
retrieved chunks to produce an evidence assessment that guides the final prompt.
"""

import logging
import re
from typing import List, Dict, Tuple
from dataclasses import dataclass, field

from models.schemas import SearchResult

logger = logging.getLogger("complianceai.evidence_analyzer")

# ─── Evidence Strength Thresholds ─────────────────────────────────────
EXPLICIT_THRESHOLD = 0.72     # Cosine sim ≥ 0.72 → likely explicit match
IMPLIED_THRESHOLD = 0.60      # 0.60–0.72 → strongly implied
PARTIAL_THRESHOLD = 0.50      # 0.50–0.60 → partial evidence only

# ─── Compliance Maturity Keywords ─────────────────────────────────────
# Used to distinguish policy existence vs. operational effectiveness

POLICY_EXISTENCE_KEYWORDS = [
    "policy", "policies", "shall", "must", "procedure", "guideline",
    "standard", "framework", "requirement", "defined", "documented",
    "established", "approved", "scope", "objective",
]

IMPLEMENTATION_KEYWORDS = [
    "implemented", "deployed", "configured", "installed", "enabled",
    "activated", "in place", "operational", "running", "enforced",
    "applied", "executed", "integrated", "adopted",
]

TESTING_KEYWORDS = [
    "tested", "verified", "validated", "assessed", "audited",
    "reviewed", "evaluated", "monitored", "measured", "inspected",
    "checked", "confirmed", "penetration test", "vulnerability scan",
    "audit trail", "log review", "evidence of testing",
]

AUDIT_VERIFICATION_KEYWORDS = [
    "certified", "attested", "audit report", "finding", "observation",
    "non-conformity", "corrective action", "management review",
    "internal audit", "external audit", "surveillance audit",
    "certification body", "evidence reviewed", "sample tested",
]


@dataclass
class EvidenceClassification:
    """Classification of a single retrieved chunk's evidence strength."""
    chunk_index: int
    filename: str
    page_number: int
    score: float
    strength: str  # "EXPLICIT" | "IMPLICIT" | "ADJACENT" | "MISSING"
    content_preview: str  # first 200 chars
    maturity_signals: List[str] = field(default_factory=list)  # what maturity levels are mentioned
    key_quotes: List[str] = field(default_factory=list)  # relevant sentence extracts


@dataclass
class EvidenceAnalysis:
    """Complete analysis of all retrieved evidence for a query."""
    overall_strength: str    # aggregate evidence strength
    classifications: List[EvidenceClassification]
    explicit_count: int = 0
    implied_count: int = 0
    partial_count: int = 0
    no_evidence_count: int = 0
    maturity_assessment: str = ""  # summary of policy/impl/testing/audit signals
    coverage_gaps: List[str] = field(default_factory=list)
    grounding_notes: str = ""  # instructions for the LLM prompt


def analyze_evidence(
    query: str,
    chunks: List[SearchResult],
) -> EvidenceAnalysis:
    """
    Analyze retrieved evidence chunks before they reach the LLM.

    Produces a structured analysis that:
      1. Classifies each chunk's evidence strength
      2. Extracts key quotes that the LLM should cite
      3. Detects compliance maturity level (policy/implementation/testing/audit)
      4. Identifies gaps where evidence is missing
      5. Generates grounding instructions for the prompt builder

    Args:
        query: The user's compliance question.
        chunks: Retrieved document chunks from vector search.

    Returns:
        EvidenceAnalysis with all classifications and grounding notes.
    """
    if not chunks or any("Exact citation unavailable" in str(getattr(c, "content", "")) for c in chunks):
        return EvidenceAnalysis(
            overall_strength="MISSING",
            classifications=[],
            no_evidence_count=0,
            grounding_notes="No document chunks were retrieved or no actual citation is present. The model must state that no evidence was found.",
        )

    classifications = []
    query_lower = query.lower()

    for i, chunk in enumerate(chunks):
        classification = _classify_chunk(i, chunk, query_lower)
        classifications.append(classification)

    # Count by category
    explicit_count = sum(1 for c in classifications if c.strength == "EXPLICIT")
    implied_count = sum(1 for c in classifications if c.strength == "IMPLICIT")
    partial_count = sum(1 for c in classifications if c.strength == "ADJACENT")
    no_evidence_count = sum(1 for c in classifications if c.strength == "MISSING")

    # Determine overall strength (most conservative realistic assessment)
    overall_strength = _determine_overall_strength(
        explicit_count, implied_count, partial_count, no_evidence_count, len(classifications)
    )

    # Assess compliance maturity signals across all chunks
    all_content = " ".join(c.content.lower() for c in chunks)
    maturity_assessment = _assess_maturity(all_content)

    # Identify coverage gaps
    coverage_gaps = _identify_gaps(query_lower, all_content, maturity_assessment)

    # Build grounding notes for prompt builder
    grounding_notes = _build_grounding_notes(
        overall_strength, explicit_count, implied_count, partial_count,
        maturity_assessment, coverage_gaps
    )

    analysis = EvidenceAnalysis(
        overall_strength=overall_strength,
        classifications=classifications,
        explicit_count=explicit_count,
        implied_count=implied_count,
        partial_count=partial_count,
        no_evidence_count=no_evidence_count,
        maturity_assessment=maturity_assessment,
        coverage_gaps=coverage_gaps,
        grounding_notes=grounding_notes,
    )

    logger.info(
        "Evidence analysis: overall=%s, explicit=%d, implied=%d, partial=%d, none=%d",
        overall_strength, explicit_count, implied_count, partial_count, no_evidence_count
    )

    return analysis


def format_evidence_analysis(analysis: EvidenceAnalysis) -> str:
    """
    Format the evidence analysis into a text block for prompt injection.

    This block goes between the retrieved context and the LLM instructions,
    giving the model explicit guidance on what evidence exists and what is missing.
    """
    lines = [
        "═══ EVIDENCE ANALYSIS (pre-computed — DO NOT override) ═══",
        f"Overall Evidence Strength: {analysis.overall_strength}",
        f"Chunks Analyzed: {len(analysis.classifications)}",
        f"  Explicit Evidence: {analysis.explicit_count}",
        f"  Strongly Implied: {analysis.implied_count}",
        f"  Partial Evidence: {analysis.partial_count}",
        f"  No Evidence: {analysis.no_evidence_count}",
        "",
    ]

    if analysis.maturity_assessment:
        lines.append(f"Compliance Maturity: {analysis.maturity_assessment}")
        lines.append("")

    if analysis.coverage_gaps:
        lines.append("Coverage Gaps Detected:")
        for gap in analysis.coverage_gaps:
            lines.append(f"  [!] {gap}")
        lines.append("")

    # Per-chunk classifications
    lines.append("Per-Source Classification:")
    for c in analysis.classifications:
        maturity_str = f" [{', '.join(c.maturity_signals)}]" if c.maturity_signals else ""
        lines.append(
            f"  Source {c.chunk_index + 1} ({c.filename}, p.{c.page_number}): "
            f"{c.strength} (score={c.score:.2f}){maturity_str}"
        )

    lines.append("")
    lines.append(f"GROUNDING INSTRUCTIONS: {analysis.grounding_notes}")
    lines.append("═══ END EVIDENCE ANALYSIS ═══")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════════

def _classify_chunk(
    index: int,
    chunk: SearchResult,
    query_lower: str,
) -> EvidenceClassification:
    """Classify a single chunk's evidence strength."""
    content_lower = chunk.content.lower()
    score = chunk.score

    # Determine evidence strength from retrieval score
    if score >= EXPLICIT_THRESHOLD:
        strength = "EXPLICIT"
    elif score >= IMPLIED_THRESHOLD:
        strength = "IMPLICIT"
    elif score >= PARTIAL_THRESHOLD:
        strength = "ADJACENT"
    else:
        strength = "MISSING"

    # Extract key sentences (sentences containing query keywords)
    key_quotes = _extract_key_quotes(chunk.content, query_lower)

    # Detect compliance maturity signals
    maturity_signals = _detect_maturity_signals(content_lower)

    # Downgrade if content is only policy-level but query asks about implementation/testing
    if _query_asks_operational(query_lower) and maturity_signals == ["policy_exists"]:
        if strength == "EXPLICIT":
            strength = "IMPLICIT"
        elif strength == "IMPLICIT":
            strength = "ADJACENT"

    return EvidenceClassification(
        chunk_index=index,
        filename=chunk.filename,
        page_number=chunk.page_number,
        score=score,
        strength=strength,
        content_preview=chunk.content[:200],
        maturity_signals=maturity_signals,
        key_quotes=key_quotes[:3],  # Max 3 quotes per chunk
    )


def _determine_overall_strength(
    explicit: int, implied: int, partial: int, none: int, total: int
) -> str:
    """Determine the aggregate evidence strength — conservative assessment."""
    if total == 0:
        return "MISSING"
    if explicit >= 2:
        return "EXPLICIT"
    if explicit >= 1 and implied >= 1:
        return "EXPLICIT"
    if explicit >= 1:
        return "IMPLICIT"
    if implied >= 2:
        return "IMPLICIT"
    if implied >= 1:
        return "ADJACENT"
    if partial >= 1:
        return "ADJACENT"
    return "MISSING"


def _assess_maturity(all_content: str) -> str:
    """Assess what compliance maturity levels are evidenced across all chunks."""
    signals = []

    has_policy = any(kw in all_content for kw in POLICY_EXISTENCE_KEYWORDS)
    has_impl = any(kw in all_content for kw in IMPLEMENTATION_KEYWORDS)
    has_test = any(kw in all_content for kw in TESTING_KEYWORDS)
    has_audit = any(kw in all_content for kw in AUDIT_VERIFICATION_KEYWORDS)

    if has_policy:
        signals.append("Policy documented")
    if has_impl:
        signals.append("Implementation evidence")
    if has_test:
        signals.append("Testing/verification mentioned")
    if has_audit:
        signals.append("Audit/certification evidence")

    if not signals:
        return "No compliance maturity signals detected"

    return " -> ".join(signals)


def _identify_gaps(query_lower: str, all_content: str, maturity: str) -> List[str]:
    """Identify coverage gaps between what's asked and what's evidenced."""
    gaps = []

    # Check if query asks about things not evidenced
    if any(w in query_lower for w in ["frequency", "how often", "schedule", "periodic"]):
        if not any(w in all_content for w in ["monthly", "quarterly", "annually", "weekly", "daily", "periodic"]):
            gaps.append("Review/audit frequency not specified in retrieved evidence")

    if any(w in query_lower for w in ["owner", "responsible", "accountability", "who"]):
        if not any(w in all_content for w in ["responsible", "owner", "accountable", "role", "assigned"]):
            gaps.append("Ownership/accountability not explicitly assigned in retrieved evidence")

    if any(w in query_lower for w in ["test", "tested", "verify", "validated"]):
        if "Testing/verification" not in maturity:
            gaps.append("No testing or verification evidence found — only policy may exist")

    if any(w in query_lower for w in ["certif", "attest", "audit report"]):
        if "Audit/certification" not in maturity:
            gaps.append("No audit or certification evidence found")

    if any(w in query_lower for w in ["effective", "effectiveness", "operating"]):
        if "Implementation" not in maturity:
            gaps.append("No evidence of operational effectiveness -- policy existence does not equal implementation proof")

    return gaps


def _extract_key_quotes(content: str, query_lower: str) -> List[str]:
    """Extract sentences from chunk content that are most relevant to the query."""
    # Split into sentences
    sentences = re.split(r'[.!?]\s+', content)
    query_words = set(query_lower.split()) - {"is", "are", "the", "a", "an", "and", "or", "of", "in", "to", "for", "does", "do", "has", "have", "what", "how"}

    scored = []
    for s in sentences:
        s = s.strip()
        if len(s) < 15:
            continue
        s_lower = s.lower()
        overlap = sum(1 for w in query_words if w in s_lower)
        if overlap > 0:
            scored.append((overlap, s[:200]))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored]


def _detect_maturity_signals(content_lower: str) -> List[str]:
    """Detect which compliance maturity levels are evidenced in a single chunk."""
    signals = []
    if any(kw in content_lower for kw in POLICY_EXISTENCE_KEYWORDS[:6]):
        signals.append("policy_exists")
    if any(kw in content_lower for kw in IMPLEMENTATION_KEYWORDS[:6]):
        signals.append("implemented")
    if any(kw in content_lower for kw in TESTING_KEYWORDS[:6]):
        signals.append("tested")
    if any(kw in content_lower for kw in AUDIT_VERIFICATION_KEYWORDS[:4]):
        signals.append("audit_verified")
    return signals if signals else ["policy_exists"]


def _query_asks_operational(query_lower: str) -> bool:
    """Check if the query is asking about operational effectiveness, not just policy existence."""
    operational_keywords = [
        "implemented", "tested", "effective", "operating", "verified",
        "monitored", "enforced", "in practice", "in production",
        "evidence of", "proof of", "demonstrate",
    ]
    return any(kw in query_lower for kw in operational_keywords)


def _build_grounding_notes(
    overall_strength: str,
    explicit_count: int,
    implied_count: int,
    partial_count: int,
    maturity_assessment: str,
    coverage_gaps: List[str],
) -> str:
    """
    Build natural-language grounding instructions for the LLM prompt.

    These instructions tell the model what it CAN and CANNOT claim
    based on the pre-computed evidence analysis.
    """
    notes = []

    # Strength-based instructions
    if overall_strength == "MISSING":
        notes.append(
            "No relevant evidence was found. You MUST respond with "
            "'Not found in uploaded documents.' Do NOT attempt to answer."
        )
        return " ".join(notes)

    if overall_strength == "EXPLICIT":
        notes.append(
            f"Strong evidence available ({explicit_count} explicit match(es)). "
            f"Cite the exact source text. Do not overstate beyond what is quoted."
        )
    elif overall_strength == "IMPLICIT":
        notes.append(
            f"Evidence is implied but not directly stated ({implied_count} implied match(es)). "
            f"Use cautious language. Separate what is stated from what is inferred."
        )
    elif overall_strength == "ADJACENT":
        notes.append(
            f"Only partial evidence exists ({partial_count} partial match(es)). "
            f"Clearly state what IS evidenced and what is MISSING. Prefer 'Partial' verdict."
        )

    # Maturity-based constraints
    if "Policy documented" in maturity_assessment and "Implementation" not in maturity_assessment:
        notes.append(
            "IMPORTANT: Only POLICY documentation was found. Do NOT claim the control is "
            "'implemented' or 'operating effectively' -- policy existence does not equal implementation proof."
        )

    if coverage_gaps:
        gap_str = "; ".join(coverage_gaps)
        notes.append(f"Known gaps: {gap_str}. Acknowledge these gaps in your response.")

    return " ".join(notes)


# ═══════════════════════════════════════════════════════════════════════
# DETERMINISTIC VERDICT + CONFIDENCE NORMALIZATION
# Single source of truth — no LLM discretion allowed.
# ═══════════════════════════════════════════════════════════════════════

# Canonical mapping tables (read-only, never modified at runtime)
_STRENGTH_TO_VERDICT = {
    "EXPLICIT": "Yes",
    "IMPLICIT": "Partial",
    "ADJACENT": "Partial",
    "MISSING":  "No Evidence",
}

_STRENGTH_TO_CONFIDENCE = {
    "EXPLICIT": "High",
    "IMPLICIT": "Medium",
    "ADJACENT": "Medium",
    "MISSING":  "Low",
}

# Allowed output values (used for validation)
VALID_VERDICTS    = {"Yes", "Partial", "No", "No Evidence"}
VALID_CONFIDENCES = {"High", "Medium", "Low"}
VALID_STRENGTHS   = {"EXPLICIT", "IMPLICIT", "ADJACENT", "MISSING"}


def normalize_verdict_and_confidence(evidence_strength: str) -> tuple:
    """
    Return (verdict, confidence) deterministically from evidence_strength.

    This is the SINGLE SOURCE OF TRUTH for verdict and confidence.
    Neither the LLM nor any other code path may override these values.

    Args:
        evidence_strength: One of the four canonical strength labels.

    Returns:
        (verdict, confidence) — both guaranteed to be in the allowed value sets.
    """
    # Normalise input defensively (strip, title-case check)
    strength = evidence_strength.strip() if evidence_strength else "MISSING"
    if strength not in VALID_STRENGTHS:
        logger.warning("Unrecognised evidence_strength '%s' — defaulting to 'MISSING'", strength)
        strength = "MISSING"

    verdict    = _STRENGTH_TO_VERDICT[strength]
    confidence = _STRENGTH_TO_CONFIDENCE[strength]

    logger.debug("Normalised: strength=%s -> verdict=%s, confidence=%s", strength, verdict, confidence)
    return verdict, confidence


def build_prefilled_template(
    query: str,
    evidence_strength: str,
    verdict: str,
    confidence: str,
) -> str:
    """
    Build a rigid output scaffold that the LLM must complete.

    The EVIDENCE STRENGTH, FINAL VERDICT, and CONFIDENCE fields are
    pre-filled with deterministic values so the LLM cannot deviate.
    The LLM only fills SOURCE EVIDENCE and AI INTERPRETATION.

    Args:
        query:            The user's compliance question.
        evidence_strength: Pre-computed strength label.
        verdict:           Pre-computed verdict (from normalize_verdict_and_confidence).
        confidence:        Pre-computed confidence (from normalize_verdict_and_confidence).

    Returns:
        A string template with locked fields and blank fields for the LLM to fill.
    """
    return (
        f"QUESTION:\n{query}\n\n"
        f"EVIDENCE STRENGTH:\n{evidence_strength}\n\n"
        f"AI INTERPRETATION:\n"
        f"[Write a conservative compliance interpretation. "
        f"Use words such as 'suggests', 'indicates', 'partially demonstrates', "
        f"'not explicitly confirmed', 'appears consistent with'. "
        f"Explicitly state what the evidence does NOT show. "
        f"Distinguish policy existence from implementation or tested effectiveness. "
        f"Do NOT use: 'fully compliant', 'guarantees', 'ensures', 'confirmed' "
        f"unless directly proven by the retrieved text.]\n\n"
        f"SOURCE EVIDENCE:\n"
        f"[List ONLY citations from the retrieved sources above. "
        f"Each entry: '- \"exact quote\" | File: <filename> | Page: <page_number>'. "
        f"If page number is not in the retrieved metadata, write 'Page: not available in retrieved evidence'. "
        f"Do NOT guess or invent page numbers.]\n\n"
        f"FINAL VERDICT:\n{verdict}\n\n"
        f"CONFIDENCE:\n{confidence}\n\n"
        f"GAP ANALYSIS:\n"
        f"[1-2 sentences: what specific document, control, or action is needed "
        f"to fully satisfy this requirement. If Compliant, write: No gap identified.]"
    )
