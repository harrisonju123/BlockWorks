"""Tests for workflow payment splitting and settlement.

Validates proportional cost distribution, minimum payment floor,
and settlement through state channels.
"""

from __future__ import annotations

import pytest

from blockthrough.channels.manager import ChannelManager
from blockthrough.channels.types import ChannelConfig
from blockthrough.workflows.payments import (
    MINIMUM_PAYMENT,
    calculate_splits,
    settle_workflow,
)
from blockthrough.workflows.types import (
    StepResult,
    WorkflowExecution,
    WorkflowExecutionStatus,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _execution(workflow_id: str = "wf-1") -> WorkflowExecution:
    return WorkflowExecution(
        id="exec-1",
        workflow_id=workflow_id,
        status=WorkflowExecutionStatus.COMPLETED,
    )


def _result(step_id: str, cost: float) -> StepResult:
    return StepResult(
        step_id=step_id,
        status=WorkflowExecutionStatus.COMPLETED,
        cost=cost,
    )


def _listing_map(*pairs: tuple[str, str]) -> dict[str, str]:
    return {step_id: listing_id for step_id, listing_id in pairs}


# ---------------------------------------------------------------------------
# calculate_splits
# ---------------------------------------------------------------------------


class TestCalculateSplits:

    def test_equal_cost_steps(self) -> None:
        results = [_result("a", 0.01), _result("b", 0.01)]
        listing_map = _listing_map(("a", "l1"), ("b", "l2"))

        splits = calculate_splits(_execution(), results, listing_map)
        assert len(splits) == 2
        for split in splits:
            assert split.percentage_of_total == pytest.approx(50.0)
            assert split.amount == pytest.approx(0.01)

    def test_unequal_cost_proportional(self) -> None:
        results = [_result("a", 0.03), _result("b", 0.01)]
        listing_map = _listing_map(("a", "l1"), ("b", "l2"))

        splits = calculate_splits(_execution(), results, listing_map)
        assert len(splits) == 2

        split_a = next(s for s in splits if s.step_id == "a")
        split_b = next(s for s in splits if s.step_id == "b")

        assert split_a.percentage_of_total == pytest.approx(75.0)
        assert split_b.percentage_of_total == pytest.approx(25.0)
        assert split_a.amount == pytest.approx(0.03)
        assert split_b.amount == pytest.approx(0.01)

    def test_single_step(self) -> None:
        results = [_result("a", 0.05)]
        listing_map = _listing_map(("a", "l1"))

        splits = calculate_splits(_execution(), results, listing_map)
        assert len(splits) == 1
        assert splits[0].percentage_of_total == pytest.approx(100.0)
        assert splits[0].amount == pytest.approx(0.05)

    def test_zero_total_cost_returns_empty(self) -> None:
        results = [_result("a", 0.0), _result("b", 0.0)]
        listing_map = _listing_map(("a", "l1"), ("b", "l2"))

        splits = calculate_splits(_execution(), results, listing_map)
        assert splits == []

    def test_below_minimum_floor_excluded(self) -> None:
        """Steps costing less than MINIMUM_PAYMENT are excluded."""
        results = [
            _result("a", 0.05),
            _result("b", 0.0001),  # below floor
        ]
        listing_map = _listing_map(("a", "l1"), ("b", "l2"))

        splits = calculate_splits(_execution(), results, listing_map)
        # Only step A should get a split — step B is dust
        assert len(splits) == 1
        assert splits[0].step_id == "a"

    def test_all_below_minimum_returns_empty(self) -> None:
        results = [
            _result("a", 0.0001),
            _result("b", 0.0002),
        ]
        listing_map = _listing_map(("a", "l1"), ("b", "l2"))

        splits = calculate_splits(_execution(), results, listing_map)
        assert splits == []

    def test_missing_listing_id_skipped(self) -> None:
        results = [_result("a", 0.01), _result("b", 0.01)]
        # Only step A has a listing mapping
        listing_map = _listing_map(("a", "l1"))

        splits = calculate_splits(_execution(), results, listing_map)
        assert len(splits) == 1
        assert splits[0].step_id == "a"

    def test_three_steps_proportional(self) -> None:
        results = [
            _result("a", 0.02),
            _result("b", 0.03),
            _result("c", 0.05),
        ]
        listing_map = _listing_map(("a", "l1"), ("b", "l2"), ("c", "l3"))

        splits = calculate_splits(_execution(), results, listing_map)
        assert len(splits) == 3

        total_amount = sum(s.amount for s in splits)
        assert total_amount == pytest.approx(0.10)

        total_pct = sum(s.percentage_of_total for s in splits)
        assert total_pct == pytest.approx(100.0)

    def test_amounts_are_rounded(self) -> None:
        """Amounts should be rounded to 6 decimal places."""
        results = [_result("a", 0.0033333), _result("b", 0.0066667)]
        listing_map = _listing_map(("a", "l1"), ("b", "l2"))

        splits = calculate_splits(_execution(), results, listing_map)
        for split in splits:
            # Check that amount has at most 6 decimal places
            decimal_str = f"{split.amount:.10f}".rstrip("0")
            decimal_places = len(decimal_str.split(".")[1]) if "." in decimal_str else 0
            assert decimal_places <= 6


# ---------------------------------------------------------------------------
# settle_workflow
# ---------------------------------------------------------------------------


class TestSettleWorkflow:

    @pytest.mark.asyncio
    async def test_settle_creates_channels(self) -> None:
        from blockthrough.workflows.types import PaymentSplit

        splits = [
            PaymentSplit(step_id="a", listing_id="l1", amount=0.01, percentage_of_total=50.0),
            PaymentSplit(step_id="b", listing_id="l2", amount=0.01, percentage_of_total=50.0),
        ]

        mgr = ChannelManager(config=ChannelConfig(min_deposit=0.001))
        channel_ids = await settle_workflow(splits, mgr, sender="workflow-owner")

        assert len(channel_ids) == 2
        # All channels should be closed after settlement
        for cid in channel_ids:
            ch = mgr.get_channel(cid)
            assert ch.is_open is False

    @pytest.mark.asyncio
    async def test_settle_skips_dust_payments(self) -> None:
        from blockthrough.workflows.types import PaymentSplit

        splits = [
            PaymentSplit(step_id="a", listing_id="l1", amount=0.0001, percentage_of_total=100.0),
        ]

        mgr = ChannelManager(config=ChannelConfig(min_deposit=0.001))
        channel_ids = await settle_workflow(splits, mgr, sender="owner")
        assert channel_ids == []

    @pytest.mark.asyncio
    async def test_settle_empty_splits(self) -> None:
        mgr = ChannelManager(config=ChannelConfig(min_deposit=0.001))
        channel_ids = await settle_workflow([], mgr, sender="owner")
        assert channel_ids == []

    @pytest.mark.asyncio
    async def test_settle_payment_amounts_correct(self) -> None:
        from blockthrough.workflows.types import PaymentSplit

        splits = [
            PaymentSplit(step_id="a", listing_id="l1", amount=0.05, percentage_of_total=100.0),
        ]

        mgr = ChannelManager(config=ChannelConfig(min_deposit=0.001))
        channel_ids = await settle_workflow(splits, mgr, sender="owner")

        ch = mgr.get_channel(channel_ids[0])
        assert ch.spent_amount == pytest.approx(0.05)
