"""Hits the service layer directly with real threads and a real Postgres
connection each, per PLAN.md section 7. This is what actually proves the
SELECT ... FOR UPDATE locking works, not just that the code compiles.
"""

import threading
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.domain.errors import DomainError, ErrorCode
from app.services import transfer_service
from tests.conftest import create_account, fund_account


def _session_factory():
    # A dedicated engine per test so pool sizing doesn't limit how many threads
    # can hold a connection at once -- each thread needs its own connection to
    # actually contend on the same row lock in Postgres, not just in Python.
    engine = create_engine(settings.database_url, pool_size=40, max_overflow=0)
    return sessionmaker(bind=engine)


def test_20_threads_10_each_from_100_balance_never_oversells(client):
    sender = create_account(client, daily_limit_minor=10_000_000)
    receiver = create_account(client, daily_limit_minor=10_000_000)
    fund_account(client, sender["id"], 100)

    Session = _session_factory()
    results: list[str] = []
    lock = threading.Lock()

    def attempt():
        db = Session()
        try:
            transfer_service.execute_transfer(
                db,
                caller_cat_id=sender["id"],
                idempotency_key=str(uuid.uuid4()),
                source_account_id=uuid.UUID(sender["id"]),
                destination_account_id=uuid.UUID(receiver["id"]),
                amount_minor=10,
                currency="TREATS",
                expected_currency=settings.currency,
                max_amount_minor=settings.transfer_max_amount_minor,
            )
            with lock:
                results.append("POSTED")
        except DomainError as exc:
            with lock:
                results.append(exc.code.value)
        finally:
            db.close()

    threads = [threading.Thread(target=attempt) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count("POSTED") == 10
    assert results.count(ErrorCode.INSUFFICIENT_FUNDS.value) == 10

    final_balance = client.get(f"/v1/accounts/{sender['id']}").json()["balance_minor"]
    assert final_balance == 0


def test_opposite_direction_transfers_do_not_deadlock(client):
    account_a = create_account(client, daily_limit_minor=10_000_000)
    account_b = create_account(client, daily_limit_minor=10_000_000)
    fund_account(client, account_a["id"], 5_000)
    fund_account(client, account_b["id"], 5_000)

    Session = _session_factory()
    errors: list[Exception] = []

    def send(source_id: str, destination_id: str, count: int):
        db = Session()
        try:
            for _ in range(count):
                transfer_service.execute_transfer(
                    db,
                    caller_cat_id=source_id,
                    idempotency_key=str(uuid.uuid4()),
                    source_account_id=uuid.UUID(source_id),
                    destination_account_id=uuid.UUID(destination_id),
                    amount_minor=1,
                    currency="TREATS",
                    expected_currency=settings.currency,
                    max_amount_minor=settings.transfer_max_amount_minor,
                )
        except Exception as exc:  # noqa: BLE001 - we want to see anything, deadlock included
            errors.append(exc)
        finally:
            db.close()

    t1 = threading.Thread(target=send, args=(account_a["id"], account_b["id"], 25))
    t2 = threading.Thread(target=send, args=(account_b["id"], account_a["id"], 25))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert not t1.is_alive() and not t2.is_alive(), "a thread hung -- likely deadlocked"
    assert errors == [], f"unexpected errors: {errors}"

    # 25 each way at 1 whisker: net zero movement, so both ending balances equal
    # their starting balances.
    final_a = client.get(f"/v1/accounts/{account_a['id']}").json()["balance_minor"]
    final_b = client.get(f"/v1/accounts/{account_b['id']}").json()["balance_minor"]
    assert final_a == 5_000
    assert final_b == 5_000
