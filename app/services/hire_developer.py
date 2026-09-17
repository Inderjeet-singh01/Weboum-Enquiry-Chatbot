"""Deterministic Hire a Developer state machine.

Implements the multi-step developer hiring enquiry flow:
1. Skills (multi-select)
2. Technology (multi-select)
3. Work Time (single-choice)
4. Timeframe (single-choice)
5. Start (single-choice)
6. Contact Information (validated form)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import ValidationError

from app.schemas.chat import (
    ChatResponse,
    ConversationMode,
    HireDeveloperData,
    ResponseType,
    validate_full_name,
    validate_hire_dev_comment,
    validate_hire_dev_email,
    validate_hire_dev_phone,
    validate_website_url,
)
from app.services.email import (
    EMAIL_FAILURE_MESSAGE,
    EmailSendError,
    send_hire_developer_email,
)

logger = logging.getLogger(__name__)

HIRE_DEV_STEPS = [
    "skills",
    "technology",
    "work_time",
    "timeframe",
    "start",
    "contact_information",
]

FIRST_STEP = HIRE_DEV_STEPS[0]

SKILLS_OPTIONS = [
    "Python",
    "React",
    "Angular",
    "PHP",
    "Node",
    "HTML",
    "Javascript",
    "SEO",
]

TECHNOLOGY_OPTIONS = [
    "AI & Automation",
    "Creative UI/UX Designing",
    "E-Commerce Solutions",
    "App Development",
    "Web Application",
    "Digital Marketing",
    "Technical Support",
    "Business Intelligence Software",
    "Custom Software Technology",
]

WORK_TIME_OPTIONS = [
    "Full-Time",
    "Part-Time",
]

TIMEFRAME_OPTIONS = [
    "Less than 1 month",
    "1 to 3 months",
    "3 to 6 months",
    "More than 6 months",
    "I am not sure",
]

START_OPTIONS = [
    "In a couple of days",
    "In a week",
    "In a couple of weeks",
    "In a month",
    "More than a month",
    "I am not sure",
]

STEP_CONFIG: dict[str, dict[str, Any]] = {
    "skills": {
        "title": "Skills",
        "supporting": "Select one or more skills required for your project.",
        "type": ResponseType.multi_options,
        "options": SKILLS_OPTIONS,
        "is_multi": True,
    },
    "technology": {
        "title": "Technology",
        "supporting": "Select one or more technology domains you need.",
        "type": ResponseType.multi_options,
        "options": TECHNOLOGY_OPTIONS,
        "is_multi": True,
    },
    "work_time": {
        "title": "Work Time",
        "supporting": "Select one or both engagement models.",
        "type": ResponseType.multi_options,
        "options": WORK_TIME_OPTIONS,
        "is_multi": True,
    },
    "timeframe": {
        "title": "Timeframe",
        "supporting": "Select the estimated duration for this engagement.",
        "type": ResponseType.options,
        "options": TIMEFRAME_OPTIONS,
        "is_multi": False,
    },
    "start": {
        "title": "Start",
        "supporting": "Select when you would like the developer to start.",
        "type": ResponseType.options,
        "options": START_OPTIONS,
        "is_multi": False,
    },
    "contact_information": {
        "title": "Contact Information",
        "supporting": "Please provide your contact details and requirements below.",
        "type": ResponseType.contact_form,
        "options": [],
        "is_multi": False,
    },
}

COMPLETION_MESSAGE = (
    "Thank you! Your Hire a Developer request has been submitted successfully. "
    "Our team will review your requirements and get back to you shortly.\n\n"
    "Anything else?"
)


def empty_hire_developer_data() -> dict[str, Any]:
    return {
        "skills": [],
        "technology": [],
        "work_time": [],
        "timeframe": None,
        "start": None,
        "name": None,
        "email": None,
        "phone": None,
        "website_url": None,
        "comment": None,
    }


def reset_hire_developer_state(session: Any) -> None:
    """Clear all hire-a-developer state on session."""
    session.hire_dev_data = empty_hire_developer_data()
    if session.mode == ConversationMode.hire_developer.value:
        session.current_step = None
        session.completed = False
        session.email_sent = False


def start_hire_developer(session: Any) -> ChatResponse:
    """Start a fresh Hire a Developer flow at Step 1."""
    logger.info("Hire a Developer flow started")
    session.mode = ConversationMode.hire_developer.value
    session.completed = False
    session.email_sent = False
    session.hire_dev_data = empty_hire_developer_data()
    session.current_step = FIRST_STEP
    return step_response(session, FIRST_STEP)


def completion_response(top_level_options: list[str] | None = None) -> ChatResponse:
    return ChatResponse(
        message=COMPLETION_MESSAGE,
        type=ResponseType.text,
        suggestions=["Anything else?"],
        mode=ConversationMode.hire_developer,
        step=None,
        completed=True,
    )


def step_response(
    session: Any,
    step_key: str,
    prefix: str | None = None,
) -> ChatResponse:
    config = STEP_CONFIG[step_key]
    title = config["title"]
    supporting = config["supporting"]

    data = getattr(session, "hire_dev_data", {}) or {}
    selected_val = data.get(step_key)

    nl = chr(10)
    # Compose suggestions and selected list
    if config["is_multi"]:
        selected_list = list(selected_val) if isinstance(selected_val, list) else []
        options = list(config["options"])
        suggestions = options + (["Continue", "Back"] if step_key != FIRST_STEP else ["Continue"])
        msg = f"{title}{nl}{nl}{supporting}"
        if selected_list:
            msg += f"{nl}{nl}Currently selected: {', '.join(selected_list)}"
    elif step_key == "contact_information":
        selected_list = None
        suggestions = ["Submit", "Back"]
        msg = f"{title}{nl}{nl}{supporting}"
    else:
        selected_list = [str(selected_val)] if selected_val else []
        options = list(config["options"])
        suggestions = options + ["Continue", "Back"]
        msg = f"{title}{nl}{nl}{supporting}"
        if selected_val:
            msg += f"{nl}{nl}Currently selected: {selected_val}"

    if prefix:
        msg = f"{prefix}{nl}{nl}{msg}"

    return ChatResponse(
        message=msg,
        type=config["type"],
        suggestions=suggestions,
        mode=ConversationMode.hire_developer,
        step=step_key,
        completed=False,
        selected=selected_list,
        form_data=dict(data) if step_key == "contact_information" else None,
    )


def _extract_step_payload(step_key: str, payload_data: dict[str, Any] | None) -> Any:
    if not payload_data or not isinstance(payload_data, dict):
        return None
    if step_key in payload_data:
        return payload_data[step_key]
    nested = payload_data.get("data")
    if isinstance(nested, dict) and step_key in nested:
        return nested[step_key]
    return None


def parse_contact_inputs(message: str, payload_data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Extract contact information fields from payload_data, JSON message, or key-value text."""
    result: dict[str, Any] = {}
    if payload_data and isinstance(payload_data, dict):
        nested = payload_data.get("data")
        if isinstance(nested, dict):
            result.update(nested)
        result.update({k: v for k, v in payload_data.items() if k != "data"})

    msg = (message or "").strip()
    if msg.startswith("{") and msg.endswith("}"):
        try:
            parsed = json.loads(msg)
            if isinstance(parsed, dict):
                result.update(parsed)
                return result
        except Exception:
            pass

    # Try line-by-line key: value parsing
    lines = msg.splitlines()
    for line in lines:
        if ":" in line:
            k, v = line.split(":", 1)
            k_clean = k.strip().lower().replace(" ", "_").replace("*", "")
            v_clean = v.strip()
            if k_clean in ("name", "full_name"):
                result["name"] = v_clean
            elif k_clean in ("email", "work_email"):
                result["email"] = v_clean
            elif k_clean in ("phone", "phone_number"):
                result["phone"] = v_clean
            elif k_clean in ("website", "website_url", "url"):
                result["website_url"] = v_clean
            elif k_clean in ("comment", "comments", "requirements"):
                result["comment"] = v_clean

    return result


