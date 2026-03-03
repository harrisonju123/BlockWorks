"""Trust score computation logic.

Pure functions that compute individual dimension scores and the weighted
composite. No state — the registry handles storage and history.
"""

from __future__ import annotations

from agentproof.trust.types import TrustScore, TrustWeights
from agentproof.utils import utcnow


def compute_composite(
    reliability: float,
    efficiency: float,
    quality: float,
    usage_volume: float,
    weights: TrustWeights,
) -> float:
    """Weighted sum of trust dimensions, clamped to [0, 1]."""
    raw = (
        reliability * weights.reliability_weight
        + efficiency * weights.efficiency_weight
        + quality * weights.quality_weight
        + usage_volume * weights.usage_weight
    )
    return max(0.0, min(1.0, raw))


class TrustCalculator:
    """Stateless calculator for trust score dimensions."""

    def __init__(self, weights: TrustWeights | None = None) -> None:
        self._weights = weights or TrustWeights()

    @property
    def weights(self) -> TrustWeights:
        return self._weights

    def compute_score(
        self,
        agent_id: str,
        reliability: float,
        efficiency: float,
        quality: float,
        usage_volume: float,
    ) -> TrustScore:
        """Build a complete TrustScore from raw dimension values."""
        composite = compute_composite(
            reliability, efficiency, quality, usage_volume, self._weights
        )
        return TrustScore(
            agent_id=agent_id,
            reliability=reliability,
            efficiency=efficiency,
            quality=quality,
            usage_volume=usage_volume,
            composite_score=composite,
            last_updated=utcnow(),
        )

    def update_reliability(
        self,
        uptime_pct: float,
        error_rate: float,
    ) -> float:
        """Compute reliability score from uptime and error rate.

        uptime_pct in [0, 1], error_rate in [0, 1].
        Reliability = uptime * (1 - error_rate), giving more weight to
        actually completing requests correctly.
        """
        uptime_pct = max(0.0, min(1.0, uptime_pct))
        error_rate = max(0.0, min(1.0, error_rate))
        return uptime_pct * (1.0 - error_rate)

    def update_efficiency(
        self,
        cost_per_outcome: float,
        benchmark_cost: float,
    ) -> float:
        """Compute efficiency score from cost relative to benchmark.

        Returns 1.0 when cost equals benchmark, approaches 0.0 as cost
        grows unbounded, and > 1.0 is clamped (cheaper than benchmark
        is still perfect efficiency).
        """
        if benchmark_cost <= 0:
            return 0.5  # No benchmark data — neutral
        ratio = benchmark_cost / max(cost_per_outcome, 1e-9)
        return max(0.0, min(1.0, ratio))

    def update_quality(
        self,
        eval_scores: list[float],
    ) -> float:
        """Compute quality score from a list of evaluation scores.

        Simple average of recent eval scores, each in [0, 1]. Returns
        0.5 (neutral) if no scores provided.
        """
        if not eval_scores:
            return 0.5
        avg = sum(eval_scores) / len(eval_scores)
        return max(0.0, min(1.0, avg))

    def update_usage(
        self,
        call_count: int,
        total_agents_count: int,
    ) -> float:
        """Compute normalized usage score.

        Measures relative usage volume. An agent with average usage
        across the population gets 0.5. The formula caps at 1.0 to
        prevent a single dominant agent from distorting scores.
        """
        if total_agents_count <= 0 or call_count <= 0:
            return 0.0
        # Normalize: if each agent had equal share, they'd each have 1/N of calls
        # Ratio > 1 means above average usage
        avg_calls_per_agent = 1.0  # normalized baseline
        ratio = call_count / max(total_agents_count, 1)
        # Sigmoid-like normalization: maps ratio to [0, 1]
        # At ratio=1 (average), score ~= 0.5
        score = ratio / (ratio + 1.0)
        return max(0.0, min(1.0, score))
