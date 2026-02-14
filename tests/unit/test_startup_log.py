from __future__ import annotations

from app.core.config.startup_log import _redact_proxy_url


def test_redact_proxy_url_redacts_credentials_with_scheme() -> None:
    assert _redact_proxy_url("http://user:pass@host:8080") == "http://***:***@host:8080"


def test_redact_proxy_url_redacts_credentials_without_scheme() -> None:
    assert _redact_proxy_url("user:pass@host:8080") == "***:***@host:8080"


def test_redact_proxy_url_leaves_host_only_proxy_unchanged() -> None:
    assert _redact_proxy_url("http://host:8080") == "http://host:8080"


def test_redact_proxy_url_leaves_no_proxy_unchanged() -> None:
    assert _redact_proxy_url("localhost,127.0.0.1,.internal") == "localhost,127.0.0.1,.internal"
