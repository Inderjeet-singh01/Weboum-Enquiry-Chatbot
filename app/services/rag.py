"""Runtime retrieval service using Google Gemini Embedding 2 vectors and NumPy cosine similarity.

Retrieval flow:
1. Load precomputed 768-dimensional dense vector embeddings from ``data/embeddings.pkl``.
2. Generate a 768-dimensional query vector via Google Gemini Embedding 2 API.
3. Compute cosine similarity with fast NumPy matrix dot product over normalized vectors.
4. Select diverse, non-duplicate chunks within the context character budget.

No local embedding models or PyTorch are loaded into memory.
Runtime auto-generation is disabled: indices must be created offline via scripts/create_embeddings.py.
"""

from __future__ import annotations

import logging
import pickle
import threading
import sys
from pathlib import Path
from typing import Any

import numpy as np

# Cross-compatibility shim between NumPy 1.x and NumPy 2.x pickles
if not hasattr(np, "_core") and hasattr(np, "core"):
    sys.modules["numpy._core"] = np.core
    if hasattr(np.core, "numeric"):
        sys.modules["numpy._core.numeric"] = np.core.numeric
    if hasattr(np.core, "multiarray"):
        sys.modules["numpy._core.multiarray"] = np.core.multiarray


class _NumpyCompatUnpickler(pickle.Unpickler):
    """Custom unpickler resolving numpy.core <-> numpy._core discrepancies across versions."""

    def find_class(self, module: str, name: str) -> Any:
        if module.startswith("numpy._core") and not hasattr(np, "_core") and hasattr(np, "core"):
            module = module.replace("numpy._core", "numpy.core", 1)
        elif module.startswith("numpy.core") and hasattr(np, "_core") and not hasattr(np, "core"):
            module = module.replace("numpy.core", "numpy._core", 1)
        return super().find_class(module, name)


from app.core.config import settings
from app.services.embedding import (
    GeminiDimensionError,
    GeminiEmbeddingError,
    embed_query,
)

logger = logging.getLogger(__name__)

INDEX_KEYS = {"version", "embedding_model", "embedding_dimension", "chunks", "embeddings"}


class RAGError(Exception):
    """Base error for the RAG retrieval pipeline."""


class RAGIndexError(RAGError):
    """Raised when the retrieval index file is missing, corrupt, or incompatible."""


_index: dict[str, Any] | None = None
_state_lock = threading.Lock()


def reset_rag_state() -> None:
    """Clear cached index. Intended for tests and reloading."""
    global _index
    with _state_lock:
        _index = None


def get_index_path() -> Path:
    return settings.rag_index_path


def _load_index() -> dict[str, Any]:
    """Load and validate the precomputed Gemini vector embeddings index from data/embeddings.pkl.

    Does NOT automatically generate the index at runtime. If the index is missing,
    corrupted, or uses an incompatible model/dimension, raises RAGIndexError.
    """
    global _index
    if _index is not None:
        return _index

    path = get_index_path()
    with _state_lock:
        if _index is not None:
            return _index

        if not path.exists():
            raise RAGIndexError(
                f"Embeddings index not found at {path}. "
                "Run 'python scripts/create_embeddings.py' offline to generate it."
            )

        try:
            with open(path, "rb") as fh:
                data = _NumpyCompatUnpickler(fh).load()
        except Exception as exc:
            logger.exception("RAG failed")
            raise RAGIndexError(f"Embeddings index at {path} is unreadable: {exc}") from exc

        if not isinstance(data, dict) or not INDEX_KEYS.issubset(data.keys()):
            raise RAGIndexError(
                f"Embeddings index at {path} is missing required keys: {INDEX_KEYS - set(data.keys() if isinstance(data, dict) else [])}"
            )

        # Validate embedding model compatibility
        stored_model = data.get("embedding_model")
        if stored_model != settings.GEMINI_EMBEDDING_MODEL:
            raise RAGIndexError(
                f"Embeddings index model mismatch: expected '{settings.GEMINI_EMBEDDING_MODEL}', "
                f"but found '{stored_model}'. Please regenerate the index with scripts/create_embeddings.py."
            )

        # Validate embedding dimension compatibility
        stored_dimension = data.get("embedding_dimension")
        if stored_dimension != settings.EMBEDDING_DIMENSION:
            raise RAGIndexError(
                f"Embeddings index dimension mismatch: expected {settings.EMBEDDING_DIMENSION}, "
                f"but found {stored_dimension}. Please regenerate the index with scripts/create_embeddings.py."
            )

        chunks = data["chunks"]
        embeddings = data["embeddings"]
        if not isinstance(chunks, list) or not isinstance(embeddings, np.ndarray):
            raise RAGIndexError(f"Embeddings index at {path} has invalid chunks or embeddings array")

        if embeddings.ndim != 2 or embeddings.shape[1] != settings.EMBEDDING_DIMENSION:
            raise RAGIndexError(
                f"Embeddings index array shape mismatch: expected (*, {settings.EMBEDDING_DIMENSION}), "
                f"got {embeddings.shape}"
            )

        if len(chunks) != embeddings.shape[0]:
            raise RAGIndexError(
                f"Mismatch in embeddings index: {len(chunks)} chunks vs {embeddings.shape[0]} vectors"
            )

        logger.info(
            "Loaded embeddings index from %s: %d chunks, dimension %d, model %s",
            path,
            len(chunks),
            embeddings.shape[1],
            stored_model,
        )
        _index = data
        return _index


