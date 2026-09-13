"""Fixed ids for the two system accounts created by migration 0002.

Why these exist at all: balance is strictly derived from ledger_entries (see
db/models.py), so a brand-new account is funded only by a real transfer into it.
That means the very first treats in the system have to come from *somewhere*
that isn't a customer account -- otherwise there is no way to fund the first
wallet without special-casing the ledger invariant.

The fix keeps the invariant intact instead of poking a hole in it: seed one
balanced pair, exactly like any other transfer, with one system account
standing in for "the outside world" (EXTERNAL_FUNDING, which the migration
drives deeply negative on purpose) and one standing in for spendable float
(TREASURY, which ends up with a large positive balance). Every treat any demo
account ever receives can be traced back to that one seeded transfer, and
`SUM(credits) - SUM(debits) == 0` holds globally from the very first migration.

Both are ordinary rows in `accounts` -- no rule in domain/rules.py special-cases
them. TREASURY works as a funding source only because it has a real positive
balance and a daily_limit_minor large enough not to bite; EXTERNAL_FUNDING is
never used again after the seed migration (a normal transfer *from* it through
the API would immediately fail INSUFFICIENT_FUNDS, since it is already
negative -- that is what stops it from being misused as a second free-money
tap, with no special-case code required).

Fixed, well-known UUIDs (rather than querying for them by name) so the seed
script, the frontend, and the README can all refer to TREASURY_ACCOUNT_ID as a
constant without a lookup.
"""

import uuid

TREASURY_ACCOUNT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
EXTERNAL_FUNDING_ACCOUNT_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")

# 1,000,000 treats. Arbitrary but generous for a demo; see migration 0002.
SEED_AMOUNT_MINOR = 100_000_000
