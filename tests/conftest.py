import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import chatbot


@pytest.fixture(autouse=True)
def clear_runtime_state():
    chatbot.reset_runtime_state()
    yield
    chatbot.reset_runtime_state()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def mock_llm(monkeypatch):
    async def fake_answer(question: str, context: str = "", *args, **kwargs) -> str:
        return (
            "We provide technology solutions including AI/ML development, "
            "automation, custom software and other digital solutions."
        )

    monkeypatch.setattr(chatbot, "generate_general_answer", fake_answer)
    return fake_answer


@pytest.fixture(autouse=True)
def mock_email(monkeypatch):
    calls: list[dict] = []

    async def fake_send(mapped: dict) -> None:
        calls.append(mapped)

    monkeypatch.setattr("app.services.chatbot.send_enquiry_email", fake_send)
    return calls

