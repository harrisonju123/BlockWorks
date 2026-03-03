"""Tests for report generation — summary stats, Merkle root, risk distribution.

Validates that generate_audit_report correctly computes aggregates and
produces a tamper-evident attestation hash via the Merkle tree.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from agentproof.attestation.merkle import EMPTY_LEAF, MerkleTree
from agentproof.compliance.builder import build_audit_record_from_row
from agentproof.compliance.report import generate_audit_report
from agentproof.compliance.types import RiskLevel


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
    """Build a list of audit records from synthetic event rows."""
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


class TestGenerateAuditReport:
    """Report generation and summary statistics."""

    def test_total_events_count(self) -> None:
        records = _make_records(5)
        report = generate_audit_report(records, "org-1", _START, _END)
        assert report.total_events == 5

    def test_empty_records(self) -> None:
        report = generate_audit_report([], "org-1", _START, _END)
        assert report.total_events == 0
        assert report.human_oversight_pct == 0.0

    def test_human_oversight_percentage_all_human(self) -> None:
        records = _make_records(
            4, custom_metadata={"human_in_loop": True}
        )
        report = generate_audit_report(records, "org-1", _START, _END)
        assert report.human_oversight_pct == 100.0

    def test_human_oversight_percentage_none(self) -> None:
        records = _make_records(4)
        report = generate_audit_report(records, "org-1", _START, _END)
        assert report.human_oversight_pct == 0.0

    def test_human_oversight_percentage_mixed(self) -> None:
        human = _make_records(1, custom_metadata={"human_in_loop": True})
        auto = _make_records(3)
        report = generate_audit_report(human + auto, "org-1", _START, _END)
        assert report.human_oversight_pct == 25.0

    def test_risk_distribution_populated(self) -> None:
        records = _make_records(3)  # default conversation = LOW
        report = generate_audit_report(records, "org-1", _START, _END)

        assert report.risk_distribution["low"] == 3
        assert report.risk_distribution["medium"] == 0
        assert report.risk_distribution["high"] == 0
        assert report.risk_distribution["critical"] == 0

    def test_risk_distribution_mixed(self) -> None:
        low = _make_records(2, task_type="conversation")
        medium = _make_records(1, task_type="classification")
        high = _make_records(1, task_type="code_generation", has_tool_calls=True)

        report = generate_audit_report(
            low + medium + high, "org-1", _START, _END
        )
        assert report.risk_distribution["low"] == 2
        assert report.risk_distribution["medium"] == 1
        assert report.risk_distribution["high"] == 1

    def test_attestation_hash_is_64_char_hex(self) -> None:
        records = _make_records(3)
        report = generate_audit_report(records, "org-1", _START, _END)

        assert len(report.attestation_hash) == 64
        int(report.attestation_hash, 16)

    def test_attestation_hash_deterministic(self) -> None:
        """Same records should always produce the same Merkle root."""
        records = _make_records(3)
        a = generate_audit_report(records, "org-1", _START, _END)
        b = generate_audit_report(records, "org-1", _START, _END)
        assert a.attestation_hash == b.attestation_hash

    def test_attestation_hash_matches_manual_merkle(self) -> None:
        """The report's attestation_hash should match a manually built tree."""
        records = _make_records(4)
        report = generate_audit_report(records, "org-1", _START, _END)

        leaf_data = [r.record_hash for r in records]
        tree = MerkleTree(leaf_data)
        assert report.attestation_hash == tree.root

    def test_empty_records_merkle_root(self) -> None:
        report = generate_audit_report([], "org-1", _START, _END)
        assert report.attestation_hash == EMPTY_LEAF

    def test_org_id_is_hashed(self) -> None:
        """Report should contain hashed org_id, not raw."""
        report = generate_audit_report([], "acme-corp", _START, _END)
        assert "acme" not in report.org_id
        assert len(report.org_id) == 64

    def test_period_boundaries_preserved(self) -> None:
        report = generate_audit_report([], "org-1", _START, _END)
        assert report.period_start == _START
        assert report.period_end == _END

    def test_generated_at_is_set(self) -> None:
        report = generate_audit_report([], "org-1", _START, _END)
        assert report.generated_at is not None

    def test_records_included_in_report(self) -> None:
        records = _make_records(5)
        report = generate_audit_report(records, "org-1", _START, _END)
        assert len(report.records) == 5
