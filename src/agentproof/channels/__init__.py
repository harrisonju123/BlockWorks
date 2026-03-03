"""State channels for micropayments between agent operators and tool providers.

Enables off-chain payment updates with millisecond latency, settling
on-chain only when a session ends. This is foundational infrastructure
for the Phase 4 marketplace.

Public API:
    ChannelState        -- current state of a payment channel
    PaymentUpdate       -- a single off-chain payment increment
    ChannelConfig       -- tunable channel parameters
    SettlementProof     -- final proof for on-chain settlement
    ChannelManager      -- core channel lifecycle logic (in-memory)
    ChannelError        -- validation failures raised by the manager
    SessionManager      -- agent session lifecycle integration
    sign_payment        -- create a payment signature
    verify_signature    -- verify a payment signature
"""

from agentproof.channels.manager import ChannelError, ChannelManager
from agentproof.channels.session import SessionManager
from agentproof.channels.signing import sign_payment, verify_signature
from agentproof.channels.types import (
    ChannelConfig,
    ChannelState,
    PaymentUpdate,
    SettlementProof,
)

__all__ = [
    "ChannelConfig",
    "ChannelError",
    "ChannelManager",
    "ChannelState",
    "PaymentUpdate",
    "SessionManager",
    "SettlementProof",
    "sign_payment",
    "verify_signature",
]
