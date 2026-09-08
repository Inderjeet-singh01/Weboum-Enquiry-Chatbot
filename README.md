# Company Website Chatbot (MVP)

Async FastAPI backend for a company-website chatbot. One endpoint drives every turn. Business enquiry is a fixed Python state machine with Brevo email notifications. Website questions go through a Groq LLM grounded in semantic vector embeddings retrieved locally without an external vector database. Conversation state lives in process memory.

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
3. After step 9 → confirmation, `completed: true`, enquiry emailed to team via Brevo, stored in `_completed_enquiries`, suggestion exactly `Anything Else?`
4. After submit, `Anything Else?` switches **the same session** to `mode=general` (`Sure! What else would you like to know?`)
5. `Website / General Question` → `mode=general`, user asks a question, local vector embedding retrieval + Groq LLM, then exactly `Anything Else?` and `Enquire Now`
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
| `app/main.py` | FastAPI app, CORS, router include, static files mount. No business logic |
| `app/api/chat.py` | `POST /api/chat` and `GET /api/all-sessions` |
| `app/schemas/chat.py` | `ChatRequest`, `ChatResponse` |
| `app/core/config.py` | Settings: Groq, Brevo, CORS, and embedding model paths |
| `app/services/chatbot.py` | Runtime sessions, locks, routing, Groq call (`generate_general_answer`) |
| `app/services/enquiry.py` | Deterministic enquiry machine, validation, `Session` dataclass |
| `app/services/email.py` | Async Brevo transactional email sender for completed enquiries |
| `app/services/rag.py` | Dense vector embedding retrieval, NumPy cosine similarity, context budget |
| `app/prompts/general.py` | `GENERAL_SYSTEM_PROMPT` (context-grounded prompt without hardcoded facts) |
| `scripts/create_embeddings.py` | Offline embedding generator (JSON knowledge → `data/embeddings.pkl`) |
| `data/embeddings.pkl` | Precomputed vector embeddings index (423 chunks × 384 dimensions) |
| `company-docs/weboum_knowledge.json` | Weboum company knowledge base (crawled documentation) |
| `tests/test_chat.py` | Acceptance tests; LLM and email are mocked |
| `tests/test_rag.py` | Unit tests for vector retrieval, context formatting, and index loading |

**Runtime store (module globals in `chatbot.py`)**

- `_sessions[session_id] -> Session(mode, current_step, data, completed)`
- `_completed_enquiries` list of `{ session_id, ...nine fields }`
- Per-session `asyncio.Lock` so concurrent users do not mix state
- `reset_runtime_state()` for tests only

**Out of scope on purpose**

No external vector database (Chroma/Pinecone), Redis, Docker, SQL database, or multi-worker design.

**Future extensions**

- Re-run `scripts/create_embeddings.py` whenever `company-docs/weboum_knowledge.json` changes
- Persistence: swap the in-memory dict with a database; keep `handle_chat` / enquiry API the same

---

## 1. How to Run

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Set `GROQ_API_KEY` and Brevo email variables in `.env`. Precompute the embeddings (already generated):

```bash
python scripts/create_embeddings.py
```

Then start **one** Uvicorn worker:

### Development
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

- Web UI: http://127.0.0.1:8000/
- API: http://127.0.0.1:8000/api/chat
- Health Check: http://127.0.0.1:8000/health
- OpenAPI: http://127.0.0.1:8000/docs

### Production / Render
```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

- Health Check Path: `/health` (HTTP 200 `{"status": "ok"}`)
- Do not use `--reload` in production.
- Do not use multiple Uvicorn workers for this MVP. Each process has its own memory.

---

## 2. Project Overview

The chatbot on the company website has two entry points:

1. **Business Enquiry** — nine questions, one at a time, exact order, Python-controlled. Completed submissions are emailed directly to the team via Brevo.
2. **Website / General Question** — User queries are converted to dense vector embeddings, matched via cosine similarity against `data/embeddings.pkl` in memory, and answered accurately by the Groq LLM.

There is no database. Sessions and completed enquiries exist only while this process is running.

---

## 3. Architecture

```text
Frontend (Widget / UI)
   │  POST /api/chat  { session_id, message }
   ▼
app/api/chat.py
   ▼
