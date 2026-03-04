"""Tests for the EVMProvider implementation.

Mocks AsyncWeb3 and contract functions to test encoding, decoding,
transaction flow, and error handling without a real chain.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentproof.attestation.evm_provider import (
    EVMProvider,
    _bytes32_to_hex,
    _dt_to_uint40,
    _hex_to_bytes32,
    _record_to_contract_tuple,
    _tuple_to_record,
    _uint40_to_dt,
)
from agentproof.attestation.provider import AttestationError

from .conftest import NOW, PERIOD_END, PERIOD_START, make_record


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

NOW_TS = int(NOW.timestamp())
_PS_TS = int(PERIOD_START.timestamp())
_PE_TS = int(PERIOD_END.timestamp())


def _make_contract_tuple(
    org_id_hash: bytes = bytes.fromhex("aa" * 32),
    period_start: int = _PS_TS,
    period_end: int = _PE_TS,
    metrics_hash: bytes = bytes.fromhex("dd" * 32),
    benchmark_hash: bytes = bytes.fromhex("bb" * 32),
    merkle_root: bytes = bytes.fromhex("cc" * 32),
    prev_hash: bytes = bytes.fromhex("0" * 64),
    nonce: int = 1,
    timestamp: int = NOW_TS,
) -> tuple:
    return (
        org_id_hash,
        period_start,
        period_end,
        metrics_hash,
        benchmark_hash,
        merkle_root,
        prev_hash,
        nonce,
        timestamp,
    )


# ---------------------------------------------------------------------------
# Type conversion helpers
# ---------------------------------------------------------------------------


class TestTypeConversions:

    def test_hex_to_bytes32_valid(self) -> None:
        result = _hex_to_bytes32("aa" * 32)
        assert len(result) == 32
        assert result == bytes.fromhex("aa" * 32)

    def test_hex_to_bytes32_wrong_length(self) -> None:
        with pytest.raises(AttestationError, match="Expected 32 bytes"):
            _hex_to_bytes32("aa" * 16)

    def test_bytes32_to_hex(self) -> None:
        raw = bytes.fromhex("bb" * 32)
        assert _bytes32_to_hex(raw) == "bb" * 32

    def test_dt_to_uint40_roundtrip(self) -> None:
        ts = _dt_to_uint40(NOW)
        dt = _uint40_to_dt(ts)
        assert dt == NOW

    def test_tuple_to_record_valid(self) -> None:
        t = _make_contract_tuple()
        record = _tuple_to_record(t)
        assert record is not None
        assert record.org_id_hash == "aa" * 32
        assert record.nonce == 1
        assert record.metrics_hash == "dd" * 32
        assert record.period_start == PERIOD_START
        assert record.period_end == PERIOD_END

    def test_tuple_to_record_nonce_zero_returns_none(self) -> None:
        """Nonce 0 is the contract's 'not found' sentinel."""
        t = _make_contract_tuple(nonce=0)
        assert _tuple_to_record(t) is None

    def test_record_to_contract_tuple(self) -> None:
        record = make_record()
        args = _record_to_contract_tuple(record)
        assert len(args) == 7
        assert args[0] == bytes.fromhex("aa" * 32)  # orgIdHash
        assert args[1] == _PS_TS  # periodStart
        assert args[2] == _PE_TS  # periodEnd
        assert args[3] == bytes.fromhex("dd" * 32)  # metricsHash


# ---------------------------------------------------------------------------
# Mock web3 setup
# ---------------------------------------------------------------------------


def _make_mock_provider():
    """Create a mock EVMProvider with mocked web3 internals."""
    provider = EVMProvider(
        rpc_url="http://localhost:8545",
        contract_address="0x5FbDB2315678afecb367f032d93F642f64180aa3",
        private_key="0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
    )

    # Mock web3 instance
    mock_w3 = MagicMock()
    mock_w3.eth = MagicMock()
    mock_w3.eth.get_transaction_count = AsyncMock(return_value=0)
    # web3 async gas_price is an async property returning a coroutine;
    # each mock instance is used for a single _send_tx call so this is safe.
    async def _gas():
        return 1_000_000_000

    mock_w3.eth.gas_price = _gas()
    mock_w3.eth.send_raw_transaction = AsyncMock(return_value=b"\x01" * 32)
    mock_w3.eth.wait_for_transaction_receipt = AsyncMock(
        return_value={"status": 1, "transactionHash": b"\x01" * 32}
    )
    mock_w3.to_checksum_address = MagicMock(
        return_value="0x5FbDB2315678afecb367f032d93F642f64180aa3"
    )

    # Mock account
    mock_account = MagicMock()
    mock_account.address = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
    mock_account.sign_transaction = MagicMock(
        return_value=MagicMock(raw_transaction=b"\x02" * 32)
    )
    mock_w3.eth.account = MagicMock()
    mock_w3.eth.account.from_key = MagicMock(return_value=mock_account)

    # Mock contract
    mock_contract = MagicMock()

    provider._w3 = mock_w3
    provider._account = mock_account
    provider._contract = mock_contract

    return provider, mock_contract, mock_w3


