"""Cache miss detector (1B-4).

Finds events with identical prompt_hash within a configurable time
window. These represent missed caching opportunities — the same
prompt was sent multiple times when caching could have avoided
repeat processing.
"""

from __future__ import annotations

from agentproof.waste.types import WasteCategory, WasteItem, WasteSeverity


def detect_cache_misses(
    duplicate_prompt_rows: list[dict],
) -> list[WasteItem]:
    """Flag repeated identical prompts that could have been cached.

    Args:
        duplicate_prompt_rows: Output of get_prompt_hash_duplicates — each row has:
            prompt_hash, dup_count, total_cost, models (array),
            first_seen, last_seen, trace_ids (array).

    Returns:
        WasteItems for each cache miss pattern.
    """
    if not duplicate_prompt_rows:
        return []

    items: list[WasteItem] = []

    for row in duplicate_prompt_rows:
        dup_count = int(row.get("dup_count") or 0)
        total_cost = float(row.get("total_cost") or 0)
        trace_ids = row.get("trace_ids") or []
        prompt_hash = row.get("prompt_hash", "")[:12]

        if dup_count < 2:
            continue

        # First call is necessary, the rest could have been cached.
        # Cached calls typically cost ~90% less (only cache read cost).
        wasted_calls = dup_count - 1
        cost_per_call = total_cost / dup_count if dup_count > 0 else 0
        # Anthropic prompt caching gives ~90% discount on cached tokens
        cached_cost = cost_per_call * 0.1
        savings = wasted_calls * (cost_per_call - cached_cost)

        if savings <= 0:
            continue

        severity = _classify_severity(dup_count, savings)

        # Limit trace_ids to avoid giant payloads
        affected_traces = trace_ids[:20] if isinstance(trace_ids, list) else []

        items.append(
            WasteItem(
                category=WasteCategory.CACHE_MISSES,
                severity=severity,
                affected_trace_ids=affected_traces,
                call_count=dup_count,
                current_cost=round(total_cost, 6),
                projected_cost=round(cost_per_call + (wasted_calls * cached_cost), 6),
                savings=round(savings, 6),
                description=(
                    f"Prompt {prompt_hash}... sent {dup_count}x within time window — "
                    f"{wasted_calls} calls could use prompt caching"
                ),
                confidence=0.9,
            )
        )

    return sorted(items, key=lambda i: i.savings, reverse=True)


def _classify_severity(dup_count: int, savings: float) -> WasteSeverity:
    if savings >= 100 or dup_count >= 50:
        return WasteSeverity.CRITICAL
    if savings >= 10 or dup_count >= 10:
        return WasteSeverity.WARNING
    return WasteSeverity.INFO
