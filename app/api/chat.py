import json
import logging
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chatbot import (
    get_all_sessions,
    handle_chat,
    is_general_question,
    stream_chat,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


@router.post("/chat")
async def chat(request: Request, payload: ChatRequest) -> Response:
    """Chat endpoint supporting deterministic flow and progressive LLM streaming."""
    accept_header = request.headers.get("accept", "")
    is_stream_client = (
        "text/event-stream" in accept_header
        or request.headers.get("x-stream") == "true"
        or request.query_params.get("stream") == "true"
    )

    if is_stream_client and is_general_question(payload.session_id, payload.message):
        async def event_generator():
            try:
                async for event in stream_chat(payload.session_id, payload.message):
                    yield f"data: {json.dumps(event)}\n\n"
            except Exception:
                logger.exception("Error during general chat stream")
                yield f"data: {json.dumps({'type': 'error', 'message': 'An error occurred during streaming.'})}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # Standard JSON response for enquiry mode, initial options, or non-streaming clients
    response = await handle_chat(payload.session_id, payload.message)
    return JSONResponse(content=response.model_dump())


@router.get("/all-sessions")
async def all_sessions() -> dict[str, Any]:
    return get_all_sessions()
