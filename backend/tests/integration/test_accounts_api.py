from tests.conftest import create_account


def test_create_account_defaults_daily_limit(client):
    resp = client.post("/v1/accounts", json={"cat_name": "Garfield"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["cat_name"] == "Garfield"
    assert body["status"] == "ACTIVE"
    assert body["balance_minor"] == 0
    assert body["daily_limit_minor"] > 0


def test_create_account_rejects_blank_name(client):
    resp = client.post("/v1/accounts", json={"cat_name": ""})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_get_account_returns_derived_balance(client):
    account = create_account(client, cat_name="Tom")
    resp = client.get(f"/v1/accounts/{account['id']}")
    assert resp.status_code == 200
    assert resp.json()["balance_minor"] == 0


def test_get_account_404(client):
    resp = client.get("/v1/accounts/00000000-0000-0000-0000-000000009999")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"


def test_transactions_empty_for_new_account(client):
    account = create_account(client, cat_name="Sylvester")
    resp = client.get(f"/v1/accounts/{account['id']}/transactions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["limit"] == 20
    assert body["offset"] == 0


def test_transactions_404_for_missing_account(client):
    resp = client.get("/v1/accounts/00000000-0000-0000-0000-000000009999/transactions")
    assert resp.status_code == 404


def test_list_accounts_includes_created_account(client):
    account = create_account(client, cat_name="Azrael")
    resp = client.get("/v1/accounts?limit=200")
    assert resp.status_code == 200
    body = resp.json()
    ids = [a["id"] for a in body["items"]]
    assert account["id"] in ids


def test_update_account_status_round_trip(client):
    account = create_account(client, cat_name="Felix")
    resp = client.patch(f"/v1/accounts/{account['id']}/status", json={"status": "FROZEN"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "FROZEN"

    resp = client.patch(f"/v1/accounts/{account['id']}/status", json={"status": "NOT_A_STATUS"})
    assert resp.status_code == 422
