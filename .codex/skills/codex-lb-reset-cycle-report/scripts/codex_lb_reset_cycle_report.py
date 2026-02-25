#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence


def _ensure_shared_on_syspath() -> None:
    this_file = Path(__file__).resolve()
    for parent in this_file.parents:
        if parent.name == "skills":
            shared = parent / "_shared"
            if shared.exists():
                shared_str = str(shared)
                if shared_str and shared_str not in sys.path:
                    sys.path.insert(0, shared_str)
            break


_ensure_shared_on_syspath()

from codex_lb_shared.utils import (  # noqa: E402
    UTC,
    dt_from_sqlite,
    parse_time_to_epoch_seconds,
    redact,
    sqlite_connect,
)


def _ensure_codex_lb_repo_on_syspath() -> None:
    # If this script is executed from e.g. `.claude/skills/...`, Python will put the
    # script directory on `sys.path`, not the repo root. Ensure the repo root is
    # importable so `app.core.usage.pricing` works without requiring installing the repo.
    candidates: list[Path] = [Path.cwd()]

    this_file = Path(__file__).resolve()
    for parent in this_file.parents:
        if (parent / "app" / "core" / "usage" / "pricing.py").exists():
            candidates.append(parent)
            break

    for candidate in candidates:
        candidate_str = str(candidate)
        if candidate_str and candidate_str not in sys.path:
            sys.path.insert(0, candidate_str)


def _import_pricing() -> tuple[Any, Any, Any]:
    _ensure_codex_lb_repo_on_syspath()
    try:
        from app.core.usage.pricing import UsageTokens, calculate_cost_from_usage, get_pricing_for_model
    except Exception as exc:  # pragma: no cover
        raise SystemExit(
            "Failed to import pricing from codex-lb repo. Run this from the repo root (or set "
            "`PYTHONPATH=.`) so `app.core.usage.pricing` is importable.\n"
            f"Import error: {exc}"
        ) from exc
    return UsageTokens, calculate_cost_from_usage, get_pricing_for_model


@dataclass(frozen=True, slots=True)
class CycleBounds:
    label: str
    start_epoch: int
    end_epoch: int
    window_minutes: int

    @property
    def start_utc(self) -> datetime:
        return datetime.fromtimestamp(self.start_epoch, tz=UTC)

    @property
    def end_utc(self) -> datetime:
        return datetime.fromtimestamp(self.end_epoch, tz=UTC)


@dataclass(frozen=True, slots=True)
class RequestLogRow:
    request_id: str
    requested_at: datetime
    model: str
    status: str
    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None


@dataclass(frozen=True, slots=True)
class UsagePoint:
    recorded_at: datetime
    used_percent: float


@dataclass(frozen=True, slots=True)
class CostedRequest:
    request_id: str
    requested_at: datetime
    model: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    usd: float


def _open_db(path: Path) -> sqlite3.Connection:
    try:
        return sqlite_connect(path, must_exist=True)
    except FileNotFoundError as exc:
        raise SystemExit(str(exc)) from exc


def _print_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
    rows_list = [list(row) for row in rows]
    widths = [len(h) for h in headers]
    for row in rows_list:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    numeric_re = re.compile(r"^\$?-?\d[\d,]*(\.\d+)?(%|pp)?$")

    def looks_numeric(cell: str) -> bool:
        return bool(numeric_re.match(cell.strip()))

    aligns: list[str] = []
    for i in range(len(headers)):
        col_cells = [row[i] for row in rows_list if i < len(row)]
        aligns.append("r" if col_cells and all(looks_numeric(c) for c in col_cells) else "l")

    def border(fill: str) -> str:
        # +---+ style borders where each column has 1-space padding on both sides.
        return "+" + "+".join(fill * (w + 2) for w in widths) + "+"

    def fmt_row(values: Sequence[str]) -> str:
        padded: list[str] = []
        for i in range(len(headers)):
            cell = values[i] if i < len(values) else ""
            padded.append(cell.rjust(widths[i]) if aligns[i] == "r" else cell.ljust(widths[i]))
        return "| " + " | ".join(padded) + " |"

    print(border("-"))
    print(fmt_row(list(headers)))
    print(border("="))
    for row in rows_list:
        print(fmt_row(row))
    print(border("-"))


