"""Background alert checker that periodically evaluates alert rules.

Runs on a configurable interval (default 60s). For each enabled rule,
evaluates the condition, fires a notification if triggered, and records
the event in alert_history. Deduplicates within a cooldown period to
avoid alert storms.
"""

from __future__ import annotations

import asyncio
import logging
import time
from uuid import UUID

from blockthrough.alerts.notify import dispatch_alert
from blockthrough.alerts.types import AlertEvent, AlertRule, AlertSeverity
from blockthrough.utils import utcnow

logger = logging.getLogger(__name__)


class AlertChecker:
    """Evaluate alert rules on a timer and dispatch notifications.

    This is a standalone background task -- it does not run in the
    request path. The caller is responsible for starting it via
    asyncio.create_task(checker.run()).
    """

    def __init__(
        self,
        *,
        check_interval_s: int = 60,
        cooldown_s: int = 3600,
    ) -> None:
        self._check_interval_s = check_interval_s
        self._cooldown_s = cooldown_s
        self._shutdown_event = asyncio.Event()
        # Tracks last fire time per rule_id to enforce cooldown
        self._last_fired: dict[UUID, float] = {}

    async def shutdown(self) -> None:
        """Signal the checker loop to stop."""
        self._shutdown_event.set()

    def should_fire(self, rule_id: UUID) -> bool:
        """Check if enough time has passed since the last alert for this rule."""
        last = self._last_fired.get(rule_id)
        if last is None:
            return True
        return (time.monotonic() - last) >= self._cooldown_s

    def record_fired(self, rule_id: UUID) -> None:
        """Mark a rule as having just fired."""
        self._last_fired[rule_id] = time.monotonic()

    def _prune_fired(self) -> None:
        """Remove expired cooldown entries to prevent unbounded growth."""
        cutoff = time.monotonic() - self._cooldown_s
        self._last_fired = {k: v for k, v in self._last_fired.items() if v > cutoff}

    async def evaluate_rule(self, rule: AlertRule) -> AlertEvent | None:
        """Evaluate a single rule and return an event if triggered.

        In production this would query the database for current metrics.
        For now, returns None (rules are evaluated by the caller providing
        metric data). This method exists as the extension point for
        wiring real queries.
        """
        # Subclasses or future wiring will override this. The checker
        # loop calls this for each enabled rule; a None return means
        # the rule condition is not met.
        return None

    def process_event(
        self,
        rule: AlertRule,
        event: AlertEvent,
    ) -> AlertEvent | None:
        """Apply deduplication logic and return the event if it should fire.

        Returns None if the rule is within its cooldown window.
        """
        if not self.should_fire(rule.id):
            logger.debug(
                "Skipping alert for rule %s (cooldown active)",
                rule.id,
            )
            return None

        self.record_fired(rule.id)
        return event

    async def run(self, rules_provider: list[AlertRule] | None = None) -> None:
        """Main loop: evaluate rules on the configured interval.

        Accepts an optional static list of rules for testing. In production,
        rules would be fetched from the database each iteration.
        """
        logger.info(
            "AlertChecker started (interval=%ds, cooldown=%ds)",
            self._check_interval_s,
            self._cooldown_s,
        )

        while not self._shutdown_event.is_set():
            try:
                rules = rules_provider or []
                self._prune_fired()
                for rule in rules:
                    if not rule.enabled:
                        continue
                    event = await self.evaluate_rule(rule)
                    if event is not None:
                        fired = self.process_event(rule, event)
                        if fired is not None:
                            try:
                                await dispatch_alert(rule, fired)
                            except Exception:
                                logger.exception("Failed to dispatch alert for rule %s", rule.id)
            except Exception:
                logger.exception("AlertChecker evaluation error")

            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=self._check_interval_s,
                )
            except asyncio.TimeoutError:
                pass

        logger.info("AlertChecker shut down")

    def create_event(
        self,
        rule: AlertRule,
        message: str,
        severity: AlertSeverity,
    ) -> AlertEvent:
        """Helper to build an AlertEvent from a rule evaluation."""
        return AlertEvent(
            rule_id=rule.id,
            triggered_at=utcnow(),
            message=message,
            severity=severity,
        )
