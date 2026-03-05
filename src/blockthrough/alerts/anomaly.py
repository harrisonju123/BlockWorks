"""Spend anomaly detection using Z-score against a rolling baseline.

Queries the daily_summary continuous aggregate for the 7-day baseline,
then scores the current day against it. Also detects model switches and
failure rate spikes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from blockthrough.alerts.types import AlertSeverity


@dataclass(frozen=True)
class AnomalyResult:
    """Outcome of an anomaly check."""

    is_anomaly: bool
    z_score: float
    severity: AlertSeverity
    message: str


def compute_z_score(current: float, mean: float, stddev: float) -> float:
    """Z-score of a value against a baseline distribution.

    Returns 0.0 when the baseline has zero variance to avoid
    false positives on perfectly-flat spend history.
    """
    if stddev == 0.0 or math.isnan(stddev):
        return 0.0
    return (current - mean) / stddev


def classify_spend_anomaly(
    current_day_spend: float,
    baseline_mean: float,
    baseline_stddev: float,
    *,
    warning_z: float = 2.0,
    critical_z: float = 3.0,
) -> AnomalyResult:
    """Score today's spend against a rolling baseline.

    Thresholds are calibrated per ADR-003 section 5e:
    Z > 2.0 -> warning, Z > 3.0 -> critical.
    """
    z = compute_z_score(current_day_spend, baseline_mean, baseline_stddev)

    if z > critical_z:
        return AnomalyResult(
            is_anomaly=True,
            z_score=z,
            severity=AlertSeverity.CRITICAL,
            message=(
                f"Spend anomaly (critical): ${current_day_spend:.2f} today vs "
                f"${baseline_mean:.2f} avg (z={z:.2f})"
            ),
        )
    if z > warning_z:
        return AnomalyResult(
            is_anomaly=True,
            z_score=z,
            severity=AlertSeverity.WARNING,
            message=(
                f"Spend anomaly (warning): ${current_day_spend:.2f} today vs "
                f"${baseline_mean:.2f} avg (z={z:.2f})"
            ),
        )

    return AnomalyResult(
        is_anomaly=False,
        z_score=z,
        severity=AlertSeverity.INFO,
        message="Spend within normal range",
    )


def detect_model_switch(
    current_models: set[str],
    baseline_models: set[str],
) -> AnomalyResult | None:
    """Flag when a model appears that wasn't used in the baseline window.

    Catches accidental deployments and agent framework misconfigurations
    that silently switch to an expensive provider.
    """
    new_models = current_models - baseline_models
    if not new_models:
        return None

    model_list = ", ".join(sorted(new_models))
    return AnomalyResult(
        is_anomaly=True,
        z_score=0.0,
        severity=AlertSeverity.WARNING,
        message=f"New model(s) detected not seen in baseline: {model_list}",
    )


def detect_failure_rate_spike(
    current_failure_rate: float,
    baseline_failure_rate: float,
    *,
    spike_threshold: float = 2.0,
    min_failure_rate: float = 0.05,
) -> AnomalyResult | None:
    """Flag when failure rate spikes relative to the baseline.

    Only fires if the current rate exceeds an absolute floor (min_failure_rate)
    to avoid noise from orgs with very low request volume.
    """
    if current_failure_rate < min_failure_rate:
        return None

    if baseline_failure_rate <= 0:
        # No baseline failures; any meaningful failure rate is a spike
        if current_failure_rate >= min_failure_rate:
            return AnomalyResult(
                is_anomaly=True,
                z_score=0.0,
                severity=AlertSeverity.WARNING,
                message=(
                    f"Failure rate spike: {current_failure_rate:.1%} "
                    f"(baseline had no failures)"
                ),
            )
        return None

    ratio = current_failure_rate / baseline_failure_rate
    if ratio >= spike_threshold:
        severity = AlertSeverity.CRITICAL if ratio >= 3.0 else AlertSeverity.WARNING
        return AnomalyResult(
            is_anomaly=True,
            z_score=0.0,
            severity=severity,
            message=(
                f"Failure rate spike: {current_failure_rate:.1%} vs "
                f"{baseline_failure_rate:.1%} baseline ({ratio:.1f}x)"
            ),
        )

    return None
