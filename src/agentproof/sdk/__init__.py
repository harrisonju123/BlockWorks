"""AgentProof Python SDK — standalone client for reporting LLM events.

Works independently of LiteLLM. Provides both async (AgentProofSDK)
and sync (AgentProofClient) interfaces for tracking events, querying
stats, waste scores, and fitness matrices.
"""

from agentproof.sdk.client import AgentProofClient, AgentProofSDK
from agentproof.sdk.decorators import agentproof_trace, track_anthropic, track_llm_call, track_openai
from agentproof.sdk.types import (
    CostEstimate,
    FitnessMatrixResponse,
    SDKConfig,
    StatsResponse,
    TrackEventRequest,
    TrackEventResponse,
    WasteScoreResponse,
)

__all__ = [
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
    "track_anthropic",
    "track_llm_call",
    "track_openai",
]
