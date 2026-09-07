from app.services import chatbot
from app.services.email import EMAIL_FAILURE_MESSAGE, EmailSendError
from app.services.enquiry import ENQUIRY_FIELD_ORDER, STEP_DEFINITIONS, map_enquiry_data

CHALLENGE = "Repetitive manual data entry & paper/PDF document processing"
AI_CAPABILITY = "AI Chatbots & Voice AI Assistants"
INDUSTRY = "Healthcare"
SIZE = "11 - 50 employees"


def start_session(client, session_id: str = "abc123"):
    response = client.post("/api/chat", json={"session_id": session_id, "message": ""})
    assert response.status_code == 200
    return response.json()


def send(client, session_id: str, message: str):
    response = client.post("/api/chat", json={"session_id": session_id, "message": message})
    return response


def run_enquiry_until(client, session_id: str, stop_before: str | None = None) -> dict:
    start_session(client, session_id)
    send(client, session_id, "Business Enquiry")

    answers = {
        "biggest_operational_challenge": CHALLENGE,
        "ai_capability": AI_CAPABILITY,
        "primary_industry": INDUSTRY,
        "business_size": SIZE,
        "full_name": "Ada Lovelace",
        "company_name": "Analytical Engines",
        "work_email": "ada@example.com",
        "phone_number": "+44 7700 900123",
        "current_technology_stack": "WhatsApp, Salesforce, Excel",
    }

    last = None
    for step in ENQUIRY_FIELD_ORDER:
        if stop_before and step == stop_before:
            break
        last = send(client, session_id, answers[step])
        assert last.status_code == 200
    return last.json() if last is not None else {}


def test_new_session_returns_exactly_two_initial_options(client):
    data = start_session(client, "new-1")
    assert data["message"] == "Hi! How can I help you today?"
    assert data["type"] == "options"
    assert data["suggestions"] == ["Business Enquiry", "Website / General Question"]
    assert data["mode"] == "initial"
    assert data["step"] is None
    assert data["completed"] is False


def test_business_enquiry_starts_correctly(client):
    start_session(client, "enq-start")
    response = send(client, "enq-start", "Business Enquiry")
    data = response.json()
    assert response.status_code == 200
    assert data["mode"] == "enquiry"
    assert data["step"] == "biggest_operational_challenge"
    assert data["type"] == "options"
    assert data["completed"] is False
    assert data["message"].startswith("What is your biggest operational challenge right now?")


def test_step_1_returns_exact_challenge_options(client):
    start_session(client, "s1")
    data = send(client, "s1", "Business Enquiry").json()
    assert data["suggestions"] == STEP_DEFINITIONS["biggest_operational_challenge"]["options"]


def test_step_2_returns_exact_ai_capability_options(client):
    start_session(client, "s2")
    send(client, "s2", "Business Enquiry")
    data = send(client, "s2", CHALLENGE).json()
    assert data["step"] == "ai_capability"
    assert data["suggestions"] == STEP_DEFINITIONS["ai_capability"]["options"]
    assert data["message"].startswith("Which AI capabilities are you most interested in exploring?")


def test_step_3_returns_exact_industry_options(client):
    start_session(client, "s3")
    send(client, "s3", "Business Enquiry")
    send(client, "s3", CHALLENGE)
    data = send(client, "s3", AI_CAPABILITY).json()
    assert data["step"] == "primary_industry"
    assert data["suggestions"] == STEP_DEFINITIONS["primary_industry"]["options"]


def test_step_4_returns_exact_business_size_options(client):
    start_session(client, "s4")
    send(client, "s4", "Business Enquiry")
    send(client, "s4", CHALLENGE)
    send(client, "s4", AI_CAPABILITY)
    data = send(client, "s4", INDUSTRY).json()
    assert data["step"] == "business_size"
    assert data["suggestions"] == STEP_DEFINITIONS["business_size"]["options"]


