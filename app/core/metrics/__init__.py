from __future__ import annotations

from functools import lru_cache

from app.core.metrics.metrics import Metrics


@lru_cache(maxsize=1)
def get_metrics() -> Metrics:
    return Metrics()
