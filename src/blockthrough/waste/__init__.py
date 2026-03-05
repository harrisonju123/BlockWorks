"""Waste detection and recommendations engine.

Analyzes LLM usage patterns to identify specific waste categories
(model overkill, redundant calls, context bloat, cache misses, agent loops)
and provides actionable recommendations with dollar amounts.
"""

from blockthrough.waste.analyzer import WasteAnalyzer
from blockthrough.waste.suggest import Suggestion, suggest_alternative
from blockthrough.waste.types import WasteCategory, WasteItem, WasteReport, WasteSeverity

__all__ = [
    "Suggestion",
    "WasteAnalyzer",
    "WasteCategory",
    "WasteItem",
    "WasteReport",
    "WasteSeverity",
    "suggest_alternative",
]
