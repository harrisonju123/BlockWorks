"""EVM attestation provider — submits attestations to an on-chain contract.

Uses web3.py 7+ async API to interact with AgentProofAttestation.sol on
any EVM-compatible chain (Anvil local, Base L2, etc.). Embeds a minimal ABI
covering the 5 contract functions we call, avoiding a file dependency on
Foundry's compiled output.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from agentproof.attestation.provider import AttestationError, AttestationProvider
from agentproof.attestation.types import AttestationRecord

# Shared ABI component lists to avoid repeating the same struct shapes.
_ATTEST_INPUT_COMPONENTS = [
    {"name": "orgIdHash", "type": "bytes32"},
    {"name": "periodStart", "type": "uint40"},
    {"name": "periodEnd", "type": "uint40"},
    {"name": "metricsHash", "type": "bytes32"},
    {"name": "benchmarkHash", "type": "bytes32"},
    {"name": "merkleRoot", "type": "bytes32"},
    {"name": "prevHash", "type": "bytes32"},
]

_ATTESTATION_OUTPUT_COMPONENTS = [
    *_ATTEST_INPUT_COMPONENTS,
    {"name": "nonce", "type": "uint64"},
    {"name": "timestamp", "type": "uint40"},
]

# Minimal ABI — avoids depending on Foundry out/ artifacts at runtime.
_ATTESTATION_ABI = [
    {
        "type": "function",
        "name": "attest",
        "inputs": list(_ATTEST_INPUT_COMPONENTS),
        "outputs": [],
        "stateMutability": "nonpayable",
    },
    {
        "type": "function",
        "name": "batchAttest",
        "inputs": [
            {
                "name": "inputs",
                "type": "tuple[]",
                "components": list(_ATTEST_INPUT_COMPONENTS),
            }
        ],
        "outputs": [],
        "stateMutability": "nonpayable",
    },
    {
        "type": "function",
        "name": "getLatest",
        "inputs": [{"name": "orgIdHash", "type": "bytes32"}],
        "outputs": [
            {
                "name": "",
                "type": "tuple",
                "components": list(_ATTESTATION_OUTPUT_COMPONENTS),
            }
        ],
        "stateMutability": "view",
    },
    {
        "type": "function",
        "name": "latestNonce",
        "inputs": [{"name": "orgIdHash", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "uint64"}],
        "stateMutability": "view",
    },
    {
        "type": "function",
        "name": "verify",
        "inputs": [
            {"name": "orgIdHash", "type": "bytes32"},
            {"name": "nonce", "type": "uint64"},
        ],
        "outputs": [
            {
                "name": "",
                "type": "tuple",
                "components": list(_ATTESTATION_OUTPUT_COMPONENTS),
            }
        ],
        "stateMutability": "view",
    },
]

# Anvil single-attest is ~60k gas; 500k accommodates batchAttest with ~6 records.
_DEFAULT_GAS_LIMIT = 500_000
_MAX_BATCH_SIZE = 10
_TX_TIMEOUT_SECONDS = 30


def _hex_to_bytes32(hex_str: str) -> bytes:
    """Convert a 64-char hex string (with or without 0x prefix) to 32-byte value."""
    clean = hex_str.removeprefix("0x").removeprefix("0X")
    raw = bytes.fromhex(clean)
    if len(raw) != 32:
        raise AttestationError(f"Expected 32 bytes, got {len(raw)} from '{hex_str[:16]}...'")
    return raw


def _bytes32_to_hex(b: bytes) -> str:
    """Convert 32-byte value to 64-char hex string."""
    return b.hex()


def _dt_to_uint40(dt: datetime) -> int:
    """Convert datetime to uint40 unix timestamp."""
    ts = int(dt.timestamp())
    if ts < 0 or ts >= 2**40:
        raise AttestationError(f"Timestamp {ts} out of uint40 range")
    return ts


def _uint40_to_dt(ts: int) -> datetime:
    """Convert uint40 unix timestamp to datetime."""
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _tuple_to_record(t: tuple) -> AttestationRecord | None:
    """Convert a contract return tuple to an AttestationRecord.

    Returns None if the tuple represents a zero-initialized struct
    (nonce == 0 means "not found" in the contract).
    """
    if len(t) != 9:
        raise AttestationError(f"Expected 9-element tuple from contract, got {len(t)}")

    nonce = t[7]
    if nonce == 0:
        return None

    return AttestationRecord(
        org_id_hash=_bytes32_to_hex(t[0]),
        period_start=_uint40_to_dt(t[1]),
        period_end=_uint40_to_dt(t[2]),
        metrics_hash=_bytes32_to_hex(t[3]),
        benchmark_hash=_bytes32_to_hex(t[4]),
        merkle_root=_bytes32_to_hex(t[5]),
        prev_hash=_bytes32_to_hex(t[6]),
        nonce=nonce,
        timestamp=_uint40_to_dt(t[8]),
    )


def _record_to_contract_tuple(record: AttestationRecord) -> tuple:
    """Convert an AttestationRecord to the 7-field tuple matching the contract ABI."""
    return (
        _hex_to_bytes32(record.org_id_hash),
        _dt_to_uint40(record.period_start),
        _dt_to_uint40(record.period_end),
        _hex_to_bytes32(record.metrics_hash),
        _hex_to_bytes32(record.benchmark_hash),
        _hex_to_bytes32(record.merkle_root),
        _hex_to_bytes32(record.prev_hash),
    )


class EVMProvider(AttestationProvider):
    """Provider targeting an EVM smart contract via web3.py async API."""

    def __init__(
        self,
        rpc_url: str,
        contract_address: str,
        private_key: str,
    ) -> None:
        self._rpc_url = rpc_url
        self._contract_address = contract_address
        self._private_key = private_key
        self._w3 = None
        self._contract = None
        self._account = None
        self._init_lock = asyncio.Lock()
        self._tx_lock = asyncio.Lock()
        # Track org hashes seen via submit/query (contract has no enumeration)
        self._known_orgs: set[str] = set()

    async def _ensure_connected(self) -> None:
        """Lazy-initialize the web3 connection and contract instance."""
        if self._w3 is not None:
            return

        async with self._init_lock:
            if self._w3 is not None:
                return

            try:
                from web3 import AsyncWeb3
                from web3.providers import AsyncHTTPProvider

                w3 = AsyncWeb3(AsyncHTTPProvider(self._rpc_url))
                account = w3.eth.account.from_key(self._private_key)
                contract = w3.eth.contract(
                    address=w3.to_checksum_address(self._contract_address),
                    abi=_ATTESTATION_ABI,
                )
                self._w3 = w3
                self._account = account
                self._contract = contract
            except Exception as exc:
                raise AttestationError(f"Failed to connect to EVM node: {exc}") from exc

    def _require_connected(self) -> None:
        if self._w3 is None or self._contract is None or self._account is None:
            raise AttestationError("Provider not connected; call _ensure_connected first")

    async def _send_tx(self, fn) -> str:
        """Build, sign, send a transaction and wait for receipt."""
        await self._ensure_connected()
        self._require_connected()

        try:
            async with self._tx_lock:
                nonce, gas_price = await asyncio.gather(
                    self._w3.eth.get_transaction_count(self._account.address),
                    self._w3.eth.gas_price,
                )
                tx = await fn.build_transaction({
                    "from": self._account.address,
                    "nonce": nonce,
                    "gas": _DEFAULT_GAS_LIMIT,
                    "gasPrice": gas_price,
                })
                signed = self._account.sign_transaction(tx)
                tx_hash = await self._w3.eth.send_raw_transaction(signed.raw_transaction)

            receipt = await asyncio.wait_for(
                self._w3.eth.wait_for_transaction_receipt(tx_hash),
                timeout=_TX_TIMEOUT_SECONDS,
            )
        except AttestationError:
            raise
        except asyncio.TimeoutError as exc:
            raise AttestationError("Transaction confirmation timed out") from exc
        except Exception as exc:
            raise AttestationError(f"Transaction failed: {exc}") from exc

        if receipt["status"] != 1:
            raise AttestationError(
                f"Transaction reverted: {tx_hash.hex()}"
            )

        return tx_hash.hex()

    async def submit(self, record: AttestationRecord) -> str:
        await self._ensure_connected()
        self._require_connected()

        fn = self._contract.functions.attest(*_record_to_contract_tuple(record))
        tx_hash = await self._send_tx(fn)
        self._known_orgs.add(record.org_id_hash)
        return tx_hash

    async def batch_submit(self, records: list[AttestationRecord]) -> list[str]:
        if not records:
            return []

        if len(records) > _MAX_BATCH_SIZE:
            raise AttestationError(
                f"Batch size {len(records)} exceeds max {_MAX_BATCH_SIZE}"
            )

        await self._ensure_connected()
        self._require_connected()

        tuples = [_record_to_contract_tuple(r) for r in records]
        fn = self._contract.functions.batchAttest(tuples)
        tx_hash = await self._send_tx(fn)
        return [tx_hash] * len(records)

    async def verify(
        self,
        org_id_hash: str,
        period_start: datetime,
        period_end: datetime,
    ) -> AttestationRecord | None:
        """Walk nonces backward to find a record matching the period."""
        await self._ensure_connected()
        self._require_connected()

        latest_nonce = await self.get_latest_nonce(org_id_hash)
        if latest_nonce == 0:
            return None

        org_bytes = _hex_to_bytes32(org_id_hash)
        ps_ts = _dt_to_uint40(period_start)
        pe_ts = _dt_to_uint40(period_end)

        for nonce in range(latest_nonce, 0, -1):
            result = await self._contract.functions.verify(org_bytes, nonce).call()
            record = _tuple_to_record(result)
            if record is None:
                continue
            # Early exit: periods are monotonically increasing, so stop
            # once we've passed the target window.
            record_pe = _dt_to_uint40(record.period_end)
            if record_pe < ps_ts:
                break
            if (
                _dt_to_uint40(record.period_start) == ps_ts
                and record_pe == pe_ts
            ):
                return record

        return None

    async def get_latest(self, org_id_hash: str) -> AttestationRecord | None:
        await self._ensure_connected()
        self._require_connected()

        org_bytes = _hex_to_bytes32(org_id_hash)
        result = await self._contract.functions.getLatest(org_bytes).call()
        record = _tuple_to_record(result)
        if record is not None:
            self._known_orgs.add(org_id_hash)
        return record

    async def get_latest_nonce(self, org_id_hash: str) -> int:
        await self._ensure_connected()
        self._require_connected()

        org_bytes = _hex_to_bytes32(org_id_hash)
        return await self._contract.functions.latestNonce(org_bytes).call()

    async def get_org_hashes(self) -> list[str]:
        return sorted(self._known_orgs)
