"""Waste detection and recommendations engine.

Analyzes LLM usage patterns to identify specific waste categories
(model overkill, redundant calls, context bloat, cache misses, agent loops)
and provides actionable recommendations with dollar amounts.
"""

from agentproof.waste.analyzer import WasteAnalyzer
from agentproof.waste.types import WasteCategory, WasteItem, WasteReport, WasteSeverity

__all__ = [
    "WasteAnalyzer",
    "WasteCategory",
    "WasteItem",
    "WasteReport",
    "WasteSeverity",
]
