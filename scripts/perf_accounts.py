from __future__ import annotations

import argparse
import asyncio
import time
from dataclasses import dataclass

import httpx


@dataclass(frozen=True, slots=True)
class Sample:
    latency_seconds: float
    status_code: int


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
    semaphore: asyncio.Semaphore,
) -> Sample:
    async with semaphore:
        start = time.perf_counter()
        response = await client.get(url)
        end = time.perf_counter()
        return Sample(latency_seconds=end - start, status_code=response.status_code)


async def run(*, base_url: str, requests: int, concurrency: int) -> None:
    url = base_url.rstrip("/") + "/api/accounts"
    timeout = httpx.Timeout(connect=5.0, read=120.0, write=5.0, pool=5.0)
    semaphore = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(timeout=timeout) as client:
        cold = await _one(client, url, semaphore)
        print(f"cold: status={cold.status_code} latency_ms={cold.latency_seconds * 1000:.1f}")

        tasks = [asyncio.create_task(_one(client, url, semaphore)) for _ in range(requests)]
        samples = await asyncio.gather(*tasks)

    latencies = sorted(sample.latency_seconds for sample in samples)
    ok_count = sum(1 for sample in samples if 200 <= sample.status_code < 300)
    total_count = len(samples)

    print(f"requests: total={total_count} ok={ok_count} error={total_count - ok_count}")
    print(
        "latency_ms:"
        f" p50={_percentile(latencies, 0.50) * 1000:.1f}"
        f" p95={_percentile(latencies, 0.95) * 1000:.1f}"
        f" p99={_percentile(latencies, 0.99) * 1000:.1f}"
        f" min={latencies[0] * 1000:.1f}"
        f" max={latencies[-1] * 1000:.1f}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Basic latency sampling for GET /api/accounts.")
    parser.add_argument("--base-url", default="http://127.0.0.1:2456")
    parser.add_argument("--requests", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=10)
    args = parser.parse_args()

    asyncio.run(run(base_url=args.base_url, requests=args.requests, concurrency=args.concurrency))


if __name__ == "__main__":
    main()

