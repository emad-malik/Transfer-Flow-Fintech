from app.domain.money import format_treats, is_valid_amount


def test_format_treats_splits_whiskers():
    assert format_treats(0) == "0.00 treats"
    assert format_treats(1) == "0.01 treats"
    assert format_treats(100) == "1.00 treats"
    assert format_treats(12345) == "123.45 treats"


def test_format_treats_handles_negative_amounts():
    # divmod() floors toward -inf, so a naive divmod(-150, 100) gives (-2, 50)
    # and prints "-2.50" for what should be -1.50. Regression coverage for that.
    assert format_treats(-1) == "-0.01 treats"
    assert format_treats(-100) == "-1.00 treats"
    assert format_treats(-150) == "-1.50 treats"
    assert format_treats(-12345) == "-123.45 treats"


def test_is_valid_amount_accepts_plain_int():
    assert is_valid_amount(0) is True
    assert is_valid_amount(5) is True
    assert is_valid_amount(-5) is True  # sign is a *rule* concern, not a type concern


def test_is_valid_amount_rejects_non_int_types():
    assert is_valid_amount(5.0) is False
    assert is_valid_amount("5") is False
    assert is_valid_amount(None) is False
    assert is_valid_amount(True) is False  # bool is a subclass of int in Python
    assert is_valid_amount(False) is False
