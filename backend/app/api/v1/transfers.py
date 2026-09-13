"""POST /v1/transfers response status is deliberately uniform: 200 whenever
execute_transfer returns a Transfer (whether freshly POSTED or a replayed
idempotent lookup), and the error table's own status codes whenever it raises.
This keeps "how did this call end" answerable from one thing (raised or not)
instead of inspecting the body to know which 2xx it is.
"""

import uuid

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from app.api.v1.schemas import TransferCreateRequest, TransferResponse
from app.config import settings
from app.db.session import get_db
from app.domain.errors import DomainError, ErrorCode
from app.repositories import transfers_repo
from app.services import transfer_service

router = APIRouter(tags=["transfers"])


@router.post("/transfers", response_model=TransferResponse, status_code=200)
def create_transfer(
    body: TransferCreateRequest,
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    caller_cat_id: str | None = Header(default=None, alias="X-Cat-Id"),
) -> TransferResponse:
    if not idempotency_key:
        raise DomainError(
            ErrorCode.MISSING_IDEMPOTENCY_KEY, "Idempotency-Key header is required."
        )
    if not caller_cat_id:
        # A missing principal is a malformed request, not an identity mismatch --
        # UNAUTHORIZED_SOURCE is reserved for "you are someone, but not the source".
        raise DomainError(ErrorCode.VALIDATION_ERROR, "X-Cat-Id header is required.")

    transfer = transfer_service.execute_transfer(
        db,
        caller_cat_id=caller_cat_id,
        idempotency_key=idempotency_key,
        source_account_id=body.source_account_id,
        destination_account_id=body.destination_account_id,
        amount_minor=body.amount_minor,
        currency=body.currency,
        expected_currency=settings.currency,
        max_amount_minor=settings.transfer_max_amount_minor,
    )
    return TransferResponse.model_validate(transfer)


@router.get("/transfers/{transfer_id}", response_model=TransferResponse)
def get_transfer(transfer_id: uuid.UUID, db: Session = Depends(get_db)) -> TransferResponse:
    transfer = transfers_repo.get_by_id(db, transfer_id)
    if transfer is None:
        raise DomainError(ErrorCode.TRANSFER_NOT_FOUND, "Transfer not found.")
    return TransferResponse.model_validate(transfer)
