# MeowPay "Send Treats" Slice: Build Plan

Status: agreed scope, pre-build.
Budget: ~4 hours of build. Correctness over surface area.

---

## 1. Stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.12 + FastAPI | Async, Pydantic v2 validation at the HTTP boundary, OpenAPI for free. Structurally close to Spring Boot, so a Kotlin reviewer reads it without friction. |
| ORM / migrations | SQLAlchemy 2.0 (sync) + Alembic | Explicit transaction control and `SELECT ... FOR UPDATE`, which is the whole point of this slice. Sync, not async, because the hard part here is locking semantics and sync sessions make that unambiguous. |
| Database | Postgres 16 | Real transactions, real row locks, real unique constraints. Money needs a database that can say no. |
| Frontend | Next.js 15 (App Router) + TypeScript | Thin. One page. It exists to prove the API is usable, nothing more. |
| Run | Docker Compose | `docker compose up` from a clean clone is the whole setup story. |
| Tests | pytest + httpx | Unit tests for pure domain, integration tests against real Postgres, a threaded concurrency test. |
| Lint | ruff | One tool, zero config debate. |

**On not using Kotlin/Spring.** The brief says ship in whatever is fastest. Python is where I am fastest, and the value of this exercise is in the ledger design, the locking and the edge-case handling, none of which are language-specific. The layering below maps one-to-one onto Spring: routers are controllers, services are `@Service`, repositories are repositories, and the domain package is framework-free.

---

## 2. Data model

Three tables. Money is never a float and never a mutable balance column.

**accounts**
- `id` uuid pk
- `cat_name` text
- `status` enum: `ACTIVE | FROZEN | CLOSED`
- `daily_limit_minor` bigint
- `created_at`, `updated_at`

**transfers** (the request record, one row per attempt, including failures)
- `id` uuid pk
- `idempotency_key` text, **unique**
- `request_fingerprint` text (sha256 of the canonical request body)
- `source_account_id`, `destination_account_id` uuid fk
- `amount_minor` bigint, check `> 0`
- `currency` text, fixed `TREATS` in this slice
- `status` enum: `PENDING | POSTED | FAILED`
- `failure_code`, `failure_reason` nullable
- `created_at`, `completed_at`

**ledger_entries** (double entry, append only)
- `id` uuid pk
- `transfer_id` uuid fk
- `account_id` uuid fk
- `direction` enum: `DEBIT | CREDIT`
- `amount_minor` bigint, check `> 0`
- `created_at`
- index on `(account_id, created_at)`

### Balance is derived, never stored

`balance(account) = SUM(credits) - SUM(debits)` over `ledger_entries`.

There is no `accounts.balance` column, so there is nothing that can drift out of agreement with the ledger. Every posted transfer writes exactly two entries in one transaction: a DEBIT on the source and a CREDIT on the destination, equal in amount. The system-wide invariant is that all entries net to zero, and there is a test that asserts exactly that.

Trade-off to state out loud in the README: at real volume you would not SUM the full history per read. You would add a balance snapshot per account per period, or a cached balance column maintained inside the same transaction plus a reconciliation job. For a slice this size, a derived balance is strictly more correct and I would rather defend correctness than premature caching.

### Money

`amount_minor` is an integer count of **whiskers**; 100 whiskers = 1 treat. No floats, no `Decimal` in storage, no currency conversion. The API accepts an integer only. A float, a numeric string, zero, a negative, or anything above the per-transfer cap is a 422 before it reaches the service layer.

---

## 3. Architecture

```
backend/app/
  api/v1/          HTTP only. Routers, request/response schemas, error mapping.
  services/        Orchestration. Owns the transaction boundary.
  domain/          Pure Python. Money, rules, error codes. No SQLAlchemy, no FastAPI.
  repositories/     All SQL lives here. Nothing above this imports a session directly.
  db/              Engine, session factory, ORM models.
  config.py        Pydantic settings from env.
```

The rule that makes this defensible in a walkthrough: **the domain package imports nothing from the framework or the database**. Every business rule is unit-testable in milliseconds without Postgres running. The service layer is the only place a transaction is opened or committed. Routers never touch a session.

---

## 4. The transfer flow

`POST /v1/transfers` with header `Idempotency-Key: <uuid>`, body `{source_account_id, destination_account_id, amount_minor, currency}`.