def _resolve_account_id(accounts_conn: sqlite3.Connection, email: str) -> str:
    row = accounts_conn.execute("select id from accounts where email = ? limit 1", (email,)).fetchone()
    if not row:
        raise SystemExit(f"Email not found in accounts DB: {email}")
    account_id = row["id"]
    if not isinstance(account_id, str) or not account_id:
        raise SystemExit(f"Invalid account_id for email {email}: {account_id!r}")
    return account_id


def _latest_window_config(store_conn: sqlite3.Connection, *, account_id: str, window: str) -> tuple[int, int]:
    row = store_conn.execute(
        """
        select reset_at, window_minutes
        from usage_history
        where account_id = ? and window = ?
        order by recorded_at desc
        limit 1
        """,
        (account_id, window),
    ).fetchone()
    if not row:
        raise SystemExit(f"No usage_history rows for account_id={account_id!r} window={window!r}")

    reset_at = row["reset_at"]
    window_minutes = row["window_minutes"]
    if not isinstance(reset_at, int) or reset_at <= 0:
        raise SystemExit(f"Invalid reset_at in usage_history: {reset_at!r}")
    if not isinstance(window_minutes, int) or window_minutes <= 0:
        raise SystemExit(f"Invalid window_minutes in usage_history: {window_minutes!r}")
    return reset_at, window_minutes


def _compute_cycles(*, reset_at: int, window_minutes: int, which: str) -> list[CycleBounds]:
    win_secs = window_minutes * 60
    current_start = reset_at - win_secs
    cycles: list[CycleBounds] = []

    if which in {"current", "both"}:
        cycles.append(
            CycleBounds(
                label="current",
                start_epoch=current_start,
                end_epoch=reset_at,
                window_minutes=window_minutes,
            )
        )

    if which in {"previous", "both"}:
        cycles.append(
            CycleBounds(
                label="previous",
                start_epoch=current_start - win_secs,
                end_epoch=current_start,
                window_minutes=window_minutes,
            )
        )

    if not cycles:
        raise SystemExit(f"Invalid --cycles value: {which!r}")
    return cycles


def _fetch_request_logs(
    store_conn: sqlite3.Connection,
    *,
    account_id: str,
    start_epoch: int,
    end_epoch: int,
) -> list[RequestLogRow]:
    rows = store_conn.execute(
        """
        select request_id, requested_at, model, status, input_tokens, output_tokens, cached_input_tokens
        from request_logs
        where account_id = ?
          and requested_at >= datetime(?, 'unixepoch')
          and requested_at <  datetime(?, 'unixepoch')
        order by requested_at asc
        """,
        (account_id, start_epoch, end_epoch),
    ).fetchall()

    result: list[RequestLogRow] = []
    for row in rows:
        requested_at_raw = row["requested_at"]
        if not isinstance(requested_at_raw, str):
            continue

        request_id = row["request_id"]
        model = row["model"]
        status = row["status"]
        if not (isinstance(request_id, str) and isinstance(model, str) and isinstance(status, str)):
            continue

        input_tokens = row["input_tokens"] if isinstance(row["input_tokens"], int) else None
        output_tokens = row["output_tokens"] if isinstance(row["output_tokens"], int) else None
        cached_input_tokens = row["cached_input_tokens"] if isinstance(row["cached_input_tokens"], int) else None

        result.append(
            RequestLogRow(
                request_id=request_id,
                requested_at=dt_from_sqlite(requested_at_raw),
                model=model,
                status=status,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_input_tokens,
            )
        )
    return result


def _fetch_usage_points(
    store_conn: sqlite3.Connection,
    *,
    account_id: str,
    window: str,
    start_epoch: int,
    end_epoch: int,
) -> list[UsagePoint]:
    before = store_conn.execute(
        """
        select recorded_at, used_percent
        from usage_history
        where account_id = ? and window = ?
          and recorded_at < datetime(?, 'unixepoch')
        order by recorded_at desc
        limit 1
        """,
        (account_id, window, start_epoch),
    ).fetchall()
    inside = store_conn.execute(
        """
        select recorded_at, used_percent
        from usage_history
        where account_id = ? and window = ?
          and recorded_at >= datetime(?, 'unixepoch')
          and recorded_at <  datetime(?, 'unixepoch')
        order by recorded_at asc
        """,
        (account_id, window, start_epoch, end_epoch),
    ).fetchall()

    points: list[UsagePoint] = []
    for row in list(before) + list(inside):
        recorded_at_raw = row["recorded_at"]
        used_percent = row["used_percent"]
        if not (isinstance(recorded_at_raw, str) and isinstance(used_percent, (int, float))):
            continue
        points.append(UsagePoint(recorded_at=dt_from_sqlite(recorded_at_raw), used_percent=float(used_percent)))
    points.sort(key=lambda p: p.recorded_at)
    return points