def test_step_5_asks_full_name(client):
    start_session(client, "s5")
    send(client, "s5", "Business Enquiry")
    send(client, "s5", CHALLENGE)
    send(client, "s5", AI_CAPABILITY)
    send(client, "s5", INDUSTRY)
    data = send(client, "s5", SIZE).json()
    assert data["step"] == "full_name"
    assert data["type"] == "text"
    assert data["suggestions"] == []
    assert "Full Name" in data["message"]


def test_step_6_asks_company_name(client):
    start_session(client, "s6")
    send(client, "s6", "Business Enquiry")
    send(client, "s6", CHALLENGE)
    send(client, "s6", AI_CAPABILITY)
    send(client, "s6", INDUSTRY)
    send(client, "s6", SIZE)
    data = send(client, "s6", "Ada Lovelace").json()
    assert data["step"] == "company_name"
    assert data["type"] == "text"
    assert "Company Name" in data["message"]


def test_step_7_asks_work_email(client):
    start_session(client, "s7")
    send(client, "s7", "Business Enquiry")
    send(client, "s7", CHALLENGE)
    send(client, "s7", AI_CAPABILITY)
    send(client, "s7", INDUSTRY)
    send(client, "s7", SIZE)
    send(client, "s7", "Ada Lovelace")
    data = send(client, "s7", "Analytical Engines").json()
    assert data["step"] == "work_email"
    assert data["type"] == "email"
    assert "Work Email" in data["message"]


def test_invalid_email_is_rejected(client):
    start_session(client, "bad-email")
    send(client, "bad-email", "Business Enquiry")
    send(client, "bad-email", CHALLENGE)
    send(client, "bad-email", AI_CAPABILITY)
    send(client, "bad-email", INDUSTRY)
    send(client, "bad-email", SIZE)
    send(client, "bad-email", "Ada Lovelace")
    send(client, "bad-email", "Analytical Engines")
    data = send(client, "bad-email", "not-an-email").json()
    assert data["step"] == "work_email"
    assert data["type"] == "email"
    assert data["completed"] is False
    assert "valid work email" in data["message"].lower()


def test_step_8_asks_phone_number(client):
    start_session(client, "s8")
    send(client, "s8", "Business Enquiry")
    send(client, "s8", CHALLENGE)
    send(client, "s8", AI_CAPABILITY)
    send(client, "s8", INDUSTRY)
    send(client, "s8", SIZE)
    send(client, "s8", "Ada Lovelace")
    send(client, "s8", "Analytical Engines")
    data = send(client, "s8", "ada@example.com").json()
    assert data["step"] == "phone_number"
    assert data["type"] == "phone"
    assert "Phone Number" in data["message"]


def test_invalid_phone_is_rejected(client):
    start_session(client, "bad-phone")
    send(client, "bad-phone", "Business Enquiry")
    send(client, "bad-phone", CHALLENGE)
    send(client, "bad-phone", AI_CAPABILITY)
    send(client, "bad-phone", INDUSTRY)
    send(client, "bad-phone", SIZE)
    send(client, "bad-phone", "Ada Lovelace")
    send(client, "bad-phone", "Analytical Engines")
    send(client, "bad-phone", "ada@example.com")
    data = send(client, "bad-phone", "abc").json()
    assert data["step"] == "phone_number"
    assert data["completed"] is False
    assert "valid phone" in data["message"].lower()


def test_step_9_asks_current_technology_stack(client):
    start_session(client, "s9")
    send(client, "s9", "Business Enquiry")
    send(client, "s9", CHALLENGE)
    send(client, "s9", AI_CAPABILITY)
    send(client, "s9", INDUSTRY)
    send(client, "s9", SIZE)
    send(client, "s9", "Ada Lovelace")
    send(client, "s9", "Analytical Engines")
    send(client, "s9", "ada@example.com")
    data = send(client, "s9", "+44 7700 900123").json()
    assert data["step"] == "current_technology_stack"
    assert data["type"] == "text"
    assert "Current Technology Stack" in data["message"]


