"""Maps every error path (domain errors, request validation, anything unhandled)
onto the single envelope shape in PLAN.md section 5. This is the only place that
knows the envelope's JSON shape.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.domain.errors import DomainError

logger = logging.getLogger("meowpay")


def _envelope(code: str, message: str, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content=_envelope(exc.code.value, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # FastAPI's own shape is a list of {loc, msg, type}; flatten it into one
        # message and keep the raw list in details for anyone who wants specifics.
        first = exc.errors()[0] if exc.errors() else None
        if first:
            location = ".".join(str(p) for p in first["loc"])
            message = f"{location}: {first['msg']}"
        else:
            message = "Invalid request."
        return JSONResponse(
            status_code=422,
            content=_envelope("VALIDATION_ERROR", message, {"errors": exc.errors()}),
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        # Not part of the formal error table -- a last-resort safety net so a bug
        # never leaks a raw traceback to a caller. Logged with full detail server
        # side; the client gets nothing sensitive.
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=_envelope("INTERNAL_ERROR", "An unexpected error occurred."),
        )