def _cost_requests(
    logs: Iterable[RequestLogRow],
) -> tuple[list[CostedRequest], dict[str, float], dict[str, int]]:
    UsageTokens, calculate_cost_from_usage, get_pricing_for_model = _import_pricing()

    costed: list[CostedRequest] = []
    usd_by_model: dict[str, float] = {}
    counters: dict[str, int] = {
        "success_total": 0,
        "success_missing_tokens": 0,
        "success_unpriced_model": 0,
        "success_unpriced_or_missing": 0,
        "success_priced": 0,
    }

    for log in logs:
        if log.status != "success":
            continue
        counters["success_total"] += 1

        if log.input_tokens is None or log.output_tokens is None:
            counters["success_missing_tokens"] += 1
            counters["success_unpriced_or_missing"] += 1
            continue

        cached = log.cached_input_tokens or 0
        cached = max(0, min(cached, log.input_tokens))

        resolved = get_pricing_for_model(log.model)
        if not resolved:
            counters["success_unpriced_model"] += 1
            counters["success_unpriced_or_missing"] += 1
            continue
        canonical, price = resolved

        usage = UsageTokens(
            input_tokens=float(log.input_tokens),
            output_tokens=float(log.output_tokens),
            cached_input_tokens=float(cached),
        )
        usd = calculate_cost_from_usage(usage, price)
        if usd is None or usd < 0:
            counters["success_unpriced_or_missing"] += 1
            continue

        counters["success_priced"] += 1
        usd_by_model[canonical] = usd_by_model.get(canonical, 0.0) + float(usd)
        costed.append(
            CostedRequest(
                request_id=log.request_id,
                requested_at=log.requested_at,
                model=canonical,
                input_tokens=log.input_tokens,
                output_tokens=log.output_tokens,
                cached_input_tokens=cached,
                usd=float(usd),
            )
        )

    return costed, usd_by_model, counters


def _analyze_jumps(
    *,
    usage_points: Sequence[UsagePoint],
    request_times: Sequence[datetime],
    success_times: Sequence[datetime],
    threshold_pp: float,
) -> tuple[list[tuple[UsagePoint, UsagePoint, float, int, int]], list[tuple[UsagePoint, UsagePoint, float]]]:
    if len(usage_points) < 2:
        return [], []

    big: list[tuple[UsagePoint, UsagePoint, float, int, int]] = []
    suspect: list[tuple[UsagePoint, UsagePoint, float]] = []

    req_idx = 0
    succ_idx = 0
    for i in range(len(usage_points) - 1):
        p1 = usage_points[i]
        p2 = usage_points[i + 1]
        delta_pp = p2.used_percent - p1.used_percent

        t1 = p1.recorded_at
        t2 = p2.recorded_at
        if t2 <= t1:
            continue

        while req_idx < len(request_times) and request_times[req_idx] < t1:
            req_idx += 1
        req_j = req_idx
        while req_j < len(request_times) and request_times[req_j] < t2:
            req_j += 1
        reqs = req_j - req_idx
        req_idx = req_j

        while succ_idx < len(success_times) and success_times[succ_idx] < t1:
            succ_idx += 1
        succ_j = succ_idx
        while succ_j < len(success_times) and success_times[succ_j] < t2:
            succ_j += 1
        succ = succ_j - succ_idx
        succ_idx = succ_j

        if delta_pp >= threshold_pp:
            big.append((p1, p2, delta_pp, reqs, succ))
        if delta_pp > 0 and reqs == 0:
            suspect.append((p1, p2, delta_pp))

    return big, suspect


