"""Redundant call detector (1B-2).

Identifies duplicate tool calls within a trace — same tool invoked
with identical arguments. These usually indicate retries that should
have been cached or agent confusion.
"""

from __future__ import annotations

from agentproof.waste.types import WasteCategory, WasteItem, WasteSeverity


def detect_redundant_calls(
    duplicate_rows: list[dict],
) -> list[WasteItem]:
    """Flag traces with duplicate tool call hashes.

    Args:
        duplicate_rows: Output of get_duplicate_tool_calls — each row has:
            trace_id, tool_name, args_hash, dup_count, estimated_cost_per_call.

    Returns:
        WasteItems for each redundancy pattern found.
    """
    if not duplicate_rows:
        return []

    items: list[WasteItem] = []

    for row in duplicate_rows:
        trace_id = row.get("trace_id", "")
        tool_name = row.get("tool_name", "unknown")
        dup_count = int(row.get("dup_count") or 0)
        cost_per_call = float(row.get("estimated_cost_per_call") or 0)

        if dup_count < 2:
            continue

        # The first call is necessary; the rest are waste
        wasted_calls = dup_count - 1
        savings = wasted_calls * cost_per_call

        severity = _classify_severity(dup_count)

        items.append(
            WasteItem(
                category=WasteCategory.REDUNDANT_CALLS,
                severity=severity,
                affected_trace_ids=[trace_id] if trace_id else [],
                call_count=dup_count,
                current_cost=round(dup_count * cost_per_call, 6),
                projected_cost=round(cost_per_call, 6),
                savings=round(savings, 6),
                description=(
                    f"Tool '{tool_name}' called {dup_count}x with identical args "
                    f"in trace {trace_id[:12]}... — {wasted_calls} redundant"
                ),
                confidence=0.95,
            )
        )

    return sorted(items, key=lambda i: i.savings, reverse=True)


def _classify_severity(dup_count: int) -> WasteSeverity:
    if dup_count >= 10:
        return WasteSeverity.CRITICAL
    if dup_count >= 5:
        return WasteSeverity.WARNING
    return WasteSeverity.INFO
