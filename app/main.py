import logging
import sys
from pathlib import Path
from contextlib import asynccontextmanager

import numpy as np

# Cross-compatibility shim between NumPy 1.x and NumPy 2.x pickles
if not hasattr(np, "_core") and hasattr(np, "core"):
    sys.modules["numpy._core"] = np.core
    if hasattr(np.core, "numeric"):
        sys.modules["numpy._core.numeric"] = np.core.numeric
    if hasattr(np.core, "multiarray"):
        sys.modules["numpy._core.multiarray"] = np.core.multiarray

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.exceptions import RequestValidationError
from app.api.chat import router as chat_router
from app.core.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:     %(message)s",
    force=True,
)

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Application startup")
    yield
    logger.info("Application shutdown")


app = FastAPI(
    title="Company Website Chatbot",
    description=(
        "MVP chatbot backend for business enquiries and website questions. "
        "Single endpoint: POST /api/chat."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router, prefix="/api")


@app.get("/health", tags=["health"])
async def health_check() -> dict[str, str]:
    """Lightweight production health check endpoint."""
    return {"status": "ok"}


@app.middleware("http")
async def no_cache_static_middleware(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static") or request.url.path == "/":
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(str(STATIC_DIR / "index.html"))


@app.exception_handler(Exception)
async def unhandled_error(_request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, (HTTPException, StarletteHTTPException, RequestValidationError)):
        raise exc
    logger.exception("Unhandled application error")
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
