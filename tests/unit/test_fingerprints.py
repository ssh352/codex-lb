from __future__ import annotations

import hmac
from hashlib import sha256

from app.core.utils.fingerprints import hmac_sha256_fingerprint


def test_hmac_sha256_fingerprint_prefix_and_truncation() -> None:
    key = b"test-key"
    value = "hello"
    expected = hmac.new(key, value.encode("utf-8"), sha256).hexdigest()[:12]
    assert hmac_sha256_fingerprint(value, key=key) == f"hmac_sha256:{expected}"
