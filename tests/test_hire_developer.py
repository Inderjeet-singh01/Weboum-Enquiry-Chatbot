from unittest.mock import AsyncMock, patch
import pytest

from app.schemas.chat import ConversationMode, ResponseType
from app.services import chatbot
from app.services.email import EmailSendError
from app.services.hire_developer import (
    SKILLS_OPTIONS,
    TECHNOLOGY_OPTIONS,
    WORK_TIME_OPTIONS,
    TIMEFRAME_OPTIONS,
    START_OPTIONS,
)


def start_session(client, session_id: str = "hd-test"):
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


def test_top_level_options(client):
    data = start_session(client, "top-level-1")
    assert data["suggestions"] == [
        "Website / General Question",
        "Business Solutions Enquiry",
        "Hire a Developer",
    ]
    assert data["mode"] == "initial"


def test_hire_developer_start_flow(client):
    start_session(client, "hd-start")
    resp = send(client, "hd-start", "Hire a Developer")
    data = resp.json()
    assert resp.status_code == 200
    assert data["mode"] == "hire_developer"
    assert data["step"] == "skills"
    assert data["type"] == "multi_options"
    assert data["completed"] is False
    for skill in SKILLS_OPTIONS:
        assert skill in data["suggestions"]
    assert "Continue" in data["suggestions"]


def test_skills_single_selection_and_continue(client):
    start_session(client, "hd-s1")
    send(client, "hd-s1", "Hire a Developer")
    
    # Select Python
    r1 = send(client, "hd-s1", "Python").json()
    assert r1["step"] == "skills"
    assert "Python" in r1["selected"]
    
    # Click Continue
    r2 = send(client, "hd-s1", "Continue").json()
    assert r2["step"] == "technology"
    assert r2["type"] == "multi_options"
    for tech in TECHNOLOGY_OPTIONS:
        assert tech in r2["suggestions"]


def test_skills_multiple_selection_and_continue(client):
    start_session(client, "hd-s2")
    send(client, "hd-s2", "Hire a Developer")
    
    # Select multiple skills
    send(client, "hd-s2", "Python")
    send(client, "hd-s2", "React")
    r = send(client, "hd-s2", "Node").json()
    assert set(r["selected"]) == {"Python", "React", "Node"}
    
    # Continue
    r2 = send(client, "hd-s2", "Continue").json()
    assert r2["step"] == "technology"


def test_skills_continue_without_selection_fails(client):
    start_session(client, "hd-s3")
    send(client, "hd-s3", "Hire a Developer")
    
    # Click continue with no selection
    r = send(client, "hd-s3", "Continue").json()
    assert r["step"] == "skills"
    assert "Please select at least one option." in r["message"]


def test_technology_multiple_selection_and_continue(client):
    start_session(client, "hd-t1")
    send(client, "hd-t1", "Hire a Developer")
    send(client, "hd-t1", "Python")
    send(client, "hd-t1", "Continue")
    
    # Select AI & Automation and Web Application
    send(client, "hd-t1", "AI & Automation")
    r = send(client, "hd-t1", "Web Application").json()
    assert "AI & Automation" in r["selected"]
    assert "Web Application" in r["selected"]
    
    # Continue
    r2 = send(client, "hd-t1", "Continue").json()
    assert r2["step"] == "work_time"
    assert r2["type"] == "multi_options"
    for wt in WORK_TIME_OPTIONS:
        assert wt in r2["suggestions"]


def test_technology_continue_without_selection_fails(client):
    start_session(client, "hd-t2")
    send(client, "hd-t2", "Hire a Developer")
    send(client, "hd-t2", "Python")
    send(client, "hd-t2", "Continue")
    
    r = send(client, "hd-t2", "Continue").json()
    assert r["step"] == "technology"
    assert "Please select at least one option." in r["message"]


def test_work_time_selection_and_continue(client):
    start_session(client, "hd-wt")
    send(client, "hd-wt", "Hire a Developer")
    send(client, "hd-wt", "Python")
    send(client, "hd-wt", "Continue")
    send(client, "hd-wt", "AI & Automation")
    send(client, "hd-wt", "Continue")
    
    # Test empty continue failure
    err = send(client, "hd-wt", "Continue").json()
    assert err["step"] == "work_time"
    assert "Please select at least one option." in err["message"]
    
    # Select Full-Time
    r = send(client, "hd-wt", "Full-Time").json()
    assert r["step"] == "work_time"
    assert r["selected"] == ["Full-Time"]
    
    # Continue
    r2 = send(client, "hd-wt", "Continue").json()
    assert r2["step"] == "timeframe"
    for tf in TIMEFRAME_OPTIONS:
        assert tf in r2["suggestions"]


