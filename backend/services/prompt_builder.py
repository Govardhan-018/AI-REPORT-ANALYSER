"""
Prompt Builder — Construct LLM prompts with anti-hallucination enforcement.

Uses the EXACT verbatim system prompt from the spec.
Critical for compliance accuracy and hallucination prevention.
"""

import logging
from typing import List

from models.schemas import SearchResult, ChatMessage

logger = logging.getLogger("complianceai.prompt_builder")

# ─── SYSTEM PROMPT (VERBATIM FROM SPEC — DO NOT MODIFY) ─────────────
SYSTEM_PROMPT_TEMPLATE = """You are a compliance document analysis assistant.

RULES:
1. Answer ONLY using the document context provided below.
2. If the answer is not explicitly present in the context, respond:
   "Not found in uploaded documents."
3. Never infer, assume, or extrapolate beyond the provided text.
4. Always cite the source filename and page number for every claim.
5. Structure every response as:

   ANSWER: [direct yes/no or factual answer]

   EVIDENCE:
   [filename], Page [N]:
   "[exact relevant passage]"

   CONFIDENCE: [High | Medium | Low]
   (High = direct explicit statement found,
    Medium = implied by context,
    Low = partial match only)

DOCUMENT CONTEXT:
{context}"""


def build_prompt(
    query: str,
    context_chunks: List[SearchResult],
    history: List[ChatMessage] = None,
) -> List[dict]:
    """
    Build the complete message list for the LLM.

    Args:
        query: The user's question.
        context_chunks: Retrieved document chunks with metadata.
        history: Previous conversation messages (max 5 exchanges kept).

    Returns:
        List of message dicts ready for Ollama chat API.
    """
    messages: List[dict] = []

    # 1. Format document context
    context_text = _format_context(context_chunks)

    # 2. System prompt with context injected
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(context=context_text)
    messages.append({"role": "system", "content": system_prompt})

    # 3. Add conversation history (limited to last 5 exchanges)
    if history:
        recent_history = history[-10:]  # Last 5 user+assistant pairs
        for msg in recent_history:
            messages.append({"role": msg.role, "content": msg.content})

    # 4. User query
    messages.append({"role": "user", "content": query})

    return messages


def _format_context(chunks: List[SearchResult]) -> str:
    """Format search results into a labeled context block."""
    if not chunks:
        return "[No relevant document chunks found.]"

    context_parts: List[str] = []
    for i, chunk in enumerate(chunks, 1):
        context_parts.append(
            f"--- Source {i} ---\n"
            f"File: {chunk.filename}\n"
            f"Page: {chunk.page_number}\n"
            f"Relevance: {chunk.score:.2f}\n"
            f"Content:\n{chunk.content}\n"
        )

    return "\n".join(context_parts)
