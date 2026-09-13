"""initial: accounts, transfers, ledger_entries

Revision ID: 0001
Revises:
Create Date: 2026-09-13
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("cat_name", sa.String(120), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("daily_limit_minor", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("daily_limit_minor > 0", name="ck_accounts_daily_limit_positive"),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'FROZEN', 'CLOSED')", name="ck_accounts_status_valid"
        ),
    )

    op.create_table(
        "transfers",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("idempotency_key", sa.String(255), nullable=False, unique=True),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "source_account_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id"),
            nullable=False,
        ),
        sa.Column(
            "destination_account_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id"),
            nullable=False,
        ),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False, server_default="TREATS"),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("failure_code", sa.String(50), nullable=True),
        sa.Column("failure_reason", sa.String(500), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING', 'POSTED', 'FAILED')", name="ck_transfers_status_valid"
        ),
    )
    op.create_index(
        "ix_transfers_source_account_id", "transfers", ["source_account_id"]
    )
    op.create_index(
        "ix_transfers_destination_account_id", "transfers", ["destination_account_id"]
    )

    op.create_table(
        "ledger_entries",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "transfer_id", pg.UUID(as_uuid=True), sa.ForeignKey("transfers.id"), nullable=False
        ),
        sa.Column(
            "account_id", pg.UUID(as_uuid=True), sa.ForeignKey("accounts.id"), nullable=False
        ),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("amount_minor > 0", name="ck_ledger_amount_positive"),
        sa.CheckConstraint("direction IN ('DEBIT', 'CREDIT')", name="ck_ledger_direction_valid"),
    )
    op.create_index(
        "ix_ledger_account_created", "ledger_entries", ["account_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_ledger_account_created", table_name="ledger_entries")
    op.drop_table("ledger_entries")
    op.drop_index("ix_transfers_destination_account_id", table_name="transfers")
    op.drop_index("ix_transfers_source_account_id", table_name="transfers")
    op.drop_table("transfers")
    op.drop_table("accounts")