def test_work_time_part_time_only(client):
    """Part-Time only is a valid single selection for the multi-select work_time step."""
    start_session(client, "hd-wt-pt")
    send(client, "hd-wt-pt", "Hire a Developer")
    send(client, "hd-wt-pt", "Python")
    send(client, "hd-wt-pt", "Continue")
    send(client, "hd-wt-pt", "AI & Automation")
    send(client, "hd-wt-pt", "Continue")

    # Select Part-Time
    r = send(client, "hd-wt-pt", "Part-Time").json()
    assert r["step"] == "work_time"
    assert r["selected"] == ["Part-Time"]

    # Continue advances to timeframe
    r2 = send(client, "hd-wt-pt", "Continue").json()
    assert r2["step"] == "timeframe"


def test_work_time_both_options(client):
    """Both Full-Time and Part-Time can be selected together."""
    start_session(client, "hd-wt-both")
    send(client, "hd-wt-both", "Hire a Developer")
    send(client, "hd-wt-both", "Python")
    send(client, "hd-wt-both", "Continue")
    send(client, "hd-wt-both", "AI & Automation")
    send(client, "hd-wt-both", "Continue")

    # Select Full-Time then Part-Time
    send(client, "hd-wt-both", "Full-Time")
    r = send(client, "hd-wt-both", "Part-Time").json()
    assert r["step"] == "work_time"
    assert "Full-Time" in r["selected"]
    assert "Part-Time" in r["selected"]
    assert len(r["selected"]) == 2

    # Continue advances to timeframe
    r2 = send(client, "hd-wt-both", "Continue").json()
    assert r2["step"] == "timeframe"


def test_timeframe_selection_and_continue(client):
    start_session(client, "hd-tf")
    send(client, "hd-tf", "Hire a Developer")
    send(client, "hd-tf", "Python")
    send(client, "hd-tf", "Continue")
    send(client, "hd-tf", "AI & Automation")
    send(client, "hd-tf", "Continue")
    send(client, "hd-tf", "Full-Time")
    send(client, "hd-tf", "Continue")
    
    # Empty continue failure
    err = send(client, "hd-tf", "Continue").json()
    assert err["step"] == "timeframe"
    assert "Please select an option." in err["message"]
    send(client, "hd-tf", "1 to 3 months")
    r2 = send(client, "hd-tf", "Continue").json()
    assert r2["step"] == "start"
    for st in START_OPTIONS:
        assert st in r2["suggestions"]


def test_start_selection_and_continue(client):
    start_session(client, "hd-st")
    send(client, "hd-st", "Hire a Developer")
    send(client, "hd-st", "Python")
    send(client, "hd-st", "Continue")
    send(client, "hd-st", "AI & Automation")
    send(client, "hd-st", "Continue")
    send(client, "hd-st", "Full-Time")
    send(client, "hd-st", "Continue")
    send(client, "hd-st", "1 to 3 months")
    send(client, "hd-st", "Continue")
    
    # Empty continue failure
    err = send(client, "hd-st", "Continue").json()
    assert err["step"] == "start"
    assert "Please select an option." in err["message"]
    send(client, "hd-st", "In a couple of weeks")
    r2 = send(client, "hd-st", "Continue").json()
    assert r2["step"] == "contact_information"
    assert r2["type"] == "contact_form"


def test_back_navigation_preserves_data(client):
    start_session(client, "hd-back")
    send(client, "hd-back", "Hire a Developer")
    send(client, "hd-back", "Python, React")
    send(client, "hd-back", "Continue")
    send(client, "hd-back", "Web Application")
    send(client, "hd-back", "Continue")
    send(client, "hd-back", "Full-Time")
    send(client, "hd-back", "Continue")
    
    # Currently on timeframe. Navigate back to work_time
    b1 = send(client, "hd-back", "Back").json()
    assert b1["step"] == "work_time"
    assert b1["selected"] == ["Full-Time"]
    
    # Navigate back to technology
    b2 = send(client, "hd-back", "Back").json()
    assert b2["step"] == "technology"
    assert b2["selected"] == ["Web Application"]
    
    # Navigate back to skills
    b3 = send(client, "hd-back", "Back").json()
    assert b3["step"] == "skills"
    assert set(b3["selected"]) == {"Python", "React"}
    
    # Advance forward again
    f1 = send(client, "hd-back", "Continue").json()
    assert f1["step"] == "technology"
    f2 = send(client, "hd-back", "Continue").json()
    assert f2["step"] == "work_time"


