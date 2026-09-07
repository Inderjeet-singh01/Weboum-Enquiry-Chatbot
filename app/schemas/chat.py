from __future__ import annotations

from enum import Enum
import re

from pydantic import BaseModel, EmailStr, Field, TypeAdapter, ValidationError, field_validator


class ResponseType(str, Enum):
    options = "options"
    text = "text"
    email = "email"
    phone = "phone"


class ConversationMode(str, Enum):
    initial = "initial"
    enquiry = "enquiry"
    general = "general"


# ---------------------------------------------------------------------------
# Reusable Field Validation Helpers with Pydantic Integration
# ---------------------------------------------------------------------------

_EMAIL_ADAPTER = TypeAdapter(EmailStr)
_NAME_PATTERN = re.compile(r"^[a-zA-ZÀ-ÿ\s'\-\.]+$")


def validate_work_email(value: str) -> str:
    """Validate email address format and domain structure using Pydantic EmailStr."""
    value = value.strip()
    try:
        validated = str(_EMAIL_ADAPTER.validate_python(value))
    except ValidationError:
        raise ValueError("Please enter a valid work email address (e.g. name@company.com).")
    parts = validated.split("@")
    if len(parts) != 2 or "." not in parts[1]:
        raise ValueError("Please enter a valid work email address.")
    tld = parts[1].rsplit(".", 1)[-1]
    if len(tld) < 2 or not tld.isalpha():
        raise ValueError("Please enter a valid work email address.")
    return validated


def validate_full_name(value: str) -> str:
    """Validate person full name: length, alphabetic characters, not purely digits."""
    value = value.strip()
    if len(value) < 2:
        raise ValueError("Please enter a valid full name (at least 2 characters).")
    if len(value) > 100:
        raise ValueError("Full name must not exceed 100 characters.")
    if not re.search(r"[a-zA-ZÀ-ÿ]", value):
        raise ValueError("Please enter a valid full name (must contain letters).")
    if not _NAME_PATTERN.fullmatch(value):
        raise ValueError("Please enter a valid full name (no numbers or special symbols).")
    return value


def validate_company_name(value: str) -> str:
    """Validate company name: length, characters, not purely numbers."""
    value = value.strip()
    if len(value) < 2:
        raise ValueError("Please enter a valid company name (at least 2 characters).")
    if len(value) > 120:
        raise ValueError("Company name must not exceed 120 characters.")
    if not re.search(r"[a-zA-Z0-9À-ÿ]", value):
        raise ValueError("Please enter a valid company name.")
    if re.fullmatch(r"^\d+$", value):
        raise ValueError("Please enter a valid company name (not just digits).")
    return value


def validate_tech_stack(value: str) -> str:
    """Validate technology stack description."""
    value = value.strip()
    if len(value) < 2:
        raise ValueError("Please enter your current tools or technology stack (at least 2 characters, e.g. WhatsApp, Salesforce, Excel).")
    if len(value) > 500:
        raise ValueError("Technology stack must not exceed 500 characters.")
    if not re.search(r"[a-zA-Z0-9]", value):
        raise ValueError("Please enter a valid technology stack.")
    if re.fullmatch(r"^\d+$", value):
        raise ValueError("Please enter your current tools or technology stack (e.g. WhatsApp, Salesforce, Excel).")
    return value


# ---------------------------------------------------------------------------
# API Request / Response & Data Models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    session_id: str | None = Field(default=None, description="Session ID, auto-generated if omitted.")
    message: str = Field(default="", max_length=2000, description="Chat message input.")

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if not re.match(r"^[a-zA-Z0-9_\-]+$", value):
            raise ValueError("session_id must contain only alphanumeric characters, dashes, or underscores")
        if len(value) > 128:
            raise ValueError("session_id must not exceed 128 characters")
        return value

    @field_validator("message")
    @classmethod
    def strip_message(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value


class ChatResponse(BaseModel):
    session_id: str | None = None
    message: str
    type: ResponseType
    suggestions: list[str] = Field(default_factory=list)
    mode: ConversationMode
    step: str | None = None
    completed: bool = False


class EnquiryData(BaseModel):
    """Pydantic model representing validated business enquiry data."""
    biggest_operational_challenge: str | None = None
    ai_capability: str | None = None
    primary_industry: str | None = None
    business_size: str | None = None
    full_name: str | None = None
    company_name: str | None = None
    work_email: str | None = None
    phone_number: str | None = None
    current_technology_stack: str | None = None

    @field_validator("work_email")
    @classmethod
    def check_email(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return validate_work_email(v)

    @field_validator("full_name")
    @classmethod
    def check_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return validate_full_name(v)

    @field_validator("company_name")
    @classmethod
    def check_company(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return validate_company_name(v)

    @field_validator("current_technology_stack")
    @classmethod
    def check_tech_stack(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return validate_tech_stack(v)


class ErrorResponse(BaseModel):
    detail: str
