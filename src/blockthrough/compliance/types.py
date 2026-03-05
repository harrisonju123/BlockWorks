"""Pydantic models and enums for the compliance audit trail.

These types represent audit records and reports generated on-the-fly
from llm_events data. No new DB tables — everything is computed at
query time and hashed for tamper-evident export.
"""

from __future__ import annotations

import enum
from datetime import datetime

from pydantic import BaseModel, Field


class RiskLevel(str, enum.Enum):
    """Risk classification for AI agent operations.

    Drives the risk_distribution in compliance reports and maps to
    EU AI Act risk tiers.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ComplianceFramework(str, enum.Enum):
    """Supported regulatory frameworks.

    Each framework defines its own required fields, checked by
    validate_compliance() in frameworks.py.
    """

    EU_AI_ACT = "eu_ai_act"
    SOC2 = "soc2"
    HIPAA = "hipaa"
    FINANCIAL_SERVICES = "financial_services"


class DecisionType(str, enum.Enum):
    """Whether a decision was made autonomously or with human oversight."""

    AUTONOMOUS = "autonomous"
    HUMAN_IN_LOOP = "human_in_loop"


class AuditRecord(BaseModel):
    """A single immutable audit record of an AI agent operation.

    Generated from an llm_event row. All identifiers are hashed —
    no raw user data leaves the system.
    """

    timestamp: datetime
    event_id: str  # hashed UUID
    agent_id: str  # hashed agent identity (agent_name or trace_id)
    model: str
    task_type: str | None
    decision_type: DecisionType
    data_accessed_hash: str  # SHA-256 of prompt content
    output_hash: str  # SHA-256 of completion content
    human_oversight_flag: bool
    risk_level: RiskLevel
    record_hash: str = ""  # computed after construction


class AuditReport(BaseModel):
    """Compliance report covering a time period for one org.

    Contains summary statistics, risk distribution, and a Merkle root
    anchoring all audit records for tamper detection.
    """

    org_id: str  # hashed
    period_start: datetime
    period_end: datetime
    records: list[AuditRecord] = Field(default_factory=list)
    total_events: int = 0
    human_oversight_pct: float = 0.0
    risk_distribution: dict[str, int] = Field(default_factory=dict)
    attestation_hash: str = ""
    generated_at: datetime | None = None
