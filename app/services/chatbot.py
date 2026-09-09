"""Main conversation controller: sessions, routing, and unified responses."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
import time
from typing import Any
import uuid

from fastapi import HTTPException
from groq import AsyncGroq

from app.core.config import settings
from app.prompts.general import GENERAL_SYSTEM_PROMPT
from app.schemas.chat import ChatResponse, ConversationMode, ResponseType
from app.services import rag
from app.services.email import EMAIL_FAILURE_MESSAGE, EmailSendError, send_enquiry_email
from app.services.enquiry import (
    Session,
    build_enquiry_object,
    map_enquiry_data,
    process_enquiry_answer,
    start_enquiry,
)

logger = logging.getLogger(__name__)

BUSINESS_ENQUIRY = "Business Enquiry"
WEBSITE_GENERAL = "Website / General Question"
ANYTHING_ELSE = "Anything Else?"
ENQUIRE_NOW = "Enquire Now"

INITIAL_MESSAGE = "Hi! How can I help you today?"
INITIAL_SUGGESTIONS = [BUSINESS_ENQUIRY, WEBSITE_GENERAL]
GENERAL_FOLLOW_UP = [ANYTHING_ELSE, ENQUIRE_NOW]

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


def is_general_question(session_id: str | None, message: str) -> bool:
    """Return True if the message will trigger a Groq LLM completion."""
    if not session_id or not message or not message.strip():
        return False
    session = _sessions.get(session_id.strip())
    if session is None:
        return False
    if session.mode != ConversationMode.general.value:
        return False
    if message.strip() in (ANYTHING_ELSE, ENQUIRE_NOW):
        return False
    return True


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
            logger.info("New session created")
            session = Session()
            _sessions[session_id] = session
            logger.info("Chat routed | mode=%s", ConversationMode.initial.value)
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

        if session.mode != ConversationMode.general.value or message.strip() in (ANYTHING_ELSE, ENQUIRE_NOW):
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

        logger.info("Chat routed | mode=%s", session.mode)
        _require_message(message)
        logger.info("General question started")
        full_answer_parts: list[str] = []
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
                "suggestions": list(GENERAL_FOLLOW_UP),
                "mode": ConversationMode.general.value,
            }
        except Exception:
            logger.exception("Chat processing failed")
            yield {"type": "chunk", "content": LLM_FALLBACK_MESSAGE}
            yield {
                "type": "done",
                "session_id": session_id,
                "message": LLM_FALLBACK_MESSAGE,
                "suggestions": list(GENERAL_FOLLOW_UP),
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
        logger.info("New session created")
        session = Session()
        _sessions[session_id] = session
        logger.info("Chat routed | mode=%s", ConversationMode.initial.value)
        return _initial_response()

    logger.info("Chat routed | mode=%s", session.mode)
    if session.mode == ConversationMode.initial.value:
        return _handle_initial(session, message)

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


def _handle_initial(session: Session, message: str) -> ChatResponse:
    if message == BUSINESS_ENQUIRY:
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
    session.mode = ConversationMode.general.value
    session.current_step = None
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

    if message == ANYTHING_ELSE:
        return ChatResponse(
            message=ANYTHING_ELSE_MESSAGE,
            type=ResponseType.text,
            suggestions=[],
            mode=ConversationMode.general,
            step=None,
            completed=False,
        )

    if message == ENQUIRE_NOW:
        return start_enquiry(session)

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
