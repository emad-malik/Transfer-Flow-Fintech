"""Ledger reads and the one place double-entry rows get inserted."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db.models import LedgerDirection, LedgerEntry


def balance_minor(db: Session, account_id: uuid.UUID) -> int:
    """balance(account) = SUM(credits) - SUM(debits). Derived, never stored.

    See README for the tradeoff: at real volume this SUM would be replaced by a
    periodic snapshot plus incremental reconciliation. For this slice, deriving it
    fresh on every read means there is nothing that can drift from the ledger.
    """
    signed = case(
        (LedgerEntry.direction == LedgerDirection.CREDIT.value, LedgerEntry.amount_minor),
        else_=-LedgerEntry.amount_minor,
    )
    stmt = select(func.coalesce(func.sum(signed), 0)).where(LedgerEntry.account_id == account_id)
    return int(db.execute(stmt).scalar_one())


def posted_debits_since(db: Session, account_id: uuid.UUID, since: datetime) -> int:
    stmt = select(func.coalesce(func.sum(LedgerEntry.amount_minor), 0)).where(
        LedgerEntry.account_id == account_id,
        LedgerEntry.direction == LedgerDirection.DEBIT.value,
        LedgerEntry.created_at >= since,
    )
    return int(db.execute(stmt).scalar_one())


def posted_debits_today_utc(db: Session, account_id: uuid.UUID) -> int:
    """The daily limit resets at UTC midnight. A single fixed boundary, stated
    plainly, beats a per-account timezone nobody asked for in this slice.
    """
    start_of_day = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return posted_debits_since(db, account_id, start_of_day)


def insert_double_entry(
    db: Session,
    *,
    transfer_id: uuid.UUID,
    source_account_id: uuid.UUID,
    destination_account_id: uuid.UUID,
    amount_minor: int,
) -> None:
    """One DEBIT on the source, one CREDIT on the destination, same amount, same
    transfer, same transaction. This is the only function in the codebase that
    writes to ledger_entries, and it never writes an unbalanced pair.
    """
    db.add(
        LedgerEntry(
            id=uuid.uuid4(),
            transfer_id=transfer_id,
            account_id=source_account_id,
            direction=LedgerDirection.DEBIT.value,
            amount_minor=amount_minor,
        )
    )
    db.add(
        LedgerEntry(
            id=uuid.uuid4(),
            transfer_id=transfer_id,
            account_id=destination_account_id,
            direction=LedgerDirection.CREDIT.value,
            amount_minor=amount_minor,
        )
    )
    db.flush()


def list_for_account(
    db: Session, account_id: uuid.UUID, limit: int, offset: int
) -> list[LedgerEntry]:
    stmt = (
        select(LedgerEntry)
        .where(LedgerEntry.account_id == account_id)
        .order_by(LedgerEntry.created_at.desc(), LedgerEntry.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(db.execute(stmt).scalars())
