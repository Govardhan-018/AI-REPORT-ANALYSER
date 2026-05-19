"""
Pydantic models for the ComplianceAI application.
All data schemas for documents, chunks, chat, and evidence.
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Literal, Dict
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
    evidence_strength: str = ""  # "Explicit Evidence" | "Strongly Implied" | "Partial Evidence" | "No Evidence"
    maturity_signals: List[str] = Field(default_factory=list)  # e.g. ["policy_exists", "implemented"]


class ChatResponse(BaseModel):
    """Complete response from the chat endpoint (non-streaming)."""
    answer: str
    evidence: List[Evidence] = Field(default_factory=list)
    confidence: str = "Low"              # High, Medium, Low
    evidence_strength: str = ""           # Overall: Explicit/Implied/Partial/No Evidence
    verdict: str = ""                     # Yes, Partial, No, No Evidence, Compliant, Non-Compliant
    coverage_gaps: List[str] = Field(default_factory=list)
    requirement_attributes: List[str] = Field(default_factory=list)
    proven_attributes: List[str] = Field(default_factory=list)
    missing_attributes: List[str] = Field(default_factory=list)
    gap_analysis: str = ""
    audit_defensibility: str = ""
    evidence_sufficiency: str = ""
    compliance_risk: str = ""
    reasoning: str = ""


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


# ─── Questionnaire Models ─────────────────────────────────────────────

class QuestionRow(BaseModel):
    """A single question row parsed from the Excel file."""
    row_index: int
    question_text: str
    raw_row_data: Dict[str, str] = Field(default_factory=dict)


class AnswerResult(BaseModel):
    """Result of processing a single questionnaire row through the RAG pipeline."""
    row_index: int
    question: str
    answer: str
    evidence_chunks: List[Dict] = Field(default_factory=list)
    top_source_file: Optional[str] = None
    top_source_page: Optional[int] = None
    confidence_score: float = 0.0
    status: str = "No Evidence"
    requirement_attributes: List[str] = Field(default_factory=list)
    evidence_classification: str = ""
    proven_attributes: List[str] = Field(default_factory=list)
    missing_attributes: List[str] = Field(default_factory=list)
    gap_analysis: str = ""
    audit_defensibility: str = ""
    evidence_sufficiency: str = ""
    compliance_risk: str = ""
    reasoning: str = ""
    evidence_strength: str = ""
    source_file: Optional[str] = None
    page_reference: Optional[str] = None
    explanation: str = ""
    gap_recommendation: str = ""
    framework_tag: str = ""
    review_flag: str = "NO"


class QuestionnaireRunRequest(BaseModel):
    """Request body to run the questionnaire auto-fill."""
    file_id: str
    question_column: str
    answer_column: Optional[str] = None
    status_column: Optional[str] = None
    evidence_column: Optional[str] = None
    page_column: Optional[str] = None
    confidence_column: Optional[str] = None
    evidence_strength_column: Optional[str] = None
    gap_column: Optional[str] = None
    explanation_column: Optional[str] = None
    framework_column: Optional[str] = None
    review_flag_column: Optional[str] = None
    provider: str = "ollama"        # "ollama", "groq", or "openrouter"
    model: Optional[str] = None     # specific model name, or None for provider default


class QuestionnaireUploadResponse(BaseModel):
    """Response after uploading a questionnaire Excel file."""
    file_id: str
    filename: str
    total_rows: int
    detected_columns: List[str] = Field(default_factory=list)
    preview_rows: List[Dict] = Field(default_factory=list)

