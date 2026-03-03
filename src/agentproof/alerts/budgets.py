"""Budget cap enforcement and spend tracking.

Tracks cumulative spend per (org_id, project_id, period) and determines
what action to take when thresholds are approached or breached.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentproof.alerts.types import AlertSeverity, BudgetAction, BudgetConfig
from agentproof.models import MODEL_CATALOG, get_downgrade

# Backward-compat alias — tests and external callers that referenced the old dict
# can still import it from here. Derived from MODEL_CATALOG at import time.
MODEL_DOWNGRADE_MAP: dict[str, str] = {
    model: info.downgrade_to
    for model, info in MODEL_CATALOG.items()
    if info.downgrade_to is not None
}


@dataclass(frozen=True)
class BudgetCheckResult:
    """Outcome of a budget check for a single incoming request."""

    action: BudgetAction
    severity: AlertSeverity
    utilization_pct: float
    message: str
    suggested_model: str | None = None


def check_budget(
    config: BudgetConfig,
    new_cost: float,
    *,
    current_model: str | None = None,
) -> BudgetCheckResult:
    """Evaluate whether a new cost would breach budget thresholds.

    Thresholds: 80% -> info, 95% -> warning, 100% -> configured action.
    The caller decides whether to enforce (this function is side-effect-free).
    """
    projected_spend = config.current_spend + new_cost

    if config.budget_usd <= 0:
        return BudgetCheckResult(
            action=BudgetAction.NONE,
            severity=AlertSeverity.INFO,
            utilization_pct=0.0,
            message="Budget is zero or negative; skipping check",
        )

    utilization = projected_spend / config.budget_usd

    if utilization >= 1.0:
        suggested = _suggest_downgrade(current_model) if current_model else None
        return BudgetCheckResult(
            action=config.action,
            severity=AlertSeverity.CRITICAL,
            utilization_pct=utilization * 100,
            message=(
                f"Budget exceeded: ${projected_spend:.2f} / ${config.budget_usd:.2f} "
                f"({utilization:.0%})"
            ),
            suggested_model=suggested,
        )

    if utilization >= 0.95:
        suggested = _suggest_downgrade(current_model) if current_model else None
        return BudgetCheckResult(
            action=BudgetAction.ALERT,
            severity=AlertSeverity.WARNING,
            utilization_pct=utilization * 100,
            message=(
                f"Budget warning: ${projected_spend:.2f} / ${config.budget_usd:.2f} "
                f"({utilization:.0%})"
            ),
            suggested_model=suggested,
        )

    if utilization >= 0.80:
        return BudgetCheckResult(
            action=BudgetAction.ALERT,
            severity=AlertSeverity.INFO,
            utilization_pct=utilization * 100,
            message=(
                f"Budget approaching: ${projected_spend:.2f} / ${config.budget_usd:.2f} "
                f"({utilization:.0%})"
            ),
        )

    return BudgetCheckResult(
        action=BudgetAction.NONE,
        severity=AlertSeverity.INFO,
        utilization_pct=utilization * 100,
        message="Within budget",
    )


def _suggest_downgrade(current_model: str) -> str | None:
    """Suggest a cheaper model, or None if already at the cheapest known tier."""
    return get_downgrade(current_model)
