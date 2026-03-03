"""Revenue sharing API endpoints.

Exposes split calculation, settlement execution, earnings queries,
and protocol-level stats. Backed by the in-memory SettlementEngine
for local development.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agentproof.config import get_config
from agentproof.revenue.calculator import SplitCalculationError, calculate_shares
from agentproof.revenue.settlement import SettlementEngine, SettlementError
from agentproof.revenue.types import RevenueConfig, SplitBasis, SplitRule

router = APIRouter(prefix="/revenue")


# ---------------------------------------------------------------------------
# Module-level engine singleton — lazily initialized on first request.
# ---------------------------------------------------------------------------

_engine: SettlementEngine | None = None


def _get_engine() -> SettlementEngine:
    global _engine
    if _engine is None:
        cfg = get_config()
        revenue_config = RevenueConfig(
            protocol_fee_pct=cfg.revenue_protocol_fee_pct,
            burn_pct=cfg.revenue_burn_pct,
            min_settlement=cfg.revenue_min_settlement,
        )
        _engine = SettlementEngine(config=revenue_config)
    return _engine


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class SplitRuleRequest(BaseModel):
    participant_id: str
    basis: SplitBasis
    weight: float = Field(gt=0)


class CalculateRequest(BaseModel):
    execution_id: str
    execution_cost: float = Field(ge=0)
    split_rules: list[SplitRuleRequest]
    protocol_fee_pct: float | None = None


class ShareResponse(BaseModel):
    participant_id: str
    share_pct: float
    amount_usd: float
    settled: bool


class ProtocolFeeResponse(BaseModel):
    fee_pct: float
    fee_amount: float
    burn_amount: float


class CalculateResponse(BaseModel):
    execution_id: str
    shares: list[ShareResponse]
    protocol_fee: ProtocolFeeResponse
    distributable_amount: float


class SettleRequest(BaseModel):
    execution_id: str
    execution_cost: float = Field(ge=0)
    split_rules: list[SplitRuleRequest]


class SettlementResponse(BaseModel):
    settlement_id: str
    execution_id: str
    shares: list[ShareResponse]
    protocol_fee: ProtocolFeeResponse
    total_amount: float
    settlement_hash: str
    settled_at: str


class EarningsResponse(BaseModel):
    participant_id: str
    total_earnings_usd: float


class ProtocolStatsResponse(BaseModel):
    total_settlements: int
    total_fees_collected: float
    total_burned: float
    total_volume: float


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/calculate", response_model=CalculateResponse)
async def calculate_split(body: CalculateRequest) -> CalculateResponse:
    """Preview revenue split for a workflow without executing settlement."""
    cfg = get_config()
    fee_pct = body.protocol_fee_pct if body.protocol_fee_pct is not None else cfg.revenue_protocol_fee_pct

    rules = [
        SplitRule(
            participant_id=r.participant_id,
            basis=r.basis,
            weight=r.weight,
        )
        for r in body.split_rules
    ]

    try:
        shares, protocol_fee = calculate_shares(
            execution_id=body.execution_id,
            execution_cost=body.execution_cost,
            split_rules=rules,
            protocol_fee_pct=fee_pct,
            burn_pct=cfg.revenue_burn_pct,
        )
    except SplitCalculationError as e:
        raise HTTPException(status_code=422, detail=str(e))

    distributable = body.execution_cost - protocol_fee.fee_amount

    return CalculateResponse(
        execution_id=body.execution_id,
        shares=[
            ShareResponse(
                participant_id=s.participant_id,
                share_pct=s.share_pct,
                amount_usd=s.amount_usd,
                settled=s.settled,
            )
            for s in shares
        ],
        protocol_fee=ProtocolFeeResponse(
            fee_pct=protocol_fee.fee_pct,
            fee_amount=protocol_fee.fee_amount,
            burn_amount=protocol_fee.burn_amount,
        ),
        distributable_amount=round(distributable, 8),
    )


@router.post("/settle", response_model=SettlementResponse, status_code=201)
async def settle_execution(body: SettleRequest) -> SettlementResponse:
    """Execute settlement for a workflow execution."""
    engine = _get_engine()
    cfg = get_config()

    rules = [
        SplitRule(
            participant_id=r.participant_id,
            basis=r.basis,
            weight=r.weight,
        )
        for r in body.split_rules
    ]

    try:
        shares, protocol_fee = calculate_shares(
            execution_id=body.execution_id,
            execution_cost=body.execution_cost,
            split_rules=rules,
            protocol_fee_pct=cfg.revenue_protocol_fee_pct,
            burn_pct=cfg.revenue_burn_pct,
        )
    except SplitCalculationError as e:
        raise HTTPException(status_code=422, detail=str(e))

    try:
        settlement = engine.settle(
            execution_id=body.execution_id,
            shares=shares,
            protocol_fee=protocol_fee,
            total_amount=body.execution_cost,
        )
    except SettlementError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return SettlementResponse(
        settlement_id=settlement.id,
        execution_id=settlement.execution_id,
        shares=[
            ShareResponse(
                participant_id=s.participant_id,
                share_pct=s.share_pct,
                amount_usd=s.amount_usd,
                settled=s.settled,
            )
            for s in settlement.shares
        ],
        protocol_fee=ProtocolFeeResponse(
            fee_pct=settlement.protocol_fee.fee_pct,
            fee_amount=settlement.protocol_fee.fee_amount,
            burn_amount=settlement.protocol_fee.burn_amount,
        ),
        total_amount=settlement.total_amount,
        settlement_hash=settlement.settlement_hash,
        settled_at=settlement.settled_at.isoformat() if settlement.settled_at else "",
    )


@router.get("/earnings/{participant_id}", response_model=EarningsResponse)
async def get_earnings(participant_id: str) -> EarningsResponse:
    """Get cumulative earnings for a participant."""
    engine = _get_engine()
    total = engine.get_earnings(participant_id)
    return EarningsResponse(
        participant_id=participant_id,
        total_earnings_usd=total,
    )


@router.get("/settlements/{settlement_id}", response_model=SettlementResponse)
async def get_settlement(settlement_id: str) -> SettlementResponse:
    """Get details of a specific settlement."""
    engine = _get_engine()
    settlement = engine.get_settlement(settlement_id)
    if settlement is None:
        raise HTTPException(status_code=404, detail="Settlement not found")

    return SettlementResponse(
        settlement_id=settlement.id,
        execution_id=settlement.execution_id,
        shares=[
            ShareResponse(
                participant_id=s.participant_id,
                share_pct=s.share_pct,
                amount_usd=s.amount_usd,
                settled=s.settled,
            )
            for s in settlement.shares
        ],
        protocol_fee=ProtocolFeeResponse(
            fee_pct=settlement.protocol_fee.fee_pct,
            fee_amount=settlement.protocol_fee.fee_amount,
            burn_amount=settlement.protocol_fee.burn_amount,
        ),
        total_amount=settlement.total_amount,
        settlement_hash=settlement.settlement_hash,
        settled_at=settlement.settled_at.isoformat() if settlement.settled_at else "",
    )


@router.get("/protocol-stats", response_model=ProtocolStatsResponse)
async def get_protocol_stats() -> ProtocolStatsResponse:
    """Get aggregate protocol stats: total fees, burns, and settlement volume."""
    engine = _get_engine()
    stats = engine.get_protocol_stats()
    return ProtocolStatsResponse(**stats)


def reset_engine() -> None:
    """Reset the module-level engine. Used by tests for clean state."""
    global _engine
    _engine = None
