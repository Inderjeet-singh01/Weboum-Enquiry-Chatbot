"""Main conversation controller: sessions, routing, and unified responses."""

from __future__ import annotations

import asyncio
from enum import Enum
import logging
from collections.abc import AsyncGenerator
import re
import time
from typing import Any
import uuid

from fastapi import HTTPException
from groq import AsyncGroq

from app.core.config import settings
from app.prompts.general import GENERAL_SYSTEM_PROMPT
from app.schemas.chat import (
    ChatResponse,
    ConversationMode,
    ResponseType,
    validate_company_name,
    validate_full_name,
    validate_phone_number,
    validate_tech_stack,
    validate_work_email,
)
from app.services import rag
from app.services.email import EMAIL_FAILURE_MESSAGE, EmailSendError, send_enquiry_email
from app.services.enquiry import (
    ENQUIRY_FIELD_ORDER,
    STEP_DEFINITIONS,
    Session,
    build_enquiry_object,
    map_enquiry_data,
    process_enquiry_answer,
    reset_enquiry_state,
    start_enquiry,
    step_response,
)

logger = logging.getLogger(__name__)

BUSINESS_ENQUIRY = "Business Enquiry"
BUSINESS_SOLUTIONS_ENQUIRY = "Business Solutions Enquiry"
WEBSITE_GENERAL = "Website / General Question"
ANYTHING_ELSE = "Anything Else?"
ENQUIRE_NOW = "Enquire Now"

INITIAL_MESSAGE = "Hi! How can I help you today?"
INITIAL_SUGGESTIONS = [WEBSITE_GENERAL, BUSINESS_SOLUTIONS_ENQUIRY]
GENERAL_FOLLOW_UP = [WEBSITE_GENERAL, BUSINESS_SOLUTIONS_ENQUIRY]

GENERAL_INTRO_MESSAGE = "Sure! What would you like to know about us?"
ANYTHING_ELSE_MESSAGE = "Sure! What else would you like to know?"
CHOOSE_INITIAL_OPTION = "Please choose one of the options below."
EMPTY_MESSAGE_DETAIL = "message must not be empty"
LLM_FALLBACK_MESSAGE = (
    "Sorry, I could not answer just now. Please try again, or start a business enquiry."
)

_sessions: dict[str, Session] = {}
_completed_enquiries: list[dict[str, str | None]] = []
_session_locks: dict[str, asyncio.Lock] = {}
_registry_lock = asyncio.Lock()


def reset_runtime_state() -> None:
    """Used by tests to isolate cases. Not part of the public API."""
    _sessions.clear()
    _completed_enquiries.clear()
    _session_locks.clear()


def get_completed_enquiries() -> list[dict[str, str | None]]:
    return list(_completed_enquiries)


def get_all_sessions() -> dict[str, Any]:
    sessions_snapshot = {}
    for session_id, session in list(_sessions.items()):
        sessions_snapshot[session_id] = {
            "session_id": session_id,
            "mode": session.mode,
            "current_step": session.current_step,
            "data": dict(session.data) if session.data else {},
            "completed": session.completed,
        }
    return {
        "total_sessions": len(sessions_snapshot),
        "sessions": sessions_snapshot,
        "completed_enquiries": list(_completed_enquiries),
    }


async def handle_chat(session_id: str | None, message: str) -> ChatResponse:
    if not session_id or not session_id.strip():
        session_id = uuid.uuid4().hex
    else:
        session_id = session_id.strip()

    lock = await _lock_for(session_id)
    async with lock:
        response = await _handle_locked(session_id, message)
        response.session_id = session_id
        return response


class UserIntent(str, Enum):
    NAVIGATION = "navigation"
    GENERAL_QUESTION = "general_question"
    ENQUIRY_ANSWER = "enquiry_answer"
    UNCERTAIN = "uncertain"


