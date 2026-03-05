"""Tests for RBAC — user management, permission checks, role updates, SSO stub."""

from __future__ import annotations

from blockthrough.enterprise.rbac import (
    add_user,
    authenticate_sso,
    check_permission,
    get_tenant_users,
    get_user,
    remove_user,
    reset_store,
    update_role,
)
from blockthrough.enterprise.tenants import create_tenant
from blockthrough.enterprise.tenants import reset_store as reset_tenant_store
from blockthrough.enterprise.types import Permission, Role


class TestAddUser:
    def setup_method(self) -> None:
        reset_store()
        reset_tenant_store()

    def test_add_user_returns_user(self) -> None:
        t = create_tenant("Org")
        u = add_user(t.id, "alice@co.com", "Alice", Role.ADMIN)
        assert u.id
        assert u.tenant_id == t.id
        assert u.email == "alice@co.com"
        assert u.role == Role.ADMIN

    def test_default_role_is_viewer(self) -> None:
        t = create_tenant("Org")
        u = add_user(t.id, "bob@co.com", "Bob")
        assert u.role == Role.VIEWER

    def test_add_user_with_sso(self) -> None:
        t = create_tenant("Org")
        u = add_user(t.id, "carol@co.com", "Carol", sso_provider="okta")
        assert u.sso_provider == "okta"

    def test_user_is_retrievable(self) -> None:
        t = create_tenant("Org")
        u = add_user(t.id, "dan@co.com", "Dan")
        found = get_user(u.id)
        assert found is not None
        assert found.email == "dan@co.com"


class TestRemoveUser:
    def setup_method(self) -> None:
        reset_store()
        reset_tenant_store()

    def test_remove_existing_user(self) -> None:
        t = create_tenant("Org")
        u = add_user(t.id, "x@co.com", "X")
        assert remove_user(t.id, u.id) is True
        assert get_user(u.id) is None

    def test_remove_nonexistent_returns_false(self) -> None:
        t = create_tenant("Org")
        assert remove_user(t.id, "no-such-user") is False

    def test_remove_wrong_tenant_returns_false(self) -> None:
        """Cannot remove a user via a different tenant's endpoint."""
        t1 = create_tenant("Org1")
        t2 = create_tenant("Org2")
        u = add_user(t1.id, "x@co.com", "X")
        assert remove_user(t2.id, u.id) is False
        # User should still exist
        assert get_user(u.id) is not None


class TestUpdateRole:
    def setup_method(self) -> None:
        reset_store()
        reset_tenant_store()

    def test_update_role(self) -> None:
        t = create_tenant("Org")
        u = add_user(t.id, "y@co.com", "Y", Role.VIEWER)
        updated = update_role(t.id, u.id, Role.EDITOR)
        assert updated is not None
        assert updated.role == Role.EDITOR

    def test_update_role_nonexistent_returns_none(self) -> None:
        t = create_tenant("Org")
        assert update_role(t.id, "ghost", Role.ADMIN) is None

    def test_update_role_wrong_tenant_returns_none(self) -> None:
        t1 = create_tenant("Org1")
        t2 = create_tenant("Org2")
        u = add_user(t1.id, "y@co.com", "Y")
        assert update_role(t2.id, u.id, Role.ADMIN) is None


