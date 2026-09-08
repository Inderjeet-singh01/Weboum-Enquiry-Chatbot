import pytest
from app.core.config import Settings
from app.services import chatbot


def test_health_endpoint_returns_ok(client):
    """Verify /health returns HTTP 200 with status ok."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_endpoint_is_lightweight(client):
    """Verify /health does not alter runtime session state."""
    chatbot.reset_runtime_state()
    initial_sessions = len(chatbot._sessions)
    assert initial_sessions == 0

    response = client.get("/health")
    assert response.status_code == 200
    assert len(chatbot._sessions) == 0


def test_cors_origins_parsing():
    """Verify comma-separated CORS origins parsing and trimming."""
    # Development default
    s1 = Settings(CORS_ORIGINS="http://localhost:3000")
    assert s1.cors_origins_list == ["http://localhost:3000"]

    # Production multi-origin with whitespace, quotes, and trailing slashes
    s2 = Settings(CORS_ORIGINS="https://frontend.com/, 'https://admin.frontend.com', \"https://api.frontend.com/\"")
    assert s2.cors_origins_list == [
        "https://frontend.com",
        "https://admin.frontend.com",
        "https://api.frontend.com",
    ]

    # Empty string (same origin deployment)
    s3 = Settings(CORS_ORIGINS="")
    assert s3.cors_origins_list == []


def test_cors_preflight_request(client):
    """Verify preflight OPTIONS request headers against configured origin."""
    response = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert response.headers.get("access-control-allow-credentials") == "true"
