from enum import Enum

from pydantic import BaseModel, Field, field_validator


class ResponseType(str, Enum):
    options = "options"
    text = "text"
    email = "email"
    phone = "phone"


class ConversationMode(str, Enum):
    initial = "initial"
    enquiry = "enquiry"
    general = "general"


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    message: str = Field(default="")

    @field_validator("session_id")
    @classmethod
    def strip_session_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("session_id must not be empty")
        return value

    @field_validator("message")
    @classmethod
    def strip_message(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value


class ChatResponse(BaseModel):
    message: str
    type: ResponseType
    suggestions: list[str] = Field(default_factory=list)
    mode: ConversationMode
    step: str | None = None
    completed: bool = False


class ErrorResponse(BaseModel):
    detail: str
