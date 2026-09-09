import pickle
import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

from app.services import embedding, rag
from app.services.embedding import (
    GeminiDimensionError,
    GeminiEmbeddingError,
    MissingGeminiAPIKeyError,
    format_document_for_embedding,
    format_query_for_embedding,
)


def test_format_query_asymmetric_prefix():
    """Verify asymmetric retrieval formatting for queries."""
    formatted = format_query_for_embedding("What services do you provide?")
    assert formatted == "task: search result | query: What services do you provide?"


def test_format_document_prefix():
    """Verify document formatting with title and text."""
    formatted = format_document_for_embedding("About Us", "We are an AI company.")
    assert formatted == "title: About Us | text: We are an AI company."


def test_gemini_query_embedding_returns_768_dim_vector():
    """Verify unpatched embed_query returns a 768-dimensional float32 vector."""
    # Create mock GenAI client
    mock_client = MagicMock()
    mock_values = [0.01 * (i % 50) for i in range(768)]
    mock_response = MagicMock()
    mock_response.embeddings = [MagicMock(values=mock_values)]
    mock_client.models.embed_content.return_value = mock_response

    # Call original embed_query function from embedding module
    from app.services.embedding import embed_query as real_embed_query
    vec = real_embed_query("What services do you provide?", client=mock_client)

    assert isinstance(vec, np.ndarray)
    assert vec.shape == (768,)
    assert vec.dtype == np.float32

    # Verify embed_content arguments
    mock_client.models.embed_content.assert_called_once()
    call_kwargs = mock_client.models.embed_content.call_args[1]
    assert call_kwargs["model"] == "gemini-embedding-2"
    assert call_kwargs["contents"] == "task: search result | query: What services do you provide?"
    assert call_kwargs["config"].output_dimensionality == 768


def test_gemini_documents_embedding_returns_n_by_768():
    """Verify embed_documents uses separate Content objects and returns (N, 768)."""
    mock_client = MagicMock()
    docs = [
        {"title": "Doc 1", "text": "Content 1"},
        {"title": "Doc 2", "text": "Content 2"},
    ]
    mock_vec = [0.05] * 768
    mock_response = MagicMock()
    mock_response.embeddings = [MagicMock(values=mock_vec), MagicMock(values=mock_vec)]
    mock_client.models.embed_content.return_value = mock_response

    mat = embedding.embed_documents(docs, client=mock_client, batch_size=50)

    assert mat.shape == (2, 768)
    assert mat.dtype == np.float32
    call_kwargs = mock_client.models.embed_content.call_args[1]
    contents = call_kwargs["contents"]
    assert len(contents) == 2
    # Verify separate Content objects
    assert contents[0].parts[0].text == "title: Doc 1 | text: Content 1"
    assert contents[1].parts[0].text == "title: Doc 2 | text: Content 2"


def test_rag_index_loads_successfully():
    """Verify precomputed 768-dim index loads cleanly."""
    rag.reset_rag_state()
    index = rag._load_index()
    assert index["embedding_model"] == "gemini-embedding-2"
    assert index["embedding_dimension"] == 768
    assert isinstance(index["embeddings"], np.ndarray)
    assert index["embeddings"].shape[1] == 768
    assert len(index["chunks"]) == index["embeddings"].shape[0]


def test_rag_dimension_mismatch_rejected(monkeypatch, tmp_path):
    """Verify index loading fails if stored dimension is not 768."""
    rag.reset_rag_state()
    bad_index_file = tmp_path / "bad_dim.pkl"
    payload = {
        "version": 2,
        "embedding_model": "gemini-embedding-2",
        "embedding_dimension": 384,  # Old BGE dimension
        "chunks": [{"title": "t", "text": "c"}],
        "embeddings": np.ones((1, 384), dtype=np.float32),
    }
    with open(bad_index_file, "wb") as f:
        pickle.dump(payload, f)

    monkeypatch.setattr(rag.settings, "RAG_INDEX_PATH", str(bad_index_file))
    with pytest.raises(rag.RAGIndexError, match="dimension mismatch"):
        rag._load_index()
    rag.reset_rag_state()