def test_final_enquiry_completes_correctly(client):
    data = run_enquiry_until(client, "complete")
    assert data["completed"] is True
    assert data["type"] == "text"
    assert data["suggestions"] == ["Anything Else?"]
    assert data["step"] is None
    assert data["mode"] == "enquiry"
    assert "submitted successfully" in data["message"]

    stored = chatbot.get_completed_enquiries()
    assert len(stored) == 1
    assert stored[0]["session_id"] == "complete"
    assert stored[0]["full_name"] == "Ada Lovelace"
    assert stored[0]["work_email"] == "ada@example.com"
    assert stored[0]["biggest_operational_challenge"] == CHALLENGE
    assert stored[0]["current_technology_stack"] == "WhatsApp, Salesforce, Excel"


def test_enquiry_email_html_escapes_user_input():
    from app.services.email import build_enquiry_email_html

    html_body = build_enquiry_email_html(
        [
            {
                "session_id": "s1",
                "field": "Full Name",
                "value": "<script>alert(1)</script>",
            }
        ]
    )
    assert "<script>" not in html_body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_body


def test_anything_else_after_enquiry_submit_switches_to_general(client, mock_llm):
    run_enquiry_until(client, "after-submit")
    follow = send(client, "after-submit", "Anything Else?").json()
    assert follow["mode"] == "general"
    assert follow["completed"] is False
    assert follow["message"] == "Sure! What else would you like to know?"
    assert follow["suggestions"] == []
    assert chatbot._sessions["after-submit"].mode == "general"

    answer = send(client, "after-submit", "What services do you provide?").json()
    assert answer["mode"] == "general"
    assert answer["suggestions"] == ["Anything Else?", "Enquire Now"]
    assert len(chatbot.get_completed_enquiries()) == 1


def test_two_users_maintain_separate_session_data(client):
    start_session(client, "user-a")
    start_session(client, "user-b")
    send(client, "user-a", "Business Enquiry")
    send(client, "user-b", "Business Enquiry")
    send(client, "user-a", CHALLENGE)

    user_b = send(client, "user-b", AI_CAPABILITY).json()
    assert "listed options" in user_b["message"].lower()
    assert user_b["step"] == "biggest_operational_challenge"

    user_a = send(client, "user-a", AI_CAPABILITY).json()
    assert user_a["step"] == "primary_industry"

    assert chatbot._sessions["user-a"].data["biggest_operational_challenge"] == CHALLENGE
    assert chatbot._sessions["user-b"].data["biggest_operational_challenge"] is None
    assert chatbot._sessions["user-a"].data["ai_capability"] == AI_CAPABILITY
    assert chatbot._sessions["user-b"].data["ai_capability"] is None


def test_general_mode_answers_website_questions(client, mock_llm):
    start_session(client, "gen-1")
    intro = send(client, "gen-1", "Website / General Question").json()
    assert intro["mode"] == "general"
    assert intro["suggestions"] == []

    answer = send(client, "gen-1", "What services does your company provide?").json()
    assert answer["mode"] == "general"
    assert answer["type"] == "text"
    assert "AI/ML development" in answer["message"]
    assert answer["suggestions"] == ["Anything Else?", "Enquire Now"]
    assert answer["step"] is None
    assert answer["completed"] is False


def test_general_answer_returns_exactly_two_follow_up_options(client, mock_llm):
    start_session(client, "gen-2")
    send(client, "gen-2", "Website / General Question")
    data = send(client, "gen-2", "What industries do you serve?").json()
    assert data["suggestions"] == ["Anything Else?", "Enquire Now"]
    assert len(data["suggestions"]) == 2


def test_anything_else_keeps_user_in_general_mode(client, mock_llm):
    start_session(client, "gen-3")
    send(client, "gen-3", "Website / General Question")
    send(client, "gen-3", "Tell me about your company.")
    follow = send(client, "gen-3", "Anything Else?").json()
    assert follow["mode"] == "general"
    assert follow["message"] == "Sure! What else would you like to know?"
    assert follow["suggestions"] == []
    assert follow["step"] is None

    again = send(client, "gen-3", "What industries do you work with?").json()
    assert again["mode"] == "general"
    assert again["suggestions"] == ["Anything Else?", "Enquire Now"]


