"""Tests for data isolation — TenantContext, tenant_filter, and context variables."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from agentproof.enterprise.isolation import (
    TenantContext,
    get_current_tenant,
    tenant_filter,
)
from agentproof.enterprise.types import Plan, Tenant


def _make_tenant(**overrides) -> Tenant:
    defaults = {
        "id": "t-001",
        "name": "Test Org",
        "slug": "test-org",
        "plan": Plan.FREE,
        "created_at": datetime(2026, 3, 1, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    return Tenant(**defaults)


class TestTenantContext:
    """TenantContext sets and resets the context variable correctly."""

    def test_sync_context_sets_tenant(self) -> None:
        tenant = _make_tenant()
        assert get_current_tenant() is None
        with TenantContext(tenant):
            assert get_current_tenant() is not None
            assert get_current_tenant().id == "t-001"
        # Resets after exit
        assert get_current_tenant() is None

    def test_async_context_sets_tenant(self) -> None:
        tenant = _make_tenant()

        async def _run():
            assert get_current_tenant() is None
            async with TenantContext(tenant):
                assert get_current_tenant() is not None
                assert get_current_tenant().id == "t-001"
            assert get_current_tenant() is None

        asyncio.run(_run())

    def test_nested_contexts(self) -> None:
        """Inner context should override outer, then restore."""
        outer = _make_tenant(id="outer")
        inner = _make_tenant(id="inner")

        with TenantContext(outer):
            assert get_current_tenant().id == "outer"
            with TenantContext(inner):
                assert get_current_tenant().id == "inner"
            assert get_current_tenant().id == "outer"

    def test_context_resets_on_exception(self) -> None:
        tenant = _make_tenant()
        try:
            with TenantContext(tenant):
                assert get_current_tenant() is not None
                raise ValueError("boom")
        except ValueError:
            pass
        assert get_current_tenant() is None


class TestTenantFilter:
    """tenant_filter appends the correct WHERE/AND clause."""

    def test_adds_where_clause(self) -> None:
        q = "SELECT * FROM llm_events"
        result_q, params = tenant_filter(q, "t-123")
        assert "WHERE org_id = :_tenant_id" in result_q
        assert params == {"_tenant_id": "t-123"}

    def test_adds_and_clause_when_where_exists(self) -> None:
        q = "SELECT * FROM llm_events WHERE status = 'success'"
        result_q, params = tenant_filter(q, "t-456")
        assert "AND org_id = :_tenant_id" in result_q
        assert params == {"_tenant_id": "t-456"}

    def test_case_insensitive_where_detection(self) -> None:
        q = "SELECT * FROM llm_events where model = 'gpt-4o'"
        result_q, params = tenant_filter(q, "t-789")
        assert "AND org_id" in result_q
        assert params["_tenant_id"] == "t-789"

    def test_preserves_original_query(self) -> None:
        q = "SELECT count(*) FROM llm_events"
        result_q, params = tenant_filter(q, "t-001")
        assert result_q.startswith(q)
        assert "_tenant_id" in params
