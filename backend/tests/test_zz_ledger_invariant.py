"""Named to sort last so it runs after every other test in the suite has had a
chance to write to the ledger (pytest, absent other config, collects files in
directory-then-name order, and this file sorts after unit/, integration/, and
concurrency/). After the entire suite, credits and debits across the whole
table must net to exactly zero -- no exceptions, ever.
"""

from sqlalchemy import func, select

from app.db.models import LedgerDirection, LedgerEntry


def test_ledger_nets_to_zero_across_every_entry_ever_written(client):
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        credits = db.execute(
            select(func.coalesce(func.sum(LedgerEntry.amount_minor), 0)).where(
                LedgerEntry.direction == LedgerDirection.CREDIT.value
            )
        ).scalar_one()
        debits = db.execute(
            select(func.coalesce(func.sum(LedgerEntry.amount_minor), 0)).where(
                LedgerEntry.direction == LedgerDirection.DEBIT.value
            )
        ).scalar_one()
        assert credits == debits
        assert credits > 0  # sanity: the suite actually wrote something
    finally:
        db.close()
