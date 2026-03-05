"""Context bloat analyzer (1B-3).

Flags events where a large system prompt produces a disproportionately
small completion. High system_prompt-to-completion ratios suggest the
prompt could be trimmed without affecting output quality.
"""

from __future__ import annotations

from blockthrough.models import MODEL_CATALOG
from blockthrough.waste.types import WasteCategory, WasteItem, WasteSeverity

# Thresholds: flag when system prompt exceeds this token count
# AND the completion is below the completion threshold
_SYSTEM_PROMPT_MIN_TOKENS = 2000
_COMPLETION_MAX_TOKENS = 100

# Assume trimming can cut system prompt by this fraction
_TRIM_FACTOR = 0.5


def detect_context_bloat(
    events: list[dict],
    *,
    system_prompt_min: int = _SYSTEM_PROMPT_MIN_TOKENS,
    completion_max: int = _COMPLETION_MAX_TOKENS,
    trim_factor: float = _TRIM_FACTOR,
) -> list[WasteItem]:
    """Flag events with bloated system prompts relative to output.

    Args:
        events: Raw event rows with keys: trace_id, model, prompt_tokens,
            completion_tokens, system_prompt_tokens, estimated_cost.
        system_prompt_min: Minimum system prompt tokens to trigger.
        completion_max: Maximum completion tokens — above this, the ratio
            is considered acceptable.
        trim_factor: Fraction of system prompt tokens we estimate can be trimmed.

    Returns:
        WasteItems grouped by trace.
    """
    if not events:
        return []

    # Group flagged events by trace_id for aggregation
    trace_groups: dict[str, list[dict]] = {}

    for event in events:
        sys_tokens = int(event.get("system_prompt_tokens") or 0)
        comp_tokens = int(event.get("completion_tokens") or 0)

        if sys_tokens < system_prompt_min:
            continue
        if comp_tokens > completion_max:
            continue

        trace_id = event.get("trace_id", "unknown")
        trace_groups.setdefault(trace_id, []).append(event)

    items: list[WasteItem] = []

    for trace_id, trace_events in trace_groups.items():
        total_cost = sum(float(e.get("estimated_cost") or 0) for e in trace_events)
        total_sys_tokens = sum(int(e.get("system_prompt_tokens") or 0) for e in trace_events)

        # Estimate savings from trimming the system prompt
        # Savings = (trimmed tokens / total prompt tokens) * cost
        total_prompt_tokens = sum(int(e.get("prompt_tokens") or 0) for e in trace_events)
        if total_prompt_tokens == 0:
            continue

        trimmed_tokens = total_sys_tokens * trim_factor
        savings_fraction = trimmed_tokens / total_prompt_tokens
        # Only the input cost is affected by prompt trimming
        savings = _estimate_input_savings(trace_events, savings_fraction)

        if savings <= 0:
            continue

        severity = _classify_severity(total_sys_tokens, len(trace_events))

        items.append(
            WasteItem(
                category=WasteCategory.CONTEXT_BLOAT,
                severity=severity,
                affected_trace_ids=[trace_id],
                call_count=len(trace_events),
                current_cost=round(total_cost, 6),
                projected_cost=round(total_cost - savings, 6),
                savings=round(savings, 6),
                description=(
                    f"{len(trace_events)} calls with system prompt >{system_prompt_min} tokens "
                    f"but completion <{completion_max} tokens — "
                    f"avg {total_sys_tokens // max(len(trace_events), 1)} sys tokens/call"
                ),
                confidence=0.7,
            )
        )

    return sorted(items, key=lambda i: i.savings, reverse=True)


def _estimate_input_savings(events: list[dict], savings_fraction: float) -> float:
    """Estimate dollar savings from reducing input tokens by savings_fraction.

    Uses MODEL_CATALOG for per-model input pricing. Falls back to
    a conservative estimate for unknown models.
    """
    total_savings = 0.0
    for event in events:
        model = event.get("model", "")
        prompt_tokens = int(event.get("prompt_tokens") or 0)
        cost_info = MODEL_CATALOG.get(model)

        if cost_info and prompt_tokens > 0:
            # cost_per_1k_input is the rate; savings = trimmed tokens * rate
            trimmed_tokens = prompt_tokens * savings_fraction
            total_savings += (trimmed_tokens / 1000) * cost_info.cost_per_1k_input
        else:
            # Conservative fallback: assume input is half the total cost
            total_cost = float(event.get("estimated_cost") or 0)
            total_savings += total_cost * savings_fraction * 0.5

    return total_savings


def _classify_severity(total_sys_tokens: int, event_count: int) -> WasteSeverity:
    if total_sys_tokens > 10_000 and event_count >= 10:
        return WasteSeverity.CRITICAL
    if total_sys_tokens > 5_000 or event_count >= 5:
        return WasteSeverity.WARNING
    return WasteSeverity.INFO
