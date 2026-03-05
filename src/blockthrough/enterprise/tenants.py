"""In-memory tenant store with CRUD operations and plan-based limits.

All state lives in module-level dicts — no DB tables required.
Production would back this with a tenants table, but for the local-first
MVP this keeps the dependency footprint minimal.
"""

from __future__ import annotations

import re
import uuid

from blockthrough.config import get_config
from blockthrough.utils import utcnow
from blockthrough.enterprise.types import Plan, Tenant


# In-memory store keyed by tenant ID
_tenants: dict[str, Tenant] = {}


def _generate_slug(name: str) -> str:
    """Derive a URL-safe slug from a tenant name.

    Lowercases, replaces non-alphanumeric runs with hyphens, trims edges.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "tenant"


def _plan_limit(plan: Plan) -> int | None:
    """Return the monthly request cap for a plan, or None for unlimited."""
    config = get_config()
    limits: dict[Plan, int | None] = {
        Plan.FREE: config.enterprise_free_limit,
        Plan.PRO: config.enterprise_pro_limit,
        Plan.ENTERPRISE: None,
    }
    return limits[plan]


def create_tenant(name: str, plan: Plan = Plan.FREE) -> Tenant:
    """Create a new tenant and return it."""
    tenant = Tenant(
        id=str(uuid.uuid4()),
        name=name,
        slug=_generate_slug(name),
        plan=plan,
        created_at=utcnow(),
    )
    _tenants[tenant.id] = tenant
    return tenant


def get_tenant(tenant_id: str) -> Tenant | None:
    """Look up a tenant by ID. Returns None if not found."""
    return _tenants.get(tenant_id)


def update_tenant(tenant_id: str, updates: dict) -> Tenant | None:
    """Apply partial updates to a tenant. Returns None if not found."""
    tenant = _tenants.get(tenant_id)
    if tenant is None:
        return None

    data = tenant.model_dump()
    # Only allow updating safe fields — slug is derived, id is immutable
    allowed = {"name", "plan", "settings", "is_active"}
    for key, value in updates.items():
        if key in allowed:
            data[key] = value

    # Re-derive slug if name changed
    if "name" in updates:
        data["slug"] = _generate_slug(updates["name"])

    updated = Tenant(**data)
    _tenants[tenant_id] = updated
    return updated


def list_tenants() -> list[Tenant]:
    """Return all tenants (active and inactive)."""
    return list(_tenants.values())


def deactivate_tenant(tenant_id: str) -> Tenant | None:
    """Soft-delete a tenant by marking it inactive."""
    return update_tenant(tenant_id, {"is_active": False})


def get_plan_limit(tenant_id: str) -> int | None:
    """Return the monthly request limit for a tenant's plan.

    Returns None for unlimited (enterprise), or the numeric cap.
    Raises ValueError if the tenant doesn't exist.
    """
    tenant = _tenants.get(tenant_id)
    if tenant is None:
        raise ValueError(f"Tenant {tenant_id} not found")
    return _plan_limit(tenant.plan)


def reset_store() -> None:
    """Clear in-memory state. Used by tests to get a clean slate."""
    _tenants.clear()
