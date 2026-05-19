"""
Compliance Intelligence Layer — Post-retrieval enhancement layer.

Implements:
1. Requirement Decomposition
2. Evidence Strength Classification (EXPLICIT, IMPLICIT, ADJACENT, MISSING)
3. Hard Compliance Keyword Rules
4. Gap Analysis Engine (PROVEN vs NOT PROVEN)
5. Contradiction Detection
6. Deterministic Verdict Engine
7. Audit Defensibility Scoring
8. Evidence Sufficiency
9. Compliance Risk Indicator
10. Confidence Calibration
"""

import re
from typing import List, Dict, Any, Tuple
from models.schemas import SearchResult

# Hard compliance keywords requiring EXPLICIT evidence
HARD_COMPLIANCE_KEYWORDS = [
    "documented", "approved", "signed", "reviewed", "retained", 
    "assigned", "monitored", "tracked", "periodic", "formal"
]

# Uncertainty phrases for contradiction detection
UNCERTAINTY_PHRASES = [
    "not explicitly stated", "appears", "inferred", 
    "suggests", "likely", "partially evidenced"
]

def evaluate_compliance(query: str, chunks: List[SearchResult]) -> Dict[str, Any]:
    """
    Evaluates compliance evidence based on strict rules.
    This acts as the deterministic intelligence layer.
    """
    # 1. Requirement Decomposition
    attributes = _decompose_requirements(query)
    
    # 2. Evaluate Evidence for each attribute
    proven_attributes = []
    missing_attributes = []
    
    all_content = " ".join([c.content.lower() for c in chunks])
    
    for attr in attributes:
        # Simple heuristic: check if attribute keywords are present in evidence
        # A more advanced NLP approach could be used, but rule-based string matching works as a baseline
        if _is_attribute_proven(attr, all_content):
            proven_attributes.append(attr)
        else:
            missing_attributes.append(attr)
            
    # 3. Evidence Strength Classification
    classification = _classify_evidence(proven_attributes, missing_attributes, len(attributes), chunks)
    
    # 4. Hard Compliance Keyword Rules
    query_lower = query.lower()
    requires_explicit = any(kw in query_lower for kw in HARD_COMPLIANCE_KEYWORDS)
    if requires_explicit and classification in ["IMPLICIT", "ADJACENT"]:
        # If it requires explicit but we only have inferred evidence, downgrade
        classification = "ADJACENT" # Downgrade to prevent compliant verdict
        missing_attributes.append("explicit_proof_required")
        
    # 5. Gap Analysis Engine
    gap_analysis = _generate_gap_analysis(proven_attributes, missing_attributes)
    
    # 6. Deterministic Verdict Engine
    verdict = _determine_verdict(classification, proven_attributes, missing_attributes, len(attributes))
    
    # 7. Audit Defensibility Scoring
    audit_defensibility = _score_defensibility(classification)
    
    # 8. Evidence Sufficiency
    evidence_sufficiency = _score_sufficiency(classification, verdict)
    
    # 9. Compliance Risk Indicator
    compliance_risk = _assess_risk(missing_attributes, classification, query_lower)
    
    # 10. Confidence Calibration
    confidence = _calibrate_confidence(classification)
    
    return {
        "question": query,
        "requirement_attributes": attributes,
        "evidence": [c.content[:200] + "..." for c in chunks],
        "evidence_classification": classification,
        "proven_attributes": proven_attributes,
        "missing_attributes": missing_attributes,
        "gap_analysis": gap_analysis,
        "audit_defensibility": audit_defensibility,
        "evidence_sufficiency": evidence_sufficiency,
        "compliance_risk": compliance_risk,
        "verdict": verdict,
        "confidence": confidence,
        "reasoning": gap_analysis # The normalizer will also use LLM reasoning, this is a deterministic baseline
    }

