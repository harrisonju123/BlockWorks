"""Enterprise multi-tenant API endpoints.

Tenant lifecycle, user management, RBAC, and audit export.
All endpoints use in-memory stores — no DB required.
"""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agentproof.compliance.types import ComplianceFramework
from agentproof.enterprise import rbac, tenants
from agentproof.enterprise.audit_export import (
    export_tenant_audit,
    schedule_audit_export,
)
from agentproof.enterprise.types import Plan, Role
from agentproof.utils import utcnow

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class TenantCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    plan: Plan = Plan.FREE


class TenantUpdateRequest(BaseModel):
    name: str | None = None
    plan: Plan | None = None
    settings: dict | None = None


class TenantResponse(BaseModel):
    id: str
    name: str
    slug: str
    plan: Plan
    created_at: datetime
    settings: dict
    is_active: bool


class UserAddRequest(BaseModel):
    email: str = Field(min_length=1)
    name: str = Field(min_length=1)
    role: Role = Role.VIEWER
    sso_provider: str | None = None


class UserResponse(BaseModel):
    id: str
    tenant_id: str
    email: str
    name: str
    role: Role
    created_at: datetime
    last_login: datetime | None
    sso_provider: str | None


class RoleUpdateRequest(BaseModel):
    role: Role


class AuditExportRequest(BaseModel):
    framework: ComplianceFramework = ComplianceFramework.SOC2
    format: str = Field(default="json", pattern="^(json|csv)$")


class AuditScheduleRequest(BaseModel):
    frequency: str = Field(pattern="^(daily|weekly|monthly)$")
    framework: ComplianceFramework = ComplianceFramework.SOC2
    destination: str = Field(min_length=1)


class UsageResponse(BaseModel):
    tenant_id: str
    plan: Plan
    request_limit: int | None  # None = unlimited
    current_usage: int
    utilization_pct: float | None  # None when unlimited


# ---------------------------------------------------------------------------
# Tenant CRUD
# ---------------------------------------------------------------------------


@router.post(
    "/enterprise/tenants",
    response_model=TenantResponse,
    status_code=201,
)
async def create_tenant(body: TenantCreateRequest) -> TenantResponse:
    tenant = tenants.create_tenant(name=body.name, plan=body.plan)
    return TenantResponse(**tenant.model_dump())


@router.get("/enterprise/tenants", response_model=list[TenantResponse])
async def list_all_tenants() -> list[TenantResponse]:
    return [TenantResponse(**t.model_dump()) for t in tenants.list_tenants()]


@router.get("/enterprise/tenants/{tenant_id}", response_model=TenantResponse)
async def get_tenant_detail(tenant_id: str) -> TenantResponse:
    tenant = tenants.get_tenant(tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return TenantResponse(**tenant.model_dump())


@router.put("/enterprise/tenants/{tenant_id}", response_model=TenantResponse)
async def update_tenant(tenant_id: str, body: TenantUpdateRequest) -> TenantResponse:
    updates = body.model_dump(exclude_unset=True)
    tenant = tenants.update_tenant(tenant_id, updates)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return TenantResponse(**tenant.model_dump())


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------


@router.post(
    "/enterprise/tenants/{tenant_id}/users",
    response_model=UserResponse,
    status_code=201,
)
async def add_user(tenant_id: str, body: UserAddRequest) -> UserResponse:
    # Verify tenant exists
    tenant = tenants.get_tenant(tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")

    user = rbac.add_user(
        tenant_id=tenant_id,
        email=body.email,
        name=body.name,
        role=body.role,
        sso_provider=body.sso_provider,
    )
    return UserResponse(**user.model_dump())


@router.get(
    "/enterprise/tenants/{tenant_id}/users",
    response_model=list[UserResponse],
)
async def list_users(tenant_id: str) -> list[UserResponse]:
    tenant = tenants.get_tenant(tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    users = rbac.get_tenant_users(tenant_id)
    return [UserResponse(**u.model_dump()) for u in users]


@router.put(
    "/enterprise/tenants/{tenant_id}/users/{user_id}/role",
    response_model=UserResponse,
)
async def update_user_role(
    tenant_id: str, user_id: str, body: RoleUpdateRequest
) -> UserResponse:
    user = rbac.update_role(tenant_id, user_id, body.role)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found in tenant")
    return UserResponse(**user.model_dump())


@router.delete(
    "/enterprise/tenants/{tenant_id}/users/{user_id}",
    status_code=204,
)
async def remove_user(tenant_id: str, user_id: str) -> None:
    removed = rbac.remove_user(tenant_id, user_id)
    if not removed:
        raise HTTPException(status_code=404, detail="User not found in tenant")


# ---------------------------------------------------------------------------
# Audit export
# ---------------------------------------------------------------------------


@router.post("/enterprise/tenants/{tenant_id}/audit-export")
async def trigger_audit_export(
    tenant_id: str, body: AuditExportRequest
) -> dict:
    """Trigger an on-demand audit export for a tenant.

    In a full implementation this would query llm_events filtered by
    tenant_id and build real audit records. For the MVP, we return
    an empty but structurally valid export envelope.
    """
    tenant = tenants.get_tenant(tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Build a minimal report — real implementation queries the DB
    from agentproof.compliance.types import AuditReport

    now = utcnow()
    empty_report = AuditReport(
        org_id=tenant_id,
        period_start=now,
        period_end=now,
        generated_at=now,
    )

    export_bytes = export_tenant_audit(
        tenant_id=tenant_id,
        report=empty_report,
        framework=body.framework,
        fmt=body.format,
    )

    if body.format == "csv":
        return {"format": "csv", "size_bytes": len(export_bytes), "content": export_bytes.decode("utf-8")}

    return json.loads(export_bytes)


# ---------------------------------------------------------------------------
# Usage / plan limits
# ---------------------------------------------------------------------------


@router.get(
    "/enterprise/tenants/{tenant_id}/usage",
    response_model=UsageResponse,
)
async def get_usage(tenant_id: str) -> UsageResponse:
    """Return current plan usage for a tenant.

    In production, current_usage comes from a COUNT on llm_events
    for the current billing period. The MVP returns 0.
    """
    tenant = tenants.get_tenant(tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")

    limit = tenants.get_plan_limit(tenant_id)
    current_usage = 0  # Placeholder — real impl queries event count

    utilization = None
    if limit is not None and limit > 0:
        utilization = round(current_usage / limit * 100, 2)

    return UsageResponse(
        tenant_id=tenant_id,
        plan=tenant.plan,
        request_limit=limit,
        current_usage=current_usage,
        utilization_pct=utilization,
    )


def reset_stores() -> None:
    """Clear all in-memory state. Used by tests."""
    tenants.reset_store()
    rbac.reset_store()
