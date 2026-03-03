"""Shared fixtures for framework integration tests.

These tests hit the live LiteLLM proxy and AgentProof API, so they
require the full stack to be running (docker compose up -d). Tests
auto-skip when the required services aren't reachable.

Run manually with:
    pytest tests/integration/frameworks/ -m framework -v
"""

from __future__ import annotations

import time

import httpx
import pytest

PROXY_URL = "http://localhost:4000"
PROXY_KEY = "sk-local-dev-key"
API_URL = "http://localhost:8100"


def _service_reachable(url: str, path: str = "/health") -> bool:
    """Check if a service responds to a health check."""
    try:
        resp = httpx.get(f"{url}{path}", timeout=3.0)
        return resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException):
        return False


@pytest.fixture(scope="session")
def proxy_available() -> bool:
    """True if the LiteLLM proxy is running on localhost:4000."""
    available = _service_reachable(PROXY_URL)
    if not available:
        pytest.skip("LiteLLM proxy not available at localhost:4000")
    return available


@pytest.fixture(scope="session")
def api_available() -> bool:
    """True if the AgentProof API is running on localhost:8100."""
    available = _service_reachable(API_URL)
    if not available:
        pytest.skip("AgentProof API not available at localhost:8100")
    return available


def _get_event_count() -> int:
    """Fetch current total event count from the AgentProof API."""
    try:
        resp = httpx.get(f"{API_URL}/api/v1/events", params={"limit": 1}, timeout=5.0)
        return resp.json().get("total_count", 0)
    except Exception:
        return 0


@pytest.fixture
def event_count_before(proxy_available: bool, api_available: bool) -> int:
    """Capture the event count before each test runs."""
    return _get_event_count()


def verify_event_captured(
    count_before: int,
    *,
    timeout_s: float = 10.0,
    poll_interval: float = 0.5,
) -> int:
    """Poll the API until the event count increases or timeout expires.

    Returns the new total count. Raises AssertionError if no new event
    appeared within the timeout.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        current = _get_event_count()
        if current > count_before:
            return current
        time.sleep(poll_interval)

    current = _get_event_count()
    assert current > count_before, (
        f"No new events captured within {timeout_s}s "
        f"(before={count_before}, after={current})"
    )
    return current
