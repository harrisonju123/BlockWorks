"""In-memory trust score registry.

Stores per-agent trust scores with history tracking and decay mechanics.
Production would persist to TimescaleDB; this in-memory implementation
validates the interface.
"""

from __future__ import annotations

from agentproof.trust.calculator import TrustCalculator, compute_composite
from agentproof.utils import utcnow
from agentproof.trust.types import (
    ScoreUpdate,
    TrustDimension,
    TrustScore,
    TrustWeights,
)


class AgentNotRegisteredError(Exception):
    pass


class AgentAlreadyRegisteredError(Exception):
    pass


class TrustRegistry:
    """In-memory agent trust score registry with history and decay."""

    # Neutral starting score for all dimensions
    NEUTRAL_SCORE = 0.5

    def __init__(
        self,
        weights: TrustWeights | None = None,
    ) -> None:
        self._weights = weights or TrustWeights()
        self._calculator = TrustCalculator(self._weights)
        self._scores: dict[str, TrustScore] = {}
        self._history: dict[str, list[ScoreUpdate]] = {}

    @property
    def weights(self) -> TrustWeights:
        return self._weights

    def register_agent(self, agent_id: str) -> TrustScore:
        """Register a new agent with neutral (0.5) scores.

        Raises:
            AgentAlreadyRegisteredError: If agent_id is already registered.
        """
        if agent_id in self._scores:
            raise AgentAlreadyRegisteredError(f"Agent {agent_id} already registered")

        score = self._calculator.compute_score(
            agent_id=agent_id,
            reliability=self.NEUTRAL_SCORE,
            efficiency=self.NEUTRAL_SCORE,
            quality=self.NEUTRAL_SCORE,
            usage_volume=self.NEUTRAL_SCORE,
        )
        self._scores[agent_id] = score
        self._history[agent_id] = []
        return score

    def update_score(
        self,
        agent_id: str,
        dimension: TrustDimension,
        value: float,
        reason: str = "",
    ) -> TrustScore:
        """Update a single trust dimension for an agent.

        Recomputes the composite score after the update.

        Raises:
            AgentNotRegisteredError: If agent_id is not registered.
        """
        current = self._scores.get(agent_id)
        if current is None:
            raise AgentNotRegisteredError(f"Agent {agent_id} not registered")

        value = max(0.0, min(1.0, value))
        now = utcnow()

        # Record history before updating
        # TrustDimension.USAGE maps to "usage" but the field is "usage_volume"
        _DIMENSION_FIELD = {
            TrustDimension.RELIABILITY: "reliability",
            TrustDimension.EFFICIENCY: "efficiency",
            TrustDimension.QUALITY: "quality",
            TrustDimension.USAGE: "usage_volume",
        }
        field_name = _DIMENSION_FIELD[dimension]
        old_value = getattr(current, field_name)

        update = ScoreUpdate(
            agent_id=agent_id,
            dimension=dimension,
            old_value=old_value,
            new_value=value,
            reason=reason,
            timestamp=now,
        )
        self._history[agent_id].append(update)

        # Apply the dimension update
        reliability = current.reliability
        efficiency = current.efficiency
        quality = current.quality
        usage_volume = current.usage_volume

        if dimension == TrustDimension.RELIABILITY:
            reliability = value
        elif dimension == TrustDimension.EFFICIENCY:
            efficiency = value
        elif dimension == TrustDimension.QUALITY:
            quality = value
        elif dimension == TrustDimension.USAGE:
            usage_volume = value

        composite = compute_composite(
            reliability, efficiency, quality, usage_volume, self._weights
        )

        updated = TrustScore(
            agent_id=agent_id,
            reliability=reliability,
            efficiency=efficiency,
            quality=quality,
            usage_volume=usage_volume,
            composite_score=composite,
            last_updated=now,
        )
        self._scores[agent_id] = updated
        return updated

    def get_score(self, agent_id: str) -> TrustScore:
        """Get the current trust score for an agent.

        Raises:
            AgentNotRegisteredError: If agent_id is not registered.
        """
        score = self._scores.get(agent_id)
        if score is None:
            raise AgentNotRegisteredError(f"Agent {agent_id} not registered")
        return score

    def get_top_agents(self, limit: int = 10) -> list[TrustScore]:
        """Get the top agents sorted by composite score descending."""
        all_scores = list(self._scores.values())
        all_scores.sort(key=lambda s: s.composite_score, reverse=True)
        return all_scores[:limit]

    def get_history(self, agent_id: str) -> list[ScoreUpdate]:
        """Get score update history for an agent.

        Raises:
            AgentNotRegisteredError: If agent_id is not registered.
        """
        if agent_id not in self._scores:
            raise AgentNotRegisteredError(f"Agent {agent_id} not registered")
        return list(self._history.get(agent_id, []))

    def decay_scores(self, factor: float = 0.95) -> None:
        """Decay all scores toward neutral (0.5).

        Each dimension moves toward 0.5 by the decay factor:
        new_value = neutral + (current - neutral) * factor

        This ensures inactive agents gradually return to neutral rather
        than retaining stale high/low scores indefinitely.
        """
        factor = max(0.0, min(1.0, factor))
        now = utcnow()

        for agent_id, current in self._scores.items():
            reliability = self.NEUTRAL_SCORE + (current.reliability - self.NEUTRAL_SCORE) * factor
            efficiency = self.NEUTRAL_SCORE + (current.efficiency - self.NEUTRAL_SCORE) * factor
            quality = self.NEUTRAL_SCORE + (current.quality - self.NEUTRAL_SCORE) * factor
            usage_volume = self.NEUTRAL_SCORE + (current.usage_volume - self.NEUTRAL_SCORE) * factor

            composite = compute_composite(
                reliability, efficiency, quality, usage_volume, self._weights
            )

            self._scores[agent_id] = TrustScore(
                agent_id=agent_id,
                reliability=reliability,
                efficiency=efficiency,
                quality=quality,
                usage_volume=usage_volume,
                composite_score=composite,
                last_updated=now,
            )

    def agent_count(self) -> int:
        """Total number of registered agents."""
        return len(self._scores)

    def reset(self) -> None:
        """Clear all state. Used by tests."""
        self._scores.clear()
        self._history.clear()
