from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from app.core.config.settings import get_settings
from app.db.models import AccountStatus
from app.db.sqlite_utils import sqlite_db_path_from_url

UTC = timezone.utc


def _dt_from_epoch_seconds(value: int | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(int(value), tz=UTC)


def _iso_utc_from_epoch_seconds(value: int | None) -> str | None:
    dt = _dt_from_epoch_seconds(value)
    return dt.isoformat() if dt is not None else None


def _now_epoch() -> int:
    return int(time.time())


def _format_epoch_and_iso(value: int | None) -> str:
    if value is None:
        return "null"
    return f"{int(value)} ({_iso_utc_from_epoch_seconds(int(value))})"


def _redact(value: str, *, keep_prefix: int = 0) -> str:
    raw = (value or "").strip()
    if not raw:
        return raw
    if keep_prefix > 0:
        return raw[:keep_prefix] + "<redacted>"
    return "<redacted>"


def _join_url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + path


def _load_json_response(response: Any) -> Any:
    raw = response.read()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        snippet = raw[:2000].decode("utf-8", errors="replace")
        raise RuntimeError(f"Invalid JSON response (first 2000 bytes): {snippet}") from exc


@dataclass(frozen=True, slots=True)
class HttpJsonResult:
    status_code: int
    payload: Any


def _http_json(
    *,
    method: str,
    url: str,
    timeout_seconds: float,
    body: Any | None = None,
    headers: dict[str, str] | None = None,
) -> HttpJsonResult:
    data: bytes | None = None
    resolved_headers: dict[str, str] = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        resolved_headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url=url, data=data, headers=resolved_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = _load_json_response(response)
            return HttpJsonResult(status_code=int(getattr(response, "status", 200)), payload=payload)
    except urllib.error.HTTPError as exc:
        payload: Any
        try:
            payload = json.loads(exc.read())
        except Exception:
            payload = {"error": {"message": exc.reason, "code": "http_error"}}
        return HttpJsonResult(status_code=int(exc.code), payload=payload)


def _sqlite_connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


@dataclass(frozen=True, slots=True)
class AccountDbRow:
    account_id: str
    chatgpt_account_id: str | None
    email: str
    plan_type: str
    last_refresh_raw: str
    created_at_raw: str
    status: str
    deactivation_reason: str | None
    reset_at: int | None


def _fetch_account_by_email(conn: sqlite3.Connection, *, email: str) -> AccountDbRow | None:
    cursor = conn.execute(
        """
        SELECT
            id,
            chatgpt_account_id,
            email,
            plan_type,
            last_refresh,
            created_at,
            status,
            deactivation_reason,
            reset_at
        FROM accounts
        WHERE email = ?
        LIMIT 1
        """.strip(),
        (email,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return AccountDbRow(
        account_id=str(row["id"]),
        chatgpt_account_id=str(row["chatgpt_account_id"]) if row["chatgpt_account_id"] is not None else None,
        email=str(row["email"]),
        plan_type=str(row["plan_type"]),
        last_refresh_raw=str(row["last_refresh"]),
        created_at_raw=str(row["created_at"]),
        status=str(row["status"]),
        deactivation_reason=str(row["deactivation_reason"]) if row["deactivation_reason"] is not None else None,
        reset_at=int(row["reset_at"]) if row["reset_at"] is not None else None,
    )


def _fetch_accounts_by_status(conn: sqlite3.Connection, *, status: str) -> list[AccountDbRow]:
    cursor = conn.execute(
        """
        SELECT
            id,
            chatgpt_account_id,
            email,
            plan_type,
            last_refresh,
            created_at,
            status,
            deactivation_reason,
            reset_at
        FROM accounts
        WHERE lower(status) = lower(?)
        ORDER BY email ASC
        """.strip(),
        (status,),
    )
    rows = cursor.fetchall()
    return [
        AccountDbRow(
            account_id=str(row["id"]),
            chatgpt_account_id=str(row["chatgpt_account_id"]) if row["chatgpt_account_id"] is not None else None,
            email=str(row["email"]),
            plan_type=str(row["plan_type"]),
            last_refresh_raw=str(row["last_refresh"]),
            created_at_raw=str(row["created_at"]),
            status=str(row["status"]),
            deactivation_reason=str(row["deactivation_reason"]) if row["deactivation_reason"] is not None else None,
            reset_at=int(row["reset_at"]) if row["reset_at"] is not None else None,
        )
        for row in rows
    ]


@dataclass(frozen=True, slots=True)
class UsageSnapshot:
    recorded_at_raw: str
    used_percent: float
    reset_at: int | None
    window_raw: str | None
    window_minutes: int | None

    @property
    def effective_window(self) -> Literal["primary", "secondary", "unknown"]:
        raw = (self.window_raw or "primary").strip().lower()
        minutes = int(self.window_minutes) if self.window_minutes is not None else None
        if raw == "primary" and minutes is not None and minutes >= 24 * 60:
            return "secondary"
        if raw == "primary":
            return "primary"
        if raw == "secondary":
            return "secondary"
        return "unknown"


def _fetch_latest_usage_snapshots(
    conn: sqlite3.Connection,
    *,
    account_id: str,
    limit: int = 250,
) -> tuple[UsageSnapshot | None, UsageSnapshot | None]:
    cursor = conn.execute(
        """
        SELECT
            recorded_at,
            used_percent,
            reset_at,
            window,
            window_minutes
        FROM usage_history
        WHERE account_id = ?
        ORDER BY recorded_at DESC, id DESC
        LIMIT ?
        """.strip(),
        (account_id, int(limit)),
    )
    primary: UsageSnapshot | None = None
    secondary: UsageSnapshot | None = None
    for row in cursor.fetchall():
        snap = UsageSnapshot(
            recorded_at_raw=str(row["recorded_at"]),
            used_percent=float(row["used_percent"]),
            reset_at=int(row["reset_at"]) if row["reset_at"] is not None else None,
            window_raw=str(row["window"]) if row["window"] is not None else None,
            window_minutes=int(row["window_minutes"]) if row["window_minutes"] is not None else None,
        )
        if primary is None and snap.effective_window == "primary":
            primary = snap
        if secondary is None and snap.effective_window == "secondary":
            secondary = snap
        if primary is not None and secondary is not None:
            break
    return primary, secondary


@dataclass(frozen=True, slots=True)
class RequestLogRow:
    requested_at_raw: str
    request_id: str
    model: str
    status: str
    error_code: str | None
    error_message: str | None
    latency_ms: int | None


def _fetch_recent_request_logs(
    conn: sqlite3.Connection,
    *,
    account_id: str,
    limit: int = 25,
) -> list[RequestLogRow]:
    cursor = conn.execute(
        """
        SELECT
            requested_at,
            request_id,
            model,
            status,
            error_code,
            error_message,
            latency_ms
        FROM request_logs
        WHERE account_id = ?
        ORDER BY requested_at DESC, id DESC
        LIMIT ?
        """.strip(),
        (account_id, int(limit)),
    )
    rows = cursor.fetchall()
    return [
        RequestLogRow(
            requested_at_raw=str(row["requested_at"]),
            request_id=str(row["request_id"]),
            model=str(row["model"]),
            status=str(row["status"]),
            error_code=str(row["error_code"]) if row["error_code"] is not None else None,
            error_message=str(row["error_message"]) if row["error_message"] is not None else None,
            latency_ms=int(row["latency_ms"]) if row["latency_ms"] is not None else None,
        )
        for row in rows
    ]


@dataclass(frozen=True, slots=True)
class ApiAccountSummary:
    account_id: str
    email: str
    status: str
    status_reset_at_raw: str | None
    reset_at_primary_raw: str | None
    reset_at_secondary_raw: str | None


def _parse_api_account_summary(item: Any) -> ApiAccountSummary:
    if not isinstance(item, dict):
        raise TypeError("Account summary must be an object")
    account_id = item.get("accountId") or item.get("account_id")
    email = item.get("email")
    status = item.get("status")
    if not isinstance(account_id, str) or not account_id.strip():
        raise ValueError("Missing or invalid accountId")
    if not isinstance(email, str) or not email.strip():
        raise ValueError(f"Missing or invalid email for accountId={account_id!r}")
    if not isinstance(status, str) or not status.strip():
        raise ValueError(f"Missing or invalid status for accountId={account_id!r}")
    status_reset_at = item.get("statusResetAt") or item.get("status_reset_at")
    reset_at_primary = item.get("resetAtPrimary") or item.get("reset_at_primary")
    reset_at_secondary = item.get("resetAtSecondary") or item.get("reset_at_secondary")
    return ApiAccountSummary(
        account_id=account_id,
        email=email,
        status=status,
        status_reset_at_raw=str(status_reset_at) if status_reset_at is not None else None,
        reset_at_primary_raw=str(reset_at_primary) if reset_at_primary is not None else None,
        reset_at_secondary_raw=str(reset_at_secondary) if reset_at_secondary is not None else None,
    )


def _fetch_api_accounts(*, base_url: str, timeout_seconds: float) -> list[ApiAccountSummary]:
    url = _join_url(base_url, "/api/accounts")
    result = _http_json(method="GET", url=url, timeout_seconds=timeout_seconds)
    if result.status_code != 200:
        raise RuntimeError(f"GET /api/accounts failed (HTTP {result.status_code})")
    if not isinstance(result.payload, dict):
        raise RuntimeError("Unexpected /api/accounts response shape (expected object)")
    raw_accounts = result.payload.get("accounts")
    if not isinstance(raw_accounts, list):
        raise RuntimeError("Unexpected /api/accounts response shape (expected accounts list)")
    return [_parse_api_account_summary(item) for item in raw_accounts]


def _fetch_api_overview_account(
    *,
    base_url: str,
    timeout_seconds: float,
    email: str,
) -> ApiAccountSummary | None:
    url = _join_url(base_url, "/api/dashboard/overview?requestLimit=25&requestOffset=0")
    result = _http_json(method="GET", url=url, timeout_seconds=timeout_seconds)
    if result.status_code != 200:
        raise RuntimeError(f"GET /api/dashboard/overview failed (HTTP {result.status_code})")
    if not isinstance(result.payload, dict):
        raise RuntimeError("Unexpected /api/dashboard/overview response shape (expected object)")
    raw_accounts = result.payload.get("accounts")
    if not isinstance(raw_accounts, list):
        raise RuntimeError("Unexpected /api/dashboard/overview response shape (expected accounts list)")
    for item in raw_accounts:
        summary = _parse_api_account_summary(item)
        if summary.email == email:
            return summary
    return None


def _effective_blocked_until_epoch(
    *,
    persisted_status: str,
    account_reset_at: int | None,
    primary_reset_at: int | None,
    secondary_reset_at: int | None,
) -> int | None:
    status = persisted_status.strip().lower()
    if status == "rate_limited":
        candidates = [v for v in (account_reset_at, primary_reset_at) if v is not None]
        return max(candidates) if candidates else None
    if status == "quota_exceeded":
        candidates = [v for v in (account_reset_at, secondary_reset_at) if v is not None]
        return max(candidates) if candidates else None
    return None


def _probe_account(
    *,
    base_url: str,
    timeout_seconds: float,
    account_id: str,
    model: str,
) -> HttpJsonResult:
    url = _join_url(base_url, "/v1/responses")
    headers = {"x-codex-lb-force-account-id": account_id}
    body = {"model": model, "input": "ping", "stream": False}
    return _http_json(method="POST", url=url, timeout_seconds=timeout_seconds, body=body, headers=headers)


@dataclass(frozen=True, slots=True)
class AccountReport:
    now_epoch: int
    now_iso_utc: str
    settings_store_db: str
    settings_accounts_db: str
    account: AccountDbRow
    usage_primary: UsageSnapshot | None
    usage_secondary: UsageSnapshot | None
    effective_blocked_until_epoch: int | None
    effective_blocked_until_iso_utc: str | None
    stale_blocked: bool
    recent_request_logs: list[RequestLogRow]
    api_accounts_row: ApiAccountSummary | None
    api_overview_row: ApiAccountSummary | None
    probe_status_code: int | None = None
    probe_error_code: str | None = None
    probe_error_message: str | None = None
    probe_resets_at: int | None = None
    probe_resets_in_seconds: float | None = None


def _choose_probe_model_from_logs(recent_request_logs: list[RequestLogRow]) -> str:
    # Prefer the most recent successful model for this account (highest chance the upstream accepts it).
    for entry in recent_request_logs:
        if entry.status == "success" and entry.model.strip():
            return entry.model
    for entry in recent_request_logs:
        if entry.model.strip():
            return entry.model
    # Fallback (overrideable via --probe-model).
    return "gpt-5.2"


def _render_text_report(report: AccountReport, *, redact: bool) -> str:
    account_id = _redact(report.account.account_id, keep_prefix=3) if redact else report.account.account_id
    email = _redact(report.account.email) if redact else report.account.email

    lines: list[str] = []
    lines.append(f"Now (UTC): {report.now_iso_utc} (epoch={report.now_epoch})")
    lines.append(f"accounts.db: {report.settings_accounts_db}")
    lines.append(f"store.db:    {report.settings_store_db}")
    lines.append("")
    lines.append("Account (accounts.db):")
    lines.append(f"- email:    {email}")
    lines.append(f"- id:       {account_id}")
    if report.account.chatgpt_account_id:
        chatgpt_account_id = (
            _redact(report.account.chatgpt_account_id, keep_prefix=3) if redact else report.account.chatgpt_account_id
        )
        lines.append(f"- chatgpt:  {chatgpt_account_id}")
    lines.append(f"- plan:     {report.account.plan_type}")
    lines.append(f"- status:   {report.account.status}")
    lines.append(f"- reset_at: {_format_epoch_and_iso(report.account.reset_at)}")
    lines.append(f"- last_refresh: {report.account.last_refresh_raw}")
    if report.account.deactivation_reason:
        lines.append(f"- deactivation_reason: {report.account.deactivation_reason}")
    lines.append("")

    def _usage_line(label: str, snap: UsageSnapshot | None) -> None:
        if snap is None:
            lines.append(f"{label}: null")
            return
        lines.append(
            f"{label}: recorded_at={snap.recorded_at_raw} used%={snap.used_percent:.2f} "
            f"reset_at={_format_epoch_and_iso(snap.reset_at)} window={snap.effective_window} "
            f"window_minutes={snap.window_minutes}"
        )

    lines.append("Usage (store.db):")
    _usage_line("- primary", report.usage_primary)
    _usage_line("- secondary", report.usage_secondary)
    lines.append("")

    effective = _format_epoch_and_iso(report.effective_blocked_until_epoch)
    lines.append(f"Effective blocked-until (from persisted status): {effective}")
    lines.append(f"Stale blocked? {report.stale_blocked}")
    if report.stale_blocked:
        lines.append(
            "Explanation: status is blocked in accounts.db, but effective blocked-until is in the past. "
            "codex-lb clears these stale states when /api/accounts or /api/dashboard/overview is queried."
        )
    lines.append("")

    lines.append("Recent request logs (store.db):")
    if not report.recent_request_logs:
        lines.append("- (none)")
    for entry in report.recent_request_logs[:10]:
        request_id = _redact(entry.request_id, keep_prefix=6) if redact else entry.request_id
        error_code = entry.error_code or ""
        error_message = (entry.error_message or "").replace("\n", " ").strip()
        if len(error_message) > 120:
            error_message = error_message[:117] + "..."
        suffix = ""
        if error_code or error_message:
            suffix = f" err={error_code} msg={error_message}"
        latency = f" latency_ms={entry.latency_ms}" if entry.latency_ms is not None else ""
        lines.append(f"- {entry.requested_at_raw} {entry.status}{latency} model={entry.model} req={request_id}{suffix}")
    lines.append("")

    lines.append("Dashboard API:")
    if report.api_accounts_row is None:
        lines.append("- /api/accounts: null (not found or failed)")
    else:
        api_id = (
            _redact(report.api_accounts_row.account_id, keep_prefix=3) if redact else report.api_accounts_row.account_id
        )
        lines.append(
            f"- /api/accounts: status={report.api_accounts_row.status} id={api_id} "
            f"status_reset_at={report.api_accounts_row.status_reset_at_raw}"
        )
    if report.api_overview_row is None:
        lines.append("- /api/dashboard/overview: null (not found or skipped/failed)")
    else:
        api_id = (
            _redact(report.api_overview_row.account_id, keep_prefix=3) if redact else report.api_overview_row.account_id
        )
        lines.append(
            f"- /api/dashboard/overview: status={report.api_overview_row.status} id={api_id} "
            f"status_reset_at={report.api_overview_row.status_reset_at_raw}"
        )
    lines.append("")

    if report.probe_status_code is not None:
        lines.append("Probe (forced account):")
        lines.append(f"- status_code: {report.probe_status_code}")
        if report.probe_error_code or report.probe_error_message:
            lines.append(f"- error_code: {report.probe_error_code}")
            lines.append(f"- error_message: {report.probe_error_message}")
        if report.probe_resets_at is not None:
            lines.append(f"- resets_at: {_format_epoch_and_iso(report.probe_resets_at)}")
        if report.probe_resets_in_seconds is not None:
            lines.append(f"- resets_in_seconds: {report.probe_resets_in_seconds}")

    return "\n".join(lines).rstrip() + "\n"


def _build_message_draft(report: AccountReport) -> str:
    effective = _format_epoch_and_iso(report.effective_blocked_until_epoch)
    return (
        "Account status update\n\n"
        f"- Current status (accounts.db): {report.account.status}\n"
        f"- Blocked-until (effective): {effective}\n\n"
        "If it recently flipped from rate_limited/quota_exceeded to active, that usually means the reset time has "
        "passed and codex-lb reconciled the stale blocked state when the dashboard/API was queried.\n"
    )


def _report_for_account(
    *,
    account: AccountDbRow,
    store_db_path: Path,
    accounts_db_path: Path,
    base_url: str,
    timeout_seconds: float,
    include_overview: bool,
    include_probe: bool,
    probe_model: str | None,
) -> AccountReport:
    now_epoch = _now_epoch()
    now_iso = datetime.fromtimestamp(now_epoch, tz=UTC).isoformat()

    with _sqlite_connect(store_db_path) as store_conn:
        usage_primary, usage_secondary = _fetch_latest_usage_snapshots(store_conn, account_id=account.account_id)
        recent_logs = _fetch_recent_request_logs(store_conn, account_id=account.account_id)

    primary_reset = usage_primary.reset_at if usage_primary is not None else None
    secondary_reset = usage_secondary.reset_at if usage_secondary is not None else None
    effective_blocked = _effective_blocked_until_epoch(
        persisted_status=account.status,
        account_reset_at=account.reset_at,
        primary_reset_at=primary_reset,
        secondary_reset_at=secondary_reset,
    )
    effective_iso = _iso_utc_from_epoch_seconds(effective_blocked) if effective_blocked is not None else None
    stale_blocked = bool(
        effective_blocked is not None
        and effective_blocked <= now_epoch
        and account.status.strip().lower() in {"rate_limited", "quota_exceeded"}
    )

    api_accounts_row: ApiAccountSummary | None = None
    api_overview_row: ApiAccountSummary | None = None
    try:
        api_accounts = _fetch_api_accounts(base_url=base_url, timeout_seconds=timeout_seconds)
        api_accounts_row = next((row for row in api_accounts if row.email == account.email), None)
    except Exception:
        api_accounts_row = None

    if include_overview:
        try:
            api_overview_row = _fetch_api_overview_account(
                base_url=base_url, timeout_seconds=timeout_seconds, email=account.email
            )
        except Exception:
            api_overview_row = None

    report = AccountReport(
        now_epoch=now_epoch,
        now_iso_utc=now_iso,
        settings_store_db=str(store_db_path),
        settings_accounts_db=str(accounts_db_path),
        account=account,
        usage_primary=usage_primary,
        usage_secondary=usage_secondary,
        effective_blocked_until_epoch=effective_blocked,
        effective_blocked_until_iso_utc=effective_iso,
        stale_blocked=stale_blocked,
        recent_request_logs=recent_logs,
        api_accounts_row=api_accounts_row,
        api_overview_row=api_overview_row,
    )

    if include_probe:
        model = probe_model or _choose_probe_model_from_logs(recent_logs)
        try:
            probe = _probe_account(
                base_url=base_url,
                timeout_seconds=timeout_seconds,
                account_id=account.account_id,
                model=model,
            )
            error_obj = probe.payload.get("error") if isinstance(probe.payload, dict) else None
            error_code: str | None = None
            error_message: str | None = None
            resets_at: int | None = None
            resets_in_seconds: float | None = None
            if isinstance(error_obj, dict):
                raw_code = error_obj.get("code") or error_obj.get("type")
                if isinstance(raw_code, str) and raw_code.strip():
                    error_code = raw_code.strip()
                raw_message = error_obj.get("message")
                if isinstance(raw_message, str) and raw_message.strip():
                    error_message = raw_message.strip()
                raw_resets_at = error_obj.get("resets_at")
                if isinstance(raw_resets_at, (int, float)):
                    resets_at = int(raw_resets_at)
                raw_resets_in = error_obj.get("resets_in_seconds")
                if isinstance(raw_resets_in, (int, float)) and float(raw_resets_in) >= 0:
                    resets_in_seconds = float(raw_resets_in)
            return replace(
                report,
                probe_status_code=int(probe.status_code),
                probe_error_code=error_code,
                probe_error_message=error_message,
                probe_resets_at=resets_at,
                probe_resets_in_seconds=resets_in_seconds,
            )
        except Exception as exc:
            return replace(
                report,
                probe_status_code=0,
                probe_error_code="probe_failed",
                probe_error_message=str(exc),
            )

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="codex-lb account status debugger (DB + dashboard APIs).")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--email", type=str, help="Account email to inspect.")
    target.add_argument("--account-id", type=str, help="Account id to inspect (from accounts.db).")
    target.add_argument(
        "--all-rate-limited",
        action="store_true",
        help="Inspect all accounts with status=rate_limited.",
    )
    target.add_argument(
        "--all-status",
        type=str,
        choices=["active", "rate_limited", "quota_exceeded", "paused", "deactivated"],
        help="Inspect all accounts in a given persisted status.",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:2455", help="codex-lb dashboard base URL.")
    parser.add_argument("--timeout", type=float, default=10.0, help="Per-request HTTP timeout seconds.")
    parser.add_argument("--skip-overview", action="store_true", help="Skip GET /api/dashboard/overview.")
    parser.add_argument("--probe", action="store_true", help="Force a live probe request via /v1/responses.")
    parser.add_argument("--probe-model", type=str, default=None, help="Model to use for --probe (default: auto).")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply a corrected status/reset_at to accounts.db based on probe result + usage reset timestamps.",
    )
    parser.add_argument(
        "--apply-dry-run",
        action="store_true",
        help="Compute the --apply update but do not write to accounts.db.",
    )
    parser.add_argument(
        "--message-draft",
        action="store_true",
        help="Print an owner-facing message draft (single account).",
    )
    parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format.")
    parser.add_argument("--redact", action="store_true", help="Redact PII (email/account_id/request_id).")
    args = parser.parse_args()

    settings = get_settings()
    store_db_path = sqlite_db_path_from_url(settings.database_url)
    accounts_db_path = sqlite_db_path_from_url(settings.accounts_database_url)
    if store_db_path is None or accounts_db_path is None:
        print("This debugger only supports SQLite database URLs.", file=sys.stderr)
        return 2
    if not store_db_path.exists():
        print(f"store.db not found: {store_db_path}", file=sys.stderr)
        return 2
    if not accounts_db_path.exists():
        print(f"accounts.db not found: {accounts_db_path}", file=sys.stderr)
        return 2

    with _sqlite_connect(accounts_db_path) as accounts_conn:
        accounts: list[AccountDbRow] = []
        if args.email:
            row = _fetch_account_by_email(accounts_conn, email=str(args.email))
            if row is None:
                print(f"Account not found in accounts.db for email={args.email!r}", file=sys.stderr)
                return 1
            accounts = [row]
        elif args.account_id:
            cursor = accounts_conn.execute(
                """
                SELECT
                    id,
                    chatgpt_account_id,
                    email,
                    plan_type,
                    last_refresh,
                    created_at,
                    status,
                    deactivation_reason,
                    reset_at
                FROM accounts
                WHERE id = ?
                LIMIT 1
                """.strip(),
                (str(args.account_id),),
            )
            raw = cursor.fetchone()
            if raw is None:
                print(f"Account not found in accounts.db for account_id={args.account_id!r}", file=sys.stderr)
                return 1
            accounts = [
                AccountDbRow(
                    account_id=str(raw["id"]),
                    chatgpt_account_id=(
                        str(raw["chatgpt_account_id"]) if raw["chatgpt_account_id"] is not None else None
                    ),
                    email=str(raw["email"]),
                    plan_type=str(raw["plan_type"]),
                    last_refresh_raw=str(raw["last_refresh"]),
                    created_at_raw=str(raw["created_at"]),
                    status=str(raw["status"]),
                    deactivation_reason=(
                        str(raw["deactivation_reason"]) if raw["deactivation_reason"] is not None else None
                    ),
                    reset_at=int(raw["reset_at"]) if raw["reset_at"] is not None else None,
                )
            ]
        else:
            status = "rate_limited" if args.all_rate_limited else str(args.all_status)
            accounts = _fetch_accounts_by_status(accounts_conn, status=status)

    if args.message_draft and len(accounts) != 1:
        print("--message-draft only supports a single account target.", file=sys.stderr)
        return 2
    if bool(args.apply) and len(accounts) != 1:
        print("--apply only supports a single account target.", file=sys.stderr)
        return 2
    if bool(args.apply) and not bool(args.probe):
        print("--apply requires --probe so status is based on a fresh upstream result.", file=sys.stderr)
        return 2

    reports = [
        _report_for_account(
            account=account,
            store_db_path=store_db_path,
            accounts_db_path=accounts_db_path,
            base_url=str(args.base_url),
            timeout_seconds=float(args.timeout),
            include_overview=not bool(args.skip_overview),
            include_probe=bool(args.probe),
            probe_model=str(args.probe_model) if args.probe_model else None,
        )
        for account in accounts
    ]

    if args.apply:
        report = reports[0]
        if report.probe_status_code is None:
            print("--apply requires --probe to produce a probe result.", file=sys.stderr)
            return 2

        desired_status: str | None = None
        desired_reset_at: int | None = None
        probe_code = (report.probe_error_code or "").strip().lower()

        if int(report.probe_status_code) == 200:
            desired_status = "ACTIVE"
            desired_reset_at = None
        elif int(report.probe_status_code) == 429:
            probe_reset_at = report.probe_resets_at
            primary_reset_at = report.usage_primary.reset_at if report.usage_primary is not None else None
            secondary_reset_at = report.usage_secondary.reset_at if report.usage_secondary is not None else None

            def _choose_reset(*candidates: int | None) -> int | None:
                values = [int(v) for v in candidates if v is not None]
                return max(values) if values else None

            if probe_code == "rate_limit_exceeded":
                desired_status = "RATE_LIMITED"
                desired_reset_at = _choose_reset(probe_reset_at, primary_reset_at)
            elif probe_code == "usage_limit_reached":
                desired_status = "RATE_LIMITED"
                desired_reset_at = _choose_reset(probe_reset_at, secondary_reset_at)
            elif probe_code in {"insufficient_quota", "usage_not_included", "quota_exceeded"}:
                desired_status = "QUOTA_EXCEEDED"
                desired_reset_at = _choose_reset(probe_reset_at, secondary_reset_at)
            else:
                print(
                    f"Unsupported probe error_code for --apply: {report.probe_error_code!r} (HTTP 429)",
                    file=sys.stderr,
                )
                return 2
        else:
            print(
                f"Unsupported probe status_code for --apply: {report.probe_status_code} "
                f"error_code={report.probe_error_code!r}",
                file=sys.stderr,
            )
            return 2

        if desired_status in {"RATE_LIMITED", "QUOTA_EXCEEDED"}:
            if desired_reset_at is None:
                print(
                    f"Cannot apply {desired_status}: no reset_at candidate available from probe/usage snapshots.",
                    file=sys.stderr,
                )
                return 2
            if int(desired_reset_at) <= int(report.now_epoch):
                print(
                    f"Cannot apply {desired_status}: computed reset_at={desired_reset_at} is not in the future.",
                    file=sys.stderr,
                )
                return 2

        status_value = AccountStatus[desired_status].value

        if args.apply_dry_run:
            print(
                "apply_dry_run=true "
                f"account_id={report.account.account_id} "
                f"status={status_value} "
                f"reset_at={_format_epoch_and_iso(desired_reset_at)}"
            )
        else:
            with _sqlite_connect(accounts_db_path) as conn:
                conn.execute(
                    """
                    UPDATE accounts
                    SET status = ?, reset_at = ?, deactivation_reason = NULL
                    WHERE id = ?
                    """.strip(),
                    (status_value, desired_reset_at, report.account.account_id),
                )
                conn.commit()
            print(
                "applied=true "
                f"account_id={report.account.account_id} "
                f"status={status_value} "
                f"reset_at={_format_epoch_and_iso(desired_reset_at)}"
            )

    if args.format == "json":

        def _to_json(report: AccountReport) -> dict[str, Any]:
            data = asdict(report)
            if args.redact:
                data["account"]["email"] = _redact(str(data["account"]["email"]))
                data["account"]["account_id"] = _redact(str(data["account"]["account_id"]), keep_prefix=3)
                if data["account"].get("chatgpt_account_id"):
                    data["account"]["chatgpt_account_id"] = _redact(
                        str(data["account"]["chatgpt_account_id"]), keep_prefix=3
                    )
                for entry in data.get("recent_request_logs", []):
                    if isinstance(entry, dict) and entry.get("request_id"):
                        entry["request_id"] = _redact(str(entry["request_id"]), keep_prefix=6)
                for key in ("api_accounts_row", "api_overview_row"):
                    row = data.get(key)
                    if isinstance(row, dict):
                        if row.get("email"):
                            row["email"] = _redact(str(row["email"]))
                        if row.get("account_id"):
                            row["account_id"] = _redact(str(row["account_id"]), keep_prefix=3)
            return data

        payload: Any
        if len(reports) == 1:
            payload = _to_json(reports[0])
        else:
            payload = [_to_json(r) for r in reports]
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.message_draft:
        print(_build_message_draft(reports[0]))
        return 0

    for idx, report in enumerate(reports):
        if idx:
            print("\n" + ("=" * 80) + "\n")
        sys.stdout.write(_render_text_report(report, redact=bool(args.redact)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
