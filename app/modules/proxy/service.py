from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from datetime import timedelta
from hashlib import sha256
from typing import AsyncIterator, Mapping

import anyio

from app.core import usage as usage_core
from app.core.auth.refresh import RefreshError, should_refresh
from app.core.balancer import PERMANENT_FAILURE_CODES
from app.core.balancer.types import UpstreamError
from app.core.clients.proxy import ProxyResponseError, filter_inbound_headers
from app.core.clients.proxy import compact_responses as core_compact_responses
from app.core.clients.proxy import stream_responses as core_stream_responses
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor, get_or_create_key
from app.core.errors import openai_error, response_failed_event
from app.core.metrics import get_metrics
from app.core.metrics.metrics import ProxyRequestObservation
from app.core.openai.models import OpenAIEvent, OpenAIResponsePayload
from app.core.openai.parsing import parse_sse_event
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.core.request_logs.buffer import RequestLogCreate, enqueue_request_log
from app.core.types import JsonValue
from app.core.usage.types import UsageWindowRow
from app.core.utils.fingerprints import hmac_sha256_fingerprint
from app.core.utils.request_id import ensure_request_id, get_request_id
from app.core.utils.sse import format_sse_event, parse_sse_data_json
from app.core.utils.time import utcnow
from app.db.models import Account, UsageHistory
from app.modules.accounts.auth_manager import AuthManager
from app.modules.proxy.helpers import (
    _apply_error_metadata,
    _credits_headers,
    _credits_snapshot,
    _header_account_id,
    _normalize_error_code,
    _parse_openai_error,
    _plan_type_for_accounts,
    _rate_limit_details,
    _rate_limit_headers,
    _select_accounts_for_limits,
    _summarize_window,
    _upstream_error_from_openai,
    _window_snapshot,
)
from app.modules.proxy.load_balancer import LoadBalancer, LoadBalancerDebugDump, LoadBalancerSelectionEvent
from app.modules.proxy.rate_limit_cache import get_or_build_rate_limit_headers
from app.modules.proxy.repo_bundle import ProxyRepoFactory, ProxyRepositories
from app.modules.proxy.types import RateLimitStatusPayloadData

logger = logging.getLogger(__name__)

_TEXT_DELTA_EVENT_TYPES = frozenset({"response.output_text.delta", "response.refusal.delta"})
_TEXT_DONE_CONTENT_PART_TYPES = frozenset({"output_text", "refusal"})


def _maybe_prompt_cache_key_hash(value: str | None) -> str | None:
    if not value:
        return None
    if not get_settings().request_logs_prompt_cache_key_hash_enabled:
        return None
    key = get_or_create_key()
    return hmac_sha256_fingerprint(value, key=key)


