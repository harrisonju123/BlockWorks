"""Tests for trust score type models — validation and defaults."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentproof.trust.types import (
    ScoreUpdate,
    TrustDimension,
    TrustScore,
    TrustWeights,
)


class TestTrustDimension:

    def test_all_dimensions_exist(self) -> None:
        expected = {"reliability", "efficiency", "quality", "usage"}
        actual = {d.value for d in TrustDimension}
        assert actual == expected


class TestTrustWeights:

    def test_defaults(self) -> None:
        w = TrustWeights()
        assert w.reliability_weight == 0.30
        assert w.efficiency_weight == 0.25
        assert w.quality_weight == 0.30
        assert w.usage_weight == 0.15

    def test_default_weights_sum_to_one(self) -> None:
        w = TrustWeights()
        total = (
            w.reliability_weight
            + w.efficiency_weight
            + w.quality_weight
            + w.usage_weight
        )
        assert abs(total - 1.0) < 0.001

    def test_negative_weight_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TrustWeights(reliability_weight=-0.1)

    def test_weight_above_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TrustWeights(reliability_weight=1.1)


class TestTrustScore:

    def test_score_out_of_range_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TrustScore(
                agent_id="agent-1",
                reliability=1.5,
                efficiency=0.5,
                quality=0.5,
                usage_volume=0.5,
                composite_score=0.5,
                last_updated="2026-01-01T00:00:00Z",
            )

    def test_negative_score_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TrustScore(
                agent_id="agent-1",
                reliability=-0.1,
                efficiency=0.5,
                quality=0.5,
                usage_volume=0.5,
                composite_score=0.5,
                last_updated="2026-01-01T00:00:00Z",
            )
