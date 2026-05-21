"""
RAG Pipeline - Retrieval-Augmented Generation orchestrator with evidence analysis.

Flow:
  Query -> Embedding -> Vector Search -> Evidence Analysis -> Prompt Building -> LLM Inference

The evidence analysis layer classifies retrieved chunks BEFORE they reach the LLM,
ensuring audit-defensible responses with proper grounding.

FIX 4: Temporal evidence prioritization - chunks containing temporal keywords
       (annually, quarterly, monthly, etc.) are moved to the top of the context
       window so the LLM sees schedule/frequency evidence first.
"""

import json
import logging
import asyncio
import re
from typing import List, AsyncGenerator

from models.schemas import SearchResult, ChatMessage, Evidence, ChatResponse
from services.embeddings import embed_single
from services.vector_store import VectorStoreService
from services.prompt_builder import build_prompt
from services.evidence_analyzer import analyze_evidence, format_evidence_analysis
from services.compliance_intelligence import evaluate_compliance

logger = logging.getLogger("complianceai.rag_pipeline")

# FIX 2/4: Increased from 5 to 10 to match vector_store default
DEFAULT_TOP_K = 10
MIN_RELEVANCE_SCORE = 0.50  # nomic-embed-text typical cosine similarities range 0.5-0.6 for relevant matches

# FIX 4: Temporal keywords used to prioritize chunks in the context window
TEMPORAL_KEYWORDS = [
    "annually", "annual", "quarterly", "monthly", "periodic",
    "planned interval", "at least once", "at least",
    "semi-annual", "biannual", "weekly", "daily",
]
# Compile a single regex pattern for efficient matching
_TEMPORAL_PATTERN = re.compile(
    r'\b(?:' + '|'.join(re.escape(kw) for kw in TEMPORAL_KEYWORDS) + r')\b',
    re.IGNORECASE
)


def extract_temporal_evidence(chunks: List[SearchResult]) -> List[SearchResult]:
    """
    FIX 4: Filter and return chunks that contain temporal keywords.

    Used to identify chunks with schedule/frequency evidence so they can
    be moved to the top of the context window before prompt building.

    Args:
        chunks: List of SearchResult objects from vector search.

    Returns:
        List of SearchResult objects that contain temporal keywords.
    """
    temporal_chunks = []
    for chunk in chunks:
        if _TEMPORAL_PATTERN.search(chunk.content):
            temporal_chunks.append(chunk)
    return temporal_chunks


def _reorder_chunks_temporal_first(chunks: List[SearchResult]) -> List[SearchResult]:
    """
    FIX 4: Reorder chunks so that temporal-keyword chunks appear first,
    followed by remaining chunks in their original relevance order.
    No duplicates - each chunk appears exactly once.
    """
    temporal = []
    non_temporal = []

    for chunk in chunks:
        if _TEMPORAL_PATTERN.search(chunk.content):
            temporal.append(chunk)
        else:
            non_temporal.append(chunk)

    if temporal:
        logger.info(
            "FIX 4: Moved %d temporal-evidence chunks to top of context window",
            len(temporal)
        )

    return temporal + non_temporal


