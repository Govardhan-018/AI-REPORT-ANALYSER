"""
Vector Store Service — ChromaDB persistent local storage.

Manages collections, document indexing, and semantic similarity search.
All data stored locally on disk. No external connections.
"""

import logging
from typing import List, Optional, Dict

import chromadb

from models.schemas import Chunk, SearchResult

logger = logging.getLogger("complianceai.vector_store")

# Default collection name
DEFAULT_COLLECTION = "compliance_docs"


class VectorStoreService:
    """ChromaDB persistent vector store for document chunks."""

    def __init__(self, persist_directory: str):
        """
        Initialize ChromaDB with persistent local storage.

        Args:
            persist_directory: Path to the directory for ChromaDB data.
        """
        self.persist_directory = persist_directory
        self.client = chromadb.PersistentClient(path=persist_directory)
        self.collection = self.client.get_or_create_collection(
            name=DEFAULT_COLLECTION,
            metadata={"hnsw:space": "cosine"},  # Use cosine similarity
        )
        logger.info(
            "ChromaDB initialized: %s (%d existing documents)",
            persist_directory,
            self.collection.count(),
        )

    def add_chunks(
        self,
        chunks: List[Chunk],
        embeddings: List[List[float]],
    ) -> int:
        """
        Add document chunks with their embeddings to the vector store.

        Args:
            chunks: List of document chunks.
            embeddings: Corresponding embedding vectors.

        Returns:
            Number of chunks added.
        """
        if not chunks or not embeddings:
            logger.warning("add_chunks called with empty chunks or embeddings")
            return 0

        if len(chunks) != len(embeddings):
            raise ValueError(
                f"Chunks ({len(chunks)}) and embeddings ({len(embeddings)}) count mismatch"
            )

        logger.info("Adding %d chunks to ChromaDB", len(chunks))

        ids = [chunk.chunk_id for chunk in chunks]
        documents = [chunk.content for chunk in chunks]
        metadatas = [
            {
                "filename": chunk.filename,
                "page_number": chunk.page_number,
                "char_start": chunk.char_start,
                "char_end": chunk.char_end,
                "session_id": chunk.session_id,
            }
            for chunk in chunks
        ]

        # ChromaDB handles batching internally, but we chunk to avoid memory issues
        batch_size = 100
        total_added = 0

        for i in range(0, len(ids), batch_size):
            batch_end = min(i + batch_size, len(ids))
            batch_num = (i // batch_size) + 1
            total_batches = (len(ids) + batch_size - 1) // batch_size

            logger.info("Adding batch %d/%d (%d chunks)", batch_num, total_batches, batch_end - i)

            try:
                self.collection.add(
                    ids=ids[i:batch_end],
                    documents=documents[i:batch_end],
                    embeddings=embeddings[i:batch_end],
                    metadatas=metadatas[i:batch_end],
                )
                batch_added = batch_end - i
                total_added += batch_added
                logger.info("Added batch %d: %d chunks", batch_num, batch_added)
            except Exception as e:
                logger.error("Error adding batch %d: %s", batch_num, e, exc_info=True)
                raise

        logger.info("Total chunks added: %d (collection size: %d)", total_added, self.collection.count())
        return total_added

    def search(
        self,
        query_embedding: List[float],
        top_k: int = 10,  # FIX 2: Increased from 5 to 10 — SOC 2 answers span 6+ chunks
        filename_filter: Optional[str] = None,
    ) -> List[SearchResult]:
        """
        Perform semantic similarity search.

        Args:
            query_embedding: Query embedding vector.
            top_k: Number of results to return.
            filename_filter: Optional filter to search within a specific document.

        Returns:
            List of SearchResult objects ranked by relevance.
        """
        where_filter = None
        if filename_filter:
            where_filter = {"filename": {"$eq": filename_filter}}

        try:
            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k,
                where=where_filter,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            logger.error("Vector search failed: %s", e, exc_info=True)
            return []

        search_results: List[SearchResult] = []

        if results and results["ids"] and results["ids"][0]:
            for i, chunk_id in enumerate(results["ids"][0]):
                metadata = results["metadatas"][0][i] if results["metadatas"] else {}
                document = results["documents"][0][i] if results["documents"] else ""
                distance = results["distances"][0][i] if results["distances"] else 1.0

                # Convert cosine distance to similarity score (0–1)
                similarity = max(0.0, 1.0 - distance)

                search_results.append(SearchResult(
                    chunk_id=chunk_id,
                    filename=metadata.get("filename", "unknown"),
                    page_number=metadata.get("page_number", 0),
                    content=document,
                    score=round(similarity, 4),
                ))

        logger.info("Search returned %d results (top_k=%d)", len(search_results), top_k)
        return search_results

    def delete_document(self, filename: str) -> int:
        """
        Delete all chunks belonging to a specific document.

        Args:
            filename: The document filename to remove.

        Returns:
            Number of chunks deleted.
        """
        try:
            # Get all IDs for this document
            results = self.collection.get(
                where={"filename": {"$eq": filename}},
                include=[],
            )

            if results and results["ids"]:
                ids_to_delete = results["ids"]
                self.collection.delete(ids=ids_to_delete)
                logger.info("Deleted %d chunks for %s", len(ids_to_delete), filename)
                return len(ids_to_delete)

        except Exception as e:
            logger.error("Failed to delete document %s: %s", filename, e)

        return 0

    def list_documents(self) -> List[Dict]:
        """
        List all unique documents in the vector store with chunk counts.

        Returns:
            List of dicts with filename and chunk_count.
        """
        try:
            results = self.collection.get(include=["metadatas"])

            if not results or not results["metadatas"]:
                return []

            # Count chunks per document
            doc_counts: Dict[str, int] = {}
            for metadata in results["metadatas"]:
                filename = metadata.get("filename", "unknown")
                doc_counts[filename] = doc_counts.get(filename, 0) + 1

            return [
                {"filename": fname, "chunk_count": count}
                for fname, count in sorted(doc_counts.items())
            ]

        except Exception as e:
            logger.error("Failed to list documents: %s", e)
            return []

    def get_document_chunk_count(self, filename: str) -> int:
        """Get the number of chunks for a specific document."""
        try:
            results = self.collection.get(
                where={"filename": {"$eq": filename}},
                include=[],
            )
            return len(results["ids"]) if results and results["ids"] else 0
        except Exception:
            return 0

    def get_total_count(self) -> int:
        """Get total number of chunks in the store."""
        return self.collection.count()