# ---------------------------------------------------------------------------
# Submit
# ---------------------------------------------------------------------------


class TestSubmit:

    @pytest.mark.asyncio
    async def test_submit_calls_attest_with_correct_args(self) -> None:
        provider, mock_contract, _ = _make_mock_provider()
        record = make_record()

        mock_fn = MagicMock()
        mock_fn.build_transaction = AsyncMock(return_value={
            "to": "0x5FbDB2315678afecb367f032d93F642f64180aa3",
            "data": b"",
            "gas": 500_000,
        })
        mock_contract.functions.attest = MagicMock(return_value=mock_fn)

        tx_id = await provider.submit(record)

        mock_contract.functions.attest.assert_called_once()
        args = mock_contract.functions.attest.call_args[0]
        assert args[0] == bytes.fromhex("aa" * 32)  # orgIdHash
        assert args[1] == _PS_TS  # periodStart
        assert args[2] == _PE_TS  # periodEnd
        assert len(tx_id) == 64  # hex tx hash

    @pytest.mark.asyncio
    async def test_submit_raises_on_reverted_tx(self) -> None:
        provider, mock_contract, mock_w3 = _make_mock_provider()
        record = make_record()

        mock_fn = MagicMock()
        mock_fn.build_transaction = AsyncMock(return_value={
            "to": "0x5FbDB2315678afecb367f032d93F642f64180aa3",
            "data": b"",
            "gas": 500_000,
        })
        mock_contract.functions.attest = MagicMock(return_value=mock_fn)

        # Simulate reverted transaction
        mock_w3.eth.wait_for_transaction_receipt = AsyncMock(
            return_value={"status": 0, "transactionHash": b"\x01" * 32}
        )

        with pytest.raises(AttestationError, match="Transaction reverted"):
            await provider.submit(record)


# ---------------------------------------------------------------------------
# Batch submit
# ---------------------------------------------------------------------------


class TestBatchSubmit:

    @pytest.mark.asyncio
    async def test_batch_submit_calls_batch_attest(self) -> None:
        provider, mock_contract, _ = _make_mock_provider()

        mock_fn = MagicMock()
        mock_fn.build_transaction = AsyncMock(return_value={
            "to": "0x5FbDB2315678afecb367f032d93F642f64180aa3",
            "data": b"",
            "gas": 500_000,
        })
        mock_contract.functions.batchAttest = MagicMock(return_value=mock_fn)

        records = [make_record(nonce=1), make_record(nonce=2)]
        tx_ids = await provider.batch_submit(records)

        mock_contract.functions.batchAttest.assert_called_once()
        tuples = mock_contract.functions.batchAttest.call_args[0][0]
        assert len(tuples) == 2
        # All share the same tx hash (single transaction)
        assert len(tx_ids) == 2
        assert tx_ids[0] == tx_ids[1]

    @pytest.mark.asyncio
    async def test_batch_submit_empty_list(self) -> None:
        provider, _, _ = _make_mock_provider()
        result = await provider.batch_submit([])
        assert result == []


# ---------------------------------------------------------------------------
# get_latest
# ---------------------------------------------------------------------------


class TestGetLatest:

    @pytest.mark.asyncio
    async def test_get_latest_returns_record(self) -> None:
        provider, mock_contract, _ = _make_mock_provider()

        mock_contract.functions.getLatest = MagicMock(
            return_value=MagicMock(
                call=AsyncMock(return_value=_make_contract_tuple(nonce=3))
            )
        )

        record = await provider.get_latest("aa" * 32)
        assert record is not None
        assert record.nonce == 3
        assert record.org_id_hash == "aa" * 32

    @pytest.mark.asyncio
    async def test_get_latest_returns_none_when_no_attestations(self) -> None:
        provider, mock_contract, _ = _make_mock_provider()

        mock_contract.functions.getLatest = MagicMock(
            return_value=MagicMock(
                call=AsyncMock(return_value=_make_contract_tuple(nonce=0))
            )
        )

        record = await provider.get_latest("aa" * 32)
        assert record is None


# ---------------------------------------------------------------------------
# get_latest_nonce
# ---------------------------------------------------------------------------


