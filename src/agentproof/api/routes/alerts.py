"""Alert and budget management API endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from agentproof.alerts.types import (
    AlertChannel,
    AlertSeverity,
    BudgetAction,
    BudgetPeriod,
    RuleType,
)
from agentproof.utils import utcnow

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
# In-memory stores (replaced by DB queries once wired to sqlalchemy)
# ---------------------------------------------------------------------------

_alert_rules: dict[str, AlertRuleResponse] = {}
_alert_history: list[AlertHistoryItem] = []
_budget_configs: dict[str, BudgetResponse] = {}


# ---------------------------------------------------------------------------
# Alert rule endpoints
# ---------------------------------------------------------------------------


@router.get("/alerts/rules", response_model=list[AlertRuleResponse])
async def list_alert_rules(
    org_id: str | None = None,
) -> list[AlertRuleResponse]:
    rules = list(_alert_rules.values())
    if org_id:
        rules = [r for r in rules if r.org_id == org_id]
    return rules


@router.post("/alerts/rules", response_model=AlertRuleResponse, status_code=201)
async def create_alert_rule(body: AlertRuleCreate) -> AlertRuleResponse:
    now = utcnow()
    rule = AlertRuleResponse(
        id=str(uuid.uuid4()),
        org_id=body.org_id,
        rule_type=body.rule_type,
        threshold_config=body.threshold_config,
        channel=body.channel,
        webhook_url=body.webhook_url,
        enabled=body.enabled,
        created_at=now,
        updated_at=now,
    )
    _alert_rules[rule.id] = rule
    return rule


@router.put("/alerts/rules/{rule_id}", response_model=AlertRuleResponse)
async def update_alert_rule(rule_id: str, body: AlertRuleUpdate) -> AlertRuleResponse:
    existing = _alert_rules.get(rule_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Alert rule not found")

    updated_data = existing.model_dump()
    update_fields = body.model_dump(exclude_unset=True)
    updated_data.update(update_fields)
    updated_data["updated_at"] = utcnow()

    updated = AlertRuleResponse(**updated_data)
    _alert_rules[rule_id] = updated
    return updated


@router.delete("/alerts/rules/{rule_id}", status_code=204)
async def delete_alert_rule(rule_id: str) -> None:
    if rule_id not in _alert_rules:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    del _alert_rules[rule_id]


# ---------------------------------------------------------------------------
# Alert history
# ---------------------------------------------------------------------------


@router.get("/alerts/history", response_model=AlertHistoryResponse)
async def get_alert_history(
    org_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> AlertHistoryResponse:
    # In-memory implementation; production reads from alert_history hypertable
    items = _alert_history
    total = len(items)
    page = items[offset : offset + limit]
    return AlertHistoryResponse(
        items=page,
        total_count=total,
        has_more=(offset + limit) < total,
    )


# ---------------------------------------------------------------------------
# Budget endpoints
# ---------------------------------------------------------------------------


@router.get("/budgets", response_model=list[BudgetResponse])
async def list_budgets(
    org_id: str | None = None,
) -> list[BudgetResponse]:
    budgets = list(_budget_configs.values())
    if org_id:
        budgets = [b for b in budgets if b.org_id == org_id]
    return budgets


@router.post("/budgets", response_model=BudgetResponse, status_code=201)
async def create_budget(body: BudgetCreate) -> BudgetResponse:
    now = utcnow()
    budget = BudgetResponse(
        id=str(uuid.uuid4()),
        org_id=body.org_id,
        project_id=body.project_id,
        budget_usd=body.budget_usd,
        period=body.period,
        action=body.action,
        current_spend=0.0,
        period_start=now,
        created_at=now,
    )
    _budget_configs[budget.id] = budget
    return budget


@router.get("/budgets/{budget_id}/status", response_model=BudgetStatusResponse)
async def get_budget_status(budget_id: str) -> BudgetStatusResponse:
    budget = _budget_configs.get(budget_id)
    if not budget:
        raise HTTPException(status_code=404, detail="Budget not found")

    utilization = (budget.current_spend / budget.budget_usd * 100) if budget.budget_usd > 0 else 0.0

    return BudgetStatusResponse(
        id=budget.id,
        budget_usd=budget.budget_usd,
        current_spend=budget.current_spend,
        utilization_pct=utilization,
        period=budget.period,
        action=budget.action,
        period_start=budget.period_start,
    )


def reset_stores() -> None:
    """Clear in-memory stores. Used by tests to get a clean state."""
    _alert_rules.clear()
    _alert_history.clear()
    _budget_configs.clear()