def test_contact_validation_and_successful_submit(client):
    start_session(client, "hd-sub")
    send(client, "hd-sub", "Hire a Developer")
    send(client, "hd-sub", "Python, React")
    send(client, "hd-sub", "Continue")
    send(client, "hd-sub", "AI & Automation, Web Application")
    send(client, "hd-sub", "Continue")
    send(client, "hd-sub", "Full-Time")
    send(client, "hd-sub", "Continue")
    send(client, "hd-sub", "1 to 3 months")
    send(client, "hd-sub", "Continue")
    send(client, "hd-sub", "In a couple of weeks")
    send(client, "hd-sub", "Continue")
    
    # Currently on contact_information
    # 1. Empty submit
    r = send(client, "hd-sub", "Submit").json()
    assert r["step"] == "contact_information"
    assert "Please enter a valid full name." in r["message"]
    
    # 2. Missing comment
    missing_comment_data = {
        "name": "John Doe",
        "email": "not-an-email",
        "phone": "123",
        "website_url": "not-a-url",
        "comment": "",
    }
    r = send(client, "hd-sub", "", data=missing_comment_data).json()
    assert r["step"] == "contact_information"
    assert "Comment is required." in r["message"]

    # 3. Submit with arbitrary email, phone, and website URL (validations removed)
    valid_data = {
        "name": "John Doe",
        "email": "not-an-email",
        "phone": "123",
        "website_url": "not-a-url",
        "comment": "We need senior Python and React developers for AI platform.",
    }
    with patch("app.services.hire_developer.send_hire_developer_email", new_callable=AsyncMock) as mock_send:
        res = send(client, "hd-sub", "", data=valid_data)
        assert res.status_code == 200
        data = res.json()
        assert data["completed"] is True
        assert data["step"] is None
        assert data["mode"] == "hire_developer"
        assert "submitted successfully" in data["message"]
        assert mock_send.call_count == 1
        
        # Verify payload passed to Brevo email
        payload_sent = mock_send.call_args[0][0]
        assert payload_sent["skills"] == ["Python", "React"]
        assert payload_sent["technology"] == ["AI & Automation", "Web Application"]
        assert payload_sent["work_time"] == ["Full-Time"]
        assert payload_sent["timeframe"] == "1 to 3 months"
        assert payload_sent["start"] == "In a couple of weeks"
        assert payload_sent["name"] == "John Doe"
        assert payload_sent["email"] == "not-an-email"
        assert payload_sent["phone"] == "123"
        assert payload_sent["website_url"] == "not-a-url"
        assert payload_sent["comment"] == "We need senior Python and React developers for AI platform."
        
        # 7. Duplicate submit protection
        res2 = send(client, "hd-sub", "Submit")
        data2 = res2.json()
        assert data2["completed"] is True
        # Email should NOT be called again
        assert mock_send.call_count == 1


@pytest.mark.parametrize("step_target,query", [
    ("skills", "Who is the CEO of Weboum?"),
    ("skills", "CEO of company"),
    ("technology", "Tell me about the company"),
    ("work_time", "What services do you provide?"),
    ("timeframe", "Who runs Weboum?"),
    ("start", "company leadership"),
    ("contact_information", "What are your pricing options?"),
])
def test_general_question_interrupts_hire_developer_at_all_steps(client, mock_llm, step_target, query):
    sid = f"hd-int-{step_target}"
    start_session(client, sid)
    send(client, sid, "Hire a Developer")
    
    if step_target != "skills":
        send(client, sid, "Python")
        send(client, sid, "Continue")
    if step_target in ("work_time", "timeframe", "start", "contact_information"):
        send(client, sid, "AI & Automation")
        send(client, sid, "Continue")
    if step_target in ("timeframe", "start", "contact_information"):
        send(client, sid, "Full-Time")
        send(client, sid, "Continue")
    if step_target in ("start", "contact_information"):
        send(client, sid, "1 to 3 months")
        send(client, sid, "Continue")
    if step_target == "contact_information":
        send(client, sid, "In a couple of weeks")
        send(client, sid, "Continue")
        
    # User interrupts with general question
    resp = send(client, sid, query).json()
    assert resp["mode"] == "general"
    assert resp["step"] is None
    assert resp["suggestions"] == [
        "Website / General Question",
        "Business Solutions Enquiry",
        "Hire a Developer",
    ]
    
    # Selecting Hire a Developer again must start fresh at Step 1 (Skills)
    fresh = send(client, sid, "Hire a Developer").json()
    assert fresh["mode"] == "hire_developer"
    assert fresh["step"] == "skills"
    assert fresh["selected"] == []


