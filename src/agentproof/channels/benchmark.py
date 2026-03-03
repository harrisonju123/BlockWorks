"""Latency benchmarks for state channel operations.

Measures payment creation, signature verification, and throughput
for sequential payment streams. Target: sub-millisecond per payment.
"""

from __future__ import annotations

import time

from agentproof.channels.manager import ChannelManager
from agentproof.channels.signing import sign_payment, verify_signature
from agentproof.channels.types import ChannelConfig


def benchmark_payment_creation(n: int = 1000) -> dict[str, float]:
    """Measure time to create n sequential payments on a single channel.

    Returns dict with total_ms, per_payment_ms, and payments_per_second.
    """
    mgr = ChannelManager(config=ChannelConfig(min_deposit=0.0001))
    # Large deposit so we don't hit the cap during the benchmark
    state = mgr.open_channel("bench-sender", "bench-receiver", deposit=float(n))

    start = time.perf_counter()
    for _ in range(n):
        mgr.create_payment(state.channel_id, 0.001)
    elapsed = time.perf_counter() - start

    total_ms = elapsed * 1000
    per_payment_ms = total_ms / n
    payments_per_sec = n / elapsed if elapsed > 0 else float("inf")

    return {
        "total_ms": round(total_ms, 3),
        "per_payment_ms": round(per_payment_ms, 6),
        "payments_per_second": round(payments_per_sec, 1),
        "count": n,
    }


def benchmark_signature_creation(n: int = 1000) -> dict[str, float]:
    """Measure time to create n signatures."""
    start = time.perf_counter()
    for i in range(n):
        sign_payment("bench-channel-id", float(i) * 0.001, i + 1, "bench-key")
    elapsed = time.perf_counter() - start

    total_ms = elapsed * 1000
    per_sig_ms = total_ms / n

    return {
        "total_ms": round(total_ms, 3),
        "per_signature_ms": round(per_sig_ms, 6),
        "count": n,
    }


def benchmark_signature_verification(n: int = 1000) -> dict[str, float]:
    """Measure time to verify n signatures."""
    # Pre-generate signatures
    sigs = []
    for i in range(n):
        sig = sign_payment("bench-channel-id", float(i) * 0.001, i + 1, "bench-key")
        sigs.append((float(i) * 0.001, i + 1, sig))

    start = time.perf_counter()
    for amount, nonce, sig in sigs:
        verify_signature("bench-channel-id", amount, nonce, sig, "bench-key")
    elapsed = time.perf_counter() - start

    total_ms = elapsed * 1000
    per_verify_ms = total_ms / n

    return {
        "total_ms": round(total_ms, 3),
        "per_verify_ms": round(per_verify_ms, 6),
        "count": n,
    }


def run_all_benchmarks() -> dict[str, dict[str, float]]:
    """Run all benchmarks and return combined results."""
    return {
        "payment_creation": benchmark_payment_creation(),
        "signature_creation": benchmark_signature_creation(),
        "signature_verification": benchmark_signature_verification(),
    }


if __name__ == "__main__":
    results = run_all_benchmarks()
    print("\n=== State Channel Latency Benchmarks ===\n")
    for name, data in results.items():
        print(f"  {name}:")
        for key, value in data.items():
            print(f"    {key}: {value}")
        print()
