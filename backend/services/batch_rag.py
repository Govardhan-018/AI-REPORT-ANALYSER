"""
Batch RAG Processor — Process multiple questionnaire questions through the RAG pipeline.

Handles:
  - Sequential processing of question rows with progress callbacks
  - Configurable concurrency (default: 1 to avoid Ollama overload)
  - Per-question timeout with graceful error handling
  - Confidence scoring and compliance status determination

FIX 5: Query expansion for questionnaire processing.
  - Generates 2 additional search queries per question using heuristic rules
  - Frequency questions -> "annual schedule" + "review interval" variants
  - Policy questions -> "<subject> policy procedure" + "<subject> documented"
  - All questions -> shortened 5-7 word version (ISO tag stripped)
  - Retrieves chunks for all expanded queries, deduplicates, merges top-10
"""

import os
import logging
import asyncio
import re
from typing import List, Dict, Callable, Optional

from models.schemas import QuestionRow, AnswerResult, SearchResult
from services.rag_pipeline import RAGPipeline, MIN_RELEVANCE_SCORE, _reorder_chunks_temporal_first
from services.prompt_builder import build_questionnaire_prompt, detect_framework_hint, _format_context
from services.embeddings import embed_single
from services.vector_store import VectorStoreService

logger = logging.getLogger("complianceai.batch_rag")

# Configurable via environment variables
BATCH_CONCURRENCY = int(os.getenv("BATCH_CONCURRENCY", "1"))
QUESTION_TIMEOUT = int(os.getenv("QUESTION_TIMEOUT", "60"))

# FIX 5: Maximum number of unique chunks to pass after query expansion merge
MAX_MERGED_CHUNKS = 10


def clean_duplicate_blocks(text: str) -> str:
    """Remove duplicate block sections from LLM output."""
    if text.count("FINAL VERDICT") <= 1:
        return text
    
    first_verdict = text.find("FINAL VERDICT:")
    second_verdict = text.find("FINAL VERDICT:", first_verdict + 1)
    
    if second_verdict != -1:
        cut_idx = text.rfind("\n\n", first_verdict, second_verdict)
        if cut_idx != -1:
            return text[:cut_idx].strip()
        else:
            return text[:second_verdict].strip()
    return text


# =====================================================================
# FIX 5: QUERY EXPANSION HELPERS
# =====================================================================

def _strip_iso_tag(question: str) -> str:
    """
    FIX 5: Remove ISO/framework tag prefixes like '[ISO 27001]', '[SOC 2]',
    '[HIPAA]' from the beginning of a question.
    """
    return re.sub(r'^\s*\[.*?\]\s*', '', question).strip()


def _shorten_query(question: str, max_words: int = 7) -> str:
    """
    FIX 5: Create a shortened 5-7 word search query from a question.
    Strips ISO tags, removes filler words, keeps core subject.
    """
    clean = _strip_iso_tag(question)
    # Remove common question prefixes
    clean = re.sub(
        r'^(?:does the organization|is there a|are there|do you have|'
        r'how often does|how frequently|does the company|'
        r'is the organization|has the organization)\s+',
        '', clean, flags=re.IGNORECASE
    )
    words = clean.split()
    return ' '.join(words[:max_words])


def _extract_subject_noun(question: str) -> str:
    """
    FIX 5: Extract the core subject noun phrase from a policy/procedure question.
    E.g., "Does the policy include data retention?" -> "data retention"
    """
    clean = _strip_iso_tag(question)
    # Try to find the subject after common verbs
    patterns = [
        r'(?:include|cover|address|define|establish|describe)\s+(.+?)(?:\?|$)',
        r'(?:policy for|procedure for|process for)\s+(.+?)(?:\?|$)',
        r'(?:is there a|does .+ have a?)\s+(.+?)(?:\s+policy|\s+procedure|\s+process)?(?:\?|$)',
    ]
    for pattern in patterns:
        match = re.search(pattern, clean, re.IGNORECASE)
        if match:
            subject = match.group(1).strip().rstrip('?.,;')
            # Limit to reasonable length
            words = subject.split()
            return ' '.join(words[:5])
    
    # Fallback: return the shortened question
    return _shorten_query(question, max_words=5)


