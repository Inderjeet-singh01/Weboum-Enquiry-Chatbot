#!/usr/bin/env python3
"""Offline embedding builder script using Google Gemini Embedding 2.

Reads company-docs/weboum_knowledge.json, creates structure-aware chunks,
computes 768-dimensional dense vector embeddings using gemini-embedding-2,
and saves the resulting index to data/embeddings.pkl for fast runtime cosine similarity.
"""

from __future__ import annotations

import argparse
import json
import logging
import pickle
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np

# Ensure project root is on sys.path so 'import app' works when run directly
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings
from app.services.embedding import embed_documents, get_gemini_client

logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_KNOWLEDGE_PATH = "company-docs/weboum_knowledge.json"
DEFAULT_OUTPUT_PATH = "data/embeddings.pkl"


def build_chunks(knowledge_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert knowledge JSON documents into semantic searchable chunks."""
    docs = knowledge_data.get("documents", [])
    chunks: list[dict[str, Any]] = []

    for doc in docs:
        doc_id = str(doc.get("id", ""))
        title = str(doc.get("title", ""))
        topic = str(doc.get("topic", ""))
        category = str(doc.get("category", ""))
        urls = doc.get("source_urls") or []
        content = str(doc.get("content", ""))

        raw_sections = re.split(r"\n(?=#{1,4}\s)", content)
        merged_sections: list[str] = []
        current_sec = ""

        for sec in raw_sections:
            sec = sec.strip()
            if not sec:
                continue
            if len(current_sec.split()) < 80:
                current_sec = f"{current_sec}\n\n{sec}".strip()
            else:
                merged_sections.append(current_sec)
                current_sec = sec
        if current_sec:
            merged_sections.append(current_sec)

        for sec_idx, sec in enumerate(merged_sections):
            if len(sec) < 20:
                continue

            # Contextual embedding text including title and topic metadata
            context_header = f"Title: {title}\nTopic: {topic}\nCategory: {category}\n"
            if topic == "company:contact" or "Sector 72" in sec or "Mohali" in sec:
                context_header += "Weboum Technology Office Address, Contact Information, Location, Timings:\n"

            embed_text = f"{context_header}\n{sec}"

            chunks.append({
                "chunk_id": f"{doc_id}-{sec_idx}",
                "doc_id": doc_id,
                "title": title,
                "topic": topic,
                "category": category,
                "source_urls": urls,
                "text": sec,
                "embed_text": embed_text,
            })

    return chunks


def create_embeddings(
    knowledge_path: Path,
    output_path: Path,
    api_key: str | None = None,
    batch_size: int = 50,
) -> None:
    if not knowledge_path.exists():
        raise FileNotFoundError(f"Knowledge file not found at: {knowledge_path}")

    logger.info("Loading knowledge JSON from: %s", knowledge_path)
    with open(knowledge_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    chunks = build_chunks(data)
    total_chunks = len(chunks)
    logger.info("Generated %d chunks from %d documents", total_chunks, len(data.get("documents", [])))

    client = get_gemini_client(api_key=api_key)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output_path.parent / f".{output_path.stem}_checkpoint.pkl"

    all_vectors: list[list[float]] = []
    if checkpoint_path.exists():
        try:
            with open(checkpoint_path, "rb") as fh:
                cp = pickle.load(fh)
            if cp.get("chunks_count") == total_chunks and "vectors" in cp:
                all_vectors = cp["vectors"]
                logger.info("Resuming from checkpoint: %d/%d chunks already embedded.", len(all_vectors), total_chunks)
        except Exception as err:
            logger.warning("Failed to load checkpoint (%s); starting fresh.", err)
            all_vectors = []

    logger.info(
        "Computing 768-dimensional embeddings using %s (batch size: %d)...",
        settings.GEMINI_EMBEDDING_MODEL,
        batch_size,
    )

    start_idx = len(all_vectors)
    for i in range(start_idx, total_chunks, batch_size):
        batch = chunks[i : i + batch_size]
        batch_num = i // batch_size + 1
        total_batches = (total_chunks + batch_size - 1) // batch_size
        logger.info("Processing batch %d/%d (chunks %d-%d of %d)...", batch_num, total_batches, i + 1, min(i + batch_size, total_chunks), total_chunks)

        batch_vectors = embed_documents(batch, client=client, batch_size=batch_size)
        all_vectors.extend(batch_vectors.tolist())

        # Save checkpoint after each batch
        with open(checkpoint_path, "wb") as fh:
            pickle.dump({"chunks_count": total_chunks, "vectors": all_vectors}, fh, protocol=pickle.HIGHEST_PROTOCOL)

    embeddings = np.asarray(all_vectors, dtype=np.float32)
    if embeddings.shape != (total_chunks, settings.EMBEDDING_DIMENSION):
        raise ValueError(
            f"Embedding matrix shape mismatch: expected ({total_chunks}, {settings.EMBEDDING_DIMENSION}), "
            f"got {embeddings.shape}"
        )

    payload = {
        "version": 2,
        "embedding_model": settings.GEMINI_EMBEDDING_MODEL,
        "embedding_dimension": settings.EMBEDDING_DIMENSION,
        "chunks": chunks,
        "embeddings": embeddings,
    }

    logger.info("Saving complete embeddings index to: %s (shape: %s)", output_path, embeddings.shape)
    with open(output_path, "wb") as fh:
        pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)

    if checkpoint_path.exists():
        checkpoint_path.unlink()

    logger.info("Successfully generated Gemini embeddings file at: %s", output_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Gemini Embedding 2 pickle file from knowledge JSON.")
    parser.add_argument("--knowledge", default=DEFAULT_KNOWLEDGE_PATH, help="Path to knowledge JSON")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_PATH, help="Path to output embeddings.pkl")
    parser.add_argument("--api-key", default=None, help="Google Gemini API key (defaults to GEMINI_API_KEY in .env)")
    parser.add_argument("--batch-size", type=int, default=50, help="Batch size for embedding generation")

    args = parser.parse_args()
    create_embeddings(
        knowledge_path=Path(args.knowledge),
        output_path=Path(args.output),
        api_key=args.api_key,
        batch_size=args.batch_size,
    )


if __name__ == "__main__":
    main()
