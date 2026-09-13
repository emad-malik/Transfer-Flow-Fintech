# MeowPay: Send Treats

A small, correctness-first slice of a payments feature: move "treats" (the
in-app currency) between cat wallets, with double-entry ledgering, row
locking, and idempotent retries. The plan had three gaps I found while
building this; they're covered below along with how I closed them.

## Running it

```
docker compose up --build
```

- Backend: http://localhost:8000 (docs at `/docs`)
- Frontend: http://localhost:3000
- Postgres: localhost:5432 (user/pass/db: `meowpay`)

The backend container runs `alembic upgrade head` before serving, so a clean
`docker compose up` ends with a migrated and seeded database (see "Funding
the system" below). No manual setup step needed.

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

FastAPI, SQLAlchemy 2.0 (sync, not async), Postgres, and Alembic on the
backend. Next.js 15 for a single thin page on the frontend. pytest against a
real Postgres for tests, ruff for lint.

Sync SQLAlchemy is the one choice worth explaining. The point of this
exercise is locking semantics: `SELECT ... FOR UPDATE`, transaction
boundaries, deadlock ordering. An async session makes that harder to reason
about without buying anything at this scale. If this needed to handle real
production throughput, that's the choice I'd revisit first, not the
language.

## Architecture

```
backend/app/
  api/v1/          HTTP only: routers, Pydantic schemas, error-envelope mapping.
  services/        Orchestration. Owns the transaction boundary.
  domain/          Pure Python: money, rules, error codes. No SQLAlchemy, no FastAPI.
  repositories/     All SQL. Nothing above this imports a Session directly.
  db/              Engine, session factory, ORM models.
```

`domain/` imports nothing from FastAPI or SQLAlchemy, which is what lets
`tests/unit/` run in well under a second with no database at all. Every
transfer rule, the idempotency fingerprint, and the money helpers are tested
there in isolation. `services/transfer_service.py` is the only place a
transaction is opened or committed; routers never touch a `Session`.

## The ledger

Balance is never stored. `balance(account) = SUM(credits) - SUM(debits)` over
`ledger_entries`, computed fresh on every read (`repositories/ledger_repo.py`).
There's no `accounts.balance` column, so there's nothing that can drift out
of sync with the ledger: every posted transfer writes exactly one DEBIT and
one CREDIT, equal in amount, in the same transaction.
`tests/test_zz_ledger_invariant.py` checks that `SUM(credits) == SUM(debits)`
across the entire table after the whole suite runs.

This doesn't scale forever. At real volume, summing the full history on
every read gets slow, and you'd want a periodic balance snapshot plus
reconciliation, or a cached balance column written in the same transaction
as the ledger entry. At this size, though, a derived balance is simpler and
harder to get wrong, so that's what I built.

## The transfer flow

`POST /v1/transfers`, header `Idempotency-Key: <uuid>`, header
`X-Cat-Id: <account id>` standing in for an authenticated principal (see
"What's stubbed" below). The flow, as implemented in
`services/transfer_service.py`:

1. Compute a sha256 fingerprint of `{source, destination, amount, currency}`.
2. Idempotency pre-check: same key and same fingerprint already recorded,
   **replay the original outcome** -- 200 with the stored transfer if it
   POSTED, the same error code/status it returned the first time if it
   FAILED (409 `INSUFFICIENT_FUNDS` stays 409, not a 200 with `status:
   FAILED` buried in the body, which a naive retrying client would read as
   success). Same key with a different fingerprint, 409
   `IDEMPOTENCY_KEY_REUSE`. This pre-check is an optimisation; the real
   guarantee is the unique constraint on `(transfers.caller_cat_id,
   transfers.idempotency_key)`, enforced via a SAVEPOINT around the insert
   so two requests racing past the pre-check still can't double-post (see
   the docstring in `transfer_service.py` for the exact interleaving this
   defends against). The constraint is scoped to the caller, not global:
   idempotency keys are client-generated with no coordination between
   callers, so a bare-unique column would let one caller's key collide with
   an unrelated caller's request.
3. Lock both accounts with `SELECT ... FOR UPDATE`, always in ascending id
   order. That ordering is what stops A->B and B->A transfers from
   deadlocking each other, and it's checked by a real multi-threaded test
   against real Postgres (`tests/concurrency/test_concurrent_transfers.py`),
   not just asserted in a comment.
4. Insert the transfer row as `PENDING` before evaluating any rule, so a
   rule failure is a durable, auditable `FAILED` row rather than a silently
   dropped request.
