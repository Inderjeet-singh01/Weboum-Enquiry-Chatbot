import pytest
from app.services import rag


def test_rag_retrieves_chunks():
    rag.reset_rag_state()
    chunks = rag.retrieve("What services do you provide?")
    assert len(chunks) > 0
    assert "text" in chunks[0]
    assert "title" in chunks[0]
    assert chunks[0]["score"] > 0


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
    monkeypatch.setattr(rag.settings, "KNOWLEDGE_PATH", str(tmp_path / "nonexistent.json"))
    with pytest.raises(rag.RAGIndexError):
        rag.retrieve("anything")
    rag.reset_rag_state()