def is_semantic_general_query(message: str, current_step: str | None = None) -> bool:
    """Analyze whether a message expresses general/informational query intent.
    
    Covers natural-language variations without brittle exact keyword matching:
    - Questions with '?' or interrogative syntax (what, which, who, how, why, where, when)
    - Inverted auxiliary verbs (can you, could you, does weboum, is there, are you, etc.)
    - Imperative request phrasing (tell me, explain, describe, give me, show me, etc.)
    - Prepositional topic inquiries (about the company, details on, everything about, etc.)
    - Quantifier / catalog inquiries (all the teams, all projects delivered, etc.)
    - Domain inquiries (who is the CEO, team leads, services, pricing, technologies, etc.)
    
    Protects free-text enquiry answers (e.g. company names like 'Weboum Technology' or
    tech stack like 'Python React Node') from false positives.
    """
    if not message or not message.strip():
        return False

    msg = message.strip()
    lower_msg = msg.lower()
    words = re.findall(r"[a-zA-Z']+", lower_msg)
    if not words:
        return False

    # 1. Direct punctuation indicator
    if "?" in msg:
        return True

    # 2. Interrogative pronoun/adverb starters
    question_starters = {
        "what", "what's", "whats", "which", "who", "who's", "whom", "whose",
        "why", "why's", "where", "where's", "when", "when's", "how", "how's",
    }
    if words[0] in question_starters:
        return True

    # 3. Inverted auxiliary question starters (modal / auxiliary verbs)
    aux_starters = {
        ("can", "you"), ("can", "i"), ("can", "we"),
        ("could", "you"), ("could", "i"), ("could", "we"),
        ("would", "you"), ("will", "you"),
        ("do", "you"), ("does", "weboum"), ("does", "it"),
        ("is", "there"), ("is", "it"), ("is", "weboum"),
        ("are", "there"), ("are", "you"),
        ("should", "i"), ("may", "i"), ("shall", "we"),
        ("have", "you"), ("has", "weboum"), ("did", "you"),
    }
    if len(words) >= 2 and (words[0], words[1]) in aux_starters:
        return True

    # 4. Imperative & declarative request starters
    req_prefixes = (
        "tell me", "tell us", "give me", "give us", "show me", "show us",
        "explain", "describe", "provide", "share", "clarify", "elaborate",
        "i want to know", "i would like to know", "i'd like to know", "i need to know",
        "i wanna know", "i was wondering", "let me know", "help me understand",
        "can you tell", "please tell", "could you share", "walk me through",
    )
    if any(lower_msg.startswith(p) for p in req_prefixes):
        return True

    # 5. Prepositional topic inquiries
    prepositional_starters = (
        "about ", "about the ", "details about ", "full details about ",
        "more details about ", "details on ", "details of ",
        "information about ", "information on ", "info about ", "info on ",
        "more about ", "more on ", "everything about ", "all about ", "anything about ",
        "company information",
    )
    if any(lower_msg.startswith(p) for p in prepositional_starters):
        return True

    info_phrases = {
        "company details", "company information", "company info",
        "company overview", "company profile", "services overview",
        "business overview", "team details", "team info",
    }
    if lower_msg in info_phrases:
        return True

    # 6. Quantifier / catalog inquiries (e.g. 'all the teams', 'all projects delivered')
    quantifier_starters = ("all the ", "all of ", "all of the ", "every ", "what all ")
    if any(lower_msg.startswith(q) for q in quantifier_starters):
        return True

    if words[0] in ("all", "every", "list"):
        domain_targets = {
            "teams", "team", "leads", "lead", "projects", "services", "solutions",
            "products", "clients", "offerings", "work", "deliverables", "delivered",
            "technologies", "tech", "staff", "members", "people",
        }
        if any(w in domain_targets for w in words[1:]):
            return True

    # 7. Targeted inquiries about company personnel / leadership / management
    leadership_terms = {
        "ceo", "cto", "cfo", "coo", "founder", "founders",
        "leadership", "leads", "leader", "leaders",
        "management", "executives", "directors",
    }
    if any(term in words for term in leadership_terms):
        company_assoc = {
            "who", "tell", "details", "about", "is", "name", "the", "of",
            "company", "weboum", "firm", "organization", "team", "your", "our", "runs", "head",
        }
        if len(words) == 1 or any(k in words for k in company_assoc):
            return True

    # 8. Standalone & contextual inquiry nouns regarding services / pricing / products / technologies
    inquiry_nouns = {
        "services", "pricing", "cost", "portfolio", "offerings", "features", "products", "projects", "technologies",
    }
    if any(noun in words for noun in inquiry_nouns):
        if len(words) == 1:
            return True
        if any(term in words for term in ("weboum", "company", "firm", "organization", "your", "our", "all", "what", "tell", "list", "show", "of")):
            return True

    return False


