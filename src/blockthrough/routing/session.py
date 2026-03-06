"""Session-level routing: detect session phase from recent task history.

Adjusts effective task_type before it reaches resolve() so the router
core stays unchanged. Phase detection uses majority vote over a sliding
window of recent classifications per session_id.
"""

from __future__ import annotations

import enum
import time
from collections import deque
from dataclasses import dataclass, field

from blockthrough.types import TaskType


class SessionPhase(str, enum.Enum):
    PLANNING = "planning"
    IMPLEMENTING = "implementing"
    ITERATING = "iterating"
    FINISHING = "finishing"


# Maps each TaskType to its session phase
TASK_TO_PHASE: dict[TaskType, SessionPhase] = {
    TaskType.ARCHITECTURE: SessionPhase.PLANNING,
    TaskType.REASONING: SessionPhase.PLANNING,
    TaskType.CODE_REVIEW: SessionPhase.PLANNING,
    TaskType.CODE_GENERATION: SessionPhase.IMPLEMENTING,
    TaskType.REFACTORING: SessionPhase.IMPLEMENTING,
    TaskType.TESTING: SessionPhase.IMPLEMENTING,
    TaskType.DEBUGGING: SessionPhase.ITERATING,
    TaskType.CONVERSATION: SessionPhase.ITERATING,
    TaskType.TOOL_SELECTION: SessionPhase.ITERATING,
    TaskType.SUMMARIZATION: SessionPhase.FINISHING,
    TaskType.DOCUMENTATION: SessionPhase.FINISHING,
    TaskType.EXTRACTION: SessionPhase.FINISHING,
    TaskType.CLASSIFICATION: SessionPhase.FINISHING,
}

# Representative task_type for each phase (used for step-down)
PHASE_REPRESENTATIVE: dict[SessionPhase, TaskType] = {
    SessionPhase.PLANNING: TaskType.ARCHITECTURE,
    SessionPhase.IMPLEMENTING: TaskType.CODE_GENERATION,
    SessionPhase.ITERATING: TaskType.DEBUGGING,
    SessionPhase.FINISHING: TaskType.SUMMARIZATION,
}


@dataclass(frozen=True)
class StepDownPolicy:
    """Controls how aggressively session phase overrides classification."""
    window_size: int
    min_window: int
    transition_threshold: float
    re_escalation_confidence: float

    @staticmethod
    def aggressive() -> StepDownPolicy:
        return StepDownPolicy(window_size=5, min_window=2, transition_threshold=0.5, re_escalation_confidence=0.9)

    @staticmethod
    def moderate() -> StepDownPolicy:
        return StepDownPolicy(window_size=8, min_window=3, transition_threshold=0.6, re_escalation_confidence=0.8)

    @staticmethod
    def conservative() -> StepDownPolicy:
        return StepDownPolicy(window_size=12, min_window=5, transition_threshold=0.7, re_escalation_confidence=0.7)


_POLICY_PRESETS: dict[str, StepDownPolicy] = {
    "aggressive": StepDownPolicy.aggressive(),
    "moderate": StepDownPolicy.moderate(),
    "conservative": StepDownPolicy.conservative(),
}


def get_step_down_policy(name: str) -> StepDownPolicy:
    """Get a named step-down policy preset."""
    return _POLICY_PRESETS.get(name, StepDownPolicy.moderate())


@dataclass
class _SessionState:
    history: deque[TaskType]
    phase: SessionPhase = SessionPhase.PLANNING
    last_seen: float = field(default_factory=time.monotonic)


def _detect_phase(history: deque[TaskType], policy: StepDownPolicy) -> SessionPhase:
    """Majority vote over the sliding window to determine session phase.

    Returns PLANNING when the window is too small (< min_window).
    """
    if len(history) < policy.min_window:
        return SessionPhase.PLANNING

    # Deque maxlen already constrains to window_size, iterate directly
    phase_counts: dict[SessionPhase, int] = {}
    for task in history:
        phase = TASK_TO_PHASE.get(task, SessionPhase.ITERATING)
        phase_counts[phase] = phase_counts.get(phase, 0) + 1

    total = len(history)
    # Check if any phase exceeds transition threshold
    for phase, count in sorted(phase_counts.items(), key=lambda x: x[1], reverse=True):
        if count / total >= policy.transition_threshold:
            return phase

    # No clear majority — return the most common phase
    return max(phase_counts, key=lambda p: phase_counts[p])


class SessionTracker:
    """Track per-session task history and detect session phases."""

    def __init__(self, policy: StepDownPolicy, max_age_s: int = 3600) -> None:
        self._policy = policy
        self._max_age_s = max_age_s
        self._sessions: dict[str, _SessionState] = {}

    def record(self, session_id: str, task_type: TaskType) -> SessionPhase:
        """Append a task to the session history and return the detected phase."""
        state = self._sessions.get(session_id)
        if state is None:
            state = _SessionState(
                history=deque(maxlen=self._policy.window_size),
            )
            self._sessions[session_id] = state

        state.history.append(task_type)
        state.last_seen = time.monotonic()
        state.phase = _detect_phase(state.history, self._policy)
        return state.phase

    def get_phase(self, session_id: str) -> SessionPhase | None:
        """Return the current phase for a session, or None if unknown."""
        state = self._sessions.get(session_id)
        return state.phase if state is not None else None

    def prune(self) -> int:
        """Evict sessions older than max_age_s. Returns count evicted."""
        cutoff = time.monotonic() - self._max_age_s
        stale = [sid for sid, s in self._sessions.items() if s.last_seen < cutoff]
        for sid in stale:
            del self._sessions[sid]
        return len(stale)

    @property
    def re_escalation_confidence(self) -> float:
        return self._policy.re_escalation_confidence

    @property
    def session_count(self) -> int:
        return len(self._sessions)
