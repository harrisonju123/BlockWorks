"""Tests for export formats — JSON and CSV structure validation.

Ensures exports contain the right fields, use hashed identifiers,
and parse correctly in their respective formats.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from uuid import uuid4

from blockthrough.compliance.builder import build_audit_record_from_row
from blockthrough.compliance.export import export_csv, export_json
from blockthrough.compliance.report import generate_audit_report


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


class TestExportJson:
    """JSON export format validation."""

    def test_valid_json(self) -> None:
        records = _make_records(3)
        report = generate_audit_report(records, "org-1", _START, _END)
        result = export_json(report)
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_top_level_fields(self) -> None:
        records = _make_records(2)
        report = generate_audit_report(records, "org-1", _START, _END)
        parsed = json.loads(export_json(report))

        assert "org_id" in parsed
        assert "period_start" in parsed
        assert "period_end" in parsed
        assert "total_events" in parsed
        assert "human_oversight_pct" in parsed
        assert "risk_distribution" in parsed
        assert "attestation_hash" in parsed
        assert "records" in parsed

    def test_record_count_matches(self) -> None:
        records = _make_records(5)
        report = generate_audit_report(records, "org-1", _START, _END)
        parsed = json.loads(export_json(report))
        assert len(parsed["records"]) == 5

    def test_record_fields(self) -> None:
        records = _make_records(1)
        report = generate_audit_report(records, "org-1", _START, _END)
        parsed = json.loads(export_json(report))
        rec = parsed["records"][0]

        expected_fields = {
            "timestamp", "event_id", "agent_id", "model",
            "task_type", "decision_type", "data_accessed_hash",
            "output_hash", "human_oversight", "risk_level", "record_hash",
        }
        assert set(rec.keys()) == expected_fields

    def test_hashed_identifiers_only(self) -> None:
        """No raw identifiers should appear in the export."""
        records = _make_records(1, agent_name="secret-agent-name")
        report = generate_audit_report(records, "secret-org", _START, _END)
        result = export_json(report)

        assert "secret-agent-name" not in result
        assert "secret-org" not in result

    def test_empty_report(self) -> None:
        report = generate_audit_report([], "org-1", _START, _END)
        parsed = json.loads(export_json(report))
        assert parsed["total_events"] == 0
        assert parsed["records"] == []

    def test_risk_distribution_in_json(self) -> None:
        records = _make_records(3)
        report = generate_audit_report(records, "org-1", _START, _END)
        parsed = json.loads(export_json(report))

        dist = parsed["risk_distribution"]
        assert isinstance(dist, dict)
        assert "low" in dist


class TestExportCsv:
    """CSV export format validation."""

    def test_valid_csv(self) -> None:
        records = _make_records(3)
        result = export_csv(records)
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        # header + 3 data rows
        assert len(rows) == 4

    def test_csv_header_columns(self) -> None:
        records = _make_records(1)
        result = export_csv(records)
        reader = csv.reader(io.StringIO(result))
        header = next(reader)

        expected = [
            "timestamp", "event_id", "agent_id", "model",
            "task_type", "decision_type", "risk_level",
            "human_oversight", "data_accessed_hash",
            "output_hash", "record_hash",
        ]
        assert header == expected

    def test_csv_row_count(self) -> None:
        records = _make_records(5)
        result = export_csv(records)
        reader = csv.DictReader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 5

    def test_csv_field_values(self) -> None:
        records = _make_records(1, task_type="classification")
        result = export_csv(records)
        reader = csv.DictReader(io.StringIO(result))
        row = next(reader)

        assert row["model"] == "gpt-4o"
        assert row["task_type"] == "classification"
        assert row["risk_level"] == "medium"
        assert row["human_oversight"] == "False"

    def test_csv_hashed_identifiers(self) -> None:
        records = _make_records(1, agent_name="my-agent")
        result = export_csv(records)
        assert "my-agent" not in result

    def test_empty_records(self) -> None:
        result = export_csv([])
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        # header only
        assert len(rows) == 1

    def test_none_task_type_exported_as_empty(self) -> None:
        records = _make_records(1, task_type=None)
        result = export_csv(records)
        reader = csv.DictReader(io.StringIO(result))
        row = next(reader)
        assert row["task_type"] == ""

    def test_csv_parseable_by_dictreader(self) -> None:
        """Ensure DictReader can consume the output without errors."""
        records = _make_records(10)
        result = export_csv(records)
        reader = csv.DictReader(io.StringIO(result))
        for row in reader:
            assert row["event_id"]  # non-empty
            assert row["record_hash"]  # non-empty
