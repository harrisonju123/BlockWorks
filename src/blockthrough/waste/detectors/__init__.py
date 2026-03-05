"""Individual waste detectors, one per category."""

from blockthrough.waste.detectors.agent_loops import detect_agent_loops
from blockthrough.waste.detectors.cache_misses import detect_cache_misses
from blockthrough.waste.detectors.context_bloat import detect_context_bloat
from blockthrough.waste.detectors.model_overkill import detect_model_overkill
from blockthrough.waste.detectors.redundant_calls import detect_redundant_calls

__all__ = [
    "detect_agent_loops",
    "detect_cache_misses",
    "detect_context_bloat",
    "detect_model_overkill",
    "detect_redundant_calls",
]
