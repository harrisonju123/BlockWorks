"""Global Model Fitness Index.

Public, real-time leaderboard of model performance across task types
based on aggregated production benchmark data. Reuses the existing
fitness matrix and benchmark results -- no new schema required.
"""

from agentproof.fitness.builder import build_leaderboard
from agentproof.fitness.comparison import compare_models
from agentproof.fitness.types import (
    FitnessIndexConfig,
    LeaderboardEntry,
    LeaderboardFilter,
    ModelComparison,
    TrendPoint,
)
from agentproof.fitness.widget import generate_badge_data, generate_summary_widget

__all__ = [
    "FitnessIndexConfig",
    "LeaderboardEntry",
    "LeaderboardFilter",
    "ModelComparison",
    "TrendPoint",
    "build_leaderboard",
    "compare_models",
    "generate_badge_data",
    "generate_summary_widget",
]
