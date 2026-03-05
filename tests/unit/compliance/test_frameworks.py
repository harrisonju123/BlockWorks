"""Tests for framework-specific compliance validation.

Validates that each framework's required fields are correctly enforced,
and that missing fields produce clear violation messages.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from blockthrough.compliance.builder import build_audit_record_from_row
from blockthrough.compliance.frameworks import get_required_fields, validate_compliance
from blockthrough.compliance.report import generate_audit_report
from blockthrough.compliance.types import (
    AuditRecord,
    AuditReport,
    ComplianceFramework,
    DecisionType,
    RiskLevel,
)


def _make_row(**overrides) -> dict:
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


def _make_records(count: int, **overrides) -> list:
    records = []
    for i in range(count):
        row = _make_row(
            id=uuid4(),
            trace_id=f"trace-{i:03d}",
            **overrides,
        )
        records.append(build_audit_record_from_row(row))
    return records


_START = datetime(2026, 3, 1, 0, 0, 0, tzinfo=timezone.utc)
_END = datetime(2026, 3, 2, 0, 0, 0, tzinfo=timezone.utc)


class TestGetRequiredFields:
    """Required field lists per framework."""

    def test_eu_ai_act_fields(self) -> None:
        fields = get_required_fields(ComplianceFramework.EU_AI_ACT)
        assert "risk_level" in fields
        assert "human_oversight_flag" in fields
        assert "decision_type" in fields
        assert "timestamp" in fields

    def test_soc2_fields(self) -> None:
        fields = get_required_fields(ComplianceFramework.SOC2)
        assert "timestamp" in fields
        assert "agent_id" in fields
        assert "data_accessed_hash" in fields
        assert "output_hash" in fields

    def test_hipaa_fields(self) -> None:
        fields = get_required_fields(ComplianceFramework.HIPAA)
        assert "timestamp" in fields
        assert "data_accessed_hash" in fields
        assert "human_oversight_flag" in fields
        assert "risk_level" in fields

    def test_financial_services_fields(self) -> None:
        fields = get_required_fields(ComplianceFramework.FINANCIAL_SERVICES)
        assert "timestamp" in fields
        assert "agent_id" in fields
        assert "model" in fields
        assert "risk_level" in fields
        assert "decision_type" in fields

    def test_returns_new_list_each_call(self) -> None:
        """Callers should not be able to mutate the internal list."""
        a = get_required_fields(ComplianceFramework.EU_AI_ACT)
        b = get_required_fields(ComplianceFramework.EU_AI_ACT)
        assert a is not b


class TestValidateCompliance:
    """Framework compliance validation."""

    def test_valid_report_eu_ai_act(self) -> None:
        """A properly built report should have no violations."""
        records = _make_records(3)
        report = generate_audit_report(records, "org-1", _START, _END)
        violations = validate_compliance(report, ComplianceFramework.EU_AI_ACT)
        assert violations == []

    def test_valid_report_soc2(self) -> None:
        records = _make_records(3)
        report = generate_audit_report(records, "org-1", _START, _END)
        violations = validate_compliance(report, ComplianceFramework.SOC2)
        assert violations == []

    def test_valid_report_hipaa(self) -> None:
        records = _make_records(3)
        report = generate_audit_report(records, "org-1", _START, _END)
        violations = validate_compliance(report, ComplianceFramework.HIPAA)
        assert violations == []

    def test_valid_report_financial_services(self) -> None:
        records = _make_records(3)
        report = generate_audit_report(records, "org-1", _START, _END)
        violations = validate_compliance(
            report, ComplianceFramework.FINANCIAL_SERVICES
        )
        assert violations == []

    def test_empty_report_has_violation(self) -> None:
        report = generate_audit_report([], "org-1", _START, _END)
        violations = validate_compliance(report, ComplianceFramework.EU_AI_ACT)
        assert any("no audit records" in v for v in violations)

    def test_missing_attestation_hash_flagged(self) -> None:
        """A report without a Merkle root should be flagged."""
        records = _make_records(2)
        report = generate_audit_report(records, "org-1", _START, _END)
        report.attestation_hash = ""

        violations = validate_compliance(report, ComplianceFramework.SOC2)
        assert any("attestation_hash" in v for v in violations)

    def test_record_with_missing_field_flagged(self) -> None:
        """Simulate a record with an empty required field."""
        record = AuditRecord(
            timestamp=_START,
            event_id="abc123",
            agent_id="",  # empty — violates SOC2
            model="gpt-4o",
            task_type="conversation",
            decision_type=DecisionType.AUTONOMOUS,
            data_accessed_hash="a" * 64,
            output_hash="b" * 64,
            human_oversight_flag=False,
            risk_level=RiskLevel.LOW,
            record_hash="c" * 64,
        )

        report = AuditReport(
            org_id="hashed-org",
            period_start=_START,
            period_end=_END,
            records=[record],
            total_events=1,
            human_oversight_pct=0.0,
            risk_distribution={"low": 1, "medium": 0, "high": 0, "critical": 0},
            attestation_hash="d" * 64,
            generated_at=_START,
        )

        violations = validate_compliance(report, ComplianceFramework.SOC2)
        assert any("agent_id" in v for v in violations)

    def test_all_frameworks_accept_well_formed_report(self) -> None:
        """Sanity check: a well-formed report passes all frameworks."""
        records = _make_records(5)
        report = generate_audit_report(records, "org-1", _START, _END)

        for framework in ComplianceFramework:
            violations = validate_compliance(report, framework)
            assert violations == [], (
                f"{framework.value} had violations: {violations}"
            )
