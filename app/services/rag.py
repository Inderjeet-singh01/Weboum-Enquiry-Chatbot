"""Runtime retrieval service using dense vector embeddings without an external vector DB.

This module owns everything related to embedding-based retrieval:
1. Loading precomputed dense vector embeddings from ``data/embeddings.pkl``.
2. Lazy-loading the local SentenceTransformer embedding model (``BAAI/bge-small-en-v1.5``).
3. Encoding user queries with the BGE query prefix and calculating cosine similarity
   using fast NumPy matrix multiplication (dot product over L2-normalized vectors).
4. Selecting diverse, non-duplicate chunks within the context character budget.

No external vector database (Chroma, Pinecone, Qdrant, Milvus) is required.
All vectors reside in memory as a NumPy matrix for ultra-fast local retrieval.
"""

from __future__ import annotations

import logging
import pickle
import threading
from pathlib import Path
from typing import Any

import numpy as np

from app.core.config import settings

logger = logging.getLogger(__name__)

BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages:"

INDEX_KEYS = {"version", "embedding_model", "embedding_dimension", "chunks", "embeddings"}


class RAGError(Exception):
    """Base error for the RAG retrieval pipeline."""


class RAGIndexError(RAGError):
    """Raised when the retrieval index file is missing, corrupt or incompatible."""


class RAGModelError(RAGError):
    """Raised when the embedding model cannot be loaded."""


_index: dict[str, Any] | None = None
_embedding_model: Any = None
_state_lock = threading.Lock()


def reset_rag_state() -> None:
    """Clear cached models and index. Intended for tests and reloading."""
    global _index, _embedding_model
    with _state_lock:
        _index = None
        _embedding_model = None


def get_index_path() -> Path:
    return settings.rag_index_path


def _get_embedding_model() -> Any:
    """Lazy load the sentence transformer model once."""
    global _embedding_model
    if _embedding_model is not None:
        return _embedding_model

    with _state_lock:
        if _embedding_model is not None:
            return _embedding_model

        from sentence_transformers import SentenceTransformer

        logger.info("Loading embedding model: %s", settings.EMBEDDING_MODEL)
        try:
            _embedding_model = SentenceTransformer(settings.EMBEDDING_MODEL)
        except Exception as exc:
            logger.error("Failed to load embedding model %s: %s", settings.EMBEDDING_MODEL, exc)
            raise RAGModelError(f"Failed to load embedding model {settings.EMBEDDING_MODEL}") from exc
        logger.info("Embedding model loaded successfully: %s", settings.EMBEDDING_MODEL)
        return _embedding_model


def _load_index() -> dict[str, Any]:
    """Load the precomputed vector embeddings index from data/embeddings.pkl."""
    global _index
    if _index is not None:
        return _index

    path = get_index_path()
    with _state_lock:
        if _index is not None:
            return _index

        if not path.exists():
            # If embeddings.pkl is not yet generated, attempt auto-generation from knowledge JSON
            knowledge_file = settings.knowledge_path
            if knowledge_file.exists():
                logger.info("embeddings.pkl not found at %s. Auto-generating from %s...", path, knowledge_file)
                try:
                    from scripts.create_embeddings import create_embeddings
                    create_embeddings(knowledge_file, path, settings.EMBEDDING_MODEL)
                except Exception as exc:
                    logger.error("Failed to auto-generate embeddings.pkl: %s", exc)
                    raise RAGIndexError(f"Embeddings index not found and auto-generation failed: {exc}") from exc
            else:
                raise RAGIndexError(f"Embeddings index not found at {path}")

        try:
            with open(path, "rb") as fh:
                data = pickle.load(fh)
        except Exception as exc:
            logger.error("Embeddings index at %s is unreadable: %s", path, exc)
            raise RAGIndexError(f"Embeddings index at {path} is invalid: {exc}") from exc

        if not isinstance(data, dict) or not INDEX_KEYS.issubset(data.keys()):
            raise RAGIndexError(f"Embeddings index at {path} is missing required keys: {INDEX_KEYS}")

        chunks = data["chunks"]
        embeddings = data["embeddings"]
        if not isinstance(chunks, list) or not isinstance(embeddings, np.ndarray):
            raise RAGIndexError(f"Embeddings index at {path} has invalid chunks or embeddings array")

        if len(chunks) != embeddings.shape[0]:
            raise RAGIndexError(
                f"Mismatch in embeddings index: {len(chunks)} chunks vs {embeddings.shape[0]} vectors"
            )

        logger.info(
            "Loaded embeddings index from %s: %d chunks, dimension %d, model %s",
            path,
            len(chunks),
            embeddings.shape[1],
            data.get("embedding_model"),
        )
        _index = data
        return _index


def embed_query(text: str) -> np.ndarray:
    """Embed user query using BGE instruction prefix and normalize for cosine similarity."""
    model = _get_embedding_model()
    prompt = f"{BGE_QUERY_INSTRUCTION} {text.strip()}"
    vector = model.encode([prompt], normalize_embeddings=True)[0]
    return np.asarray(vector, dtype=np.float32)


def retrieve(
    query: str,
    top_k: int | None = None,
    final_top_k: int | None = None,
) -> list[dict[str, Any]]:
    """Retrieve top relevant chunks using dense vector cosine similarity."""
    if not query or not query.strip():
        return []

    initial_top_k = top_k if top_k is not None else settings.RAG_INITIAL_TOP_K
    final_count = final_top_k if final_top_k is not None else settings.RAG_FINAL_TOP_K

    data = _load_index()
    chunks: list[dict[str, Any]] = data["chunks"]
    embeddings: np.ndarray = data["embeddings"]

    query_vec = embed_query(query)
    # Cosine similarity is dot product because embeddings and query_vec are L2-normalized
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

    logger.info("Embedding retrieval returned %d chunks for query: %s", len(selected), query)
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