def test_invalid_inputs_remain_on_step_and_do_not_trigger_rag(client, mock_llm):
    sid = "hd-invalid-input"
    start_session(client, sid)
    send(client, sid, "Hire a Developer")
    
    # Send random non-question invalid text on skills
    r1 = send(client, sid, "xyz123").json()
    assert r1["mode"] == "hire_developer"
    assert r1["step"] == "skills"
    assert "Please select at least one option." in r1["message"]
    
    # Valid selection and move to work_time
    send(client, sid, "Python")
    send(client, sid, "Continue")
    send(client, sid, "AI & Automation")
    send(client, sid, "Continue")
    
    # Send invalid option on work_time
    r2 = send(client, sid, "random input").json()
    assert r2["mode"] == "hire_developer"
    assert r2["step"] == "work_time"
    assert "Please select at least one option." in r2["message"]


@pytest.mark.anyio
async def test_stream_hire_developer_interruption_resets_state_and_restarts_at_step_1(mock_stream_llm):
    chatbot.reset_runtime_state()
    session_id = "test-stream-hd-interrupt"
    await chatbot.handle_chat(session_id, "")
    
    # Start Hire a Developer
    r1 = await chatbot.handle_chat(session_id, "Hire a Developer")
    assert r1.mode.value == "hire_developer"
    assert r1.step == "skills"
    
    # User selects skills
    r2 = await chatbot.handle_chat(session_id, "Python, React")
    assert r2.step == "skills"
    assert "Python" in chatbot._sessions[session_id].hire_dev_data["skills"]
    
    # User streams a general question during Hire a Developer
    events = []
    async for event in chatbot.stream_chat(session_id, "Who is the CEO of Weboum?"):
        events.append(event)
        
    types = [e["type"] for e in events]
    assert "chunk" in types
    assert "done" in types
    
    done_event = [e for e in events if e["type"] == "done"][0]
    assert done_event["mode"] == "general"
    assert done_event["suggestions"] == [
        "Website / General Question",
        "Business Solutions Enquiry",
        "Hire a Developer",
    ]
    
    # Verify hire_developer state was abandoned / reset
    assert chatbot._sessions[session_id].hire_dev_data["skills"] == []
    
    # Selecting Hire a Developer again starts fresh from Step 1
    fresh = await chatbot.handle_chat(session_id, "Hire a Developer")
    assert fresh.mode.value == "hire_developer"
    assert fresh.step == "skills"


