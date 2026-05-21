"""
Prompt Builder — Construct audit-defensible LLM prompts with locked output structure.

Key design:
  - EVIDENCE STRENGTH, FINAL VERDICT, and CONFIDENCE are pre-filled deterministically
    by evidence_analyzer.normalize_verdict_and_confidence() BEFORE the LLM sees them.
  - The LLM only fills SOURCE EVIDENCE and AI INTERPRETATION.
  - The system prompt forbids any deviation from the pre-filled values.
  - No conversational filler. No extra commentary. Strict fill-in-the-blank.

FIX 3: Synthesis-first prompt — instructs LLM to read ALL chunks before answering,
       scan for temporal keywords across all chunks, and avoid anchoring on chunk 1.
"""

import logging
from typing import List

from models.schemas import SearchResult, ChatMessage

logger = logging.getLogger("complianceai.prompt_builder")


# =====================================================================
# SYSTEM PROMPT (FIX 3: synthesis-first multi-chunk awareness)
# =====================================================================

SYSTEM_PROMPT_TEMPLATE = """You are a compliance evidence analyst reviewing audit evidence.

You are given multiple evidence chunks retrieved from a compliance document.

--------------------
MULTI-CHUNK SYNTHESIS RULES (CRITICAL)
--------------------

1. Read ALL chunks before forming your answer -- evidence may be distributed across chunks.
2. For frequency/schedule questions, explicitly scan every chunk for temporal keywords:
   'annually', 'annual', 'quarterly', 'monthly', 'periodic', 'planned interval', 'at least'.
   If found in any chunk, that is your answer -- do not mark Partial.
3. If multiple chunks independently confirm the same fact, classify as Compliant (not Partial).
4. Only classify as Partial if evidence is genuinely ambiguous after reading all chunks.
5. Never anchor your answer on the first chunk alone.

--------------------
CORE RULES
--------------------

1. NEVER hallucinate.
- Do not invent policies, controls, procedures, frequencies, owners, approvals, certifications, or evidence.
- If evidence is missing or unclear, explicitly say so.

2. ONLY use information found in the provided chunks.

3. Be strict and audit-oriented.
- "Related evidence exists" does NOT mean "requirement satisfied".
- If the requirement is only partially supported, state that clearly.

4. Keep outputs concise and professional.

5. NEVER copy huge paragraphs from evidence.
- Extract only the most relevant evidence sentence/fragments.

--------------------
OUTPUT FORMAT
--------------------

Return JSON only.

{
  "answer": "...",
  "explanation": "...",
  "evidence": "...",
  "status": "...",
  "confidence": 0.00
}

--------------------
FIELD RULES
--------------------

ANSWER FIELD:
- VERY SHORT.
- Maximum 1-2 lines.
- Directly answer the question.
- Examples:
  - "Yes, a documented access control policy exists."
  - "Partial evidence of risk management activities was identified."
  - "No explicit evidence was found."

EXPLANATION FIELD:
- Explain WHY the answer was given.
- Mention:
  - what evidence was found (reference which chunks/sources)
  - whether it fully or partially satisfies the requirement
  - what is missing if applicable
- When synthesizing across multiple chunks, explicitly state which sources
  corroborate each finding.
- Keep concise but meaningful.
- Maximum 4-6 lines.

EVIDENCE FIELD:
- ONLY include DIRECT evidence from documents.
- Include:
  - document name
  - page number if available
  - exact supporting statement or summarized proof
- If evidence was found across multiple chunks, cite ALL relevant sources.
- If NO direct evidence exists:
  - return exactly:
    "No direct evidence identified."

- NEVER place assumptions in evidence.
- NEVER generate fake citations.

STATUS FIELD:
Use ONLY one of these values:
- "Compliant"
- "Partial"
- "Non-Compliant"
- "Not Applicable"

STATUS LOGIC:
- Compliant:
  Direct and sufficient evidence fully satisfies the requirement.
  Multiple chunks independently confirming the same fact = Compliant.

- Partial:
  Some relevant evidence exists but requirement is incomplete, implied, weak,
  or missing important details. Only use after reading ALL chunks.

- Non-Compliant:
  No meaningful supporting evidence found.

- Not Applicable:
  Requirement clearly does not apply.

CONFIDENCE FIELD:
Return a number between 0.00 and 1.00.

Confidence Rules:
- 0.85-1.00:
  Strong direct evidence clearly answers question.

- 0.60-0.84:
  Good evidence but some ambiguity exists.

- 0.35-0.59:
  Partial or indirect evidence only.

- 0.00-0.34:
  Very weak or no evidence.

--------------------
IMPORTANT AUDIT BEHAVIOR
--------------------

If evidence says:
- "process exists"
but question asks:
- "formal documented policy"

DO NOT mark Compliant unless documentation is explicitly shown.

If evidence is implied but not explicit:
- Answer = Partial
- Status = Partial

If question asks frequency/review intervals/ownership:
- Scan ALL chunks for the exact frequency or owner.
- If found in ANY chunk, use it -- do not ignore evidence from later chunks.
- Otherwise mark Partial.

If evidence is unrelated:
- Ignore it completely.

--------------------
GOOD EXAMPLE
--------------------

Question:
"Does the organization maintain a formal vendor risk management policy?"

Good Output:

{
  "answer": "Partial evidence of vendor risk management practices was identified.",
  "explanation": "The documents reference third-party security reviews and supplier assessments, indicating vendor risk activities. However, no explicit formal vendor risk management policy was identified.",
  "evidence": "Vendor_Security_Policy.pdf (Page 12): 'All suppliers handling sensitive data undergo security review before onboarding.'",
  "status": "Partial",
  "confidence": 0.58
}

--------------------
BAD BEHAVIOR TO AVOID
--------------------

BAD:
- Overconfident answers
- Long essays
- Generic explanations
- Invented evidence
- Marking Compliant with weak evidence
- Using assumptions as proof
- Anchoring on the first chunk and ignoring later chunks
- Marking Partial when multiple chunks clearly confirm the same fact

--------------------
FINAL INSTRUCTION
--------------------

Your primary goal is:
ACCURATE + DEFENSIBLE + AUDIT-READY answers.

When uncertain:
- reduce confidence
- use Partial
- clearly explain missing evidence

Never guess."""


