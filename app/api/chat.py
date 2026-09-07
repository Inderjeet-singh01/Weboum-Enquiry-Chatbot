from fastapi import APIRouter

from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chatbot import handle_chat

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest) -> ChatResponse:
    return await handle_chat(payload.session_id, payload.message)