def expand_query(question: str) -> List[str]:
    """
    FIX 5: Generate additional search queries from a questionnaire question.

    Heuristic expansion rules:
    - Frequency questions ("how often", "frequency", "how frequently"):
        -> replace trigger phrase with "annual schedule"
        -> prepend "review interval" to core subject
    - Policy questions ("does the policy include", "is there a policy"):
        -> "<subject> policy procedure"
        -> "<subject> documented"
    - All questions: also search a shortened 5-7 word version (ISO tag stripped)

    Args:
        question: The original questionnaire question text.

    Returns:
        List of expanded query strings (does NOT include the original question).
    """
    expanded = []
    question_lower = question.lower()
    clean_question = _strip_iso_tag(question)

    # --- Rule 1: Frequency questions ---
    frequency_triggers = ['how often', 'frequency', 'how frequently']
    is_frequency = any(trigger in question_lower for trigger in frequency_triggers)

    if is_frequency:
        # Variant 1: Replace trigger phrase with "annual schedule"
        variant1 = clean_question
        for trigger in frequency_triggers:
            variant1 = re.sub(re.escape(trigger), 'annual schedule', variant1, flags=re.IGNORECASE)
        expanded.append(variant1)

        # Variant 2: Prepend "review interval" to the core subject
        subject = _shorten_query(question, max_words=5)
        expanded.append(f"review interval {subject}")

    # --- Rule 2: Policy questions ---
    policy_triggers = ['does the policy include', 'is there a policy', 'does the organization have a policy']
    is_policy = any(trigger in question_lower for trigger in policy_triggers)

    if is_policy:
        subject = _extract_subject_noun(question)
        # Variant 1: "<subject> policy procedure"
        expanded.append(f"{subject} policy procedure")
        # Variant 2: "<subject> documented"
        expanded.append(f"{subject} documented")

    # --- Rule 3: All questions get a shortened variant (ISO tag stripped) ---
    short = _shorten_query(question, max_words=7)
    if short and short not in expanded:
        expanded.append(short)

    logger.debug("FIX 5: Expanded '%s' into %d additional queries: %s",
                 question[:60], len(expanded), expanded)

    return expanded


async def _retrieve_with_expansion(
    question_text: str,
    vector_store: VectorStoreService,
) -> List[SearchResult]:
    """
    FIX 5: Retrieve chunks using query expansion.

    1. Embed the original question + all expanded queries
    2. Retrieve chunks for each query
    3. Merge results, deduplicate by chunk_id
    4. Rank by best score, return top MAX_MERGED_CHUNKS unique chunks

    Args:
        question_text: The original question text.
        vector_store: ChromaDB vector store service.

    Returns:
        List of top-10 unique SearchResult objects ranked by best score.
    """
    loop = asyncio.get_event_loop()

    # Generate expanded queries
    expanded_queries = expand_query(question_text)
    all_queries = [question_text] + expanded_queries

    logger.info("FIX 5: Searching with %d queries (1 original + %d expanded)",
                len(all_queries), len(expanded_queries))

    # Collect all results across all queries
    # Key: chunk_id -> SearchResult (keep the one with the highest score)
    best_by_chunk_id: Dict[str, SearchResult] = {}

    for query in all_queries:
        # Embed the query
        query_embedding = await loop.run_in_executor(None, embed_single, query)

        if not query_embedding or all(v == 0.0 for v in query_embedding):
            logger.warning("FIX 5: Failed to embed expanded query: '%s'", query[:60])
            continue

        # FIX 2: Retrieve with n_results=10 for batch processing
        search_results = vector_store.search(query_embedding=query_embedding, top_k=10)

        # Merge: keep best score per chunk_id (deduplication)
        for result in search_results:
            existing = best_by_chunk_id.get(result.chunk_id)
            if existing is None or result.score > existing.score:
                best_by_chunk_id[result.chunk_id] = result

    # Rank by best score, take top MAX_MERGED_CHUNKS
    merged = sorted(best_by_chunk_id.values(), key=lambda r: r.score, reverse=True)
    top_chunks = merged[:MAX_MERGED_CHUNKS]

    logger.info("FIX 5: Merged %d unique chunks from %d queries, returning top %d",
                len(merged), len(all_queries), len(top_chunks))

    return top_chunks


# =====================================================================
# BATCH PROCESSING (updated to use query expansion)
# =====================================================================

