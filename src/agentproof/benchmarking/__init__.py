"""Cross-provider benchmarking engine.

Silently benchmarks alternative models against production traffic.
Builds a fitness matrix (task_type x model -> quality, cost, latency)
that drives smart routing and waste detection. Includes drift detection
and vendor accountability report generation.
"""

from agentproof.benchmarking.accountability import (
    AccountabilityReport,
    DriftItem,
    generate_report,
)
from agentproof.benchmarking.drift import DriftReport, detect_drift
from agentproof.benchmarking.types import (
    BenchmarkConfig,
    BenchmarkResult,
    FitnessEntry,
    Rubric,
    RubricCriterion,
)

__all__ = [
    "AccountabilityReport",
    "BenchmarkConfig",
    "BenchmarkResult",
    "DriftItem",
    "DriftReport",
    "FitnessEntry",
    "Rubric",
    "RubricCriterion",
    "detect_drift",
    "generate_report",
]
