# MeowPay -- Send Treats

A small, correctness-first slice of a payments feature: move "treats" (the
in-app currency) between cat wallets, with double-entry ledgering, row
locking, and idempotent retries. Built against the plan in `PLAN.md`; this
README is the record of what actually happened while building it, including
three places the plan turned out to be incomplete and how I closed them.

## Running it

```
docker compose up --build
```

- Backend: http://localhost:8000 (docs at `/docs`)
- Frontend: http://localhost:3000
- Postgres: localhost:5432 (user/pass/db: `meowpay`)

The backend container runs `alembic upgrade head` before serving, so a clean
`docker compose up` ends with a migrated *and seeded* database (see "Funding
the system" below) -- no manual setup step.

To run the backend test suite locally (not inside Docker, since it uses a
second local Postgres database, `meowpay_test`, for speed):

```
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
createdb meowpay_test   # once
export DATABASE_URL=postgresql+psycopg://meowpay:meowpay@localhost:5432/meowpay_test
pytest
ruff check .
```

## Stack and why

See `PLAN.md` section 1 for the full table. The short version: FastAPI +
SQLAlchemy 2.0 (sync, not async) + Postgres + Alembic on the backend, Next.js
15 for a single thin page on the frontend, pytest against a real Postgres for
tests. Sync SQLAlchemy was the one non-default choice worth restating here:
the entire point of this exercise is locking semantics
(`SELECT ... FOR UPDATE`, transaction boundaries, deadlock ordering), and an
async session makes exactly that harder to reason about for no benefit at this
scale. I'd revisit that choice, not the language, if this needed to survive
real production throughput.

## Architecture

```
backend/app/
  api/v1/          HTTP only: routers, Pydantic schemas, error-envelope mapping.
  services/        Orchestration. Owns the transaction boundary.
  domain/          Pure Python: money, rules, error codes. No SQLAlchemy, no FastAPI.
  repositories/     All SQL. Nothing above this imports a Session directly.
  db/              Engine, session factory, ORM models.
```

`domain/` imports nothing from FastAPI or SQLAlchemy, which is what makes
`tests/unit/` run in well under a second with no database at all -- every
transfer rule, the idempotency fingerprint, and the money helpers are tested
there in isolation. `services/transfer_service.py` is the only place a
transaction is opened or committed; routers never touch a `Session`.

## The ledger

Balance is never stored. `balance(account) = SUM(credits) - SUM(debits)` over
`ledger_entries`, computed fresh on every read (`repositories/ledger_repo.py`).
There is no `accounts.balance` column, so there is nothing that can drift out
of agreement with the ledger -- every posted transfer writes exactly one
DEBIT and one CREDIT, equal in amount, in the same transaction.
`tests/test_zz_ledger_invariant.py` asserts that `SUM(credits) == SUM(debits)`
across the *entire* table after the whole suite has run.

**Tradeoff, stated plainly:** at real volume you would not `SUM` the full
history on every balance read. You'd add a periodic balance snapshot per
account plus incremental reconciliation, or a cached balance column
maintained inside the same transaction as the ledger write. For a slice this
size, a derived balance is strictly more correct, and I'd rather defend
correctness than premature caching in a review.

## The transfer flow

`POST /v1/transfers`, header `Idempotency-Key: <uuid>`, header
`X-Cat-Id: <account id>` standing in for an authenticated principal (see
"What's stubbed" below). Full numbered flow in `PLAN.md` section 4; the
short version, as implemented in `services/transfer_service.py`:

1. Compute a sha256 fingerprint of `{source, destination, amount, currency}`.
2. Idempotency pre-check: same key + same fingerprint already recorded ->
   return the stored transfer, 200. Same key + different fingerprint -> 409
   `IDEMPOTENCY_KEY_REUSE`. This is an optimisation; the guarantee is the
   unique constraint on `transfers.idempotency_key`, exercised via a
   SAVEPOINT around the insert so two requests racing past the pre-check
   still can't double-post (see the docstring in `transfer_service.py` for the
   exact interleaving this defends against).
3. Lock both accounts with `SELECT ... FOR UPDATE`, always in ascending id
   order -- this is what stops A->B and B->A from deadlocking each other, and
   it's exercised by a real multi-threaded test against real Postgres
   (`tests/concurrency/test_concurrent_transfers.py`), not just asserted in a
   comment.
4. Insert the transfer row as `PENDING` *before* evaluating any rule, so a
   rule failure is a durable, auditable `FAILED` row rather than a silently
   dropped request.
5. Run every rule in `domain/rules.py` against the locked, freshly-read state.
   Pass: write the DEBIT/CREDIT pair, mark `POSTED`, commit. Fail: mark
   `FAILED` with the rule's error code and message, commit *that*, then
   re-raise the error to the caller -- the failure is on disk before the
   caller ever sees the 409/422.

One deliberate asymmetry: `ACCOUNT_NOT_FOUND`, `MISSING_IDEMPOTENCY_KEY`, and
`IDEMPOTENCY_KEY_REUSE` happen *before* a transfer row exists, not after.
`transfers.source_account_id` / `destination_account_id` are foreign keys, so
there's no valid row to attach a "the destination doesn't exist" failure to --
same bucket as a malformed request that never reaches the database at all.
Every other rule in the table runs against a real `PENDING` row and its
failure is persisted.

Also deliberate: `transfers.amount_minor` has **no** positive-amount CHECK
constraint, even though `ledger_entries.amount_minor` does. A `transfers` row
is the audit record of an *attempt*, including one with a zero or negative
amount that needs to be persisted as `FAILED` / `AMOUNT_NOT_POSITIVE` -- a DB
constraint blocking that insert would contradict the "always persist the
attempt" design. `ledger_entries` is the table that represents money that
actually moved, and that one still can't hold a non-positive amount.

