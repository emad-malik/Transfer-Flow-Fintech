"""Test DB wiring. TEST_DATABASE_URL (or a hardcoded default pointing at
meowpay_test) is set *before* app.config is ever imported, so the app's engine
never touches the dev database while tests run.
"""

import os
import uuid
from pathlib import Path

os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://meowpay:meowpay@localhost:5432/meowpay_test"
)

import pytest  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from alembic import command  # noqa: E402

BACKEND_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session", autouse=True)
def _migrated_test_database():
    """Reset the test database to a bare schema, then run our real Alembic
    migrations against it -- including the 0002 seed -- so integration tests
    exercise the exact same migration path a fresh clone would run, not a
    metadata.create_all() shortcut that could silently drift from it.
    """
    db_url = os.environ["DATABASE_URL"]
    engine = create_engine(db_url)
    with engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.commit()
    engine.dispose()

    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(scope="session")
def client() -> TestClient:
    from app.main import app

    with TestClient(app) as c:
        yield c


TREASURY_ID = "00000000-0000-0000-0000-000000000001"


def new_idempotency_key() -> str:
    return str(uuid.uuid4())


def create_account(
    client: TestClient, *, cat_name: str = "Test Cat", daily_limit_minor: int = 10_000_000
) -> dict:
    resp = client.post(
        "/v1/accounts", json={"cat_name": cat_name, "daily_limit_minor": daily_limit_minor}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def fund_account(client: TestClient, account_id: str, amount_minor: int) -> dict:
    resp = client.post(
        "/v1/transfers",
        json={
            "source_account_id": TREASURY_ID,
            "destination_account_id": account_id,
            "amount_minor": amount_minor,
            "currency": "TREATS",
        },
        headers={"Idempotency-Key": new_idempotency_key(), "X-Cat-Id": TREASURY_ID},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def make_transfer(
    client: TestClient,
    *,
    source_id: str,
    destination_id: str,
    amount_minor: int,
    currency: str = "TREATS",
    idempotency_key: str | None = None,
    caller_cat_id: str | None = None,
):
    headers = {}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    if caller_cat_id is not None:
        headers["X-Cat-Id"] = caller_cat_id
    return client.post(
        "/v1/transfers",
        json={
            "source_account_id": source_id,
            "destination_account_id": destination_id,
            "amount_minor": amount_minor,
            "currency": currency,
        },
        headers=headers,
    )