def classify_user_intent(session: Session | None, message: str) -> UserIntent:
    """Authoritative intent classifier shared across all endpoints and execution paths.

    Strictly implements the field-aware routing model:
    1. Navigation actions -> NAVIGATION
    2. Initial mode:
       - General semantic query -> GENERAL_QUESTION
       - Else -> UNCERTAIN
    3. General mode -> GENERAL_QUESTION
    4. Enquiry mode:
       - If completed -> ENQUIRY_ANSWER
       - If option-based step:
           - If message in predefined options -> ENQUIRY_ANSWER
           - Else if semantic general query -> GENERAL_QUESTION
           - Else -> UNCERTAIN (stays on current step with validation error)
       - If work_email:
           - Try validate_work_email(message):
               - PASS -> ENQUIRY_ANSWER
           - FAIL:
               - If semantic general query -> GENERAL_QUESTION
               - Else -> UNCERTAIN (stays on work_email with validation error)
       - If phone_number:
           - Try phone number validation:
               - PASS (and not general query) -> ENQUIRY_ANSWER
           - FAIL:
               - If semantic general query -> GENERAL_QUESTION
               - Else -> UNCERTAIN (stays on phone_number with validation error)
       - If full_name:
           - If semantic general query -> GENERAL_QUESTION
           - Else try validate_full_name(message):
               - PASS -> ENQUIRY_ANSWER
               - FAIL -> UNCERTAIN (stays on full_name with validation error)
       - If company_name:
           - If semantic general query -> GENERAL_QUESTION
           - Else:
               - If message.lower() in ("company", "business"): UNCERTAIN
               - Else try validate_company_name(message):
                   - PASS -> ENQUIRY_ANSWER
                   - FAIL -> UNCERTAIN (stays on company_name with validation error)
       - If current_technology_stack:
           - If semantic general query -> GENERAL_QUESTION
           - Else try validate_tech_stack(message):
               - PASS -> ENQUIRY_ANSWER
               - FAIL -> UNCERTAIN (stays on current_technology_stack with validation error)
    """
    if not message or not message.strip():
        return UserIntent.UNCERTAIN

    msg = message.strip()

    # 1. Deterministic navigation actions
    if msg in (BUSINESS_SOLUTIONS_ENQUIRY, BUSINESS_ENQUIRY, WEBSITE_GENERAL, ANYTHING_ELSE, ENQUIRE_NOW):
        return UserIntent.NAVIGATION

    # 2. In initial mode
    if session is None or session.mode == ConversationMode.initial.value:
        if is_semantic_general_query(msg, current_step=None):
            return UserIntent.GENERAL_QUESTION
        return UserIntent.UNCERTAIN

    # 3. In general mode
    if session.mode == ConversationMode.general.value:
        return UserIntent.GENERAL_QUESTION

    # 4. In enquiry mode
    if session.mode == ConversationMode.enquiry.value:
        if session.completed:
            return UserIntent.ENQUIRY_ANSWER

        current_step = session.current_step

        # Step Type A: Predefined options step match
        if current_step and current_step in STEP_DEFINITIONS:
            step_def = STEP_DEFINITIONS[current_step]
            if step_def.get("type") == ResponseType.options:
                if msg in step_def.get("options", []):
                    return UserIntent.ENQUIRY_ANSWER
                if is_semantic_general_query(msg, current_step=current_step):
                    return UserIntent.GENERAL_QUESTION
                return UserIntent.UNCERTAIN

        # Step Type B: Work email step
        if current_step == "work_email":
            try:
                validate_work_email(msg)
                return UserIntent.ENQUIRY_ANSWER
            except ValueError:
                if is_semantic_general_query(msg, current_step="work_email"):
                    return UserIntent.GENERAL_QUESTION
                return UserIntent.UNCERTAIN

        # Step Type C: Phone number step
        if current_step == "phone_number":
            try:
                validate_phone_number(msg)
                if not is_semantic_general_query(msg, current_step="phone_number"):
                    return UserIntent.ENQUIRY_ANSWER
            except ValueError:
                pass
            if is_semantic_general_query(msg, current_step="phone_number"):
                return UserIntent.GENERAL_QUESTION
            return UserIntent.UNCERTAIN

        # Step Type D: Full name step
        if current_step == "full_name":
            if is_semantic_general_query(msg, current_step="full_name"):
                return UserIntent.GENERAL_QUESTION
            try:
                validate_full_name(msg)
                return UserIntent.ENQUIRY_ANSWER
            except ValueError:
                return UserIntent.UNCERTAIN

        # Step Type E: Company name step
        if current_step == "company_name":
            if is_semantic_general_query(msg, current_step="company_name"):
                return UserIntent.GENERAL_QUESTION
            if msg.lower() in ("company", "business"):
                return UserIntent.UNCERTAIN
            try:
                validate_company_name(msg)
                return UserIntent.ENQUIRY_ANSWER
            except ValueError:
                return UserIntent.UNCERTAIN

        # Step Type F: Current technology stack step
        if current_step == "current_technology_stack":
            if is_semantic_general_query(msg, current_step="current_technology_stack"):
                return UserIntent.GENERAL_QUESTION
            try:
                validate_tech_stack(msg)
                return UserIntent.ENQUIRY_ANSWER
            except ValueError:
                return UserIntent.UNCERTAIN

        # Fallback if unknown step definition
        if is_semantic_general_query(msg, current_step=current_step):
            return UserIntent.GENERAL_QUESTION
        return UserIntent.UNCERTAIN

    return UserIntent.UNCERTAIN