## Error contract

Every error is `{"error": {"code", "message", "details"}}`. The full table of
codes is in `PLAN.md` section 5 and each row has a dedicated integration test
in `tests/integration/test_transfers_errors.py` -- adding a rule later means
adding a row there, not inventing a new test shape.

## Where the plan turned out to be incomplete

Three gaps only became visible once I tried to actually exercise the API
end to end. Each is a small, deliberate addition beyond the plan's original
scope, called out here rather than buried in a diff.

**1. There was no way to fund the first wallet.** Balance is strictly derived
from `ledger_entries`, so a brand-new account starts at zero and can only be
funded by a real transfer *into* it -- which needs a source account that
already has a balance. Nothing does, on a fresh database. I resolved this the
way real payment systems do: two system accounts, seeded by migration
`0002_seed_system_accounts.py`:

- `MeowPay External Funding` (`...002`) -- stands in for money entering from
  outside the system. Seeded deeply negative on purpose.
- `MeowPay Treasury` (`...001`) -- the funded wallet other accounts draw from.

The migration inserts one ordinary, balanced DEBIT/CREDIT pair between them --
not a special-cased unbalanced `INSERT` -- so the "credits equal debits
globally" invariant holds from the very first migration, not just after the
app has been used for a while. Every rule in `domain/rules.py` treats these
as perfectly ordinary accounts; the only reason `EXTERNAL_FUNDING` can't be
misused as a second free-money tap through the API is that it's already
negative, so a normal transfer *from* it fails `INSUFFICIENT_FUNDS` like any
other overdrawn account -- no special-case code required. Fund a new demo
account by sending a transfer from Treasury (`00000000-0000-0000-0000-000000000001`).

**2. `FROZEN` and `CLOSED` were unreachable.** The plan defines both statuses
and two error codes that depend on them, but none of the six listed endpoints
can ever set an account to either state -- they'd only be reachable by
editing the database directly, which would make `SOURCE_ACCOUNT_NOT_ACTIVE`
and `DESTINATION_ACCOUNT_NOT_ACTIVE` dead code from the API's point of view.
Added `PATCH /v1/accounts/{id}/status`, unauthenticated like account
creation, explicitly marked in its own docstring as a dev/admin addition
outside the original endpoint list.

**3. The frontend's sender/recipient dropdowns had nothing to populate them
from.** The plan's endpoint list has no way to list accounts at all. Added
`GET /v1/accounts` (paginated, same shape as the transactions list) for the
same reason as #2: the feature as specified can't actually be demoed
without it.

All three are called out in their own code comments (`system_accounts.py`,
the status router, the list-accounts router) so they're easy to find and
easy to argue with.

## What's stubbed, and why

- **Auth.** `X-Cat-Id` stands in for an authenticated principal. The one
  thing that *is* enforced, on purpose, is authorization: the service checks
  that the caller equals the source account, because "anyone can move
  anyone's money" is not a gap a fintech reviewer should find, stub auth or
  not.
- **Multi-currency / FX.** `currency` is a real field, checked, but only one
  value (`TREATS`) is accepted. Wiring real FX would touch the money
  representation, not just add a rule, so it's out of scope rather than
  half-done.
- **Reversals, refunds, chargebacks.** None of these are "add a rule" --
  they're new domain concepts (a transfer that references another transfer)
  that deserve their own design, not a bolt-on.
- **Async settlement / outbox / webhooks.** The `PENDING` status exists
  specifically so this can be added later without a schema change -- see the
  "why persist PENDING" note in `PLAN.md` section 4 -- but no worker exists
  in this slice; everything resolves synchronously inside the request.
- **Rate limiting, fraud checks, observability beyond structured logs, cursor
  pagination, idempotency key expiry/cleanup.** Explicitly out of scope per
  the original plan; nothing here changed that.

## Money

`amount_minor` is an integer count of "whiskers"; 100 whiskers = 1 treat. No
floats or `Decimal` anywhere in storage or arithmetic. The per-transfer cap
(`TRANSFER_MAX_AMOUNT_MINOR`, default 1,000,000 whiskers = 10,000 treats) is a
fat-finger guard, not a risk control, and is a config value for exactly that
reason -- it's meant to be tuned per deployment, not hardcoded.

## Frontend

One page (`frontend/app/page.tsx`). It generates one idempotency key per
"attempt" (new key whenever sender, recipient, or amount changes) and reuses
it if a submit comes back as a network failure or a `500` -- the case an
idempotency key exists for is exactly "I don't know if the server saw my
first request." A definitive business or validation error (bad amount,
insufficient funds, frozen account) issues a fresh key on the next submit,
since the user is about to send a materially different request by fixing the
input, not retrying the same one. The amount input parses treats as a decimal
string into integer minor units without ever going through a float
(`parseTreatsToMinor` in `page.tsx`), so client-side rounding can't disagree
with the server's integer-only contract.

## Known limitations, on top of the explicit scope cuts

- The daily limit resets at a fixed UTC midnight boundary, not per-account
  timezone -- stated once, plainly, rather than silently assumed.
- `GET /v1/accounts` has no filter beyond pagination; fine at demo scale, not
  meant to survive a real account base.
- The account-status and list-accounts endpoints (additions #2 and #3 above)
  are unauthenticated by design, matching account creation -- this is a
  dev-tool decision that would need real admin auth before shipping.