class ProxyService:
    def __init__(self, repo_factory: ProxyRepoFactory) -> None:
        self._repo_factory = repo_factory
        self._encryptor = TokenEncryptor()
        self._load_balancer = LoadBalancer(repo_factory)

    def invalidate_routing_snapshot(self) -> None:
        self._load_balancer.invalidate_snapshot()

    async def debug_lb_dump(self) -> LoadBalancerDebugDump:
        return await self._load_balancer.debug_dump()

    def debug_lb_events(self, *, limit: int) -> list[LoadBalancerSelectionEvent]:
        max_size = get_settings().debug_lb_event_buffer_size
        capped = max(1, min(int(limit), int(max_size)))
        return self._load_balancer.debug_events(limit=capped)

    def stream_responses(
        self,
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        *,
        propagate_http_errors: bool = False,
        api: str = "responses",
        suppress_text_done_events: bool = False,
    ) -> AsyncIterator[str]:
        _maybe_log_proxy_request_payload("stream", payload, headers)
        _maybe_log_proxy_request_shape("stream", payload, headers)
        filtered = filter_inbound_headers(headers)
        return self._stream_with_retry(
            payload,
            filtered,
            propagate_http_errors=propagate_http_errors,
            api=api,
            suppress_text_done_events=suppress_text_done_events,
        )

    async def compact_responses(
        self,
        payload: ResponsesCompactRequest,
        headers: Mapping[str, str],
    ) -> OpenAIResponsePayload:
        _maybe_log_proxy_request_payload("compact", payload, headers)
        _maybe_log_proxy_request_shape("compact", payload, headers)
        filtered = filter_inbound_headers(headers)
        codex_session_id = self._optional_header_value(filtered, "x-codex-session-id")
        codex_conversation_id = self._optional_header_value(filtered, "x-codex-conversation-id")
        request_id = ensure_request_id()
        sticky_key = _sticky_key_from_compact_payload(payload)
        prompt_cache_key_hash = _maybe_prompt_cache_key_hash(sticky_key)
        retryable_codes = {
            "rate_limit_exceeded",
            "usage_limit_reached",
            "insufficient_quota",
            "usage_not_included",
            "quota_exceeded",
        }
        # Fail over across multiple accounts, but keep the bound small to avoid long tail latency
        # when upstream is broadly unavailable/limited across many accounts.
        max_attempts = 3
        last_retryable_error: ProxyResponseError | None = None

        async def _persist_request_log(
            *,
            account_id: str,
            model: str,
            latency_ms: int,
            status: str,
            error_code: str | None,
            error_message: str | None,
            input_tokens: int | None,
            output_tokens: int | None,
            cached_input_tokens: int | None,
            reasoning_tokens: int | None,
        ) -> None:
            with anyio.CancelScope(shield=True):
                if get_settings().request_logs_buffer_enabled:
                    enqueue_request_log(
                        RequestLogCreate(
                            account_id=account_id,
                            request_id=request_id,
                            model=model,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            cached_input_tokens=cached_input_tokens,
                            reasoning_tokens=reasoning_tokens,
                            reasoning_effort=None,
                            latency_ms=latency_ms,
                            status=status,
                            error_code=error_code,
                            error_message=error_message,
                            prompt_cache_key_hash=prompt_cache_key_hash,
                            codex_session_id=codex_session_id,
                            codex_conversation_id=codex_conversation_id,
                            requested_at=utcnow(),
                        )
                    )
                    return

                async with self._repo_factory() as repos:
                    await repos.request_logs.add_log(
                        account_id=account_id,
                        request_id=request_id,
                        model=model,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cached_input_tokens=cached_input_tokens,
                        reasoning_tokens=reasoning_tokens,
                        reasoning_effort=None,
                        latency_ms=latency_ms,
                        status=status,
                        error_code=error_code,
                        error_message=error_message,
                        prompt_cache_key_hash=prompt_cache_key_hash,
                        codex_session_id=codex_session_id,
                        codex_conversation_id=codex_conversation_id,
                        requested_at=utcnow(),
                    )

        for attempt in range(max_attempts):
            start = time.monotonic()
            selection = await self._load_balancer.select_account(
                sticky_key=sticky_key,
                reallocate_sticky=attempt > 0,
            )
            account = selection.account
            if not account:
                if last_retryable_error is not None:
                    raise last_retryable_error
                raise ProxyResponseError(
                    503,
                    openai_error("no_accounts", selection.error_message or "No active accounts available"),
                )

            async def _call_compact(target: Account) -> OpenAIResponsePayload:
                access_token = self._encryptor.decrypt(target.access_token_encrypted)
                account_id = _header_account_id(target.chatgpt_account_id)
                return await core_compact_responses(payload, filtered, access_token, account_id)

            try:
                account = await self._ensure_fresh_if_needed(account)
                response = await _call_compact(account)
                latency_ms = int((time.monotonic() - start) * 1000)
                usage = response.usage
                input_tokens = usage.input_tokens if usage else None
                output_tokens = usage.output_tokens if usage else None
                cached_input_tokens = (
                    usage.input_tokens_details.cached_tokens if usage and usage.input_tokens_details else None
                )
                reasoning_tokens = (
                    usage.output_tokens_details.reasoning_tokens if usage and usage.output_tokens_details else None
                )
                status = "success"
                error_code = None
                if response.status == "failed" or response.error is not None:
                    status = "error"
                    error = response.error
                    error_code = _normalize_error_code(
                        error.code if error else None,
                        error.type if error else None,
                    )
                get_metrics().observe_proxy_request(
                    ProxyRequestObservation(
                        account_id=account.id,
                        api="responses_compact",
                        status=status,
                        model=payload.model,
                        latency_ms=latency_ms,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cached_input_tokens=cached_input_tokens,
                        reasoning_tokens=reasoning_tokens,
                        error_code=error_code,
                    )
                )
                try:
                    await _persist_request_log(
                        account_id=account.id,
                        model=payload.model,
                        latency_ms=latency_ms,
                        status=status,
                        error_code=error_code,
                        error_message=response.error.message if response.error else None,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cached_input_tokens=cached_input_tokens,
                        reasoning_tokens=reasoning_tokens,
                    )
                except Exception:
                    logger.warning(
                        "Failed to persist request log account_id=%s request_id=%s",
                        account.id,
                        request_id,
                        exc_info=True,
                    )
                return response
            except ProxyResponseError as exc:
                if exc.status_code == 401:
                    try:
                        account = await self._ensure_fresh(account, force=True)
                    except RefreshError as refresh_exc:
                        if refresh_exc.is_permanent:
                            await self._load_balancer.mark_permanent_failure(account, refresh_exc.code)
                        latency_ms = int((time.monotonic() - start) * 1000)
                        get_metrics().observe_proxy_request(
                            ProxyRequestObservation(
                                account_id=account.id,
                                api="responses_compact",
                                status="error",
                                model=payload.model,
                                latency_ms=latency_ms,
                                input_tokens=None,
                                output_tokens=None,
                                cached_input_tokens=None,
                                reasoning_tokens=None,
                                error_code="auth_refresh_failed",
                            )
                        )
                        try:
                            await _persist_request_log(
                                account_id=account.id,
                                model=payload.model,
                                latency_ms=latency_ms,
                                status="error",
                                error_code="auth_refresh_failed",
                                error_message=str(refresh_exc),
                                input_tokens=None,
                                output_tokens=None,
                                cached_input_tokens=None,
                                reasoning_tokens=None,
                            )
                        except Exception:
                            logger.warning(
                                "Failed to persist request log account_id=%s request_id=%s",
                                account.id,
                                request_id,
                                exc_info=True,
                            )
                        raise exc
                    try:
                        response = await _call_compact(account)
                        latency_ms = int((time.monotonic() - start) * 1000)
                        usage = response.usage
                        input_tokens = usage.input_tokens if usage else None
                        output_tokens = usage.output_tokens if usage else None
                        cached_input_tokens = (
                            usage.input_tokens_details.cached_tokens if usage and usage.input_tokens_details else None
                        )
                        reasoning_tokens = (
                            usage.output_tokens_details.reasoning_tokens
                            if usage and usage.output_tokens_details
                            else None
                        )
                        status = "success"
                        error_code = None
                        if response.status == "failed" or response.error is not None:
                            status = "error"
                            error = response.error
                            error_code = _normalize_error_code(
                                error.code if error else None,
                                error.type if error else None,
                            )
                        get_metrics().observe_proxy_request(
                            ProxyRequestObservation(
                                account_id=account.id,
                                api="responses_compact",
                                status=status,
                                model=payload.model,
                                latency_ms=latency_ms,
                                input_tokens=input_tokens,
                                output_tokens=output_tokens,
                                cached_input_tokens=cached_input_tokens,
                                reasoning_tokens=reasoning_tokens,
                                error_code=error_code,
                            )
                        )
                        try:
                            await _persist_request_log(
                                account_id=account.id,
                                model=payload.model,
                                latency_ms=latency_ms,
                                status=status,
                                error_code=error_code,
                                error_message=response.error.message if response.error else None,
                                input_tokens=input_tokens,
                                output_tokens=output_tokens,
                                cached_input_tokens=cached_input_tokens,
                                reasoning_tokens=reasoning_tokens,
                            )
                        except Exception:
                            logger.warning(
                                "Failed to persist request log account_id=%s request_id=%s",
                                account.id,
                                request_id,
                                exc_info=True,
                            )
                        return response
                    except ProxyResponseError as exc:
                        await self._handle_proxy_error(account, exc)
                        error = _parse_openai_error(exc.payload)
                        code = _normalize_error_code(
                            error.code if error else None,
                            error.type if error else None,
                        )
                        latency_ms = int((time.monotonic() - start) * 1000)
                        get_metrics().observe_proxy_request(
                            ProxyRequestObservation(
                                account_id=account.id,
                                api="responses_compact",
                                status="error",
                                model=payload.model,
                                latency_ms=latency_ms,
                                input_tokens=None,
                                output_tokens=None,
                                cached_input_tokens=None,
                                reasoning_tokens=None,
                                error_code=code,
                            )
                        )
                        try:
                            await _persist_request_log(
                                account_id=account.id,
                                model=payload.model,
                                latency_ms=latency_ms,
                                status="error",
                                error_code=code,
                                error_message=error.message if error else None,
                                input_tokens=None,
                                output_tokens=None,
                                cached_input_tokens=None,
                                reasoning_tokens=None,
                            )
                        except Exception:
                            logger.warning(
                                "Failed to persist request log account_id=%s request_id=%s",
                                account.id,
                                request_id,
                                exc_info=True,
                            )
                        raise

                error = _parse_openai_error(exc.payload)
                code = _normalize_error_code(
                    error.code if error else None,
                    error.type if error else None,
                )
                await self._handle_stream_error(account, _upstream_error_from_openai(error), code)
                latency_ms = int((time.monotonic() - start) * 1000)
                get_metrics().observe_proxy_request(
                    ProxyRequestObservation(
                        account_id=account.id,
                        api="responses_compact",
                        status="error",
                        model=payload.model,
                        latency_ms=latency_ms,
                        input_tokens=None,
                        output_tokens=None,
                        cached_input_tokens=None,
                        reasoning_tokens=None,
                        error_code=code,
                    )
                )
                try:
                    await _persist_request_log(
                        account_id=account.id,
                        model=payload.model,
                        latency_ms=latency_ms,
                        status="error",
                        error_code=code,
                        error_message=error.message if error else None,
                        input_tokens=None,
                        output_tokens=None,
                        cached_input_tokens=None,
                        reasoning_tokens=None,
                    )
                except Exception:
                    logger.warning(
                        "Failed to persist request log account_id=%s request_id=%s",
                        account.id,
                        request_id,
                        exc_info=True,
                    )
                if code in retryable_codes and attempt < (max_attempts - 1):
                    get_metrics().observe_proxy_retry(
                        api="responses_compact",
                        error_code=code,
                        account_id=account.id,
                    )
                    last_retryable_error = exc
                    continue
                raise
            except RefreshError as exc:
                if exc.is_permanent:
                    await self._load_balancer.mark_permanent_failure(account, exc.code)
                continue

        if last_retryable_error is not None:
            raise last_retryable_error
        raise ProxyResponseError(
            503,
            openai_error("no_accounts", "No available accounts after retries"),
        )

    async def rate_limit_headers(self) -> dict[str, str]:
        async def _build() -> dict[str, str]:
            now = utcnow()
            headers: dict[str, str] = {}
            async with self._repo_factory() as repos:
                accounts = await repos.accounts.list_accounts()
                account_map = {account.id: account for account in accounts}

                primary_minutes = await repos.usage.latest_window_minutes("primary")
                if primary_minutes is None:
                    primary_minutes = usage_core.default_window_minutes("primary")
                if primary_minutes:
                    primary_rows = await repos.usage.aggregate_since(
                        now - timedelta(minutes=primary_minutes),
                        window="primary",
                    )
                    if primary_rows:
                        summary = usage_core.summarize_usage_window(
                            [row.to_window_row() for row in primary_rows],
                            account_map,
                            "primary",
                        )
                        headers.update(_rate_limit_headers("primary", summary))

                secondary_minutes = await repos.usage.latest_window_minutes("secondary")
                if secondary_minutes is None:
                    secondary_minutes = usage_core.default_window_minutes("secondary")
                if secondary_minutes:
                    secondary_rows = await repos.usage.aggregate_since(
                        now - timedelta(minutes=secondary_minutes),
                        window="secondary",
                    )
                    if secondary_rows:
                        summary = usage_core.summarize_usage_window(
                            [row.to_window_row() for row in secondary_rows],
                            account_map,
                            "secondary",
                        )
                        headers.update(_rate_limit_headers("secondary", summary))

                latest_usage = await repos.usage.latest_by_account()
                headers.update(_credits_headers(latest_usage.values()))
            return headers

        return await get_or_build_rate_limit_headers(_build)

    async def get_rate_limit_payload(self) -> RateLimitStatusPayloadData:
        async with self._repo_factory() as repos:
            accounts = await repos.accounts.list_accounts()
            selected_accounts = _select_accounts_for_limits(accounts)
            if not selected_accounts:
                return RateLimitStatusPayloadData(plan_type="guest")

            account_map = {account.id: account for account in selected_accounts}
            primary_rows = await self._latest_usage_rows(repos, account_map, "primary")
            secondary_rows = await self._latest_usage_rows(repos, account_map, "secondary")

            primary_summary = _summarize_window(primary_rows, account_map, "primary")
            secondary_summary = _summarize_window(secondary_rows, account_map, "secondary")

            now_epoch = int(time.time())
            primary_window = _window_snapshot(primary_summary, primary_rows, "primary", now_epoch)
            secondary_window = _window_snapshot(secondary_summary, secondary_rows, "secondary", now_epoch)

            return RateLimitStatusPayloadData(
                plan_type=_plan_type_for_accounts(selected_accounts),
                rate_limit=_rate_limit_details(primary_window, secondary_window),
                credits=_credits_snapshot(await self._latest_usage_entries(repos, account_map)),
            )

    async def _stream_with_retry(
        self,
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        *,
        propagate_http_errors: bool,
        api: str,
        suppress_text_done_events: bool,
    ) -> AsyncIterator[str]:
        request_id = ensure_request_id()
        sticky_key = _sticky_key_from_payload(payload)
        prompt_cache_key_hash = _maybe_prompt_cache_key_hash(sticky_key)
        retryable_codes = {
            "rate_limit_exceeded",
            "usage_limit_reached",
            "insufficient_quota",
            "usage_not_included",
            "quota_exceeded",
        }
        emitted_any = False
        # Account failover happens inside this loop. When upstream errors are marked retryable and
        # we haven't emitted any SSE output yet, we re-select an account (`reallocate_sticky=True`)
        # and try again. After `max_attempts`, we surface the last upstream HTTP error to the client.
        max_attempts = 3
        last_retryable_error: ProxyResponseError | None = None
        for attempt in range(max_attempts):
            selection = await self._load_balancer.select_account(
                sticky_key=sticky_key,
                reallocate_sticky=attempt > 0,
            )
            account = selection.account
            if not account:
                if propagate_http_errors and last_retryable_error is not None:
                    raise last_retryable_error
                event = response_failed_event(
                    "no_accounts",
                    selection.error_message or "No active accounts available",
                    response_id=request_id,
                )
                yield format_sse_event(event)
                return

            account_id_value = account.id
            try:
                account = await self._ensure_fresh_if_needed(account)
                async for line in self._stream_once(
                    account,
                    payload,
                    headers,
                    request_id,
                    attempt < max_attempts - 1,
                    prompt_cache_key_hash=prompt_cache_key_hash,
                    api=api,
                    suppress_text_done_events=suppress_text_done_events,
                ):
                    emitted_any = True
                    yield line
                return
            except _RetryableStreamError as exc:
                await self._handle_stream_error(account, exc.error, exc.code)
                get_metrics().observe_proxy_retry(
                    api=api,
                    error_code=exc.code,
                    account_id=account_id_value,
                )
                continue
            except ProxyResponseError as exc:
                if exc.status_code == 401:
                    try:
                        account = await self._ensure_fresh(account, force=True)
                    except RefreshError as refresh_exc:
                        if refresh_exc.is_permanent:
                            await self._load_balancer.mark_permanent_failure(account, refresh_exc.code)
                        continue
                    async for line in self._stream_once(
                        account,
                        payload,
                        headers,
                        request_id,
                        attempt < max_attempts - 1,
                        suppress_text_done_events=suppress_text_done_events,
                        prompt_cache_key_hash=prompt_cache_key_hash,
                        api=api,
                    ):
                        emitted_any = True
                        yield line
                    return
                error = _parse_openai_error(exc.payload)
                error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
                error_message = error.message if error else None
                error_type = error.type if error else None
                error_param = error.param if error else None
                await self._handle_stream_error(
                    account,
                    _upstream_error_from_openai(error),
                    error_code,
                )
                if not emitted_any and error_code in retryable_codes and attempt < (max_attempts - 1):
                    get_metrics().observe_proxy_retry(
                        api=api,
                        error_code=error_code,
                        account_id=account_id_value,
                    )
                    last_retryable_error = exc
                    continue
                if propagate_http_errors:
                    raise
                event = response_failed_event(
                    error_code,
                    error_message or "Upstream error",
                    error_type=error_type or "server_error",
                    response_id=request_id,
                    error_param=error_param,
                )
                _apply_error_metadata(event["response"]["error"], error)
                yield format_sse_event(event)
                return
            except RefreshError as exc:
                if exc.is_permanent:
                    await self._load_balancer.mark_permanent_failure(account, exc.code)
                continue
            except Exception:
                try:
                    await self._load_balancer.record_error(account)
                except Exception:
                    logger.warning(
                        "Failed to record proxy error account_id=%s request_id=%s",
                        account_id_value,
                        request_id,
                        exc_info=True,
                    )
                if attempt == max_attempts - 1:
                    event = response_failed_event(
                        "upstream_error",
                        "Proxy streaming failed",
                        response_id=request_id,
                    )
                    yield format_sse_event(event)
                    return
        event = response_failed_event(
            "no_accounts",
            "No available accounts after retries",
            response_id=request_id,
        )
        yield format_sse_event(event)

    async def _stream_once(
        self,
        account: Account,
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        request_id: str,
        allow_retry: bool,
        *,
        prompt_cache_key_hash: str | None,
        api: str,
        suppress_text_done_events: bool,
    ) -> AsyncIterator[str]:
        account_id_value = account.id
        access_token = self._encryptor.decrypt(account.access_token_encrypted)
        account_id = _header_account_id(account.chatgpt_account_id)
        codex_session_id = self._optional_header_value(headers, "x-codex-session-id")
        codex_conversation_id = self._optional_header_value(headers, "x-codex-conversation-id")
        model = payload.model
        reasoning_effort = payload.reasoning.effort if payload.reasoning else None
        start = time.monotonic()
        status = "success"
        error_code = None
        error_message = None
        usage = None
        saw_text_delta = False

        try:
            stream = core_stream_responses(
                payload,
                headers,
                access_token,
                account_id,
                raise_for_status=True,
            )
            iterator = stream.__aiter__()
            try:
                first = await iterator.__anext__()
            except StopAsyncIteration:
                return
            first_payload = parse_sse_data_json(first)
            event = parse_sse_event(first)
            event_type = _event_type_from_payload(event, first_payload)
            if event and event.type in ("response.failed", "error"):
                if event.type == "response.failed":
                    response = event.response
                    error = response.error if response else None
                else:
                    error = event.error
                code = _normalize_error_code(
                    error.code if error else None,
                    error.type if error else None,
                )
                status = "error"
                error_code = code
                error_message = error.message if error else None
                if allow_retry:
                    error_payload = _upstream_error_from_openai(error)
                    raise _RetryableStreamError(code, error_payload)

            if event and event.type in ("response.completed", "response.incomplete"):
                usage = event.response.usage if event.response else None
                if event.type == "response.incomplete":
                    status = "error"

            if suppress_text_done_events and event_type in _TEXT_DELTA_EVENT_TYPES:
                saw_text_delta = True
            if not _should_suppress_text_done_event(
                event_type=event_type,
                payload=first_payload,
                suppress_text_done_events=suppress_text_done_events,
                saw_text_delta=saw_text_delta,
            ):
                yield first

            async for line in iterator:
                event_payload = parse_sse_data_json(line)
                event = parse_sse_event(line)
                event_type = _event_type_from_payload(event, event_payload)
                if suppress_text_done_events and event_type in _TEXT_DELTA_EVENT_TYPES:
                    saw_text_delta = True
                if _should_suppress_text_done_event(
                    event_type=event_type,
                    payload=event_payload,
                    suppress_text_done_events=suppress_text_done_events,
                    saw_text_delta=saw_text_delta,
                ):
                    continue
                if event:
                    if event_type in ("response.failed", "error"):
                        status = "error"
                        if event_type == "response.failed":
                            response = event.response
                            error = response.error if response else None
                        else:
                            error = event.error
                        error_code = _normalize_error_code(
                            error.code if error else None,
                            error.type if error else None,
                        )
                        error_message = error.message if error else None
                    if event_type in ("response.completed", "response.incomplete"):
                        usage = event.response.usage if event.response else None
                        if event_type == "response.incomplete":
                            status = "error"
                yield line
        except ProxyResponseError as exc:
            error = _parse_openai_error(exc.payload)
            status = "error"
            error_code = _normalize_error_code(
                error.code if error else None,
                error.type if error else None,
            )
            error_message = error.message if error else None
            raise
        finally:
            latency_ms = int((time.monotonic() - start) * 1000)
            input_tokens = usage.input_tokens if usage else None
            output_tokens = usage.output_tokens if usage else None
            cached_input_tokens = (
                usage.input_tokens_details.cached_tokens if usage and usage.input_tokens_details else None
            )
            reasoning_tokens = (
                usage.output_tokens_details.reasoning_tokens if usage and usage.output_tokens_details else None
            )
            with anyio.CancelScope(shield=True):
                try:
                    get_metrics().observe_proxy_request(
                        ProxyRequestObservation(
                            account_id=account_id_value,
                            api=api,
                            status=status,
                            model=model or "unknown",
                            latency_ms=latency_ms,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            cached_input_tokens=cached_input_tokens,
                            reasoning_tokens=reasoning_tokens,
                            error_code=error_code,
                        )
                    )
                    if get_settings().request_logs_buffer_enabled:
                        enqueue_request_log(
                            RequestLogCreate(
                                account_id=account_id_value,
                                request_id=request_id,
                                model=model,
                                input_tokens=input_tokens,
                                output_tokens=output_tokens,
                                cached_input_tokens=cached_input_tokens,
                                reasoning_tokens=reasoning_tokens,
                                reasoning_effort=reasoning_effort,
                                latency_ms=latency_ms,
                                status=status,
                                error_code=error_code,
                                error_message=error_message,
                                prompt_cache_key_hash=prompt_cache_key_hash,
                                codex_session_id=codex_session_id,
                                codex_conversation_id=codex_conversation_id,
                                requested_at=utcnow(),
                            )
                        )
                    else:
                        async with self._repo_factory() as repos:
                            await repos.request_logs.add_log(
                                account_id=account_id_value,
                                request_id=request_id,
                                model=model,
                                input_tokens=input_tokens,
                                output_tokens=output_tokens,
                                cached_input_tokens=cached_input_tokens,
                                reasoning_tokens=reasoning_tokens,
                                reasoning_effort=reasoning_effort,
                                latency_ms=latency_ms,
                                status=status,
                                error_code=error_code,
                                error_message=error_message,
                                prompt_cache_key_hash=prompt_cache_key_hash,
                                codex_session_id=codex_session_id,
                                codex_conversation_id=codex_conversation_id,
                            )
                except Exception:
                    logger.warning(
                        "Failed to persist request log account_id=%s request_id=%s",
                        account_id_value,
                        request_id,
                        exc_info=True,
                    )

    @staticmethod
    def _optional_header_value(headers: Mapping[str, str], name: str) -> str | None:
        # Headers are case-insensitive; normalize by scanning the inbound mapping.
        #
        # We intentionally store Codex session identifiers *raw* in the local DB to keep personal
        # debugging simple ("WHERE codex_session_id = ?") without requiring a hashing step.
        target = name.lower()
        for key, value in headers.items():
            if key.lower() == target:
                stripped = value.strip()
                return stripped or None
        return None

    async def _latest_usage_rows(
        self,
        repos: ProxyRepositories,
        account_map: dict[str, Account],
        window: str,
    ) -> list[UsageWindowRow]:
        if not account_map:
            return []
        latest = await repos.usage.latest_by_account(window=window)
        return [
            UsageWindowRow(
                account_id=entry.account_id,
                used_percent=entry.used_percent,
                reset_at=entry.reset_at,
                window_minutes=entry.window_minutes,
            )
            for entry in latest.values()
            if entry.account_id in account_map
        ]

    async def _latest_usage_entries(
        self,
        repos: ProxyRepositories,
        account_map: dict[str, Account],
    ) -> list[UsageHistory]:
        if not account_map:
            return []
        latest = await repos.usage.latest_by_account()
        return [entry for entry in latest.values() if entry.account_id in account_map]

    async def _ensure_fresh(self, account: Account, *, force: bool = False) -> Account:
        async with self._repo_factory() as repos:
            auth_manager = AuthManager(repos.accounts)
            return await auth_manager.ensure_fresh(account, force=force)

    async def _ensure_fresh_if_needed(self, account: Account) -> Account:
        if account.chatgpt_account_id and not should_refresh(account.last_refresh):
            return account
        return await self._ensure_fresh(account)

    async def _handle_proxy_error(self, account: Account, exc: ProxyResponseError) -> None:
        error = _parse_openai_error(exc.payload)
        code = _normalize_error_code(
            error.code if error else None,
            error.type if error else None,
        )
        await self._handle_stream_error(account, _upstream_error_from_openai(error), code)

    async def _handle_stream_error(self, account: Account, error: UpstreamError, code: str) -> None:
        # NOTE: `usage_limit_reached` is not guaranteed to mean "weekly quota is fully exhausted".
        # In practice it can appear transiently (and even be followed by successful requests) while
        # `/backend-api/wham/usage` still reports <100% used for the secondary window.
        #
        # Operationally, a "fail-open with cooldown/backoff" policy tends to be safer than locking
        # an account out for days on a single `usage_limit_reached`. If upstream *is* truly hard
        # limiting, repeated errors and/or a refreshed usage snapshot should confirm it.
        if code == "rate_limit_exceeded":
            await self._load_balancer.mark_rate_limit(account, error)
            return
        if code == "usage_limit_reached":
            await self._load_balancer.mark_usage_limit_reached(account, error)
            return
        if code in {"insufficient_quota", "usage_not_included", "quota_exceeded"}:
            await self._load_balancer.mark_quota_exceeded(account, error)
            return
        if code in PERMANENT_FAILURE_CODES:
            await self._load_balancer.mark_permanent_failure(account, code)
            return
        await self._load_balancer.record_error(account)


