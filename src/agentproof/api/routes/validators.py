"""Validator API endpoints.

Exposes registration, listing, task submission, and consensus checking
for decentralized benchmark validators. Backed by in-memory registry
and consensus engine for local development.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agentproof.config import get_config
from agentproof.validators.consensus import ConsensusEngine
from agentproof.validators.economics import ValidatorEconomics
from agentproof.validators.registry import RegistryError, ValidatorRegistry
from agentproof.validators.tasks import TaskDistributor
from agentproof.utils import utcnow
from agentproof.validators.types import ValidationSubmission

router = APIRouter(prefix="/validators")


# ---------------------------------------------------------------------------
# Module-level singletons — lazily initialized on first request.
# Same pattern as channels and attestation modules.
# ---------------------------------------------------------------------------

_registry: ValidatorRegistry | None = None
_distributor: TaskDistributor | None = None
_consensus: ConsensusEngine | None = None
_economics: ValidatorEconomics | None = None


def _init() -> (
    tuple[ValidatorRegistry, TaskDistributor, ConsensusEngine, ValidatorEconomics]
):
    global _registry, _distributor, _consensus, _economics
    if _registry is None:
        cfg = get_config()
        _registry = ValidatorRegistry(min_stake=cfg.validators_min_stake)
        _consensus = ConsensusEngine(
            threshold=cfg.validators_consensus_threshold,
            tolerance=cfg.validators_agreement_tolerance,
        )
        _distributor = TaskDistributor(registry=_registry)
        _economics = ValidatorEconomics(
            registry=_registry, consensus=_consensus
        )
    assert _registry is not None
    assert _distributor is not None
    assert _consensus is not None
    assert _economics is not None
    return _registry, _distributor, _consensus, _economics


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    address: str
    stake_amount: float = Field(gt=0)


class ValidatorResponse(BaseModel):
    address: str
    stake_amount: float
    registered_at: datetime
    is_active: bool
    total_validations: int
    accuracy_score: float
    cumulative_rewards: float
    cumulative_slashes: float


class ValidatorListResponse(BaseModel):
    validators: list[ValidatorResponse]
    count: int


class SubmitValidationRequest(BaseModel):
    validator_address: str
    quality_score: float = Field(ge=0.0, le=1.0)
    judge_model: str = "claude-haiku-4-5-20251001"
    signature: str = ""


class SubmitValidationResponse(BaseModel):
    accepted: bool
    task_id: str
    validator_address: str


class ConsensusResponse(BaseModel):
    task_id: str
    agreed_score: float | None
    consensus_reached: bool
    submission_count: int
    agreement_threshold: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/register", response_model=ValidatorResponse, status_code=201)
async def register_validator(body: RegisterRequest) -> ValidatorResponse:
    """Register a new validator with the given stake."""
    registry, _, _, _ = _init()
    try:
        info = registry.register(body.address, body.stake_amount)
    except RegistryError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return ValidatorResponse(
        address=info.address,
        stake_amount=info.stake_amount,
        registered_at=info.registered_at,
        is_active=info.is_active,
        total_validations=info.total_validations,
        accuracy_score=info.accuracy_score,
        cumulative_rewards=info.cumulative_rewards,
        cumulative_slashes=info.cumulative_slashes,
    )


@router.get("", response_model=ValidatorListResponse)
async def list_validators(active_only: bool = False) -> ValidatorListResponse:
    """List all registered validators."""
    registry, _, _, _ = _init()
    if active_only:
        validators = registry.get_active_validators()
    else:
        validators = registry.get_all_validators()

    items = [
        ValidatorResponse(
            address=v.address,
            stake_amount=v.stake_amount,
            registered_at=v.registered_at,
            is_active=v.is_active,
            total_validations=v.total_validations,
            accuracy_score=v.accuracy_score,
            cumulative_rewards=v.cumulative_rewards,
            cumulative_slashes=v.cumulative_slashes,
        )
        for v in validators
    ]

    return ValidatorListResponse(validators=items, count=len(items))


@router.get("/{address}", response_model=ValidatorResponse)
async def get_validator(address: str) -> ValidatorResponse:
    """Get info for a specific validator."""
    registry, _, _, _ = _init()
    info = registry.get_validator(address)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Validator {address} not found")

    return ValidatorResponse(
        address=info.address,
        stake_amount=info.stake_amount,
        registered_at=info.registered_at,
        is_active=info.is_active,
        total_validations=info.total_validations,
        accuracy_score=info.accuracy_score,
        cumulative_rewards=info.cumulative_rewards,
        cumulative_slashes=info.cumulative_slashes,
    )


@router.post(
    "/tasks/{task_id}/submit",
    response_model=SubmitValidationResponse,
)
async def submit_validation(
    task_id: str, body: SubmitValidationRequest
) -> SubmitValidationResponse:
    """Submit a validation score for a task."""
    _, distributor, consensus, economics = _init()

    task = distributor.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    submission = ValidationSubmission(
        task_id=task_id,
        validator_address=body.validator_address,
        quality_score=body.quality_score,
        judge_model=body.judge_model,
        submitted_at=utcnow(),
        signature=body.signature,
    )

    accepted = consensus.submit_validation(submission)
    if not accepted:
        raise HTTPException(
            status_code=409,
            detail=f"Validator {body.validator_address} already submitted for task {task_id}",
        )

    # Auto-settle if consensus is reached after this submission
    result = consensus.check_consensus(task_id)
    if result.consensus_reached:
        try:
            economics.settle_task(task_id)
        except Exception:
            # Settlement failure shouldn't block the submission response
            pass

    return SubmitValidationResponse(
        accepted=True,
        task_id=task_id,
        validator_address=body.validator_address,
    )


@router.get("/tasks/{task_id}/consensus", response_model=ConsensusResponse)
async def check_consensus(task_id: str) -> ConsensusResponse:
    """Check the consensus status for a validation task."""
    _, _, consensus, _ = _init()

    result = consensus.check_consensus(task_id)

    return ConsensusResponse(
        task_id=result.task_id,
        agreed_score=result.agreed_score,
        consensus_reached=result.consensus_reached,
        submission_count=len(result.submissions),
        agreement_threshold=result.agreement_threshold,
    )


def reset_state() -> None:
    """Reset all module-level state. Used by tests for clean state."""
    global _registry, _distributor, _consensus, _economics
    _registry = None
    _distributor = None
    _consensus = None
    _economics = None