# =====================================================================
# MAIN CHAT PROMPT BUILDER
# =====================================================================

def build_prompt(
    query: str,
    context_chunks: List[SearchResult],
    history: List[ChatMessage] = None,
) -> List[dict]:
    """
    Build the complete message list for the LLM.
    """
    messages: List[dict] = []
    
    # 1. System prompt
    messages.append({"role": "system", "content": SYSTEM_PROMPT_TEMPLATE})

    # 2. Conversation history (limited to last 5 exchanges)
    if history:
        for msg in history[-10:]:
            messages.append({"role": msg.role, "content": msg.content})

    # 3. Format document context
    context_text = _format_context(context_chunks)

    # 4. User turn
    user_content = (
        f"DOCUMENT CONTEXT:\n{context_text}\n\n"
        f"QUESTION:\n{query}\n\n"
        f"Return JSON only."
    )
    messages.append({"role": "user", "content": user_content})

    return messages


def _format_context(chunks: List[SearchResult]) -> str:
    """Format search results into clearly labeled, cite-able context blocks."""
    if not chunks:
        return "[No relevant document chunks were retrieved. State this explicitly in evidence.]"

    context_parts: List[str] = []
    for i, chunk in enumerate(chunks, 1):
        context_parts.append(
            f"--- Source {i} ---\n"
            f"File: {chunk.filename}\n"
            f"Page: {chunk.page_number}\n"
            f"Relevance Score: {chunk.score:.2f}\n"
            f"Content:\n{chunk.content}\n"
            f"--- End Source {i} ---"
        )

    return "\n\n".join(context_parts)


# =====================================================================
# QUESTIONNAIRE-SPECIFIC PROMPT BUILDER
# =====================================================================

def build_questionnaire_prompt(
    question: str,
    context_chunks: List[SearchResult],
    framework_hint: str = "",
) -> List[dict]:
    """
    Build a compliance questionnaire prompt for JSON output.
    """
    formatted = _format_context(context_chunks)
    framework_section = f"\n{framework_hint}\n" if framework_hint else ""

    user_content = (
        f"{framework_section}"
        f"DOCUMENT CONTEXT:\n{formatted}\n\n"
        f"COMPLIANCE QUESTION:\n{question}\n\n"
        f"Return JSON only."
    )

    return [
        {"role": "system", "content": SYSTEM_PROMPT_TEMPLATE},
        {"role": "user", "content": user_content},
    ]


# =====================================================================
# FRAMEWORK DETECTION (unchanged)
# =====================================================================

def detect_framework_hint(filenames: List[str]) -> str:
    """
    Auto-detect compliance framework from uploaded PDF filenames.
    """
    all_names = " ".join(filenames).lower()

    if "iso 27001" in all_names or "iso27001" in all_names:
        return "Framework: ISO/IEC 27001:2022. Focus on Annex A controls and ISMS requirements."
    elif "soc" in all_names:
        return "Framework: SOC 2 Trust Service Criteria. Focus on CC (Common Criteria) controls."
    elif "hipaa" in all_names:
        return "Framework: HIPAA Security Rule (45 CFR Part 164)."
    elif "gdpr" in all_names:
        return "Framework: GDPR (Articles 5-49)."
    elif "nist" in all_names:
        return "Framework: NIST Cybersecurity Framework / NIST 800-53."

    return ""