def test_enquire_now_switches_same_session_into_enquiry_mode(client, mock_llm):
    session_id = "same-session"
    start_session(client, session_id)
    send(client, session_id, "Website / General Question")
    send(client, session_id, "What services do you provide?")
    data = send(client, session_id, "Enquire Now").json()
    assert data["mode"] == "enquiry"
    assert data["step"] == "biggest_operational_challenge"
    assert data["type"] == "options"
    assert data["suggestions"] == STEP_DEFINITIONS["biggest_operational_challenge"]["options"]
    assert session_id in chatbot._sessions
    assert chatbot._sessions[session_id].mode == "enquiry"


def test_general_question_does_not_enter_enquiry_mode(client, mock_llm):
    start_session(client, "stay-general")
    send(client, "stay-general", "Website / General Question")
    data = send(client, "stay-general", "Business Enquiry").json()
    assert data["mode"] == "general"
    assert data["suggestions"] == ["Anything Else?", "Enquire Now"]
    assert chatbot._sessions["stay-general"].mode == "general"


def test_invalid_conversation_states_are_handled_safely(client):
    start_session(client, "invalid")
    data = send(client, "invalid", "Random click").json()
    assert data["mode"] == "initial"
    assert data["suggestions"] == ["Business Enquiry", "Website / General Question"]

    send(client, "invalid", "Business Enquiry")
    retry = send(client, "invalid", "not a listed challenge").json()
    assert retry["mode"] == "enquiry"
    assert retry["step"] == "biggest_operational_challenge"
    assert retry["completed"] is False

    chatbot._sessions["invalid"].current_step = "not_a_real_step"
    recovered = send(client, "invalid", "anything").json()
    assert recovered["step"] == "biggest_operational_challenge"
    assert recovered["mode"] == "enquiry"


def test_empty_message_rejected_after_session_exists(client):
    start_session(client, "empty")
    send(client, "empty", "Business Enquiry")
    response = send(client, "empty", "   ")
    assert response.status_code == 400
    assert response.json()["detail"] == "message must not be empty"


def test_missing_session_id_is_rejected(client):
    response = client.post("/api/chat", json={"message": "hello"})
    assert response.status_code == 422


def test_new_session_ignores_first_payload_and_returns_greeting(client):
    data = send(client, "fresh", "Business Enquiry").json()
    assert data["mode"] == "initial"
    assert data["suggestions"] == ["Business Enquiry", "Website / General Question"]


def test_completed_enquiry_sends_one_mapped_brevo_email(client, mock_email):
    data = run_enquiry_until(client, "44f95284c2174f4fa787699b45663925")
    assert data["completed"] is True
    assert "submitted successfully" in data["message"]
    assert len(mock_email) == 1
    mapped = mock_email[0]
    assert list(mapped.keys()) == ["mapped_data"]
    items = mapped["mapped_data"]
    assert len(items) == 9
    assert {item["session_id"] for item in items} == {"44f95284c2174f4fa787699b45663925"}
    assert [item["field"] for item in items] == [
        "Biggest Operational Challenge",
        "AI Capability / Area of Interest",
        "Primary Industry",
        "Business Size",
        "Full Name",
        "Company Name",
        "Work Email",
        "Phone Number",
        "Current Technology Stack",
    ]
    assert items[4]["value"] == "Ada Lovelace"
    assert items[6]["value"] == "ada@example.com"


def test_two_completed_enquiries_send_separate_emails(client, mock_email):
    run_enquiry_until(client, "user-a-complete")
    run_enquiry_until(client, "user-b-complete")
    assert len(mock_email) == 2
    first_ids = {item["session_id"] for item in mock_email[0]["mapped_data"]}
    second_ids = {item["session_id"] for item in mock_email[1]["mapped_data"]}
    assert first_ids == {"user-a-complete"}
    assert second_ids == {"user-b-complete"}