def test_structured_actions_and_no_loop_transitions(client):
    """Verify that structured actions advance through every step without looping."""
    sid = "hd-struct-trans"
    start_session(client, sid)
    r_init = send(client, sid, "Hire a Developer").json()
    assert r_init["step"] == "skills"

    # Step 1: Skills Continue with structured data
    r_tech = send(
        client, sid, "Python, React",
        action="continue", step="skills",
        data={"action": "continue", "step": "skills", "skills": ["Python", "React"]}
    ).json()
    assert r_tech["step"] == "technology"
    assert r_tech["step"] != "skills"

    # Step 2: Technology Continue with structured data
    r_wt = send(
        client, sid, "AI & Automation, Web Application",
        action="continue", step="technology",
        data={"action": "continue", "step": "technology", "technology": ["AI & Automation", "Web Application"]}
    ).json()
    assert r_wt["step"] == "work_time"
    assert r_wt["step"] != "technology"

    # Step 3: Work Time Continue with structured data
    r_tf = send(
        client, sid, "Full-Time, Part-Time",
        action="continue", step="work_time",
        data={"action": "continue", "step": "work_time", "work_time": ["Full-Time", "Part-Time"]}
    ).json()
    assert r_tf["step"] == "timeframe"
    assert r_tf["step"] != "work_time"

    # Step 4: Timeframe Continue with structured data -> MUST NOT LOOP!
    r_st = send(
        client, sid, "1 to 3 months",
        action="continue", step="timeframe",
        data={"action": "continue", "step": "timeframe", "timeframe": "1 to 3 months"}
    ).json()
    assert r_st["step"] == "start"
    assert r_st["step"] != "timeframe"

    # Step 5: Start Continue with structured data -> MUST NOT LOOP!
    r_ci = send(
        client, sid, "In a couple of weeks",
        action="continue", step="start",
        data={"action": "continue", "step": "start", "start": "In a couple of weeks"}
    ).json()
    assert r_ci["step"] == "contact_information"
    assert r_ci["step"] != "start"

    # Step 6: Contact Submit with structured data
    contact_payload = {
        "name": "Jane Developer",
        "email": "jane@company.com",
        "phone": "+1 555 123 4567",
        "website_url": "https://example.com",
        "comment": "Looking for senior full stack developers immediately."
    }
    with patch("app.services.hire_developer.send_hire_developer_email", new_callable=AsyncMock) as mock_email:
        r_done = send(
            client, sid, "Submit Request",
            action="submit", step="contact_information",
            data={"action": "submit", "step": "contact_information", **contact_payload}
        ).json()
        assert r_done["completed"] is True
        assert r_done["step"] is None
        assert mock_email.call_count == 1
        
        sent_data = mock_email.call_args[0][0]
        assert sent_data["skills"] == ["Python", "React"]
        assert sent_data["technology"] == ["AI & Automation", "Web Application"]
        assert sent_data["work_time"] == ["Full-Time", "Part-Time"]
        assert sent_data["timeframe"] == "1 to 3 months"
        assert sent_data["start"] == "In a couple of weeks"
        assert sent_data["name"] == "Jane Developer"


def test_timeframe_single_select_does_not_loop(client):
    """Direct verification that Timeframe does not loop on Continue."""
    sid = "hd-tf-no-loop"
    start_session(client, sid)
    send(client, sid, "Hire a Developer")
    send(client, sid, "Continue", data={"skills": ["Python"]})
    send(client, sid, "Continue", data={"technology": ["AI & Automation"]})
    send(client, sid, "Continue", data={"work_time": ["Full-Time"]})

    # User submits timeframe with Continue structured action
    resp = send(
        client, sid, "1 to 3 months",
        action="continue", step="timeframe",
        data={"action": "continue", "step": "timeframe", "data": {"timeframe": "1 to 3 months"}}
    ).json()

    assert resp["step"] == "start"
    assert resp["step"] != "timeframe"


def test_start_single_select_does_not_loop(client):
    """Direct verification that Start step does not loop on Continue."""
    sid = "hd-st-no-loop"
    start_session(client, sid)
    send(client, sid, "Hire a Developer")
    send(client, sid, "Continue", data={"skills": ["Python"]})
    send(client, sid, "Continue", data={"technology": ["AI & Automation"]})
    send(client, sid, "Continue", data={"work_time": ["Full-Time"]})
    send(client, sid, "Continue", data={"timeframe": "1 to 3 months"})

    # User submits start with Continue structured action
    resp = send(
        client, sid, "In a couple of weeks",
        action="continue", step="start",
        data={"action": "continue", "step": "start", "data": {"start": "In a couple of weeks"}}
    ).json()

    assert resp["step"] == "contact_information"
    assert resp["step"] != "start"


def test_work_time_internal_list_representation(client):
    """Verify internal representation of work_time is strictly a list for single and multiple selections."""
    sid = "hd-wt-repr"
    start_session(client, sid)
    send(client, sid, "Hire a Developer")
    send(client, sid, "Continue", data={"skills": ["Python"]})
    send(client, sid, "Continue", data={"technology": ["AI & Automation"]})

    # Select Full-Time only
    send(client, sid, "Continue", data={"work_time": ["Full-Time"]})
    session = chatbot._sessions[sid]
    assert session.hire_dev_data["work_time"] == ["Full-Time"]
    assert isinstance(session.hire_dev_data["work_time"], list)

    # Go back and select both
    send(client, sid, "Back", action="back")
    send(client, sid, "Continue", data={"work_time": ["Full-Time", "Part-Time"]})
    session = chatbot._sessions[sid]
    assert session.hire_dev_data["work_time"] == ["Full-Time", "Part-Time"]
    assert isinstance(session.hire_dev_data["work_time"], list)


