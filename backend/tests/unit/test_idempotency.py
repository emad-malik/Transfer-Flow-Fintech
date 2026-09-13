import uuid

from app.domain.idempotency import compute_fingerprint

SRC = uuid.uuid4()
DST = uuid.uuid4()


def _fp(**overrides):
    kwargs = dict(
        source_account_id=SRC, destination_account_id=DST, amount_minor=100, currency="TREATS"
    )
    kwargs.update(overrides)
    return compute_fingerprint(**kwargs)


def test_same_inputs_same_fingerprint():
    assert _fp() == _fp()


def test_uuid_object_and_str_form_agree():
    as_uuids = compute_fingerprint(
        source_account_id=SRC, destination_account_id=DST, amount_minor=100, currency="TREATS"
    )
    as_strs = compute_fingerprint(
        source_account_id=str(SRC),
        destination_account_id=str(DST),
        amount_minor=100,
        currency="TREATS",
    )
    assert as_uuids == as_strs


def test_changing_amount_changes_fingerprint():
    assert _fp() != _fp(amount_minor=101)


def test_changing_currency_changes_fingerprint():
    assert _fp() != _fp(currency="USD")


def test_changing_source_changes_fingerprint():
    assert _fp() != _fp(source_account_id=uuid.uuid4())


def test_changing_destination_changes_fingerprint():
    assert _fp() != _fp(destination_account_id=uuid.uuid4())


def test_swapping_source_and_destination_changes_fingerprint():
    swapped = compute_fingerprint(
        source_account_id=DST, destination_account_id=SRC, amount_minor=100, currency="TREATS"
    )
    assert _fp() != swapped