def test_general_questions_do_not_send_email(client, mock_llm, mock_email):
    start_session(client, "gen-email")
    send(client, "gen-email", "Website / General Question")
    send(client, "gen-email", "What services does your company provide?")
    send(client, "gen-email", "Anything Else?")
    assert mock_email == []


def test_enquire_now_sends_email_only_after_full_enquiry(client, mock_llm, mock_email):
    session_id = "enquire-now-email"
    start_session(client, session_id)
    send(client, session_id, "Website / General Question")
    send(client, session_id, "What services do you provide?")
    send(client, session_id, "Enquire Now")
    assert mock_email == []
    assert chatbot._sessions[session_id].mode == "enquiry"

    answers = {
        "biggest_operational_challenge": CHALLENGE,
        "ai_capability": AI_CAPABILITY,
        "primary_industry": INDUSTRY,
        "business_size": SIZE,
        "full_name": "Ada Lovelace",
        "company_name": "Analytical Engines",
        "work_email": "ada@example.com",
        "phone_number": "+44 7700 900123",
        "current_technology_stack": "WhatsApp, Salesforce, Excel",
    }
    last = None
    for step in ENQUIRY_FIELD_ORDER:
        last = send(client, session_id, answers[step])
        assert last.status_code == 200
    assert last.json()["completed"] is True
    assert len(mock_email) == 1
    assert {item["session_id"] for item in mock_email[0]["mapped_data"]} == {session_id}


def test_brevo_failure_does_not_claim_success(client, monkeypatch):
    async def fail_send(_mapped: dict) -> None:
        raise EmailSendError("Enquiry email could not be sent")

    monkeypatch.setattr(chatbot, "send_enquiry_email", fail_send)
    data = run_enquiry_until(client, "email-fail")
    assert data["completed"] is True
    assert data["message"] == EMAIL_FAILURE_MESSAGE
    assert "submitted successfully" not in data["message"].lower()
    assert "brevo" not in data["message"].lower()
    assert "api key" not in data["message"].lower()
    assert data["suggestions"] == ["Anything Else?"]


def test_map_enquiry_data_shape():
    enquiry = {
        "session_id": "44f95284c2174f4fa787699b45663925",
        "biggest_operational_challenge": "Repetitive manual data entry & paper/PDF document processing",
        "ai_capability": "AI Agents & Workflow Automation",
        "primary_industry": "Hospitality & Restaurants",
        "business_size": "11 - 50 employees",
        "full_name": "inder",
        "company_name": "weboum",
        "work_email": "weboum@g.com",
        "phone_number": "9876543210",
        "current_technology_stack": "WhatsApp",
    }
    mapped = map_enquiry_data(enquiry)
    assert mapped == {
        "mapped_data": [
            {
                "session_id": "44f95284c2174f4fa787699b45663925",
                "field": "Biggest Operational Challenge",
                "value": "Repetitive manual data entry & paper/PDF document processing",
            },
            {
                "session_id": "44f95284c2174f4fa787699b45663925",
                "field": "AI Capability / Area of Interest",
                "value": "AI Agents & Workflow Automation",
            },
            {
                "session_id": "44f95284c2174f4fa787699b45663925",
                "field": "Primary Industry",
                "value": "Hospitality & Restaurants",
            },
            {
                "session_id": "44f95284c2174f4fa787699b45663925",
                "field": "Business Size",
                "value": "11 - 50 employees",
            },
            {
                "session_id": "44f95284c2174f4fa787699b45663925",
                "field": "Full Name",
                "value": "inder",
            },
            {
                "session_id": "44f95284c2174f4fa787699b45663925",
                "field": "Company Name",
                "value": "weboum",
            },
            {
                "session_id": "44f95284c2174f4fa787699b45663925",
                "field": "Work Email",
                "value": "weboum@g.com",
            },
            {
                "session_id": "44f95284c2174f4fa787699b45663925",
                "field": "Phone Number",
                "value": "9876543210",
            },
            {
                "session_id": "44f95284c2174f4fa787699b45663925",
                "field": "Current Technology Stack",
                "value": "WhatsApp",
            },
        ]
    }