class RAGPipeline:
    """Retrieval-Augmented Generation pipeline for compliance Q&A."""

    def __init__(self, vector_store: VectorStoreService, llm_client, top_k: int = DEFAULT_TOP_K):
        self.vector_store = vector_store
        self.llm_client = llm_client
        self.top_k = top_k

    async def query_stream(self, query: str, history: List[ChatMessage] = None) -> AsyncGenerator[dict, None]:
        """
        Process a query and stream the response as SSE events.

        Pipeline:
          1. Embed query
          2. Vector search
          3. Evidence analysis (NEW — classify, grade, detect gaps)
          4. Send evidence + analysis to frontend
          5. Build constrained auditor prompt
          6. Stream LLM response
          7. Assess confidence and send completion

        Yields SSE-formatted event dicts:
          - event: evidence  (with retrieved chunks + evidence analysis)
          - event: token     (streaming answer tokens)
          - event: done      (final metadata including evidence strength)
        """
        history = history or []

        # Step 1: Embed the query
        logger.info("Step 1: Embedding query: %s", query[:100])
        try:
            loop = asyncio.get_event_loop()
            query_embedding = await loop.run_in_executor(None, embed_single, query)
        except Exception as e:
            logger.error("Failed to embed query: %s", e, exc_info=True)
            yield self._sse_event("error", {"message": "Failed to generate query embedding. Is Ollama running?"})
            return

        if not query_embedding or all(v == 0.0 for v in query_embedding):
            yield self._sse_event("error", {"message": "Failed to generate query embedding. Check nomic-embed-text model."})
            return

        # Step 2: Retrieve relevant chunks
        logger.info("Step 2: Searching vector store (top_k=%d)", self.top_k)
        try:
            search_results = self.vector_store.search(query_embedding=query_embedding, top_k=self.top_k)
        except Exception as e:
            logger.error("Vector search failed: %s", e, exc_info=True)
            yield self._sse_event("error", {"message": "Vector search failed."})
            return

        # Filter by minimum relevance
        relevant_chunks = [r for r in search_results if r.score >= MIN_RELEVANCE_SCORE]
        logger.info("Retrieved %d relevant chunks (from %d total, threshold=%.2f)", len(relevant_chunks), len(search_results), MIN_RELEVANCE_SCORE)

        # FIX 4: Reorder chunks so temporal-keyword chunks appear first
        relevant_chunks = _reorder_chunks_temporal_first(relevant_chunks)

        # Step 3: Analyze evidence quality (NEW)
        logger.info("Step 3: Analyzing evidence quality")
        evidence_analysis = analyze_evidence(query=query, chunks=relevant_chunks)
        evidence_analysis_text = format_evidence_analysis(evidence_analysis)

        # Step 4: Send evidence + analysis to frontend
        evidence_data = []
        for i, chunk in enumerate(relevant_chunks):
            classification = None
            if i < len(evidence_analysis.classifications):
                classification = evidence_analysis.classifications[i]

            evidence_data.append({
                "filename": chunk.filename,
                "page_number": chunk.page_number,
                "content": chunk.content,
                "relevance_score": chunk.score,
                "evidence_strength": classification.strength if classification else "",
                "maturity_signals": classification.maturity_signals if classification else [],
            })

        yield self._sse_event("evidence", {
            "evidence": evidence_data,
            "evidence_analysis": {
                "overall_strength": evidence_analysis.overall_strength,
                "maturity_assessment": evidence_analysis.maturity_assessment,
                "coverage_gaps": evidence_analysis.coverage_gaps,
            },
        })

        # Step 5: Compute deterministic verdict + confidence BEFORE building prompt
        # Use new Intelligence Layer
        intel = evaluate_compliance(query, relevant_chunks)
        verdict = intel["verdict"]
        confidence = intel["confidence"]

        # Step 7: Build audit-constrained prompt
        logger.info("Step 7: Building audit-constrained prompt for JSON")
        messages = build_prompt(
            query=query,
            context_chunks=relevant_chunks,
            history=history,
        )

        # Step 6: Stream LLM response
        logger.info("Step 6: Streaming LLM response")
        full_response = ""
        token_count = 0
        try:
            async for token in self.llm_client.chat_stream(messages):
                token_count += 1
                full_response += token
                yield self._sse_event("token", {"content": token})
            logger.info("LLM stream completed. Total tokens: %d", token_count)
        except Exception as e:
            logger.error("LLM streaming failed: %s", e, exc_info=True)
            yield self._sse_event("error", {"message": f"LLM inference failed: {str(e)}"})
            return

        # Parse JSON from response
        import re
        json_str = full_response
        match = re.search(r'\{.*\}', full_response, re.DOTALL)
        if match:
            json_str = match.group(0)
            
        parsed_json = {
            "answer": full_response,
            "status": "No Evidence",
            "confidence": 0.0,
            "explanation": "",
        }
        try:
            parsed_json = json.loads(json_str)
        except json.JSONDecodeError:
            logger.error("Failed to parse JSON in query_stream")

        answer_text = parsed_json.get("answer", "")
        if parsed_json.get("explanation"):
            answer_text += f"\n\nExplanation: {parsed_json['explanation']}"

        yield self._sse_event("done", {
            "answer": answer_text,
            "confidence": str(parsed_json.get("confidence", 0.0)),
            "evidence_strength": intel["evidence_classification"],
            "verdict": parsed_json.get("status", "No Evidence"),
            "coverage_gaps": evidence_analysis.coverage_gaps,
            "requirement_attributes": intel["requirement_attributes"],
            "proven_attributes": intel["proven_attributes"],
            "missing_attributes": intel["missing_attributes"],
            "gap_analysis": intel["gap_analysis"],
            "audit_defensibility": intel["audit_defensibility"],
            "evidence_sufficiency": intel["evidence_sufficiency"],
            "compliance_risk": intel["compliance_risk"],
            "reasoning": intel["reasoning"],
        })
        logger.info(
            "Query completed. verdict=%s, confidence=%s, evidence_strength=%s",
            verdict, confidence, evidence_analysis.overall_strength,
        )

    async def query_sync(self, query: str, history: List[ChatMessage] = None) -> ChatResponse:
        """Process a query and return complete response (non-streaming)."""
        history = history or []
        query_embedding = embed_single(query)
        search_results = self.vector_store.search(query_embedding=query_embedding, top_k=self.top_k)
        relevant_chunks = [r for r in search_results if r.score >= MIN_RELEVANCE_SCORE]
        # FIX 4: Reorder - temporal chunks first in sync path too
        relevant_chunks = _reorder_chunks_temporal_first(relevant_chunks)

        # Evidence analysis
        evidence_analysis = analyze_evidence(query=query, chunks=relevant_chunks)
        evidence_analysis_text = format_evidence_analysis(evidence_analysis)

        # Deterministic verdict + confidence from intelligence layer
        intel = evaluate_compliance(query, relevant_chunks)
        verdict = intel["verdict"]
        confidence = intel["confidence"]

        messages = build_prompt(
            query=query,
            context_chunks=relevant_chunks,
            history=history,
        )
        answer_raw = await self.llm_client.chat(messages)

        import re
        json_str = answer_raw
        match = re.search(r'\{.*\}', answer_raw, re.DOTALL)
        if match:
            json_str = match.group(0)
            
        parsed_json = {
            "answer": answer_raw,
            "status": "No Evidence",
            "confidence": 0.0,
            "explanation": ""
        }
        try:
            parsed_json = json.loads(json_str)
        except json.JSONDecodeError:
            pass

        answer_text = parsed_json.get("answer", "")
        if parsed_json.get("explanation"):
            answer_text += f"\n\nExplanation: {parsed_json['explanation']}"

        # Build enriched evidence list
        evidence = []
        for i, c in enumerate(relevant_chunks):
            classification = evidence_analysis.classifications[i] if i < len(evidence_analysis.classifications) else None
            evidence.append(Evidence(
                filename=c.filename,
                page_number=c.page_number,
                content=c.content,
                relevance_score=c.score,
                evidence_strength=classification.strength if classification else "",
                maturity_signals=classification.maturity_signals if classification else [],
            ))

        return ChatResponse(
            answer=answer_text,
            evidence=evidence,
            confidence=str(parsed_json.get("confidence", 0.0)),
            evidence_strength=intel["evidence_classification"],
            verdict=parsed_json.get("status", "No Evidence"),
            coverage_gaps=evidence_analysis.coverage_gaps,
            requirement_attributes=intel["requirement_attributes"],
            proven_attributes=intel["proven_attributes"],
            missing_attributes=intel["missing_attributes"],
            gap_analysis=intel["gap_analysis"],
            audit_defensibility=intel["audit_defensibility"],
            evidence_sufficiency=intel["evidence_sufficiency"],
            compliance_risk=intel["compliance_risk"],
            reasoning=intel["reasoning"],
        )

    @staticmethod
    def _sse_event(event_type: str, data: dict) -> dict:
        return {"event": event_type, "data": json.dumps(data)}

