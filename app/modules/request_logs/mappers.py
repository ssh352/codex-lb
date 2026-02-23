from __future__ import annotations

from typing import cast

from app.core.usage.logs import RequestLogLike, cached_input_tokens_from_log, cost_from_log, total_tokens_from_log
from app.db.models import RequestLog
from app.modules.request_logs.schemas import RequestLogEntry

# `usage_limit_reached` may be emitted transiently upstream even when the usage meter is < 100%.
# We classify it as rate-limit-like for logs/UX purposes.
#
# Important distinction:
# - Request logs record raw upstream error codes/messages (e.g. `usage_limit_reached`).
# - Account status (`rate_limited` / `quota_exceeded`) is codex-lb policy + derived state and may
#   be driven by usage history (e.g. secondary window exhaustion) even if the most recent upstream
#   error was `usage_limit_reached`.
RATE_LIMIT_CODES = {"rate_limit_exceeded", "usage_limit_reached"}
QUOTA_CODES = {"insufficient_quota", "usage_not_included", "quota_exceeded"}


def log_status(log: RequestLog) -> str:
    if log.status == "success":
        return "ok"
    if log.error_code in RATE_LIMIT_CODES:
        return "rate_limit"
    if log.error_code in QUOTA_CODES:
        return "quota"
    return "error"


def to_request_log_entry(log: RequestLog) -> RequestLogEntry:
    log_like = cast(RequestLogLike, log)
    return RequestLogEntry(
        requested_at=log.requested_at,
        account_id=log.account_id,
        request_id=log.request_id,
        codex_session_id=log.codex_session_id,
        codex_conversation_id=log.codex_conversation_id,
        model=log.model,
        reasoning_effort=log.reasoning_effort,
        status=log_status(log),
        error_code=log.error_code,
        error_message=log.error_message,
        tokens=total_tokens_from_log(log_like),
        cached_input_tokens=cached_input_tokens_from_log(log_like),
        cost_usd=cost_from_log(log_like, precision=6),
        latency_ms=log.latency_ms,
    )
