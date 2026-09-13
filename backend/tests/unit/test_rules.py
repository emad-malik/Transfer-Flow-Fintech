"""Every rule tested in isolation, then evaluate_transfer's ordering. No DB, no
FastAPI -- these run in milliseconds, which is the entire point of keeping
domain/ framework-free.
"""

import pytest

from app.domain.errors import DomainError, ErrorCode
from app.domain.rules import (
    AccountSnapshot,
    TransferRequestInputs,
    check_account_active,
    check_amount,
    check_authorized,
    check_currency,
    check_daily_limit,
    check_not_self_transfer,
    check_sufficient_funds,
    evaluate_transfer,
)

MAX_AMOUNT = 1_000_000


def account(**overrides) -> AccountSnapshot:
    defaults = dict(
        id="acct-1",
        status="ACTIVE",
        balance_minor=1_000,
        daily_limit_minor=10_000,
        posted_debits_today_minor=0,
    )
    defaults.update(overrides)
    return AccountSnapshot(**defaults)


# ---- individual rules ----------------------------------------------------


def test_check_not_self_transfer_rejects_same_id():
    with pytest.raises(DomainError) as exc:
        check_not_self_transfer("a", "a")
    assert exc.value.code == ErrorCode.SELF_TRANSFER


def test_check_not_self_transfer_allows_different_ids():
    check_not_self_transfer("a", "b")  # does not raise


@pytest.mark.parametrize("amount", [0, -1, -1000])
def test_check_amount_rejects_non_positive(amount):
    with pytest.raises(DomainError) as exc:
        check_amount(amount, MAX_AMOUNT)
    assert exc.value.code == ErrorCode.AMOUNT_NOT_POSITIVE


def test_check_amount_accepts_one():
    check_amount(1, MAX_AMOUNT)  # boundary: smallest legal amount


def test_check_amount_accepts_exactly_the_cap():
    check_amount(MAX_AMOUNT, MAX_AMOUNT)  # boundary: cap itself is legal


def test_check_amount_rejects_cap_plus_one():
    with pytest.raises(DomainError) as exc:
        check_amount(MAX_AMOUNT + 1, MAX_AMOUNT)
    assert exc.value.code == ErrorCode.AMOUNT_ABOVE_MAX


def test_check_currency_mismatch():
    with pytest.raises(DomainError) as exc:
        check_currency("USD", "TREATS")
    assert exc.value.code == ErrorCode.CURRENCY_MISMATCH


def test_check_currency_match():
    check_currency("TREATS", "TREATS")


@pytest.mark.parametrize("status,role,expected", [
    ("FROZEN", "source", ErrorCode.SOURCE_ACCOUNT_NOT_ACTIVE),
    ("CLOSED", "source", ErrorCode.SOURCE_ACCOUNT_NOT_ACTIVE),
    ("FROZEN", "destination", ErrorCode.DESTINATION_ACCOUNT_NOT_ACTIVE),
    ("CLOSED", "destination", ErrorCode.DESTINATION_ACCOUNT_NOT_ACTIVE),
])
def test_check_account_active_rejects_non_active(status, role, expected):
    with pytest.raises(DomainError) as exc:
        check_account_active(status, role=role)
    assert exc.value.code == expected


def test_check_account_active_allows_active():
    check_account_active("ACTIVE", role="source")


def test_check_sufficient_funds_boundary_exact_balance_is_allowed():
    check_sufficient_funds(balance_minor=100, amount_minor=100)


def test_check_sufficient_funds_rejects_one_over():
    with pytest.raises(DomainError) as exc:
        check_sufficient_funds(balance_minor=99, amount_minor=100)
    assert exc.value.code == ErrorCode.INSUFFICIENT_FUNDS


def test_check_daily_limit_boundary_exact_limit_is_allowed():
    check_daily_limit(posted_debits_today_minor=900, amount_minor=100, daily_limit_minor=1000)


def test_check_daily_limit_rejects_one_over():
    with pytest.raises(DomainError) as exc:
        check_daily_limit(posted_debits_today_minor=901, amount_minor=100, daily_limit_minor=1000)
    assert exc.value.code == ErrorCode.DAILY_LIMIT_EXCEEDED


def test_check_authorized_rejects_non_owner():
    with pytest.raises(DomainError) as exc:
        check_authorized("cat-2", "cat-1")
    assert exc.value.code == ErrorCode.UNAUTHORIZED_SOURCE


def test_check_authorized_allows_owner():
    check_authorized("cat-1", "cat-1")


# ---- evaluate_transfer: full pass and ordering ----------------------------


def test_evaluate_transfer_passes_when_everything_is_fine():
    inputs = TransferRequestInputs(
        caller_cat_id="acct-1",
        source=account(id="acct-1"),
        destination=account(id="acct-2"),
        amount_minor=500,
        currency="TREATS",
        expected_currency="TREATS",
        max_amount_minor=MAX_AMOUNT,
    )
    evaluate_transfer(inputs)  # does not raise


def test_evaluate_transfer_self_transfer_wins_over_everything_else():
    # Same id on both sides *and* a bad amount: self-transfer must still be the
    # error reported, because it is checked first and is cheaper than anything
    # that reads account state.
    inputs = TransferRequestInputs(
        caller_cat_id="acct-1",
        source=account(id="acct-1"),
        destination=account(id="acct-1"),
        amount_minor=-5,
        currency="TREATS",
        expected_currency="TREATS",
        max_amount_minor=MAX_AMOUNT,
    )
    with pytest.raises(DomainError) as exc:
        evaluate_transfer(inputs)
    assert exc.value.code == ErrorCode.SELF_TRANSFER


def test_evaluate_transfer_insufficient_funds_reported_over_daily_limit():
    # Both would fail; funds is checked first in this implementation.
    inputs = TransferRequestInputs(
        caller_cat_id="acct-1",
        source=account(id="acct-1", balance_minor=10, daily_limit_minor=5),
        destination=account(id="acct-2"),
        amount_minor=100,
        currency="TREATS",
        expected_currency="TREATS",
        max_amount_minor=MAX_AMOUNT,
    )
    with pytest.raises(DomainError) as exc:
        evaluate_transfer(inputs)
    assert exc.value.code == ErrorCode.INSUFFICIENT_FUNDS