async def process_questionnaire_batch(
    questions: List[QuestionRow],
    vector_store: VectorStoreService,
    llm_client,
    progress_callback: Optional[Callable] = None,
) -> List[AnswerResult]:
    """
    Process a batch of questionnaire questions through the RAG pipeline.

    For each question:
      1. Embed the question (with FIX 5 query expansion)
      2. Retrieve relevant chunks from ChromaDB
      3. Build a compliance-specific prompt
      4. Get LLM response
      5. Compute confidence score and compliance status

    Args:
        questions: List of QuestionRow objects to process.
        vector_store: ChromaDB vector store service.
        llm_client: LLM client (Ollama, Groq, etc.).
        progress_callback: async callback(current, total, question_text, result).

    Returns:
        List of AnswerResult objects.
    """
    results: List[AnswerResult] = []
    total = len(questions)

    # Auto-detect framework from stored document filenames
    docs = vector_store.list_documents()
    doc_filenames = [d["filename"] for d in docs]
    framework_hint = detect_framework_hint(doc_filenames)
    logger.info("Framework hint: '%s' (from %d documents)", framework_hint, len(doc_filenames))

    # Process questions with concurrency control
    semaphore = asyncio.Semaphore(BATCH_CONCURRENCY)

    for i, question in enumerate(questions):
        async with semaphore:
            result = await _process_single_question(
                question=question,
                vector_store=vector_store,
                llm_client=llm_client,
                framework_hint=framework_hint,
            )
            results.append(result)

            if progress_callback:
                await progress_callback(i + 1, total, question.question_text, result)

    logger.info("Batch processing complete: %d/%d questions processed", len(results), total)
    return results


async def _process_single_question(
    question: QuestionRow,
    vector_store: VectorStoreService,
    llm_client,
    framework_hint: str = "",
) -> AnswerResult:
    """
    Process a single question through the RAG pipeline with timeout handling.

    Returns an AnswerResult with answer, evidence, confidence, and status.
    """
    question_text = question.question_text.strip()

    # Skip blank/empty questions
    if not question_text:
        logger.warning("Skipping blank question at row %d", question.row_index)
        return AnswerResult(
            row_index=question.row_index,
            question=question_text,
            answer="(Skipped -- empty question)",
            confidence_score=0.0,
            status="No Evidence",
        )

    try:
        result = await asyncio.wait_for(
            _rag_query(question_text, question.row_index, vector_store, llm_client, framework_hint),
            timeout=QUESTION_TIMEOUT,
        )
        return result

    except asyncio.TimeoutError:
        logger.error("Timeout processing question at row %d: %s", question.row_index, question_text[:80])
        return AnswerResult(
            row_index=question.row_index,
            question=question_text,
            answer="Processing timeout -- please retry this question manually",
            confidence_score=0.0,
            status="No Evidence",
        )

    except Exception as e:
        logger.error("Error processing question at row %d: %s", question.row_index, e, exc_info=True)
        return AnswerResult(
            row_index=question.row_index,
            question=question_text,
            answer=f"Error processing question: {str(e)}",
            confidence_score=0.0,
            status="No Evidence",
        )


