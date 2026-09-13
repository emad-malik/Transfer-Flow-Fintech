"""Money as an integer count of minor units. No floats, no Decimal, ever.

1 treat = 100 whiskers. amount_minor is always whiskers. Storing money as an
integer means addition/subtraction can never introduce rounding error, and it
matches exactly what a bigint column in Postgres can hold without loss.
"""

WHISKERS_PER_TREAT = 100


def format_treats(amount_minor: int) -> str:
    """Human-readable display only. Never parse this back into a number.

    divmod() floors toward negative infinity, so divmod(-150, 100) is (-2, 50),
    which would print "-2.50" for -1.50 treats. Split off the sign and divmod
    the absolute value instead, so the magnitude is never wrong.
    """
    sign = "-" if amount_minor < 0 else ""
    treats, whiskers = divmod(abs(amount_minor), WHISKERS_PER_TREAT)
    return f"{sign}{treats}.{whiskers:02d} treats"


def is_valid_amount(value: object) -> bool:
    """True only for a plain, non-bool int. Pydantic v2 with strict mode already
    rejects "5" and 5.0 at the schema boundary; this is the domain-level backstop
    so a caller that builds requests programmatically (or a future non-HTTP caller)
    gets the same guarantee.
    """
    return isinstance(value, int) and not isinstance(value, bool)
