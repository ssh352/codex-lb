from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections.abc import AsyncIterator

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse


def create_app() -> FastAPI:
    app = FastAPI(title="codex-lb upstream stub")

    @app.post("/backend-api/codex/responses/compact")
    async def responses_compact() -> dict:
        # Minimal payload accepted by OpenAIResponsePayload (all fields optional).
        return {"id": "stub_response", "status": "completed"}

    @app.post("/backend-api/codex/responses")
    async def responses_stream(request: Request) -> StreamingResponse:
        payload = await request.json()
        stub_config = payload.get("__stub") if isinstance(payload, dict) else None
        if not isinstance(stub_config, dict):
            stub_config = {}

        events = int(stub_config.get("events") or 64)
        payload_bytes = int(stub_config.get("payload_bytes") or 256)
        delay_ms = float(stub_config.get("delay_ms") or 0.0)

        events = max(1, min(events, 5_000))
        payload_bytes = max(0, min(payload_bytes, 64 * 1024))
        delay_seconds = max(0.0, min(delay_ms / 1000.0, 5.0))

        def _sse(data: dict) -> bytes:
            return ("data: " + json.dumps(data, separators=(",", ":")) + "\n\n").encode("utf-8")

        async def _iter() -> AsyncIterator[bytes]:
            start = time.monotonic()
            yield _sse({"type": "response.created", "response": {"id": "stub_response", "status": "in_progress"}})

            filler = "x" * payload_bytes
            for _ in range(events):
                yield _sse({"type": "response.output_text.delta", "delta": filler})
                if delay_seconds:
                    await asyncio.sleep(delay_seconds)

            elapsed_ms = int((time.monotonic() - start) * 1000)
            yield _sse(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "stub_response",
                        "status": "completed",
                        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                    },
                    "elapsed_ms": elapsed_ms,
                }
            )
            yield b"data: [DONE]\n\n"

        return StreamingResponse(_iter(), media_type="text/event-stream")

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
