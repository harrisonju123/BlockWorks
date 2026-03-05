"""Task distribution for decentralized benchmark validation.

Creates validation tasks and assigns them to validators using
stake-weighted random selection. Higher-staked validators are more
likely to be selected, aligning economic incentives with network
security.
"""

from __future__ import annotations

import logging
import random
import uuid
from datetime import timedelta

from blockthrough.utils import utcnow
from blockthrough.validators.registry import ValidatorRegistry
from blockthrough.validators.types import ValidationTask

logger = logging.getLogger(__name__)


class TaskDistributionError(Exception):
    """Raised when task creation or assignment fails."""


class TaskDistributor:
    """Manages validation task lifecycle and validator assignment.

    Holds all pending/active tasks in memory and delegates validator
    lookups to the registry.
    """

    def __init__(
        self,
        registry: ValidatorRegistry,
        task_deadline_hours: int = 24,
    ) -> None:
        self._registry = registry
        self._tasks: dict[str, ValidationTask] = {}
        self._task_deadline_hours = task_deadline_hours
        # Track which validators are assigned to which tasks so
        # we can prevent double-assignment.
        self._assignments: dict[str, set[str]] = {}

    def create_task(
        self,
        benchmark_model: str,
        task_type: str,
        prompt_hash: str,
        completion_hash: str,
    ) -> ValidationTask:
        """Create a new validation task and store it."""
        now = utcnow()
        task = ValidationTask(
            task_id=str(uuid.uuid4()),
            benchmark_model=benchmark_model,
            task_type=task_type,
            prompt_hash=prompt_hash,
            original_completion_hash=completion_hash,
            created_at=now,
            deadline=now + timedelta(hours=self._task_deadline_hours),
        )
        self._tasks[task.task_id] = task
        self._assignments[task.task_id] = set()
        return task

    def get_task(self, task_id: str) -> ValidationTask | None:
        """Look up a task by ID."""
        return self._tasks.get(task_id)

    def assign_validators(
        self,
        task_id: str,
        count: int = 3,
        rng: random.Random | None = None,
    ) -> list[str]:
        """Select validators for a task using stake-weighted random sampling.

        Uses the registry's active validators, weighted by stake_amount.
        Validators already assigned to this task are excluded.

        The optional rng parameter allows deterministic selection in tests.

        Raises TaskDistributionError if:
          - The task doesn't exist
          - Not enough eligible validators are available
        """
        task = self._tasks.get(task_id)
        if task is None:
            raise TaskDistributionError(f"Task {task_id} not found")

        already_assigned = self._assignments.get(task_id, set())
        active = [
            v
            for v in self._registry.get_active_validators()
            if v.address not in already_assigned
        ]

        if len(active) < count:
            raise TaskDistributionError(
                f"Need {count} validators but only {len(active)} eligible"
            )

        _rng = rng or random.Random()

        # Stake-weighted selection without replacement
        selected = _weighted_sample(active, count, _rng)
        addresses = [v.address for v in selected]

        task.assigned_validators.extend(addresses)
        self._assignments[task_id].update(addresses)

        logger.info(
            "Assigned %d validators to task %s: %s",
            count,
            task_id,
            addresses,
        )
        return addresses


def _weighted_sample(
    validators: list,
    count: int,
    rng: random.Random,
) -> list:
    """Stake-weighted random sampling without replacement.

    Uses the standard reservoir approach: draw one at a time from
    the weighted distribution, removing selected items each round.
    """
    pool = list(validators)
    selected = []

    for _ in range(count):
        weights = [v.stake_amount for v in pool]
        total = sum(weights)
        if total <= 0:
            break

        # cumulative distribution for weighted pick
        r = rng.random() * total
        cumulative = 0.0
        chosen_idx = len(pool) - 1
        for i, w in enumerate(weights):
            cumulative += w
            if r <= cumulative:
                chosen_idx = i
                break

        selected.append(pool.pop(chosen_idx))

    return selected
