from unittest.mock import AsyncMock, patch
import pytest

from app.schemas.chat import ConversationMode, ResponseType
from app.services import chatbot
from app.services.hire_developer import COMPLETION_MESSAGE


def start_session(client, session_id: str = "hd-ps-test"):
    chatbot.reset_runtime_state()
    response = client.post("/api/chat", json={"session_id": session_id, "message": ""})
    assert response.status_code == 200
    return response.json()


def send(client, session_id: str, message: str, data: dict | None = None, action: str | None = None, step: str | None = None):
    payload = {"session_id": session_id, "message": message}
    if data is not None:
        payload["data"] = data
    if action is not None:
        payload["action"] = action
    if step is not None:
        payload["step"] = step
    return client.post("/api/chat", json=payload)


def submit_hire_developer_flow(client, session_id: str = "hd-post-sub"):
    """Helper to complete full Hire a Developer flow up to submission."""
    start_session(client, session_id)
    send(client, session_id, "Hire a Developer")
    send(
        client, session_id, "Python",
        action="continue", step="skills",
        data={"action": "continue", "step": "skills", "skills": ["Python"]}
    )
    send(
        client, session_id, "AI & Automation",
        action="continue", step="technology",
        data={"action": "continue", "step": "technology", "technology": ["AI & Automation"]}
    )
    send(
        client, session_id, "Full-Time",
        action="continue", step="work_time",
        data={"action": "continue", "step": "work_time", "work_time": ["Full-Time"]}
    )
    send(
        client, session_id, "1 to 3 months",
        action="continue", step="timeframe",
        data={"action": "continue", "step": "timeframe", "timeframe": "1 to 3 months"}
    )
    send(
        client, session_id, "In a couple of weeks",
        action="continue", step="start",
        data={"action": "continue", "step": "start", "start": "In a couple of weeks"}
    )

    contact_payload = {
        "name": "Jane Developer",
        "email": "jane@example.com",
        "phone": "+1 555 123 4567",
        "website_url": "https://example.com",
        "comment": "Need full-stack engineers."
    }

    with patch("app.services.hire_developer.send_hire_developer_email", new_callable=AsyncMock) as mock_send:
        res = send(
            client, session_id, "Submit Request",
            action="submit", step="contact_information",
            data={"action": "submit", "step": "contact_information", **contact_payload}
        )
        assert res.status_code == 200
        assert mock_send.call_count == 1
        return res.json()


def test_1_successful_hire_developer_submission(client):
    """1. Successful Hire Developer submission:
    - success message returned
    - 'Anything else?' returned
    - 3 top-level options NOT returned yet
    """
    data = submit_hire_developer_flow(client, "hd-test-1")

    # Success message returned
    assert data["message"] == (
        "Thank you! Your Hire a Developer request has been submitted successfully. "
        "Our team will review your requirements and get back to you shortly.\n\n"
        "Anything else?"
    )
    # Exactly one selectable suggestion: 'Anything else?'
    assert data["suggestions"] == ["Anything else?"]
    # 3 top-level options NOT returned yet
    assert "Website / General Question" not in data["suggestions"]
    assert "Business Solutions Enquiry" not in data["suggestions"]
    assert "Hire a Developer" not in data["suggestions"]
    assert data["completed"] is True
    assert data["step"] is None
    assert data["mode"] == "hire_developer"


def test_2_click_anything_else_via_action(client):
    """2. Click 'Anything else?' via explicit action:
    - General mode activated
    - prompt: 'Sure! What would you like to know about us?'
    - no Hire Developer success message repeated
    - suggestions empty
    """
    submit_hire_developer_flow(client, "hd-test-2")

    resp = send(client, "hd-test-2", "Anything else?", action="anything_else", data={"action": "anything_else"})
    assert resp.status_code == 200
    data = resp.json()

    assert data["mode"] == "general"
    assert data["message"] == "Sure! What would you like to know about us?"
    assert data["suggestions"] == []
    assert data["completed"] is False
    assert data["step"] is None
    assert "submitted successfully" not in data["message"]


def test_2b_click_anything_else_via_text(client):
    """2b. Click 'Anything else?' via simple text message:
    - General mode activated
    - prompt: 'Sure! What would you like to know about us?'
    - no Hire Developer success message repeated
    """
    submit_hire_developer_flow(client, "hd-test-2b")

    resp = send(client, "hd-test-2b", "Anything else?")
    assert resp.status_code == 200
    data = resp.json()

    assert data["mode"] == "general"
    assert data["message"] == "Sure! What would you like to know about us?"
    assert data["suggestions"] == []
    assert data["completed"] is False
    assert data["step"] is None


