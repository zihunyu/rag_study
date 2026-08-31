"""UUIDv7 identifiers without a vendor or framework dependency."""

from __future__ import annotations

import secrets
import time
import uuid


def new_uuid7(timestamp_ms: int | None = None) -> str:
    """Return an RFC 9562 UUIDv7 string."""

    milliseconds = int(time.time() * 1000) if timestamp_ms is None else timestamp_ms
    if not 0 <= milliseconds < 1 << 48:
        raise ValueError("timestamp_ms is outside the UUIDv7 48-bit range")
    random_a = secrets.randbits(12)
    random_b = secrets.randbits(62)
    value = (milliseconds << 80) | (0x7 << 76) | (random_a << 64) | (0b10 << 62) | random_b
    return str(uuid.UUID(int=value))
