"""Audit record builder — converts raw llm_events into compliance records.

Risk classification uses task_type + model + metadata signals to assign
a level per the EU AI Act risk tiers. Human oversight detection checks
the custom_metadata JSONB field for a human_in_loop flag.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agentproof.compliance.types import AuditRecord, DecisionType, RiskLevel
from agentproof.pipeline.hasher import hash_content
from agentproof.types import TaskType

# Keywords in custom_metadata that signal sensitive data domains.
# Presence of these alongside certain task types escalates risk.
_SENSITIVE_KEYWORDS = frozenset({
    "financial", "finance", "payment", "banking", "transaction",
    "medical", "health", "patient", "diagnosis", "hipaa",
    "pii", "ssn", "credit_card", "personal_data",
})


def classify_risk(
    task_type: str | None,
    has_tool_calls: bool,
    metadata: dict | None,
) -> RiskLevel:
    """Assign a risk level based on task signals and metadata.

    Classification rules (ordered from most to least severe):
      CRITICAL: reasoning tasks touching sensitive data domains
      HIGH: code_generation with tool_calls (autonomous code execution)
      MEDIUM: classification, extraction (automated data processing)
      LOW: conversation, summarization (informational only)
    """
    metadata_str = str(metadata).lower() if metadata else ""
    has_sensitive = any(kw in metadata_str for kw in _SENSITIVE_KEYWORDS)

    tt = task_type or "unknown"

    # CRITICAL: reasoning on sensitive data
    if tt == TaskType.REASONING.value and has_sensitive:
        return RiskLevel.CRITICAL

    # HIGH: autonomous code execution, or any sensitive + tool usage
    if tt == TaskType.CODE_GENERATION.value and has_tool_calls:
        return RiskLevel.HIGH
    if has_sensitive and has_tool_calls:
        return RiskLevel.HIGH

    # MEDIUM: automated data processing tasks
    if tt in (TaskType.CLASSIFICATION.value, TaskType.EXTRACTION.value):
        return RiskLevel.MEDIUM
    if tt == TaskType.TOOL_SELECTION.value:
        return RiskLevel.MEDIUM

    # LOW: informational tasks
    return RiskLevel.LOW


def _detect_human_oversight(metadata: dict | None) -> bool:
    """Check custom_metadata for human-in-the-loop flag.

    Supports both boolean and string representations since metadata
    comes from diverse agent frameworks.
    """
    if not metadata:
        return False

    flag = metadata.get("human_in_loop")
    if flag is None:
        # Also check nested metadata patterns
        flag = metadata.get("human_in_the_loop")
    if flag is None:
        return False

    if isinstance(flag, bool):
        return flag
    if isinstance(flag, str):
        return flag.lower() in ("true", "1", "yes")
    return bool(flag)


def _hash_agent_id(agent_name: str | None, trace_id: str) -> str:
    """Deterministic pseudonym for an agent identity.

    Uses agent_name when available (more stable across traces),
    falls back to trace_id for anonymous agents.
    """
    identity = agent_name or trace_id
    return hash_content(identity)


def build_audit_record_from_row(row: dict) -> AuditRecord:
    """Convert a single llm_events row dict into an AuditRecord.

    Extracted so it can be used both by the async DB path and by
    unit tests with synthetic data.
    """
    metadata = row.get("custom_metadata")
    has_tool_calls = bool(row.get("has_tool_calls", False))
    task_type = row.get("task_type")

    human_oversight = _detect_human_oversight(metadata)
    risk = classify_risk(task_type, has_tool_calls, metadata)

    decision_type = (
        DecisionType.HUMAN_IN_LOOP if human_oversight else DecisionType.AUTONOMOUS
    )

    agent_id = _hash_agent_id(row.get("agent_name"), row["trace_id"])

    record = AuditRecord(
        timestamp=row["created_at"],
        event_id=hash_content(str(row["id"])),
        agent_id=agent_id,
        model=row["model"],
        task_type=task_type,
        decision_type=decision_type,
        data_accessed_hash=row["prompt_hash"],
        output_hash=row["completion_hash"],
        human_oversight_flag=human_oversight,
        risk_level=risk,
    )

    # Seal the record with a hash of its own content
    record.record_hash = hash_content({
        "timestamp": record.timestamp.isoformat(),
        "event_id": record.event_id,
        "agent_id": record.agent_id,
        "model": record.model,
        "task_type": record.task_type,
        "decision_type": record.decision_type.value,
        "data_accessed_hash": record.data_accessed_hash,
        "output_hash": record.output_hash,
        "human_oversight_flag": record.human_oversight_flag,
        "risk_level": record.risk_level.value,
    })

    return record


async def build_audit_records(
    session: AsyncSession,
    org_id: str | None,
    start: datetime,
    end: datetime,
) -> list[AuditRecord]:
    """Query llm_events and build audit records for the period.

    Hits the raw table since we need per-event granularity and
    custom_metadata access (not available in continuous aggregates).
    """
    org_filter = "AND org_id = :org_id" if org_id else ""

    query = text(f"""
        SELECT
            id, created_at, model, task_type, trace_id,
            agent_name, has_tool_calls, prompt_hash, completion_hash,
            custom_metadata
        FROM llm_events
        WHERE created_at >= :start AND created_at < :end
        {org_filter}
        ORDER BY created_at
    """)

    params: dict = {"start": start, "end": end}
    if org_id:
        params["org_id"] = org_id

    result = await session.execute(query, params)
    rows = [dict(r._mapping) for r in result.fetchall()]

    return [build_audit_record_from_row(row) for row in rows]
