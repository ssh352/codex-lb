from __future__ import annotations

import argparse

import uvicorn
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="codex-lb upstream stub")

    @app.post("/backend-api/codex/responses/compact")
    async def responses_compact() -> dict:
        # Minimal payload accepted by OpenAIResponsePayload (all fields optional).
        return {}

    @app.post("/backend-api/codex/responses")
    async def responses_stream() -> dict:
        # Not used by the compact perf test; present so accidental calls fail less mysteriously.
        return {}

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Local stub for CODEX_LB_UPSTREAM_BASE_URL.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9999)
    args = parser.parse_args()

    uvicorn.run(
        create_app(),
        host=args.host,
        port=args.port,
        log_level="warning",
    )


if __name__ == "__main__":
    main()