async def classify_intent_with_llm(message: str, current_step: str | None = None) -> UserIntent | None:
    """Optional zero-shot LLM intent classification when Groq API key is present."""
    if not settings.GROQ_API_KEY:
        return None
    step_question = (
        STEP_DEFINITIONS.get(current_step, {}).get("question", current_step)
        if current_step
        else "general interaction"
    )
    prompt = (
        "Classify the user message into either GENERAL_QUESTION or ENQUIRY_ANSWER.\n"
        f"Context: The assistant asked: '{step_question}'\n"
        f"User message: '{message}'\n"
        "Return ONLY 'GENERAL_QUESTION' if the user is asking about Weboum, company details, services, team, portfolio, or technology.\n"
        "Return ONLY 'ENQUIRY_ANSWER' if the user is providing their own details or answering the question.\n"
        "Answer with strictly the label."
    )
    try:
        client = AsyncGroq(api_key=settings.GROQ_API_KEY)
        completion = await asyncio.wait_for(
            client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=10,
                temperature=0.0,
            ),
            timeout=1.5,
        )
        text = completion.choices[0].message.content.strip().upper()
        if "GENERAL" in text:
            return UserIntent.GENERAL_QUESTION
        if "ANSWER" in text:
            return UserIntent.ENQUIRY_ANSWER
    except Exception as e:
        logger.debug("LLM intent classification skipped or timed out: %s", e)
    return None


def is_general_question_intent(message: str, current_step: str | None = None) -> bool:
    """Backwards-compatible helper delegating to the authoritative semantic classifier."""
    dummy_session = Session(mode=ConversationMode.enquiry.value, current_step=current_step)
    return classify_user_intent(dummy_session, message) == UserIntent.GENERAL_QUESTION


def is_general_question(session_id: str | None, message: str) -> bool:
    """Authoritative check used by API streaming router to decide whether to stream general LLM response."""
    if not session_id or not message or not message.strip():
        return False
    session = _sessions.get(session_id.strip())
    if session is None:
        return False
    intent = classify_user_intent(session, message)
    return intent == UserIntent.GENERAL_QUESTION


