"""Tests for budget tracker: cumulative spend, threshold actions, model downgrade."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from blockthrough.alerts.budgets import (
    BudgetCheckResult,
    check_budget,
)
from blockthrough.models import MODEL_CATALOG
from blockthrough.alerts.types import (
    AlertSeverity,
    BudgetAction,
    BudgetConfig,
    BudgetPeriod,
)


def _config(
    budget_usd: float = 100.0,
    current_spend: float = 0.0,
    action: BudgetAction = BudgetAction.ALERT,
    period: BudgetPeriod = BudgetPeriod.DAILY,
) -> BudgetConfig:
    return BudgetConfig(
        id=uuid.uuid4(),
        org_id="org-test",
        budget_usd=budget_usd,
        period=period,
        action=action,
        current_spend=current_spend,
        period_start=datetime.now(timezone.utc),
    )


class TestBudgetWithinLimits:

    def test_low_utilization_returns_none_action(self) -> None:
        result = check_budget(_config(budget_usd=100.0, current_spend=10.0), 5.0)
        assert result.action == BudgetAction.NONE
        assert result.utilization_pct == pytest.approx(15.0)

    def test_message_says_within_budget(self) -> None:
        result = check_budget(_config(budget_usd=100.0, current_spend=0.0), 1.0)
        assert "Within budget" in result.message


class TestBudgetInfoThreshold:

    def test_eighty_percent_triggers_info(self) -> None:
        # 79 + 1.01 = 80.01 -> above 80%
        result = check_budget(_config(budget_usd=100.0, current_spend=79.0), 1.01)
        assert result.action == BudgetAction.ALERT
        assert result.severity == AlertSeverity.INFO

    def test_exactly_eighty_percent_triggers_info(self) -> None:
        result = check_budget(_config(budget_usd=100.0, current_spend=79.0), 1.0)
        assert result.action == BudgetAction.ALERT
        assert result.severity == AlertSeverity.INFO

    def test_just_below_eighty_no_alert(self) -> None:
        result = check_budget(_config(budget_usd=100.0, current_spend=79.0), 0.99)
        assert result.action == BudgetAction.NONE


class TestBudgetWarningThreshold:

    def test_ninety_five_percent_triggers_warning(self) -> None:
        result = check_budget(_config(budget_usd=100.0, current_spend=94.0), 1.0)
        assert result.action == BudgetAction.ALERT
        assert result.severity == AlertSeverity.WARNING

    def test_ninety_five_percent_message(self) -> None:
        result = check_budget(_config(budget_usd=100.0, current_spend=94.0), 1.0)
        assert "warning" in result.message.lower() or "Budget" in result.message


class TestBudgetExceeded:

    def test_hundred_percent_triggers_configured_action(self) -> None:
        result = check_budget(
            _config(budget_usd=100.0, current_spend=99.0, action=BudgetAction.BLOCK),
            1.0,
        )
        assert result.action == BudgetAction.BLOCK
        assert result.severity == AlertSeverity.CRITICAL

    def test_downgrade_action(self) -> None:
        result = check_budget(
            _config(budget_usd=100.0, current_spend=99.0, action=BudgetAction.DOWNGRADE),
            2.0,
            current_model="claude-opus-4-20250514",
        )
        assert result.action == BudgetAction.DOWNGRADE
        assert result.suggested_model == "claude-sonnet-4-6"

    def test_alert_action_on_exceed(self) -> None:
        result = check_budget(
            _config(budget_usd=100.0, current_spend=99.0, action=BudgetAction.ALERT),
            5.0,
        )
        assert result.action == BudgetAction.ALERT
        assert result.severity == AlertSeverity.CRITICAL

    def test_exceeded_message_contains_amounts(self) -> None:
        result = check_budget(
            _config(budget_usd=100.0, current_spend=99.0),
            10.0,
        )
        assert "$109.00" in result.message
        assert "$100.00" in result.message


class TestModelDowngrade:

    def test_opus_downgrades_to_sonnet(self) -> None:
        result = check_budget(
            _config(budget_usd=100.0, current_spend=100.0, action=BudgetAction.DOWNGRADE),
            1.0,
            current_model="claude-opus-4-20250514",
        )
        assert result.suggested_model == "claude-sonnet-4-6"

    def test_sonnet_downgrades_to_haiku(self) -> None:
        result = check_budget(
            _config(budget_usd=100.0, current_spend=100.0, action=BudgetAction.DOWNGRADE),
            1.0,
            current_model="claude-sonnet-4-20250514",
        )
        assert result.suggested_model == "claude-haiku-4-5-20251001"

    def test_cheapest_model_has_no_downgrade(self) -> None:
        result = check_budget(
            _config(budget_usd=100.0, current_spend=100.0, action=BudgetAction.DOWNGRADE),
            1.0,
            current_model="claude-haiku-4-5-20251001",
        )
        assert result.suggested_model is None

    def test_unknown_model_has_no_downgrade(self) -> None:
        result = check_budget(
            _config(budget_usd=100.0, current_spend=100.0, action=BudgetAction.DOWNGRADE),
            1.0,
            current_model="custom-model-v1",
        )
        assert result.suggested_model is None

    def test_warning_threshold_also_suggests_downgrade(self) -> None:
        """At 95% utilization, suggest a downgrade preemptively."""
        result = check_budget(
            _config(budget_usd=100.0, current_spend=94.0, action=BudgetAction.DOWNGRADE),
            1.0,
            current_model="gpt-4o",
        )
        assert result.suggested_model == "claude-haiku-4-5-20251001"


class TestBudgetEdgeCases:

    def test_zero_budget_skips_check(self) -> None:
        result = check_budget(_config(budget_usd=0.01, current_spend=0.0), 0.0)
        # budget_usd must be > 0 per Field constraint, but 0.01 is valid
        assert result.action == BudgetAction.NONE

    def test_negative_current_spend(self) -> None:
        """Shouldn't happen in practice but should not crash."""
        result = check_budget(_config(budget_usd=100.0, current_spend=-10.0), 5.0)
        assert result.action == BudgetAction.NONE

    def test_all_budget_periods(self) -> None:
        for period in BudgetPeriod:
            result = check_budget(
                _config(budget_usd=100.0, current_spend=50.0, period=period),
                1.0,
            )
            assert result.action == BudgetAction.NONE

    def test_downgrade_map_completeness(self) -> None:
        """Every downgrade target should be a known model in the catalog."""
        for model, info in MODEL_CATALOG.items():
            if info.downgrade_to is not None:
                assert info.downgrade_to in MODEL_CATALOG, (
                    f"{model} downgrades to {info.downgrade_to} which is not in MODEL_CATALOG"
                )
