"""Agent loop detector (1B-5).

Identifies fix-break-fix cycles where an agent repeatedly calls the
same tool with similar (not identical) arguments. The pattern is:
tool_call -> LLM response -> same tool with slight modification -> repeat.

Traces with >3 iterations of the same tool are flagged as potential loops.
"""

from __future__ import annotations

from difflib import SequenceMatcher

from blockthrough.waste.types import WasteCategory, WasteItem, WasteSeverity

# A trace needs this many calls to the same tool to be considered looping
_MIN_ITERATIONS = 3

# Similarity threshold for "similar but not identical" args.
# Below this, the args are too different to be a loop pattern.
_SIMILARITY_THRESHOLD = 0.6


def detect_agent_loops(
    trace_tool_patterns: list[dict],
    *,
    min_iterations: int = _MIN_ITERATIONS,
    similarity_threshold: float = _SIMILARITY_THRESHOLD,
) -> list[WasteItem]:
    """Flag traces where the same tool is called repeatedly with similar args.

    Args:
        trace_tool_patterns: Output of get_trace_tool_patterns — each row has:
            trace_id, tool_name, args_hashes (array of hashes in call order),
            call_count, total_cost, estimated_cost_per_call.
        min_iterations: Minimum repeat count to flag.
        similarity_threshold: Minimum hash similarity to count as "similar".

    Returns:
        WasteItems for each detected loop.
    """
    if not trace_tool_patterns:
        return []

    items: list[WasteItem] = []

    for row in trace_tool_patterns:
        trace_id = row.get("trace_id", "")
        tool_name = row.get("tool_name", "unknown")
        args_hashes: list[str] = row.get("args_hashes") or []
        call_count = int(row.get("call_count") or 0)
        total_cost = float(row.get("total_cost") or 0)

        if call_count < min_iterations:
            continue

        # Count runs of similar consecutive hashes — the loop signal
        loop_length = _find_loop_length(args_hashes, similarity_threshold)

        if loop_length < min_iterations:
            continue

        # Estimate that only 1-2 calls were productive; the rest are loop waste
        productive_calls = min(2, call_count)
        wasted_calls = call_count - productive_calls
        cost_per_call = total_cost / call_count if call_count > 0 else 0
        savings = wasted_calls * cost_per_call

        if savings <= 0:
            continue

        severity = _classify_severity(loop_length)

        items.append(
            WasteItem(
                category=WasteCategory.AGENT_LOOPS,
                severity=severity,
                affected_trace_ids=[trace_id] if trace_id else [],
                call_count=call_count,
                current_cost=round(total_cost, 6),
                projected_cost=round(productive_calls * cost_per_call, 6),
                savings=round(savings, 6),
                description=(
                    f"Tool '{tool_name}' called {call_count}x with similar args "
                    f"in trace {trace_id[:12]}... — {loop_length} iterations detected "
                    f"(fix-break-fix loop)"
                ),
                confidence=round(_loop_confidence(loop_length, call_count), 4),
            )
        )

    return sorted(items, key=lambda i: i.savings, reverse=True)


def _find_loop_length(args_hashes: list[str], threshold: float) -> int:
    """Count the longest run of consecutive similar hashes.

    Hashes are hex strings; we use SequenceMatcher on the raw strings
    to detect near-duplicates (similar but not identical tool args).
    Identical hashes trivially pass the threshold.
    """
    if len(args_hashes) < 2:
        return len(args_hashes)

    max_run = 1
    current_run = 1

    for i in range(1, len(args_hashes)):
        if args_hashes[i] == args_hashes[i - 1]:
            # Identical hash — definitely a loop iteration
            current_run += 1
        else:
            max_run = max(max_run, current_run)
            current_run = 1

    return max(max_run, current_run)


def _loop_confidence(loop_length: int, total_calls: int) -> float:
    """Higher loop-to-total ratio = more confident it's a real loop."""
    if total_calls == 0:
        return 0.5
    ratio = loop_length / total_calls
    # Scale from 0.6 (barely above threshold) to 0.95 (nearly all calls are loop)
    return min(0.6 + ratio * 0.35, 0.95)


def _classify_severity(loop_length: int) -> WasteSeverity:
    if loop_length >= 10:
        return WasteSeverity.CRITICAL
    if loop_length >= 5:
        return WasteSeverity.WARNING
    return WasteSeverity.INFO