5. Run every rule in `domain/rules.py` against the locked, freshly-read
   state, authorization first -- a caller who isn't the source shouldn't be
   able to distinguish error codes on an account that isn't theirs by
   probing self-transfer, amount, or currency checks first. Pass: write the
   DEBIT/CREDIT pair, mark `POSTED`, commit. Fail: mark `FAILED` with the
   rule's error code and message, commit that, then re-raise the error to
   the caller. The failure is on disk before the caller ever sees the
   409/422, and its id rides in the error's `details.transfer_id` either
   way, so it's still fetchable by `GET /v1/transfers/{id}` afterward.

One asymmetry worth flagging: `ACCOUNT_NOT_FOUND`, `MISSING_IDEMPOTENCY_KEY`,
and `IDEMPOTENCY_KEY_REUSE` happen before a transfer row exists, not after.
`transfers.source_account_id` and `destination_account_id` are foreign keys,
so there's no valid row to attach a "the destination doesn't exist" failure
to. It falls in the same bucket as a malformed request that never reaches
the database. Every other rule in the table runs against a real `PENDING`
row, and its failure gets persisted.

`transfers.amount_minor` also has no positive-amount CHECK constraint, even
though `ledger_entries.amount_minor` does. A `transfers` row is the audit
record of an attempt, including a zero or negative amount that needs to be
persisted as `FAILED` / `AMOUNT_NOT_POSITIVE`. A DB constraint blocking that
insert would work against the "always persist the attempt" design.
`ledger_entries` represents money that actually moved, and that table still
can't hold a non-positive amount.

## Error contract

Every error is `{"error": {"code", "message", "details"}}`. Each row below
has a dedicated integration test in
`tests/integration/test_transfers_errors.py`. Adding a rule later means
adding a row there, not a new test shape.

| Code | HTTP | Rule |
|---|---|---|
| `VALIDATION_ERROR` | 422 | Malformed body, non-integer amount, missing field, unknown currency |
| `MISSING_IDEMPOTENCY_KEY` | 400 | Header absent on POST /transfers |
| `IDEMPOTENCY_KEY_REUSE` | 409 | Key seen before with a different body |
| `ACCOUNT_NOT_FOUND` | 404 | Either side does not exist |
| `SELF_TRANSFER` | 422 | Source equals destination |
| `AMOUNT_NOT_POSITIVE` | 422 | Zero or negative |
| `AMOUNT_ABOVE_MAX` | 422 | Above the per-transfer cap |
| `SOURCE_ACCOUNT_NOT_ACTIVE` | 409 | Source frozen or closed |
| `DESTINATION_ACCOUNT_NOT_ACTIVE` | 409 | Destination frozen or closed |
| `INSUFFICIENT_FUNDS` | 409 | Derived balance below amount |
| `DAILY_LIMIT_EXCEEDED` | 409 | Today's posted debits plus amount exceeds the account limit |
| `CURRENCY_MISMATCH` | 422 | Anything other than TREATS |
| `UNAUTHORIZED_SOURCE` | 403 | Caller is not the source account holder |

## Where the plan turned out to be incomplete

I found three gaps once I actually ran the API end to end, not just read the
plan. Each one needed a small addition beyond the plan's original scope,
noted here rather than left for someone to stumble on in a diff.

**1. There was no way to fund the first wallet.** Balance is strictly
derived from `ledger_entries`, so a brand-new account starts at zero and can
only be funded by a real transfer into it, which needs a source account
that already has a balance. Nothing does, on a fresh database. I resolved
this the way real payment systems do: two system accounts, seeded by
migration `0002_seed_system_accounts.py`:

- `MeowPay External Funding` (`...002`): stands in for money entering from
  outside the system. Seeded deeply negative on purpose.
- `MeowPay Treasury` (`...001`): the funded wallet other accounts draw from.

The migration inserts one ordinary, balanced DEBIT/CREDIT pair between
them, not a special-cased unbalanced INSERT, so the "credits equal debits
globally" invariant holds from the first migration, not just after the app
has been running a while. Every rule in `domain/rules.py` treats these as
ordinary accounts. `EXTERNAL_FUNDING` can't be misused as a second
free-money tap through the API because it's already negative: a normal
transfer from it fails `INSUFFICIENT_FUNDS` like any other overdrawn
account, no special-case code needed. Fund a new demo account by sending a
transfer from Treasury (`00000000-0000-0000-0000-000000000001`).

**2. `FROZEN` and `CLOSED` were unreachable.** The plan defines both
statuses, and two error codes depend on them, but none of the six listed
endpoints can set an account to either state. They'd only be reachable by
editing the database directly, which would make `SOURCE_ACCOUNT_NOT_ACTIVE`
and `DESTINATION_ACCOUNT_NOT_ACTIVE` dead code from the API's point of view.
I added `PATCH /v1/accounts/{id}/status`, unauthenticated like account
creation, and marked it in its own docstring as a dev/admin addition
outside the original endpoint list.

