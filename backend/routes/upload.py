"""
Upload Routes — Handle document upload and processing.

Accepts PDF files, processes them through the full pipeline:
extract -> chunk -> embed -> store in ChromaDB.
Uploaded files are auto-cleaned after successful indexing.
"""

import os
import uuid
import logging
import asyncio
from datetime import datetime

from fastapi import APIRouter, UploadFile, File, Request, HTTPException

from models.schemas import UploadResponse
from services.pdf_processor import extract_pages
from services.chunker import chunk_document
from services.embeddings import embed_texts

logger = logging.getLogger("complianceai.routes.upload")

router = APIRouter(tags=["upload"])

ALLOWED_EXTENSIONS = {".pdf"}
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB


@router.post("/upload", response_model=UploadResponse)
async def upload_document(request: Request, file: UploadFile = File(...)):
    """Upload a document for processing."""
    filename = file.filename or "unknown"
    ext = os.path.splitext(filename)[1].lower()

    logger.info("Upload request: %s (%s)", filename, ext)

    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}. Allowed: PDF")

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail=f"File too large. Maximum: {MAX_FILE_SIZE // (1024*1024)} MB")
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Empty file uploaded")

    # Save to uploads directory
    uploads_dir = request.app.state.uploads_dir
    filepath = os.path.join(uploads_dir, filename)

    # Handle duplicate filenames
    if os.path.exists(filepath):
        name, extension = os.path.splitext(filename)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{name}_{timestamp}{extension}"
        filepath = os.path.join(uploads_dir, filename)

    with open(filepath, "wb") as f:
        f.write(content)

    logger.info("File saved: %s (%d bytes)", filename, len(content))

    # Generate session ID for this upload
    session_id = str(uuid.uuid4())

    # Set initial processing status
    request.app.state.processing_status[filename] = {
        "status": "processing",
        "message": "Extracting text...",
        "progress": 0,
        "page_count": 0,
        "chunk_count": 0,
        "uploaded_at": datetime.now().isoformat(),
        "file_size": len(content),
        "session_id": session_id,
    }

    # Start background processing
    asyncio.create_task(_process_document(request.app, filename, filepath, session_id))

    return UploadResponse(filename=filename, status="processing", message="Document uploaded and processing started.")


@router.get("/upload/status/{filename}")
async def get_processing_status(request: Request, filename: str):
    """Get the processing status of an uploaded document."""
    status = request.app.state.processing_status.get(filename)
    if not status:
        raise HTTPException(status_code=404, detail="Document not found")
    return status


async def _process_document(app, filename: str, filepath: str, session_id: str):
    """Background task: process uploaded document through the full pipeline."""
    logger.info("Processing started: %s", filename)
    status = app.state.processing_status[filename]
    vector_store = app.state.vector_store

    if vector_store is None:
        status["status"] = "error"
        status["message"] = "ChromaDB not initialized"
        return

    try:
        # Step 1: Extract text
        status["message"] = "Extracting text from document..."
        status["progress"] = 10
        pages = extract_pages(filepath)

        if not pages:
            status["status"] = "error"
            status["message"] = "No text content could be extracted from the document."
            return

        status["page_count"] = len(pages)
        status["progress"] = 30

        # Step 2: Chunk text
        status["message"] = "Chunking document..."
        status["progress"] = 40
        loop = asyncio.get_event_loop()
        chunks = await loop.run_in_executor(None, chunk_document, pages, filename, session_id)

        if not chunks:
            status["status"] = "error"
            status["message"] = "No chunks could be generated from the document."
            return

        status["chunk_count"] = len(chunks)
        status["progress"] = 50

        # Step 3: Generate embeddings
        status["message"] = f"Generating embeddings for {len(chunks)} chunks..."
        status["progress"] = 60
        chunk_texts = [chunk.content for chunk in chunks]
        embeddings = await loop.run_in_executor(None, embed_texts, chunk_texts)

        if not embeddings or len(embeddings) != len(chunks):
            status["status"] = "error"
            status["message"] = "Failed to generate embeddings. Is Ollama running with nomic-embed-text?"
            return

        status["progress"] = 80

        # Step 4: Store in ChromaDB
        status["message"] = "Storing in vector database..."
        status["progress"] = 90
        added = vector_store.add_chunks(chunks, embeddings)

        # Step 5: Auto-cleanup uploaded file
        try:
            os.remove(filepath)
            logger.info("Auto-cleaned uploaded file: %s", filepath)
        except Exception as e:
            logger.warning("Failed to auto-clean file %s: %s", filepath, e)

        # Done
        status["status"] = "ready"
        status["message"] = f"Processed: {len(pages)} pages, {added} chunks indexed."
        status["progress"] = 100
        logger.info("Document processed: %s (%d pages, %d chunks)", filename, len(pages), added)

    except Exception as e:
        logger.error("Processing failed for %s: %s", filename, e, exc_info=True)
        status["status"] = "error"
        status["message"] = f"Processing failed: {str(e)}"
