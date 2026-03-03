"""EVM attestation provider stub.

Defines the interface for submitting attestations to an EVM-compatible L2
(Base, Optimism, etc.) via web3.py. All methods raise NotImplementedError
until the full EVM integration is built in task 2A-4.

This stub exists so that:
  1. The factory can reference it and validate config early.
  2. The interface is documented alongside the LocalProvider.
  3. Type checkers see a concrete class that satisfies AttestationProvider.
"""

from __future__ import annotations

from datetime import datetime

from agentproof.attestation.provider import AttestationProvider
from agentproof.attestation.types import AttestationRecord

_NOT_IMPLEMENTED_MSG = "EVM provider requires web3.py — implement in 2A-4"


class EVMProvider(AttestationProvider):
    """Stub provider targeting an EVM L2 smart contract.

    Constructor accepts the chain connection parameters so config
    validation happens at startup, even though operations are not yet
    implemented.
    """

    def __init__(
        self,
        rpc_url: str,
        contract_address: str,
        private_key: str,
    ) -> None:
        self._rpc_url = rpc_url
        self._contract_address = contract_address
        self._private_key = private_key

    async def submit(self, record: AttestationRecord) -> str:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    async def batch_submit(self, records: list[AttestationRecord]) -> list[str]:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    async def verify(
        self,
        org_id_hash: str,
        period_start: datetime,
        period_end: datetime,
    ) -> AttestationRecord | None:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    async def get_latest(self, org_id_hash: str) -> AttestationRecord | None:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    async def get_latest_nonce(self, org_id_hash: str) -> int:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)