async def stream_chat(
    session_id: str | None,
    message: str,
) -> AsyncGenerator[dict[str, Any], None]:
    """Stream LLM response chunk-by-chunk for general website questions."""
    if not session_id or not session_id.strip():
        session_id = uuid.uuid4().hex
    else:
        session_id = session_id.strip()

    lock = await _lock_for(session_id)
    async with lock:
        session = _sessions.get(session_id)
        if session is None:
            logger.info("New session created | session_id=%s", session_id)
            session = Session()
            _sessions[session_id] = session
            logger.info(
                "Routing decision (stream) | session_id=%s prev_mode=None intent=initial step=None new_mode=initial reset=False",
                session_id,
            )
            resp = _initial_response()
            yield {"type": "chunk", "content": resp.message}
            yield {
                "type": "done",
                "session_id": session_id,
                "message": resp.message,
                "suggestions": resp.suggestions,
                "mode": resp.mode.value,
            }
            return

        prev_mode = session.mode
        current_step = session.current_step
        intent = classify_user_intent(session, message)
        logger.info(
            "Routing decision (stream) | session_id=%s prev_mode=%s intent=%s step=%s",
            session_id,
            prev_mode,
            intent.value,
            current_step,
        )

        if not is_general_question(session_id, message):
            resp = await _handle_locked(session_id, message)
            yield {"type": "chunk", "content": resp.message}
            yield {
                "type": "done",
                "session_id": session_id,
                "message": resp.message,
                "suggestions": resp.suggestions,
                "mode": resp.mode.value,
                "step": resp.step,
                "completed": resp.completed,
            }
            return

        if session.mode == ConversationMode.enquiry.value:
            logger.info(
                "Enquiry interrupted by general question (stream) | session_id=%s step=%s -> resetting enquiry state",
                session_id,
                session.current_step,
            )
            reset_enquiry_state(session)
        session.mode = ConversationMode.general.value
        logger.info(
            "Routing state transition (stream) | session_id=%s new_mode=%s reset=True",
            session_id,
            session.mode,
        )
        _require_message(message)
        logger.info("General question started")
        full_answer_parts: list[str] = []
        follow_ups = list(GENERAL_FOLLOW_UP)
        try:
            context = await _build_rag_context(message, session.history)
            async for chunk in stream_general_answer(message, context, session.history):
                if chunk:
                    full_answer_parts.append(chunk)
                    yield {"type": "chunk", "content": chunk}

            full_answer = "".join(full_answer_parts).strip()
            if not full_answer:
                raise RuntimeError("LLM returned empty stream")

            session.history.append({"role": "user", "content": message})
            session.history.append({"role": "assistant", "content": full_answer})
            max_history = settings.MAX_SESSION_HISTORY_MESSAGES
            if max_history > 0 and len(session.history) > max_history:
                session.history = session.history[-max_history:]

            yield {
                "type": "done",
                "session_id": session_id,
                "message": full_answer,
                "suggestions": follow_ups,
                "mode": ConversationMode.general.value,
            }
        except Exception:
            logger.exception("Chat processing failed")
            yield {"type": "chunk", "content": LLM_FALLBACK_MESSAGE}
            yield {
                "type": "done",
                "session_id": session_id,
                "message": LLM_FALLBACK_MESSAGE,
                "suggestions": follow_ups,
                "mode": ConversationMode.general.value,
            }


handle_chat_stream = stream_chat


async def generate_general_answer(
    user_question: str,
    context: str = "",
    history: list[dict[str, str]] | None = None,
) -> str:
    """Answer a website/company question using retrieved RAG context.

    ``context`` is the pre-built retrieved-website-context block (or empty).
    ``history`` is recent conversation turns (roles 'user' and 'assistant').
    Mock this in tests.
    """
    if not settings.GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is not configured")

    if context:
        system_content = f"{GENERAL_SYSTEM_PROMPT}\n\nRETRIEVED WEBSITE CONTEXT:\n{context}"
    else:
        system_content = GENERAL_SYSTEM_PROMPT

    messages: list[dict[str, str]] = [{"role": "system", "content": system_content}]
    if history:
        limit = settings.CHAT_HISTORY_CONTEXT_MESSAGES
        history_slice = history[-limit:] if limit > 0 else []
        for turn in history_slice:
            role = turn.get("role")
            content = turn.get("content")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": f"USER QUESTION:\n{user_question}"})

    logger.info("LLM request started | model=%s", settings.LLM_MODEL)
    start_time = time.perf_counter()
    try:
        client = AsyncGroq(api_key=settings.GROQ_API_KEY)
        completion = await client.chat.completions.create(
            model=settings.LLM_MODEL,
            temperature=0.2,
            max_tokens=700,
            messages=messages,
        )
        content = completion.choices[0].message.content
        if not content or not content.strip():
            raise RuntimeError("LLM returned an empty answer")
        duration_ms = (time.perf_counter() - start_time) * 1000
        logger.info("LLM stream completed | duration_ms=%.2f", duration_ms)
        return content.strip()
    except Exception:
        logger.exception("LLM request failed")
        raise


