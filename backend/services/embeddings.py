"""
Embedding Service — Generate embeddings using Ollama's nomic-embed-text model.

Fully local, no external API calls. Batch processing supported.
"""

import logging
from typing import List

import ollama

logger = logging.getLogger("complianceai.embeddings")

# Default embedding model
EMBEDDING_MODEL = "nomic-embed-text"

# Maximum batch size for embedding requests
MAX_BATCH_SIZE = 32


def embed_texts(texts: List[str], model: str = EMBEDDING_MODEL) -> List[List[float]]:
    """
    Generate embeddings for a list of texts using local Ollama.

    Args:
        texts: List of text strings to embed.
        model: Embedding model name (default: nomic-embed-text).

    Returns:
        List of embedding vectors (each a list of floats).
    """
    if not texts:
        logger.warning("embed_texts called with empty texts list")
        return []

    logger.info("Embedding %d texts with model: %s", len(texts), model)

    all_embeddings: List[List[float]] = []

    # Process in batches
    for i in range(0, len(texts), MAX_BATCH_SIZE):
        batch = texts[i:i + MAX_BATCH_SIZE]
        batch_num = (i // MAX_BATCH_SIZE) + 1
        total_batches = (len(texts) + MAX_BATCH_SIZE - 1) // MAX_BATCH_SIZE

        logger.info(
            "Embedding batch %d/%d (%d texts)", batch_num, total_batches, len(batch)
        )

        try:
            response = ollama.embed(model=model, input=batch)
            embeddings = response.get("embeddings", [])

            if len(embeddings) != len(batch):
                logger.warning(
                    "Expected %d embeddings, got %d", len(batch), len(embeddings)
                )

            all_embeddings.extend(embeddings)

        except Exception as e:
            logger.error("Embedding batch %d failed: %s", batch_num, e, exc_info=True)
            # Return zero vectors as fallback for failed batch
            dim = _get_embedding_dimension(model)
            for _ in batch:
                all_embeddings.append([0.0] * dim)

    logger.info("Generated %d embeddings using %s", len(all_embeddings), model)
    return all_embeddings


def embed_single(text: str, model: str = EMBEDDING_MODEL) -> List[float]:
    """
    Generate embedding for a single text.

    Args:
        text: Text string to embed.
        model: Embedding model name.

    Returns:
        Embedding vector as list of floats.
    """
    results = embed_texts([text], model=model)
    if results:
        return results[0]
    return []


def _get_embedding_dimension(model: str = EMBEDDING_MODEL) -> int:
    """Get the embedding dimension for a model. Returns default if unknown."""
    # nomic-embed-text produces 768-dimensional embeddings
    dimensions = {
        "nomic-embed-text": 768,
    }
    return dimensions.get(model, 768)
