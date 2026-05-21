"""
Semantic Chunker — Split document pages into overlapping chunks.

Sentence-boundary-aware chunking that preserves page references.
Target chunk size: 700 tokens (~2800 chars), overlap: 125 tokens (~500 chars).

FIX 1: Table-aware chunking for structured audit documents (SOC 2 testing matrices).
  - Detects control ID patterns: CC#.#(.#)?, A#.#, C#.#
  - Never splits mid-row — each control entry is an atomic unit
  - Groups 3–4 adjacent control rows into one chunk
  - Prepends nearest section header to every chunk for self-containment
  - Non-table content falls back to paragraph/token-based splitting
"""

import re
import uuid
import logging
from typing import List, Optional, Tuple

from models.schemas import PageContent, Chunk

logger = logging.getLogger("complianceai.chunker")

# Approximate: 1 token ≈ 4 characters for English text
CHARS_PER_TOKEN = 4

# Chunking parameters per spec (600-800 tokens, 100-150 overlap)
DEFAULT_CHUNK_SIZE_TOKENS = 700
DEFAULT_OVERLAP_TOKENS = 125

# --- FIX 1: Control ID pattern for SOC 2 / audit table detection ---
# Matches control IDs like CC1.2, CC1.2.1, A1.1, C1.2, CC6.1.3
CONTROL_ID_PATTERN = re.compile(
    r'^\s*(?:CC\d+\.\d+(?:\.\d+)?|A\d+\.\d+|C\d+\.\d+)',
    re.MULTILINE
)

# Section header pattern — matches lines like "CC1.0 Control Environment",
# "## Section: ...", bold markers, or COSO principle references
SECTION_HEADER_PATTERN = re.compile(
    r'^\s*(?:#{1,3}\s+|(?:CC|A|C)\d+\.0\s+|Section\s*:\s*|COSO\s+Principle)',
    re.MULTILINE | re.IGNORECASE
)

# Number of control rows to group into a single chunk
CONTROL_ROWS_PER_CHUNK = 4


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

    FIX 1: Before sentence-based splitting, a table-aware pre-processing
    pass detects and groups control rows from audit tables (SOC 2 matrices).

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

        # --- FIX 1: Table-aware pre-processing pass ---
        # Separate table rows (control entries) from narrative text,
        # then chunk each type appropriately.
        table_segments, narrative_segments = _separate_table_and_narrative(text)

        # Process table segments: group control rows, prepend headers
        if table_segments:
            table_chunks = _build_table_chunks(
                table_segments=table_segments,
                page_number=page.page_number,
                filename=filename,
                session_id=session_id,
                full_page_text=text,
            )
            all_chunks.extend(table_chunks)

        # Process narrative segments: use original sentence-based splitting
        for narrative_text in narrative_segments:
            if not narrative_text.strip():
                continue
            sentences = _split_into_sentences(narrative_text)
            if not sentences:
                continue

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


# ═══════════════════════════════════════════════════════════════════════
# FIX 1: TABLE-AWARE CHUNKING HELPERS
# ═══════════════════════════════════════════════════════════════════════

