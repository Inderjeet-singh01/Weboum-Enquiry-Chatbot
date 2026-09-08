import json
import pytest
from app.services import chatbot


@pytest.fixture
def mock_stream_llm(monkeypatch):
    async def fake_stream(question: str, context: str = "", *args, **kwargs):
        chunks = ["Weboum ", "Technology ", "provides ", "AI and ", "cloud solutions."]
        for c in chunks:
            yield c

    monkeypatch.setattr(chatbot, "stream_general_answer", fake_stream)
    return fake_stream


@pytest.mark.anyio
async def test_stream_chat_general_question(mock_stream_llm):
    chatbot.reset_runtime_state()
    session_id = "test-stream-general"
    await chatbot.handle_chat(session_id, "")
    await chatbot.handle_chat(session_id, "Website / General Question")

    events = []
    async for event in chatbot.stream_chat(session_id, "What services do you offer?"):
        events.append(event)

    types = [e["type"] for e in events]
    assert "chunk" in types
    assert "done" in types

    chunks = [e["content"] for e in events if e["type"] == "chunk"]
    assert "".join(chunks) == "Weboum Technology provides AI and cloud solutions."

    done_event = [e for e in events if e["type"] == "done"][0]
    assert done_event["mode"] == "general"
    assert done_event["suggestions"] == ["Anything Else?", "Enquire Now"]

    session = chatbot._sessions[session_id]
    assert len(session.history) == 2
    assert session.history[0]["content"] == "What services do you offer?"
    assert session.history[1]["content"] == "Weboum Technology provides AI and cloud solutions."


def test_api_chat_streams_sse_when_accept_header_set(client, mock_stream_llm):
    chatbot.reset_runtime_state()
    session_id = "api-stream-gen"
    client.post("/api/chat", json={"session_id": session_id, "message": ""})
    client.post("/api/chat", json={"session_id": session_id, "message": "Website / General Question"})

    # Send general question with Accept: text/event-stream
    response = client.post(
        "/api/chat",
        json={"session_id": session_id, "message": "What services do you offer?"},
        headers={"Accept": "text/event-stream"},
    )
    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")

    lines = response.text.strip().split("\n\n")
    events = [json.loads(line.replace("data:", "").strip()) for line in lines if line.startswith("data:")]

    types = [e["type"] for e in events]
    assert "chunk" in types
    assert "done" in types


def test_api_chat_returns_json_for_enquiry_even_with_stream_header(client):
    chatbot.reset_runtime_state()
    session_id = "api-stream-enq"
    client.post("/api/chat", json={"session_id": session_id, "message": ""})

    # Start business enquiry with Accept: text/event-stream
    response = client.post(
        "/api/chat",
        json={"session_id": session_id, "message": "Business Enquiry"},
        headers={"Accept": "text/event-stream"},
    )
    assert response.status_code == 200
    # Must NOT stream business enquiry
    assert "application/json" in response.headers.get("content-type", "")
    data = response.json()
    assert data["mode"] == "enquiry"
    assert data["step"] == "biggest_operational_challenge"


def test_api_chat_returns_json_when_no_streaming_requested(client, mock_llm):
    chatbot.reset_runtime_state()
    session_id = "api-non-stream"
    client.post("/api/chat", json={"session_id": session_id, "message": ""})
    client.post("/api/chat", json={"session_id": session_id, "message": "Website / General Question"})

    # Send general question with default headers
    response = client.post(
        "/api/chat",
        json={"session_id": session_id, "message": "What services do you offer?"},
    )
    assert response.status_code == 200
    assert "application/json" in response.headers.get("content-type", "")
    data = response.json()
    assert data["mode"] == "general"
    assert "AI/ML development" in data["message"]
