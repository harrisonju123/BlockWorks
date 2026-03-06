"""Tests for session-level routing phase detection and step-down."""

import time
from collections import deque
from unittest.mock import patch

import pytest

from blockthrough.routing.session import (
    PHASE_REPRESENTATIVE,
    TASK_TO_PHASE,
    SessionPhase,
    SessionTracker,
    StepDownPolicy,
    _detect_phase,
    get_step_down_policy,
)
from blockthrough.types import TaskType


class TestSessionPhaseEnum:
    def test_all_phases_exist(self):
        assert set(SessionPhase) == {
            SessionPhase.PLANNING,
            SessionPhase.IMPLEMENTING,
            SessionPhase.ITERATING,
            SessionPhase.FINISHING,
        }

    def test_string_values(self):
        assert SessionPhase.PLANNING.value == "planning"
        assert SessionPhase.IMPLEMENTING.value == "implementing"


class TestTaskToPhaseMapping:
    def test_all_non_unknown_tasks_mapped(self):
        """Every TaskType except UNKNOWN should have a phase mapping."""
        for task in TaskType:
            if task == TaskType.UNKNOWN:
                continue
            assert task in TASK_TO_PHASE, f"{task} not in TASK_TO_PHASE"

    def test_planning_tasks(self):
        assert TASK_TO_PHASE[TaskType.ARCHITECTURE] == SessionPhase.PLANNING
        assert TASK_TO_PHASE[TaskType.REASONING] == SessionPhase.PLANNING
        assert TASK_TO_PHASE[TaskType.CODE_REVIEW] == SessionPhase.PLANNING

    def test_implementing_tasks(self):
        assert TASK_TO_PHASE[TaskType.CODE_GENERATION] == SessionPhase.IMPLEMENTING
        assert TASK_TO_PHASE[TaskType.REFACTORING] == SessionPhase.IMPLEMENTING
        assert TASK_TO_PHASE[TaskType.TESTING] == SessionPhase.IMPLEMENTING

    def test_iterating_tasks(self):
        assert TASK_TO_PHASE[TaskType.DEBUGGING] == SessionPhase.ITERATING
        assert TASK_TO_PHASE[TaskType.CONVERSATION] == SessionPhase.ITERATING
        assert TASK_TO_PHASE[TaskType.TOOL_SELECTION] == SessionPhase.ITERATING

    def test_finishing_tasks(self):
        assert TASK_TO_PHASE[TaskType.SUMMARIZATION] == SessionPhase.FINISHING
        assert TASK_TO_PHASE[TaskType.DOCUMENTATION] == SessionPhase.FINISHING
        assert TASK_TO_PHASE[TaskType.EXTRACTION] == SessionPhase.FINISHING
        assert TASK_TO_PHASE[TaskType.CLASSIFICATION] == SessionPhase.FINISHING


class TestPhaseRepresentative:
    def test_all_phases_have_representative(self):
        for phase in SessionPhase:
            assert phase in PHASE_REPRESENTATIVE

    def test_representative_maps_back_to_phase(self):
        for phase, task in PHASE_REPRESENTATIVE.items():
            assert TASK_TO_PHASE[task] == phase


class TestStepDownPolicy:
    def test_aggressive_preset(self):
        p = StepDownPolicy.aggressive()
        assert p.window_size == 5
        assert p.min_window == 2
        assert p.transition_threshold == 0.5
        assert p.re_escalation_confidence == 0.9

    def test_moderate_preset(self):
        p = StepDownPolicy.moderate()
        assert p.window_size == 8
        assert p.min_window == 3
        assert p.transition_threshold == 0.6
        assert p.re_escalation_confidence == 0.8

    def test_conservative_preset(self):
        p = StepDownPolicy.conservative()
        assert p.window_size == 12
        assert p.min_window == 5
        assert p.transition_threshold == 0.7
        assert p.re_escalation_confidence == 0.7

    def test_frozen(self):
        p = StepDownPolicy.moderate()
        with pytest.raises(AttributeError):
            p.window_size = 99

    def test_get_step_down_policy_known(self):
        assert get_step_down_policy("aggressive") == StepDownPolicy.aggressive()

    def test_get_step_down_policy_unknown_defaults_moderate(self):
        assert get_step_down_policy("nonexistent") == StepDownPolicy.moderate()


