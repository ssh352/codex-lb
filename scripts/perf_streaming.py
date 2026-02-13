from __future__ import annotations

import argparse
import asyncio
import time
from dataclasses import dataclass

import httpx


@dataclass(frozen=True, slots=True)
class Sample:
    status_code: int
    ttfb_seconds: float | None
    duration_seconds: float | None
    bytes_read: int
    data_events: int


def _percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    if p <= 0:
        return sorted_values[0]
    if p >= 1:
        return sorted_values[-1]
    index = int(round(p * (len(sorted_values) - 1)))
    return sorted_values[index]


async def _one(
    client: httpx.AsyncClient,
    url: str,
    payload: dict,
    semaphore: asyncio.Semaphore,
) -> Sample:
    async with semaphore:
        start = time.perf_counter()
        ttfb: float | None = None
        bytes_read = 0
        data_events = 0
        try:
            async with client.stream("POST", url, json=payload, headers={"Accept": "text/event-stream"}) as resp:
                status_code = resp.status_code
                if not (200 <= status_code < 300):
                    body = await resp.aread()
                    return Sample(
                        status_code=status_code,
                        ttfb_seconds=None,
                        duration_seconds=time.perf_counter() - start,
                        bytes_read=len(body),
                        data_events=0,
                    )

                async for chunk in resp.aiter_bytes():
                    if not chunk:
                        continue
                    if ttfb is None:
                        ttfb = time.perf_counter() - start
                    bytes_read += len(chunk)
                    data_events += chunk.count(b"data:")

        except httpx.HTTPError:
            return Sample(
                status_code=0,
                ttfb_seconds=None,
                duration_seconds=time.perf_counter() - start,
                bytes_read=bytes_read,
                data_events=data_events,
            )

        duration = time.perf_counter() - start
        return Sample(
            status_code=200,
            ttfb_seconds=ttfb,
            duration_seconds=duration,
            bytes_read=bytes_read,
            data_events=data_events,
        )


async def run(
    *,
    base_url: str,
    requests: int,
    concurrency: int,
    sticky_key_prefix: str,
    sticky_keys: int,
    use_stub_config: bool,
    stub_events: int,
    stub_payload_bytes: int,
    stub_delay_ms: float,
) -> None:
    url = base_url.rstrip("/") + "/backend-api/codex/responses"
    timeout = httpx.Timeout(connect=5.0, read=120.0, write=5.0, pool=5.0)
    semaphore = asyncio.Semaphore(concurrency)

    def _payload(i: int) -> dict:
        payload: dict = {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": "ping",
            "prompt_cache_key": f"{sticky_key_prefix}-{i % max(1, sticky_keys)}",
        }
        if use_stub_config:
            payload["__stub"] = {
                "events": stub_events,
                "payload_bytes": stub_payload_bytes,
                "delay_ms": stub_delay_ms,
            }
        return payload

    async with httpx.AsyncClient(timeout=timeout) as client:
        cold = await _one(client, url, _payload(0), semaphore)
        print(
            "cold:"
            f" status={cold.status_code}"
            f" ttfb_ms={(cold.ttfb_seconds or 0.0) * 1000:.1f}"
            f" duration_ms={(cold.duration_seconds or 0.0) * 1000:.1f}"
            f" bytes={cold.bytes_read}"
            f" data_events={cold.data_events}",
            flush=True,
        )

        tasks = [asyncio.create_task(_one(client, url, _payload(i), semaphore)) for i in range(requests)]
        samples = await asyncio.gather(*tasks)

    ok = [sample for sample in samples if 200 <= sample.status_code < 300]
    errors = [sample for sample in samples if not (200 <= sample.status_code < 300)]

    ttfb_values = sorted(sample.ttfb_seconds for sample in ok if sample.ttfb_seconds is not None)
    duration_values = sorted(sample.duration_seconds for sample in ok if sample.duration_seconds is not None)

    print(f"requests: total={len(samples)} ok={len(ok)} error={len(errors)}", flush=True)
    if ttfb_values:
        print(
            "ttfb_ms:"
            f" p50={_percentile(ttfb_values, 0.50) * 1000:.1f}"
            f" p95={_percentile(ttfb_values, 0.95) * 1000:.1f}"
            f" p99={_percentile(ttfb_values, 0.99) * 1000:.1f}"
            f" min={ttfb_values[0] * 1000:.1f}"
            f" max={ttfb_values[-1] * 1000:.1f}",
            flush=True,
        )
    if duration_values:
        print(
            "duration_ms:"
            f" p50={_percentile(duration_values, 0.50) * 1000:.1f}"
            f" p95={_percentile(duration_values, 0.95) * 1000:.1f}"
            f" p99={_percentile(duration_values, 0.99) * 1000:.1f}"
            f" min={duration_values[0] * 1000:.1f}"
            f" max={duration_values[-1] * 1000:.1f}",
            flush=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Basic latency sampling for POST /backend-api/codex/responses.")
    parser.add_argument("--base-url", default="http://127.0.0.1:2456")
    parser.add_argument("--requests", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--sticky-key-prefix", default="perf")
    parser.add_argument("--sticky-keys", type=int, default=10)

    parser.add_argument(
        "--stub",
        action="store_true",
        help="Include __stub config in the payload (for use with scripts/upstream_stub.py).",
    )
    parser.add_argument("--stub-events", type=int, default=64)
    parser.add_argument("--stub-payload-bytes", type=int, default=256)
    parser.add_argument("--stub-delay-ms", type=float, default=0.0)
    args = parser.parse_args()

    asyncio.run(
        run(
            base_url=args.base_url,
            requests=args.requests,
            concurrency=args.concurrency,
            sticky_key_prefix=args.sticky_key_prefix,
            sticky_keys=args.sticky_keys,
            use_stub_config=bool(args.stub),
            stub_events=args.stub_events,
            stub_payload_bytes=args.stub_payload_bytes,
            stub_delay_ms=args.stub_delay_ms,
        )
    )


if __name__ == "__main__":
    main()
