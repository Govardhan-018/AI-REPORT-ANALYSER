"""
Semantic Chunker — Split document pages into overlapping chunks.

Sentence-boundary-aware chunking that preserves page references.
Target chunk size: 700 tokens (~2800 chars), overlap: 125 tokens (~500 chars).
"""

import re
import uuid
import logging
from typing import List

from models.schemas import PageContent, Chunk

logger = logging.getLogger("complianceai.chunker")

# Approximate: 1 token ≈ 4 characters for English text
CHARS_PER_TOKEN = 4

# Chunking parameters per spec (600-800 tokens, 100-150 overlap)
DEFAULT_CHUNK_SIZE_TOKENS = 700
DEFAULT_OVERLAP_TOKENS = 125


def chunk_document(
    pages: List[PageContent],
    filename: str,
    session_id: str = "",
    chunk_size_tokens: int = DEFAULT_CHUNK_SIZE_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> List[Chunk]:
    """
    Split document pages into overlapping chunks.

    Each chunk preserves the page number of its source.
    Chunks are split at sentence boundaries when possible.

    Args:
        pages: List of extracted page contents.
        filename: Original filename for metadata.
        session_id: Upload session identifier.
        chunk_size_tokens: Target size in tokens per chunk.
        overlap_tokens: Number of overlap tokens between chunks.

    Returns:
        List of Chunk objects with metadata.
    """
    chunk_size_chars = chunk_size_tokens * CHARS_PER_TOKEN
    overlap_chars = overlap_tokens * CHARS_PER_TOKEN

    all_chunks: List[Chunk] = []

    for page in pages:
        text = page.text.strip()
        if not text:
            continue

        # Split page text into sentences
        sentences = _split_into_sentences(text)

        if not sentences:
            continue

        # Build chunks from sentences
        page_chunks = _build_chunks_from_sentences(
            sentences=sentences,
            page_number=page.page_number,
            filename=filename,
            session_id=session_id,
            chunk_size_chars=chunk_size_chars,
            overlap_chars=overlap_chars,
        )

        all_chunks.extend(page_chunks)

    logger.info(
        "Chunked %d pages into %d chunks for %s (size=%d tokens, overlap=%d tokens)",
        len(pages), len(all_chunks), filename, chunk_size_tokens, overlap_tokens,
    )

    return all_chunks


def _split_into_sentences(text: str) -> List[str]:
    """Split text into sentences at natural boundaries."""
    # Split on sentence-ending punctuation followed by whitespace,
    # or on double newlines (paragraph breaks)
    pattern = r'(?<=[.!?])\s+|\n\n+'
    parts = re.split(pattern, text)

    # Filter out empty strings and strip whitespace
    sentences = [s.strip() for s in parts if s.strip()]
    return sentences


def _build_chunks_from_sentences(
    sentences: List[str],
    page_number: int,
    filename: str,
    session_id: str,
    chunk_size_chars: int,
    overlap_chars: int,
) -> List[Chunk]:
    """Build overlapping chunks from a list of sentences."""
    chunks: List[Chunk] = []
    current_chunk_parts: List[str] = []
    current_length = 0
    char_position = 0

    for sentence in sentences:
        sentence_len = len(sentence)

        # If adding this sentence exceeds chunk size, finalize current chunk
        if current_length + sentence_len > chunk_size_chars and current_chunk_parts:
            chunk_text = " ".join(current_chunk_parts)
            chunks.append(Chunk(
                chunk_id=str(uuid.uuid4()),
                filename=filename,
                page_number=page_number,
                content=chunk_text,
                char_start=char_position - current_length,
                char_end=char_position,
                session_id=session_id,
            ))

            # Calculate overlap: keep trailing sentences that fit in overlap
            overlap_parts: List[str] = []
            overlap_len = 0
            for part in reversed(current_chunk_parts):
                if overlap_len + len(part) <= overlap_chars:
                    overlap_parts.insert(0, part)
                    overlap_len += len(part)
                else:
                    break

            current_chunk_parts = overlap_parts
            current_length = overlap_len

        current_chunk_parts.append(sentence)
        current_length += sentence_len
        char_position += sentence_len

    # Don't forget the last chunk
    if current_chunk_parts:
        chunk_text = " ".join(current_chunk_parts)
        chunks.append(Chunk(
            chunk_id=str(uuid.uuid4()),
            filename=filename,
            page_number=page_number,
            content=chunk_text,
            char_start=char_position - current_length,
            char_end=char_position,
            session_id=session_id,
        ))

    return chunks
