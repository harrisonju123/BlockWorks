"""Tests for the feedback API endpoint."""

import pytest

from blockthrough.feedback.types import FeedbackSignal, SIGNAL_DEFAULTS


class TestFeedbackSignalDefaults:
    """Verify signal defaults are correctly configured for the API."""

    def test_positive_rating_maps_to_positive_signal(self):
        signal = FeedbackSignal.EXPLICIT_POSITIVE
        delta, weight = SIGNAL_DEFAULTS[signal]
        assert delta > 0
        assert weight > 0

    def test_negative_rating_maps_to_negative_signal(self):
        signal = FeedbackSignal.EXPLICIT_NEGATIVE
        delta, weight = SIGNAL_DEFAULTS[signal]
        assert delta < 0
        assert weight > 0

    def test_all_weights_positive(self):
        for signal, (_, weight) in SIGNAL_DEFAULTS.items():
            assert weight > 0, f"{signal} has non-positive weight"