class _RetryableStreamError(Exception):
    def __init__(self, code: str, error: UpstreamError) -> None:
        super().__init__(code)
        self.code = code
        self.error = error


def _event_type_from_payload(event: OpenAIEvent | None, payload: dict[str, JsonValue] | None) -> str | None:
    if event is not None:
        return event.type
    if payload is None:
        return None
    payload_type = payload.get("type")
    if isinstance(payload_type, str):
        return payload_type
    return None


def _should_suppress_text_done_event(
    *,
    event_type: str | None,
    payload: dict[str, JsonValue] | None,
    suppress_text_done_events: bool,
    saw_text_delta: bool,
) -> bool:
    if not suppress_text_done_events or not saw_text_delta or event_type is None:
        return False
    if event_type == "response.output_text.done":
        return True
    if event_type == "response.content_part.done":
        return _is_text_content_part(payload)
    return False


def _is_text_content_part(payload: dict[str, JsonValue] | None) -> bool:
    if payload is None:
        return False
    part = payload.get("part")
    if not isinstance(part, dict):
        return False
    part_type = part.get("type")
    return isinstance(part_type, str) and part_type in _TEXT_DONE_CONTENT_PART_TYPES


def _maybe_log_proxy_request_shape(
    kind: str,
    payload: ResponsesRequest | ResponsesCompactRequest,
    headers: Mapping[str, str],
) -> None:
    settings = get_settings()
    if not settings.log_proxy_request_shape:
        return
    if not logger.isEnabledFor(logging.DEBUG):
        return

    request_id = get_request_id()
    prompt_cache_key = getattr(payload, "prompt_cache_key", None)
    if prompt_cache_key is None and payload.model_extra:
        extra_value = payload.model_extra.get("prompt_cache_key")
        if isinstance(extra_value, str):
            prompt_cache_key = extra_value
    prompt_cache_key_hash = _hash_identifier(prompt_cache_key) if isinstance(prompt_cache_key, str) else None
    prompt_cache_key_raw = (
        _truncate_identifier(prompt_cache_key)
        if settings.log_proxy_request_shape_raw_cache_key and isinstance(prompt_cache_key, str)
        else None
    )

    extra_keys = sorted(payload.model_extra.keys()) if payload.model_extra else []
    fields_set = sorted(payload.model_fields_set)
    input_summary = _summarize_input(payload.input)
    header_keys = _interesting_header_keys(headers)

    logger.debug(
        "proxy_request_shape request_id=%s kind=%s model=%s stream=%s input=%s "
        "prompt_cache_key=%s prompt_cache_key_raw=%s fields=%s extra=%s headers=%s",
        request_id,
        kind,
        payload.model,
        getattr(payload, "stream", None),
        input_summary,
        prompt_cache_key_hash,
        prompt_cache_key_raw,
        fields_set,
        extra_keys,
        header_keys,
    )


