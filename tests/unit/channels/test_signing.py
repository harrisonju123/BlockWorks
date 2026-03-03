"""Tests for the payment signing and verification module.

Validates HMAC-SHA256 signing, verification, and that the signature
scheme is deterministic and tamper-evident.
"""

from __future__ import annotations

import time

from agentproof.channels.signing import sign_payment, verify_signature


# ---------------------------------------------------------------------------
# Basic signing and verification
# ---------------------------------------------------------------------------


class TestSignPayment:

    def test_sign_returns_hex_string(self) -> None:
        sig = sign_payment("ch-1", 0.5, 1, "my-key")
        assert isinstance(sig, str)
        assert len(sig) == 64  # SHA-256 hex digest
        int(sig, 16)  # valid hex

    def test_sign_is_deterministic(self) -> None:
        sig1 = sign_payment("ch-1", 0.5, 1, "key")
        sig2 = sign_payment("ch-1", 0.5, 1, "key")
        assert sig1 == sig2

    def test_different_channel_different_sig(self) -> None:
        sig1 = sign_payment("ch-1", 0.5, 1, "key")
        sig2 = sign_payment("ch-2", 0.5, 1, "key")
        assert sig1 != sig2

    def test_different_amount_different_sig(self) -> None:
        sig1 = sign_payment("ch-1", 0.5, 1, "key")
        sig2 = sign_payment("ch-1", 0.6, 1, "key")
        assert sig1 != sig2

    def test_different_nonce_different_sig(self) -> None:
        sig1 = sign_payment("ch-1", 0.5, 1, "key")
        sig2 = sign_payment("ch-1", 0.5, 2, "key")
        assert sig1 != sig2

    def test_different_key_different_sig(self) -> None:
        sig1 = sign_payment("ch-1", 0.5, 1, "key-a")
        sig2 = sign_payment("ch-1", 0.5, 1, "key-b")
        assert sig1 != sig2


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


class TestVerifySignature:

    def test_valid_signature_passes(self) -> None:
        key = "test-key"
        sig = sign_payment("ch-1", 1.0, 5, key)
        assert verify_signature("ch-1", 1.0, 5, sig, key) is True

    def test_wrong_key_fails(self) -> None:
        sig = sign_payment("ch-1", 1.0, 5, "real-key")
        assert verify_signature("ch-1", 1.0, 5, sig, "wrong-key") is False

    def test_tampered_amount_fails(self) -> None:
        key = "test-key"
        sig = sign_payment("ch-1", 1.0, 5, key)
        # Verify with different amount
        assert verify_signature("ch-1", 2.0, 5, sig, key) is False

    def test_tampered_nonce_fails(self) -> None:
        key = "test-key"
        sig = sign_payment("ch-1", 1.0, 5, key)
        assert verify_signature("ch-1", 1.0, 6, sig, key) is False

    def test_tampered_channel_fails(self) -> None:
        key = "test-key"
        sig = sign_payment("ch-1", 1.0, 5, key)
        assert verify_signature("ch-2", 1.0, 5, sig, key) is False

    def test_garbage_signature_fails(self) -> None:
        assert verify_signature("ch-1", 1.0, 5, "deadbeef" * 8, "key") is False

    def test_zero_amount_signs_correctly(self) -> None:
        key = "test-key"
        sig = sign_payment("ch-1", 0.0, 0, key)
        assert verify_signature("ch-1", 0.0, 0, sig, key) is True

    def test_large_amount_signs_correctly(self) -> None:
        key = "test-key"
        sig = sign_payment("ch-1", 999999.99, 100, key)
        assert verify_signature("ch-1", 999999.99, 100, sig, key) is True


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------


class TestSigningPerformance:

    def test_sign_is_sub_millisecond(self) -> None:
        """Single signature creation should be well under 1ms."""
        # Warm up
        sign_payment("ch-1", 0.5, 1, "key")

        start = time.perf_counter()
        for i in range(100):
            sign_payment("ch-1", float(i) * 0.01, i + 1, "key")
        elapsed = time.perf_counter() - start

        per_sig_ms = (elapsed / 100) * 1000
        assert per_sig_ms < 1.0, f"Signature creation took {per_sig_ms:.3f}ms (target: <1ms)"

    def test_verify_is_sub_millisecond(self) -> None:
        """Single signature verification should be well under 1ms."""
        key = "key"
        sigs = [
            (float(i) * 0.01, i + 1, sign_payment("ch-1", float(i) * 0.01, i + 1, key))
            for i in range(100)
        ]

        start = time.perf_counter()
        for amount, nonce, sig in sigs:
            verify_signature("ch-1", amount, nonce, sig, key)
        elapsed = time.perf_counter() - start

        per_verify_ms = (elapsed / 100) * 1000
        assert per_verify_ms < 1.0, f"Verification took {per_verify_ms:.3f}ms (target: <1ms)"
