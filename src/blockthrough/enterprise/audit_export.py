"""Enterprise-specific audit export building on the compliance module.

Wraps the existing compliance export_json / export_csv functions with
tenant metadata, compliance validation results, and scheduling config.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from blockthrough.compliance.export import export_csv, export_json
from blockthrough.compliance.frameworks import validate_compliance
from blockthrough.compliance.types import (
    AuditRecord,
    AuditReport,
    ComplianceFramework,
)
from blockthrough.enterprise.tenants import get_tenant
from blockthrough.utils import utcnow


class AuditExportConfig(BaseModel):
    """Configuration for a scheduled audit export job."""

    tenant_id: str
    frequency: str  # "daily", "weekly", "monthly"
    framework: ComplianceFramework
    destination: str  # e.g., "s3://bucket/path" or "email:admin@co.com"
    created_at: datetime


# In-memory schedule store: tenant_id -> list of export configs
_export_schedules: dict[str, list[AuditExportConfig]] = {}


def export_tenant_audit(
    tenant_id: str,
    report: AuditReport,
    framework: ComplianceFramework,
    fmt: str = "json",
) -> bytes:
    """Generate a tenant-scoped audit export with metadata envelope.

    Includes tenant info, compliance validation results, and the full
    audit report in the requested format (JSON or CSV).
    """
    tenant = get_tenant(tenant_id)
    tenant_meta = {
        "tenant_id": tenant_id,
        "tenant_name": tenant.name if tenant else "unknown",
        "tenant_plan": tenant.plan.value if tenant else "unknown",
    }

    violations = validate_compliance(report, framework)

    if fmt == "csv":
        # CSV format: prepend a comment header with metadata, then the records
        csv_body = export_csv(report.records)
        header_lines = [
            f"# Tenant: {tenant_meta['tenant_name']} ({tenant_id})",
            f"# Framework: {framework.value}",
            f"# Violations: {len(violations)}",
            f"# Period: {report.period_start.isoformat()} to {report.period_end.isoformat()}",
            f"# Attestation Hash: {report.attestation_hash}",
            "",
        ]
        return ("\n".join(header_lines) + csv_body).encode("utf-8")

    # Default: JSON envelope wrapping the compliance export
    report_json = json.loads(export_json(report))
    envelope: dict[str, Any] = {
        "tenant": tenant_meta,
        "framework": framework.value,
        "compliance": {
            "is_compliant": len(violations) == 0,
            "violations": violations,
        },
        "report": report_json,
    }
    return json.dumps(envelope, indent=2).encode("utf-8")


def schedule_audit_export(
    tenant_id: str,
    frequency: str,
    framework: ComplianceFramework,
    destination: str,
) -> AuditExportConfig:
    """Register a scheduled audit export for a tenant.

    Stores the config in memory. A real implementation would persist this
    and hook into a task scheduler (e.g., Celery beat, APScheduler).
    """
    config = AuditExportConfig(
        tenant_id=tenant_id,
        frequency=frequency,
        framework=framework,
        destination=destination,
        created_at=utcnow(),
    )

    if tenant_id not in _export_schedules:
        _export_schedules[tenant_id] = []
    _export_schedules[tenant_id].append(config)

    return config


def get_export_schedules(tenant_id: str) -> list[AuditExportConfig]:
    """Return all scheduled exports for a tenant."""
    return _export_schedules.get(tenant_id, [])


def reset_store() -> None:
    """Clear in-memory state. Used by tests."""
    _export_schedules.clear()
