"""One row per error code in README's error contract table. Adding a rule
later means adding a row here.
"""

import pytest

from app.config import settings
from tests.conftest import (
    create_account,
    fund_account,
    make_transfer,
    new_idempotency_key,
)


def test_missing_idempotency_key(client):
    sender = create_account(client)
    receiver = create_account(client)
    resp = make_transfer(
        client, source_id=sender["id"], destination_id=receiver["id"], amount_minor=10,
        caller_cat_id=sender["id"],  # no idempotency_key
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "MISSING_IDEMPOTENCY_KEY"


def test_account_not_found_source(client):
    receiver = create_account(client)
    resp = make_transfer(
        client,
        source_id="00000000-0000-0000-0000-000000009999",
        destination_id=receiver["id"],
        amount_minor=10,
        idempotency_key=new_idempotency_key(),
        caller_cat_id="00000000-0000-0000-0000-000000009999",
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"


def test_account_not_found_destination(client):
    sender = create_account(client)
    fund_account(client, sender["id"], 100)
    resp = make_transfer(
        client,
        source_id=sender["id"],
        destination_id="00000000-0000-0000-0000-000000009999",
        amount_minor=10,
        idempotency_key=new_idempotency_key(),
        caller_cat_id=sender["id"],
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"


def test_self_transfer(client):
    account = create_account(client)
    resp = make_transfer(
        client, source_id=account["id"], destination_id=account["id"], amount_minor=10,
        idempotency_key=new_idempotency_key(), caller_cat_id=account["id"],
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "SELF_TRANSFER"


@pytest.mark.parametrize("amount", [0, -1])
def test_amount_not_positive(client, amount):
    sender = create_account(client)
    receiver = create_account(client)
    resp = make_transfer(
        client, source_id=sender["id"], destination_id=receiver["id"], amount_minor=amount,
        idempotency_key=new_idempotency_key(), caller_cat_id=sender["id"],
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "AMOUNT_NOT_POSITIVE"


def test_amount_above_max(client):
    sender = create_account(client)
    receiver = create_account(client)
    resp = make_transfer(
        client, source_id=sender["id"], destination_id=receiver["id"],
        amount_minor=settings.transfer_max_amount_minor + 1,
        idempotency_key=new_idempotency_key(), caller_cat_id=sender["id"],
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "AMOUNT_ABOVE_MAX"


def test_source_account_not_active(client):
    sender = create_account(client)
    receiver = create_account(client)
    fund_account(client, sender["id"], 100)
    client.patch(f"/v1/accounts/{sender['id']}/status", json={"status": "FROZEN"})
    resp = make_transfer(
        client, source_id=sender["id"], destination_id=receiver["id"], amount_minor=10,
        idempotency_key=new_idempotency_key(), caller_cat_id=sender["id"],
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "SOURCE_ACCOUNT_NOT_ACTIVE"


def test_destination_account_not_active(client):
    sender = create_account(client)
    receiver = create_account(client)
    fund_account(client, sender["id"], 100)
    client.patch(f"/v1/accounts/{receiver['id']}/status", json={"status": "CLOSED"})
    resp = make_transfer(
        client, source_id=sender["id"], destination_id=receiver["id"], amount_minor=10,
        idempotency_key=new_idempotency_key(), caller_cat_id=sender["id"],
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "DESTINATION_ACCOUNT_NOT_ACTIVE"


def test_insufficient_funds(client):
    sender = create_account(client)
    receiver = create_account(client)
    fund_account(client, sender["id"], 50)
    resp = make_transfer(
        client, source_id=sender["id"], destination_id=receiver["id"], amount_minor=51,
        idempotency_key=new_idempotency_key(), caller_cat_id=sender["id"],
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "INSUFFICIENT_FUNDS"


def test_daily_limit_exceeded(client):
    sender = create_account(client, daily_limit_minor=100)
    receiver = create_account(client)
    fund_account(client, sender["id"], 10_000)  # funding itself doesn't count as a debit for sender
    resp = make_transfer(
        client, source_id=sender["id"], destination_id=receiver["id"], amount_minor=101,
        idempotency_key=new_idempotency_key(), caller_cat_id=sender["id"],
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "DAILY_LIMIT_EXCEEDED"


def test_currency_mismatch(client):
    sender = create_account(client)
    receiver = create_account(client)
    fund_account(client, sender["id"], 100)
    resp = make_transfer(
        client, source_id=sender["id"], destination_id=receiver["id"], amount_minor=10,
        currency="USD", idempotency_key=new_idempotency_key(), caller_cat_id=sender["id"],
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "CURRENCY_MISMATCH"


def test_unauthorized_source(client):
    sender = create_account(client)
    receiver = create_account(client)
    someone_else = create_account(client)
    fund_account(client, sender["id"], 100)
    resp = make_transfer(
        client, source_id=sender["id"], destination_id=receiver["id"], amount_minor=10,
        idempotency_key=new_idempotency_key(), caller_cat_id=someone_else["id"],
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "UNAUTHORIZED_SOURCE"


def test_failed_transfer_is_still_persisted_and_readable(client):
    """A rule failure must be a durable, auditable record, not a silently
    dropped request -- the whole reason for inserting PENDING before
    evaluating rules. Proof: the FAILED row is fetchable by id afterwards.

    Replaying the *same* idempotency key against the same body reproduces the
    *original outcome* -- same 409, same INSUFFICIENT_FUNDS -- rather than a
    200 with status: FAILED buried in the body, which a naive retrying client
    would read as success. See README's error contract.
    """
    sender = create_account(client)
    receiver = create_account(client)
    key = new_idempotency_key()

    first = make_transfer(
        client, source_id=sender["id"], destination_id=receiver["id"], amount_minor=10,
        idempotency_key=key, caller_cat_id=sender["id"],
    )
    assert first.status_code == 409
    first_body = first.json()
    assert first_body["error"]["code"] == "INSUFFICIENT_FUNDS"
    transfer_id = first_body["error"]["details"]["transfer_id"]

    replay = make_transfer(
        client, source_id=sender["id"], destination_id=receiver["id"], amount_minor=10,
        idempotency_key=key, caller_cat_id=sender["id"],
    )
    assert replay.status_code == 409
    replay_body = replay.json()
    assert replay_body["error"]["code"] == "INSUFFICIENT_FUNDS"
    assert replay_body["error"]["details"]["transfer_id"] == transfer_id

    fetched = client.get(f"/v1/transfers/{transfer_id}")
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "FAILED"
    assert fetched.json()["failure_code"] == "INSUFFICIENT_FUNDS"


def test_validation_error_on_non_integer_amount(client):
    """schemas.py documents StrictInt as the guarantee that separates
    VALIDATION_ERROR (fails schema, never reaches the domain layer) from
    AMOUNT_NOT_POSITIVE (a plain int that fails the domain rule) -- but that
    guarantee had no test on /v1/transfers itself. A numeric string, a float,
    and a bool must all be rejected at the schema boundary.
    """
    sender = create_account(client)
    receiver = create_account(client)
    fund_account(client, sender["id"], 100)

    for bad_amount in ["100", 1.5, True]:
        resp = client.post(
            "/v1/transfers",
            json={
                "source_account_id": sender["id"],
                "destination_account_id": receiver["id"],
                "amount_minor": bad_amount,
                "currency": "TREATS",
            },
            headers={"Idempotency-Key": new_idempotency_key(), "X-Cat-Id": sender["id"]},
        )
        assert resp.status_code == 422, (bad_amount, resp.text)
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
