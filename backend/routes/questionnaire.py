"""
Questionnaire Routes — Handle Excel questionnaire upload, processing, and download.

Endpoints:
  - POST /upload      : Upload .xlsx file, parse questions, return preview
  - POST /run         : Process questions through RAG with SSE progress streaming
  - GET  /download/{token} : Download completed .xlsx file
"""

import os
import uuid
import json
import time
import logging
import asyncio
from datetime import datetime

from fastapi import APIRouter, UploadFile, File, Request, HTTPException
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse

from models.schemas import (
    QuestionRow, AnswerResult, QuestionnaireRunRequest,
    QuestionnaireUploadResponse,
)
from services.excel_processor import parse_questionnaire, write_results, validate_xlsx
from services.batch_rag import process_questionnaire_batch

logger = logging.getLogger("complianceai.routes.questionnaire")

router = APIRouter(tags=["questionnaire"])

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
TOKEN_EXPIRY_SECONDS = 3600  # 1 hour


@router.post("/upload", response_model=QuestionnaireUploadResponse)
async def upload_questionnaire(request: Request, file: UploadFile = File(...)):
    """
    Upload an Excel questionnaire file.

    Parses the file, detects columns, and returns a preview of the first 5 rows.
    """
    filename = file.filename or "unknown.xlsx"
    ext = os.path.splitext(filename)[1].lower()

    logger.info("Questionnaire upload: %s", filename)

    if ext != ".xlsx":
        raise HTTPException(status_code=400, detail="Only .xlsx files are supported.")

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail=f"File too large. Maximum: {MAX_FILE_SIZE // (1024*1024)} MB")
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Empty file uploaded.")

    # Generate unique file ID and save
    file_id = str(uuid.uuid4())
    questionnaire_dir = _get_questionnaire_dir(request)
    save_path = os.path.join(questionnaire_dir, f"{file_id}.xlsx")

    with open(save_path, "wb") as f:
        f.write(content)

    # Validate magic bytes
    if not validate_xlsx(save_path):
        os.remove(save_path)
        raise HTTPException(status_code=400, detail="Invalid file format. The file does not appear to be a genuine .xlsx file.")

    # Parse the questionnaire
    try:
        parsed = parse_questionnaire(save_path)
    except Exception as e:
        os.remove(save_path)
        logger.error("Failed to parse questionnaire: %s", e, exc_info=True)
        raise HTTPException(status_code=400, detail=f"Failed to parse Excel file: {str(e)}")

    if parsed["total_rows"] == 0:
        os.remove(save_path)
        raise HTTPException(status_code=400, detail="No question rows found in the file.")

    # Register the file in app state
    registry = _get_registry(request)
    registry[file_id] = {
        "original_filename": filename,
        "file_path": save_path,
        "output_path": None,
        "upload_time": datetime.now().isoformat(),
        "columns": parsed["columns"],
        "questions": parsed["questions"],
    }

    logger.info("Questionnaire registered: %s (%d rows, %d columns)",
                file_id, parsed["total_rows"], len(parsed["columns"]))

    return QuestionnaireUploadResponse(
        file_id=file_id,
        filename=filename,
        total_rows=parsed["total_rows"],
        detected_columns=parsed["columns"],
        preview_rows=parsed["preview_rows"],
    )


