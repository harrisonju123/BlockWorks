"""Tests for the trust score registry — agent lifecycle, scoring, history, and decay."""

from __future__ import annotations

import pytest

from blockthrough.trust.registry import (
    AgentAlreadyRegisteredError,
    AgentNotRegisteredError,
    TrustRegistry,
)
from blockthrough.trust.types import TrustDimension, TrustWeights


class TestRegisterAgent:

    def test_new_agent_gets_neutral_scores(self) -> None:
        registry = TrustRegistry()
        score = registry.register_agent("agent-1")
        assert score.agent_id == "agent-1"
        assert abs(score.reliability - 0.5) < 0.001
        assert abs(score.efficiency - 0.5) < 0.001
        assert abs(score.quality - 0.5) < 0.001
        assert abs(score.usage_volume - 0.5) < 0.001

    def test_composite_is_neutral(self) -> None:
        registry = TrustRegistry()
        score = registry.register_agent("agent-1")
        assert abs(score.composite_score - 0.5) < 0.01

    def test_duplicate_registration_raises(self) -> None:
        registry = TrustRegistry()
        registry.register_agent("agent-1")
        with pytest.raises(AgentAlreadyRegisteredError):
            registry.register_agent("agent-1")

    def test_agent_count_increments(self) -> None:
        registry = TrustRegistry()
        assert registry.agent_count() == 0
        registry.register_agent("agent-1")
        assert registry.agent_count() == 1
        registry.register_agent("agent-2")
        assert registry.agent_count() == 2


class TestUpdateScore:

    def test_update_reliability(self) -> None:
        registry = TrustRegistry()
        registry.register_agent("agent-1")
        updated = registry.update_score("agent-1", TrustDimension.RELIABILITY, 0.9)
        assert abs(updated.reliability - 0.9) < 0.001

    def test_update_efficiency(self) -> None:
        registry = TrustRegistry()
        registry.register_agent("agent-1")
        updated = registry.update_score("agent-1", TrustDimension.EFFICIENCY, 0.8)
        assert abs(updated.efficiency - 0.8) < 0.001

    def test_update_quality(self) -> None:
        registry = TrustRegistry()
        registry.register_agent("agent-1")
        updated = registry.update_score("agent-1", TrustDimension.QUALITY, 0.95)
        assert abs(updated.quality - 0.95) < 0.001

    def test_update_usage(self) -> None:
        registry = TrustRegistry()
        registry.register_agent("agent-1")
        updated = registry.update_score("agent-1", TrustDimension.USAGE, 0.7)
        assert abs(updated.usage_volume - 0.7) < 0.001

    def test_composite_recomputed_on_update(self) -> None:
        registry = TrustRegistry()
        original = registry.register_agent("agent-1")
        updated = registry.update_score("agent-1", TrustDimension.RELIABILITY, 1.0)
        # Reliability went from 0.5 to 1.0, so composite should increase
        assert updated.composite_score > original.composite_score

    def test_unregistered_agent_raises(self) -> None:
        registry = TrustRegistry()
        with pytest.raises(AgentNotRegisteredError):
            registry.update_score("ghost", TrustDimension.RELIABILITY, 0.9)

    def test_value_clamped_to_unit(self) -> None:
        registry = TrustRegistry()
        registry.register_agent("agent-1")
        updated = registry.update_score("agent-1", TrustDimension.RELIABILITY, 1.5)
        assert updated.reliability <= 1.0

    def test_negative_value_clamped_to_zero(self) -> None:
        registry = TrustRegistry()
        registry.register_agent("agent-1")
        updated = registry.update_score("agent-1", TrustDimension.RELIABILITY, -0.5)
        assert updated.reliability >= 0.0

    def test_other_dimensions_preserved(self) -> None:
        """Updating one dimension should not change the others."""
        registry = TrustRegistry()
        registry.register_agent("agent-1")
        registry.update_score("agent-1", TrustDimension.RELIABILITY, 0.9)
        updated = registry.update_score("agent-1", TrustDimension.QUALITY, 0.8)
        assert abs(updated.reliability - 0.9) < 0.001
        assert abs(updated.quality - 0.8) < 0.001


class TestGetScore:

    def test_get_registered_agent(self) -> None:
        registry = TrustRegistry()
        registry.register_agent("agent-1")
        score = registry.get_score("agent-1")
        assert score.agent_id == "agent-1"

    def test_get_unregistered_agent_raises(self) -> None:
        registry = TrustRegistry()
        with pytest.raises(AgentNotRegisteredError):
            registry.get_score("ghost")


class TestGetTopAgents:

    def test_empty_registry(self) -> None:
        registry = TrustRegistry()
        assert registry.get_top_agents() == []

    def test_sorted_by_composite_descending(self) -> None:
        registry = TrustRegistry()
        registry.register_agent("low")
        registry.register_agent("mid")
        registry.register_agent("high")

        registry.update_score("low", TrustDimension.RELIABILITY, 0.1)
        registry.update_score("mid", TrustDimension.RELIABILITY, 0.5)
        registry.update_score("high", TrustDimension.RELIABILITY, 0.99)

        top = registry.get_top_agents(limit=3)
        assert len(top) == 3
        assert top[0].agent_id == "high"
        assert top[2].agent_id == "low"

    def test_limit_respected(self) -> None:
        registry = TrustRegistry()
        for i in range(5):
            registry.register_agent(f"agent-{i}")

        top = registry.get_top_agents(limit=2)
        assert len(top) == 2

    def test_limit_larger_than_count(self) -> None:
        registry = TrustRegistry()
        registry.register_agent("agent-1")
        top = registry.get_top_agents(limit=100)
        assert len(top) == 1


