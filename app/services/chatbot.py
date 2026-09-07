"""Main conversation controller: sessions, routing, and unified responses."""

from __future__ import annotations

import asyncio
import logging

from fastapi import HTTPException
from groq import AsyncGroq

from app.core.config import settings
from app.prompts.general import COMPANY_KNOWLEDGE, GENERAL_SYSTEM_PROMPT
from app.schemas.chat import ChatResponse, ConversationMode, ResponseType
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


async def handle_chat(session_id: str, message: str) -> ChatResponse:
    lock = await _lock_for(session_id)
    async with lock:
        return await _handle_locked(session_id, message)


async def generate_general_answer(user_question: str) -> str:
    """Answer a website/company question from centralized knowledge. Mock this in tests."""
    if not settings.GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is not configured")

    client = AsyncGroq(api_key=settings.GROQ_API_KEY)
    completion = await client.chat.completions.create(
        model=settings.LLM_MODEL,
        temperature=0.2,
        max_tokens=700,
        messages=[
            {
                "role": "system",
                "content": (
                    f"{GENERAL_SYSTEM_PROMPT}\n\n"
                    f"WEBSITE / COMPANY KNOWLEDGE:\n{COMPANY_KNOWLEDGE}"
                ),
            },
            {"role": "user", "content": user_question},
        ],
    )
    content = completion.choices[0].message.content
    if not content or not content.strip():
        raise RuntimeError("LLM returned an empty answer")
    return content.strip()


async def _lock_for(session_id: str) -> asyncio.Lock:
    async with _registry_lock:
        if session_id not in _session_locks:
            _session_locks[session_id] = asyncio.Lock()
        return _session_locks[session_id]


async def _handle_locked(session_id: str, message: str) -> ChatResponse:
    session = _sessions.get(session_id)
    if session is None:
        _sessions[session_id] = Session()
        return _initial_response()

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

    response = process_enquiry_answer(session, message)
    if response.completed:
        enquiry = build_enquiry_object(session_id, session)
        _completed_enquiries.append(enquiry)
        try:
            await send_enquiry_email(map_enquiry_data(enquiry))
            session.email_sent = True
        except EmailSendError:
            logger.exception("Enquiry notification email failed")
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

    try:
        answer = await generate_general_answer(message)
    except Exception:
        logger.exception("General LLM call failed")
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


def _require_message(message: str) -> None:
    if not message:
        raise HTTPException(status_code=400, detail=EMPTY_MESSAGE_DETAIL)