def _maybe_log_proxy_request_payload(
    kind: str,
    payload: ResponsesRequest | ResponsesCompactRequest,
    headers: Mapping[str, str],
) -> None:
    settings = get_settings()
    if not settings.log_proxy_request_payload:
        return
    if not logger.isEnabledFor(logging.DEBUG):
        return

    request_id = get_request_id()
    payload_dict = payload.model_dump(mode="json", exclude_none=True)
    extra = payload.model_extra or {}
    if extra:
        payload_dict = {**payload_dict, "_extra": extra}
    header_keys = _interesting_header_keys(headers)
    payload_json = json.dumps(payload_dict, ensure_ascii=True, separators=(",", ":"))

    logger.debug(
        "proxy_request_payload request_id=%s kind=%s payload=%s headers=%s",
        request_id,
        kind,
        payload_json,
        header_keys,
    )


def _hash_identifier(value: str) -> str:
    digest = sha256(value.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:12]}"


def _summarize_input(items: JsonValue) -> str:
    if items is None:
        return "0"
    if isinstance(items, str):
        return "str"
    if isinstance(items, Sequence) and not isinstance(items, (str, bytes, bytearray)):
        if not items:
            return "0"
        type_counts: dict[str, int] = {}
        for item in items:
            type_name = type(item).__name__
            type_counts[type_name] = type_counts.get(type_name, 0) + 1
        summary = ",".join(f"{key}={type_counts[key]}" for key in sorted(type_counts))
        return f"{len(items)}({summary})"
    return type(items).__name__


def _truncate_identifier(value: str, *, max_length: int = 96) -> str:
    if len(value) <= max_length:
        return value
    return f"{value[:48]}...{value[-16:]}"


def _interesting_header_keys(headers: Mapping[str, str]) -> list[str]:
    allowlist = {
        "user-agent",
        "x-request-id",
        "request-id",
        "x-openai-client-id",
        "x-openai-client-version",
        "x-openai-client-arch",
        "x-openai-client-os",
        "x-openai-client-user-agent",
        "x-codex-session-id",
        "x-codex-conversation-id",
    }
    return sorted({key.lower() for key in headers.keys() if key.lower() in allowlist})


def _sticky_key_from_payload(payload: ResponsesRequest) -> str | None:
    value = payload.prompt_cache_key
    if not value:
        return None
    stripped = value.strip()
    return stripped or None


def _sticky_key_from_compact_payload(payload: ResponsesCompactRequest) -> str | None:
    if not payload.model_extra:
        return None
    value = payload.model_extra.get("prompt_cache_key")
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
