"""Export formats for compliance reports.

Supports JSON (for GRC tool ingestion) and CSV (for spreadsheet analysis).
All exports use hashed identifiers — raw user data never leaves the system.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime

from blockthrough.compliance.types import AuditRecord, AuditReport


def _serialize_datetime(obj: object) -> str:
    """JSON serializer for datetime objects."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def export_json(report: AuditReport) -> str:
    """Export a compliance report as a JSON string.

    Produces a GRC-compatible format with summary stats at the top
    level and individual records nested under 'records'.
    """
    payload = {
        "org_id": report.org_id,
        "period_start": report.period_start.isoformat(),
        "period_end": report.period_end.isoformat(),
        "generated_at": report.generated_at.isoformat() if report.generated_at else None,
        "total_events": report.total_events,
        "human_oversight_pct": report.human_oversight_pct,
        "risk_distribution": report.risk_distribution,
        "attestation_hash": report.attestation_hash,
        "records": [
            {
                "timestamp": r.timestamp.isoformat(),
                "event_id": r.event_id,
                "agent_id": r.agent_id,
                "model": r.model,
                "task_type": r.task_type,
                "decision_type": r.decision_type.value,
                "data_accessed_hash": r.data_accessed_hash,
                "output_hash": r.output_hash,
                "human_oversight": r.human_oversight_flag,
                "risk_level": r.risk_level.value,
                "record_hash": r.record_hash,
            }
            for r in report.records
        ],
    }
    return json.dumps(payload, indent=2, default=_serialize_datetime)


# CSV column order — matches the spec's required export fields
_CSV_COLUMNS = [
    "timestamp",
    "event_id",
    "agent_id",
    "model",
    "task_type",
    "decision_type",
    "risk_level",
    "human_oversight",
    "data_accessed_hash",
    "output_hash",
    "record_hash",
]


def export_csv(records: list[AuditRecord]) -> str:
    """Export audit records as a CSV string.

    Includes all fields needed for compliance review. Hashed identifiers
    only — no raw content is ever included.
    """
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=_CSV_COLUMNS)
    writer.writeheader()

    for r in records:
        writer.writerow({
            "timestamp": r.timestamp.isoformat(),
            "event_id": r.event_id,
            "agent_id": r.agent_id,
            "model": r.model,
            "task_type": r.task_type or "",
            "decision_type": r.decision_type.value,
            "risk_level": r.risk_level.value,
            "human_oversight": str(r.human_oversight_flag),
            "data_accessed_hash": r.data_accessed_hash,
            "output_hash": r.output_hash,
            "record_hash": r.record_hash,
        })

    return output.getvalue()
