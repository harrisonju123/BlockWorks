"""Pydantic models and enums for the alerts & budgets subsystem."""

from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class RuleType(str, enum.Enum):
    SPEND_THRESHOLD = "spend_threshold"
    ANOMALY_ZSCORE = "anomaly_zscore"
    ERROR_RATE = "error_rate"
    LATENCY_P95 = "latency_p95"


class AlertChannel(str, enum.Enum):
    SLACK = "slack"
    EMAIL = "email"
    BOTH = "both"


class BudgetPeriod(str, enum.Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class BudgetAction(str, enum.Enum):
    NONE = "none"
    ALERT = "alert"
    DOWNGRADE = "downgrade"
    BLOCK = "block"


class AlertSeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertRule(BaseModel):
    """User-defined alert trigger configuration."""

    id: UUID
    org_id: str
    rule_type: RuleType
    threshold_config: dict
    channel: AlertChannel
    webhook_url: str | None = None
    enabled: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


class BudgetConfig(BaseModel):
    """Spend cap for an org or project with an enforcement action."""

    id: UUID
    org_id: str
    project_id: str | None = None
    budget_usd: float = Field(gt=0)
    period: BudgetPeriod
    action: BudgetAction
    current_spend: float = 0.0
    period_start: datetime | None = None


class AlertEvent(BaseModel):
    """A fired alert event ready to be persisted and dispatched."""

    rule_id: UUID
    triggered_at: datetime
    message: str
    severity: AlertSeverity
