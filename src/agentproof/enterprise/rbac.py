"""Role-based access control for the enterprise multi-tenant platform.

Manages tenant users and their permissions. The SSO authenticate_sso()
stub validates token format only — real SAML/OIDC integration is future work.
"""

from __future__ import annotations

import uuid

from agentproof.enterprise.types import (
    Permission,
    Role,
    ROLE_PERMISSIONS,
    TenantUser,
)
from agentproof.utils import utcnow


# In-memory user store: user_id -> TenantUser
_users: dict[str, TenantUser] = {}


def add_user(
    tenant_id: str,
    email: str,
    name: str,
    role: Role = Role.VIEWER,
    sso_provider: str | None = None,
) -> TenantUser:
    """Add a user to a tenant with the given role."""
    user = TenantUser(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        email=email,
        name=name,
        role=role,
        created_at=utcnow(),
        sso_provider=sso_provider,
    )
    _users[user.id] = user
    return user


def remove_user(tenant_id: str, user_id: str) -> bool:
    """Remove a user from a tenant. Returns True if the user was found and removed."""
    user = _users.get(user_id)
    if user is None or user.tenant_id != tenant_id:
        return False
    del _users[user_id]
    return True


def update_role(tenant_id: str, user_id: str, new_role: Role) -> TenantUser | None:
    """Change a user's role within their tenant. Returns None if not found."""
    user = _users.get(user_id)
    if user is None or user.tenant_id != tenant_id:
        return None

    updated = TenantUser(
        **{**user.model_dump(), "role": new_role}
    )
    _users[user_id] = updated
    return updated


def check_permission(user_id: str, permission: Permission) -> bool:
    """Check whether a user has a specific permission via their role."""
    user = _users.get(user_id)
    if user is None:
        return False
    allowed = ROLE_PERMISSIONS.get(user.role, set())
    return permission in allowed


def get_user(user_id: str) -> TenantUser | None:
    """Look up a user by ID."""
    return _users.get(user_id)


def get_tenant_users(tenant_id: str) -> list[TenantUser]:
    """Return all users belonging to a tenant."""
    return [u for u in _users.values() if u.tenant_id == tenant_id]


def authenticate_sso(token: str, provider: str) -> TenantUser | None:
    """Stub SSO authentication — validates token format, returns matching user.

    Real implementation would verify SAML assertions or OIDC JWTs against
    the identity provider. For now, we accept tokens in the format
    'sso:<provider>:<email>' and look up the user by email + provider.
    """
    parts = token.split(":")
    if len(parts) != 3 or parts[0] != "sso" or parts[1] != provider:
        return None

    email = parts[2]
    for user in _users.values():
        if user.email == email and user.sso_provider == provider:
            # Update last_login on successful auth
            updated = TenantUser(
                **{**user.model_dump(), "last_login": utcnow()}
            )
            _users[user.id] = updated
            return updated

    return None


def reset_store() -> None:
    """Clear in-memory state. Used by tests to get a clean slate."""
    _users.clear()
