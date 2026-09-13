import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.v1.schemas import (
    AccountCreateRequest,
    AccountResponse,
    AccountsPageResponse,
    AccountStatusUpdateRequest,
    LedgerEntryResponse,
    TransactionsPageResponse,
)
from app.db.session import get_db
from app.repositories import accounts_repo, ledger_repo
from app.services import account_service

router = APIRouter(tags=["accounts"])


def _to_account_response(db: Session, account) -> AccountResponse:
    return AccountResponse(
        id=account.id,
        cat_name=account.cat_name,
        status=str(account.status),
        balance_minor=account_service.get_balance_minor(db, account.id),
        daily_limit_minor=account.daily_limit_minor,
        created_at=account.created_at,
    )


@router.post("/accounts", response_model=AccountResponse, status_code=201)
def create_account(body: AccountCreateRequest, db: Session = Depends(get_db)) -> AccountResponse:
    account = account_service.create_account(
        db, cat_name=body.cat_name, daily_limit_minor=body.daily_limit_minor
    )
    return _to_account_response(db, account)


@router.get("/accounts", response_model=AccountsPageResponse)
def list_accounts(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> AccountsPageResponse:
    """Not one of the original six endpoints. Added because the frontend's
    sender/recipient dropdowns have nothing to populate from without it, and
    there is no other way to discover an account id in a fresh clone.
    Unauthenticated, like account creation -- see README.
    """
    accounts = accounts_repo.list_all(db, limit=limit, offset=offset)
    return AccountsPageResponse(
        items=[_to_account_response(db, a) for a in accounts], limit=limit, offset=offset
    )


@router.get("/accounts/{account_id}", response_model=AccountResponse)
def get_account(account_id: uuid.UUID, db: Session = Depends(get_db)) -> AccountResponse:
    account = account_service.get_account_or_404(db, account_id)
    return _to_account_response(db, account)


@router.patch("/accounts/{account_id}/status", response_model=AccountResponse)
def update_account_status(
    account_id: uuid.UUID, body: AccountStatusUpdateRequest, db: Session = Depends(get_db)
) -> AccountResponse:
    """Dev/admin-only, unauthenticated -- see account_service.set_account_status.
    Not one of the original six endpoints; added so FROZEN/CLOSED are
    reachable through the API at all instead of only by editing the database.
    """
    account = account_service.set_account_status(db, account_id, body.status)
    return _to_account_response(db, account)


@router.get(
    "/accounts/{account_id}/transactions",
    response_model=TransactionsPageResponse,
)
def get_account_transactions(
    account_id: uuid.UUID,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> TransactionsPageResponse:
    account_service.get_account_or_404(db, account_id)  # 404 before touching the ledger
    entries = ledger_repo.list_for_account(db, account_id, limit=limit, offset=offset)
    return TransactionsPageResponse(
        items=[LedgerEntryResponse.model_validate(e) for e in entries],
        limit=limit,
        offset=offset,
    )
