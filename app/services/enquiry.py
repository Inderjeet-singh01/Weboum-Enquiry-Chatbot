"""Deterministic Business Enquiry state machine.

The LLM must not choose the next step. Python owns the exact sequence.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.schemas.chat import (
    ChatResponse,
    ConversationMode,
    EnquiryData,
    ResponseType,
    validate_company_name,
    validate_full_name,
    validate_tech_stack,
    validate_work_email,
)

logger = logging.getLogger(__name__)

ENQUIRY_FIELD_ORDER = [
    "biggest_operational_challenge",
    "ai_capability",
    "primary_industry",
    "business_size",
    "full_name",
    "company_name",
    "work_email",
    "phone_number",
    "current_technology_stack",
]

STEP_DEFINITIONS: dict[str, dict[str, Any]] = {
    "biggest_operational_challenge": {
        "question": "What is your biggest operational challenge right now?",
        "supporting": "Where is your team spending the most unnecessary time or money?",
        "type": ResponseType.options,
        "options": [
            "High customer support call/chat volume & slow response time",
            "Repetitive manual data entry & paper/PDF document processing",
            "Difficulty qualifying and following up with sales leads quickly",
            "Lack of real-time operational data dashboards & insights",
            "Custom software systems that are outdated or don't talk to each other",
        ],
    },
    "ai_capability": {
        "question": "Which AI capabilities are you most interested in exploring?",
        "supporting": "Choose your main interest area.",
        "type": ResponseType.options,
        "options": [
            "AI Agents & Workflow Automation",
            "AI Chatbots & Voice AI Assistants",
            "Custom Software / ERP / CRM",
            "AI Analytics & Data Warehousing",
            "Generative AI & LLM Integration",
        ],
    },
    "primary_industry": {
        "question": "What is your primary industry?",
        "supporting": "This helps us tailor AI opportunities specific to your sector.",
        "type": ResponseType.options,
        "options": [
            "Healthcare",
            "Hospitality & Restaurants",
            "Real Estate",
            "Manufacturing",
            "Retail & E-Commerce",
            "Logistics & Transport",
            "Services & Other",
        ],
    },
    "business_size": {
        "question": "What size is your business?",
        "supporting": "Select your total team headcount.",
        "type": ResponseType.options,
        "options": [
            "1 - 10 employees",
            "11 - 50 employees",
            "51 - 200 employees",
            "200+ enterprise employees",
        ],
    },
    "full_name": {
        "question": "What is your Full Name?",
        "supporting": None,
        "type": ResponseType.text,
        "options": [],
    },
    "company_name": {
        "question": "What is your Company Name?",
        "supporting": None,
        "type": ResponseType.text,
        "options": [],
    },
    "work_email": {
        "question": "What is your Work Email?",
        "supporting": None,
        "type": ResponseType.email,
        "options": [],
    },
    "phone_number": {
        "question": "What is your Phone Number?",
        "supporting": None,
        "type": ResponseType.phone,
        "options": [],
    },
    "current_technology_stack": {
        "question": "What is your Current Technology Stack?",
        "supporting": "e.g. WhatsApp, Salesforce, Excel, Custom POS",
        "type": ResponseType.text,
        "options": [],
    },
}

FIRST_STEP = ENQUIRY_FIELD_ORDER[0]
COMPLETION_MESSAGE = (
    "Thank you! Your enquiry has been submitted successfully. "
    "Our team will review your requirements and get back to you."
)
POST_SUBMIT_SUGGESTIONS = ["Anything Else?"]


def empty_enquiry_data() -> dict[str, str | None]:
    return {key: None for key in ENQUIRY_FIELD_ORDER}


MAPPED_FIELD_LABELS = [
    ("biggest_operational_challenge", "Biggest Operational Challenge"),
    ("ai_capability", "AI Capability / Area of Interest"),
    ("primary_industry", "Primary Industry"),
    ("business_size", "Business Size"),
    ("full_name", "Full Name"),
    ("company_name", "Company Name"),
    ("work_email", "Work Email"),
    ("phone_number", "Phone Number"),
    ("current_technology_stack", "Current Technology Stack"),
]


@dataclass
class Session:
    mode: str = ConversationMode.initial.value
    current_step: str | None = None
    data: dict[str, str | None] = field(default_factory=empty_enquiry_data)
    completed: bool = False
    email_sent: bool = False
    history: list[dict[str, str]] = field(default_factory=list)


def start_enquiry(session: Session) -> ChatResponse:
    logger.info("Enquiry flow started")
    session.mode = ConversationMode.enquiry.value
    session.completed = False
    session.email_sent = False
    session.data = empty_enquiry_data()
    session.current_step = FIRST_STEP
    return step_response(FIRST_STEP)


def completion_response() -> ChatResponse:
    return ChatResponse(
        message=COMPLETION_MESSAGE,
        type=ResponseType.text,
        suggestions=list(POST_SUBMIT_SUGGESTIONS),
        mode=ConversationMode.enquiry,
        step=None,
        completed=True,
    )


def process_enquiry_answer(session: Session, message: str) -> ChatResponse:
    if session.completed:
        return completion_response()

    step_key = session.current_step
    if step_key not in STEP_DEFINITIONS:
        session.current_step = FIRST_STEP
        return step_response(
            FIRST_STEP,
            prefix="Let's restart the enquiry from the first question.",
        )

    error = _validate_answer(step_key, message)
    if error:
        logger.warning("Validation failure | field=%s", step_key)
        return step_response(step_key, prefix=error)

    session.data[step_key] = message
    logger.info("Enquiry step processed | step=%s", step_key)
    next_step = _next_step(step_key)
    if next_step is None:
        session.completed = True
        session.current_step = None
        logger.info("Enquiry completed")
        return completion_response()

    session.current_step = next_step
    return step_response(next_step)


def build_enquiry_object(session_id: str, session: Session) -> dict[str, str | None]:
    try:
        validated = EnquiryData(**session.data).model_dump()
    except (ValueError, ValidationError):
        validated = dict(session.data)
    return {"session_id": session_id, **validated}


def map_enquiry_data(enquiry: dict[str, str | None]) -> dict[str, list[dict[str, str]]]:
    session_id = str(enquiry.get("session_id") or "")
    mapped_data = []
    for key, label in MAPPED_FIELD_LABELS:
        value = enquiry.get(key)
        mapped_data.append(
            {
                "session_id": session_id,
                "field": label,
                "value": "" if value is None else str(value),
            }
        )
    return {"mapped_data": mapped_data}


def step_response(step_key: str, prefix: str | None = None) -> ChatResponse:
    definition = STEP_DEFINITIONS[step_key]
    message = _compose_message(definition)
    if prefix:
        message = f"{prefix}\n\n{message}"
    return ChatResponse(
        message=message,
        type=definition["type"],
        suggestions=list(definition["options"]),
        mode=ConversationMode.enquiry,
        step=step_key,
        completed=False,
    )


def _compose_message(definition: dict[str, Any]) -> str:
    question = definition["question"]
    supporting = definition.get("supporting")
    if supporting:
        return f"{question}\n\n{supporting}"
    return question


def _next_step(current: str) -> str | None:
    index = ENQUIRY_FIELD_ORDER.index(current)
    if index >= len(ENQUIRY_FIELD_ORDER) - 1:
        return None
    return ENQUIRY_FIELD_ORDER[index + 1]


def _validate_answer(step_key: str, message: str) -> str | None:
    message = message.strip() if message else ""
    if not message:
        return "Please provide an answer to continue."

    definition = STEP_DEFINITIONS.get(step_key)
    if not definition:
        return "Invalid enquiry step."

    response_type: ResponseType = definition["type"]
    options: list[str] = definition["options"]

    if response_type == ResponseType.options:
        if message not in options:
            return "Please choose one of the listed options."
        return None

    try:
        if step_key == "work_email":
            validate_work_email(message)
        elif step_key == "full_name":
            validate_full_name(message)
        elif step_key == "company_name":
            validate_company_name(message)
        elif step_key == "current_technology_stack":
            validate_tech_stack(message)
    except (ValueError, ValidationError) as exc:
        return str(exc)

    return None