class TestDetectPhase:
    def test_too_small_window_defaults_to_planning(self):
        policy = StepDownPolicy.moderate()  # min_window=3
        history = deque([TaskType.CODE_GENERATION, TaskType.DEBUGGING])
        assert _detect_phase(history, policy) == SessionPhase.PLANNING

    def test_majority_implementing(self):
        policy = StepDownPolicy.moderate()  # threshold=0.6
        history = deque([
            TaskType.CODE_GENERATION,
            TaskType.CODE_GENERATION,
            TaskType.CODE_GENERATION,
            TaskType.DEBUGGING,
            TaskType.CODE_GENERATION,
        ])
        assert _detect_phase(history, policy) == SessionPhase.IMPLEMENTING

    def test_majority_iterating(self):
        policy = StepDownPolicy.moderate()
        history = deque([
            TaskType.DEBUGGING,
            TaskType.CONVERSATION,
            TaskType.DEBUGGING,
        ])
        assert _detect_phase(history, policy) == SessionPhase.ITERATING

    def test_no_clear_majority_picks_most_common(self):
        policy = StepDownPolicy.conservative()  # threshold=0.7
        history = deque([
            TaskType.CODE_GENERATION,
            TaskType.CODE_GENERATION,
            TaskType.DEBUGGING,
            TaskType.DEBUGGING,
            TaskType.SUMMARIZATION,
        ])
        # No phase reaches 0.7 threshold; IMPLEMENTING and ITERATING tied at 2 each
        phase = _detect_phase(history, policy)
        # Should pick one of the two most common
        assert phase in {SessionPhase.IMPLEMENTING, SessionPhase.ITERATING}

    def test_planning_phase_detected(self):
        policy = StepDownPolicy.moderate()
        history = deque([
            TaskType.ARCHITECTURE,
            TaskType.REASONING,
            TaskType.ARCHITECTURE,
        ])
        assert _detect_phase(history, policy) == SessionPhase.PLANNING

    def test_window_size_respected(self):
        """Only the last window_size entries matter."""
        policy = StepDownPolicy(window_size=3, min_window=2, transition_threshold=0.6, re_escalation_confidence=0.8)
        # Old entries are planning, but last 3 are implementing
        history = deque([
            TaskType.ARCHITECTURE,
            TaskType.ARCHITECTURE,
            TaskType.CODE_GENERATION,
            TaskType.CODE_GENERATION,
            TaskType.REFACTORING,
        ])
        assert _detect_phase(history, policy) == SessionPhase.IMPLEMENTING


class TestSessionTracker:
    def test_record_returns_phase(self):
        tracker = SessionTracker(policy=StepDownPolicy.moderate())
        phase = tracker.record("sess-1", TaskType.ARCHITECTURE)
        assert phase == SessionPhase.PLANNING

    def test_get_phase_unknown_session(self):
        tracker = SessionTracker(policy=StepDownPolicy.moderate())
        assert tracker.get_phase("unknown") is None

    def test_get_phase_after_record(self):
        tracker = SessionTracker(policy=StepDownPolicy.moderate())
        tracker.record("sess-1", TaskType.CODE_GENERATION)
        phase = tracker.get_phase("sess-1")
        assert phase is not None

    def test_session_transitions(self):
        """Session starts PLANNING, transitions to IMPLEMENTING."""
        tracker = SessionTracker(policy=StepDownPolicy.aggressive())  # min_window=2, threshold=0.5
        # Start with planning
        tracker.record("s1", TaskType.ARCHITECTURE)
        tracker.record("s1", TaskType.REASONING)
        assert tracker.get_phase("s1") == SessionPhase.PLANNING

        # Shift to implementing
        tracker.record("s1", TaskType.CODE_GENERATION)
        tracker.record("s1", TaskType.CODE_GENERATION)
        tracker.record("s1", TaskType.REFACTORING)
        phase = tracker.get_phase("s1")
        assert phase == SessionPhase.IMPLEMENTING

    def test_multiple_sessions_independent(self):
        tracker = SessionTracker(policy=StepDownPolicy.aggressive())
        tracker.record("s1", TaskType.ARCHITECTURE)
        tracker.record("s1", TaskType.ARCHITECTURE)
        tracker.record("s2", TaskType.CODE_GENERATION)
        tracker.record("s2", TaskType.CODE_GENERATION)
        assert tracker.get_phase("s1") == SessionPhase.PLANNING
        assert tracker.get_phase("s2") == SessionPhase.IMPLEMENTING

    def test_prune_evicts_old_sessions(self):
        tracker = SessionTracker(policy=StepDownPolicy.moderate(), max_age_s=10)
        tracker.record("old-sess", TaskType.DEBUGGING)
        # Manually backdate
        tracker._sessions["old-sess"].last_seen = time.monotonic() - 20
        tracker.record("new-sess", TaskType.CODE_GENERATION)

        evicted = tracker.prune()
        assert evicted == 1
        assert tracker.get_phase("old-sess") is None
        assert tracker.get_phase("new-sess") is not None

    def test_prune_returns_zero_when_nothing_to_evict(self):
        tracker = SessionTracker(policy=StepDownPolicy.moderate())
        tracker.record("fresh", TaskType.DEBUGGING)
        assert tracker.prune() == 0

    def test_session_count(self):
        tracker = SessionTracker(policy=StepDownPolicy.moderate())
        assert tracker.session_count == 0
        tracker.record("s1", TaskType.DEBUGGING)
        tracker.record("s2", TaskType.DEBUGGING)
        assert tracker.session_count == 2

    def test_deque_bounded_by_window_size(self):
        policy = StepDownPolicy(window_size=3, min_window=2, transition_threshold=0.5, re_escalation_confidence=0.8)
        tracker = SessionTracker(policy=policy)
        for _ in range(10):
            tracker.record("s1", TaskType.CODE_GENERATION)
        assert len(tracker._sessions["s1"].history) == 3


class TestNoSessionIdBackwardCompat:
    """When no session_id is provided, no phase adjustment should happen."""

    def test_no_session_means_no_tracker_interaction(self):
        # SessionTracker is only called when session_id is present.
        # This is a documentation test — the actual gate is in proxy.py
        tracker = SessionTracker(policy=StepDownPolicy.moderate())
        assert tracker.session_count == 0
