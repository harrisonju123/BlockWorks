"""Unified model suggestion engine.

Single source of truth for "given (task_type, model), what's the best
cheaper alternative?" Uses fitness matrix data when available, falls
back to tier-based heuristic rules with capped confidence when it doesn't.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from blockthrough.benchmarking.types import FitnessEntry
from blockthrough.models import MODEL_CATALOG, ModelInfo, get_tier
from blockthrough.types import TaskType

# ── Heuristic fallback helpers (moved from api/waste.py) ────────────────

_TASK_TYPE_MAP: dict[str, TaskType] = {t.value: t for t in TaskType}

# Cheapest model per tier — deterministic: pick by lowest avg_cost
_CHEAPEST_BY_TIER: dict[int, str] = {}
for _model, _info in MODEL_CATALOG.items():
    if _info.tier not in _CHEAPEST_BY_TIER:
        _CHEAPEST_BY_TIER[_info.tier] = _model
    else:
        existing = MODEL_CATALOG[_CHEAPEST_BY_TIER[_info.tier]]
        if _info.avg_cost < existing.avg_cost:
            _CHEAPEST_BY_TIER[_info.tier] = _model

_SIMPLE_TASKS: set[TaskType] = {
    TaskType.CLASSIFICATION,
    TaskType.EXTRACTION,
    TaskType.CONVERSATION,
}

_HEURISTIC_CONFIDENCE_CAP = 0.5


def _cost_ratio(current_model: str, suggested_model: str) -> float:
    cur = MODEL_CATALOG[current_model]
    sug = MODEL_CATALOG[suggested_model]
    if cur.avg_cost == 0:
        return 1.0
    return sug.avg_cost / cur.avg_cost


def _suggest_model_heuristic(task_type: TaskType, current_tier: int) -> str | None:
    """Tier-based heuristic: returns suggested model or None."""
    if task_type in _SIMPLE_TASKS:
        if current_tier in (1, 2):
            return _CHEAPEST_BY_TIER[3]
        return None

    if task_type == TaskType.SUMMARIZATION:
        if current_tier == 1:
            return _CHEAPEST_BY_TIER[2]
        return None

    if task_type in (TaskType.CODE_GENERATION, TaskType.CODE_REVIEW, TaskType.REASONING):
        if current_tier == 1:
            return _CHEAPEST_BY_TIER[2]
        return None

    if task_type == TaskType.TOOL_SELECTION:
        if current_tier == 1:
            return _CHEAPEST_BY_TIER[3]
        return None

    return None


# ── Fitness-based suggestion ────────────────────────────────────────────

def _find_cheapest_qualified(
    candidates: list[FitnessEntry],
    current_cost: ModelInfo,
    quality_threshold: float,
) -> FitnessEntry | None:
    """Pick the cheapest model that exceeds the quality bar and is cheaper than current."""
    best: FitnessEntry | None = None
    best_avg_cost: float = current_cost.avg_cost

    for entry in candidates:
        if entry.avg_quality < quality_threshold:
            continue

        alt_cost_info = MODEL_CATALOG.get(entry.model)
        if not alt_cost_info:
            continue

        if alt_cost_info.avg_cost >= current_cost.avg_cost:
            continue

        if alt_cost_info.avg_cost < best_avg_cost:
            best = entry
            best_avg_cost = alt_cost_info.avg_cost

    return best


def _compute_confidence(
    quality: float,
    sample_size: int,
    quality_threshold: float,
) -> float:
    """Evidence-based confidence from quality headroom and sample size."""
    denom = 1.0 - quality_threshold
    if denom <= 0:
        quality_factor = 1.0
    else:
        quality_factor = min((quality - quality_threshold) / denom, 1.0)

    # Saturates around 50 samples: log2(51) ≈ 5.67
    sample_factor = min(math.log2(sample_size + 1) / math.log2(51), 1.0)

    return round(quality_factor * sample_factor, 4)


# ── Public API ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Suggestion:
    suggested_model: str
    quality: float | None       # from fitness matrix; None for heuristic
    sample_size: int            # 0 for heuristic
    confidence: float
    source: str                 # "fitness" | "heuristic"
    cost_ratio: float


def suggest_alternative(
    task_type: str,
    current_model: str,
    fitness_entries: list[FitnessEntry] | None = None,
    *,
    quality_threshold: float = 0.85,
) -> Suggestion | None:
    """Suggest a cheaper model for (task_type, current_model).

    Tries fitness data first; falls back to tier-based heuristic when
    no benchmark evidence exists. Returns None when no suggestion applies.
    """
    current_info = MODEL_CATALOG.get(current_model)
    if not current_info:
        return None

    # ── Fitness path ────────────────────────────────────────────────
    if fitness_entries:
        by_task = [e for e in fitness_entries if e.task_type == task_type]
        best = _find_cheapest_qualified(by_task, current_info, quality_threshold)

        if best is not None:
            alt_info = MODEL_CATALOG.get(best.model)
            if alt_info and current_info.avg_cost > 0:
                ratio = alt_info.avg_cost / current_info.avg_cost
                confidence = _compute_confidence(
                    best.avg_quality, best.sample_size, quality_threshold,
                )
                return Suggestion(
                    suggested_model=best.model,
                    quality=round(best.avg_quality, 4),
                    sample_size=best.sample_size,
                    confidence=confidence,
                    source="fitness",
                    cost_ratio=ratio,
                )

    # ── Heuristic fallback ──────────────────────────────────────────
    tt = _TASK_TYPE_MAP.get(task_type)
    if tt is None:
        return None

    tier = current_info.tier
    suggested = _suggest_model_heuristic(tt, tier)
    if suggested is None:
        return None

    ratio = _cost_ratio(current_model, suggested)
    return Suggestion(
        suggested_model=suggested,
        quality=None,
        sample_size=0,
        confidence=_HEURISTIC_CONFIDENCE_CAP,
        source="heuristic",
        cost_ratio=ratio,
    )