def _decompose_requirements(query: str) -> List[str]:
    """Break requirement into atomic attributes using heuristic rules."""
    # Remove question words
    q = re.sub(r'^(is there a|are there|does the|do you|how does|what is)\s+', '', query.lower())
    
    # Split by common conjunctions to get attributes
    parts = re.split(r'\s+and\s+|\s+that is\s+|,\s*', q)
    
    attributes = []
    for p in parts:
        p = p.strip()
        if not p or p in ["a", "the", "an"]: continue
        attributes.append(p)
        
    # Extract hard keywords as explicit attributes if present
    for kw in HARD_COMPLIANCE_KEYWORDS:
        if kw in query.lower() and not any(kw in a for a in attributes):
            attributes.append(kw)
            
    if not attributes:
        attributes = [query.lower().strip("? ")]
        
    return attributes

def _is_attribute_proven(attribute: str, all_content: str) -> bool:
    """Check if an attribute is supported by the content."""
    # Simple keyword overlap scoring
    words = [w for w in attribute.split() if len(w) > 3]
    if not words:
        return attribute in all_content
        
    match_count = sum(1 for w in words if w in all_content)
    # If more than 50% of significant words match, consider it proven (heuristic)
    return (match_count / len(words)) >= 0.5 if words else False

def _classify_evidence(proven: List[str], missing: List[str], total: int, chunks: List[SearchResult]) -> str:
    if total == 0 or not chunks:
        return "MISSING"
        
    if len(missing) == 0:
        # Check if chunks have high scores (explicit)
        if any(c.score > 0.70 for c in chunks):
            return "EXPLICIT"
        return "IMPLICIT"
        
    if len(proven) > 0:
        if len(proven) >= len(missing):
            return "IMPLICIT"
        return "ADJACENT"
        
    return "MISSING"

def _generate_gap_analysis(proven: List[str], missing: List[str]) -> str:
    lines = []
    if proven:
        lines.append("PROVEN: " + ", ".join(proven))
    if missing:
        lines.append("NOT PROVEN: " + ", ".join(missing))
    if not proven and not missing:
        lines.append("No specific attributes could be evaluated.")
    return "\n".join(lines)

def _determine_verdict(classification: str, proven: List[str], missing: List[str], total: int) -> str:
    if classification == "EXPLICIT" and len(missing) == 0:
        return "Compliant"
    elif classification in ["IMPLICIT", "ADJACENT"] or (len(proven) > 0 and len(missing) > 0):
        # Implicit/Adjacent cannot become Compliant based on rule 5 & 6
        return "Partial"
    elif len(proven) == 0 or classification == "MISSING":
        return "Non-Compliant"
    return "Partial"

def _score_defensibility(classification: str) -> str:
    if classification == "EXPLICIT": return "STRONG"
    if classification == "IMPLICIT": return "MEDIUM"
    return "WEAK"

def _score_sufficiency(classification: str, verdict: str) -> str:
    if classification == "EXPLICIT": return "Sufficient"
    if classification == "IMPLICIT" or verdict == "Partial": return "Partially Sufficient"
    return "Insufficient"

def _assess_risk(missing: List[str], classification: str, query_lower: str) -> str:
    missing_str = " ".join(missing)
    if "approv" in missing_str or "sign" in missing_str or "policy" in missing_str:
        return "HIGH"
    if classification == "MISSING":
        return "HIGH"
    if classification == "ADJACENT" or "frequenc" in missing_str or "period" in missing_str:
        return "MEDIUM"
    if classification == "EXPLICIT":
        return "LOW"
    return "MEDIUM"

def _calibrate_confidence(classification: str) -> str:
    if classification == "EXPLICIT": return "0.85-0.98"
    if classification == "IMPLICIT": return "0.60-0.80"
    if classification == "ADJACENT": return "0.40-0.65"
    return "0.10-0.35"

def detect_contradictions_in_reasoning(reasoning: str, current_verdict: str) -> str:
    """Auto-downgrade verdict if reasoning contains uncertainty phrases."""
    reasoning_lower = reasoning.lower()
    has_uncertainty = any(phrase in reasoning_lower for phrase in UNCERTAINTY_PHRASES)
    
    if has_uncertainty and current_verdict == "Compliant":
        return "Partial"
    return current_verdict
