"""
Chat Routes — Handle RAG-powered chat queries.

Supports SSE streaming for real-time token delivery.
"""

import logging
from typing import AsyncGenerator

from fastapi import APIRouter, Request, HTTPException
from sse_starlette.sse import EventSourceResponse

from models.schemas import ChatRequest
from services.rag_pipeline import RAGPipeline
from services.vector_store import VectorStoreService
from services.ollama_client import OllamaClient

logger = logging.getLogger("complianceai.routes.chat")

router = APIRouter(tags=["chat"])


@router.post("/chat")
async def chat_stream(request: Request, chat_request: ChatRequest):
    """
    Process a chat query using RAG and stream the response via SSE.

    Events:
      - evidence: Retrieved document chunks with metadata
      - token: Streaming answer tokens
      - done: Completion event with full answer and confidence
      - error: Error event with message
    """
    logger.info("Chat request: %s", chat_request.query[:100])

    vector_store: VectorStoreService = request.app.state.vector_store
    
    if vector_store is None:
        raise HTTPException(status_code=503, detail="Vector store not initialized")

    total_chunks = vector_store.get_total_count()
    if total_chunks == 0:
        raise HTTPException(status_code=400, detail="No documents uploaded yet. Please upload a document first.")

    provider = chat_request.provider.lower()
    if provider == "groq":
        llm_client = request.app.state.groq_client
        if not llm_client.api_key:
            raise HTTPException(status_code=400, detail="GROQ_API_KEY is not configured in .env file.")
    elif provider == "openrouter":
        llm_client = request.app.state.openrouter_client
        if not llm_client.api_key:
            raise HTTPException(status_code=400, detail="OPENROUTER_API_KEY is not configured in .env file.")
    else:
        # Default to ollama
        llm_client = request.app.state.ollama_client
        ollama_available = await llm_client.check_availability()
        if not ollama_available:
            raise HTTPException(status_code=503, detail="Ollama is not running. Please start Ollama before using local chat.")

    rag = RAGPipeline(vector_store=vector_store, llm_client=llm_client)

    async def event_generator() -> AsyncGenerator:
        async for event in rag.query_stream(
            query=chat_request.query,
            history=chat_request.conversation_history,
        ):
            yield event

    return EventSourceResponse(event_generator(), media_type="text/event-stream")
