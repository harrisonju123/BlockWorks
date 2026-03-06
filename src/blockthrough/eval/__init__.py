"""Evaluation framework for AgentProof's auto-routing pipeline.

Provides offline simulation and comparison tools for the classifier → router chain.
"""

from __future__ import annotations

from blockthrough.eval.e2e_eval import E2EReport, PairedResult, ParetoPoint
from blockthrough.eval.routing_eval import compare_policies, load_expectations, simulate_policy, simulate_with_defaults
from blockthrough.eval.types import (
    ExpectedBehavior,
    PolicyComparison,
    RoutingExpectation,
    SimulationReport,
    SimulationRow,
    TaskBreakdown,
)

__all__ = [
    "E2EReport",
    "ExpectedBehavior",
    "PairedResult",
    "ParetoPoint",
    "PolicyComparison",
    "RoutingExpectation",
    "SimulationReport",
    "SimulationRow",
    "TaskBreakdown",
    "compare_policies",
    "load_expectations",
    "simulate_policy",
    "simulate_with_defaults",
]
