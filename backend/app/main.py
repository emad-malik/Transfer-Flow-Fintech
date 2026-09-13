import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.v1.errors import register_exception_handlers
from app.api.v1.router import api_router
from app.config import settings
from app.db.session import engine

logger = logging.getLogger("meowpay.requests")

app = FastAPI(title="MeowPay", version="0.1.0")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """One structured log line per request: request id, caller, and outcome.
    README's central argument is auditability -- the only logging that
    existed was the unhandled-error logger, which left every successful and
    every domain-rejected request with no trace at all. This isn't a
    replacement for real observability (out of scope, see README), just
    enough that "who did what, and what happened" is answerable from logs.
    """

    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        started = time.monotonic()
        response = await call_next(request)
        duration_ms = (time.monotonic() - started) * 1000
        logger.info(
            "request_id=%s method=%s path=%s caller=%s status=%s transfer_id=%s duration_ms=%.1f",
            request_id,
            request.method,
            request.url.path,
            request.headers.get("x-cat-id", "-"),
            response.status_code,
            response.headers.get("x-transfer-id", "-"),
            duration_ms,
        )
        response.headers["X-Request-Id"] = request_id
        return response


app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)
app.include_router(api_router)


@app.get("/healthz")
def healthz() -> dict:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok"}
