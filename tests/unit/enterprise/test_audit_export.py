"""Tests for enterprise audit export — JSON/CSV formats and tenant metadata."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from agentproof.compliance.builder import build_audit_record_from_row
from agentproof.compliance.report import generate_audit_report
from agentproof.compliance.types import AuditReport, ComplianceFramework
from agentproof.config import get_config
from agentproof.enterprise.audit_export import (
    export_tenant_audit,
    get_export_schedules,
    reset_store,
    schedule_audit_export,
)
from agentproof.enterprise.tenants import create_tenant
from agentproof.enterprise.tenants import reset_store as reset_tenant_store


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


def _make_report(tenant_id: str, n_records: int = 3) -> AuditReport:
    records = []
    for i in range(n_records):
        row = _make_row(id=uuid4(), trace_id=f"trace-{i:03d}")
        records.append(build_audit_record_from_row(row))

    start = datetime(2026, 3, 1, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 3, 2, 0, 0, 0, tzinfo=timezone.utc)
    return generate_audit_report(records, tenant_id, start, end)


class TestExportTenantAuditJson:
    def setup_method(self) -> None:
        reset_store()
        reset_tenant_store()
        get_config.cache_clear()

    def test_json_envelope_has_tenant_metadata(self) -> None:
        t = create_tenant("Acme Corp")
        report = _make_report(t.id)
        result = export_tenant_audit(t.id, report, ComplianceFramework.SOC2)
        parsed = json.loads(result)

        assert "tenant" in parsed
        assert parsed["tenant"]["tenant_id"] == t.id
        assert parsed["tenant"]["tenant_name"] == "Acme Corp"

    def test_json_envelope_has_compliance_status(self) -> None:
        t = create_tenant("Compliant Co")
        report = _make_report(t.id)
        result = export_tenant_audit(t.id, report, ComplianceFramework.SOC2)
        parsed = json.loads(result)

        assert "compliance" in parsed
        assert "is_compliant" in parsed["compliance"]
        assert "violations" in parsed["compliance"]

    def test_json_envelope_has_framework(self) -> None:
        t = create_tenant("Framework Co")
        report = _make_report(t.id)
        result = export_tenant_audit(t.id, report, ComplianceFramework.EU_AI_ACT)
        parsed = json.loads(result)
        assert parsed["framework"] == "eu_ai_act"

    def test_json_envelope_has_report(self) -> None:
        t = create_tenant("Report Co")
        report = _make_report(t.id, n_records=2)
        result = export_tenant_audit(t.id, report, ComplianceFramework.SOC2)
        parsed = json.loads(result)

        assert "report" in parsed
        assert len(parsed["report"]["records"]) == 2

    def test_empty_report_produces_valid_json(self) -> None:
        t = create_tenant("Empty Co")
        now = datetime.now(timezone.utc)
        empty_report = AuditReport(
            org_id=t.id,
            period_start=now,
            period_end=now,
            generated_at=now,
        )
        result = export_tenant_audit(t.id, empty_report, ComplianceFramework.SOC2)
        parsed = json.loads(result)
        assert parsed["report"]["total_events"] == 0


class TestExportTenantAuditCsv:
    def setup_method(self) -> None:
        reset_store()
        reset_tenant_store()
        get_config.cache_clear()

    def test_csv_has_metadata_header(self) -> None:
        t = create_tenant("CSV Corp")
        report = _make_report(t.id)
        result = export_tenant_audit(t.id, report, ComplianceFramework.SOC2, fmt="csv")
        text = result.decode("utf-8")

        assert text.startswith("# Tenant:")
        assert "CSV Corp" in text
        assert "soc2" in text

    def test_csv_has_attestation_hash(self) -> None:
        t = create_tenant("Hash Co")
        report = _make_report(t.id)
        result = export_tenant_audit(t.id, report, ComplianceFramework.SOC2, fmt="csv")
        text = result.decode("utf-8")
        assert "Attestation Hash:" in text

    def test_csv_contains_data_rows(self) -> None:
        t = create_tenant("Data Co")
        report = _make_report(t.id, n_records=5)
        result = export_tenant_audit(t.id, report, ComplianceFramework.SOC2, fmt="csv")
        lines = result.decode("utf-8").strip().split("\n")
        # Metadata header lines + CSV header + 5 data rows
        data_lines = [l for l in lines if not l.startswith("#") and l.strip()]
        # First non-comment line is CSV header, rest are data
        assert len(data_lines) >= 6  # header + 5 rows


class TestScheduleAuditExport:
    def setup_method(self) -> None:
        reset_store()
        reset_tenant_store()

    def test_schedule_creates_config(self) -> None:
        config = schedule_audit_export(
            "t-001", "weekly", ComplianceFramework.SOC2, "s3://bucket/audits"
        )
        assert config.tenant_id == "t-001"
        assert config.frequency == "weekly"
        assert config.framework == ComplianceFramework.SOC2
        assert config.destination == "s3://bucket/audits"

    def test_schedule_is_retrievable(self) -> None:
        schedule_audit_export(
            "t-002", "monthly", ComplianceFramework.HIPAA, "email:admin@co.com"
        )
        schedules = get_export_schedules("t-002")
        assert len(schedules) == 1
        assert schedules[0].frequency == "monthly"

    def test_multiple_schedules_per_tenant(self) -> None:
        schedule_audit_export("t-003", "daily", ComplianceFramework.SOC2, "s3://a")
        schedule_audit_export("t-003", "weekly", ComplianceFramework.HIPAA, "s3://b")
        assert len(get_export_schedules("t-003")) == 2

    def test_no_schedules_returns_empty(self) -> None:
        assert get_export_schedules("nonexistent") == []
