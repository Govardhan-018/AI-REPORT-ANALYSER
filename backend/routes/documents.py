"""
Documents Routes — Document management endpoints.

List, inspect, and delete uploaded documents.
"""

import os
import logging

from fastapi import APIRouter, Request, HTTPException

from models.schemas import DocumentInfo
from services.vector_store import VectorStoreService

logger = logging.getLogger("complianceai.routes.documents")

router = APIRouter(tags=["documents"])


@router.get("/documents")
async def list_documents(request: Request):
    """List all uploaded and indexed documents with metadata."""
    vector_store: VectorStoreService = request.app.state.vector_store
    processing_status = request.app.state.processing_status

    documents = []

    if vector_store:
        indexed_docs = vector_store.list_documents()
        for doc in indexed_docs:
            filename = doc["filename"]
            status_info = processing_status.get(filename, {})
            documents.append(DocumentInfo(
                filename=filename,
                page_count=status_info.get("page_count", 0),
                chunk_count=doc["chunk_count"],
                uploaded_at=status_info.get("uploaded_at", ""),
                status=status_info.get("status", "ready"),
                file_size=status_info.get("file_size", 0),
            ))

    # Add documents that are still processing
    indexed_filenames = {d.filename for d in documents}
    for filename, status_info in processing_status.items():
        if filename not in indexed_filenames:
            documents.append(DocumentInfo(
                filename=filename,
                page_count=status_info.get("page_count", 0),
                chunk_count=status_info.get("chunk_count", 0),
                uploaded_at=status_info.get("uploaded_at", ""),
                status=status_info.get("status", "pending"),
                file_size=status_info.get("file_size", 0),
            ))

    return {"documents": documents}


@router.delete("/documents/{filename}")
async def delete_document(request: Request, filename: str):
    """Delete a document from the vector store and uploads directory."""
    vector_store: VectorStoreService = request.app.state.vector_store
    uploads_dir = request.app.state.uploads_dir

    deleted_chunks = 0
    if vector_store:
        deleted_chunks = vector_store.delete_document(filename)

    # Remove uploaded file if it still exists
    filepath = os.path.join(uploads_dir, filename)
    if os.path.exists(filepath):
        try:
            os.remove(filepath)
        except Exception as e:
            logger.error("Failed to delete file %s: %s", filepath, e)

    # Remove processing status
    if filename in request.app.state.processing_status:
        del request.app.state.processing_status[filename]

    return {"filename": filename, "deleted_chunks": deleted_chunks, "message": f"Document '{filename}' deleted."}