def test_structured_back_preserves_data_no_form_reset(client):
    """Structured action back must preserve collected data and not reset or submit."""
    sid = "hd-back-preserve"
    start_session(client, sid)
    send(client, sid, "Hire a Developer")
    send(client, sid, "Continue", data={"skills": ["Python", "React"]})
    send(client, sid, "Continue", data={"technology": ["AI & Automation"]})
    send(client, sid, "Continue", data={"work_time": ["Full-Time"]})

    # Currently on timeframe. Go back to work_time
    b1 = send(client, sid, "Back", action="back", step="timeframe").json()
    assert b1["step"] == "work_time"
    session = chatbot._sessions[sid]
    assert session.hire_dev_data["skills"] == ["Python", "React"]
    assert session.hire_dev_data["technology"] == ["AI & Automation"]
    assert session.hire_dev_data["work_time"] == ["Full-Time"]

    # Go back to technology
    b2 = send(client, sid, "Back", action="back", step="work_time").json()
    assert b2["step"] == "technology"
    assert session.hire_dev_data["skills"] == ["Python", "React"]
    assert session.hire_dev_data["technology"] == ["AI & Automation"]

    # Go back to skills
    b3 = send(client, sid, "Back", action="back", step="technology").json()
    assert b3["step"] == "skills"
    assert session.hire_dev_data["skills"] == ["Python", "React"]


def test_brevo_failure_does_not_mark_completed(client):
    """When Brevo email send fails, completed must remain False and email_sent False."""
    sid = "hd-brevo-fail"
    start_session(client, sid)
    send(client, sid, "Hire a Developer")
    send(client, sid, "Continue", data={"skills": ["Python"]})
    send(client, sid, "Continue", data={"technology": ["AI & Automation"]})
    send(client, sid, "Continue", data={"work_time": ["Full-Time"]})
    send(client, sid, "Continue", data={"timeframe": "1 to 3 months"})
    send(client, sid, "Continue", data={"start": "In a couple of weeks"})

    valid_contact = {
        "name": "Jane Doe",
        "email": "jane@company.com",
        "phone": "+1 555 123 4567",
        "comment": "Developer requirements comment."
    }

    with patch("app.services.hire_developer.send_hire_developer_email", side_effect=EmailSendError("Brevo error")):
        res = send(
            client, sid, "Submit Request",
            action="submit", step="contact_information",
            data=valid_contact
        ).json()
        assert res["completed"] is False
        assert res["step"] == "contact_information"

        session = chatbot._sessions[sid]
        assert session.completed is False
        assert session.email_sent is False
        assert session.current_step == "contact_information"


@pytest.mark.parametrize("query", [
    "Who is the CEO of Weboum?",
    "CEO of company",
    "Who runs Weboum?",
    "Who is the founder?",
    "Company leadership",
    "Tell me about Weboum",
    "What services do you provide?",
    "What does Weboum do?",
    "Tell me about your team",
])
def test_general_question_queries_abandon_and_reset_hire_dev(client, mock_llm, query):
    """All user-specified general question examples must abandon hire dev and show 3 top-level options."""
    sid = f"hd-gen-{abs(hash(query))}"
    start_session(client, sid)
    send(client, sid, "Hire a Developer")
    send(client, sid, "Continue", data={"skills": ["Python", "React"]})

    resp = send(client, sid, query).json()
    assert resp["mode"] == "general"
    assert resp["step"] is None
    assert resp["suggestions"] == [
        "Website / General Question",
        "Business Solutions Enquiry",
        "Hire a Developer",
    ]

    session = chatbot._sessions[sid]
    assert session.hire_dev_data["skills"] == []
    assert session.current_step is None

    # Clicking Hire a Developer starts fresh at Step 1 (skills)
    fresh = send(client, sid, "Hire a Developer").json()
    assert fresh["mode"] == "hire_developer"
    assert fresh["step"] == "skills"
    assert fresh["selected"] == []