def _fmt_dt(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _report_cycle(
    *,
    store_conn: sqlite3.Connection,
    account_id: str,
    window: str,
    bounds: CycleBounds,
    jump_threshold_pp: float,
    top_n: int,
    redact_pii: bool,
    emit: bool,
) -> dict[str, Any]:
    logs = _fetch_request_logs(
        store_conn,
        account_id=account_id,
        start_epoch=bounds.start_epoch,
        end_epoch=bounds.end_epoch,
    )
    total_requests = len(logs)
    success_requests = sum(1 for r in logs if r.status == "success")
    error_requests = sum(1 for r in logs if r.status == "error")

    costed, usd_by_model, counters = _cost_requests(logs)
    total_usd = sum(r.usd for r in costed)

    input_tokens = sum(r.input_tokens or 0 for r in logs if r.status == "success")
    cached_tokens = sum(r.cached_input_tokens or 0 for r in logs if r.status == "success")
    output_tokens = sum(r.output_tokens or 0 for r in logs if r.status == "success")
    cached_tokens = max(0, min(cached_tokens, input_tokens))

    avg_usd = (total_usd / counters["success_priced"]) if counters["success_priced"] else 0.0

    if emit:
        print()
        print(f"== {bounds.label.upper()} CYCLE ==")
        print(f"Window: {window} ({bounds.window_minutes} minutes)")
        print(f"Start:  {_fmt_dt(bounds.start_utc)}")
        print(f"End:    {_fmt_dt(bounds.end_utc)}")
        print(f"Requests: {total_requests} total ({success_requests} success, {error_requests} error)")
        print(
            "Success priced: "
            f"{counters['success_priced']}/{counters['success_total']} "
            f"(missing tokens: {counters['success_missing_tokens']}, "
            f"unpriced model: {counters['success_unpriced_model']})"
        )
        print(
            "Tokens (success-only): "
            f"input={input_tokens} cached_input={cached_tokens} billable_input={max(0, input_tokens - cached_tokens)} "
            f"output={output_tokens}"
        )
        print(f"USD (estimated): total=${total_usd:.6f} avg_per_priced_success=${avg_usd:.6f}")

    if emit and usd_by_model:
        model_rows = [(model, f"${usd_by_model[model]:.6f}") for model in sorted(usd_by_model)]
        print()
        print("Cost by model:")
        _print_table(["model", "usd"], model_rows)

    top_expensive: list[dict[str, Any]] = []
    if costed and top_n > 0:
        top = sorted(costed, key=lambda r: r.usd, reverse=True)[:top_n]

        def _req_id(req_id: str) -> str:
            return redact(req_id, keep_prefix=6) if redact_pii else req_id

        top_expensive = [
            {
                "requested_at_iso_utc": r.requested_at.astimezone(UTC).isoformat(),
                "model": r.model,
                "usd": float(r.usd),
                "input_tokens": int(r.input_tokens),
                "cached_input_tokens": int(r.cached_input_tokens),
                "output_tokens": int(r.output_tokens),
                "request_id": _req_id(r.request_id),
            }
            for r in top
        ]

    if emit and top_expensive:
        rows = [
            (
                _fmt_dt(datetime.fromisoformat(item["requested_at_iso_utc"])),
                str(item["model"]),
                f"${float(item['usd']):.6f}",
                str(item["input_tokens"]),
                str(item["cached_input_tokens"]),
                str(item["output_tokens"]),
                str(item["request_id"]),
            )
            for item in top_expensive
        ]
        print()
        print(f"Top {len(top)} expensive requests:")
        _print_table(["requested_at", "model", "usd", "in_toks", "cached", "out_toks", "request_id"], rows)

    usage_points = _fetch_usage_points(
        store_conn,
        account_id=account_id,
        window=window,
        start_epoch=bounds.start_epoch,
        end_epoch=bounds.end_epoch,
    )
    req_times = [r.requested_at for r in logs]
    succ_times = [r.requested_at for r in logs if r.status == "success"]
    big, suspect = _analyze_jumps(
        usage_points=usage_points,
        request_times=req_times,
        success_times=succ_times,
        threshold_pp=jump_threshold_pp,
    )

    usage_jumps: list[dict[str, Any]] = [
        {
            "t1_iso_utc": p1.recorded_at.astimezone(UTC).isoformat(),
            "t2_iso_utc": p2.recorded_at.astimezone(UTC).isoformat(),
            "p1_used_percent": float(p1.used_percent),
            "p2_used_percent": float(p2.used_percent),
            "delta_pp": float(delta_pp),
            "requests_between": int(reqs),
            "success_between": int(succ),
        }
        for (p1, p2, delta_pp, reqs, succ) in sorted(big, key=lambda x: x[2], reverse=True)
    ]
    suspect_outside_usage: list[dict[str, Any]] = [
        {
            "t1_iso_utc": p1.recorded_at.astimezone(UTC).isoformat(),
            "t2_iso_utc": p2.recorded_at.astimezone(UTC).isoformat(),
            "p1_used_percent": float(p1.used_percent),
            "p2_used_percent": float(p2.used_percent),
            "delta_pp": float(delta_pp),
        }
        for (p1, p2, delta_pp) in sorted(suspect, key=lambda x: x[2], reverse=True)
    ]

    if emit and usage_jumps:
        print()
        print(f"Usage jumps (>= {jump_threshold_pp:g}pp):")
        rows = [
            (
                _fmt_dt(p1.recorded_at),
                _fmt_dt(p2.recorded_at),
                f"{p1.used_percent:.1f}%",
                f"{p2.used_percent:.1f}%",
                f"+{delta_pp:.1f}pp",
                str(reqs),
                str(succ),
            )
            for (p1, p2, delta_pp, reqs, succ) in sorted(big, key=lambda x: x[2], reverse=True)
        ]
        _print_table(["t1", "t2", "p1", "p2", "delta", "reqs", "succ"], rows)

    if emit and suspect_outside_usage:
        print()
        print("Suspect outside usage (percent increased, but 0 codex-lb requests between samples):")
        rows = [
            (
                _fmt_dt(p1.recorded_at),
                _fmt_dt(p2.recorded_at),
                f"{p1.used_percent:.1f}%",
                f"{p2.used_percent:.1f}%",
                f"+{delta_pp:.1f}pp",
            )
            for (p1, p2, delta_pp) in sorted(suspect, key=lambda x: x[2], reverse=True)
        ]
        _print_table(["t1", "t2", "p1", "p2", "delta"], rows)

    return {
        "label": bounds.label,
        "window": window,
        "start_epoch": int(bounds.start_epoch),
        "end_epoch": int(bounds.end_epoch),
        "start_iso_utc": bounds.start_utc.astimezone(UTC).isoformat(),
        "end_iso_utc": bounds.end_utc.astimezone(UTC).isoformat(),
        "window_minutes": int(bounds.window_minutes),
        "requests_total": int(total_requests),
        "requests_success": int(success_requests),
        "requests_error": int(error_requests),
        "success_total": int(counters["success_total"]),
        "success_priced": int(counters["success_priced"]),
        "success_missing_tokens": int(counters["success_missing_tokens"]),
        "success_unpriced_model": int(counters["success_unpriced_model"]),
        "tokens_success_input": int(input_tokens),
        "tokens_success_cached_input": int(cached_tokens),
        "tokens_success_output": int(output_tokens),
        "usd_total_estimated": float(total_usd),
        "usd_avg_per_priced_success": float(avg_usd),
        "usd_by_model": {k: float(v) for k, v in usd_by_model.items()},
        "top_expensive": top_expensive,
        "usage_jumps": usage_jumps,
        "suspect_outside_usage": suspect_outside_usage,
    }


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manual codex-lb reset-cycle report from ~/.codex-lb DBs")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--email", help="Account email (looked up in accounts.db)")
    target.add_argument("--account-id", help="Account id (from accounts.db)")
    parser.add_argument(
        "--accounts-db",
        default="~/.codex-lb/accounts.db",
        help="Path to accounts.db (default: ~/.codex-lb/accounts.db)",
    )
    parser.add_argument(
        "--store-db",
        default="~/.codex-lb/store.db",
        help="Path to store.db (default: ~/.codex-lb/store.db)",
    )
    parser.add_argument(
        "--window",
        default="secondary",
        choices=["primary", "secondary"],
        help="usage_history window to use (default: secondary)",
    )
    parser.add_argument(
        "--cycles",
        default="both",
        choices=["current", "previous", "both"],
        help="Which cycle(s) to report (default: both)",
    )
    parser.add_argument(
        "--since",
        default="",
        help="Clamp start time (epoch seconds or ISO-8601; UTC if no TZ).",
    )
    parser.add_argument(
        "--until",
        default="",
        help="Clamp end time (epoch seconds or ISO-8601; UTC if no TZ).",
    )
    parser.add_argument(
        "--jump-threshold-pp",
        type=float,
        default=10.0,
        help="Flag usage jumps at/above this many percentage-points (default: 10)",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="Show top N most expensive requests (default: 10)",
    )
    parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format.")
    parser.add_argument("--redact", action="store_true", help="Redact PII (email/account_id/request_id).")
    return parser.parse_args(list(argv))


