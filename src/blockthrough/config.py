"""Environment-based configuration using pydantic-settings.

All settings are configurable via AGENTPROOF_ prefixed env vars
(prefix kept for backward compatibility).
"""

from functools import lru_cache

from pydantic_settings import BaseSettings


class BlockThroughConfig(BaseSettings):
    model_config = {"env_prefix": "AGENTPROOF_"}

    # Database
    database_url: str = (
        "postgresql+asyncpg://agentproof:localdev@localhost:5432/agentproof"
    )

    # Pipeline
    pipeline_batch_size: int = 50
    pipeline_flush_interval_ms: int = 100
    pipeline_queue_max_size: int = 10_000
    pipeline_enable_classification: bool = True

    # MCP tracing
    mcp_tracing_enabled: bool = True

    # Classifier
    classifier_confidence_threshold: float = 0.7
    classifier_use_ml: bool = False

    # Proxy — upstream for OpenAI-compatible traffic (/v1/chat/completions)
    upstream_url: str = "http://localhost:4000"
    # Proxy — upstream for Anthropic-native traffic (/v1/messages)
    # When using `make claude`, this must point to the real Anthropic API.
    anthropic_upstream_url: str = "https://api.anthropic.com"

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8100
    api_cors_origins: list[str] = ["http://localhost:8081"]

    # Benchmarking
    benchmark_enabled: bool = False
    benchmark_sample_rate: float = 0.05
    benchmark_models: list[str] = [
        "claude-haiku-4-5-20251001",
        "gpt-4o-mini",
        "google.gemma-3-27b-it",
        "openai.gpt-oss-120b-1:0",
    ]
    benchmark_judge_model: str = "claude-sonnet-4-6"
    benchmark_smart_targeting: bool = True
    benchmark_target_max: int = 6
    benchmark_target_min_samples: int = 10

    # Waste
    waste_quality_threshold: float = 0.85

    # Routing
    routing_enabled: bool = True
    routing_policy_path: str | None = None
    routing_fitness_cache_ttl_s: int = 300
    # Models available in the upstream provider (e.g. LiteLLM).
    # The router will only select from this set. Empty = allow all catalog models.
    routing_available_models: list[str] = []
    # User-selected fallback models per tier. Used by the bootstrap policy
    # when no candidate meets quality criteria for a task type.
    routing_model_high: str = "claude-opus-4-6"
    routing_model_mid: str = "claude-sonnet-4-6"
    routing_model_low: str = "claude-haiku-4-5-20251001"

    # Alerts & Budgets
    alerts_enabled: bool = True
    alerts_check_interval_s: int = 60
    alerts_cooldown_s: int = 3600
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_from: str | None = None

    # State Channels (Phase 2)
    channels_enabled: bool = False
    channels_max_duration_s: int = 3600
    channels_min_deposit: float = 0.01

    # Attestation (Phase 2)
    attestation_provider: str = "local"
    attestation_rpc_url: str | None = None
    attestation_contract_address: str | None = None
    attestation_private_key: str = ""
    attestation_deployments_path: str = "contracts/deployments/local.json"
    attestation_scheduler_enabled: bool = True
    attestation_scheduler_interval_s: int = 3600

    # Validators (Phase 3)
    validators_enabled: bool = False
    validators_min_stake: float = 0.1
    validators_consensus_threshold: int = 2
    validators_agreement_tolerance: float = 0.1

    # Governance (Phase 3)
    governance_voting_period_s: int = 604_800  # 7 days
    governance_quorum_pct: float = 10.0

    # Trust Scores (Phase 3)
    trust_decay_factor: float = 0.95

    # Enterprise Multi-Tenant (Phase 4)
    enterprise_enabled: bool = False
    enterprise_sso_providers: list[str] = []
    enterprise_free_limit: int = 50_000
    enterprise_pro_limit: int = 500_000

    # Registry (Phase 4)
    registry_enabled: bool = False
    registry_min_stake: float = 0.01
    registry_verification_min_trust: float = 0.6
    registry_verification_min_uptime: float = 0.95
    registry_verification_min_calls: int = 100

    # Workflows (Phase 4)
    workflows_enabled: bool = False
    workflows_max_steps: int = 20
    workflows_execution_timeout_s: int = 300

    # Revenue Sharing (Phase 4)
    revenue_enabled: bool = False
    revenue_protocol_fee_pct: float = 3.0
    revenue_burn_pct: float = 30.0
    revenue_min_settlement: float = 0.001

    # Interop (Phase 4)
    interop_enabled: bool = False
    interop_default_timeout_s: int = 30
    interop_max_cost_per_invocation: float = 1.0
    interop_signing_secret: str = ""

    # General
    env: str = "development"
    log_level: str = "INFO"
    org_id: str | None = None


@lru_cache
def get_config() -> BlockThroughConfig:
    return BlockThroughConfig()


# Backward-compat alias
AgentProofConfig = BlockThroughConfig