async def _rag_query(
    question_text: str,
    row_index: int,
    vector_store: VectorStoreService,
    llm_client,
    framework_hint: str,
) -> AnswerResult:
    """
    Execute the RAG pipeline for a single question.

    Steps: expand -> embed -> search -> merge -> reorder -> prompt -> LLM -> score -> result

    FIX 5: Uses query expansion to retrieve chunks across multiple search queries,
           then deduplicates and ranks by best score before passing to the LLM.
    FIX 4: Reorders merged chunks so temporal-keyword chunks appear first.
    FIX 2: Retrieves top_k=10 chunks per query.
    """
    # FIX 5: Retrieve with query expansion (embed + search + merge + deduplicate)
    all_chunks = await _retrieve_with_expansion(question_text, vector_store)

    # Filter by minimum relevance score
    relevant_chunks = [r for r in all_chunks if r.score >= MIN_RELEVANCE_SCORE]

    # FIX 4: Reorder so temporal-keyword chunks appear first
    relevant_chunks = _reorder_chunks_temporal_first(relevant_chunks)

    # Handle case where expansion returned no embeddings at all
    if not all_chunks:
        # Fallback: try direct embedding without expansion
        loop = asyncio.get_event_loop()
        query_embedding = await loop.run_in_executor(None, embed_single, question_text)
        if not query_embedding or all(v == 0.0 for v in query_embedding):
            return AnswerResult(
                row_index=row_index,
                question=question_text,
                answer="Failed to generate query embedding. Check Ollama/nomic-embed-text.",
                confidence_score=0.0,
                status="No Evidence",
            )
        search_results = vector_store.search(query_embedding=query_embedding, top_k=10)
        relevant_chunks = [r for r in search_results if r.score >= MIN_RELEVANCE_SCORE]
        relevant_chunks = _reorder_chunks_temporal_first(relevant_chunks)

    # Step 3: Analyze evidence quality (audit-defensible grounding)
    from services.compliance_intelligence import evaluate_compliance
    from services.response_normalizer import normalize_response
    
    # Analyze evidence deterministically
    intel = evaluate_compliance(question_text, relevant_chunks)
    verdict = intel["verdict"]
    confidence = intel["confidence"]
    evidence_analysis_text = intel["gap_analysis"]

    # Step 5: Build questionnaire-specific prompt for JSON
    messages = build_questionnaire_prompt(
        question=question_text,
        context_chunks=relevant_chunks,
        framework_hint=framework_hint,
    )

    # Step 6: Get LLM response
    raw_answer = await llm_client.chat(messages)
    
    # Step 7: Parse JSON
    import json
    parsed_json = {
        "answer": "Failed to parse JSON response.",
        "explanation": raw_answer,
        "evidence": "N/A",
        "status": "No Evidence",
        "confidence": 0.0
    }
    
    # Try to extract JSON if it's wrapped in markdown
    json_str = raw_answer
    match = re.search(r'\{.*\}', raw_answer, re.DOTALL)
    if match:
        json_str = match.group(0)
        
    try:
        parsed_json = json.loads(json_str)
    except json.JSONDecodeError:
        logger.error(f"Failed to decode JSON from LLM on row {row_index}: {raw_answer[:100]}")

    # Fallback retry on specific string if needed
    fallback_phrase = "No direct evidence identified."
    if parsed_json.get("evidence", "") == fallback_phrase and len(relevant_chunks) > 0:
        # Just use the parsed JSON, no need to retry since the prompt explicitly tells it to say this.
        pass

    # Column F: Framework Tag
    framework_tag = "Unknown"
    tag_match = re.match(r'^\[(.+?)\]', question_text)
    if tag_match:
        framework_tag = tag_match.group(1).strip()

    # Step 8: Build evidence chunk list
    evidence_chunks = [
        {
            "filename": c.filename,
            "page_number": c.page_number,
            "content": c.content[:300],
            "relevance_score": c.score,
        }
        for c in relevant_chunks
    ]

    # Determine top source
    top_source_file = None
    top_source_page = None
    confidence_score = 0.0

    if relevant_chunks:
        top_chunk = max(relevant_chunks, key=lambda c: c.score)
        top_source_file = top_chunk.filename
        top_source_page = top_chunk.page_number
        confidence_score = top_chunk.score

    # Column G: Review Flag
    review_flag = "NO"
    if parsed_json.get("confidence", 0.0) < 0.60:
        review_flag = "YES"
    if intel["evidence_classification"] == "MISSING":
        review_flag = "YES"
    if parsed_json.get("status") == "Non-Compliant" and intel["evidence_classification"] == "EXPLICIT":
        review_flag = "YES"

    logger.info(
        "Row %d: evidence=%s, verdict=%s, status=%s, confidence=%s, sources=%d",
        row_index, intel["evidence_classification"], verdict, verdict, confidence, len(relevant_chunks)
    )

    return AnswerResult(
        row_index=row_index,
        question=question_text,
        answer=parsed_json.get("answer", ""),
        evidence_chunks=evidence_chunks,
        top_source_file=top_source_file,
        top_source_page=top_source_page,
        confidence_score=float(parsed_json.get("confidence", 0.0)),
        status=parsed_json.get("status", "No Evidence"),
        requirement_attributes=intel["requirement_attributes"],
        evidence_classification=intel["evidence_classification"],
        proven_attributes=intel["proven_attributes"],
        missing_attributes=intel["missing_attributes"],
        gap_analysis=intel["gap_analysis"],
        audit_defensibility=intel["audit_defensibility"],
        evidence_sufficiency=intel["evidence_sufficiency"],
        compliance_risk=intel["compliance_risk"],
        reasoning=intel["reasoning"],
        evidence_strength=intel["evidence_classification"],
        source_file=top_source_file,
        page_reference=str(top_source_page) if top_source_page else None,
        explanation=parsed_json.get("explanation", ""),
        gap_recommendation=intel["gap_analysis"],
        framework_tag=framework_tag,
        review_flag=review_flag,
    )