def retrieve(
    query: str,
    top_k: int | None = None,
    final_top_k: int | None = None,
) -> list[dict[str, Any]]:
    """Retrieve top relevant chunks using Gemini Embedding 2 dense vector cosine similarity."""
    if not query or not query.strip():
        return []

    initial_top_k = top_k if top_k is not None else settings.RAG_INITIAL_TOP_K
    final_count = final_top_k if final_top_k is not None else settings.RAG_FINAL_TOP_K

    data = _load_index()
    chunks: list[dict[str, Any]] = data["chunks"]
    embeddings: np.ndarray = data["embeddings"]

    try:
        query_vec = embed_query(query)
    except (GeminiEmbeddingError, GeminiDimensionError) as exc:
        logger.exception("RAG failed")
        raise RAGError(f"Failed to generate query embedding: {exc}") from exc

    # Cosine similarity via dot product (Gemini Embedding 2 vectors with reduced dimensions are normalized)
    scores = np.dot(embeddings, query_vec)

    if initial_top_k > 0:
        top_indices = np.argsort(scores)[::-1][:initial_top_k]
    else:
        top_indices = np.argsort(scores)[::-1]

    # Select diverse candidates across topics (max 2 chunks per topic)
    selected: list[dict[str, Any]] = []
    topic_count: dict[str, int] = {}
    seen_texts: set[str] = set()

    for idx in top_indices:
        c = dict(chunks[idx])
        c["score"] = float(scores[idx])
        topic = c.get("topic", "")
        text_key = " ".join(str(c.get("text", "")).split()).lower()[:120]

        if text_key in seen_texts:
            continue
        if topic_count.get(topic, 0) >= 2:
            continue

        selected.append(c)
        seen_texts.add(text_key)
        topic_count[topic] = topic_count.get(topic, 0) + 1

        if len(selected) >= final_count:
            break

    logger.info("RAG retrieval completed")
    return selected


def format_context(chunks: list[dict[str, Any]], max_chars: int | None = None) -> str:
    """Build the prompt context block from selected chunks enforcing character budget."""
    if not chunks:
        return ""

    budget = max_chars if max_chars is not None else settings.RAG_MAX_CONTEXT_CHARS
    blocks: list[str] = []
    total = 0

    for chunk in chunks:
        block = _format_chunk(chunk)
        block_len = len(block)

        if blocks and total + block_len > budget:
            break

        if not blocks and block_len > budget:
            block = block[:budget].rstrip()
            block_len = len(block)

        blocks.append(block)
        total += block_len

        if total >= budget:
            break

    return "\n\n".join(blocks)


def _format_chunk(chunk: dict[str, Any]) -> str:
    title = str(chunk.get("title") or "")
    topic = str(chunk.get("topic") or "")
    category = str(chunk.get("category") or "")
    urls = chunk.get("source_urls") or []
    url = str(urls[0]) if isinstance(urls, list) and urls else (str(chunk.get("source_url") or ""))
    content = str(chunk.get("text") or "")

    lines = ["[Source]"]
    if title:
        lines.append(f"Title: {title}")
    if topic:
        lines.append(f"Topic: {topic}")
    if category:
        lines.append(f"Category: {category}")
    if url:
        lines.append(f"URL: {url}")
    lines.append("")
    lines.append(content.strip())
    return "\n".join(lines)