async def process_hire_developer_step(
    session: Any,
    session_id: str,
    message: str,
    payload_data: dict[str, Any] | None = None,
    top_level_options: list[str] | None = None,
) -> ChatResponse:
    """Process incoming input according to the deterministic Hire a Developer state machine."""
    top_options = top_level_options or ["Website / General Question", "Business Solutions Enquiry", "Hire a Developer"]

    if session.completed:
        return completion_response(top_options)

    step_key = session.current_step
    if not step_key or step_key not in STEP_CONFIG:
        session.current_step = FIRST_STEP
        return step_response(session, FIRST_STEP, prefix="Starting Hire a Developer from Step 1.")

    msg = (message or "").strip()
    lower_msg = msg.lower()

    action = None
    if payload_data and isinstance(payload_data, dict) and "action" in payload_data:
        action = str(payload_data["action"]).strip().lower()

    # 1. Back navigation
    is_back = (
        action == "back"
        or lower_msg in ("back", "← back")
    )
    if is_back:
        step_idx = HIRE_DEV_STEPS.index(step_key)
        if step_idx > 0:
            prev_step = HIRE_DEV_STEPS[step_idx - 1]
            session.current_step = prev_step
            return step_response(session, prev_step)
        return step_response(session, step_key)

    # 2. Multi-select steps: Skills, Technology, Work Time
    if step_key in ("skills", "technology", "work_time"):
        allowed_options = STEP_CONFIG[step_key]["options"]
        current_selection = session.hire_dev_data.get(step_key) or []
        if not isinstance(current_selection, list):
            current_selection = [current_selection] if current_selection else []

        raw_val = _extract_step_payload(step_key, payload_data)
        new_items: list[str] = []
        if raw_val is not None:
            new_items = raw_val if isinstance(raw_val, list) else [str(raw_val)]
        elif msg.startswith("[") and msg.endswith("]"):
            try:
                parsed = json.loads(msg)
                if isinstance(parsed, list):
                    new_items = [str(x).strip() for x in parsed]
            except Exception:
                pass
        elif "," in msg:
            new_items = [x.strip() for x in msg.split(",")]
        elif lower_msg not in ("continue", "continue →", "back", "← back", "submit", "submit request"):
            for opt in allowed_options:
                if msg.lower() == opt.lower():
                    new_items = [opt]
                    break

        if raw_val is not None or (msg.startswith("[") and msg.endswith("]")) or ("," in msg):
            valid_items = [opt for item in new_items for opt in allowed_options if str(item).lower() == opt.lower()]
            session.hire_dev_data[step_key] = list(dict.fromkeys(valid_items))
            current_selection = session.hire_dev_data[step_key]
        elif new_items:
            item = next((opt for opt in allowed_options if opt.lower() == new_items[0].lower()), None)
            if item:
                if item in current_selection:
                    current_selection.remove(item)
                else:
                    current_selection.append(item)
                session.hire_dev_data[step_key] = current_selection

        is_continue = (
            action == "continue"
            or lower_msg in ("continue", "continue →")
        )

        if is_continue:
            if not current_selection:
                return step_response(session, step_key, prefix="Please select at least one option.")
            next_idx = HIRE_DEV_STEPS.index(step_key) + 1
            next_step = HIRE_DEV_STEPS[next_idx]
            session.current_step = next_step
            return step_response(session, next_step)

        if new_items:
            return step_response(session, step_key)

        return step_response(session, step_key, prefix="Please select at least one option.")

    # 3. Single-choice steps: Timeframe, Start
    if step_key in ("timeframe", "start"):
        allowed_options = STEP_CONFIG[step_key]["options"]
        raw_val = _extract_step_payload(step_key, payload_data)

        matched_opt = None
        if raw_val is not None:
            val = str(raw_val).strip()
            for opt in allowed_options:
                if val.lower() == opt.lower():
                    matched_opt = opt
                    break
        elif lower_msg not in ("continue", "continue →", "back", "← back", "submit", "submit request"):
            for opt in allowed_options:
                if msg.lower() == opt.lower():
                    matched_opt = opt
                    break

        if matched_opt:
            session.hire_dev_data[step_key] = matched_opt

        current_val = session.hire_dev_data.get(step_key)

        is_continue = (
            action == "continue"
            or lower_msg in ("continue", "continue →")
            or (raw_val is not None and lower_msg != (matched_opt or "").lower())
        )

        # Handle Continue
        if is_continue:
            if not current_val:
                return step_response(session, step_key, prefix="Please select an option.")
            next_idx = HIRE_DEV_STEPS.index(step_key) + 1
            next_step = HIRE_DEV_STEPS[next_idx]
            session.current_step = next_step
            return step_response(session, next_step)

        if matched_opt:
            return step_response(session, step_key)

        return step_response(session, step_key, prefix="Please select an option.")

    # 4. Contact Information Step
    if step_key == "contact_information":
        contact_inputs = parse_contact_inputs(message, payload_data)

        # Merge into existing session data
        for k in ("name", "email", "phone", "website_url", "comment"):
            if k in contact_inputs and contact_inputs[k] is not None:
                session.hire_dev_data[k] = contact_inputs[k]

        # Deterministic Python validation
        name = session.hire_dev_data.get("name")
        email = session.hire_dev_data.get("email")
        phone = session.hire_dev_data.get("phone")
        website_url = session.hire_dev_data.get("website_url")
        comment = session.hire_dev_data.get("comment")

        # 1. Validate Name
        if not name or not str(name).strip():
            return step_response(session, step_key, prefix="Please enter a valid full name.")
        try:
            validate_full_name(str(name))
        except (ValueError, ValidationError):
            return step_response(session, step_key, prefix="Please enter a valid full name.")

        # 2. Validate Comment
        if not comment or not str(comment).strip():
            return step_response(session, step_key, prefix="Comment is required.")
        try:
            validate_hire_dev_comment(str(comment))
        except (ValueError, ValidationError):
            return step_response(session, step_key, prefix="Comment is required.")

        # Email, phone, and website URL validations have been removed

        # All validations passed -> Duplicate submission protection
        if session.completed:
            return completion_response(top_options)

        # Build final validated dictionary
        final_payload = {
            "session_id": session_id,
            "skills": list(session.hire_dev_data.get("skills") or []),
            "technology": list(session.hire_dev_data.get("technology") or []),
            "work_time": list(session.hire_dev_data.get("work_time") or []) if isinstance(session.hire_dev_data.get("work_time"), list) else [session.hire_dev_data.get("work_time")],
            "timeframe": session.hire_dev_data.get("timeframe"),
            "start": session.hire_dev_data.get("start"),
            "name": str(name).strip() if name else "",
            "email": str(email).strip() if email else "",
            "phone": str(phone).strip() if phone else "",
            "website_url": str(website_url).strip() if website_url else None,
            "comment": str(comment).strip() if comment else "",
        }

        # Send Brevo email
        try:
            await send_hire_developer_email(final_payload)
            session.email_sent = True
        except EmailSendError:
            logger.exception("Brevo email send failed for Hire a Developer")
            session.email_sent = False
            session.completed = False
            session.current_step = step_key
            return ChatResponse(
                message=EMAIL_FAILURE_MESSAGE,
                type=ResponseType.contact_form,
                suggestions=["Submit", "Back"],
                mode=ConversationMode.hire_developer,
                step=step_key,
                completed=False,
                form_data=dict(session.hire_dev_data),
            )

        session.completed = True
        session.current_step = None
        session.email_sent = True
        logger.info("Hire a Developer completed successfully | session_id=%s", session_id)
        return completion_response(top_options)

    return step_response(session, step_key)
