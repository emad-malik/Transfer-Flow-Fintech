"""Orchestrates POST /v1/transfers. Owns the transaction boundary end to end.

See README's "The transfer flow" section for the numbered flow this
implements. Routers never touch a Session; this is the only layer that opens
or commits a transaction.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Account, Transfer, TransferStatus
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


@dataclass(frozen=True)
class _TransferSnapshot:
    """Plain-value copy of the fields the idempotency paths below need, read
    while the row is still fresh. SQLAlchemy expires ORM instances on both
    rollback and commit, so an attribute touched afterwards silently issues
    another SELECT and reopens a transaction that only closes when get_db()
    closes the session -- reading into locals first avoids that round-trip.
    """

    transfer_id: str
    fingerprint: str
    status: str
    failure_code: str | None
    failure_reason: str | None


def _snapshot_before_expiry(transfer: Transfer) -> _TransferSnapshot:
    return _TransferSnapshot(
        transfer_id=str(transfer.id),
        fingerprint=transfer.request_fingerprint,
        status=transfer.status,
        failure_code=transfer.failure_code,
        failure_reason=transfer.failure_reason,
    )


def _replay_result(transfer: Transfer, snapshot: _TransferSnapshot) -> Transfer:
    """A replayed idempotent lookup must reproduce the *original outcome*, not
    just the original row. If the stored attempt FAILED, replay the same
    error (code, message, and therefore HTTP status) the first call returned
    -- a bare 200 with status: FAILED buried in the body is exactly what a
    naive retrying client reads as success. See README's error contract.
    transfer_id rides along in details so a caller can still look up the
    durable record after a replay, the same as it could after a fresh failure.
    """
    if snapshot.status == TransferStatus.FAILED.value:
        raise DomainError(
            ErrorCode(snapshot.failure_code),
            snapshot.failure_reason or "",
            details={"transfer_id": snapshot.transfer_id},
        )
    return transfer


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
    existing = transfers_repo.get_by_idempotency_key(db, caller_cat_id, idempotency_key)
    if existing is not None:
        snapshot = _snapshot_before_expiry(existing)  # read before rollback, see above
        db.rollback()  # nothing to commit; ends the read-only transaction cleanly
        if snapshot.fingerprint != fingerprint:
            raise DomainError(
                ErrorCode.IDEMPOTENCY_KEY_REUSE,
                "This idempotency key was already used with a different request body.",
            )
        return _replay_result(existing, snapshot)

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
                    caller_cat_id=caller_cat_id,
                    idempotency_key=idempotency_key,
                    request_fingerprint=fingerprint,
                    source_account_id=source_account_id,
                    destination_account_id=destination_account_id,
                    amount_minor=amount_minor,
                    currency=currency,
                )
            # Assigned by create_pending() before insert, not read back from
            # the row -- safe to use after the commit below expires `transfer`.
            transfer_id = str(transfer.id)
        except IntegrityError:
            # Lost the race: someone else committed this key first while we were
            # waiting on the account locks. Re-read and resolve exactly like the
            # pre-check above.
            winner = transfers_repo.get_by_idempotency_key(db, caller_cat_id, idempotency_key)
            if winner is None or winner.request_fingerprint != fingerprint:
                raise DomainError(
                    ErrorCode.IDEMPOTENCY_KEY_REUSE,
                    "This idempotency key was already used with a different request body.",
                ) from None
            snapshot = _snapshot_before_expiry(winner)  # read before commit expires it
            db.commit()
            return _replay_result(winner, snapshot)

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
            # this same mark_failed write along with it. transfer_id rides in
            # details so the caller can look up the durable FAILED record.
            transfers_repo.mark_failed(db, transfer, code=exc.code.value, reason=exc.message)
            domain_error = DomainError(
                exc.code, exc.message, details={**exc.details, "transfer_id": transfer_id}
            )
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