app/services/chatbot.py               # session + mode router
   ├── initial greeting
   ├── enquiry  → app/services/enquiry.py → app/services/email.py (Brevo)
   └── general  → app/services/rag.py (embeddings.pkl) → Groq LLM
   ▼
ChatResponse { message, type, suggestions, mode, step, completed }
```

- FastAPI + Uvicorn, async endpoint and async LLM call
- Pydantic v2 request/response models
- Local vector embeddings with `BAAI/bge-small-en-v1.5` and NumPy dot product (no vector DB)
- Enquiry sequence is data in `enquiry.py`, not prompt text

---

## 4. Folder Structure

```text
app/
├── main.py
├── api/chat.py
├── schemas/chat.py
├── core/config.py
├── services/
│   ├── chatbot.py
│   ├── enquiry.py
│   ├── email.py
│   └── rag.py
└── prompts/general.py
company-docs/
└── weboum_knowledge.json
data/
└── embeddings.pkl
scripts/
└── create_embeddings.py
static/
├── index.html
├── style.css
└── script.js
tests/
├── conftest.py
├── test_chat.py
└── test_rag.py
.env.example
.gitignore
requirements.txt
README.md
```

---

## 5. API Endpoints
 
 - `POST /api/chat` — The primary public chatbot turn endpoint.
 - `GET /health` — Lightweight health check endpoint returning `{"status": "ok"}` (HTTP 200) for deployment monitors.
 - `GET /api/all-sessions` — Active sessions debug inspector.

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
| `session_id` | Required, non-empty after strip (auto-generated if omitted) |
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
- an enquiry notification is sent to the team via Brevo transactional email
- an enquiry object is appended to in-memory `_completed_enquiries`

---

## 9. Website / General Question Flow

1. User selects `Website / General Question`
2. Bot: `Sure! What would you like to know about us?`
3. User types a question (e.g. `"address"`, `"What services do you provide?"`)
4. Query is embedded into a 384-d vector and compared via cosine similarity against `data/embeddings.pkl`
5. Most relevant chunks are selected within the character budget and injected into `GENERAL_SYSTEM_PROMPT`
6. Groq LLM generates a factual, grounded response
7. Answer is returned plus suggestions `["Anything Else?", "Enquire Now"]`
8. `Anything Else?` → stay in general, invite another question
9. `Enquire Now` → same `session_id`, enquiry step 1

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
| `LLM_MODEL` | Default `openai/gpt-oss-120b` |
| `APP_ENV` | e.g. `development` |
| `CORS_ORIGINS` | Comma-separated origins, e.g. `http://localhost:3000` |
| `BREVO_API_KEY` | Brevo API key for sending enquiry emails |
| `ENQUIRY_EMAIL_TO` | Notification recipient email for completed enquiries |
| `BREVO_SENDER_EMAIL` | Verified sender email configured in Brevo |
| `BREVO_SENDER_NAME` | Display name for the email sender |
| `EMBEDDING_MODEL` | Embedding model name. Default `BAAI/bge-small-en-v1.5` |
| `RAG_INDEX_PATH` | Path to embeddings pickle file. Default `data/embeddings.pkl` |

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

Coverage includes: greeting, all nine enquiry steps, invalid email/phone, completion object, Brevo email sending, two isolated users, general answers and follow-ups, Anything Else?, Enquire Now on the same session, general text not entering enquiry, invalid state recovery, vector embedding retrieval, and context budgeting. Groq is mocked.

---

## 15. Runtime limitation

- Data exists only while this Uvicorn process is running
- Restart, crash, or deploy clears sessions and completed enquiries
- Multiple workers would each have a separate dict and must not be used
- This limitation is accepted for the MVP

When you need persistence or horizontal scale, introduce Redis or a database behind the same `Session` shape. Do not add that until it is required.

---

## 16. Future extension notes

- Re-generate `data/embeddings.pkl` whenever `company-docs/weboum_knowledge.json` changes (`python scripts/create_embeddings.py`)
- Swap the embedding model by setting `EMBEDDING_MODEL` (and regenerate the index with the same model)
- Persist sessions and completed enquiries in Postgres or Redis
- Direct CRM integration on enquiry completion
- Keep enquiry order in Python; do not let the LLM drive it