class TestCheckPermission:
    """Verify that each role gets exactly its defined permissions."""

    def setup_method(self) -> None:
        reset_store()
        reset_tenant_store()
        self.tenant = create_tenant("Org")

    def test_admin_has_all_permissions(self) -> None:
        u = add_user(self.tenant.id, "admin@co.com", "Admin", Role.ADMIN)
        for perm in Permission:
            assert check_permission(u.id, perm) is True, f"Admin missing {perm}"

    def test_editor_permissions(self) -> None:
        u = add_user(self.tenant.id, "editor@co.com", "Editor", Role.EDITOR)
        assert check_permission(u.id, Permission.READ_EVENTS) is True
        assert check_permission(u.id, Permission.WRITE_EVENTS) is True
        assert check_permission(u.id, Permission.MANAGE_ALERTS) is True
        assert check_permission(u.id, Permission.MANAGE_BUDGETS) is True
        # Should NOT have
        assert check_permission(u.id, Permission.VIEW_COMPLIANCE) is False
        assert check_permission(u.id, Permission.EXPORT_AUDIT) is False
        assert check_permission(u.id, Permission.MANAGE_USERS) is False
        assert check_permission(u.id, Permission.MANAGE_TENANT) is False

    def test_viewer_permissions(self) -> None:
        u = add_user(self.tenant.id, "viewer@co.com", "Viewer", Role.VIEWER)
        assert check_permission(u.id, Permission.READ_EVENTS) is True
        # Should NOT have any others
        assert check_permission(u.id, Permission.WRITE_EVENTS) is False
        assert check_permission(u.id, Permission.MANAGE_ALERTS) is False
        assert check_permission(u.id, Permission.VIEW_COMPLIANCE) is False

    def test_auditor_permissions(self) -> None:
        u = add_user(self.tenant.id, "auditor@co.com", "Auditor", Role.AUDITOR)
        assert check_permission(u.id, Permission.READ_EVENTS) is True
        assert check_permission(u.id, Permission.VIEW_COMPLIANCE) is True
        assert check_permission(u.id, Permission.EXPORT_AUDIT) is True
        # Should NOT have
        assert check_permission(u.id, Permission.WRITE_EVENTS) is False
        assert check_permission(u.id, Permission.MANAGE_USERS) is False

    def test_nonexistent_user_denied(self) -> None:
        assert check_permission("no-such-user", Permission.READ_EVENTS) is False

    def test_permission_changes_with_role_update(self) -> None:
        """Upgrading a viewer to editor should grant write access."""
        u = add_user(self.tenant.id, "z@co.com", "Z", Role.VIEWER)
        assert check_permission(u.id, Permission.WRITE_EVENTS) is False
        update_role(self.tenant.id, u.id, Role.EDITOR)
        assert check_permission(u.id, Permission.WRITE_EVENTS) is True


class TestGetTenantUsers:
    def setup_method(self) -> None:
        reset_store()
        reset_tenant_store()

    def test_empty_tenant(self) -> None:
        t = create_tenant("Empty")
        assert get_tenant_users(t.id) == []

    def test_returns_only_tenant_users(self) -> None:
        t1 = create_tenant("Org1")
        t2 = create_tenant("Org2")
        add_user(t1.id, "a@co.com", "A")
        add_user(t1.id, "b@co.com", "B")
        add_user(t2.id, "c@co.com", "C")
        assert len(get_tenant_users(t1.id)) == 2
        assert len(get_tenant_users(t2.id)) == 1


class TestAuthenticateSSO:
    def setup_method(self) -> None:
        reset_store()
        reset_tenant_store()

    def test_valid_sso_token(self) -> None:
        t = create_tenant("SSO Org")
        add_user(t.id, "sso@co.com", "SSO User", sso_provider="okta")
        result = authenticate_sso("sso:okta:sso@co.com", "okta")
        assert result is not None
        assert result.email == "sso@co.com"
        assert result.last_login is not None

    def test_wrong_provider(self) -> None:
        t = create_tenant("SSO Org")
        add_user(t.id, "sso@co.com", "SSO User", sso_provider="okta")
        result = authenticate_sso("sso:azure_ad:sso@co.com", "azure_ad")
        assert result is None

    def test_malformed_token(self) -> None:
        assert authenticate_sso("garbage", "okta") is None

    def test_wrong_prefix(self) -> None:
        assert authenticate_sso("bad:okta:user@co.com", "okta") is None

    def test_provider_mismatch_in_token(self) -> None:
        """Token says azure_ad but we're checking against okta."""
        t = create_tenant("Org")
        add_user(t.id, "u@co.com", "U", sso_provider="okta")
        assert authenticate_sso("sso:azure_ad:u@co.com", "okta") is None
