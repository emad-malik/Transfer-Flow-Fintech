"""All account SQL lives here. Nothing above this imports a Session directly."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Account


def get_by_id(db: Session, account_id: uuid.UUID) -> Account | None:
    return db.get(Account, account_id)


def get_for_update(db: Session, account_id: uuid.UUID) -> Account | None:
    stmt = select(Account).where(Account.id == account_id).with_for_update()
    return db.execute(stmt).scalar_one_or_none()


def lock_pair_ordered(
    db: Session, id_a: uuid.UUID, id_b: uuid.UUID
) -> dict[uuid.UUID, Account | None]:
    """Lock both accounts with SELECT ... FOR UPDATE, always in ascending id order.

    This is the deadlock guard: if A->B and B->A race, both transactions try to
    acquire locks in the same order (lowest id first), so one simply waits for the
    other instead of the two forming a wait-for cycle. Without this, concurrent
    transfers in opposite directions can deadlock and Postgres kills one of them.
    """
    ordered_ids = sorted([id_a, id_b], key=str)
    result: dict[uuid.UUID, Account | None] = {}
    for account_id in ordered_ids:
        result[account_id] = get_for_update(db, account_id)
    return result


def create(db: Session, cat_name: str, daily_limit_minor: int) -> Account:
    account = Account(cat_name=cat_name, daily_limit_minor=daily_limit_minor)
    db.add(account)
    db.flush()
    return account


def set_status(db: Session, account: Account, status: str) -> Account:
    account.status = status
    db.flush()
    return account


def list_all(db: Session, limit: int, offset: int) -> list[Account]:
    stmt = select(Account).order_by(Account.created_at.asc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars())
