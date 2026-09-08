from app.services import chatbot
from app.services.enquiry import ENQUIRY_FIELD_ORDER, STEP_DEFINITIONS

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


def test_general_mode_tracks_conversation_history(client, mock_llm):
    session_id = "history-session"
    start_session(client, session_id)
    send(client, session_id, "Website / General Question")
    send(client, session_id, "Give me contact information.")
    send(client, session_id, "address")

    session = chatbot._sessions[session_id]
    assert len(session.history) == 4
    assert session.history[0]["role"] == "user"
    assert session.history[0]["content"] == "Give me contact information."
    assert session.history[1]["role"] == "assistant"
    assert session.history[2]["role"] == "user"
    assert session.history[2]["content"] == "address"
    assert session.history[3]["role"] == "assistant"


def test_resolve_retrieval_query_contextualizes_short_and_contact_terms():
    # Short query or contact term without previous context
    q1 = chatbot._resolve_retrieval_query("address", [])
    assert q1 == "Weboum Technology address"

    # Short follow up with previous user message
    history = [{"role": "user", "content": "What AI services do you provide?"}]
    q2 = chatbot._resolve_retrieval_query("pricing?", history)
    assert "Weboum Technology" in q2
    assert "pricing?" in q2
    assert "What AI services do you provide?" in q2

    # Query already containing Weboum
    q3 = chatbot._resolve_retrieval_query("What does Weboum do?")
    assert q3 == "What does Weboum do?"



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


def test_missing_session_id_is_auto_generated(client):
    response = client.post("/api/chat", json={"message": ""})
    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] is not None
    assert len(data["session_id"]) > 0
    assert data["mode"] == "initial"
    assert data["message"] == "Hi! How can I help you today?"


def test_new_session_ignores_first_payload_and_returns_greeting(client):
    data = send(client, "fresh", "Business Enquiry").json()
    assert data["mode"] == "initial"
    assert data["suggestions"] == ["Business Enquiry", "Website / General Question"]


def test_get_all_sessions_empty(client):
    response = client.get("/api/all-sessions")
    assert response.status_code == 200
    data = response.json()
    assert data["total_sessions"] == 0
    assert data["sessions"] == {}
    assert data["completed_enquiries"] == []


def test_get_all_sessions_returns_full_information(client):
    start_session(client, "sess-1")
    send(client, "sess-1", "Business Enquiry")
    send(client, "sess-1", CHALLENGE)

    response = client.get("/api/all-sessions")
    assert response.status_code == 200
    data = response.json()
    assert data["total_sessions"] == 1
    assert "sess-1" in data["sessions"]

    s1 = data["sessions"]["sess-1"]
    assert s1["session_id"] == "sess-1"
    assert s1["mode"] == "enquiry"
    assert s1["current_step"] == "ai_capability"
    assert s1["data"]["biggest_operational_challenge"] == CHALLENGE
    assert s1["completed"] is False


def test_one_digit_name_is_rejected(client):
    start_session(client, "v-name")
    send(client, "v-name", "Business Enquiry")
    send(client, "v-name", CHALLENGE)
    send(client, "v-name", AI_CAPABILITY)
    send(client, "v-name", INDUSTRY)
    send(client, "v-name", SIZE)

    # 1 digit name
    res = send(client, "v-name", "1").json()
    assert res["step"] == "full_name"
    assert res["completed"] is False
    assert "valid full name" in res["message"].lower()


def test_one_digit_company_is_rejected(client):
    start_session(client, "v-co")
    send(client, "v-co", "Business Enquiry")
    send(client, "v-co", CHALLENGE)
    send(client, "v-co", AI_CAPABILITY)
    send(client, "v-co", INDUSTRY)
    send(client, "v-co", SIZE)
    send(client, "v-co", "Ada Lovelace")

    # 1 digit company
    res = send(client, "v-co", "1").json()
    assert res["step"] == "company_name"
    assert res["completed"] is False
    assert "valid company name" in res["message"].lower()


def test_one_digit_tech_stack_is_rejected(client):
    start_session(client, "v-tech")
    send(client, "v-tech", "Business Enquiry")
    send(client, "v-tech", CHALLENGE)
    send(client, "v-tech", AI_CAPABILITY)
    send(client, "v-tech", INDUSTRY)
    send(client, "v-tech", SIZE)
    send(client, "v-tech", "Ada Lovelace")
    send(client, "v-tech", "Analytical Engines")
    send(client, "v-tech", "ada@example.com")
    send(client, "v-tech", "+44 7700 900123")

    # 1 digit tech stack
    res = send(client, "v-tech", "1").json()
    assert res["step"] == "current_technology_stack"
    assert res["completed"] is False
    assert "tools or technology stack" in res["message"].lower()


def test_email_sent_on_completion(client, mock_email):
    run_enquiry_until(client, "email-test")
    assert len(mock_email) == 1
    mapped = mock_email[0]["mapped_data"]
    assert len(mapped) == 9
    assert mapped[0]["session_id"] == "email-test"
    assert mapped[4]["field"] == "Full Name"
    assert mapped[4]["value"] == "Ada Lovelace"


def test_email_failure_returns_failure_message(client, monkeypatch):
    from app.services.email import EmailSendError

    async def fail_send(mapped):
        raise EmailSendError("Failed")

    monkeypatch.setattr("app.services.chatbot.send_enquiry_email", fail_send)

    res = run_enquiry_until(client, "fail-email")
    assert "could not notify our team" in res["message"].lower()
    assert res["completed"] is True

