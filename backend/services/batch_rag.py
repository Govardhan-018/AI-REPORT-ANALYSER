"""
Batch RAG Processor — Process multiple questionnaire questions through the RAG pipeline.

Handles:
  - Sequential processing of question rows with progress callbacks
  - Configurable concurrency (default: 1 to avoid Ollama overload)
  - Per-question timeout with graceful error handling
  - Confidence scoring and compliance status determination
"""

import os
import logging
import asyncio
import re
from typing import List, Dict, Callable, Optional

from models.schemas import QuestionRow, AnswerResult, SearchResult
from services.rag_pipeline import RAGPipeline, MIN_RELEVANCE_SCORE
from services.prompt_builder import build_questionnaire_prompt, detect_framework_hint, _format_context
from services.embeddings import embed_single
from services.vector_store import VectorStoreService

logger = logging.getLogger("complianceai.batch_rag")

# Configurable via environment variables
BATCH_CONCURRENCY = int(os.getenv("BATCH_CONCURRENCY", "1"))
QUESTION_TIMEOUT = int(os.getenv("QUESTION_TIMEOUT", "60"))

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


async def process_questionnaire_batch(
    questions: List[QuestionRow],
    vector_store: VectorStoreService,
    llm_client,
    progress_callback: Optional[Callable] = None,
) -> List[AnswerResult]:
    """
    Process a batch of questionnaire questions through the RAG pipeline.

    For each question:
      1. Embed the question
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
            answer="(Skipped — empty question)",
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
            answer="Processing timeout — please retry this question manually",
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

    Steps: embed → search → prompt → LLM → score → result
    """
    # Step 1: Embed the question
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

    # Step 2: Retrieve relevant chunks
    search_results = vector_store.search(query_embedding=query_embedding, top_k=5)
    relevant_chunks = [r for r in search_results if r.score >= MIN_RELEVANCE_SCORE]

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
