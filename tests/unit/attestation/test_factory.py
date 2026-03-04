"""Tests for the attestation provider factory."""

from __future__ import annotations

import pytest

from agentproof.attestation.evm_provider import EVMProvider
from agentproof.attestation.factory import create_provider
from agentproof.attestation.local_provider import LocalProvider
from agentproof.config import get_config


class TestCreateProvider:

    def setup_method(self) -> None:
        get_config.cache_clear()

    def teardown_method(self) -> None:
        get_config.cache_clear()

    def test_default_creates_local_provider(self) -> None:
        provider = create_provider()
        assert isinstance(provider, LocalProvider)

    def test_explicit_local_type(self) -> None:
        provider = create_provider(provider_type="local")
        assert isinstance(provider, LocalProvider)

    def test_explicit_evm_type_with_kwargs(self) -> None:
        provider = create_provider(
            provider_type="evm",
            rpc_url="https://rpc.example.com",
            contract_address="0x1234567890abcdef",
            private_key="0xdeadbeef",
        )
        assert isinstance(provider, EVMProvider)

    def test_evm_type_missing_rpc_url_raises(self) -> None:
        with pytest.raises(ValueError, match="attestation_rpc_url"):
            create_provider(
                provider_type="evm",
                contract_address="0x1234",
            )

    def test_evm_type_missing_contract_address_raises(self) -> None:
        with pytest.raises(ValueError, match="attestation_contract_address|deployments"):
            create_provider(
                provider_type="evm",
                rpc_url="https://rpc.example.com",
            )

    def test_unknown_provider_type_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown attestation provider"):
            create_provider(provider_type="solana")

    def test_evm_provider_stores_config(self) -> None:
        provider = create_provider(
            provider_type="evm",
            rpc_url="https://rpc.example.com",
            contract_address="0x1234",
            private_key="0xbeef",
        )
        assert isinstance(provider, EVMProvider)
        assert provider._rpc_url == "https://rpc.example.com"
        assert provider._contract_address == "0x1234"
        assert provider._private_key == "0xbeef"
