"""seed: external funding + treasury system accounts, one balanced transfer

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-13

See app/domain/system_accounts.py for why these two accounts exist: balance is
derived-only, so the first treats in the system have to enter through a real,
balanced double-entry transfer rather than an unbalanced INSERT. This migration
is that one entry. It is idempotent (ON CONFLICT DO NOTHING) so re-running
migrations against a database that already has it is a no-op, not an error.
"""
import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXTERNAL_FUNDING_ID = "00000000-0000-0000-0000-000000000002"
TREASURY_ID = "00000000-0000-0000-0000-000000000001"
SEED_TRANSFER_ID = "00000000-0000-0000-0000-0000000000f0"
SEED_AMOUNT_MINOR = 100_000_000
SEED_IDEMPOTENCY_KEY = "seed-treasury-v1"


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(
        sa.text(
            """
            INSERT INTO accounts (id, cat_name, status, daily_limit_minor)
            VALUES
                (:external_id, 'MeowPay External Funding', 'ACTIVE', :cap),
                (:treasury_id, 'MeowPay Treasury', 'ACTIVE', :cap)
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {
            "external_id": EXTERNAL_FUNDING_ID,
            "treasury_id": TREASURY_ID,
            # Large enough that the one seed transfer never trips the daily
            # limit rule; both accounts are system accounts, not real wallets.
            "cap": SEED_AMOUNT_MINOR * 10,
        },
    )

    conn.execute(
        sa.text(
            """
            INSERT INTO transfers
                (id, idempotency_key, request_fingerprint, source_account_id,
                 destination_account_id, amount_minor, currency, status, completed_at)
            VALUES
                (:transfer_id, :idem_key, 'seed', :source_id, :dest_id, :amount,
                 'TREATS', 'POSTED', now())
            ON CONFLICT (idempotency_key) DO NOTHING
            """
        ),
        {
            "transfer_id": SEED_TRANSFER_ID,
            "idem_key": SEED_IDEMPOTENCY_KEY,
            "source_id": EXTERNAL_FUNDING_ID,
            "dest_id": TREASURY_ID,
            "amount": SEED_AMOUNT_MINOR,
        },
    )

    for account_id, direction, entry_suffix in (
        (EXTERNAL_FUNDING_ID, "DEBIT", "1"),
        (TREASURY_ID, "CREDIT", "2"),
    ):
        conn.execute(
            sa.text(
                """
                INSERT INTO ledger_entries (id, transfer_id, account_id, direction, amount_minor)
                SELECT :entry_id, :transfer_id, :account_id, :direction, :amount
                WHERE NOT EXISTS (
                    SELECT 1 FROM ledger_entries WHERE id = :entry_id
                )
                """
            ),
            {
                "entry_id": str(uuid.UUID(f"00000000-0000-0000-0000-0000000000{entry_suffix}0")),
                "transfer_id": SEED_TRANSFER_ID,
                "account_id": account_id,
                "direction": direction,
                "amount": SEED_AMOUNT_MINOR,
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM ledger_entries WHERE transfer_id = :id"), {"id": SEED_TRANSFER_ID}
    )
    conn.execute(sa.text("DELETE FROM transfers WHERE id = :id"), {"id": SEED_TRANSFER_ID})
    conn.execute(
        sa.text("DELETE FROM accounts WHERE id IN (:a, :b)"),
        {"a": EXTERNAL_FUNDING_ID, "b": TREASURY_ID},
    )
