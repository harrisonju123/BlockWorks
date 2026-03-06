"""Tests for the FeedbackDetector lifecycle."""

import asyncio

import pytest

from blockthrough.feedback.detector import FeedbackDetector


class TestFeedbackDetectorLifecycle:
    def test_init(self):
        detector = FeedbackDetector(
            db_url="postgresql+asyncpg://localhost/test",
            detection_interval_s=60,
        )
        assert detector._detection_interval_s == 60

    def test_db_url_normalization(self):
        detector = FeedbackDetector(
            db_url="postgresql+asyncpg://user:pass@localhost:5432/db",
        )
        assert "+asyncpg" not in detector._db_url
        assert detector._db_url.startswith("postgresql://")

    @pytest.mark.asyncio
    async def test_shutdown_signals_event(self):
        detector = FeedbackDetector(db_url="postgresql://localhost/test")
        assert not detector._shutdown_event.is_set()
        await detector.shutdown()
        assert detector._shutdown_event.is_set()
