#!/usr/bin/env python3
"""Offline embedding builder script.

Reads company-docs/weboum_knowledge.json, creates structure-aware chunks,
computes dense vector embeddings using BAAI/bge-small-en-v1.5, and saves
the resulting index to data/embeddings.pkl for fast runtime cosine similarity.
"""

from __future__ import annotations

import argparse
import json
import logging
import pickle
import re
from pathlib import Path
from typing import Any

import numpy as np
from sentence_transformers import SentenceTransformer

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_KNOWLEDGE_PATH = "company-docs/weboum_knowledge.json"
DEFAULT_OUTPUT_PATH = "data/embeddings.pkl"
DEFAULT_MODEL_NAME = "BAAI/bge-small-en-v1.5"


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
    model_name: str = DEFAULT_MODEL_NAME,
) -> None:
    if not knowledge_path.exists():
        raise FileNotFoundError(f"Knowledge file not found at: {knowledge_path}")

    logger.info("Loading knowledge JSON from: %s", knowledge_path)
    with open(knowledge_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    chunks = build_chunks(data)
    logger.info("Generated %d chunks from %d documents", len(chunks), len(data.get("documents", [])))

    logger.info("Loading SentenceTransformer model: %s", model_name)
    model = SentenceTransformer(model_name)

    texts_to_embed = [c["embed_text"] for c in chunks]
    logger.info("Computing dense vector embeddings for %d chunks...", len(texts_to_embed))
    embeddings = model.encode(
        texts_to_embed,
        batch_size=32,
        show_progress_bar=True,
        normalize_embeddings=True,
    )
    embeddings = np.asarray(embeddings, dtype=np.float32)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "embedding_model": model_name,
        "embedding_dimension": int(embeddings.shape[1]),
        "chunks": chunks,
        "embeddings": embeddings,
    }

    logger.info("Saving embeddings index to: %s (shape: %s)", output_path, embeddings.shape)
    with open(output_path, "wb") as fh:
        pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)

    logger.info("Successfully generated embeddings file at: %s", output_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate embeddings pickle file from knowledge JSON.")
    parser.add_argument("--knowledge", default=DEFAULT_KNOWLEDGE_PATH, help="Path to knowledge JSON")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_PATH, help="Path to output embeddings.pkl")
    parser.add_argument("--model", default=DEFAULT_MODEL_NAME, help="Embedding model name")

    args = parser.parse_args()
    create_embeddings(
        knowledge_path=Path(args.knowledge),
        output_path=Path(args.output),
        model_name=args.model,
    )


if __name__ == "__main__":
    main()