def test_switching_flow_isolation(client, mock_llm):
    """Test switching: General -> Hire Dev -> General Question -> Hire Dev -> General Question -> Business Enquiry -> General Question -> Hire Dev."""
    sid = "hd-switch-flow"
    start_session(client, sid)

    # 1. Start Hire a Developer
    r1 = send(client, sid, "Hire a Developer").json()
    assert r1["mode"] == "hire_developer"
    assert r1["step"] == "skills"
    send(client, sid, "Continue", data={"skills": ["Python"]})

    # 2. General Question interruption
    r2 = send(client, sid, "Who is the CEO of Weboum?").json()
    assert r2["mode"] == "general"

    # 3. Start Hire a Developer again (fresh)
    r3 = send(client, sid, "Hire a Developer").json()
    assert r3["mode"] == "hire_developer"
    assert r3["step"] == "skills"
    assert r3["selected"] == []

    # 4. General Question interruption again
    r4 = send(client, sid, "Tell me about Weboum").json()
    assert r4["mode"] == "general"

    # 5. Start Business Solutions Enquiry
    r5 = send(client, sid, "Business Solutions Enquiry").json()
    assert r5["mode"] == "enquiry"
    assert r5["step"] == "biggest_operational_challenge"

    # 6. General Question interruption during Business Solutions Enquiry
    r6 = send(client, sid, "What does Weboum do?").json()
    assert r6["mode"] == "general"
    assert r6["suggestions"] == [
        "Website / General Question",
        "Business Solutions Enquiry",
        "Hire a Developer",
    ]

    # 7. Start Hire a Developer again (fresh)
    r7 = send(client, sid, "Hire a Developer").json()
    assert r7["mode"] == "hire_developer"
    assert r7["step"] == "skills"
    assert r7["selected"] == []

    # Verify data isolation: Business Enquiry data and Hire Dev data do not mix
    session = chatbot._sessions[sid]
    assert all(v is None for v in session.data.values())
    assert session.hire_dev_data["skills"] == []


def test_cache_busting_static_assets(client):
    """Verify index.html serves updated asset URLs with cache-busting v=hire_dev_v3."""
    res = client.get("/")
    assert res.status_code == 200
    html = res.text
    assert "/static/style.css?v=hire_dev_v3" in html
    assert "/static/app.js?v=hire_dev_v3" in html


def test_hire_developer_no_validation_on_email_phone_url(client):
    """Verify phone, email, and website_url accept arbitrary formats or empty values."""
    from app.schemas.chat import HireDeveloperData

    # Pydantic schema accepts arbitrary phone, email, url
    model = HireDeveloperData(
        name="John Doe",
        email="arbitrary-string-no-at-sign",
        phone="abc-12",
        website_url="not-a-valid-url",
        comment="Test requirements"
    )
    assert model.email == "arbitrary-string-no-at-sign"
    assert model.phone == "abc-12"
    assert model.website_url == "not-a-valid-url"

    sid = "hd-no-val"
    start_session(client, sid)
    send(client, sid, "Hire a Developer")
    send(client, sid, "Python")
    send(client, sid, "Continue")
    send(client, sid, "AI & Automation")
    send(client, sid, "Continue")
    send(client, sid, "Full-Time")
    send(client, sid, "Continue")
    send(client, sid, "1 to 3 months")
    send(client, sid, "Continue")
    send(client, sid, "In a couple of days")
    send(client, sid, "Continue")

    # Submit with empty email, phone, and website_url
    empty_contact_data = {
        "name": "Jane Smith",
        "email": "",
        "phone": "",
        "website_url": "",
        "comment": "Looking for frontend and backend help.",
    }
    with patch("app.services.hire_developer.send_hire_developer_email", new_callable=AsyncMock) as mock_send:
        res = send(client, sid, "", data=empty_contact_data)
        assert res.status_code == 200
        data = res.json()
        assert data["completed"] is True
        assert data["mode"] == "hire_developer"
        assert mock_send.call_count == 1
        payload = mock_send.call_args[0][0]
        assert payload["name"] == "Jane Smith"
        assert payload["email"] == ""
        assert payload["phone"] == ""
        assert payload["website_url"] is None
        assert payload["comment"] == "Looking for frontend and backend help."

