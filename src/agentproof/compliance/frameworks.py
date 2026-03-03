"""Framework-specific compliance validation.

Each regulatory framework requires specific fields to be present and
non-empty in audit records. validate_compliance() checks a report
against a framework's requirements and returns any violations found.
"""

from __future__ import annotations

from agentproof.compliance.types import AuditReport, ComplianceFramework

# Required fields per framework — maps to AuditRecord attributes.
# These are the minimum fields each framework mandates for an audit trail.
_FRAMEWORK_REQUIRED_FIELDS: dict[ComplianceFramework, list[str]] = {
    ComplianceFramework.EU_AI_ACT: [
        "risk_level",
        "human_oversight_flag",
        "decision_type",
        "timestamp",
    ],
    ComplianceFramework.SOC2: [
        "timestamp",
        "agent_id",
        "data_accessed_hash",
        "output_hash",
    ],
    ComplianceFramework.HIPAA: [
        "timestamp",
        "data_accessed_hash",
        "human_oversight_flag",
        "risk_level",
    ],
    ComplianceFramework.FINANCIAL_SERVICES: [
        "timestamp",
        "agent_id",
        "model",
        "risk_level",
        "decision_type",
        "data_accessed_hash",
        "output_hash",
        "human_oversight_flag",
    ],
}


def get_required_fields(framework: ComplianceFramework) -> list[str]:
    """Return the list of required audit record fields for a framework."""
    return list(_FRAMEWORK_REQUIRED_FIELDS[framework])


def validate_compliance(
    report: AuditReport,
    framework: ComplianceFramework,
) -> list[str]:
    """Check a report against a framework's requirements.

    Returns a list of violation messages. An empty list means the
    report is compliant. Checks both record-level field presence
    and report-level completeness.
    """
    violations: list[str] = []
    required = _FRAMEWORK_REQUIRED_FIELDS[framework]

    if not report.records:
        violations.append("Report contains no audit records")
        return violations

    if not report.attestation_hash:
        violations.append("Report is missing attestation_hash (Merkle root)")

    for i, record in enumerate(report.records):
        for field in required:
            value = getattr(record, field, None)
            if value is None or value == "":
                violations.append(
                    f"Record {i} missing required field '{field}' "
                    f"for {framework.value}"
                )

    # Framework-specific checks beyond field presence
    if framework == ComplianceFramework.EU_AI_ACT:
        # EU AI Act requires documented risk assessment
        if "critical" not in report.risk_distribution and "high" not in report.risk_distribution:
            # Not a violation per se, but if risk dist is empty that's a problem
            if not any(v > 0 for v in report.risk_distribution.values()):
                violations.append(
                    "EU AI Act requires risk distribution to be computed"
                )

    if framework == ComplianceFramework.HIPAA:
        # HIPAA requires human oversight percentage to be tracked
        if report.human_oversight_pct is None:
            violations.append(
                "HIPAA requires human oversight percentage to be tracked"
            )

    return violations
