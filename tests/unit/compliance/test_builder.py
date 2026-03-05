"""Tests for audit record building and risk classification.

Covers the classify_risk() function edge cases, human oversight detection,
agent ID hashing, and end-to-end record construction from raw event rows.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from blockthrough.compliance.builder import (
    _detect_human_oversight,
    _hash_agent_id,
    build_audit_record_from_row,
    classify_risk,
)
from blockthrough.compliance.types import DecisionType, RiskLevel
from blockthrough.pipeline.hasher import hash_content


def _make_row(**overrides) -> dict:
    """Construct a minimal llm_events row dict for testing."""
    defaults = {
        "id": uuid4(),
        "created_at": datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc),
        "model": "gpt-4o",
        "task_type": "conversation",
        "trace_id": "trace-001",
        "agent_name": "test-agent",
        "has_tool_calls": False,
        "prompt_hash": "a" * 64,
        "completion_hash": "b" * 64,
        "custom_metadata": None,
    }
    defaults.update(overrides)
    return defaults


class TestClassifyRisk:
    """Risk classification logic based on task_type + metadata signals."""

    def test_reasoning_with_financial_metadata_is_critical(self) -> None:
        risk = classify_risk(
            "reasoning", False, {"domain": "financial analysis"}
        )
        assert risk == RiskLevel.CRITICAL

    def test_reasoning_with_medical_metadata_is_critical(self) -> None:
        risk = classify_risk(
            "reasoning", False, {"context": "patient diagnosis"}
        )
        assert risk == RiskLevel.CRITICAL

    def test_reasoning_without_sensitive_data_is_low(self) -> None:
        """Reasoning alone without sensitive keywords is just informational."""
        risk = classify_risk("reasoning", False, {"domain": "weather"})
        assert risk == RiskLevel.LOW

    def test_code_generation_with_tool_calls_is_high(self) -> None:
        risk = classify_risk("code_generation", True, None)
        assert risk == RiskLevel.HIGH

    def test_code_generation_without_tool_calls_is_low(self) -> None:
        """Code gen without autonomous execution is informational."""
        risk = classify_risk("code_generation", False, None)
        assert risk == RiskLevel.LOW

    def test_sensitive_data_with_tool_calls_is_high(self) -> None:
        """Any task with tool calls touching sensitive data escalates."""
        risk = classify_risk(
            "conversation", True, {"pii": True}
        )
        assert risk == RiskLevel.HIGH

    def test_classification_task_is_medium(self) -> None:
        risk = classify_risk("classification", False, None)
        assert risk == RiskLevel.MEDIUM

    def test_extraction_task_is_medium(self) -> None:
        risk = classify_risk("extraction", False, None)
        assert risk == RiskLevel.MEDIUM

    def test_tool_selection_is_medium(self) -> None:
        risk = classify_risk("tool_selection", False, None)
        assert risk == RiskLevel.MEDIUM

    def test_conversation_is_low(self) -> None:
        risk = classify_risk("conversation", False, None)
        assert risk == RiskLevel.LOW

    def test_summarization_is_low(self) -> None:
        risk = classify_risk("summarization", False, None)
        assert risk == RiskLevel.LOW

    def test_unknown_task_type_is_low(self) -> None:
        risk = classify_risk("unknown", False, None)
        assert risk == RiskLevel.LOW

    def test_none_task_type_is_low(self) -> None:
        risk = classify_risk(None, False, None)
        assert risk == RiskLevel.LOW

    def test_none_metadata_does_not_crash(self) -> None:
        risk = classify_risk("reasoning", False, None)
        assert risk == RiskLevel.LOW

    def test_empty_metadata_does_not_crash(self) -> None:
        risk = classify_risk("reasoning", False, {})
        assert risk == RiskLevel.LOW

    def test_hipaa_keyword_triggers_critical_for_reasoning(self) -> None:
        risk = classify_risk("reasoning", False, {"compliance": "hipaa"})
        assert risk == RiskLevel.CRITICAL

    def test_credit_card_keyword_triggers_critical_for_reasoning(self) -> None:
        risk = classify_risk(
            "reasoning", False, {"field": "credit_card validation"}
        )
        assert risk == RiskLevel.CRITICAL

    def test_payment_keyword_with_tool_calls_is_high(self) -> None:
        risk = classify_risk(
            "conversation", True, {"flow": "payment processing"}
        )
        assert risk == RiskLevel.HIGH


class TestDetectHumanOversight:
    """Human-in-the-loop detection from custom_metadata."""

    def test_boolean_true(self) -> None:
        assert _detect_human_oversight({"human_in_loop": True}) is True

    def test_boolean_false(self) -> None:
        assert _detect_human_oversight({"human_in_loop": False}) is False

    def test_string_true(self) -> None:
        assert _detect_human_oversight({"human_in_loop": "true"}) is True

    def test_string_yes(self) -> None:
        assert _detect_human_oversight({"human_in_loop": "yes"}) is True

    def test_string_one(self) -> None:
        assert _detect_human_oversight({"human_in_loop": "1"}) is True

    def test_string_false(self) -> None:
        assert _detect_human_oversight({"human_in_loop": "false"}) is False

    def test_none_metadata(self) -> None:
        assert _detect_human_oversight(None) is False

    def test_empty_metadata(self) -> None:
        assert _detect_human_oversight({}) is False

    def test_missing_key(self) -> None:
        assert _detect_human_oversight({"other": "value"}) is False

    def test_alternative_key_human_in_the_loop(self) -> None:
        """Some frameworks use human_in_the_loop (with 'the')."""
        assert _detect_human_oversight({"human_in_the_loop": True}) is True

    def test_integer_truthy(self) -> None:
        assert _detect_human_oversight({"human_in_loop": 1}) is True

    def test_integer_falsy(self) -> None:
        assert _detect_human_oversight({"human_in_loop": 0}) is False


class TestHashAgentId:
    """Agent identity hashing — prefers agent_name, falls back to trace_id."""

    def test_uses_agent_name_when_present(self) -> None:
        result = _hash_agent_id("my-agent", "trace-001")
        expected = hash_content("my-agent")
        assert result == expected

    def test_falls_back_to_trace_id_when_no_name(self) -> None:
        result = _hash_agent_id(None, "trace-001")
        expected = hash_content("trace-001")
        assert result == expected

    def test_deterministic(self) -> None:
        a = _hash_agent_id("agent-x", "trace")
        b = _hash_agent_id("agent-x", "trace")
        assert a == b

    def test_different_agents_different_hash(self) -> None:
        a = _hash_agent_id("agent-a", "trace")
        b = _hash_agent_id("agent-b", "trace")
        assert a != b


class TestBuildAuditRecordFromRow:
    """End-to-end record construction from raw event data."""

    def test_basic_record_fields(self) -> None:
        row = _make_row()
        record = build_audit_record_from_row(row)

        assert record.model == "gpt-4o"
        assert record.task_type == "conversation"
        assert record.data_accessed_hash == "a" * 64
        assert record.output_hash == "b" * 64

    def test_record_hash_is_populated(self) -> None:
        row = _make_row()
        record = build_audit_record_from_row(row)

        assert len(record.record_hash) == 64
        int(record.record_hash, 16)  # valid hex

    def test_record_hash_is_deterministic(self) -> None:
        row = _make_row(id=uuid4())
        a = build_audit_record_from_row(row)
        b = build_audit_record_from_row(row)
        assert a.record_hash == b.record_hash

    def test_human_oversight_flag_from_metadata(self) -> None:
        row = _make_row(custom_metadata={"human_in_loop": True})
        record = build_audit_record_from_row(row)

        assert record.human_oversight_flag is True
        assert record.decision_type == DecisionType.HUMAN_IN_LOOP

    def test_autonomous_when_no_oversight(self) -> None:
        row = _make_row(custom_metadata=None)
        record = build_audit_record_from_row(row)

        assert record.human_oversight_flag is False
        assert record.decision_type == DecisionType.AUTONOMOUS

    def test_risk_level_assigned(self) -> None:
        row = _make_row(task_type="code_generation", has_tool_calls=True)
        record = build_audit_record_from_row(row)
        assert record.risk_level == RiskLevel.HIGH

    def test_event_id_is_hashed(self) -> None:
        """Event ID should be a hash, not the raw UUID."""
        row = _make_row()
        record = build_audit_record_from_row(row)

        # Should be 64-char hex (SHA-256), not a UUID format
        assert len(record.event_id) == 64
        assert "-" not in record.event_id

    def test_agent_id_is_hashed(self) -> None:
        row = _make_row(agent_name="my-agent")
        record = build_audit_record_from_row(row)

        assert record.agent_id == hash_content("my-agent")
        assert "my-agent" not in record.agent_id

    def test_none_task_type_preserved(self) -> None:
        row = _make_row(task_type=None)
        record = build_audit_record_from_row(row)
        assert record.task_type is None
