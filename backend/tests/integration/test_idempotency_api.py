from tests.conftest import create_account, fund_account, make_transfer, new_idempotency_key


def test_replay_same_key_same_body_returns_same_transfer_and_does_not_double_post(client):
    sender = create_account(client)
    receiver = create_account(client)
    fund_account(client, sender["id"], 1_000)
    key = new_idempotency_key()

    first = make_transfer(
        client, source_id=sender["id"], destination_id=receiver["id"], amount_minor=100,
        idempotency_key=key, caller_cat_id=sender["id"],
    )
    second = make_transfer(
        client, source_id=sender["id"], destination_id=receiver["id"], amount_minor=100,
        idempotency_key=key, caller_cat_id=sender["id"],
    )
    third = make_transfer(
        client, source_id=sender["id"], destination_id=receiver["id"], amount_minor=100,
        idempotency_key=key, caller_cat_id=sender["id"],
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 200
    assert first.json()["id"] == second.json()["id"] == third.json()["id"]

    receiver_after = client.get(f"/v1/accounts/{receiver['id']}").json()
    assert receiver_after["balance_minor"] == 100  # not 300

    receiver_txns = client.get(f"/v1/accounts/{receiver['id']}/transactions").json()["items"]
    assert len(receiver_txns) == 1  # exactly one CREDIT, not three


def test_same_key_different_amount_is_409(client):
    sender = create_account(client)
    receiver = create_account(client)
    fund_account(client, sender["id"], 1_000)
    key = new_idempotency_key()

    make_transfer(
        client, source_id=sender["id"], destination_id=receiver["id"], amount_minor=100,
        idempotency_key=key, caller_cat_id=sender["id"],
    )
    resp = make_transfer(
        client, source_id=sender["id"], destination_id=receiver["id"], amount_minor=200,
        idempotency_key=key, caller_cat_id=sender["id"],
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSE"


def test_same_key_different_destination_is_409(client):
    sender = create_account(client)
    receiver_a = create_account(client)
    receiver_b = create_account(client)
    fund_account(client, sender["id"], 1_000)
    key = new_idempotency_key()

    make_transfer(
        client, source_id=sender["id"], destination_id=receiver_a["id"], amount_minor=100,
        idempotency_key=key, caller_cat_id=sender["id"],
    )
    resp = make_transfer(
        client, source_id=sender["id"], destination_id=receiver_b["id"], amount_minor=100,
        idempotency_key=key, caller_cat_id=sender["id"],
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSE"


def test_different_keys_same_body_both_post(client):
    """Two distinct idempotency keys are two distinct transfers, even with an
    identical body -- idempotency is about the key, not the content.
    """
    sender = create_account(client)
    receiver = create_account(client)
    fund_account(client, sender["id"], 1_000)

    first = make_transfer(
        client, source_id=sender["id"], destination_id=receiver["id"], amount_minor=100,
        idempotency_key=new_idempotency_key(), caller_cat_id=sender["id"],
    )
    second = make_transfer(
        client, source_id=sender["id"], destination_id=receiver["id"], amount_minor=100,
        idempotency_key=new_idempotency_key(), caller_cat_id=sender["id"],
    )
    assert first.json()["id"] != second.json()["id"]
    receiver_after = client.get(f"/v1/accounts/{receiver['id']}").json()
    assert receiver_after["balance_minor"] == 200
