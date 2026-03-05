"""Blockthrough Python SDK — standalone client for reporting LLM events.

Works independently of LiteLLM. Provides both async (BlockThroughSDK)
and sync (BlockThroughClient) interfaces for tracking events, querying
stats, waste scores, and fitness matrices.
"""

from blockthrough.sdk.client import (
    BlockThroughClient,
    BlockThroughSDK,
    # Backward-compat aliases re-exported from client module
    AgentProofClient,
    AgentProofSDK,
)
from blockthrough.sdk.decorators import (
    agentproof_trace,  # backward-compat alias
    blockthrough_trace,
    track_anthropic,
    track_llm_call,
    track_openai,
)
from blockthrough.sdk.types import (
    CostEstimate,
    FitnessMatrixResponse,
    SDKConfig,
    StatsResponse,
    TrackEventRequest,
    TrackEventResponse,
    WasteScoreResponse,
)

__all__ = [
    "BlockThroughClient",
    "BlockThroughSDK",
    "AgentProofClient",
    "AgentProofSDK",
    "CostEstimate",
    "FitnessMatrixResponse",
    "SDKConfig",
    "StatsResponse",
    "TrackEventRequest",
    "TrackEventResponse",
    "WasteScoreResponse",
    "agentproof_trace",
    "blockthrough_trace",
    "track_anthropic",
    "track_llm_call",
    "track_openai",
]
