from tests.conftest import create_account, fund_account, make_transfer, new_idempotency_key


def test_transfer_moves_treats_and_writes_two_ledger_entries(client):
    sender = create_account(client, cat_name="Whiskers")
    receiver = create_account(client, cat_name="Mittens")
    fund_account(client, sender["id"], 10_000)

    resp = make_transfer(
        client,
        source_id=sender["id"],
        destination_id=receiver["id"],
        amount_minor=2_500,
        idempotency_key=new_idempotency_key(),
        caller_cat_id=sender["id"],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "POSTED"
    assert body["amount_minor"] == 2_500

    sender_after = client.get(f"/v1/accounts/{sender['id']}").json()
    receiver_after = client.get(f"/v1/accounts/{receiver['id']}").json()
    assert sender_after["balance_minor"] == 10_000 - 2_500
    assert receiver_after["balance_minor"] == 2_500

    sender_txns = client.get(f"/v1/accounts/{sender['id']}/transactions").json()["items"]
    receiver_txns = client.get(f"/v1/accounts/{receiver['id']}/transactions").json()["items"]
    # sender has 2 entries: the CREDIT from fund_account, then the DEBIT from this
    # send. Newest first, so index 0 is the debit.
    assert len(sender_txns) == 2
    assert sender_txns[0]["direction"] == "DEBIT"
    assert len(receiver_txns) == 1
    assert receiver_txns[0]["direction"] == "CREDIT"


def test_get_transfer_by_id(client):
    sender = create_account(client, cat_name="Tom")
    receiver = create_account(client, cat_name="Jerry")
    fund_account(client, sender["id"], 1_000)

    created = make_transfer(
        client,
        source_id=sender["id"],
        destination_id=receiver["id"],
        amount_minor=100,
        idempotency_key=new_idempotency_key(),
        caller_cat_id=sender["id"],
    ).json()

    resp = client.get(f"/v1/transfers/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


def test_get_transfer_404(client):
    resp = client.get("/v1/transfers/00000000-0000-0000-0000-000000009999")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "TRANSFER_NOT_FOUND"
