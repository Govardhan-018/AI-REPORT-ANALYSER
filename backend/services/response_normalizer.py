import logging
import re
from typing import Tuple, Optional

logger = logging.getLogger("complianceai.response_normalizer")

def normalize_response(
    llm_output: str,
    original_query: str,
    precomputed_evidence_strength: str,
    precomputed_verdict: str,
    precomputed_confidence: str
) -> dict:
    """
    Deterministic backend validation layer AFTER LLM generation.
    Ensures the final output adheres strictly to the required schema,
    downgrades contradictory claims, and enforces exact evidence-to-verdict mapping.
    """
    # 1. Strict Evidence -> Verdict Mapping Rules
    # Override any precomputed verdicts if they violate the strict rules
    final_verdict = precomputed_verdict
    final_confidence = precomputed_confidence
    
    strength_upper = precomputed_evidence_strength.upper()
    if "EXPLICIT" in strength_upper:
        final_verdict = "Compliant" if final_verdict not in ["Partial", "Non-Compliant"] else final_verdict
        final_confidence = precomputed_confidence if precomputed_confidence else "0.85-0.98"
    elif "IMPLICIT" in strength_upper:
        final_verdict = "Partial" if final_verdict not in ["Non-Compliant"] else final_verdict
        final_confidence = precomputed_confidence if precomputed_confidence else "0.60-0.80"
    elif "ADJACENT" in strength_upper:
        final_verdict = "Partial" if final_verdict not in ["Non-Compliant"] else final_verdict
        final_confidence = precomputed_confidence if precomputed_confidence else "0.40-0.65"
    elif "MISSING" in strength_upper or "NO EVIDENCE" in strength_upper:
        final_verdict = "Non-Compliant"
        final_confidence = precomputed_confidence if precomputed_confidence else "0.10-0.35"

    # 2. Extract sections from LLM output
    sections = {
        "QUESTION": original_query,
        "EVIDENCE STRENGTH": precomputed_evidence_strength,
        "SOURCE EVIDENCE": "Exact citation unavailable from retrieved evidence.",
        "AI INTERPRETATION": "",
        "FINAL VERDICT": final_verdict,
        "CONFIDENCE": final_confidence,
        "GAP ANALYSIS": "",
    }

    # Extract source evidence
    source_match = re.search(r"SOURCE EVIDENCE:\s*(.*?)(?=\n[A-Z\s]+:|$)", llm_output, re.IGNORECASE | re.DOTALL)
    if source_match:
        source_text = source_match.group(1).strip()
        if source_text:
            # Validate citations: Must have some citation form or be flagged
            if "Page:" not in source_text and "File:" not in source_text and "Source" not in source_text:
                sections["SOURCE EVIDENCE"] = "Exact citation unavailable from retrieved evidence.\n" + source_text
                # Bug 2 fix: contradiction
                sections["EVIDENCE STRENGTH"] = "MISSING"
            else:
                sections["SOURCE EVIDENCE"] = source_text
            
            # Additional safety for Bug 2
            if "Exact citation unavailable" in sections["SOURCE EVIDENCE"]:
                sections["EVIDENCE STRENGTH"] = "MISSING"

    # Extract AI Interpretation
    interp_match = re.search(r"AI INTERPRETATION:\s*(.*?)(?=\n[A-Z\s]+:|$)", llm_output, re.IGNORECASE | re.DOTALL)
    if interp_match:
        interp_text = interp_match.group(1).strip()
        # Bug 1 fix: strip markdown
        interp_text = re.sub(r'[*#]', '', interp_text).strip()
        if interp_text:
            # Enforce conservative language (rudimentary downgrading of unsupported claims)
            interp_text = re.sub(r'(?i)\bfully compliant\b', 'partially demonstrates compliance', interp_text)
            interp_text = re.sub(r'(?i)\bguarantees\b', 'suggests', interp_text)
            interp_text = re.sub(r'(?i)\bensures\b', 'indicates', interp_text)
            sections["AI INTERPRETATION"] = interp_text

    if not sections["AI INTERPRETATION"]:
         # Bug 1 fix: fallback phrase
         sections["AI INTERPRETATION"] = "Insufficient evidence found in the uploaded documents to answer this question."

    # Enforce NO EVIDENCE rules:
    # If verdict is Partial but interpretation says "no evidence", correct it.
    if final_verdict == "Partial" and "no evidence" in sections["AI INTERPRETATION"].lower():
        sections["AI INTERPRETATION"] = sections["AI INTERPRETATION"].replace("no evidence", "partial evidence")
        sections["AI INTERPRETATION"] = sections["AI INTERPRETATION"].replace("No evidence", "Partial evidence")
        sections["AI INTERPRETATION"] = sections["AI INTERPRETATION"].replace("No Evidence", "Partial Evidence")
        sections["AI INTERPRETATION"] += " Some periodic or related activity is referenced, but exact requirements may not be fully specified."

    # Extract GAP ANALYSIS
    gap_match = re.search(r"GAP ANALYSIS:\s*(.*?)(?=\n[A-Z\s]+:|$)", llm_output, re.IGNORECASE | re.DOTALL)
    if gap_match:
        sections["GAP ANALYSIS"] = re.sub(r'[*#]', '', gap_match.group(1)).strip()

    # Create short answer and explanation
    short_ans = extract_short_answer(llm_output, final_verdict)
    expl = extract_explanation(llm_output)

    # Sanitize fields
    sections["SHORT_ANSWER"] = sanitize_answer(short_ans)
    sections["EXPLANATION"] = sanitize_answer(expl)
    sections["GAP ANALYSIS"] = sanitize_answer(sections["GAP ANALYSIS"])

    return sections