async def stream_general_answer(
    user_question: str,
    context: str = "",
    history: list[dict[str, str]] | None = None,
) -> AsyncGenerator[str, None]:
    """Stream token chunks for a website/company question using retrieved RAG context."""
    if not settings.GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is not configured")

    if context:
        system_content = f"{GENERAL_SYSTEM_PROMPT}\n\nRETRIEVED WEBSITE CONTEXT:\n{context}"
    else:
        system_content = GENERAL_SYSTEM_PROMPT

    messages: list[dict[str, str]] = [{"role": "system", "content": system_content}]
    if history:
        limit = settings.CHAT_HISTORY_CONTEXT_MESSAGES
        history_slice = history[-limit:] if limit > 0 else []
        for turn in history_slice:
            role = turn.get("role")
            content = turn.get("content")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": f"USER QUESTION:\n{user_question}"})

    logger.info("LLM request started | model=%s", settings.LLM_MODEL)
    start_time = time.perf_counter()
    first_chunk_received = False
    try:
        client = AsyncGroq(api_key=settings.GROQ_API_KEY)
        stream = await client.chat.completions.create(
            model=settings.LLM_MODEL,
            temperature=0.2,
            max_tokens=700,
            messages=messages,
            stream=True,
        )
        logger.info("LLM stream started")
        async for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            if delta:
                if not first_chunk_received:
                    logger.info("LLM first chunk received")
                    first_chunk_received = True
                yield delta
        duration_ms = (time.perf_counter() - start_time) * 1000
        logger.info("LLM stream completed | duration_ms=%.2f", duration_ms)
    except Exception:
        logger.exception("LLM request failed")
        raise


async def _lock_for(session_id: str) -> asyncio.Lock:
    async with _registry_lock:
        if session_id not in _session_locks:
            _session_locks[session_id] = asyncio.Lock()
        return _session_locks[session_id]


async def _handle_locked(session_id: str, message: str) -> ChatResponse:
    session = _sessions.get(session_id)
    if session is None:
        logger.info("New session created | session_id=%s", session_id)
        session = Session()
        _sessions[session_id] = session
        logger.info(
            "Routing decision | session_id=%s prev_mode=None intent=initial step=None new_mode=initial reset=False",
            session_id,
        )
        return _initial_response()

    prev_mode = session.mode
    current_step = session.current_step
    intent = classify_user_intent(session, message)
    logger.info(
        "Routing decision | session_id=%s prev_mode=%s intent=%s step=%s",
        session_id,
        prev_mode,
        intent.value,
        current_step,
    )

    if session.mode == ConversationMode.initial.value:
        return await _handle_initial(session, message)

    if session.mode == ConversationMode.enquiry.value:
        return await _handle_enquiry(session_id, session, message)

    if session.mode == ConversationMode.general.value:
        return await _handle_general(session, message)

    session.mode = ConversationMode.initial.value
    session.current_step = None
    session.completed = False
    return ChatResponse(
        message=f"{CHOOSE_INITIAL_OPTION}\n\n{INITIAL_MESSAGE}",
        type=ResponseType.options,
        suggestions=list(INITIAL_SUGGESTIONS),
        mode=ConversationMode.initial,
        step=None,
        completed=False,
    )


def _initial_response() -> ChatResponse:
    return ChatResponse(
        message=INITIAL_MESSAGE,
        type=ResponseType.options,
        suggestions=list(INITIAL_SUGGESTIONS),
        mode=ConversationMode.initial,
        step=None,
        completed=False,
    )