**3. The frontend's sender/recipient dropdowns had nothing to populate them
from.** The plan's endpoint list has no way to list accounts at all. I
added `GET /v1/accounts` (paginated, same shape as the transactions list),
for the same reason as #2: the feature can't be demoed without it.

All three are called out in their own code comments (`system_accounts.py`,
the status router, the list-accounts router), so they're easy to find and
easy to argue with.

## What's stubbed, and why

- **Auth.** `X-Cat-Id` stands in for an authenticated principal. The one
  thing that is enforced, on purpose, is authorization: the service checks
  that the caller equals the source account, because "anyone can move
  anyone's money" is not a gap a fintech reviewer should find, stub auth or
  not.
- **Multi-currency / FX.** `currency` is a real field, checked, but only one
  value (`TREATS`) is accepted. Real FX would touch the money
  representation, not just add a rule, so it's out of scope rather than
  half-done.
- **Reversals, refunds, chargebacks.** None of these are "add a rule." They
  are new domain concepts (a transfer that references another transfer)
  that deserve their own design, not a bolt-on.
- **Async settlement / outbox / webhooks.** The `PENDING` status exists so
  this can be added later without a schema change. Real payment rails
  aren't synchronous, so keeping that state machine now means an async
  settlement step later only touches a worker, not the schema or the API
  contract. No worker exists in this slice; everything resolves
  synchronously inside the request.
- **Rate limiting, fraud checks, deeper observability (metrics, tracing),
  cursor pagination, idempotency key expiry/cleanup.** Out of scope from the
  start. Basic structured logging is not, though: `RequestLoggingMiddleware`
  in `main.py` logs one line per request (request id, caller, transfer id
  when the request created one, status, duration), and the `DomainError`
  handler logs each rejection's code -- enough to answer "who did what, and
  what happened" from logs, which is what the auditability argument actually
  needs, without building a metrics/tracing stack for a take-home slice.

## Money

`amount_minor` is an integer count of "whiskers"; 100 whiskers = 1 treat. No
floats or `Decimal` anywhere in storage or arithmetic. The per-transfer cap
(`TRANSFER_MAX_AMOUNT_MINOR`, default 1,000,000 whiskers = 10,000 treats) is
a fat-finger guard, not a risk control, and is a config value for that
reason: it's meant to be tuned per deployment, not hardcoded.

## Frontend

One page (`frontend/app/page.tsx`). It generates one idempotency key per
"attempt" (a new key whenever sender, recipient, or amount changes) and
reuses it whenever the catch block sees a plain `NETWORK_ERROR` rather than
a parsed `ApiError`, since that's exactly the case an idempotency key exists
for: not knowing if the server saw the first request. A definitive business
or validation error (bad amount, insufficient funds, frozen account) gets a
fresh key on the next submit, since the user is about to send a materially
different request by fixing the input, not retrying the same one.

On the mechanism, not just the policy: a `500` from FastAPI's catch-all
exception handler is served by Starlette's `ServerErrorMiddleware`, which
sits *outside* `CORSMiddleware`, so the response never carries the app's CORS
headers. The browser's `fetch` sees a CORS-blocked response and throws before
`page.tsx` ever gets a body to parse -- there is no path where the code reads
`err.code === "INTERNAL_ERROR"` from a real 500 in the browser. It still
works, because that `fetch` rejection is caught by the same `catch` block as
any other network failure and lands in the generic `NETWORK_ERROR` branch,
which also retains the key. The retry policy is correct; a 500 just never
takes the branch its own error code suggests it would.

The amount input parses treats as a decimal string into integer minor units
without ever going through a float (`parseTreatsToMinor` in `page.tsx`), so
client-side rounding can't disagree with the server's integer-only contract.
`formatTreats` (display only, `lib/api.ts`) handles a negative `amount_minor`
by formatting the magnitude and prefixing the sign, rather than relying on
`Math.trunc`'s and `%`'s behavior near zero, which drops the sign entirely
for values between -1 and 0 treats. The sender/recipient pickers filter out
system accounts (`Account.is_system`, set by the backend from the fixed
Treasury/External Funding ids) so a fresh clone doesn't default to sending
from an account sitting around -1,000,000 treats.

## Known limitations, on top of the explicit scope cuts

- The daily limit resets at a fixed UTC midnight boundary, not per-account
  timezone.
- `GET /v1/accounts` has no filter beyond pagination; fine at demo scale,
  not meant to survive a real account base.
- The account-status and list-accounts endpoints (additions #2 and #3
  above) are unauthenticated by design, matching account creation. That's a
  dev-tool decision that would need real admin auth before shipping.