"""Alert and budget management API endpoints."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from blockthrough.alerts.types import (
    AlertChannel,
    AlertSeverity,
    BudgetAction,
    BudgetPeriod,
    RuleType,
)
from blockthrough.api.deps import get_db
from blockthrough.db.queries import (
    delete_alert_rule as db_delete_alert_rule,
    get_alert_history as db_get_alert_history,
    get_alert_rules as db_get_alert_rules,
    get_budget_by_id,
    get_budget_configs as db_get_budget_configs,
    insert_alert_rule,
    insert_budget_config,
    update_alert_rule as db_update_alert_rule,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class AlertRuleCreate(BaseModel):
    org_id: str
    rule_type: RuleType
    threshold_config: dict
    channel: AlertChannel
    webhook_url: str | None = None
    enabled: bool = True


class AlertRuleUpdate(BaseModel):
    threshold_config: dict | None = None
    channel: AlertChannel | None = None
    webhook_url: str | None = None
    enabled: bool | None = None


class AlertRuleResponse(BaseModel):
    id: str
    org_id: str
    rule_type: RuleType
    threshold_config: dict
    channel: AlertChannel
    webhook_url: str | None
    enabled: bool
    created_at: datetime
    updated_at: datetime


class AlertHistoryItem(BaseModel):
    id: str
    rule_id: str
    triggered_at: datetime
    message: str
    severity: AlertSeverity
    resolved: bool
    resolved_at: datetime | None


class AlertHistoryResponse(BaseModel):
    items: list[AlertHistoryItem]
    total_count: int
    has_more: bool


class BudgetCreate(BaseModel):
    org_id: str
    project_id: str | None = None
    budget_usd: float = Field(gt=0)
    period: BudgetPeriod
    action: BudgetAction


class BudgetResponse(BaseModel):
    id: str
    org_id: str
    project_id: str | None
    budget_usd: float
    period: BudgetPeriod
    action: BudgetAction
    current_spend: float
    period_start: datetime
    created_at: datetime


class BudgetStatusResponse(BaseModel):
    id: str
    budget_usd: float
    current_spend: float
    utilization_pct: float
    period: BudgetPeriod
    action: BudgetAction
    period_start: datetime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_alert_rule(row: dict) -> AlertRuleResponse:
    """Map a DB row dict into the API response model."""
    return AlertRuleResponse(
        id=str(row["id"]),
        org_id=row["org_id"],
        rule_type=row["rule_type"],
        threshold_config=row["threshold_config"],
        channel=row["channel"],
        webhook_url=row["webhook_url"],
        enabled=row["enabled"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_alert_history(row: dict) -> AlertHistoryItem:
    return AlertHistoryItem(
        id=str(row["id"]),
        rule_id=str(row["rule_id"]),
        triggered_at=row["triggered_at"],
        message=row["message"],
        severity=row["severity"],
        resolved=row["resolved"],
        resolved_at=row.get("resolved_at"),
    )


def _row_to_budget(row: dict) -> BudgetResponse:
    return BudgetResponse(
        id=str(row["id"]),
        org_id=row["org_id"],
        project_id=row.get("project_id"),
        budget_usd=float(row["budget_usd"]),
        period=row["period"],
        action=row["action"],
        current_spend=float(row["current_spend"]),
        period_start=row["period_start"],
        created_at=row["created_at"],
    )


# ---------------------------------------------------------------------------
# Alert rule endpoints
# ---------------------------------------------------------------------------


@router.get("/alerts/rules", response_model=list[AlertRuleResponse])
async def list_alert_rules(
    org_id: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[AlertRuleResponse]:
    rows = await db_get_alert_rules(db, org_id=org_id)
    return [_row_to_alert_rule(r) for r in rows]


@router.post("/alerts/rules", response_model=AlertRuleResponse, status_code=201)
async def create_alert_rule(
    body: AlertRuleCreate,
    db: AsyncSession = Depends(get_db),
) -> AlertRuleResponse:
    row = await insert_alert_rule(
        db,
        org_id=body.org_id,
        rule_type=body.rule_type.value,
        threshold_config=body.threshold_config,
        channel=body.channel.value,
        webhook_url=body.webhook_url,
        enabled=body.enabled,
    )
    return _row_to_alert_rule(row)


@router.put("/alerts/rules/{rule_id}", response_model=AlertRuleResponse)
async def update_alert_rule(
    rule_id: str,
    body: AlertRuleUpdate,
    db: AsyncSession = Depends(get_db),
) -> AlertRuleResponse:
    update_fields = body.model_dump(exclude_unset=True)
    # Enum values need to be serialized to their string form for the DB
    if "channel" in update_fields and update_fields["channel"] is not None:
        update_fields["channel"] = update_fields["channel"].value

    row = await db_update_alert_rule(db, rule_id, **update_fields)
    if not row:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    return _row_to_alert_rule(row)


@router.delete("/alerts/rules/{rule_id}", status_code=204)
async def delete_alert_rule(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    deleted = await db_delete_alert_rule(db, rule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Alert rule not found")


# ---------------------------------------------------------------------------
# Alert history
# ---------------------------------------------------------------------------


@router.get("/alerts/history", response_model=AlertHistoryResponse)
async def get_alert_history(
    org_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> AlertHistoryResponse:
    rows, total = await db_get_alert_history(db, org_id=org_id, limit=limit, offset=offset)
    return AlertHistoryResponse(
        items=[_row_to_alert_history(r) for r in rows],
        total_count=total,
        has_more=(offset + limit) < total,
    )


# ---------------------------------------------------------------------------
# Budget endpoints
# ---------------------------------------------------------------------------


@router.get("/budgets", response_model=list[BudgetResponse])
async def list_budgets(
    org_id: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[BudgetResponse]:
    rows = await db_get_budget_configs(db, org_id=org_id)
    return [_row_to_budget(r) for r in rows]


@router.post("/budgets", response_model=BudgetResponse, status_code=201)
async def create_budget(
    body: BudgetCreate,
    db: AsyncSession = Depends(get_db),
) -> BudgetResponse:
    row = await insert_budget_config(
        db,
        org_id=body.org_id,
        project_id=body.project_id,
        budget_usd=body.budget_usd,
        period=body.period.value,
        action=body.action.value,
    )
    return _row_to_budget(row)


@router.get("/budgets/{budget_id}/status", response_model=BudgetStatusResponse)
async def get_budget_status(
    budget_id: str,
    db: AsyncSession = Depends(get_db),
) -> BudgetStatusResponse:
    row = await get_budget_by_id(db, budget_id)
    if not row:
        raise HTTPException(status_code=404, detail="Budget not found")

    budget_usd = float(row["budget_usd"])
    current_spend = float(row["current_spend"])
    utilization = (current_spend / budget_usd * 100) if budget_usd > 0 else 0.0

    return BudgetStatusResponse(
        id=str(row["id"]),
        budget_usd=budget_usd,
        current_spend=current_spend,
        utilization_pct=utilization,
        period=row["period"],
        action=row["action"],
        period_start=row["period_start"],
    )
