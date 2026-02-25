from __future__ import annotations

import sys
from dataclasses import dataclass

import aiohttp
from aiohttp_retry import RetryClient

from app.core.config.settings import get_settings


@dataclass(slots=True)
class HttpClient:
    session: aiohttp.ClientSession
    retry_client: RetryClient


_http_client: HttpClient | None = None


async def init_http_client() -> HttpClient:
    global _http_client
    if _http_client is not None:
        return _http_client

    # Create ClientSession with trust_env=True to automatically use proxy settings
    # from environment variables (HTTP_PROXY, HTTPS_PROXY, ALL_PROXY, NO_PROXY).
    #
    # Note on "Response payload is not completed" errors:
    #
    # aiohttp can raise TransferEncodingError / ClientPayloadError with messages like:
    #   "Response payload is not completed: <TransferEncodingError ... Not enough data to satisfy transfer length
    #   header.>"
    #
    # This is NOT an "HTTP 400 response from OpenAI". The "400" in TransferEncodingError is an
    # internal parse code while decoding chunked transfer encoding. It means the TCP peer (or a
    # middlebox) closed the connection before all bytes promised by HTTP framing
    # (Content-Length/chunked) were received.
    #
    # In practice, this is most commonly caused by:
    # - HTTP(S) proxies / VPNs / local forwarders truncating long-lived responses (especially SSE),
    # - transient network drops or host sleep/wake,
    # - upstream occasionally closing a stream without a clean terminator.
    #
    # Related: "Connection reset by peer" (macOS [Errno 54] / ECONNRESET).
    #
    # This often happens at request start when a proxy chain silently drops idle keep-alive connections
    # and the client attempts to reuse a stale pooled socket. Tuning
    # `CODEX_LB_HTTP_CLIENT_KEEPALIVE_TIMEOUT_SECONDS` lower (e.g. ~5–10s) can reduce the probability of
    # reusing stale connections, at the cost of more frequent TCP/TLS handshakes.
    #
    # codex-lb treats these as upstream transport failures on the streaming path and surfaces them
    # to clients as "upstream_unavailable" (and stores the error message for debugging). Occasional
    # occurrences are expected; if they become frequent, first try bypassing proxies for upstream
    # hosts via NO_PROXY (e.g. chatgpt.com, auth.openai.com) or unset proxy env vars.
    settings = get_settings()
    # `enable_cleanup_closed` is ignored on newer Python patch releases and
    # triggers an aiohttp DeprecationWarning. Keep the option only on runtimes
    # where it still has effect.
    if sys.version_info < (3, 12, 12):
        connector = aiohttp.TCPConnector(
            limit=settings.http_client_connector_limit,
            limit_per_host=settings.http_client_connector_limit_per_host,
            keepalive_timeout=settings.http_client_keepalive_timeout_seconds,
            ttl_dns_cache=settings.http_client_dns_cache_ttl_seconds,
            enable_cleanup_closed=True,
        )
    else:
        connector = aiohttp.TCPConnector(
            limit=settings.http_client_connector_limit,
            limit_per_host=settings.http_client_connector_limit_per_host,
            keepalive_timeout=settings.http_client_keepalive_timeout_seconds,
            ttl_dns_cache=settings.http_client_dns_cache_ttl_seconds,
        )
    session = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=None),
        connector=connector,
        trust_env=True,  # Enable proxy support from environment variables
    )
    retry_client = RetryClient(client_session=session, raise_for_status=False, trust_env=True)
    _http_client = HttpClient(session=session, retry_client=retry_client)
    return _http_client


async def close_http_client() -> None:
    global _http_client
    if _http_client is None:
        return
    await _http_client.retry_client.close()
    _http_client = None


def get_http_client() -> HttpClient:
    if _http_client is None:
        raise RuntimeError("HTTP client not initialized")
    return _http_client