@router.post("/run")
async def run_questionnaire(request: Request, run_request: QuestionnaireRunRequest):
    """
    Process all questions through the RAG pipeline.

    Streams SSE events for progress, individual results, and completion.
    """
    registry = _get_registry(request)
    file_info = registry.get(run_request.file_id)

    if not file_info:
        raise HTTPException(status_code=404, detail="File not found. Please upload again.")

    # Check if documents are ingested
    vector_store = request.app.state.vector_store
    if vector_store is None:
        raise HTTPException(status_code=503, detail="Vector store not initialized.")

    total_chunks = vector_store.get_total_count()
    if total_chunks == 0:
        raise HTTPException(
            status_code=400,
            detail="No documents ingested. Please upload compliance documents first."
        )

    # Determine LLM client based on selected provider (mirrors chat.py routing)
    provider = run_request.provider.lower()
    model = run_request.model

    if provider == "groq":
        llm_client = request.app.state.groq_client
        if not llm_client.api_key:
            raise HTTPException(status_code=400, detail="Groq API key is not configured in .env file.")
        if model:
            llm_client.default_model = model
        logger.info("Using Groq for questionnaire processing (model=%s)", model)

    elif provider == "openrouter":
        llm_client = request.app.state.openrouter_client
        if not llm_client.api_key:
            raise HTTPException(status_code=400, detail="OpenRouter API key is not configured in .env file.")
        if model:
            llm_client.default_model = model
        logger.info("Using OpenRouter for questionnaire processing (model=%s)", model)

    else:
        # Default: Ollama (local)
        llm_client = request.app.state.ollama_client
        ollama_available = await llm_client.check_availability()
        if not ollama_available:
            raise HTTPException(
                status_code=503,
                detail="Ollama is not running. Please start Ollama or choose a cloud provider."
            )
        if model:
            llm_client.chat_model = model
        logger.info("Using Ollama for questionnaire processing (model=%s)", model or llm_client.chat_model)

    # Build question list with the selected question column
    question_column = run_request.question_column
    questions = []

    for q in file_info["questions"]:
        question_text = q["raw_row_data"].get(question_column, "").strip()
        questions.append(QuestionRow(
            row_index=q["row_index"],
            question_text=question_text,
            raw_row_data=q["raw_row_data"],
        ))

    # Build column mapping for output
    column_mapping = {
        "answer_column": run_request.answer_column,
        "status_column": run_request.status_column,
        "evidence_column": run_request.evidence_column,
        "page_column": run_request.page_column,
        "confidence_column": run_request.confidence_column,
        "evidence_strength_column": run_request.evidence_strength_column,
        "gap_column": run_request.gap_column,
        "explanation_column": getattr(run_request, "explanation_column", None),
        "framework_column": run_request.framework_column,
        "review_flag_column": run_request.review_flag_column,
        "source_file_column": getattr(run_request, "source_file_column", None),
    }

    total = len(questions)
    app = request.app  # Capture reference for use in closure

    async def event_generator():
        """SSE event generator for questionnaire processing."""
        start_time = time.time()
        all_results = []
        errors = []

        async def progress_callback(current, total_q, question_text, result):
            """Called after each question is processed."""
            pass  # We handle events inline below

        try:
            # Process questions one by one with live SSE events
            for i, question in enumerate(questions):
                try:
                    # Send progress event
                    yield {
                        "event": "progress",
                        "data": json.dumps({
                            "current": i + 1,
                            "total": total,
                            "question": question.question_text[:150],
                        })
                    }

                    # Process single question
                    from services.batch_rag import _process_single_question
                    from services.prompt_builder import detect_framework_hint

                    docs = vector_store.list_documents()
                    doc_filenames = [d["filename"] for d in docs]
                    framework_hint = detect_framework_hint(doc_filenames)

                    result = await _process_single_question(
                        question=question,
                        vector_store=vector_store,
                        llm_client=llm_client,
                        framework_hint=framework_hint,
                    )

                    all_results.append(result)

                    # Send row_done event
                    yield {
                        "event": "row_done",
                        "data": json.dumps(result.model_dump())
                    }

                except Exception as e:
                    logger.error("Error on row %d: %s", question.row_index, e)
                    error_result = AnswerResult(
                        row_index=question.row_index,
                        question=question.question_text,
                        answer=f"Error: {str(e)}",
                        confidence_score=0.0,
                        status="No Evidence",
                    )
                    all_results.append(error_result)
                    errors.append({"row": question.row_index, "error": str(e)})

                    # Send non-fatal error event
                    yield {
                        "event": "error",
                        "data": json.dumps({"row": question.row_index, "error": str(e)})
                    }

            # Write results to Excel
            try:
                questionnaire_dir = _get_questionnaire_dir_from_app(app)
                output_path = write_results(
                    file_path=file_info["file_path"],
                    results=[r.model_dump() for r in all_results],
                    column_mapping=column_mapping,
                    output_dir=questionnaire_dir,
                )

                # Create download token
                download_token = str(uuid.uuid4())
                download_tokens = _get_download_tokens(app)
                download_tokens[download_token] = {
                    "path": output_path,
                    "created": time.time(),
                    "filename": file_info["original_filename"].replace(".xlsx", "_filled.xlsx"),
                }

                # Update registry
                file_info["output_path"] = output_path

            except Exception as e:
                logger.error("Failed to write Excel results: %s", e, exc_info=True)
                download_token = None

            # Compute summary
            elapsed = round(time.time() - start_time, 1)
            compliant = sum(1 for r in all_results if r.status == "Compliant")
            partial = sum(1 for r in all_results if r.status == "Partial")
            no_evidence = sum(1 for r in all_results if r.status == "No Evidence")

            yield {
                "event": "complete",
                "data": json.dumps({
                    "download_token": download_token,
                    "summary": {
                        "total_processed": len(all_results),
                        "compliant": compliant,
                        "partial": partial,
                        "no_evidence": no_evidence,
                        "errors": len(errors),
                        "processing_time_seconds": elapsed,
                    }
                })
            }

        except Exception as e:
            logger.error("Questionnaire processing failed: %s", e, exc_info=True)
            yield {
                "event": "error",
                "data": json.dumps({"row": -1, "error": f"Fatal error: {str(e)}"})
            }

    return EventSourceResponse(event_generator(), media_type="text/event-stream")