def extract_short_answer(raw_response: str, status: str) -> str:
    """Extract a short 1-sentence answer based on verdict and interpretation."""
    opening = "Unable to determine"
    if status == "Compliant":
        opening = "Yes"
    elif status == "Non-Compliant":
        opening = "No"
    elif status == "Partial":
        opening = "Partially"

    interp_match = re.search(r"AI INTERPRETATION:\s*(.*?)(?=\n[A-Z\s]+:|$)", raw_response, re.IGNORECASE | re.DOTALL)
    interp_text = "no interpretation provided."
    if interp_match:
        text = interp_match.group(1).strip()
        text = re.sub(r'[*#]', '', text).strip()
        if text:
            # Get first sentence
            sentences = re.split(r'(?<=[.!?])\s+', text)
            first_sentence = sentences[0] if sentences else text
            
            # Condense to max 25 words
            words = first_sentence.split()
            if len(words) > 25:
                first_sentence = " ".join(words[:25]) + "..."
            interp_text = first_sentence

    if interp_text and interp_text[0].isalpha():
        interp_text = interp_text[0].lower() + interp_text[1:]

    return f"{opening} — {interp_text}"

def extract_explanation(raw_response: str) -> str:
    """Extract full detailed interpretation with source evidence."""
    interp_match = re.search(r"AI INTERPRETATION:\s*(.*?)(?=\n[A-Z\s]+:|$)", raw_response, re.IGNORECASE | re.DOTALL)
    source_match = re.search(r"SOURCE EVIDENCE:\s*(.*?)(?=\n[A-Z\s]+:|$)", raw_response, re.IGNORECASE | re.DOTALL)
    
    explanation = ""
    if interp_match:
        text = interp_match.group(1).strip()
        text = re.sub(r'[*#]', '', text)
        text = re.sub(r'\n{3,}', '\n\n', text).strip()
        explanation += text
        
    if source_match:
        text = source_match.group(1).strip()
        if text and text != "Exact citation unavailable from retrieved evidence.":
            if explanation:
                explanation += "\n\n"
            explanation += "Source Evidence:\n" + text
            
    # Max 1000 characters
    if len(explanation) > 1000:
        explanation = explanation[:960] + "... [see full evidence in source document]"
        
    return explanation

def sanitize_answer(text: str) -> str:
    """Strip scaffold labels and fields from text."""
    if not text:
        return text
    text = re.sub(r'FINAL VERDICT:.*?(?=\n[A-Z\s]+:|$)', '', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'CONFIDENCE:.*?(?=\n[A-Z\s]+:|$)', '', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'EVIDENCE STRENGTH:.*?(?=\n[A-Z\s]+:|$)', '', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'SOURCE EVIDENCE:.*?(?=\n[A-Z\s]+:|$)', '', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'QUESTION:.*?(?=\n[A-Z\s]+:|$)', '', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'GAP ANALYSIS:.*?(?=\n[A-Z\s]+:|$)', '', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'AI INTERPRETATION:\s*', '', text, flags=re.IGNORECASE)
    
    # Catch any remaining trailing blocks
    text = re.sub(r'FINAL VERDICT:.*', '', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'CONFIDENCE:.*', '', text, flags=re.IGNORECASE | re.DOTALL)
    
    return text.strip()
