"""Tests for alert checker deduplication and lifecycle logic."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

import pytest

from agentproof.alerts.checker import AlertChecker
from agentproof.alerts.types import (
    AlertChannel,
    AlertEvent,
    AlertRule,
    AlertSeverity,
    RuleType,
)


def _rule(enabled: bool = True) -> AlertRule:
    return AlertRule(
        id=uuid.uuid4(),
        org_id="org-test",
        rule_type=RuleType.SPEND_THRESHOLD,
        threshold_config={"amount_usd": 500.0},
        channel=AlertChannel.SLACK,
        webhook_url="https://hooks.slack.com/test",
        enabled=enabled,
    )


def _event(rule: AlertRule) -> AlertEvent:
    return AlertEvent(
        rule_id=rule.id,
        triggered_at=datetime.now(timezone.utc),
        message="Test alert fired",
        severity=AlertSeverity.WARNING,
    )


class TestDeduplication:

    def test_first_fire_allowed(self) -> None:
        checker = AlertChecker(cooldown_s=3600)
        rule = _rule()
        assert checker.should_fire(rule.id) is True

    def test_second_fire_within_cooldown_blocked(self) -> None:
        checker = AlertChecker(cooldown_s=3600)
        rule = _rule()
        checker.record_fired(rule.id)
        assert checker.should_fire(rule.id) is False

    def test_fire_after_cooldown_allowed(self) -> None:
        checker = AlertChecker(cooldown_s=1)
        rule = _rule()
        checker.record_fired(rule.id)
        # Manipulate the timestamp to simulate time passing
        checker._last_fired[rule.id] = time.monotonic() - 2
        assert checker.should_fire(rule.id) is True

    def test_different_rules_independent(self) -> None:
        checker = AlertChecker(cooldown_s=3600)
        rule_a = _rule()
        rule_b = _rule()
        checker.record_fired(rule_a.id)
        assert checker.should_fire(rule_a.id) is False
        assert checker.should_fire(rule_b.id) is True

    def test_zero_cooldown_always_fires(self) -> None:
        checker = AlertChecker(cooldown_s=0)
        rule = _rule()
        checker.record_fired(rule.id)
        assert checker.should_fire(rule.id) is True


class TestProcessEvent:

    def test_process_passes_through_when_not_deduplicated(self) -> None:
        checker = AlertChecker(cooldown_s=3600)
        rule = _rule()
        event = _event(rule)
        result = checker.process_event(rule, event)
        assert result is event

    def test_process_returns_none_when_deduplicated(self) -> None:
        checker = AlertChecker(cooldown_s=3600)
        rule = _rule()
        event = _event(rule)
        # First fire
        checker.process_event(rule, event)
        # Second fire should be deduplicated
        result = checker.process_event(rule, event)
        assert result is None

    def test_process_records_fired_timestamp(self) -> None:
        checker = AlertChecker(cooldown_s=3600)
        rule = _rule()
        event = _event(rule)
        assert rule.id not in checker._last_fired
        checker.process_event(rule, event)
        assert rule.id in checker._last_fired


class TestCreateEvent:

    def test_create_event_fields(self) -> None:
        checker = AlertChecker()
        rule = _rule()
        event = checker.create_event(rule, "test message", AlertSeverity.CRITICAL)
        assert event.rule_id == rule.id
        assert event.message == "test message"
        assert event.severity == AlertSeverity.CRITICAL
        assert isinstance(event.triggered_at, datetime)

    def test_create_event_timestamp_is_utc(self) -> None:
        checker = AlertChecker()
        rule = _rule()
        event = checker.create_event(rule, "test", AlertSeverity.INFO)
        assert event.triggered_at.tzinfo is not None


class TestCheckerLifecycle:

    @pytest.mark.asyncio
    async def test_shutdown_stops_loop(self) -> None:
        """Checker should exit cleanly when shutdown is called."""
        import asyncio

        checker = AlertChecker(check_interval_s=60)

        async def stop_after_brief_delay() -> None:
            await asyncio.sleep(0.05)
            await checker.shutdown()

        # Run the checker and the shutdown in parallel
        await asyncio.gather(
            checker.run(rules_provider=[]),
            stop_after_brief_delay(),
        )
        # If we reach here, the loop exited cleanly
        assert checker._shutdown_event.is_set()

    @pytest.mark.asyncio
    async def test_disabled_rules_skipped(self) -> None:
        """Disabled rules should not be evaluated."""
        import asyncio

        checker = AlertChecker(check_interval_s=60)
        disabled_rule = _rule(enabled=False)

        call_count = 0
        original_evaluate = checker.evaluate_rule

        async def counting_evaluate(rule: AlertRule) -> AlertEvent | None:
            nonlocal call_count
            call_count += 1
            return await original_evaluate(rule)

        checker.evaluate_rule = counting_evaluate  # type: ignore[assignment]

        async def stop_after_one_cycle() -> None:
            await asyncio.sleep(0.05)
            await checker.shutdown()

        await asyncio.gather(
            checker.run(rules_provider=[disabled_rule]),
            stop_after_one_cycle(),
        )

        assert call_count == 0
