from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AccountRow:
    account_id: str
    status: str
    email: str | None
    display_name: str | None

    @classmethod
    def from_api(cls, payload: Any) -> "AccountRow":
        if not isinstance(payload, dict):
            raise TypeError("Account payload must be an object")

        account_id = payload.get("accountId")
        status = payload.get("status")
        email = payload.get("email")
        display_name = payload.get("displayName")

        if not isinstance(account_id, str) or not account_id.strip():
            raise ValueError("Missing or invalid accountId")
        if not isinstance(status, str) or not status.strip():
            raise ValueError(f"Missing or invalid status for accountId={account_id!r}")
        if email is not None and not isinstance(email, str):
            raise ValueError(f"Invalid email type for accountId={account_id!r}")
        if display_name is not None and not isinstance(display_name, str):
            raise ValueError(f"Invalid displayName type for accountId={account_id!r}")

        return cls(
            account_id=account_id,
            status=status,
            email=email,
            display_name=display_name,
        )


def _join_url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + path


def _load_json_response(response: Any) -> Any:
    raw = response.read()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        snippet = raw[:2000].decode("utf-8", errors="replace")
        raise RuntimeError(f"Invalid JSON response (first 2000 bytes): {snippet}") from exc


def _http_json(*, method: str, url: str, timeout_seconds: float, body: Any | None = None) -> Any:
    data: bytes | None = None
    headers: dict[str, str] = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url=url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return _load_json_response(response)
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError(f"HTTP {exc.code} {exc.reason}: {body_text}".strip()) from exc


def _fetch_accounts(*, base_url: str, timeout_seconds: float) -> list[AccountRow]:
    url = _join_url(base_url, "/api/accounts")
    payload = _http_json(method="GET", url=url, timeout_seconds=timeout_seconds)

    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected /api/accounts response shape (expected object)")
    raw_accounts = payload.get("accounts")
    if not isinstance(raw_accounts, list):
        raise RuntimeError("Unexpected /api/accounts response shape (expected accounts list)")

    return [AccountRow.from_api(item) for item in raw_accounts]


def _reactivate_account(*, base_url: str, timeout_seconds: float, account_id: str) -> None:
    quoted = urllib.parse.quote(account_id, safe="")
    url = _join_url(base_url, f"/api/accounts/{quoted}/reactivate")
    _http_json(method="POST", url=url, timeout_seconds=timeout_seconds, body={})


def _confirm_or_exit(message: str, *, assume_yes: bool) -> None:
    if assume_yes:
        return
    print(message)
    try:
        reply = input("Proceed? [y/N] ").strip().lower()
    except EOFError:
        reply = ""
    if reply not in {"y", "yes"}:
        raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Account management utility for codex-lb (HTTP).\n\n"
            "Currently supported:\n"
            "- Resume (reactivate) all paused accounts.\n\n"
            "Uses the same endpoint as the dashboard Resume button:\n"
            "  POST /api/accounts/{accountId}/reactivate\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:2455",
        help="codex-lb server base URL (default: %(default)s).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Per-request timeout in seconds (default: %(default)s).",
    )
    parser.add_argument(
        "--sleep-ms",
        type=int,
        default=50,
        help="Sleep between resumes to reduce load (default: %(default)s).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Maximum paused accounts to resume (0 = no limit).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print paused accounts and exit without resuming anything.",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Do not prompt for confirmation.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print a line for every resumed account.",
    )
    args = parser.parse_args()

    try:
        accounts = _fetch_accounts(base_url=args.base_url, timeout_seconds=float(args.timeout))
    except Exception as exc:
        print(f"Failed to list accounts: {exc}", file=sys.stderr)
        return 2

    paused = [account for account in accounts if account.status == "paused"]
    if args.limit and args.limit > 0:
        paused = paused[: args.limit]

    if not paused:
        print("No paused accounts found.")
        return 0

    def _label(row: AccountRow) -> str:
        return row.display_name or row.email or row.account_id

    print(f"Paused accounts: {len(paused)}")
    if args.dry_run:
        for row in paused:
            print(f"- {row.account_id} ({_label(row)})")
        return 0

    _confirm_or_exit(f"About to resume {len(paused)} paused accounts.", assume_yes=bool(args.yes))

    ok = 0
    failed: list[tuple[str, str]] = []
    sleep_seconds = max(0.0, float(args.sleep_ms) / 1000.0)

    for index, row in enumerate(paused, start=1):
        try:
            _reactivate_account(base_url=args.base_url, timeout_seconds=float(args.timeout), account_id=row.account_id)
            ok += 1
            if args.verbose:
                print(f"OK   {index}/{len(paused)} {_label(row)}")
        except Exception as exc:
            message = str(exc)
            failed.append((row.account_id, message))
            print(f"FAIL {index}/{len(paused)} {_label(row)}: {message}", file=sys.stderr)
        if sleep_seconds:
            time.sleep(sleep_seconds)

    print(f"Resumed: ok={ok} failed={len(failed)}")
    if failed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
