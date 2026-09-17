from __future__ import annotations

from enum import Enum
import re
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, EmailStr, Field, TypeAdapter, ValidationError, field_validator


class ResponseType(str, Enum):
    options = "options"
    text = "text"
    email = "email"
    phone = "phone"
    multi_options = "multi_options"
    contact_form = "contact_form"


class ConversationMode(str, Enum):
    initial = "initial"
    enquiry = "enquiry"
    hire_developer = "hire_developer"
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


def validate_hire_dev_email(value: str | None) -> str | None:
    """Accept hire-a-developer contact email without strict format validation."""
    if value is None:
        return None
    return value.strip()


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
    if value.lower() in ("company", "business", "firm", "organization", "corp", "corporation"):
        raise ValueError("Please enter your company or organization name (e.g. Acme Corp).")
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


def validate_phone_number(value: str) -> str:
    """Validate phone number: digits, plus, spaces, dashes, parentheses; at least 7 digits."""
    value = value.strip()
    if len(value) < 7:
        raise ValueError("Please enter a valid phone number (at least 7 digits).")
    if len(value) > 30:
        raise ValueError("Phone number must not exceed 30 characters.")
    cleaned = re.sub(r"[\s\+\-\(\)\.]", "", value)
    if not cleaned.isdigit() or len(cleaned) < 7 or "?" in value:
        raise ValueError("Please enter a valid phone number (e.g. +44 7700 900123 or +1 555-123-4567).")
    return value


def validate_hire_dev_phone(value: str | None) -> str | None:
    """Accept hire-a-developer contact phone number without strict format validation."""
    if value is None:
        return None
    return value.strip()


def validate_hire_dev_comment(value: str) -> str:
    """Validate hire-a-developer requirements comment."""
    value = value.strip()
    if not value:
        raise ValueError("Comment is required.")
    if len(value) > 2000:
        raise ValueError("Comment must not exceed 2000 characters.")
    return value


def validate_website_url(value: str | None) -> str | None:
    """Accept hire-a-developer website URL without strict format validation."""
    if value is None:
        return None
    return value.strip()


# ---------------------------------------------------------------------------
# API Request / Response & Data Models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    session_id: str | None = Field(default=None, description="Session ID, auto-generated if omitted.")
    message: str = Field(default="", max_length=2000, description="Chat message input.")
    action: str | None = Field(default=None, description="Action: continue, back, submit, etc.")
    step: str | None = Field(default=None, description="Current step name for structured actions.")
    data: dict[str, Any] | None = Field(default=None, description="Optional structured form or step data.")

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
    selected: list[str] | None = None
    form_data: dict[str, Any] | None = None


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

    @field_validator("phone_number")
    @classmethod
    def check_phone(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return validate_phone_number(v)

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


class HireDeveloperData(BaseModel):
    """Pydantic model representing validated hire a developer data."""
    skills: list[str] = Field(default_factory=list)
    technology: list[str] = Field(default_factory=list)
    work_time: list[str] = Field(default_factory=list)
    timeframe: str | None = None
    start: str | None = None
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    website_url: str | None = None
    comment: str | None = None

    @field_validator("email")
    @classmethod
    def check_email(cls, v: str | None) -> str | None:
        return validate_hire_dev_email(v)

    @field_validator("phone")
    @classmethod
    def check_phone(cls, v: str | None) -> str | None:
        return validate_hire_dev_phone(v)

    @field_validator("name")
    @classmethod
    def check_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return validate_full_name(v)

    @field_validator("comment")
    @classmethod
    def check_comment(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return validate_hire_dev_comment(v)

    @field_validator("website_url")
    @classmethod
    def check_url(cls, v: str | None) -> str | None:
        return validate_website_url(v)


class ErrorResponse(BaseModel):
    detail: str
