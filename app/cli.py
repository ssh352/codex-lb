from __future__ import annotations

import argparse
import copy
import os
from datetime import timedelta

import anyio
import uvicorn
import uvicorn.config

from app.core.config.settings import Settings, get_settings


def _build_log_config(settings: Settings) -> dict:
    # Uvicorn's default LOGGING_CONFIG does not attach handlers to the `app.*` logger namespace.
    # Ensure application logs are visible on stdout without enabling noisy root logging.
    config = copy.deepcopy(uvicorn.config.LOGGING_CONFIG)
    loggers = config.setdefault("loggers", {})
    app_level = "DEBUG" if (settings.log_proxy_request_shape or settings.log_proxy_request_payload) else "INFO"
    loggers["app"] = {
        "handlers": ["default"],
        "level": app_level,
        "propagate": False,
    }
    return config


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the codex-lb API server.")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "2455")))
    parser.add_argument("--ssl-certfile", default=os.getenv("SSL_CERTFILE"))
    parser.add_argument("--ssl-keyfile", default=os.getenv("SSL_KEYFILE"))

    subparsers = parser.add_subparsers(dest="command")

    migrate = subparsers.add_parser(
        "migrate-accounts",
        help="Copy legacy accounts from store.db into accounts.db (split DB).",
    )
    migrate.add_argument(
        "--drop-legacy",
        action="store_true",
        help="Drop the legacy accounts table from store.db after successful migration.",
    )

    reconcile = subparsers.add_parser(
        "reconcile-status",
        help="Reconcile account status from latest request errors + usage reset timestamps.",
    )
    reconcile.add_argument(
        "--dry-run",
        action="store_true",
        help="Print how many accounts would change without writing to accounts.db.",
    )
    reconcile.add_argument(
        "--since-hours",
        type=float,
        default=48.0,
        help="Only consider request logs within this many hours (default: 48).",
    )

    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if args.command is None:
        settings = get_settings()
        if bool(args.ssl_certfile) ^ bool(args.ssl_keyfile):
            raise SystemExit("Both --ssl-certfile and --ssl-keyfile must be provided together.")

        uvicorn.run(
            "app.main:app",
            host=args.host,
            port=args.port,
            ssl_certfile=args.ssl_certfile,
            ssl_keyfile=args.ssl_keyfile,
            log_config=_build_log_config(settings),
            # Keep access logs off by default for performance; controlled via `CODEX_LB_ACCESS_LOG_ENABLED`.
            access_log=settings.access_log_enabled,
        )
        return

    if args.command == "migrate-accounts":
        from app.db.session import close_db, init_db, migrate_accounts_from_main_to_accounts_db

        async def _run() -> None:
            try:
                await init_db()
                migrated = await migrate_accounts_from_main_to_accounts_db(drop_legacy=bool(args.drop_legacy))
                print(f"migrated_accounts={migrated}")
            finally:
                await close_db()

        anyio.run(_run)
        return

    if args.command == "reconcile-status":
        from sqlalchemy import func, select

        from app.core.utils.time import to_epoch_seconds_assuming_utc, utcnow
        from app.db.models import AccountStatus, RequestLog
        from app.db.session import AccountsSessionLocal, SessionLocal, close_db, init_db
        from app.modules.accounts.repository import AccountsRepository
        from app.modules.usage.repository import UsageRepository

        async def _run() -> None:
            since_hours = float(args.since_hours)
            if since_hours <= 0:
                raise SystemExit("--since-hours must be > 0")

            now = utcnow()
            now_epoch = to_epoch_seconds_assuming_utc(now)
            since = now - timedelta(hours=since_hours)

            try:
                await init_db()

                async with AccountsSessionLocal() as accounts_session, SessionLocal() as main_session:
                    accounts_repo = AccountsRepository(accounts_session)
                    usage_repo = UsageRepository(main_session)

                    accounts = await accounts_repo.list_accounts()
                    if not accounts:
                        print("reconciled=0 skipped=0 dry_run=true" if args.dry_run else "reconciled=0 skipped=0")
                        return

                    ranked = (
                        select(
                            RequestLog.id.label("id"),
                            func.row_number()
                            .over(
                                partition_by=RequestLog.account_id,
                                order_by=(RequestLog.requested_at.desc(), RequestLog.id.desc()),
                            )
                            .label("rn"),
                        )
                        .where(RequestLog.requested_at >= since)
                        .subquery()
                    )
                    stmt = select(RequestLog).join(ranked, RequestLog.id == ranked.c.id).where(ranked.c.rn == 1)
                    result = await main_session.execute(stmt)
                    latest_logs = list(result.scalars().all())
                    latest_by_account = {entry.account_id: entry for entry in latest_logs}

                    latest_primary = await usage_repo.latest_by_account(window="primary")
                    latest_secondary = await usage_repo.latest_by_account(window="secondary")

                    changed = 0
                    skipped = 0
                    for account in accounts:
                        if account.status in (AccountStatus.PAUSED, AccountStatus.DEACTIVATED):
                            skipped += 1
                            continue

                        latest = latest_by_account.get(account.id)
                        if latest is None or latest.status != "error" or not latest.error_code:
                            skipped += 1
                            continue

                        desired_status: AccountStatus | None = None
                        reset_at: int | None = None
                        match latest.error_code:
                            case "rate_limit_exceeded":
                                desired_status = AccountStatus.RATE_LIMITED
                                primary = latest_primary.get(account.id)
                                reset_at = primary.reset_at if primary is not None else None
                            case "usage_limit_reached":
                                desired_status = AccountStatus.RATE_LIMITED
                                secondary = latest_secondary.get(account.id)
                                reset_at = secondary.reset_at if secondary is not None else None
                            case "insufficient_quota" | "usage_not_included" | "quota_exceeded":
                                desired_status = AccountStatus.QUOTA_EXCEEDED
                                secondary = latest_secondary.get(account.id)
                                reset_at = secondary.reset_at if secondary is not None else None
                            case _:
                                skipped += 1
                                continue

                        if reset_at is None or int(reset_at) <= int(now_epoch):
                            skipped += 1
                            continue

                        if account.status == desired_status and account.reset_at == int(reset_at):
                            skipped += 1
                            continue

                        changed += 1
                        if not args.dry_run:
                            await accounts_repo.update_status(
                                account.id,
                                desired_status,
                                None,
                                int(reset_at),
                            )

                    suffix = " dry_run=true" if args.dry_run else ""
                    print(f"reconciled={changed} skipped={skipped} since_hours={since_hours}{suffix}")
            finally:
                await close_db()

        anyio.run(_run)
        return

    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
