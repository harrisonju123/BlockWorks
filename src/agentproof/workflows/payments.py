"""Payment splitting for multi-step workflow executions.

Distributes the total workflow cost proportionally across steps,
then settles each split through state channels. Enforces a
minimum payment floor to avoid dust payments.
"""

from __future__ import annotations

from agentproof.channels.manager import ChannelManager
from agentproof.workflows.types import (
    PaymentSplit,
    StepResult,
    WorkflowExecution,
)


# Below this threshold we skip the payment entirely — not worth
# the overhead of a channel update for sub-tenth-of-a-cent amounts
MINIMUM_PAYMENT = 0.001


def calculate_splits(
    execution: WorkflowExecution,
    step_results: list[StepResult],
    step_listing_map: dict[str, str],
) -> list[PaymentSplit]:
    """Compute proportional payment splits from step costs.

    Each step's share of the total cost determines its percentage.
    Steps below MINIMUM_PAYMENT are excluded and their share is
    redistributed to the remaining steps proportionally.

    Args:
        execution: The completed workflow execution.
        step_results: Results for each executed step.
        step_listing_map: Maps step_id -> listing_id for payment routing.
    """
    total_cost = sum(r.cost for r in step_results)
    if total_cost <= 0:
        return []

    # First pass: identify steps above the minimum floor
    eligible: list[tuple[StepResult, str]] = []
    for result in step_results:
        listing_id = step_listing_map.get(result.step_id, "")
        if not listing_id:
            continue
        if result.cost >= MINIMUM_PAYMENT:
            eligible.append((result, listing_id))

    if not eligible:
        return []

    # Recalculate total from eligible steps only — this redistributes
    # the dust amounts proportionally among qualifying steps
    eligible_total = sum(r.cost for r, _ in eligible)
    if eligible_total <= 0:
        return []

    splits: list[PaymentSplit] = []
    for result, listing_id in eligible:
        pct = result.cost / eligible_total
        amount = total_cost * pct

        # Final floor check after redistribution
        if amount < MINIMUM_PAYMENT:
            continue

        splits.append(
            PaymentSplit(
                step_id=result.step_id,
                listing_id=listing_id,
                amount=round(amount, 6),
                percentage_of_total=round(pct * 100, 2),
            )
        )

    return splits


async def settle_workflow(
    splits: list[PaymentSplit],
    channel_manager: ChannelManager,
    sender: str,
) -> list[str]:
    """Settle payment splits through state channels.

    Opens a channel per unique receiver (listing_id), makes a
    payment for the split amount, then closes. Returns the list
    of channel IDs used.

    In production this would reuse existing open channels rather
    than opening/closing per workflow. Good enough for local dev.
    """
    channel_ids: list[str] = []

    for split in splits:
        if split.amount < MINIMUM_PAYMENT:
            continue

        # Deposit enough to cover the payment
        deposit = split.amount * 1.1  # 10% buffer for rounding
        channel = channel_manager.open_channel(
            sender=sender,
            receiver=split.listing_id,
            deposit=max(deposit, channel_manager._config.min_deposit),
        )

        channel_manager.create_payment(channel.channel_id, split.amount)
        channel_manager.close_channel(channel.channel_id)
        channel_ids.append(channel.channel_id)

    return channel_ids
