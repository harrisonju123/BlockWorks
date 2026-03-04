"""Provider factory — constructs the configured attestation provider.

Lazy imports ensure chain-specific dependencies (web3.py for EVM) are
never imported unless that provider is actually selected.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from agentproof.attestation.provider import AttestationProvider
from agentproof.config import get_config

logger = logging.getLogger(__name__)


def _load_address_from_deployments(path: str, contract_name: str) -> str | None:
    """Read a contract address from the deploy-local.sh output file."""
    try:
        data = json.loads(Path(path).read_text())
        addr = data.get("contracts", {}).get(contract_name)
        if addr:
            logger.info("Loaded %s address from %s: %s", contract_name, path, addr)
        return addr
    except FileNotFoundError:
        logger.debug("Deployments file not found: %s", path)
        return None
    except (json.JSONDecodeError, KeyError) as exc:
        logger.warning("Failed to parse deployments file %s: %s", path, exc)
        return None


def create_provider(provider_type: str | None = None, **kwargs: object) -> AttestationProvider:
    """Create an attestation provider based on config or explicit type.

    Args:
        provider_type: "local" (default) or "evm". When None, reads from
            AGENTPROOF_ATTESTATION_PROVIDER env var via config.
        **kwargs: Forwarded to the provider constructor.

    Raises:
        ValueError: If the provider type is unknown.
        ValueError: If EVM provider is selected but required config is missing.
    """
    config = get_config()
    ptype = provider_type or config.attestation_provider

    if ptype == "local":
        from agentproof.attestation.local_provider import LocalProvider

        return LocalProvider()

    if ptype == "evm":
        from agentproof.attestation.evm_provider import EVMProvider

        rpc_url = kwargs.get("rpc_url") or config.attestation_rpc_url
        contract_address = kwargs.get("contract_address") or config.attestation_contract_address

        # Auto-discover contract address from deployments file
        if not contract_address:
            contract_address = _load_address_from_deployments(
                config.attestation_deployments_path, "AgentProofAttestation"
            )

        if not rpc_url:
            raise ValueError("EVM provider requires attestation_rpc_url")
        if not contract_address:
            raise ValueError(
                "EVM provider requires attestation_contract_address or a valid deployments file"
            )

        private_key = kwargs.get("private_key") or config.attestation_private_key
        if not private_key:
            raise ValueError("EVM provider requires attestation_private_key")
        return EVMProvider(
            rpc_url=str(rpc_url),
            contract_address=str(contract_address),
            private_key=str(private_key),
        )

    raise ValueError(f"Unknown attestation provider: {ptype}")
