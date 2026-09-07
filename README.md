# Company Website Chatbot (MVP)

Async FastAPI backend for a company-website chatbot. One endpoint drives every turn. Business enquiry is a fixed Python state machine. Website questions go through an LLM grounded in a single knowledge file. Conversation state lives only in process memory.

---

## Agent working memory

Read this section before opening the rest of the repo. It is the map of behaviour and files.

**Contract**

- Only public chatbot route: `POST /api/chat`
- Request: `{ "session_id": str, "message": str }`
- Response: `{ message, type, suggestions, mode, step, completed }`
- `type`: `options` | `text` | `email` | `phone`
- `mode`: `initial` | `enquiry` | `general`
- First request for an unknown `session_id` **always** returns the greeting, even if `message` is set
- Empty `message` is allowed only to create a session; later empty messages return HTTP 400
- Frontend generates `session_id` and sends suggestion text back as `message`
- Frontend must not hardcode enquiry questions

**Flow**

1. New session → `Hi! How can I help you today?` with exactly `Business Enquiry` and `Website / General Question`
2. `Business Enquiry` → 9 fixed steps in `app/services/enquiry.py` (`ENQUIRY_FIELD_ORDER`). LLM never chooses the next step
3. After step 9 → `map_enquiry_data()` → async Brevo email via `app/services/email.py` → confirmation, `completed: true`, suggestion exactly `Anything Else?`. Email is sent once, only after all 9 steps. If Brevo fails, do **not** use the success copy; use `EMAIL_FAILURE_MESSAGE`.
4. After submit, `Anything Else?` switches **the same session** to `mode=general` (`Sure! What else would you like to know?`)
5. `Website / General Question` → `mode=general`, user asks a question, Groq LLM + `app/prompts/general.py`, then exactly `Anything Else?` and `Enquire Now`
6. `Anything Else?` stays in general
7. `Enquire Now` switches **the same session** to enquiry at step 1

**Enquiry steps (do not reorder)**

1. `biggest_operational_challenge` (options)
2. `ai_capability` (options)
3. `primary_industry` (options)
4. `business_size` (options)
5. `full_name` (text)
6. `company_name` (text)
7. `work_email` (email)
8. `phone_number` (phone)
9. `current_technology_stack` (text)

**Files**

| Path | Role |
|------|------|
| `app/main.py` | FastAPI app, CORS, router include. No business logic |
| `app/api/chat.py` | `POST /api/chat` only |
| `app/schemas/chat.py` | `ChatRequest`, `ChatResponse` |
| `app/core/config.py` | `GROQ_API_KEY`, `LLM_MODEL`, `APP_ENV`, `CORS_ORIGINS`, `BREVO_API_KEY`, `ENQUIRY_EMAIL_TO`, `BREVO_SENDER_EMAIL`, `BREVO_SENDER_NAME` |
| `app/services/chatbot.py` | Runtime sessions, locks, routing, Groq call, one email send on enquiry completion |
| `app/services/enquiry.py` | Deterministic enquiry machine, validation, `Session`, `map_enquiry_data()` |
| `app/services/email.py` | HTML email + async Brevo HTTP API. No enquiry-step logic |
| `app/prompts/general.py` | `GENERAL_SYSTEM_PROMPT` + `COMPANY_KNOWLEDGE` (edit company facts here only) |
| `tests/test_chat.py` | Acceptance tests; LLM is mocked |

**Runtime store (module globals in `chatbot.py`)**

- `_sessions[session_id] -> Session(mode, current_step, data, completed)`
- `_completed_enquiries` list of `{ session_id, ...nine fields }`
- Per-session `asyncio.Lock` so concurrent users do not mix state
- `reset_runtime_state()` for tests only

**Git (do this every time code is fetched or pushed)**

- Remote: `https://github.com/Inderjeet-singh01/Weboum-Enquiry-Chatbot.git`
- Branch: **`main` only** — never push or pull feature branches unless the user explicitly changes this
- Do **not** push until the user says to push
- Do not commit `.env` or secrets

**Out of scope on purpose**

No database, Redis, Docker, CRM, RAG/vector store, extra chat endpoints, or multi-worker design. Enquiry email is Brevo HTTP only (not SMTP).

**Replace later**

- Company copy: `app/prompts/general.py` → `COMPANY_KNOWLEDGE`
- Persistence: swap the in-memory dict; keep `handle_chat` / enquiry API the same
- RAG: retrieve chunks, still pass them into `generate_general_answer`

---

## 1. How to Run

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Set `GROQ_API_KEY` and the Brevo variables in `.env`. Then start **one** Uvicorn worker:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

- API: http://127.0.0.1:8000/api/chat
- OpenAPI: http://127.0.0.1:8000/docs

Do not use multiple Uvicorn workers for this MVP. Each process has its own memory.

---

## 2. Project Overview

The chatbot on the company website has two entry points:

1. **Business Enquiry** — nine questions, one at a time, exact order, Python-controlled.
2. **Website / General Question** — LLM answers from centralized website knowledge, then offers Anything Else? or Enquire Now.

There is no database. Sessions and completed enquiries exist only while this process is running.

---

## 3. Architecture

```
Frontend
   │  POST /api/chat  { session_id, message }
   ▼
app/api/chat.py
   ▼
app/services/chatbot.py          # session + mode router
   ├── initial greeting
   ├── enquiry  → app/services/enquiry.py → on complete, app/services/email.py (Brevo)
   └── general  → Groq LLM + app/prompts/general.py
   ▼
ChatResponse { message, type, suggestions, mode, step, completed }
```

