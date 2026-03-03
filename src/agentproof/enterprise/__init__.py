"""Enterprise multi-tenant platform — tenant isolation, RBAC, and audit export.

Provides the building blocks for white-label enterprise deployments:
tenant lifecycle management, role-based access control, data isolation
via context variables, and compliance audit export with tenant metadata.

Public API:
    Tenant           -- tenant organization model
    TenantUser       -- user with role within a tenant
    Role             -- admin/editor/viewer/auditor enum
    Plan             -- free/pro/enterprise enum
    Permission       -- granular permission enum
    ROLE_PERMISSIONS -- role-to-permissions mapping
    create_tenant    -- register a new tenant
    get_tenant       -- look up tenant by ID
    add_user         -- add a user to a tenant
    check_permission -- test if a user has a permission
    TenantContext    -- context manager for tenant scoping
    require_permission -- FastAPI dependency factory
    export_tenant_audit -- generate tenant-scoped compliance export
"""

from agentproof.enterprise.audit_export import (
    export_tenant_audit,
    schedule_audit_export,
)
from agentproof.enterprise.isolation import (
    TenantContext,
    get_current_tenant,
    require_permission,
    require_tenant,
    tenant_filter,
)
from agentproof.enterprise.rbac import (
    add_user,
    authenticate_sso,
    check_permission,
    get_tenant_users,
    get_user,
    remove_user,
    update_role,
)
from agentproof.enterprise.tenants import (
    create_tenant,
    deactivate_tenant,
    get_plan_limit,
    get_tenant,
    list_tenants,
    update_tenant,
)
from agentproof.enterprise.types import (
    ROLE_PERMISSIONS,
    Permission,
    Plan,
    Role,
    Tenant,
    TenantUser,
)

__all__ = [
    "ROLE_PERMISSIONS",
    "Permission",
    "Plan",
    "Role",
    "Tenant",
    "TenantContext",
    "TenantUser",
    "add_user",
    "authenticate_sso",
    "check_permission",
    "create_tenant",
    "deactivate_tenant",
    "export_tenant_audit",
    "get_current_tenant",
    "get_plan_limit",
    "get_tenant",
    "get_tenant_users",
    "get_user",
    "list_tenants",
    "remove_user",
    "require_permission",
    "require_tenant",
    "schedule_audit_export",
    "tenant_filter",
    "update_role",
    "update_tenant",
]
