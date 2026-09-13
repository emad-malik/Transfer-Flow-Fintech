"""Transfer row SQL. The idempotency unique constraint is defended here."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Transfer, TransferStatus


def get_by_idempotency_key(db: Session, idempotency_key: str) -> Transfer | None:
    stmt = select(Transfer).where(Transfer.idempotency_key == idempotency_key)
    return db.execute(stmt).scalar_one_or_none()


def get_by_id(db: Session, transfer_id: uuid.UUID) -> Transfer | None:
    return db.get(Transfer, transfer_id)


def create_pending(
    db: Session,
    *,
    idempotency_key: str,
    request_fingerprint: str,
    source_account_id: uuid.UUID,
    destination_account_id: uuid.UUID,
    amount_minor: int,
    currency: str,
) -> Transfer:
    """Insert the PENDING row. Callers must flush inside a transaction so an
    IntegrityError on the idempotency_key unique constraint surfaces here, not
    after the ledger entries have also been written.
    """
    transfer = Transfer(
        id=uuid.uuid4(),
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        source_account_id=source_account_id,
        destination_account_id=destination_account_id,
        amount_minor=amount_minor,
        currency=currency,
        status=TransferStatus.PENDING.value,
    )
    db.add(transfer)
    db.flush()
    return transfer


def mark_posted(db: Session, transfer: Transfer) -> None:
    transfer.status = TransferStatus.POSTED.value
    transfer.completed_at = datetime.now(UTC)
    db.flush()


def mark_failed(db: Session, transfer: Transfer, *, code: str, reason: str) -> None:
    transfer.status = TransferStatus.FAILED.value
    transfer.failure_code = code
    transfer.failure_reason = reason
    transfer.completed_at = datetime.now(UTC)
    db.flush()
