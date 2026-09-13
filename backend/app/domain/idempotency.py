"""Idempotency key handling: the request fingerprint that catches key reuse
with a different body.
"""

import hashlib
import json
import uuid


def compute_fingerprint(
    *,
    source_account_id: uuid.UUID | str,
    destination_account_id: uuid.UUID | str,
    amount_minor: int,
    currency: str,
) -> str:
    """sha256 of the canonical (sorted-key) JSON of the fields that define what the
    transfer *is*. The idempotency key itself is deliberately excluded: the whole
    point is to detect the same key reused for a materially different request.
    """
    canonical = json.dumps(
        {
            "source_account_id": str(source_account_id),
            "destination_account_id": str(destination_account_id),
            "amount_minor": amount_minor,
            "currency": currency,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
