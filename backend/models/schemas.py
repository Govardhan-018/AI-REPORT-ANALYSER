"""
Pydantic models for the ComplianceAI application.
All data schemas for documents, chunks, chat, and evidence.
"""

from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
import uuid


class PageContent(BaseModel):
    """Extracted text content from a single page."""
    page_number: int
    text: str
    metadata: dict = Field(default_factory=dict)


class Chunk(BaseModel):
    """A semantically chunked piece of document text."""
    chunk_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    filename: str
    page_number: int
    content: str
    char_start: int = 0
    char_end: int = 0
    session_id: str = ""


class SearchResult(BaseModel):
    """Result from a vector similarity search."""
    chunk_id: str
    filename: str
    page_number: int
    content: str
    score: float = 0.0


class ChatMessage(BaseModel):
    """A single message in a conversation."""
    role: str  # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    """Request body for the chat endpoint."""
    query: str
    session_id: str = ""
    provider: str = "ollama"  # "ollama" or "openrouter"
    model: Optional[str] = None
    conversation_history: List[ChatMessage] = Field(default_factory=list)


class Evidence(BaseModel):
    """A piece of evidence supporting an answer."""
    filename: str
    page_number: int
    content: str
    relevance_score: float = 0.0


class ChatResponse(BaseModel):
    """Complete response from the chat endpoint (non-streaming)."""
    answer: str
    evidence: List[Evidence] = Field(default_factory=list)
    confidence: str = "Low"  # High, Medium, Low


class DocumentInfo(BaseModel):
    """Information about an uploaded and processed document."""
    filename: str
    page_count: int = 0
    chunk_count: int = 0
    uploaded_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    status: str = "pending"  # pending, processing, ready, error
    file_size: int = 0


class UploadResponse(BaseModel):
    """Response after uploading a document."""
    filename: str
    status: str
    message: str


class HealthResponse(BaseModel):
    """System health check response."""
    backend: bool = True
    ollama: bool = False
    ollama_models: List[str] = Field(default_factory=list)
    chromadb: bool = False
    message: str = ""