def _resolve_email_and_account_id(
    accounts_conn: sqlite3.Connection,
    *,
    email: str | None,
    account_id: str | None,
) -> tuple[str, str | None]:
    if email:
        resolved_id = _resolve_account_id(accounts_conn, email)
        return resolved_id, email
    if not account_id:
        raise SystemExit("Missing --email or --account-id")
    row = accounts_conn.execute("select email from accounts where id = ? limit 1", (account_id,)).fetchone()
    resolved_email: str | None = None
    if row and isinstance(row["email"], str) and row["email"].strip():
        resolved_email = str(row["email"])
    return str(account_id), resolved_email


def _parse_optional_epoch(value: str) -> int | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return int(parse_time_to_epoch_seconds(raw))
    except Exception as exc:
        raise SystemExit(f"Invalid time value {value!r}: {exc}") from exc


def main(argv: Sequence[str]) -> int:
    args = _parse_args(argv)

    accounts_db = Path(args.accounts_db).expanduser()
    store_db = Path(args.store_db).expanduser()

    with _open_db(accounts_db) as accounts_conn, _open_db(store_db) as store_conn:
        account_id, email = _resolve_email_and_account_id(
            accounts_conn,
            email=str(args.email) if args.email else None,
            account_id=str(args.account_id) if args.account_id else None,
        )
        reset_at, window_minutes = _latest_window_config(store_conn, account_id=account_id, window=args.window)
        cycles = _compute_cycles(reset_at=reset_at, window_minutes=window_minutes, which=args.cycles)

        since_epoch = _parse_optional_epoch(str(args.since))
        until_epoch = _parse_optional_epoch(str(args.until))

        def _clamp(bounds: CycleBounds) -> CycleBounds | None:
            start = bounds.start_epoch if since_epoch is None else max(bounds.start_epoch, int(since_epoch))
            end = bounds.end_epoch if until_epoch is None else min(bounds.end_epoch, int(until_epoch))
            if end <= start:
                return None
            return CycleBounds(
                label=bounds.label,
                start_epoch=int(start),
                end_epoch=int(end),
                window_minutes=bounds.window_minutes,
            )

        resolved_cycles: list[CycleBounds] = []
        for b in cycles:
            clamped = _clamp(b)
            if clamped is not None:
                resolved_cycles.append(clamped)

        redact_pii = bool(args.redact)
        shown_email = redact(email or "", keep_prefix=0) if (redact_pii and email) else (email or "")
        shown_account_id = redact(account_id, keep_prefix=3) if redact_pii else account_id

        if args.format == "json":
            cycle_reports = [
                _report_cycle(
                    store_conn=store_conn,
                    account_id=account_id,
                    window=str(args.window),
                    bounds=cycle,
                    jump_threshold_pp=float(args.jump_threshold_pp),
                    top_n=int(args.top_n),
                    redact_pii=redact_pii,
                    emit=False,
                )
                for cycle in resolved_cycles
            ]
            payload: dict[str, Any] = {
                "email": shown_email or None,
                "account_id": shown_account_id,
                "accounts_db": str(accounts_db),
                "store_db": str(store_db),
                "window": str(args.window),
                "latest_reset_at_epoch": int(reset_at),
                "latest_reset_at_iso_utc": datetime.fromtimestamp(int(reset_at), tz=UTC).isoformat(),
                "since_epoch": int(since_epoch) if since_epoch is not None else None,
                "until_epoch": int(until_epoch) if until_epoch is not None else None,
                "cycle_reports": cycle_reports,
            }
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0

        if email:
            print(f"Email: {shown_email}")
        print(f"Account ID: {shown_account_id}")
        print(f"Accounts DB: {accounts_db}")
        print(f"Store DB: {store_db}")
        print(f"Latest reset_at: {_fmt_dt(datetime.fromtimestamp(reset_at, tz=UTC))} (epoch {reset_at})")
        if since_epoch is not None:
            print(f"Since clamp: {since_epoch} ({datetime.fromtimestamp(int(since_epoch), tz=UTC).isoformat()})")
        if until_epoch is not None:
            print(f"Until clamp: {until_epoch} ({datetime.fromtimestamp(int(until_epoch), tz=UTC).isoformat()})")

        for cycle in resolved_cycles:
            _report_cycle(
                store_conn=store_conn,
                account_id=account_id,
                window=args.window,
                bounds=cycle,
                jump_threshold_pp=args.jump_threshold_pp,
                top_n=args.top_n,
                redact_pii=redact_pii,
                emit=True,
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
