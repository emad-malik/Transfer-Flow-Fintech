"""Pydantic v2 request/response models. This is the HTTP boundary: anything that
doesn't parse never reaches the service layer.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.config import settings


class AccountCreateRequest(BaseModel):
    cat_name: str = Field(min_length=1, max_length=120)
    daily_limit_minor: StrictInt = Field(
        default_factory=lambda: settings.default_daily_limit_minor, gt=0
    )


class AccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    cat_name: str
    status: str
    balance_minor: int
    daily_limit_minor: int
    created_at: datetime


class AccountStatusUpdateRequest(BaseModel):
    status: str = Field(pattern="^(ACTIVE|FROZEN|CLOSED)$")


class LedgerEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    transfer_id: uuid.UUID
    direction: str
    amount_minor: int
    created_at: datetime


class AccountsPageResponse(BaseModel):
    items: list[AccountResponse]
    limit: int
    offset: int


class TransactionsPageResponse(BaseModel):
    items: list[LedgerEntryResponse]
    limit: int
    offset: int


class TransferCreateRequest(BaseModel):
    """amount_minor is StrictInt (not conint) on purpose: a bool, float, or numeric
    string must fail schema validation as VALIDATION_ERROR, but a zero or negative
    plain int must reach the domain layer so it can be recorded as a FAILED transfer
    with AMOUNT_NOT_POSITIVE -- see README's "The transfer flow" section for why
    the failure needs to be durable rather than rejected silently at the boundary.
    """

    source_account_id: uuid.UUID
    destination_account_id: uuid.UUID
    amount_minor: StrictInt
    currency: str = Field(min_length=1, max_length=10)


class TransferResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    idempotency_key: str
    source_account_id: uuid.UUID
    destination_account_id: uuid.UUID
    amount_minor: int
    currency: str
    status: str
    failure_code: str | None
    failure_reason: str | None
    created_at: datetime
    completed_at: datetime | None


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    error: ErrorBody
