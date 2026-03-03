"""Revenue split calculation engine.

Given an execution cost and a set of split rules, computes each
participant's share after deducting the protocol fee. Supports
four split basis types that can be mixed within a single execution.

The protocol fee is deducted *first*, then the remainder is
distributed proportionally based on each participant's weight
relative to the total weight across all rules.
"""

from __future__ import annotations

from agentproof.revenue.types import (
    ProtocolFee,
    RevenueShare,
    SplitBasis,
    SplitRule,
)


class SplitCalculationError(Exception):
    """Raised when split rules are invalid or produce degenerate results."""


def calculate_shares(
    execution_id: str,
    execution_cost: float,
    split_rules: list[SplitRule],
    protocol_fee_pct: float = 3.0,
    burn_pct: float = 30.0,
) -> tuple[list[RevenueShare], ProtocolFee]:
    """Calculate revenue shares for a workflow execution.

    Returns a tuple of (shares, protocol_fee). The shares list will have
    one entry per split rule. All amounts are rounded to 8 decimal places
    to avoid floating-point dust accumulation.

    Args:
        execution_id: Unique identifier for this workflow execution.
        execution_cost: Total cost in USD to be distributed.
        split_rules: One rule per participant defining their split basis and weight.
        protocol_fee_pct: Percentage of execution_cost taken as protocol fee (0-100).
        burn_pct: Percentage of the protocol fee that is burned (0-100).

    Raises:
        SplitCalculationError: If rules are empty, weights sum to zero, or
            execution_cost is negative.
    """
    if execution_cost < 0:
        raise SplitCalculationError("execution_cost must be non-negative")

    if not split_rules:
        raise SplitCalculationError("split_rules must not be empty")

    total_weight = sum(r.weight for r in split_rules)
    if total_weight <= 0:
        raise SplitCalculationError(
            "total weight across split rules must be positive"
        )

    # Protocol fee comes off the top
    fee_amount = round(execution_cost * protocol_fee_pct / 100.0, 8)
    burn_amount = round(fee_amount * burn_pct / 100.0, 8)

    distributable = execution_cost - fee_amount

    protocol_fee = ProtocolFee(
        execution_id=execution_id,
        fee_pct=protocol_fee_pct,
        fee_amount=fee_amount,
        burn_amount=burn_amount,
    )

    # Zero cost shortcut — everyone gets 0
    if execution_cost == 0:
        shares = [
            RevenueShare(
                workflow_execution_id=execution_id,
                participant_id=rule.participant_id,
                share_pct=round(rule.weight / total_weight * 100.0, 8),
                amount_usd=0.0,
                settled=False,
            )
            for rule in split_rules
        ]
        return shares, protocol_fee

    shares: list[RevenueShare] = []
    for rule in split_rules:
        share_pct = round(rule.weight / total_weight * 100.0, 8)
        amount = round(distributable * rule.weight / total_weight, 8)

        shares.append(
            RevenueShare(
                workflow_execution_id=execution_id,
                participant_id=rule.participant_id,
                share_pct=share_pct,
                amount_usd=amount,
                settled=False,
            )
        )

    return shares, protocol_fee
