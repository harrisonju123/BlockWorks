"""Overhead benchmark: measure callback enqueue latency.

The callback should only enqueue events, never awaiting DB writes
on the hot path. This test verifies P95 latency stays under the
threshold, proving the callback doesn't add meaningful latency.
"""

from __future__ import annotations

import os
import statistics
import time

import asyncpg
import pytest

from .conftest import make_callback, make_litellm_kwargs

pytestmark = pytest.mark.integration

NUM_CALLS = 1000
P95_THRESHOLD_MS = float(os.environ.get("BENCH_P95_THRESHOLD_MS", "8.0"))


async def test_enqueue_latency(db_url: str, _apply_schema, clean_db: asyncpg.Pool):
    """Measure async_log_success_event latency over 1000 calls.

    Only the enqueue portion is on the critical path. The background
    writer drains the queue independently.
    """
    callback = make_callback(db_url, org_id="bench-org", batch_size=100, flush_interval_ms=200)
    kwargs, response_obj, start_time, end_time = make_litellm_kwargs()
    latencies_ms: list[float] = []

    for _ in range(NUM_CALLS):
        t0 = time.perf_counter()
        await callback.async_log_success_event(kwargs, response_obj, start_time, end_time)
        t1 = time.perf_counter()
        latencies_ms.append((t1 - t0) * 1000)

    latencies_ms.sort()
    p50 = latencies_ms[len(latencies_ms) // 2]
    p95_idx = int(len(latencies_ms) * 0.95)
    p95 = latencies_ms[p95_idx]
    p99_idx = int(len(latencies_ms) * 0.99)
    p99 = latencies_ms[p99_idx]
    mean = statistics.mean(latencies_ms)

    print(f"\n--- Callback overhead benchmark ({NUM_CALLS} calls) ---")
    print(f"  Mean:  {mean:.3f} ms")
    print(f"  P50:   {p50:.3f} ms")
    print(f"  P95:   {p95:.3f} ms")
    print(f"  P99:   {p99:.3f} ms")
    print(f"  Min:   {latencies_ms[0]:.3f} ms")
    print(f"  Max:   {latencies_ms[-1]:.3f} ms")

    assert p95 < P95_THRESHOLD_MS, (
        f"P95 latency {p95:.3f}ms exceeds {P95_THRESHOLD_MS}ms threshold. "
        f"The callback is likely doing IO on the hot path."
    )


async def test_both_modes_under_threshold(db_url: str, _apply_schema, clean_db: asyncpg.Pool):
    """Both classification-enabled and disabled modes stay under threshold."""
    kwargs, response_obj, start_time, end_time = make_litellm_kwargs()

    cb_with = make_callback(db_url, enable_classification=True, batch_size=100, flush_interval_ms=200)
    cb_without = make_callback(db_url, enable_classification=False, batch_size=100, flush_interval_ms=200)

    latencies_with: list[float] = []
    latencies_without: list[float] = []

    for _ in range(200):
        t0 = time.perf_counter()
        await cb_with.async_log_success_event(kwargs, response_obj, start_time, end_time)
        latencies_with.append((time.perf_counter() - t0) * 1000)

    for _ in range(200):
        t0 = time.perf_counter()
        await cb_without.async_log_success_event(kwargs, response_obj, start_time, end_time)
        latencies_without.append((time.perf_counter() - t0) * 1000)

    mean_with = statistics.mean(latencies_with)
    mean_without = statistics.mean(latencies_without)

    print(f"\n--- Classification overhead ---")
    print(f"  With classification:    {mean_with:.3f} ms mean")
    print(f"  Without classification: {mean_without:.3f} ms mean")

    assert statistics.median(latencies_with) < P95_THRESHOLD_MS
    assert statistics.median(latencies_without) < P95_THRESHOLD_MS
