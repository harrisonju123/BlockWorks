"""EMA-based feedback adjustment for the fitness matrix.

Feedback is the third merge stage: synthetic -> benchmark -> feedback.
Adjustments are clamped and floored to prevent feedback from degrading
quality below the synthetic baseline.
"""

from __future__ import annotations

from blockthrough.benchmarking.types import FitnessEntry


def compute_feedback_adjustments(
    feedback_rows: list[dict],
    *,
    alpha: float = 0.05,
    min_samples: int = 20,
    max_adjustment: float = 0.15,
    ema_state: dict[tuple[str, str], float] | None = None,
) -> dict[tuple[str, str], float]:
    """Compute per-(model, task_type) quality adjustments from feedback.

    Each feedback_row must have: model, task_type, avg_delta (weighted avg
    of quality_delta), sample_count.

    Returns dict mapping (model, task_type) -> adjustment value.
    The ema_state dict is updated in-place if provided.
    """
    if ema_state is None:
        ema_state = {}

    adjustments: dict[tuple[str, str], float] = {}

    for row in feedback_rows:
        model = row["model"]
        task_type = row["task_type"]
        avg_delta = row["avg_delta"]
        sample_count = row["sample_count"]

        if sample_count < min_samples:
            continue

        key = (model, task_type)

        # EMA: new_value = alpha * observation + (1 - alpha) * previous
        prev = ema_state.get(key, 0.0)
        smoothed = alpha * avg_delta + (1 - alpha) * prev
        ema_state[key] = smoothed

        # Clamp to [-max_adjustment, +max_adjustment]
        clamped = max(-max_adjustment, min(max_adjustment, smoothed))
        adjustments[key] = clamped

    return adjustments


def apply_feedback_adjustments(
    entries: list[FitnessEntry],
    adjustments: dict[tuple[str, str], float],
    synthetic: list[FitnessEntry],
) -> list[FitnessEntry]:
    """Apply feedback adjustments to fitness entries with a synthetic floor.

    Adjusted quality never drops below the synthetic baseline for that
    (model, task_type) pair -- prevents feedback spirals from tanking a model.
    """
    # Build synthetic floor lookup
    synthetic_floor: dict[tuple[str, str], float] = {
        (e.model, e.task_type): e.avg_quality for e in synthetic
    }

    result: list[FitnessEntry] = []
    for entry in entries:
        key = (entry.model, entry.task_type)
        adj = adjustments.get(key)
        if adj is None:
            result.append(entry)
            continue

        floor = synthetic_floor.get(key, 0.0)
        adjusted_quality = max(entry.avg_quality + adj, floor)
        # Cap at 1.0
        adjusted_quality = min(adjusted_quality, 1.0)

        result.append(FitnessEntry(
            task_type=entry.task_type,
            model=entry.model,
            avg_quality=adjusted_quality,
            avg_cost=entry.avg_cost,
            avg_latency=entry.avg_latency,
            sample_size=entry.sample_size,
        ))

    return result
