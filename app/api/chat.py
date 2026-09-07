from typing import Any

from fastapi import APIRouter

from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chatbot import get_all_sessions, handle_chat

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest) -> ChatResponse:
    return await handle_chat(payload.session_id, payload.message)


@router.get("/all-sessions")
async def all_sessions() -> dict[str, Any]:
    return get_all_sessions()
