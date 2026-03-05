"""Data isolation primitives for multi-tenant request scoping.

Uses Python contextvars so the current tenant propagates through async
call chains without explicit parameter passing. The FastAPI dependency
`require_permission` combines tenant context with RBAC checking.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from fastapi import Depends, Header, HTTPException

from blockthrough.enterprise import rbac
from blockthrough.enterprise.tenants import get_tenant
from blockthrough.enterprise.types import Permission, Tenant

# Context variable holding the active tenant for the current request.
# Set by TenantContext or the require_tenant dependency.
_current_tenant: ContextVar[Tenant | None] = ContextVar(
    "_current_tenant", default=None
)

# Context variable holding the current user ID for permission checks.
_current_user_id: ContextVar[str | None] = ContextVar(
    "_current_user_id", default=None
)


class TenantContext:
    """Context manager that scopes a block of code to a specific tenant.

    Usage::

        async with TenantContext(tenant):
            # get_current_tenant() returns `tenant` inside here
            await do_work()
    """

    def __init__(self, tenant: Tenant) -> None:
        self._tenant = tenant
        self._token: Any = None

    async def __aenter__(self) -> Tenant:
        self._token = _current_tenant.set(self._tenant)
        return self._tenant

    async def __aexit__(self, *exc: object) -> None:
        _current_tenant.reset(self._token)

    # Support sync usage too for tests and non-async code paths
    def __enter__(self) -> Tenant:
        self._token = _current_tenant.set(self._tenant)
        return self._tenant

    def __exit__(self, *exc: object) -> None:
        _current_tenant.reset(self._token)


def get_current_tenant() -> Tenant | None:
    """Return the tenant scoped to the current async context, or None."""
    return _current_tenant.get()


def set_current_user(user_id: str) -> Any:
    """Set the current user ID in context. Returns the reset token."""
    return _current_user_id.set(user_id)


def get_current_user_id() -> str | None:
    """Return the user ID scoped to the current async context, or None."""
    return _current_user_id.get()


def tenant_filter(query: str, tenant_id: str) -> tuple[str, dict[str, str]]:
    """Append a parameterized org_id filter clause to a SQL query string.

    Returns (modified_query, params_dict) so the caller binds :_tenant_id
    as a parameter instead of interpolating the value. This prevents SQL
    injection from user-controlled tenant IDs.
    """
    clause = "org_id = :_tenant_id"
    upper = query.upper()

    if "WHERE" in upper:
        return query + f" AND {clause}", {"_tenant_id": tenant_id}
    else:
        return query + f" WHERE {clause}", {"_tenant_id": tenant_id}


async def require_tenant(
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
) -> Tenant:
    """FastAPI dependency that resolves and validates the tenant from headers."""
    tenant = get_tenant(x_tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if not tenant.is_active:
        raise HTTPException(status_code=403, detail="Tenant is deactivated")
    _current_tenant.set(tenant)
    return tenant


def require_permission(permission: Permission):
    """Factory for FastAPI dependencies that enforce a specific permission.

    Returns a dependency function that reads X-User-Id from headers,
    checks the user's role against the requested permission, and
    raises 403 if denied.

    Usage in routes::

        @router.get("/data", dependencies=[Depends(require_permission(Permission.READ_EVENTS))])
        async def get_data(): ...
    """

    async def _check(
        x_user_id: str = Header(..., alias="X-User-Id"),
        _tenant: Tenant = Depends(require_tenant),
    ) -> None:
        if not rbac.check_permission(x_user_id, permission):
            raise HTTPException(
                status_code=403,
                detail=f"Permission denied: {permission.value}",
            )
        _current_user_id.set(x_user_id)

    return _check