1. **Schema validation** (Pydantic). Malformed input dies here, 422, never reaches the DB.
2. **Idempotency lookup.** If the key exists: same fingerprint returns the original transfer, 200. Different fingerprint returns 409 `IDEMPOTENCY_KEY_REUSE`.
3. **Open one DB transaction.**
4. **Lock both accounts** with `SELECT ... FOR UPDATE`, ordered by account id ascending. Consistent lock ordering is what stops A→B and B→A deadlocking against each other.
5. **Insert transfer row as PENDING.** It exists before any decision, so failures are recorded rather than lost.
6. **Run domain rules** against the locked, freshly-read state.
7. **On pass:** insert DEBIT and CREDIT entries, mark POSTED, commit.
   **On fail:** mark FAILED with a reason code, commit that record, return the error. The failed attempt is durable and auditable.

The unique constraint on `idempotency_key` is the real defence. Two identical requests racing each other will have one lose on insert; that path catches the integrity error, re-reads the winner and returns it. The pre-check in step 2 is an optimisation, the constraint is the guarantee.

### Why persist PENDING for a synchronous transfer

Because real rails are not synchronous. Keeping the state machine now means adding an async settlement step later changes the worker, not the schema or the API contract. It also gives failed attempts a durable home, which is what anyone auditing a payment system asks for first.

---

## 5. Rules and error contract

Every error returns the same envelope:

```json
{ "error": { "code": "INSUFFICIENT_FUNDS", "message": "...", "details": {} } }
```

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

### Authentication

Stubbed deliberately: an `X-Cat-Id` header stands in for an authenticated principal, and the service enforces that you may only send from your own account. Real auth is out of scope and the README says so, but the **authorization check exists**, because "anyone can move anyone's money" is not a gap I want a fintech reviewer to find.

---

## 6. Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` | Liveness plus DB ping |
| POST | `/v1/accounts` | Create a cat wallet (dev/seed) |
| GET | `/v1/accounts/{id}` | Status and derived balance |
| GET | `/v1/accounts/{id}/transactions` | Ledger view, newest first, limit/offset |
| POST | `/v1/transfers` | Send treats |
| GET | `/v1/transfers/{id}` | Transfer status and outcome |

---

## 7. Test harness

This is the part being graded, so it gets real attention.

**Unit, no database:** money arithmetic, each rule in isolation, boundary amounts (0, 1, cap, cap+1).

**Integration, real Postgres:** the happy path end to end, then one test per error code in the table above, driven table-style so adding a rule adds a row.

**Idempotency:** replay of an identical request returns the same transfer id and does not double-post; the ledger has exactly two entries afterwards. Same key with a different amount returns 409.

**Concurrency:** 20 threads each try to send 10 treats from an account holding 100. Assert exactly 10 post, 10 fail with `INSUFFICIENT_FUNDS`, the balance is exactly 0, and it is never negative at any point. Separately, A→B and B→A fired in parallel both complete with no deadlock.

**Invariant:** after the entire suite, `SUM(credits) - SUM(debits)` across the whole `ledger_entries` table is zero.

---

## 8. Frontend

One page, `/`. Sender dropdown, recipient dropdown, amount field, submit. Shows both balances and the sender's recent transactions. It generates one idempotency key per submission attempt and **reuses it on retry**, which is the client half of the idempotency story and worth a sentence in the README. It renders the server's error code and message rather than inventing its own copy. Button disables while in flight. That is the whole UI.

---

## 9. Deliberately out of scope

Each of these gets one line in the README explaining the call:

- Real authentication and session management
- Multi-currency and FX
- Reversals, refunds, chargebacks
- Async settlement, outbox pattern, webhooks
- Rate limiting and fraud checks
- Observability beyond structured request logs
- Cursor pagination
- Idempotency key expiry and cleanup

---

## 10. Commit sequence

The history is read, so it should tell the story of the build.

1. `chore: scaffold backend, compose, tooling`
2. `feat(db): accounts, transfers and ledger entries`
3. `feat(domain): money and transfer rules`
4. `feat(api): account endpoints and derived balance`
5. `feat(transfers): post a transfer as double-entry`
6. `feat(transfers): idempotency keys`
7. `feat(transfers): row locking and concurrency test`
8. `feat(transfers): account status and daily limits`
9. `test: edge-case harness and ledger invariant`
10. `feat(web): transfer UI`
11. `docs: README, decisions and trade-offs`

## 11. Time budget

| Phase | Target |
|---|---|
| Scaffold and compose | 30 min |
| Schema and domain | 45 min |
| Transfer service and API | 60 min |
| Edge cases and tests | 60 min |
| Frontend | 30 min |
| Clean-clone verify and README | 25 min |
