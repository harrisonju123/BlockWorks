"""Payment signing and verification using HMAC-SHA256.

Local dev placeholder for real ECDSA signing. The message format
mirrors what the Solidity settlement contract will verify:
keccak256(abi.encodePacked(channel_id, amount_wei, nonce)).

For local dev, we use HMAC-SHA256 with the private key as the HMAC
key instead of actual secp256k1 signatures. The interface is designed
so that swapping in real ECDSA (via web3.py) only changes this module.
"""

from __future__ import annotations

import hashlib
import hmac
import struct


def _build_message(channel_id: str, amount: float, nonce: int) -> bytes:
    """Construct the canonical message bytes matching EVM encodePacked layout.

    Encodes channel_id as UTF-8 bytes (standing in for bytes32 on-chain),
    amount as a uint256 (wei-scale integer), and nonce as uint64.
    """
    # Convert amount to wei-scale integer (18 decimals) to avoid float issues
    amount_wei = int(amount * 10**18)
    msg = channel_id.encode("utf-8")
    msg += struct.pack(">Q", amount_wei >> 192 & 0xFFFFFFFFFFFFFFFF)
    msg += struct.pack(">Q", amount_wei >> 128 & 0xFFFFFFFFFFFFFFFF)
    msg += struct.pack(">Q", amount_wei >> 64 & 0xFFFFFFFFFFFFFFFF)
    msg += struct.pack(">Q", amount_wei & 0xFFFFFFFFFFFFFFFF)
    msg += struct.pack(">Q", nonce)
    return msg


def sign_payment(
    channel_id: str,
    amount: float,
    nonce: int,
    private_key: str,
) -> str:
    """Sign a payment update, returning a hex signature string.

    Uses HMAC-SHA256 as a local dev placeholder for ECDSA. The private_key
    is used as the HMAC key.
    """
    msg = _build_message(channel_id, amount, nonce)
    sig = hmac.new(
        private_key.encode("utf-8"),
        msg,
        hashlib.sha256,
    ).hexdigest()
    return sig


def verify_signature(
    channel_id: str,
    amount: float,
    nonce: int,
    signature: str,
    public_key: str,
) -> bool:
    """Verify a payment signature.

    In the HMAC placeholder, public_key == private_key (symmetric).
    With real ECDSA, this would recover the signer address from the
    signature and compare it to the expected public key.
    """
    expected = sign_payment(channel_id, amount, nonce, public_key)
    return hmac.compare_digest(expected, signature)
