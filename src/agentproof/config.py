"""Environment-based configuration using pydantic-settings.

All settings are configurable via AGENTPROOF_ prefixed env vars.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings


class AgentProofConfig(BaseSettings):
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

    # Classifier
    classifier_confidence_threshold: float = 0.7
    classifier_use_ml: bool = False

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8100
    api_cors_origins: list[str] = ["http://localhost:5173"]

    # General
    env: str = "development"
    log_level: str = "INFO"
    org_id: str | None = None


@lru_cache
def get_config() -> AgentProofConfig:
    return AgentProofConfig()
