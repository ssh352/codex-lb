from __future__ import annotations

import hmac
from hashlib import sha256


def hmac_sha256_fingerprint(
    value: str,
    *,
    key: bytes,
    prefix: str = "hmac_sha256",
    hex_chars: int = 12,
) -> str:
    digest = hmac.new(key, value.encode("utf-8"), sha256).hexdigest()
    return f"{prefix}:{digest[:hex_chars]}"
