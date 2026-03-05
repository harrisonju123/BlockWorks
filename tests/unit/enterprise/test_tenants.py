"""Tests for tenant CRUD operations — create, update, deactivate, slug generation, plan limits."""

from __future__ import annotations

import pytest

from blockthrough.config import get_config
from blockthrough.enterprise.tenants import (
    create_tenant,
    deactivate_tenant,
    get_plan_limit,
    get_tenant,
    list_tenants,
    reset_store,
    update_tenant,
)
from blockthrough.enterprise.types import Plan


class TestTenantCreate:
    def setup_method(self) -> None:
        reset_store()
        get_config.cache_clear()

    def test_create_returns_tenant_with_id(self) -> None:
        t = create_tenant("Acme Corp")
        assert t.id
        assert t.name == "Acme Corp"
        assert t.plan == Plan.FREE
        assert t.is_active is True

    def test_create_with_plan(self) -> None:
        t = create_tenant("Big Co", plan=Plan.ENTERPRISE)
        assert t.plan == Plan.ENTERPRISE

    def test_create_generates_unique_ids(self) -> None:
        t1 = create_tenant("First")
        t2 = create_tenant("Second")
        assert t1.id != t2.id

    def test_created_tenant_is_retrievable(self) -> None:
        t = create_tenant("Findable Inc")
        found = get_tenant(t.id)
        assert found is not None
        assert found.name == "Findable Inc"


class TestSlugGeneration:
    def setup_method(self) -> None:
        reset_store()

    def test_simple_name(self) -> None:
        t = create_tenant("Acme Corp")
        assert t.slug == "acme-corp"

    def test_special_characters_stripped(self) -> None:
        t = create_tenant("My Company! @#$% Ltd.")
        assert t.slug == "my-company-ltd"

    def test_leading_trailing_hyphens_trimmed(self) -> None:
        t = create_tenant("  --Edge Case--  ")
        assert t.slug == "edge-case"

    def test_all_uppercase(self) -> None:
        t = create_tenant("SHOUTING NAME")
        assert t.slug == "shouting-name"

    def test_numeric_name(self) -> None:
        t = create_tenant("123 Corp")
        assert t.slug == "123-corp"


class TestTenantUpdate:
    def setup_method(self) -> None:
        reset_store()

    def test_update_name(self) -> None:
        t = create_tenant("Old Name")
        updated = update_tenant(t.id, {"name": "New Name"})
        assert updated is not None
        assert updated.name == "New Name"
        assert updated.slug == "new-name"

    def test_update_plan(self) -> None:
        t = create_tenant("Upgrader")
        updated = update_tenant(t.id, {"plan": Plan.PRO})
        assert updated is not None
        assert updated.plan == Plan.PRO

    def test_update_settings(self) -> None:
        t = create_tenant("Configurable")
        updated = update_tenant(t.id, {"settings": {"theme": "dark"}})
        assert updated is not None
        assert updated.settings == {"theme": "dark"}

    def test_update_nonexistent_returns_none(self) -> None:
        result = update_tenant("ghost-id", {"name": "nope"})
        assert result is None

    def test_update_ignores_disallowed_fields(self) -> None:
        """id and created_at should not be overwritable."""
        t = create_tenant("Immutable")
        original_id = t.id
        updated = update_tenant(t.id, {"id": "hacked", "created_at": "2000-01-01"})
        assert updated is not None
        assert updated.id == original_id


class TestTenantDeactivate:
    def setup_method(self) -> None:
        reset_store()

    def test_deactivate_sets_inactive(self) -> None:
        t = create_tenant("Leaving")
        result = deactivate_tenant(t.id)
        assert result is not None
        assert result.is_active is False

    def test_deactivate_nonexistent_returns_none(self) -> None:
        result = deactivate_tenant("no-such-id")
        assert result is None

    def test_deactivated_tenant_still_retrievable(self) -> None:
        """Deactivation is soft-delete — the tenant record persists."""
        t = create_tenant("Soft Delete")
        deactivate_tenant(t.id)
        found = get_tenant(t.id)
        assert found is not None
        assert found.is_active is False


class TestListTenants:
    def setup_method(self) -> None:
        reset_store()

    def test_empty_initially(self) -> None:
        assert list_tenants() == []

    def test_returns_all_created(self) -> None:
        create_tenant("A")
        create_tenant("B")
        create_tenant("C")
        assert len(list_tenants()) == 3

    def test_includes_inactive(self) -> None:
        t = create_tenant("Active")
        deactivate_tenant(t.id)
        assert len(list_tenants()) == 1


class TestPlanLimits:
    def setup_method(self) -> None:
        reset_store()
        get_config.cache_clear()

    def test_free_limit(self) -> None:
        t = create_tenant("Free Org")
        limit = get_plan_limit(t.id)
        assert limit == 50_000

    def test_pro_limit(self) -> None:
        t = create_tenant("Pro Org", plan=Plan.PRO)
        limit = get_plan_limit(t.id)
        assert limit == 500_000

    def test_enterprise_unlimited(self) -> None:
        t = create_tenant("Enterprise Org", plan=Plan.ENTERPRISE)
        limit = get_plan_limit(t.id)
        assert limit is None

    def test_nonexistent_tenant_raises(self) -> None:
        with pytest.raises(ValueError, match="not found"):
            get_plan_limit("nonexistent")
