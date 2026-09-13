import uuid

from sqlalchemy.orm import Session

from app.db.models import Account
from app.domain.errors import DomainError, ErrorCode
from app.repositories import accounts_repo, ledger_repo


def create_account(db: Session, *, cat_name: str, daily_limit_minor: int) -> Account:
    with db.begin():
        account = accounts_repo.create(db, cat_name, daily_limit_minor)
    return account


def get_account_or_404(db: Session, account_id: uuid.UUID) -> Account:
    account = accounts_repo.get_by_id(db, account_id)
    if account is None:
        raise DomainError(ErrorCode.ACCOUNT_NOT_FOUND, "Account not found.")
    return account


def get_balance_minor(db: Session, account_id: uuid.UUID) -> int:
    return ledger_repo.balance_minor(db, account_id)


def set_account_status(db: Session, account_id: uuid.UUID, status: str) -> Account:
    """Dev/admin operation: there is no real admin auth in this slice (see
    README), so this endpoint is unauthenticated on purpose, same as the
    account-creation endpoint. It exists so FROZEN/CLOSED -- and the two error
    codes that depend on them -- are reachable through the API at all, rather
    than only by hand-editing the database.
    """
    with db.begin():
        account = accounts_repo.get_for_update(db, account_id)
        if account is None:
            raise DomainError(ErrorCode.ACCOUNT_NOT_FOUND, "Account not found.")
        accounts_repo.set_status(db, account, status)
    return account
