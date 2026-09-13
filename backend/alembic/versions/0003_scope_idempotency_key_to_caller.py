"""scope idempotency key uniqueness to the caller; partial debit index

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-13

Two independent, unrelated changes bundled into one migration because both
are cheap schema tweaks with no app-visible behavior change beyond what they
fix:

1. transfers.idempotency_key was globally unique. Keys are client-generated
   with no coordination between callers, so two unrelated callers picking the
   same key collide -- caller B gets IDEMPOTENCY_KEY_REUSE for a transfer
   that has nothing to do with them. Add caller_cat_id (the X-Cat-Id the
   request was made with) and move the uniqueness guarantee to
   (caller_cat_id, idempotency_key). Existing rows are backfilled with
   source_account_id: every transfer before this migration was only ever
   insertable by execute_transfer with caller == source (the authorization
   check ran before any row could be created by a mismatched caller until
   the caller_cat_id column existed to record one), so source_account_id is
   the exact historical caller for every row that can exist at this point.

2. A partial index on ledger_entries for direction = 'DEBIT', matching the
   predicate posted_debits_today_utc() filters on for the daily-limit check
   that runs on every transfer attempt. Not needed at this scale; cheap to
   have ready before it is.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("transfers", sa.Column("caller_cat_id", sa.String(255), nullable=True))
    op.execute(
        "UPDATE transfers SET caller_cat_id = source_account_id::text WHERE caller_cat_id IS NULL"
    )
    op.alter_column("transfers", "caller_cat_id", nullable=False)

    op.drop_constraint("transfers_idempotency_key_key", "transfers", type_="unique")
    op.create_unique_constraint(
        "ux_transfers_caller_idempotency_key",
        "transfers",
        ["caller_cat_id", "idempotency_key"],
    )

    op.create_index(
        "ix_ledger_debits_account_created",
        "ledger_entries",
        ["account_id", "created_at"],
        postgresql_where=sa.text("direction = 'DEBIT'"),
    )


def downgrade() -> None:
    op.drop_index("ix_ledger_debits_account_created", table_name="ledger_entries")

    op.drop_constraint("ux_transfers_caller_idempotency_key", "transfers", type_="unique")
    op.create_unique_constraint("transfers_idempotency_key_key", "transfers", ["idempotency_key"])

    op.drop_column("transfers", "caller_cat_id")
