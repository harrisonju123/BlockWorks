"""Tests for feedback types."""

import uuid
from datetime import datetime, timezone

import pytest

from blockthrough.feedback.types import (
    FeedbackRecord,
    FeedbackSignal,
    SIGNAL_DEFAULTS,
)


class TestFeedbackSignal:
    def test_all_signals_exist(self):
        assert len(FeedbackSignal) == 5

    def test_string_values(self):
        assert FeedbackSignal.RETRY.value == "retry"
        assert FeedbackSignal.OVERRIDE.value == "override"
        assert FeedbackSignal.ABANDON.value == "abandon"
        assert FeedbackSignal.EXPLICIT_POSITIVE.value == "explicit_positive"
        assert FeedbackSignal.EXPLICIT_NEGATIVE.value == "explicit_negative"


class TestSignalDefaults:
    def test_all_signals_have_defaults(self):
        for signal in FeedbackSignal:
            assert signal in SIGNAL_DEFAULTS

    def test_retry_defaults(self):
        delta, weight = SIGNAL_DEFAULTS[FeedbackSignal.RETRY]
        assert delta == -0.10
        assert weight == 1.0

    def test_override_defaults(self):
        delta, weight = SIGNAL_DEFAULTS[FeedbackSignal.OVERRIDE]
        assert delta == -0.15
        assert weight == 1.2

    def test_positive_is_positive(self):
        delta, _ = SIGNAL_DEFAULTS[FeedbackSignal.EXPLICIT_POSITIVE]
        assert delta > 0

    def test_negative_signals_are_negative(self):
        for signal in [FeedbackSignal.RETRY, FeedbackSignal.OVERRIDE, FeedbackSignal.ABANDON, FeedbackSignal.EXPLICIT_NEGATIVE]:
            delta, _ = SIGNAL_DEFAULTS[signal]
            assert delta < 0


class TestFeedbackRecord:
    def test_valid_record(self):
        record = FeedbackRecord(
            id=uuid.uuid4(),
            created_at=datetime.now(timezone.utc),
            event_id=uuid.uuid4(),
            model="claude-sonnet-4-6",
            task_type="code_generation",
            signal=FeedbackSignal.RETRY,
            quality_delta=-0.10,
            weight=1.0,
            source="implicit",
        )
        assert record.signal == FeedbackSignal.RETRY

    def test_default_source(self):
        record = FeedbackRecord(
            id=uuid.uuid4(),
            created_at=datetime.now(timezone.utc),
            event_id=uuid.uuid4(),
            model="gpt-4o",
            task_type="debugging",
            signal=FeedbackSignal.EXPLICIT_POSITIVE,
            quality_delta=0.05,
        )
        assert record.source == "implicit"
