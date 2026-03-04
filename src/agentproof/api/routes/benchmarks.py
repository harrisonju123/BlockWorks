"""Benchmark endpoints: fitness matrix, results, config, drift, and accountability."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentproof.api.deps import get_db
from agentproof.benchmarking.accountability import (
    AccountabilityReport,
    generate_report,
)
from agentproof.benchmarking.drift import detect_drift
from agentproof.benchmarking.types import BenchmarkResult, FitnessEntry
from agentproof.config import get_config
from agentproof.db.queries import (
    get_benchmark_config_from_db,
    get_benchmark_results,
    get_fitness_matrix,
    upsert_benchmark_config,
)

router = APIRouter(prefix="/benchmarks")


# -- Response schemas ----------------------------------------------------------


class FitnessMatrixResponse(BaseModel):
    entries: list[FitnessEntry]


class BenchmarkResultsResponse(BaseModel):
    results: list[BenchmarkResult]
    total_count: int
    has_more: bool


class BenchmarkConfigResponse(BaseModel):
    enabled: bool
    sample_rate: float
    benchmark_models: list[str]
    judge_model: str
    enabled_task_types: list[str]


class BenchmarkConfigUpdate(BaseModel):
    enabled: bool | None = None
    sample_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    benchmark_models: list[str] | None = None
    judge_model: str | None = None
    enabled_task_types: list[str] | None = None


class DriftItemResponse(BaseModel):
    model: str
    task_type: str
    baseline_quality: float
    current_quality: float
    delta_pct: float
    p_value: float
    confidence_interval: tuple[float, float]
    baseline_sample_size: int
    current_sample_size: int
    first_detected_at: datetime


class DriftResponse(BaseModel):
    drifts: list[DriftItemResponse]
    models_checked: int
    drifts_found: int


class AccountabilityDriftItem(BaseModel):
    model: str
    task_type: str
    baseline_quality: float
    current_quality: float
    delta_pct: float
    p_value: float
    confidence_interval: tuple[float, float]
    baseline_sample_size: int
    current_sample_size: int
    call_volume: int
    avg_cost_per_call: float
    estimated_cost_impact: float


class AccountabilityReportResponse(BaseModel):
    org_id: str
    generated_at: datetime
    drift_items: list[AccountabilityDriftItem]
    estimated_total_cost_impact: float
    attestation_hash: str


class AccountabilityReportsListResponse(BaseModel):
    reports: list[AccountabilityReportResponse]
    total_count: int


class GenerateReportRequest(BaseModel):
    org_id: str
    models: list[str] | None = None
    lookback_days: int = Field(default=30, ge=7, le=90)


# -- Helpers -------------------------------------------------------------------


def _config_from_row(row: dict, env_cfg) -> BenchmarkConfigResponse:
    """Build a BenchmarkConfigResponse from a DB row + env config for 'enabled'."""
    return BenchmarkConfigResponse(
        enabled=env_cfg.benchmark_enabled,
        sample_rate=float(row["sample_rate"]),
        benchmark_models=list(row["benchmark_models"]),
        judge_model=row["judge_model"],
        enabled_task_types=list(row["enabled_task_types"]),
    )


# -- Endpoints -----------------------------------------------------------------


@router.get("/fitness-matrix", response_model=FitnessMatrixResponse)
async def fitness_matrix(
    org_id: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> FitnessMatrixResponse:
    """Returns the fitness matrix: per (model, task_type) quality/cost/latency."""
    entries = await get_fitness_matrix(db, org_id)
    return FitnessMatrixResponse(entries=entries)


@router.get("/results", response_model=BenchmarkResultsResponse)
async def benchmark_results(
    org_id: str | None = None,
    task_type: str | None = None,
    benchmark_model: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> BenchmarkResultsResponse:
    """Paginated benchmark results with optional filters."""
    rows, total_count = await get_benchmark_results(
        db,
        org_id=org_id,
        task_type=task_type,
        benchmark_model=benchmark_model,
        limit=limit,
        offset=offset,
    )

    results = [
        BenchmarkResult(
            id=row["id"],
            created_at=row["created_at"],
            original_event_id=row["original_event_id"],
            original_model=row["original_model"],
            benchmark_model=row["benchmark_model"],
            task_type=row["task_type"],
            quality_score=float(row["quality_score"]),
            original_cost=float(row["original_cost"]),
            benchmark_cost=float(row["benchmark_cost"]),
            original_latency_ms=float(row["original_latency_ms"]),
            benchmark_latency_ms=float(row["benchmark_latency_ms"]),
            judge_model=row["judge_model"],
            rubric_version=row["rubric_version"],
            org_id=row["org_id"],
        )
        for row in rows
    ]

    return BenchmarkResultsResponse(
        results=results,
        total_count=total_count,
        has_more=(offset + limit) < total_count,
    )


@router.get("/config", response_model=BenchmarkConfigResponse)
async def get_benchmark_config(
    db: AsyncSession = Depends(get_db),
) -> BenchmarkConfigResponse:
    """Returns the current benchmark configuration from the DB."""
    cfg = get_config()
    row = await get_benchmark_config_from_db(db)
    if row is None:
        # No DB row yet — return env-var defaults
        return BenchmarkConfigResponse(
            enabled=cfg.benchmark_enabled,
            sample_rate=cfg.benchmark_sample_rate,
            benchmark_models=cfg.benchmark_models,
            judge_model=cfg.benchmark_judge_model,
            enabled_task_types=[],
        )
    return _config_from_row(row, cfg)


@router.post("/config", response_model=BenchmarkConfigResponse)
async def update_benchmark_config(
    update: BenchmarkConfigUpdate,
    db: AsyncSession = Depends(get_db),
) -> BenchmarkConfigResponse:
    """Update the benchmark configuration.

    Only the fields present in the request body are updated.
    Changes persist across restarts via the benchmark_config DB table.
    """
    row = await upsert_benchmark_config(
        db,
        sample_rate=update.sample_rate,
        benchmark_models=update.benchmark_models,
        judge_model=update.judge_model,
        enabled_task_types=update.enabled_task_types,
    )
    cfg = get_config()
    return _config_from_row(row, cfg)


# -- In-memory report store (replaced by DB in a future PR) -------------------
_generated_reports: list[AccountabilityReport] = []


@router.get("/drift", response_model=DriftResponse)
async def get_drift(
    models: str | None = Query(default=None, description="Comma-separated model names"),
    lookback_days: int = Query(default=30, ge=7, le=90),
    db: AsyncSession = Depends(get_db),
) -> DriftResponse:
    """Run drift detection across benchmark results.

    Returns all (model, task_type) pairs where quality has degraded
    significantly vs the baseline period.
    """
    model_list = [m.strip() for m in models.split(",")] if models else None
    reports = await detect_drift(db, models=model_list, lookback_days=lookback_days)

    items = [
        DriftItemResponse(
            model=r.model,
            task_type=r.task_type,
            baseline_quality=r.baseline_quality,
            current_quality=r.current_quality,
            delta_pct=r.delta_pct,
            p_value=r.p_value,
            confidence_interval=r.confidence_interval,
            baseline_sample_size=r.baseline_sample_size,
            current_sample_size=r.current_sample_size,
            first_detected_at=r.first_detected_at,
        )
        for r in reports
    ]

    return DriftResponse(
        drifts=items,
        models_checked=len(set((r.model, r.task_type) for r in reports)) if reports else 0,
        drifts_found=len(reports),
    )


@router.get("/accountability-reports", response_model=AccountabilityReportsListResponse)
async def list_accountability_reports(
    org_id: str | None = None,
) -> AccountabilityReportsListResponse:
    """List previously generated accountability reports."""
    filtered = _generated_reports
    if org_id:
        filtered = [r for r in _generated_reports if r.org_id == org_id]

    responses = [
        AccountabilityReportResponse(
            org_id=r.org_id,
            generated_at=r.generated_at,
            drift_items=[
                AccountabilityDriftItem(
                    model=item.model,
                    task_type=item.task_type,
                    baseline_quality=item.baseline_quality,
                    current_quality=item.current_quality,
                    delta_pct=item.delta_pct,
                    p_value=item.p_value,
                    confidence_interval=item.confidence_interval,
                    baseline_sample_size=item.baseline_sample_size,
                    current_sample_size=item.current_sample_size,
                    call_volume=item.call_volume,
                    avg_cost_per_call=item.avg_cost_per_call,
                    estimated_cost_impact=item.estimated_cost_impact,
                )
                for item in r.drift_items
            ],
            estimated_total_cost_impact=r.estimated_total_cost_impact,
            attestation_hash=r.attestation_hash,
        )
        for r in filtered
    ]

    return AccountabilityReportsListResponse(
        reports=responses,
        total_count=len(responses),
    )


@router.post(
    "/accountability-reports/generate",
    response_model=AccountabilityReportResponse,
)
async def generate_accountability_report(
    body: GenerateReportRequest,
    db: AsyncSession = Depends(get_db),
) -> AccountabilityReportResponse:
    """Trigger drift detection + accountability report generation.

    Runs drift detection for the requested org/models, computes cost impact,
    and anchors the report hash on-chain.
    """
    drifts = await detect_drift(
        db, models=body.models, lookback_days=body.lookback_days,
    )

    report = await generate_report(db, drifts, body.org_id)
    _generated_reports.append(report)

    return AccountabilityReportResponse(
        org_id=report.org_id,
        generated_at=report.generated_at,
        drift_items=[
            AccountabilityDriftItem(
                model=item.model,
                task_type=item.task_type,
                baseline_quality=item.baseline_quality,
                current_quality=item.current_quality,
                delta_pct=item.delta_pct,
                p_value=item.p_value,
                confidence_interval=item.confidence_interval,
                baseline_sample_size=item.baseline_sample_size,
                current_sample_size=item.current_sample_size,
                call_volume=item.call_volume,
                avg_cost_per_call=item.avg_cost_per_call,
                estimated_cost_impact=item.estimated_cost_impact,
            )
            for item in report.drift_items
        ],
        estimated_total_cost_impact=report.estimated_total_cost_impact,
        attestation_hash=report.attestation_hash,
    )
