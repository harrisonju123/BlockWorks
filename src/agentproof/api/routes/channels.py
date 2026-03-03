"""State channel API endpoints.

Exposes channel open, payment, close, and listing operations.
Backed by the in-memory ChannelManager for local development.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agentproof.channels.manager import ChannelError, ChannelManager
from agentproof.channels.types import ChannelConfig
from agentproof.config import get_config

router = APIRouter(prefix="/channels")


# ---------------------------------------------------------------------------
# Module-level manager singleton — lazily initialized on first request.
# ---------------------------------------------------------------------------

_manager: ChannelManager | None = None


def _get_manager() -> ChannelManager:
    global _manager
    if _manager is None:
        cfg = get_config()
        channel_config = ChannelConfig(
            max_channel_duration_s=cfg.channels_max_duration_s,
            min_deposit=cfg.channels_min_deposit,
        )
        _manager = ChannelManager(config=channel_config)
    return _manager


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class OpenChannelRequest(BaseModel):
    sender: str
    receiver: str
    deposit: float = Field(gt=0)
    sender_key: str = "default-sender-key"


class OpenChannelResponse(BaseModel):
    channel_id: str
    sender: str
    receiver: str
    deposit_amount: float
    is_open: bool


class PaymentRequest(BaseModel):
    amount: float = Field(gt=0)


class PaymentResponse(BaseModel):
    channel_id: str
    amount: float
    nonce: int
    sender_signature: str


class CloseChannelResponse(BaseModel):
    channel_id: str
    final_nonce: int
    final_amount: float
    sender_sig: str
    receiver_sig: str


class CloseChannelRequest(BaseModel):
    receiver_key: str = "default-receiver-key"


class ChannelResponse(BaseModel):
    channel_id: str
    sender: str
    receiver: str
    deposit_amount: float
    spent_amount: float
    nonce: int
    is_open: bool


class ChannelListResponse(BaseModel):
    channels: list[ChannelResponse]
    count: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/open", response_model=OpenChannelResponse, status_code=201)
async def open_channel(body: OpenChannelRequest) -> OpenChannelResponse:
    """Open a new payment channel with a deposit."""
    mgr = _get_manager()
    try:
        state = mgr.open_channel(
            sender=body.sender,
            receiver=body.receiver,
            deposit=body.deposit,
            sender_key=body.sender_key,
        )
    except ChannelError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return OpenChannelResponse(
        channel_id=state.channel_id,
        sender=state.sender,
        receiver=state.receiver,
        deposit_amount=state.deposit_amount,
        is_open=state.is_open,
    )


@router.post("/{channel_id}/pay", response_model=PaymentResponse)
async def make_payment(channel_id: str, body: PaymentRequest) -> PaymentResponse:
    """Make an off-chain payment on an open channel."""
    mgr = _get_manager()
    try:
        update = mgr.create_payment(channel_id, body.amount)
    except ChannelError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return PaymentResponse(
        channel_id=update.channel_id,
        amount=update.amount,
        nonce=update.nonce,
        sender_signature=update.sender_signature,
    )


@router.post("/{channel_id}/close", response_model=CloseChannelResponse)
async def close_channel(
    channel_id: str,
    body: CloseChannelRequest | None = None,
) -> CloseChannelResponse:
    """Close a channel and get the settlement proof."""
    mgr = _get_manager()
    receiver_key = body.receiver_key if body else "default-receiver-key"
    try:
        proof = mgr.close_channel(channel_id, receiver_key=receiver_key)
    except ChannelError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return CloseChannelResponse(
        channel_id=proof.channel_id,
        final_nonce=proof.final_nonce,
        final_amount=proof.final_amount,
        sender_sig=proof.sender_sig,
        receiver_sig=proof.receiver_sig,
    )


@router.get("/{channel_id}", response_model=ChannelResponse)
async def get_channel(channel_id: str) -> ChannelResponse:
    """Get the current state of a channel."""
    mgr = _get_manager()
    try:
        state = mgr.get_channel(channel_id)
    except ChannelError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return ChannelResponse(
        channel_id=state.channel_id,
        sender=state.sender,
        receiver=state.receiver,
        deposit_amount=state.deposit_amount,
        spent_amount=state.spent_amount,
        nonce=state.nonce,
        is_open=state.is_open,
    )


@router.get("", response_model=ChannelListResponse)
async def list_channels(address: str | None = None) -> ChannelListResponse:
    """List channels, optionally filtered by sender/receiver address."""
    mgr = _get_manager()
    if address:
        channels = mgr.get_channels_for(address)
    else:
        # Return all open channels
        channels = [ch for ch in mgr._channels.values() if ch.is_open]

    items = [
        ChannelResponse(
            channel_id=ch.channel_id,
            sender=ch.sender,
            receiver=ch.receiver,
            deposit_amount=ch.deposit_amount,
            spent_amount=ch.spent_amount,
            nonce=ch.nonce,
            is_open=ch.is_open,
        )
        for ch in channels
    ]

    return ChannelListResponse(channels=items, count=len(items))


def reset_manager() -> None:
    """Reset the module-level manager. Used by tests for clean state."""
    global _manager
    _manager = None
