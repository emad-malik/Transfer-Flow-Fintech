"""Domain error codes and the exception type that carries them.

This is the single source of truth for the error contract in PLAN.md section 5.
The API layer catches DomainError and maps it to the HTTP status + envelope;
nothing in here knows about FastAPI or HTTP.
"""

from enum import StrEnum


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    MISSING_IDEMPOTENCY_KEY = "MISSING_IDEMPOTENCY_KEY"
    IDEMPOTENCY_KEY_REUSE = "IDEMPOTENCY_KEY_REUSE"
    ACCOUNT_NOT_FOUND = "ACCOUNT_NOT_FOUND"
    SELF_TRANSFER = "SELF_TRANSFER"
    AMOUNT_NOT_POSITIVE = "AMOUNT_NOT_POSITIVE"
    AMOUNT_ABOVE_MAX = "AMOUNT_ABOVE_MAX"
    SOURCE_ACCOUNT_NOT_ACTIVE = "SOURCE_ACCOUNT_NOT_ACTIVE"
    DESTINATION_ACCOUNT_NOT_ACTIVE = "DESTINATION_ACCOUNT_NOT_ACTIVE"
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    DAILY_LIMIT_EXCEEDED = "DAILY_LIMIT_EXCEEDED"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    UNAUTHORIZED_SOURCE = "UNAUTHORIZED_SOURCE"
    TRANSFER_NOT_FOUND = "TRANSFER_NOT_FOUND"


# HTTP status per code, kept next to the codes so the two never drift apart.
STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.MISSING_IDEMPOTENCY_KEY: 400,
    ErrorCode.IDEMPOTENCY_KEY_REUSE: 409,
    ErrorCode.ACCOUNT_NOT_FOUND: 404,
    ErrorCode.SELF_TRANSFER: 422,
    ErrorCode.AMOUNT_NOT_POSITIVE: 422,
    ErrorCode.AMOUNT_ABOVE_MAX: 422,
    ErrorCode.SOURCE_ACCOUNT_NOT_ACTIVE: 409,
    ErrorCode.DESTINATION_ACCOUNT_NOT_ACTIVE: 409,
    ErrorCode.INSUFFICIENT_FUNDS: 409,
    ErrorCode.DAILY_LIMIT_EXCEEDED: 409,
    ErrorCode.CURRENCY_MISMATCH: 422,
    ErrorCode.UNAUTHORIZED_SOURCE: 403,
    ErrorCode.TRANSFER_NOT_FOUND: 404,
}


class DomainError(Exception):
    """Raised by domain/service code. The API layer is the only thing that catches it."""

    def __init__(self, code: ErrorCode, message: str, details: dict | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)

    @property
    def http_status(self) -> int:
        return STATUS_BY_CODE[self.code]