class TestGetLatestNonce:

    @pytest.mark.asyncio
    async def test_get_latest_nonce(self) -> None:
        provider, mock_contract, _ = _make_mock_provider()

        mock_contract.functions.latestNonce = MagicMock(
            return_value=MagicMock(call=AsyncMock(return_value=5))
        )

        nonce = await provider.get_latest_nonce("aa" * 32)
        assert nonce == 5

    @pytest.mark.asyncio
    async def test_get_latest_nonce_zero_for_new_org(self) -> None:
        provider, mock_contract, _ = _make_mock_provider()

        mock_contract.functions.latestNonce = MagicMock(
            return_value=MagicMock(call=AsyncMock(return_value=0))
        )

        nonce = await provider.get_latest_nonce("ee" * 32)
        assert nonce == 0


# ---------------------------------------------------------------------------
# Verify (nonce walk)
# ---------------------------------------------------------------------------


class TestVerify:

    @pytest.mark.asyncio
    async def test_verify_finds_matching_period(self) -> None:
        provider, mock_contract, _ = _make_mock_provider()

        mock_contract.functions.latestNonce = MagicMock(
            return_value=MagicMock(call=AsyncMock(return_value=2))
        )

        # Nonce 2 has different period, nonce 1 matches
        def make_verify_mock(org_bytes, nonce):
            if nonce == 2:
                return MagicMock(
                    call=AsyncMock(
                        return_value=_make_contract_tuple(
                            nonce=2,
                            period_start=_PS_TS + 86400,
                            period_end=_PE_TS + 86400,
                        )
                    )
                )
            return MagicMock(
                call=AsyncMock(return_value=_make_contract_tuple(nonce=1))
            )

        mock_contract.functions.verify = MagicMock(side_effect=make_verify_mock)

        result = await provider.verify("aa" * 32, PERIOD_START, PERIOD_END)
        assert result is not None
        assert result.nonce == 1

    @pytest.mark.asyncio
    async def test_verify_returns_none_for_no_attestations(self) -> None:
        provider, mock_contract, _ = _make_mock_provider()

        mock_contract.functions.latestNonce = MagicMock(
            return_value=MagicMock(call=AsyncMock(return_value=0))
        )

        result = await provider.verify("aa" * 32, PERIOD_START, PERIOD_END)
        assert result is None

    @pytest.mark.asyncio
    async def test_verify_returns_none_when_no_period_matches(self) -> None:
        provider, mock_contract, _ = _make_mock_provider()

        mock_contract.functions.latestNonce = MagicMock(
            return_value=MagicMock(call=AsyncMock(return_value=1))
        )

        # Nonce 1 has different period
        mock_contract.functions.verify = MagicMock(
            return_value=MagicMock(
                call=AsyncMock(
                    return_value=_make_contract_tuple(
                        nonce=1,
                        period_start=_PS_TS + 86400,
                        period_end=_PE_TS + 86400,
                    )
                )
            )
        )

        result = await provider.verify("aa" * 32, PERIOD_START, PERIOD_END)
        assert result is None


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:

    def test_hex_to_bytes32_invalid_hex(self) -> None:
        with pytest.raises(ValueError):
            _hex_to_bytes32("not-hex")

    def test_hex_to_bytes32_too_short(self) -> None:
        with pytest.raises(AttestationError, match="Expected 32 bytes"):
            _hex_to_bytes32("aa" * 16)


# ---------------------------------------------------------------------------
# Factory integration
# ---------------------------------------------------------------------------


class TestFactoryIntegration:

    def test_factory_creates_evm_provider_with_deployments(self, tmp_path) -> None:
        """Factory can auto-discover address from deployments file."""
        import json

        from agentproof.attestation.factory import _load_address_from_deployments

        deploy_file = tmp_path / "local.json"
        deploy_file.write_text(json.dumps({
            "chain_id": 31337,
            "contracts": {
                "AgentProofAttestation": "0x5FbDB2315678afecb367f032d93F642f64180aa3"
            },
        }))

        addr = _load_address_from_deployments(str(deploy_file), "AgentProofAttestation")
        assert addr == "0x5FbDB2315678afecb367f032d93F642f64180aa3"

    def test_factory_returns_none_for_missing_file(self) -> None:
        from agentproof.attestation.factory import _load_address_from_deployments

        addr = _load_address_from_deployments("/nonexistent/path.json", "AgentProofAttestation")
        assert addr is None

    def test_factory_returns_none_for_missing_contract(self, tmp_path) -> None:
        import json

        from agentproof.attestation.factory import _load_address_from_deployments

        deploy_file = tmp_path / "local.json"
        deploy_file.write_text(json.dumps({"contracts": {}}))

        addr = _load_address_from_deployments(str(deploy_file), "AgentProofAttestation")
        assert addr is None