def test_rag_model_mismatch_rejected(monkeypatch, tmp_path):
    """Verify index loading fails if stored model is not gemini-embedding-2."""
    rag.reset_rag_state()
    bad_index_file = tmp_path / "bad_model.pkl"
    payload = {
        "version": 1,
        "embedding_model": "BAAI/bge-small-en-v1.5",
        "embedding_dimension": 768,
        "chunks": [{"title": "t", "text": "c"}],
        "embeddings": np.ones((1, 768), dtype=np.float32),
    }
    with open(bad_index_file, "wb") as f:
        pickle.dump(payload, f)

    monkeypatch.setattr(rag.settings, "RAG_INDEX_PATH", str(bad_index_file))
    with pytest.raises(rag.RAGIndexError, match="model mismatch"):
        rag._load_index()
    rag.reset_rag_state()


def test_rag_retrieval_works_with_gemini_vector():
    """Verify retrieval returns relevant chunks using the 768-dim query vector."""
    rag.reset_rag_state()
    chunks = rag.retrieve("What services do you provide?")
    assert len(chunks) > 0
    assert "text" in chunks[0]
    assert "title" in chunks[0]
    assert "score" in chunks[0]


def test_rag_empty_query_returns_empty():
    assert rag.retrieve("") == []
    assert rag.retrieve("   ") == []


def test_rag_format_context_respects_budget():
    chunks = rag.retrieve("Tell me about Weboum")
    context = rag.format_context(chunks, max_chars=500)
    assert len(context) <= 600
    assert "[Source]" in context


def test_rag_handles_missing_file(monkeypatch, tmp_path):
    rag.reset_rag_state()
    monkeypatch.setattr(rag.settings, "RAG_INDEX_PATH", str(tmp_path / "nonexistent.pkl"))
    with pytest.raises(rag.RAGIndexError, match="not found"):
        rag.retrieve("anything")
    rag.reset_rag_state()


def test_no_sentence_transformer_imported_at_runtime():
    """Verify that sentence_transformers and torch are not imported by the RAG runtime service."""
    # Ensure rag module is loaded
    import app.services.rag
    import app.services.embedding

    # Verify no sentence_transformers or torch in sys.modules
    assert "sentence_transformers" not in sys.modules, "sentence_transformers must not be loaded"
    assert "torch" not in sys.modules, "torch must not be loaded"


def test_missing_gemini_api_key_produces_clear_error(monkeypatch):
    """Verify missing GEMINI_API_KEY raises MissingGeminiAPIKeyError."""
    monkeypatch.setattr(embedding.settings, "GEMINI_API_KEY", "")
    with pytest.raises(MissingGeminiAPIKeyError):
        embedding.get_gemini_client()


def test_gemini_dimension_error_on_mismatched_vector():
    """Verify GeminiDimensionError is raised when API returns unexpected dimensionality."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    # Return 512 dimensions instead of 768
    mock_response.embeddings = [MagicMock(values=[0.1] * 512)]
    mock_client.models.embed_content.return_value = mock_response

    from app.services.embedding import embed_query as real_embed_query
    with pytest.raises(GeminiDimensionError):
        real_embed_query("test query", client=mock_client)


def test_gemini_api_auth_error_raised():
    """Verify GeminiAuthenticationError is raised on 401/403."""
    from google.genai.errors import APIError
    mock_client = MagicMock()
    err = APIError(401, {"error": {"message": "API key not valid"}})
    mock_client.models.embed_content.side_effect = err

    from app.services.embedding import GeminiAuthenticationError, embed_query as real_embed_query
    with pytest.raises(GeminiAuthenticationError):
        real_embed_query("test query", client=mock_client)


def test_gemini_api_rate_limit_error_raised():
    """Verify GeminiRateLimitError is raised on 429."""
    from google.genai.errors import APIError
    mock_client = MagicMock()
    err = APIError(429, {"error": {"message": "Rate limit exceeded"}})
    mock_client.models.embed_content.side_effect = err

    from app.services.embedding import GeminiRateLimitError, embed_query as real_embed_query
    with pytest.raises(GeminiRateLimitError):
        real_embed_query("test query", client=mock_client)


@pytest.mark.anyio
async def test_general_question_safe_fallback_when_rag_fails(monkeypatch):
    """Verify that when RAG fails (e.g. Gemini embedding error), chatbot handles it safely."""
    from app.services import chatbot

    def failing_retrieve(*args, **kwargs):
        raise rag.RAGError("Simulated Gemini API failure")

    monkeypatch.setattr(rag, "retrieve", failing_retrieve)
    context = await chatbot._build_rag_context("What are your services?")
    # Returns empty context so chatbot can still proceed safely
    assert context == ""

