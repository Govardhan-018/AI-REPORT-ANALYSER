"""
RAG Pipeline — Retrieval-Augmented Generation orchestrator.

Coordinates: query embedding -> vector search -> prompt building -> LLM inference.
Supports both streaming (SSE) and synchronous response modes.
"""

import json
import logging
import asyncio
from typing import List, AsyncGenerator

from models.schemas import SearchResult, ChatMessage, Evidence, ChatResponse
from services.embeddings import embed_single
from services.vector_store import VectorStoreService
from services.prompt_builder import build_prompt

logger = logging.getLogger("complianceai.rag_pipeline")

DEFAULT_TOP_K = 5
MIN_RELEVANCE_SCORE = 0.50  # Lowered from 0.65. nomic-embed-text typical cosine similarities range 0.5-0.6 for relevant matches


class RAGPipeline:
    """Retrieval-Augmented Generation pipeline for compliance Q&A."""

    def __init__(self, vector_store: VectorStoreService, llm_client, top_k: int = DEFAULT_TOP_K):
        self.vector_store = vector_store
        self.llm_client = llm_client
        self.top_k = top_k

    async def query_stream(self, query: str, history: List[ChatMessage] = None) -> AsyncGenerator[dict, None]:
        """
        Process a query and stream the response as SSE events.

        Yields SSE-formatted event dicts:
          - event: evidence  (with retrieved chunks)
          - event: token     (streaming answer tokens)
          - event: done      (final metadata)
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

        # Step 3: Send evidence to frontend
        evidence_data = [
            {"filename": c.filename, "page_number": c.page_number, "content": c.content, "relevance_score": c.score}
            for c in relevant_chunks
        ]
        yield self._sse_event("evidence", {"evidence": evidence_data})

        # Step 4: Build prompt
        messages = build_prompt(query=query, context_chunks=relevant_chunks, history=history)

        # Step 5: Stream LLM response
        logger.info("Step 5: Streaming LLM response")
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

        # Step 6: Determine confidence
        confidence = self._assess_confidence(full_response, relevant_chunks)

        # Step 7: Send completion event
        yield self._sse_event("done", {"answer": full_response, "confidence": confidence})
        logger.info("Query processing completed. Confidence: %s", confidence)

    async def query_sync(self, query: str, history: List[ChatMessage] = None) -> ChatResponse:
        """Process a query and return complete response (non-streaming)."""
        history = history or []
        query_embedding = embed_single(query)
        search_results = self.vector_store.search(query_embedding=query_embedding, top_k=self.top_k)
        relevant_chunks = [r for r in search_results if r.score >= MIN_RELEVANCE_SCORE]
        messages = build_prompt(query=query, context_chunks=relevant_chunks, history=history)
        answer = await self.llm_client.chat(messages)
        confidence = self._assess_confidence(answer, relevant_chunks)
        evidence = [
            Evidence(filename=c.filename, page_number=c.page_number, content=c.content, relevance_score=c.score)
            for c in relevant_chunks
        ]
        return ChatResponse(answer=answer, evidence=evidence, confidence=confidence)

    def _assess_confidence(self, answer: str, chunks: List[SearchResult]) -> str:
        """Assess confidence level based on retrieval quality and answer content."""
        not_found_phrases = ["not found in uploaded documents", "not explicitly found", "no relevant information"]
        answer_lower = answer.lower()
        for phrase in not_found_phrases:
            if phrase in answer_lower:
                return "Low"
        if not chunks:
            return "Low"
        top_score = max(c.score for c in chunks) if chunks else 0
        if top_score >= 0.7:
            return "High"
        elif top_score >= 0.4:
            return "Medium"
        else:
            return "Low"

    @staticmethod
    def _sse_event(event_type: str, data: dict) -> dict:
        return {"event": event_type, "data": json.dumps(data)}
