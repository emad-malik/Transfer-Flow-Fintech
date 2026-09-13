"""Orchestrates POST /v1/transfers. Owns the transaction boundary end to end.

See README's "The transfer flow" section for the numbered flow this
implements. Routers never touch a Session; this is the only layer that opens
or commits a transaction.
"""

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Account, Transfer
from app.domain.errors import DomainError, ErrorCode
from app.domain.idempotency import compute_fingerprint
from app.domain.rules import AccountSnapshot, TransferRequestInputs, evaluate_transfer
from app.repositories import accounts_repo, ledger_repo, transfers_repo


def _snapshot(db: Session, account: Account) -> AccountSnapshot:
    return AccountSnapshot(
        id=str(account.id),
        status=str(account.status),
        balance_minor=ledger_repo.balance_minor(db, account.id),
        daily_limit_minor=account.daily_limit_minor,
        posted_debits_today_minor=ledger_repo.posted_debits_today_utc(db, account.id),
    )


def execute_transfer(
    db: Session,
    *,
    caller_cat_id: str,
    idempotency_key: str,
    source_account_id: uuid.UUID,
    destination_account_id: uuid.UUID,
    amount_minor: int,
    currency: str,
    expected_currency: str,
    max_amount_minor: int,
) -> Transfer:
    fingerprint = compute_fingerprint(
        source_account_id=source_account_id,
        destination_account_id=destination_account_id,
        amount_minor=amount_minor,
        currency=currency,
    )

    # Step 2: idempotency pre-check. An optimisation, not the guarantee -- the
    # unique constraint on idempotency_key below is what actually prevents a
    # double-post if two identical requests race past this check together.
    #
    # Note on transaction handling: this SELECT is the first statement on this
    # Session, so SQLAlchemy 2.0's session-level autobegin has already opened a
    # transaction by the time we get past it. We do not call `db.begin()`
    # ourselves (it would raise "a transaction is already begun") -- instead we
    # ride that same autobegun transaction through steps 3-7 below and call
    # `db.commit()` / `db.rollback()` explicitly, which is what actually closes
    # it out. `db.begin_nested()` (a SAVEPOINT) is still used around the
    # idempotency-key insert, and nests correctly inside either kind of
    # transaction.
    existing = transfers_repo.get_by_idempotency_key(db, idempotency_key)
    if existing is not None:
        db.rollback()  # nothing to commit; ends the read-only transaction cleanly
        if existing.request_fingerprint != fingerprint:
            raise DomainError(
                ErrorCode.IDEMPOTENCY_KEY_REUSE,
                "This idempotency key was already used with a different request body.",
            )
        return existing

    domain_error: DomainError | None = None

    # Steps 3-7, all on the transaction the session already opened above.
    try:
        # Step 4: lock both accounts, ascending id order, deadlock-safe.
        locked = accounts_repo.lock_pair_ordered(db, source_account_id, destination_account_id)
        source = locked.get(source_account_id)
        destination = locked.get(destination_account_id)

        if source is None or destination is None:
            # No transfer row is created: there is no valid FK target to attach it
            # to, so this is a pre-condition failure, not a recorded attempt --
            # same bucket as a malformed request that never reaches the DB at all.
            missing = "source_account_id" if source is None else "destination_account_id"
            raise DomainError(
                ErrorCode.ACCOUNT_NOT_FOUND,
                "One or both accounts do not exist.",
                details={"missing": missing},
            )

        # Step 5: insert PENDING, guarding the unique idempotency_key race with a
        # SAVEPOINT so a failed insert doesn't lose the locks the outer
        # transaction is already holding.
        try:
            with db.begin_nested():
                transfer = transfers_repo.create_pending(
                    db,
                    idempotency_key=idempotency_key,
                    request_fingerprint=fingerprint,
                    source_account_id=source_account_id,
                    destination_account_id=destination_account_id,
                    amount_minor=amount_minor,
                    currency=currency,
                )
        except IntegrityError:
            # Lost the race: someone else committed this key first while we were
            # waiting on the account locks. Re-read and resolve exactly like the
            # pre-check above.
            winner = transfers_repo.get_by_idempotency_key(db, idempotency_key)
            if winner is None or winner.request_fingerprint != fingerprint:
                raise DomainError(
                    ErrorCode.IDEMPOTENCY_KEY_REUSE,
                    "This idempotency key was already used with a different request body.",
                ) from None
            db.commit()
            return winner

        # Step 6: run domain rules against the locked, freshly-read state.
        try:
            evaluate_transfer(
                TransferRequestInputs(
                    caller_cat_id=caller_cat_id,
                    source=_snapshot(db, source),
                    destination=_snapshot(db, destination),
                    amount_minor=amount_minor,
                    currency=currency,
                    expected_currency=expected_currency,
                    max_amount_minor=max_amount_minor,
                )
            )
        except DomainError as exc:
            # Step 7 (fail path): record the failure and keep it. Commit below,
            # then re-raise afterwards -- raising here instead would roll back
            # this same mark_failed write along with it.
            transfers_repo.mark_failed(db, transfer, code=exc.code.value, reason=exc.message)
            domain_error = exc
        else:
            # Step 7 (pass path): the only place ledger_entries gets written.
            ledger_repo.insert_double_entry(
                db,
                transfer_id=transfer.id,
                source_account_id=source_account_id,
                destination_account_id=destination_account_id,
                amount_minor=amount_minor,
            )
            transfers_repo.mark_posted(db, transfer)

        db.commit()
    except Exception:
        db.rollback()
        raise

    # Transaction has committed. Only now do we re-raise, so a FAILED row is
    # durable before the caller ever sees the error.
    if domain_error is not None:
        raise domain_error

    return transfer