def test_3_and_4_general_question_flow_and_options(client, mock_llm):
    """3 & 4. User asks general question like 'all the services?':
    - processed by General/RAG
    - answer returned
    - after the answer: exactly 3 top-level options returned in correct order:
      1. Website / General Question
      2. Business Solutions Enquiry
      3. Hire a Developer
    - 'Anything else?' not present in options
    """
    submit_hire_developer_flow(client, "hd-test-3-4")

    # Click 'Anything else?'
    send(client, "hd-test-3-4", "Anything else?", action="anything_else")

    # User types 'all the services?'
    resp = send(client, "hd-test-3-4", "all the services?")
    assert resp.status_code == 200
    data = resp.json()

    assert data["mode"] == "general"
    assert data["type"] == "text"
    assert "technology solutions" in data["message"]

    # Exactly 3 top-level options in order
    assert data["suggestions"] == [
        "Website / General Question",
        "Business Solutions Enquiry",
        "Hire a Developer",
    ]
    assert len(data["suggestions"]) == 3
    assert "Anything else?" not in data["suggestions"]


def test_5_fresh_hire_developer_flow_afterwards(client, mock_llm):
    """5. Clicking Hire a Developer afterward:
    - starts fresh at Skills
    - previously submitted data is not reused as active form data
    """
    sid = "hd-test-5"
    submit_hire_developer_flow(client, sid)
    send(client, sid, "Anything else?", action="anything_else")
    send(client, sid, "what services do you provide?")

    # Click 'Hire a Developer'
    resp = send(client, sid, "Hire a Developer")
    data = resp.json()

    assert data["mode"] == "hire_developer"
    assert data["step"] == "skills"
    assert data["completed"] is False
    assert "Python" in data["suggestions"]
    assert "Continue" in data["suggestions"]
    # Selection should be fresh/empty
    assert data.get("selected") == [] or data.get("selected") is None

    session = chatbot._sessions.get(sid)
    assert session is not None
    assert session.hire_dev_data["skills"] == []
    assert session.hire_dev_data["name"] is None
    assert session.completed is False


def test_6_business_solutions_enquiry_afterwards(client, mock_llm):
    """6. Clicking Business Solutions Enquiry afterward:
    - starts fresh at its first step
    - does not mix Hire Developer data with Business Enquiry data
    """
    sid = "hd-test-6"
    submit_hire_developer_flow(client, sid)
    send(client, sid, "Anything else?", action="anything_else")
    send(client, sid, "tell me about the company")

    # Click 'Business Solutions Enquiry'
    resp = send(client, sid, "Business Solutions Enquiry")
    data = resp.json()

    assert data["mode"] == "enquiry"
    assert data["step"] == "biggest_operational_challenge"
    assert data["completed"] is False

    session = chatbot._sessions.get(sid)
    assert session is not None
    assert session.data["biggest_operational_challenge"] is None
    assert session.data["work_email"] is None


def test_7_clicking_anything_else_repeatedly_no_loop(client):
    """7. Clicking Anything else repeatedly:
    - no loop
    - no duplicate success message
    - stays in general mode
    """
    sid = "hd-test-7"
    submit_hire_developer_flow(client, sid)

    # 1st click
    r1 = send(client, sid, "Anything else?", action="anything_else").json()
    assert r1["mode"] == "general"
    assert r1["message"] == "Sure! What would you like to know about us?"
    assert r1["suggestions"] == []

    # 2nd click
    r2 = send(client, sid, "Anything else?", action="anything_else").json()
    assert r2["mode"] == "general"
    assert r2["message"] == "Sure! What would you like to know about us?"
    assert r2["suggestions"] == []
    assert "submitted successfully" not in r2["message"]

    # 3rd click (via text only)
    r3 = send(client, sid, "Anything else?").json()
    assert r3["mode"] == "general"
    assert r3["message"] == "Sure! What would you like to know about us?"
    assert r3["suggestions"] == []
    assert "submitted successfully" not in r3["message"]


def test_8_active_form_state_inactive_after_submission_and_switch(client):
    """8. Verify completed Hire Developer is no longer an active form state:
    - Form status: completed after submit
    - Active form: none
    - After 'Anything else?', completed is False and session is in General mode with step=None
    """
    sid = "hd-test-8"
    submit_hire_developer_flow(client, sid)

    session = chatbot._sessions.get(sid)
    assert session.current_step is None
    assert session.completed is True

    # Transition to general
    send(client, sid, "Anything else?", action="anything_else")

    assert session.mode == "general"
    assert session.current_step is None
    assert session.completed is False
    assert session.hire_dev_data["name"] is None


def test_general_question_immediately_after_submission(client, mock_llm):
    """If user types a general question directly after submission without clicking button:
    - seamlessly handled by General/RAG flow without loop or error
    """
    sid = "hd-test-direct-q"
    submit_hire_developer_flow(client, sid)

    resp = send(client, sid, "who is the CEO?")
    assert resp.status_code == 200
    data = resp.json()

    assert data["mode"] == "general"
    assert "technology solutions" in data["message"]
    assert data["suggestions"] == [
        "Website / General Question",
        "Business Solutions Enquiry",
        "Hire a Developer",
    ]
