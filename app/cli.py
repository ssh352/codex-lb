from __future__ import annotations

import argparse
import os

import anyio
import uvicorn


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
        if bool(args.ssl_certfile) ^ bool(args.ssl_keyfile):
            raise SystemExit("Both --ssl-certfile and --ssl-keyfile must be provided together.")

        uvicorn.run(
            "app.main:app",
            host=args.host,
            port=args.port,
            ssl_certfile=args.ssl_certfile,
            ssl_keyfile=args.ssl_keyfile,
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
