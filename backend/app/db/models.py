"""ORM models for the three tables: accounts, transfers, ledger_entries.

Deliberately no `accounts.balance` column. Balance is SUM(credits) - SUM(debits)
over ledger_entries, computed in repositories/ledger_repo.py. See README for the
tradeoff (a snapshot/cache would be needed at real volume; correctness first here).
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import BIGINT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class AccountStatus(enum.StrEnum):
    ACTIVE = "ACTIVE"
    FROZEN = "FROZEN"
    CLOSED = "CLOSED"


class TransferStatus(enum.StrEnum):
    PENDING = "PENDING"
    POSTED = "POSTED"
    FAILED = "FAILED"


class LedgerDirection(enum.StrEnum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cat_name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[AccountStatus] = mapped_column(
        String(20), nullable=False, default=AccountStatus.ACTIVE
    )
    daily_limit_minor: Mapped[int] = mapped_column(BIGINT, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("daily_limit_minor > 0", name="ck_accounts_daily_limit_positive"),
        CheckConstraint(
            "status IN ('ACTIVE', 'FROZEN', 'CLOSED')", name="ck_accounts_status_valid"
        ),
    )


class Transfer(Base):
    __tablename__ = "transfers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    source_account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("accounts.id"), nullable=False
    )
    destination_account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("accounts.id"), nullable=False
    )
    amount_minor: Mapped[int] = mapped_column(BIGINT, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="TREATS")
    status: Mapped[TransferStatus] = mapped_column(
        String(20), nullable=False, default=TransferStatus.PENDING
    )
    failure_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    ledger_entries: Mapped[list["LedgerEntry"]] = relationship(back_populates="transfer")

    # No positive-amount CHECK constraint here, deliberately: a transfers row is the
    # audit record of an *attempt*, including a request with amount_minor <= 0, and
    # that attempt must be persisted as FAILED with AMOUNT_NOT_POSITIVE so it shows up
    # in the audit trail. The positive-amount guarantee that actually matters -- that
    # money movements are never zero or negative -- is enforced on ledger_entries below,
    # which is the table that represents real, posted movement.
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'POSTED', 'FAILED')", name="ck_transfers_status_valid"
        ),
    )


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    transfer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("transfers.id"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("accounts.id"), nullable=False)
    direction: Mapped[LedgerDirection] = mapped_column(String(10), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BIGINT, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    transfer: Mapped["Transfer"] = relationship(back_populates="ledger_entries")

    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_ledger_amount_positive"),
        CheckConstraint("direction IN ('DEBIT', 'CREDIT')", name="ck_ledger_direction_valid"),
        Index("ix_ledger_account_created", "account_id", "created_at"),
    )
