"""Pure transfer rules. No SQLAlchemy, no FastAPI, no I/O.

Every function here takes primitives (ints, strings, enums) that the service layer
has already read from locked rows, and raises DomainError on the first rule that
fails. This is what makes the whole rule set unit-testable in milliseconds without
Postgres running: the service layer's job is only to fetch state and call this.

Rule order matters and is intentional: cheap, request-shaped checks (self-transfer,
amount, currency) run before anything that depends on locked account state, so a
malformed request never even needs a lock.
"""

from dataclasses import dataclass

from app.domain.errors import DomainError, ErrorCode
from app.domain.money import is_valid_amount


@dataclass(frozen=True)
class AccountSnapshot:
    id: str
    status: str  # "ACTIVE" | "FROZEN" | "CLOSED"
    balance_minor: int
    daily_limit_minor: int
    posted_debits_today_minor: int


@dataclass(frozen=True)
class TransferRequestInputs:
    caller_cat_id: str
    source: AccountSnapshot
    destination: AccountSnapshot
    amount_minor: int
    currency: str
    expected_currency: str
    max_amount_minor: int


def check_authorized(caller_cat_id: str, source_account_id: str) -> None:
    if caller_cat_id != source_account_id:
        raise DomainError(
            ErrorCode.UNAUTHORIZED_SOURCE,
            "You can only send treats from your own account.",
        )


def check_not_self_transfer(source_account_id: str, destination_account_id: str) -> None:
    if source_account_id == destination_account_id:
        raise DomainError(ErrorCode.SELF_TRANSFER, "Source and destination must differ.")


def check_amount(amount_minor: int, max_amount_minor: int) -> None:
    if not is_valid_amount(amount_minor) or amount_minor <= 0:
        raise DomainError(ErrorCode.AMOUNT_NOT_POSITIVE, "amount_minor must be a positive integer.")
    if amount_minor > max_amount_minor:
        raise DomainError(
            ErrorCode.AMOUNT_ABOVE_MAX,
            f"amount_minor exceeds the per-transfer cap of {max_amount_minor}.",
            details={"max_amount_minor": max_amount_minor},
        )


def check_currency(currency: str, expected_currency: str) -> None:
    if currency != expected_currency:
        raise DomainError(
            ErrorCode.CURRENCY_MISMATCH,
            f"Only {expected_currency} is supported in this slice.",
        )


def check_account_active(status: str, *, role: str) -> None:
    if status != "ACTIVE":
        code = (
            ErrorCode.SOURCE_ACCOUNT_NOT_ACTIVE
            if role == "source"
            else ErrorCode.DESTINATION_ACCOUNT_NOT_ACTIVE
        )
        raise DomainError(code, f"{role.capitalize()} account is {status.lower()}, not active.")


def check_sufficient_funds(balance_minor: int, amount_minor: int) -> None:
    if balance_minor < amount_minor:
        raise DomainError(
            ErrorCode.INSUFFICIENT_FUNDS,
            "Source account balance is lower than the transfer amount.",
            details={"balance_minor": balance_minor},
        )


def check_daily_limit(
    posted_debits_today_minor: int, amount_minor: int, daily_limit_minor: int
) -> None:
    if posted_debits_today_minor + amount_minor > daily_limit_minor:
        raise DomainError(
            ErrorCode.DAILY_LIMIT_EXCEEDED,
            "This transfer would exceed the source account's daily limit.",
            details={
                "daily_limit_minor": daily_limit_minor,
                "posted_debits_today_minor": posted_debits_today_minor,
            },
        )


def evaluate_transfer(inputs: TransferRequestInputs) -> None:
    """Run every rule in order. Raises the first DomainError encountered."""
    check_not_self_transfer(inputs.source.id, inputs.destination.id)
    check_amount(inputs.amount_minor, inputs.max_amount_minor)
    check_currency(inputs.currency, inputs.expected_currency)
    check_authorized(inputs.caller_cat_id, inputs.source.id)
    check_account_active(inputs.source.status, role="source")
    check_account_active(inputs.destination.status, role="destination")
    check_sufficient_funds(inputs.source.balance_minor, inputs.amount_minor)
    check_daily_limit(
        inputs.source.posted_debits_today_minor,
        inputs.amount_minor,
        inputs.source.daily_limit_minor,
    )
