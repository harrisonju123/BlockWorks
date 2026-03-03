"""Individual waste detectors, one per category."""

from agentproof.waste.detectors.agent_loops import detect_agent_loops
from agentproof.waste.detectors.cache_misses import detect_cache_misses
from agentproof.waste.detectors.context_bloat import detect_context_bloat
from agentproof.waste.detectors.model_overkill import detect_model_overkill
from agentproof.waste.detectors.redundant_calls import detect_redundant_calls

__all__ = [
    "detect_agent_loops",
    "detect_cache_misses",
    "detect_context_bloat",
    "detect_model_overkill",
    "detect_redundant_calls",
]