class TestHistory:

    def test_history_recorded_on_update(self) -> None:
        registry = TrustRegistry()
        registry.register_agent("agent-1")
        registry.update_score("agent-1", TrustDimension.RELIABILITY, 0.9, reason="uptime improved")

        history = registry.get_history("agent-1")
        assert len(history) == 1
        assert history[0].dimension == TrustDimension.RELIABILITY
        assert abs(history[0].old_value - 0.5) < 0.001
        assert abs(history[0].new_value - 0.9) < 0.001
        assert history[0].reason == "uptime improved"

    def test_multiple_updates_tracked(self) -> None:
        registry = TrustRegistry()
        registry.register_agent("agent-1")
        registry.update_score("agent-1", TrustDimension.RELIABILITY, 0.9)
        registry.update_score("agent-1", TrustDimension.QUALITY, 0.8)
        registry.update_score("agent-1", TrustDimension.RELIABILITY, 0.95)

        history = registry.get_history("agent-1")
        assert len(history) == 3

    def test_history_unregistered_agent_raises(self) -> None:
        registry = TrustRegistry()
        with pytest.raises(AgentNotRegisteredError):
            registry.get_history("ghost")

    def test_history_empty_for_new_agent(self) -> None:
        registry = TrustRegistry()
        registry.register_agent("agent-1")
        assert registry.get_history("agent-1") == []


class TestDecayScores:

    def test_high_scores_decay_toward_neutral(self) -> None:
        registry = TrustRegistry()
        registry.register_agent("agent-1")
        registry.update_score("agent-1", TrustDimension.RELIABILITY, 1.0)

        score_before = registry.get_score("agent-1").reliability
        registry.decay_scores(factor=0.9)
        score_after = registry.get_score("agent-1").reliability

        # Should have moved toward 0.5
        assert score_after < score_before
        assert score_after > 0.5

    def test_low_scores_decay_toward_neutral(self) -> None:
        registry = TrustRegistry()
        registry.register_agent("agent-1")
        registry.update_score("agent-1", TrustDimension.RELIABILITY, 0.0)

        registry.decay_scores(factor=0.9)
        score = registry.get_score("agent-1").reliability

        # Should have moved toward 0.5 from below
        assert score > 0.0
        assert score < 0.5

    def test_neutral_scores_unchanged(self) -> None:
        """Scores already at 0.5 should not change after decay."""
        registry = TrustRegistry()
        registry.register_agent("agent-1")
        # Default is 0.5 across all dimensions

        registry.decay_scores(factor=0.95)
        score = registry.get_score("agent-1")
        assert abs(score.reliability - 0.5) < 0.001
        assert abs(score.efficiency - 0.5) < 0.001
        assert abs(score.quality - 0.5) < 0.001
        assert abs(score.usage_volume - 0.5) < 0.001

    def test_repeated_decay_converges(self) -> None:
        registry = TrustRegistry()
        registry.register_agent("agent-1")
        registry.update_score("agent-1", TrustDimension.RELIABILITY, 1.0)

        for _ in range(100):
            registry.decay_scores(factor=0.9)

        score = registry.get_score("agent-1").reliability
        # After 100 iterations of 0.9 decay, should be very close to 0.5
        assert abs(score - 0.5) < 0.01

    def test_composite_recomputed_after_decay(self) -> None:
        registry = TrustRegistry()
        original = registry.register_agent("agent-1")
        registry.update_score("agent-1", TrustDimension.RELIABILITY, 1.0)
        before_decay = registry.get_score("agent-1").composite_score

        registry.decay_scores(factor=0.9)
        after_decay = registry.get_score("agent-1").composite_score

        assert after_decay < before_decay

    def test_decay_factor_zero_resets_to_neutral(self) -> None:
        registry = TrustRegistry()
        registry.register_agent("agent-1")
        registry.update_score("agent-1", TrustDimension.RELIABILITY, 1.0)

        registry.decay_scores(factor=0.0)
        score = registry.get_score("agent-1").reliability
        assert abs(score - 0.5) < 0.001

    def test_decay_factor_one_no_change(self) -> None:
        registry = TrustRegistry()
        registry.register_agent("agent-1")
        registry.update_score("agent-1", TrustDimension.RELIABILITY, 0.9)

        registry.decay_scores(factor=1.0)
        score = registry.get_score("agent-1").reliability
        assert abs(score - 0.9) < 0.001


class TestReset:

    def test_reset_clears_all(self) -> None:
        registry = TrustRegistry()
        registry.register_agent("agent-1")
        registry.update_score("agent-1", TrustDimension.RELIABILITY, 0.9)
        registry.reset()

        assert registry.agent_count() == 0
        with pytest.raises(AgentNotRegisteredError):
            registry.get_score("agent-1")


class TestCustomWeights:

    def test_custom_weights_applied(self) -> None:
        """Reliability-only weight should make composite equal reliability."""
        weights = TrustWeights(
            reliability_weight=1.0,
            efficiency_weight=0.0,
            quality_weight=0.0,
            usage_weight=0.0,
        )
        registry = TrustRegistry(weights=weights)
        registry.register_agent("agent-1")
        registry.update_score("agent-1", TrustDimension.RELIABILITY, 0.9)
        registry.update_score("agent-1", TrustDimension.EFFICIENCY, 0.1)

        score = registry.get_score("agent-1")
        # Composite should be driven entirely by reliability
        assert abs(score.composite_score - 0.9) < 0.001