def _separate_table_and_narrative(
    text: str,
) -> Tuple[List[dict], List[str]]:
    """
    Separate a page's text into table segments (control rows) and
    narrative segments (everything else).

    A "control row" starts with a control ID pattern (CC1.2, A1.1, etc.)
    and extends until the next control ID or a blank-line gap.

    Returns:
        table_segments: list of dicts with 'control_id' and 'text' keys
        narrative_segments: list of plain text strings (non-table content)
    """
    lines = text.split('\n')
    table_segments: List[dict] = []
    narrative_segments: List[str] = []

    current_narrative_lines: List[str] = []
    current_control_id: Optional[str] = None
    current_control_lines: List[str] = []

    for line in lines:
        control_match = CONTROL_ID_PATTERN.match(line)

        if control_match:
            # We hit a new control row — flush any in-progress narrative
            if current_narrative_lines:
                narrative_segments.append('\n'.join(current_narrative_lines))
                current_narrative_lines = []

            # Flush any previous control row
            if current_control_id is not None:
                table_segments.append({
                    'control_id': current_control_id,
                    'text': '\n'.join(current_control_lines).strip(),
                })

            # Start a new control row
            current_control_id = control_match.group(0).strip()
            current_control_lines = [line]

        elif current_control_id is not None:
            # We're inside a control row — keep appending until we hit a
            # blank line (paragraph gap) or the next control ID
            if line.strip() == '' and len(current_control_lines) > 1:
                # Blank line after content: the control row is complete
                table_segments.append({
                    'control_id': current_control_id,
                    'text': '\n'.join(current_control_lines).strip(),
                })
                current_control_id = None
                current_control_lines = []
            else:
                # Continuation of the current control row
                current_control_lines.append(line)
        else:
            # Normal narrative line
            current_narrative_lines.append(line)

    # Flush any remaining control row
    if current_control_id is not None:
        table_segments.append({
            'control_id': current_control_id,
            'text': '\n'.join(current_control_lines).strip(),
        })

    # Flush any remaining narrative
    if current_narrative_lines:
        narrative_segments.append('\n'.join(current_narrative_lines))

    return table_segments, narrative_segments


def _find_nearest_section_header(full_page_text: str, control_id: str) -> str:
    """
    Find the nearest section header that appears before a control ID
    in the full page text. Used to prepend context to each table chunk.

    Returns a string like:
      "Section: CC1.0 Control Environment > CC1.2 COSO Principle 2 | "
    or an empty string if no header is found.
    """
    # Find where the control ID appears in the page text
    control_pos = full_page_text.find(control_id)
    if control_pos < 0:
        control_pos = len(full_page_text)

    # Search backwards from the control ID position for section headers
    text_before = full_page_text[:control_pos]
    headers = list(SECTION_HEADER_PATTERN.finditer(text_before))

    if not headers:
        return ""

    # Take the last (nearest) header match
    last_header_match = headers[-1]
    # Extract the full header line
    header_start = last_header_match.start()
    header_end = text_before.find('\n', header_start)
    if header_end < 0:
        header_end = len(text_before)

    header_text = text_before[header_start:header_end].strip()
    # Clean up markdown formatting
    header_text = re.sub(r'^#+\s*', '', header_text)

    return f"Section: {header_text} | "


def _build_table_chunks(
    table_segments: List[dict],
    page_number: int,
    filename: str,
    session_id: str,
    full_page_text: str,
) -> List[Chunk]:
    """
    Group adjacent control rows into chunks of CONTROL_ROWS_PER_CHUNK rows.
    Each chunk gets the nearest section header prepended for self-containment.

    This ensures no control row is ever split across chunks, and each chunk
    carries enough context for the embedding model to understand it.
    """
    chunks: List[Chunk] = []

    # Group control rows into batches of CONTROL_ROWS_PER_CHUNK (3-4)
    for batch_start in range(0, len(table_segments), CONTROL_ROWS_PER_CHUNK):
        batch = table_segments[batch_start:batch_start + CONTROL_ROWS_PER_CHUNK]

        # Use the first control ID in the batch to find the nearest header
        first_control_id = batch[0]['control_id']
        section_prefix = _find_nearest_section_header(full_page_text, first_control_id)

        # Combine all control rows in this batch
        combined_text = '\n\n'.join(seg['text'] for seg in batch)

        # Prepend section header for self-contained context
        chunk_text = f"{section_prefix}{combined_text}" if section_prefix else combined_text

        chunks.append(Chunk(
            chunk_id=str(uuid.uuid4()),
            filename=filename,
            page_number=page_number,
            content=chunk_text,
            char_start=0,  # Table chunks don't track char offsets precisely
            char_end=len(chunk_text),
            session_id=session_id,
        ))

    logger.debug(
        "Built %d table-aware chunks from %d control rows on page %d",
        len(chunks), len(table_segments), page_number,
    )

    return chunks


# ═══════════════════════════════════════════════════════════════════════
# ORIGINAL SENTENCE-BASED CHUNKING (unchanged, used for narrative text)
# ═══════════════════════════════════════════════════════════════════════

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