async def _handle_initial(session: Session, message: str) -> ChatResponse:
    if message in (BUSINESS_SOLUTIONS_ENQUIRY, BUSINESS_ENQUIRY):
        return start_enquiry(session)

    if message == WEBSITE_GENERAL:
        session.mode = ConversationMode.general.value
        session.current_step = None
        session.completed = False
        return ChatResponse(
            message=GENERAL_INTRO_MESSAGE,
            type=ResponseType.text,
            suggestions=[],
            mode=ConversationMode.general,
            step=None,
            completed=False,
        )

    intent = classify_user_intent(session, message)
    if intent == UserIntent.GENERAL_QUESTION:
        session.mode = ConversationMode.general.value
        return await _handle_general(session, message)

    return ChatResponse(
        message=f"{CHOOSE_INITIAL_OPTION}\n\n{INITIAL_MESSAGE}",
        type=ResponseType.options,
        suggestions=list(INITIAL_SUGGESTIONS),
        mode=ConversationMode.initial,
        step=None,
        completed=False,
    )


async def _handle_enquiry(session_id: str, session: Session, message: str) -> ChatResponse:
    _require_message(message)
    if session.completed and message == ANYTHING_ELSE:
        return _switch_to_general(session)
    if session.completed:
        if not session.email_sent:
            return _email_failure_response()
        return process_enquiry_answer(session, message)

    # User explicitly clicks Business Solutions Enquiry (or Business Enquiry)
    if message in (BUSINESS_SOLUTIONS_ENQUIRY, BUSINESS_ENQUIRY):
        return start_enquiry(session)

    # User explicitly switches to General Question mode during active enquiry
    if message == WEBSITE_GENERAL:
        reset_enquiry_state(session)
        session.mode = ConversationMode.general.value
        return ChatResponse(
            message=GENERAL_INTRO_MESSAGE,
            type=ResponseType.text,
            suggestions=[],
            mode=ConversationMode.general,
            step=None,
            completed=False,
        )

    # Check if message is a general question before enquiry validation
    intent = classify_user_intent(session, message)
    if intent == UserIntent.GENERAL_QUESTION:
        logger.info(
            "Enquiry interrupted by general question | session_id=%s step=%s -> resetting enquiry state",
            session_id,
            session.current_step,
        )
        reset_enquiry_state(session)
        return await _answer_general_during_enquiry(session, message)

    # Optional LLM disambiguation check on free-text steps when Groq is configured
    if settings.GROQ_API_KEY and session.current_step in ("company_name", "current_technology_stack"):
        llm_intent = await classify_intent_with_llm(message, session.current_step)
        if llm_intent == UserIntent.GENERAL_QUESTION:
            logger.info(
                "Enquiry interrupted by general question (LLM classified) | session_id=%s step=%s -> resetting enquiry state",
                session_id,
                session.current_step,
            )
            reset_enquiry_state(session)
            return await _answer_general_during_enquiry(session, message)

    already_complete = session.completed
    response = process_enquiry_answer(session, message)
    if response.completed and not already_complete:
        enquiry = build_enquiry_object(session_id, session)
        _completed_enquiries.append(enquiry)
        try:
            await send_enquiry_email(map_enquiry_data(enquiry))
            session.email_sent = True
        except EmailSendError:
            logger.exception("Brevo email send failed")
            session.email_sent = False
            return _email_failure_response()
    return response


async def _answer_general_during_enquiry(session: Session, message: str) -> ChatResponse:
    logger.info("General question received during enquiry | resetting enquiry state")
    reset_enquiry_state(session)
    session.mode = ConversationMode.general.value
    try:
        context = await _build_rag_context(message, session.history)
        answer = await generate_general_answer(message, context, session.history)
        session.history.append({"role": "user", "content": message})
        session.history.append({"role": "assistant", "content": answer})
        max_history = settings.MAX_SESSION_HISTORY_MESSAGES
        if max_history > 0 and len(session.history) > max_history:
            session.history = session.history[-max_history:]
    except Exception:
        logger.exception("Chat processing failed during enquiry interruption")
        return ChatResponse(
            message=LLM_FALLBACK_MESSAGE,
            type=ResponseType.text,
            suggestions=list(GENERAL_FOLLOW_UP),
            mode=ConversationMode.general,
            step=None,
            completed=False,
        )

    return ChatResponse(
        message=answer,
        type=ResponseType.text,
        suggestions=list(GENERAL_FOLLOW_UP),
        mode=ConversationMode.general,
        step=None,
        completed=False,
    )