@router.get("/download/{download_token}")
async def download_questionnaire(request: Request, download_token: str):
    """
    Download a completed questionnaire .xlsx file.

    Token expires after 1 hour.
    """
    download_tokens = _get_download_tokens(request.app)

    # Clean expired tokens
    _clean_expired_tokens(download_tokens)

    token_info = download_tokens.get(download_token)
    if not token_info:
        raise HTTPException(status_code=404, detail="Download token not found or expired.")

    file_path = token_info["path"]
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Output file not found.")

    return FileResponse(
        path=file_path,
        filename=token_info.get("filename", "questionnaire_filled.xlsx"),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ─── Helper Functions ─────────────────────────────────────────────────

def _get_questionnaire_dir(request: Request) -> str:
    """Get or create the questionnaires storage directory."""
    base_dir = os.path.dirname(os.path.dirname(__file__))
    questionnaire_dir = os.path.join(base_dir, "storage", "questionnaires")
    os.makedirs(questionnaire_dir, exist_ok=True)
    return questionnaire_dir


def _get_questionnaire_dir_from_app(app) -> str:
    """Get or create the questionnaires storage directory from app reference."""
    base_dir = os.path.dirname(os.path.dirname(__file__))
    questionnaire_dir = os.path.join(base_dir, "storage", "questionnaires")
    os.makedirs(questionnaire_dir, exist_ok=True)
    return questionnaire_dir


def _get_registry(request: Request) -> dict:
    """Get the questionnaire registry from app state."""
    if not hasattr(request.app.state, "questionnaire_registry"):
        request.app.state.questionnaire_registry = {}
    return request.app.state.questionnaire_registry


def _get_download_tokens(app) -> dict:
    """Get the download tokens dict from app state."""
    if not hasattr(app.state, "download_tokens"):
        app.state.download_tokens = {}
    return app.state.download_tokens


def _clean_expired_tokens(tokens: dict) -> None:
    """Remove expired download tokens (older than 1 hour)."""
    now = time.time()
    expired = [k for k, v in tokens.items() if now - v.get("created", 0) > TOKEN_EXPIRY_SECONDS]
    for k in expired:
        del tokens[k]
    if expired:
        logger.info("Cleaned %d expired download tokens", len(expired))


async def cleanup_old_questionnaires():
    """Delete questionnaire files older than 24 hours."""
    base_dir = os.path.dirname(os.path.dirname(__file__))
    questionnaire_dir = os.path.join(base_dir, "storage", "questionnaires")

    if not os.path.exists(questionnaire_dir):
        return

    now = time.time()
    max_age = 24 * 3600  # 24 hours
    cleaned = 0

    for filename in os.listdir(questionnaire_dir):
        filepath = os.path.join(questionnaire_dir, filename)
        if os.path.isfile(filepath):
            file_age = now - os.path.getmtime(filepath)
            if file_age > max_age:
                try:
                    os.remove(filepath)
                    cleaned += 1
                except Exception as e:
                    logger.warning("Failed to clean %s: %s", filepath, e)

    if cleaned:
        logger.info("Cleaned %d old questionnaire files", cleaned)


async def periodic_cleanup():
    """Run questionnaire file cleanup every 6 hours."""
    while True:
        try:
            await cleanup_old_questionnaires()
        except Exception as e:
            logger.error("Cleanup error: %s", e)
        await asyncio.sleep(6 * 3600)
