"""Compliance audit trail — immutable, time-stamped records for regulatory reporting.

Generates audit records from llm_events data on-the-fly, classifies risk
levels per EU AI Act tiers, and exports in formats compatible with common
GRC (governance, risk, compliance) tools.

Public API:
    AuditRecord          -- single immutable audit record
    AuditReport          -- period-scoped compliance report with Merkle root
    ComplianceFramework  -- supported regulatory frameworks
    RiskLevel            -- risk classification enum
    DecisionType         -- autonomous vs human-in-the-loop
    build_audit_records  -- query DB and produce audit records
    generate_audit_report -- aggregate records into a report
    export_json          -- JSON export for GRC tools
    export_csv           -- CSV export for spreadsheet analysis
    get_required_fields  -- framework field requirements
    validate_compliance  -- check report against a framework
"""

from agentproof.compliance.builder import (
    build_audit_record_from_row,
    build_audit_records,
    classify_risk,
)
from agentproof.compliance.export import export_csv, export_json
from agentproof.compliance.frameworks import get_required_fields, validate_compliance
from agentproof.compliance.report import generate_audit_report
from agentproof.compliance.types import (
    AuditRecord,
    AuditReport,
    ComplianceFramework,
    DecisionType,
    RiskLevel,
)

__all__ = [
    "AuditRecord",
    "AuditReport",
    "ComplianceFramework",
    "DecisionType",
    "RiskLevel",
    "build_audit_record_from_row",
    "build_audit_records",
    "classify_risk",
    "export_csv",
    "export_json",
    "generate_audit_report",
    "get_required_fields",
    "validate_compliance",
]