def _email_failure_response() -> ChatResponse:
    return ChatResponse(
        message=EMAIL_FAILURE_MESSAGE,
        type=ResponseType.text,
        suggestions=["Anything Else?"],
        mode=ConversationMode.enquiry,
        step=None,
        completed=True,
    )


def _switch_to_general(session: Session) -> ChatResponse:
    reset_enquiry_state(session)
    session.mode = ConversationMode.general.value
    return ChatResponse(
        message=ANYTHING_ELSE_MESSAGE,
        type=ResponseType.text,
        suggestions=[],
        mode=ConversationMode.general,
        step=None,
        completed=False,
    )


async def _handle_general(session: Session, message: str) -> ChatResponse:
    _require_message(message)

    if message in (BUSINESS_SOLUTIONS_ENQUIRY, ENQUIRE_NOW):
        return start_enquiry(session)

    if message == ANYTHING_ELSE:
        return ChatResponse(
            message=ANYTHING_ELSE_MESSAGE,
            type=ResponseType.text,
            suggestions=[],
            mode=ConversationMode.general,
            step=None,
            completed=False,
        )

    if message == WEBSITE_GENERAL:
        return ChatResponse(
            message=GENERAL_INTRO_MESSAGE,
            type=ResponseType.text,
            suggestions=[],
            mode=ConversationMode.general,
            step=None,
            completed=False,
        )

    logger.info("General question started")
    try:
        context = await _build_rag_context(message, session.history)
        answer = await generate_general_answer(message, context, session.history)
        session.history.append({"role": "user", "content": message})
        session.history.append({"role": "assistant", "content": answer})
        max_history = settings.MAX_SESSION_HISTORY_MESSAGES
        if max_history > 0 and len(session.history) > max_history:
            session.history = session.history[-max_history:]
    except Exception:
        logger.exception("Chat processing failed")
        return ChatResponse(
            message=LLM_FALLBACK_MESSAGE,
            type=ResponseType.text,
            suggestions=list(GENERAL_FOLLOW_UP),
            mode=ConversationMode.general,
            step=None,
            completed=False,
        )

    return ChatResponse(
        message=answer,
        type=ResponseType.text,
        suggestions=list(GENERAL_FOLLOW_UP),
        mode=ConversationMode.general,
        step=None,
        completed=False,
    )


def _resolve_retrieval_query(
    question: str,
    history: list[dict[str, str]] | None = None,
) -> str:
    q = question.strip()
    words = q.split()
    lower_q = q.lower()

    contact_terms = {
        "address", "phone", "telephone", "mobile", "email", "mail",
        "office", "location", "headquarters", "hq", "hours", "timings",
        "contact", "reach", "call",
    }

    is_short = len(words) <= 3
    has_contact = any(term in lower_q for term in contact_terms)
    has_company_name = "weboum" in lower_q

    if not has_company_name:
        prev_user_text = ""
        if history:
            for turn in reversed(history):
                if turn.get("role") == "user":
                    prev_user_text = turn.get("content", "").strip()
                    break

        if is_short and prev_user_text and not has_contact:
            return f"Weboum Technology {prev_user_text} {q}"
        elif has_contact:
            return f"Weboum Technology {q}"
        else:
            return f"Weboum Technology {q}"

    return q


async def _build_rag_context(
    question: str,
    history: list[dict[str, str]] | None = None,
) -> str:
    """Retrieve and format RAG context for a general question.

    Resolves follow-ups and short queries (e.g. 'address', 'phone', 'pricing')
    by contextualizing the retrieval query with recent conversation topic.
    """
    logger.info("RAG started")
    rag_start = time.perf_counter()
    query_for_retrieval = _resolve_retrieval_query(question, history)
    try:
        chunks = await asyncio.to_thread(rag.retrieve, query_for_retrieval)
        duration_ms = (time.perf_counter() - rag_start) * 1000
        logger.info("RAG completed | duration_ms=%.2f", duration_ms)
    except Exception:
        logger.exception("RAG failed")
        return ""
    return rag.format_context(chunks)


def _require_message(message: str) -> None:
    if not message:
        raise HTTPException(status_code=400, detail=EMPTY_MESSAGE_DETAIL)
