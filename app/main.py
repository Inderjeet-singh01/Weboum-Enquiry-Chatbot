import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.chat import router as chat_router
from app.core.config import settings

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Company Website Chatbot",
    description=(
        "MVP chatbot backend for business enquiries and website questions. "
        "Single endpoint: POST /api/chat."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router, prefix="/api")


@app.exception_handler(Exception)
async def unhandled_error(_request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, (HTTPException, StarletteHTTPException, RequestValidationError)):
        raise exc
    logger.exception("Unhandled application error")
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
