"""Google Gemini Embedding 2 service for document and query embeddings.

Uses the official google-genai SDK to generate 768-dimensional dense vector
embeddings without loading any local embedding models or PyTorch.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import httpx
import numpy as np
from google import genai
from google.genai import errors, types

from app.core.config import settings

logger = logging.getLogger(__name__)


class GeminiEmbeddingError(Exception):
    """Base exception for Gemini embedding failures."""


class MissingGeminiAPIKeyError(GeminiEmbeddingError):
    """Raised when GEMINI_API_KEY is not configured or empty."""


class GeminiAuthenticationError(GeminiEmbeddingError):
    """Raised on authentication/permission failures (401/403)."""


class GeminiRateLimitError(GeminiEmbeddingError):
    """Raised when Gemini API rate limit is reached (429)."""


class GeminiNetworkError(GeminiEmbeddingError):
    """Raised when network/connectivity issues occur."""


class GeminiResponseError(GeminiEmbeddingError):
    """Raised when the Gemini API returns an invalid or empty embedding response."""


class GeminiDimensionError(GeminiEmbeddingError):
    """Raised when the returned embedding vector dimension does not match configured dimension."""


def get_gemini_client(api_key: str | None = None) -> genai.Client:
    """Create a google-genai Client using the configured API key."""
    key = (api_key or settings.GEMINI_API_KEY or "").strip()
    if not key:
        raise MissingGeminiAPIKeyError("GEMINI_API_KEY is not configured")
    return genai.Client(api_key=key)


def _handle_gemini_exception(exc: Exception) -> None:
    """Classify and raise specialized Gemini exceptions while logging."""
    code = getattr(exc, "code", None) or getattr(exc, "status", None)
    msg = str(exc).lower()

    if isinstance(exc, errors.APIError) or code is not None:
        if code in (401, 403) or "unauthenticated" in msg or "permission" in msg or "api key" in msg:
            logger.exception("Gemini API authentication failure")
            raise GeminiAuthenticationError("Gemini API authentication failed") from exc
        if code == 429 or "resource_exhausted" in msg or "rate limit" in msg:
            logger.exception("Gemini API rate-limit failure")
            raise GeminiRateLimitError("Gemini API rate limit exceeded") from exc
        logger.exception("Gemini API request failed")
        raise GeminiEmbeddingError(f"Gemini API request failed with status {code}") from exc

    if isinstance(exc, (httpx.NetworkError, httpx.TimeoutException, ConnectionError, OSError)):
        logger.exception("Gemini API network failure")
        raise GeminiNetworkError(f"Network error connecting to Gemini API: {exc}") from exc

    logger.exception("Gemini API call failed")
    raise GeminiEmbeddingError(f"Gemini embedding failed: {exc}") from exc


def format_query_for_embedding(query: str) -> str:
    """Format query text using asymmetric retrieval prefix."""
    return f"task: search result | query: {query.strip()}"


def format_document_for_embedding(title: str, content: str) -> str:
    """Format document text using title and content."""
    return f"title: {title.strip()} | text: {content.strip()}"


def embed_query(
    query: str,
    client: genai.Client | None = None,
) -> np.ndarray:
    """Embed a user query using Gemini Embedding 2.

    Returns a 1-D NumPy array of shape (EMBEDDING_DIMENSION,) and dtype float32.
    """
    cleaned_query = query.strip() if query else ""
    if not cleaned_query:
        return np.zeros(settings.EMBEDDING_DIMENSION, dtype=np.float32)

    if client is None:
        client = get_gemini_client()

    formatted_query = format_query_for_embedding(cleaned_query)
    config = types.EmbedContentConfig(
        output_dimensionality=settings.EMBEDDING_DIMENSION,
    )

    start_time = time.perf_counter()
    try:
        response = client.models.embed_content(
            model=settings.GEMINI_EMBEDDING_MODEL,
            contents=formatted_query,
            config=config,
        )
    except Exception as exc:
        _handle_gemini_exception(exc)

    duration_ms = (time.perf_counter() - start_time) * 1000
    logger.info("Query embedding completed | duration_ms=%.2f", duration_ms)

    if not response or not response.embeddings:
        raise GeminiResponseError("Gemini embedding response contained no embeddings")

    values = response.embeddings[0].values
    if not values or len(values) != settings.EMBEDDING_DIMENSION:
        raise GeminiDimensionError(
            f"Expected embedding dimension {settings.EMBEDDING_DIMENSION}, got {len(values) if values else 0}"
        )

    return np.asarray(values, dtype=np.float32)


def embed_documents(
    documents: list[dict[str, Any]],
    client: genai.Client | None = None,
    batch_size: int = 50,
) -> np.ndarray:
    """Generate separate embeddings for multiple documents.

    Uses separate types.Content objects to ensure Gemini Embedding 2 returns one
    embedding per input document rather than an aggregated vector.
    """
    if not documents:
        return np.empty((0, settings.EMBEDDING_DIMENSION), dtype=np.float32)

    if client is None:
        client = get_gemini_client()

    all_vectors: list[list[float]] = []
    config = types.EmbedContentConfig(
        output_dimensionality=settings.EMBEDDING_DIMENSION,
    )

    for i in range(0, len(documents), batch_size):
        batch = documents[i : i + batch_size]
        contents: list[types.Content] = []
        for doc in batch:
            title = str(doc.get("title") or "")
            content = str(doc.get("text") or doc.get("content") or doc.get("embed_text") or "")
            formatted_text = format_document_for_embedding(title, content)
            contents.append(types.Content(parts=[types.Part(text=formatted_text)]))

        max_retries = 10
        backoff = 5.0
        response = None
        for attempt in range(max_retries):
            try:
                response = client.models.embed_content(
                    model=settings.GEMINI_EMBEDDING_MODEL,
                    contents=contents,
                    config=config,
                )
                break
            except Exception as exc:
                err_str = str(exc).lower()
                is_rate_limit = "429" in err_str or "resource_exhausted" in err_str or "quota" in err_str
                if attempt == max_retries - 1:
                    _handle_gemini_exception(exc)
                if is_rate_limit:
                    match = re.search(r"retry in (\d+(?:\.\d+)?)s", str(exc), re.IGNORECASE)
                    parsed_delay = float(match.group(1)) + 2.0 if match else 62.0
                    sleep_time = max(parsed_delay, 62.0)
                    logger.warning(
                        "Gemini rate limit reached (Free tier: 100 requests/min). Waiting %.1fs for quota window to reset...",
                        sleep_time,
                    )
                else:
                    sleep_time = backoff
                    logger.warning(
                        "Retrying Gemini embed_content (attempt %d/%d) after error: %s",
                        attempt + 1,
                        max_retries,
                        exc,
                    )
                    backoff *= 2
                time.sleep(sleep_time)

        logger.info(
            "Successfully embedded batch %d/%d (%d chunks)",
            i // batch_size + 1,
            (len(documents) + batch_size - 1) // batch_size,
            len(batch),
        )

        if not response or not response.embeddings or len(response.embeddings) != len(batch):
            received_count = len(response.embeddings) if response and response.embeddings else 0
            raise GeminiResponseError(
                f"Expected {len(batch)} embeddings, received {received_count}"
            )

        for emb in response.embeddings:
            values = emb.values
            if not values or len(values) != settings.EMBEDDING_DIMENSION:
                raise GeminiDimensionError(
                    f"Expected embedding dimension {settings.EMBEDDING_DIMENSION}, got {len(values) if values else 0}"
                )
            all_vectors.append(values)

    return np.asarray(all_vectors, dtype=np.float32)