- FastAPI + Uvicorn, async endpoint and async LLM call
- Pydantic v2 request/response models
- pydantic-settings for environment variables
- Enquiry sequence is data in `enquiry.py`, not prompt text

---

## 4. Folder Structure

```
app/
├── main.py
├── api/chat.py
├── schemas/chat.py
├── core/config.py
├── services/chatbot.py
├── services/enquiry.py
├── services/email.py
└── prompts/general.py
tests/
├── conftest.py
└── test_chat.py
.env.example
.gitignore
requirements.txt
README.md
```

---

## 5. API Endpoint

`POST /api/chat`

That is the only chatbot endpoint. Do not add per-step or per-topic routes.

---

## 6. Request Format

```json
{
  "session_id": "abc123",
  "message": "Business Enquiry"
}
```

| Field | Rules |
|-------|--------|
| `session_id` | Required, non-empty after strip |
| `message` | Stripped. Empty is OK only on the first request for that id |

---

## 7. Response Format

```json
{
  "message": "Hi! How can I help you today?",
  "type": "options",
  "suggestions": ["Business Enquiry", "Website / General Question"],
  "mode": "initial",
  "step": null,
  "completed": false
}
```

Internal session dicts, locks, and API keys are never returned.

---

## 8. Business Enquiry Flow

Triggered by message `Business Enquiry` (from `initial`) or `Enquire Now` (from `general`).

Questions are asked one by one. Option steps require an exact match against the listed suggestion. Invalid email/phone re-asks the same step with an error prefix. After the ninth value:

- `completed` is true
- confirmation message is returned
- suggestion is exactly `["Anything Else?"]`
- an enquiry object is appended to in-memory `_completed_enquiries`
- one Brevo email is sent with `mapped_data` (HTML table)
- `Anything Else?` after submit moves the same session into general chat
- If Brevo fails: enquiry stays completed in memory, user is **not** told the email was sent

---

## 9. Website / General Question Flow

1. User selects `Website / General Question`
2. Bot: `Sure! What would you like to know about us?`
3. User types a question
4. Groq uses `GENERAL_SYSTEM_PROMPT` + `COMPANY_KNOWLEDGE`
5. Answer plus suggestions `["Anything Else?", "Enquire Now"]`
6. `Anything Else?` → stay in general, invite another question
7. `Enquire Now` → same `session_id`, enquiry step 1

Unrelated questions (weather, sports, etc.) are refused by the system prompt; the model must not invent company facts.

If Groq fails or `GROQ_API_KEY` is missing, the user gets a fallback message and the same two follow-up suggestions. Mode stays `general`.

---

## 10. Runtime Memory Design

```text
_sessions = {
  "abc123": Session(
      mode="enquiry",
      current_step="full_name",
      data={ nine enquiry keys },
      completed=False,
  )
}
```

This is intentional for the MVP. No SQLAlchemy, SQLite, Redis, or Mongo.

---

## 11. Multi-user Session Handling

Every visitor needs a unique `session_id`. User A (`abc123`) and User B (`xyz789`) never share a `Session`. Updates run under a per-session `asyncio.Lock` so two concurrent requests on the same id cannot interleave, while different users can proceed in parallel on one process.

---

## 12. Environment Variables

Copy `.env.example` to `.env`. Never commit `.env`.

| Variable | Purpose |
|----------|---------|
| `GROQ_API_KEY` | Groq API key (required for live general answers) |
| `LLM_MODEL` | Default `llama-3.3-70b-versatile` |
| `APP_ENV` | e.g. `development` |
| `CORS_ORIGINS` | Comma-separated origins, e.g. `http://localhost:3000` |
| `BREVO_API_KEY` | Brevo API key (server-side only; never log or return it) |
| `ENQUIRY_EMAIL_TO` | Company inbox that receives completed enquiries |
| `BREVO_SENDER_EMAIL` | Verified Brevo sender address |
| `BREVO_SENDER_NAME` | Sender display name, default `Website Enquiry Bot` |

---

## 13. Frontend Integration

1. Create a unique `session_id` when the widget opens
2. `POST /api/chat` (message may be `""` on the first call)
3. Render `message`
4. If `suggestions` is non-empty, render them as buttons
5. If `type` is `text` / `email` / `phone`, show that input
6. On button click or submit, send the value as `message` with the **same** `session_id` to **the same** URL
7. Repeat until `completed` is true (enquiry) or the user closes the widget

The backend owns all copy and option lists.

---

## 14. Testing

```bash
pytest -q
```

Coverage includes: greeting, all nine enquiry steps, invalid email/phone, completion object, two isolated users, general answers and follow-ups, Anything Else?, Enquire Now on the same session, general text not entering enquiry, and invalid state recovery. Groq is mocked.

---

## 15. Runtime limitation

- Data exists only while this Uvicorn process is running
- Restart, crash, or deploy clears sessions and completed enquiries
- Multiple workers would each have a separate dict and must not be used
- This limitation is accepted for the MVP

When you need persistence or horizontal scale, introduce Redis or a database behind the same `Session` shape. Do not add that until it is required.

---

## 16. Future extension notes

- Replace `COMPANY_KNOWLEDGE` with CMS, crawl, or documents
- Add retrieval (RAG) in front of `generate_general_answer` without changing `/api/chat`
- Persist sessions and completed enquiries
- CRM integration beyond the current Brevo notification
- Keep enquiry order in Python; do not let the LLM drive it
