"""Pydantic models and enums for the enterprise multi-tenant platform.

Defines tenant, user, role, and permission types that power the
RBAC system, data isolation, and plan-based rate limiting.
"""

from __future__ import annotations

import enum
from datetime import datetime

from pydantic import BaseModel, Field


class Role(str, enum.Enum):
    """User roles within a tenant organization."""

    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"
    AUDITOR = "auditor"


class Plan(str, enum.Enum):
    """Tenant subscription tiers with different request limits."""

    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class Permission(str, enum.Enum):
    """Granular permissions checked by the RBAC middleware."""

    READ_EVENTS = "read_events"
    WRITE_EVENTS = "write_events"
    MANAGE_ALERTS = "manage_alerts"
    MANAGE_BUDGETS = "manage_budgets"
    VIEW_COMPLIANCE = "view_compliance"
    EXPORT_AUDIT = "export_audit"
    MANAGE_USERS = "manage_users"
    MANAGE_TENANT = "manage_tenant"


# Maps each role to the set of permissions it grants.
# Used by check_permission() in rbac.py — the single source of truth
# for access control decisions.
ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.ADMIN: set(Permission),
    Role.EDITOR: {
        Permission.READ_EVENTS,
        Permission.WRITE_EVENTS,
        Permission.MANAGE_ALERTS,
        Permission.MANAGE_BUDGETS,
    },
    Role.VIEWER: {
        Permission.READ_EVENTS,
    },
    Role.AUDITOR: {
        Permission.READ_EVENTS,
        Permission.VIEW_COMPLIANCE,
        Permission.EXPORT_AUDIT,
    },
}


class Tenant(BaseModel):
    """A tenant organization in the multi-tenant platform."""

    id: str
    name: str
    slug: str
    plan: Plan = Plan.FREE
    created_at: datetime
    settings: dict = Field(default_factory=dict)
    is_active: bool = True


class TenantUser(BaseModel):
    """A user belonging to a specific tenant with a role-based access level."""

    id: str
    tenant_id: str
    email: str
    name: str
    role: Role
    created_at: datetime
    last_login: datetime | None = None
    sso_provider: str | None = None
