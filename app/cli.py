from __future__ import annotations

import argparse
import copy
import os

import anyio
import uvicorn
import uvicorn.config

from app.core.config.settings import get_settings


def _build_log_config() -> dict:
    # Uvicorn's default LOGGING_CONFIG does not attach handlers to the `app.*` logger namespace.
    # Ensure application logs are visible on stdout without enabling noisy root logging.
    config = copy.deepcopy(uvicorn.config.LOGGING_CONFIG)
    loggers = config.setdefault("loggers", {})
    loggers["app"] = {
        "handlers": ["default"],
        "level": "INFO",
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
            log_config=_build_log_config(),
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

    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
