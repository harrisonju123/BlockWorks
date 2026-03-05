#!/usr/bin/env python3
"""Seed realistic demo data for the Blockthrough dashboard.

Standalone script using asyncpg (matching EventWriter._flush pattern).
Designed to run inside the api container:
    docker compose exec api python /app/scripts/seed_demo.py

Idempotent: TRUNCATEs all tables before insert so it can be re-run safely.

Generates 30 days of data with:
- Growth ramp (low → peak → plateau)
- Business-hours weighting and weekend damping
- Three org personas with distinct model/task preferences
- Intentional waste patterns (~15% expensive-model-on-simple-task)
- Two error spike incidents (day 5, day 18)
- Coherent agent sessions with shared traces
- Event-correlated routing decisions and benchmarks
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import urllib.error
import urllib.request
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg

from blockthrough.attestation.builder import ZERO_HASH
from blockthrough.attestation.hashing import (
    build_merkle_root,
    compute_chain_hash,
    hash_metrics,
    hash_org_id,
)
from blockthrough.attestation.types import AttestationMetrics, AttestationRecord, TraceEvaluation

from blockthrough.waste.suggest import _SIMPLE_TASKS as _SIMPLE_TASK_ENUMS
from blockthrough.models import MODEL_CATALOG
from blockthrough.pipeline.writer import _EVENT_COLUMNS, _TOOL_CALL_COLUMNS
from blockthrough.types import EventStatus, TaskType
from blockthrough.utils import infer_provider, utcnow

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_DB_URL = "postgresql://agentproof:localdev@db:5432/agentproof"

# Derive from env (strip +asyncpg prefix used by SQLAlchemy)
_env_url = os.environ.get("AGENTPROOF_DATABASE_URL", _DEFAULT_DB_URL)
DB_URL = _env_url.replace("postgresql+asyncpg://", "postgresql://")

# Derive models from the canonical catalog
_SEED_MODELS = [
    # Tier 1: Opus-class + GPT-5.2 (~12% traffic)
    "claude-opus-4-5",
    "claude-opus-4-6",
    "claude-opus-4-20250514",
    "gpt-5.2-chat-latest",
    # Tier 2: Sonnet / strong mid-tier (~48% traffic)
    "claude-sonnet-4-6",
    "claude-sonnet-4-20250514",
    "gpt-4o",
    "qwen.qwen3-vl-235b-a22b",
    "moonshot.kimi-k2-thinking",
    "openai.gpt-oss-120b-1:0",
    "minimax.minimax-m2.1",
    # Tier 3: Budget (~40% traffic)
    "claude-haiku-4-5-20251001",
    "gpt-4o-mini",
    "google.gemma-3-27b-it",
    "mistral.ministral-3-8b-instruct",
    "us.amazon.nova-2-lite-v1:0",
]


@dataclass(frozen=True)
class SeedModel:
    name: str
    provider: str
    tier: int
    cost_in: float
    cost_out: float


MODELS: list[SeedModel] = [
    SeedModel(
        name=name,
        provider=infer_provider(name),
        tier=MODEL_CATALOG[name].tier,
        cost_in=MODEL_CATALOG[name].cost_per_1k_input,
        cost_out=MODEL_CATALOG[name].cost_per_1k_output,
    )
    for name in _SEED_MODELS
]

MODEL_BY_NAME: dict[str, SeedModel] = {m.name: m for m in MODELS}

TASK_TYPES = [t.value for t in TaskType if t != TaskType.UNKNOWN]

MCP_SERVERS = ["filesystem", "github", "slack", "postgres"]
MCP_METHODS = {
    "filesystem": ["read_file", "write_file", "list_directory"],
    "github": ["create_issue", "list_pulls", "get_repo"],
    "slack": ["send_message", "list_channels"],
    "postgres": ["query", "list_tables", "describe_table"],
}

TOOL_NAMES = [
    "web_search", "calculator", "file_reader", "code_interpreter",
    "image_gen", "sql_query", "api_call", "shell_exec",
]

AGENT_FRAMEWORKS = ["langchain", "crewai", "autogen", "claude-code"]

ROUTING_REASONS = [
    "cost_optimization",
    "quality_threshold_met",
    "latency_constraint",
    "budget_limit",
    "task_fitness_score",
    "model_unavailable",
]

# Derive string set from canonical enum set in waste.py
_SIMPLE_TASKS = {t.value for t in _SIMPLE_TASK_ENUMS}

NOW = utcnow()
SEED_START = NOW - timedelta(days=30)

random.seed(42)  # Reproducible demo data

# ---------------------------------------------------------------------------
# Org Profiles
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OrgProfile:
    org_id: str
    share: float  # fraction of total events
    error_rate: float
    users: tuple[str, ...]
    frameworks: tuple[str, ...]
    agent_names: tuple[str, ...]
    # Parallel to MODELS list — per-org model selection weights
    model_weights: tuple[float, ...]
    # Parallel to TASK_TYPES list — per-org task selection weights
    task_weights: tuple[float, ...]
    routing_group: str


ORG_PROFILES = [
    OrgProfile(
        org_id="org-acme",
        share=0.45,
        error_rate=0.03,
        users=("alice", "bob", "charlie", "diana"),
        frameworks=("claude-code", "langchain"),
        agent_names=("code-assistant", "reviewer", "planner"),
        # Heavy Opus/Sonnet: ~21% T1, ~49% T2, ~30% T3
        model_weights=(
            0.07, 0.08, 0.06,           # T1
            0.10, 0.12, 0.06, 0.05, 0.04, 0.03, 0.03, 0.06,  # T2
            0.10, 0.08, 0.05, 0.04, 0.03,  # T3
        ),
        # Focus: code_generation, reasoning
        task_weights=(
            0.25, 0.15, 0.05, 0.05, 0.05, 0.25, 0.10, 0.10,
        ),
        routing_group="quality-first",
    ),
    OrgProfile(
        org_id="org-widgets",
        share=0.30,
        error_rate=0.05,
        users=("eve", "frank", "grace"),
        frameworks=("crewai", "autogen", "langchain"),
        agent_names=("data-analyst", "planner", "code-assistant"),
        # Balanced — mirrors global weights
        model_weights=(
            0.03, 0.04, 0.05,           # T1
            0.08, 0.10, 0.07, 0.06, 0.05, 0.04, 0.04, 0.04,  # T2
            0.12, 0.10, 0.07, 0.06, 0.05,  # T3
        ),
        # Mixed across all types
        task_weights=(
            0.14, 0.12, 0.12, 0.12, 0.12, 0.12, 0.14, 0.12,
        ),
        routing_group="balanced",
    ),
    OrgProfile(
        org_id="org-labs",
        share=0.25,
        error_rate=0.04,
        users=("hank", "iris", "jack", "kim", "leo"),
        frameworks=("autogen", "claude-code"),
        agent_names=("classifier", "extractor", "chat-agent"),
        # Heavy cheap: ~4% T1, ~34% T2, ~62% T3
        model_weights=(
            0.01, 0.02, 0.01,           # T1
            0.04, 0.05, 0.04, 0.04, 0.04, 0.05, 0.04, 0.04,  # T2
            0.20, 0.16, 0.12, 0.08, 0.06,  # T3
        ),
        # Focus: classification, extraction, conversation
        task_weights=(
            0.06, 0.06, 0.22, 0.08, 0.20, 0.06, 0.22, 0.10,
        ),
        routing_group="cost-optimized",
    ),
]

# Validate weight arrays are aligned
for _p in ORG_PROFILES:
    assert len(_p.model_weights) == len(MODELS), (
        f"{_p.org_id} model_weights len {len(_p.model_weights)} != MODELS len {len(MODELS)}"
    )
    assert len(_p.task_weights) == len(TASK_TYPES), (
        f"{_p.org_id} task_weights len {len(_p.task_weights)} != TASK_TYPES len {len(TASK_TYPES)}"
    )

_ORG_BY_ID = {p.org_id: p for p in ORG_PROFILES}
_ORG_SHARES = tuple(p.share for p in ORG_PROFILES)

# ---------------------------------------------------------------------------
# Time Distribution
# ---------------------------------------------------------------------------

# Day weights: ramp up days 0-9, peak 10-24, plateau 25-29
_DAY_WEIGHTS: list[float] = []
for _d in range(30):
    if _d < 10:
        # Ramp: 0.5 → 1.0 linearly
        _DAY_WEIGHTS.append(0.5 + 0.5 * (_d / 9))
    elif _d < 25:
        # Peak
        _DAY_WEIGHTS.append(1.0)
    else:
        # Plateau (slight decline)
        _DAY_WEIGHTS.append(0.85)

# Hour weights: business hours 9-18 get 70% of daily traffic
_HOUR_WEIGHTS: list[float] = []
for _h in range(24):
    if 9 <= _h < 18:
        _HOUR_WEIGHTS.append(3.0)   # 9 hours × 3.0 = 27 → 27/(27+4.5) ≈ 86% but damped by weekend
    elif 7 <= _h < 9 or 18 <= _h < 21:
        _HOUR_WEIGHTS.append(1.0)   # shoulder hours
    else:
        _HOUR_WEIGHTS.append(0.3)   # night

# ---------------------------------------------------------------------------
# Error Spikes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ErrorSpike:
    day: int
    hour_start: int
    hour_end: int
    rate: float
    error_type: str


_ERROR_SPIKES = [
    ErrorSpike(day=5, hour_start=14, hour_end=16, rate=0.15, error_type="rate_limit"),
    ErrorSpike(day=18, hour_start=10, hour_end=11, rate=0.12, error_type="timeout"),
]


def effective_error(ts: datetime, baseline: float) -> tuple[float, str | None]:
    """Return (error_rate, forced_error_type) accounting for spike windows."""
    day_offset = (ts - SEED_START).days
    hour = ts.hour
    for spike in _ERROR_SPIKES:
        if day_offset == spike.day and spike.hour_start <= hour < spike.hour_end:
            return spike.rate, spike.error_type
    return baseline, None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fake_hash() -> str:
    return hashlib.sha256(uuid.uuid4().bytes).hexdigest()


def realistic_ts() -> datetime:
    """Pick a timestamp weighted by day growth curve + hour-of-day pattern + weekend damping."""
    day_idx = random.choices(range(30), weights=_DAY_WEIGHTS, k=1)[0]
    hour_idx = random.choices(range(24), weights=_HOUR_WEIGHTS, k=1)[0]

    base_date = SEED_START + timedelta(days=day_idx)
    # Weekend damping: 30% of weekday traffic
    weekday = base_date.weekday()
    if weekday >= 5:  # Sat=5, Sun=6
        if random.random() > 0.30:
            # Re-roll to a weekday in the same week
            shift = weekday - 4  # Sat→1, Sun→2 back to Friday
            base_date = base_date - timedelta(days=shift)

    ts = base_date.replace(
        hour=hour_idx,
        minute=random.randint(0, 59),
        second=random.randint(0, 59),
        microsecond=random.randint(0, 999999),
    )
    # Clamp to seed window
    if ts < SEED_START:
        ts = SEED_START + timedelta(seconds=random.randint(0, 3600))
    if ts > NOW:
        ts = NOW - timedelta(seconds=random.randint(0, 3600))
    return ts


def weighted_choice(items: list | tuple, weights: list | tuple):
    return random.choices(items, weights=weights, k=1)[0]


# ---------------------------------------------------------------------------
# Typed event row — eliminates magic-index tuple access
# ---------------------------------------------------------------------------

@dataclass
class EventRow:
    """Mirrors llm_events columns for cross-generator access."""
    event_id: uuid.UUID
    ts: datetime
    status: str
    provider: str
    model: str
    estimated_cost: float
    latency_ms: float
    trace_id: str
    task_type: str
    org_id: str
    session_id: str | None

    # The full DB row tuple for COPY
    db_row: tuple


# ---------------------------------------------------------------------------
# Agent Sessions — coherent multi-event traces
# ---------------------------------------------------------------------------

@dataclass
class AgentSession:
    session_id: str
    trace_id: str
    org: OrgProfile
    user: str
    framework: str
    agent_name: str
    model: SeedModel
    num_events: int
    base_ts: datetime
    span_ids: list[str] = field(default_factory=list)


def generate_sessions(n_sessions: int = 500) -> list[AgentSession]:
    """Pre-generate coherent agent sessions."""
    sessions = []
    for _ in range(n_sessions):
        org = weighted_choice(ORG_PROFILES, _ORG_SHARES)
        model = weighted_choice(MODELS, org.model_weights)
        num_events = random.randint(5, 50)

        session = AgentSession(
            session_id=f"session-{uuid.uuid4().hex[:12]}",
            trace_id=str(uuid.uuid4()),
            org=org,
            user=random.choice(org.users),
            framework=random.choice(org.frameworks),
            agent_name=random.choice(org.agent_names),
            model=model,
            num_events=num_events,
            base_ts=realistic_ts(),
        )
        # Pre-generate span IDs for chaining
        session.span_ids = [uuid.uuid4().hex[:16] for _ in range(num_events)]
        sessions.append(session)
    return sessions


# ---------------------------------------------------------------------------
# Data generators
# ---------------------------------------------------------------------------

def _make_event(
    *,
    model: SeedModel,
    ts: datetime,
    task_type: str,
    org: OrgProfile,
    user: str,
    framework: str | None,
    agent_name: str | None,
    session_id: str | None,
    trace_id: str,
    span_id: str,
    parent_span_id: str | None,
) -> tuple[EventRow, list[tuple]]:
    """Build one event + its tool_call rows."""
    # Token counts reflect real agentic workloads: multi-turn conversations
    # accumulate large context windows (50k-200k), and code generation
    # tasks often include full file contents + conversation history.
    if task_type in ("code_generation", "reasoning"):
        prompt_tokens = random.randint(15000, 100000)
        completion_tokens = random.randint(2000, 12000)
    elif task_type in ("classification", "extraction"):
        prompt_tokens = random.randint(2000, 10000)
        completion_tokens = random.randint(100, 1000)
    else:
        prompt_tokens = random.randint(8000, 50000)
        completion_tokens = random.randint(500, 5000)

    total_tokens = prompt_tokens + completion_tokens
    estimated_cost = (prompt_tokens / 1000 * model.cost_in) + (completion_tokens / 1000 * model.cost_out)

    error_rate, forced_error_type = effective_error(ts, org.error_rate)
    is_failure = random.random() < error_rate
    status = EventStatus.FAILURE.value if is_failure else EventStatus.SUCCESS.value
    if is_failure:
        error_type = forced_error_type or random.choice(["rate_limit", "timeout", "invalid_request"])
        error_hash = fake_hash()
    else:
        error_type = None
        error_hash = None

    has_tool_calls = random.random() < 0.30
    confidence = round(random.uniform(0.70, 0.98), 2)

    latency_ms = random.uniform(200, 5000) if model.tier <= 2 else random.uniform(100, 1500)
    ttft = latency_ms * random.uniform(0.05, 0.20)

    cache_read = random.randint(0, 200) if random.random() < 0.3 else 0
    cache_creation = random.randint(0, 100) if random.random() < 0.1 else 0

    event_id = uuid.uuid4()
    db_row = (
        event_id,
        ts,
        status,
        model.provider,
        model.name,
        None,  # model_group
        prompt_tokens,
        completion_tokens,
        total_tokens,
        cache_read,
        cache_creation,
        round(estimated_cost, 6),
        None,  # custom_pricing
        round(latency_ms, 1),
        round(ttft, 1),
        fake_hash(),  # prompt_hash
        fake_hash(),  # completion_hash
        fake_hash() if random.random() < 0.7 else None,  # system_prompt_hash
        session_id,
        trace_id,
        span_id,
        parent_span_id,
        framework,
        agent_name,
        has_tool_calls,
        task_type,
        confidence,
        error_type,
        error_hash,
        str(uuid.uuid4()),  # litellm_call_id
        None,  # api_base
        org.org_id,
        user,
        None,  # custom_metadata
    )

    event = EventRow(
        event_id=event_id,
        ts=ts,
        status=status,
        provider=model.provider,
        model=model.name,
        estimated_cost=round(estimated_cost, 6),
        latency_ms=round(latency_ms, 1),
        trace_id=trace_id,
        task_type=task_type,
        org_id=org.org_id,
        session_id=session_id,
        db_row=db_row,
    )

    tool_call_rows: list[tuple] = []
    if has_tool_calls:
        num_tools = random.randint(1, 4)
        for _ in range(num_tools):
            tool_call_rows.append((
                uuid.uuid4(),
                event_id,
                ts,
                random.choice(TOOL_NAMES),
                fake_hash(),
                fake_hash() if random.random() < 0.8 else None,
            ))

    return event, tool_call_rows


def generate_events(n: int = 50000) -> tuple[list[EventRow], list[tuple]]:
    """Generate llm_events and tool_calls rows.

    Two phases:
    1. Session events (~13,750): coherent multi-step agent traces
    2. Standalone events (~36,250): org-profile-weighted with 15% waste injection
    """
    events: list[EventRow] = []
    tool_call_rows: list[tuple] = []

    # Phase 1: Session events (~27% of total from coherent traces)
    n_sessions = max(5, int(n * 500 / 50000))
    sessions = generate_sessions(n_sessions)
    session_event_count = 0

    for sess in sessions:
        for i in range(sess.num_events):
            # Sequential timestamps within the session (5-120s apart)
            ts = sess.base_ts + timedelta(seconds=i * random.randint(5, 120))
            if ts > NOW:
                break

            task_type = weighted_choice(TASK_TYPES, sess.org.task_weights)
            parent_span = sess.span_ids[i - 1] if i > 0 else None

            event, tools = _make_event(
                model=sess.model,
                ts=ts,
                task_type=task_type,
                org=sess.org,
                user=sess.user,
                framework=sess.framework,
                agent_name=sess.agent_name,
                session_id=sess.session_id,
                trace_id=sess.trace_id,
                span_id=sess.span_ids[i],
                parent_span_id=parent_span,
            )
            events.append(event)
            tool_call_rows.extend(tools)
            session_event_count += 1

    # Phase 2: Standalone events to fill up to n
    standalone_target = n - session_event_count
    tier1_models = [m for m in MODELS if m.tier == 1]

    for _ in range(standalone_target):
        org = weighted_choice(ORG_PROFILES, _ORG_SHARES)
        ts = realistic_ts()

        # 15% waste injection: force tier-1 model on a simple task
        if random.random() < 0.15:
            model = random.choice(tier1_models)
            task_type = random.choice(list(_SIMPLE_TASKS))
        else:
            model = weighted_choice(MODELS, org.model_weights)
            task_type = weighted_choice(TASK_TYPES, org.task_weights)

        user = random.choice(org.users)
        framework = random.choice(org.frameworks) if random.random() < 0.6 else None
        agent_name = random.choice(org.agent_names) if framework else None

        event, tools = _make_event(
            model=model,
            ts=ts,
            task_type=task_type,
            org=org,
            user=user,
            framework=framework,
            agent_name=agent_name,
            session_id=None,
            trace_id=str(uuid.uuid4()),
            span_id=uuid.uuid4().hex[:16],
            parent_span_id=None,
        )
        events.append(event)
        tool_call_rows.extend(tools)

    # Sort by timestamp for realistic insertion order
    events.sort(key=lambda e: e.ts)

    return events, tool_call_rows


# Load real benchmark distributions for quality score sampling
_REAL_BENCHMARKS: dict | None = None
_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "real_benchmarks.json"


def _load_real_benchmarks() -> dict:
    global _REAL_BENCHMARKS
    if _REAL_BENCHMARKS is not None:
        return _REAL_BENCHMARKS
    try:
        with open(_FIXTURE_PATH) as f:
            data = json.load(f)
        _REAL_BENCHMARKS = data.get("distributions", {})
    except (FileNotFoundError, json.JSONDecodeError):
        _REAL_BENCHMARKS = {}
    return _REAL_BENCHMARKS


def _sample_quality(task_type: str, bench_tier: int, original_tier: int) -> float:
    """Sample quality score with bounded noise to prevent tier inversions.

    Uses ±0.03 uniform noise around the distribution mean instead of unbounded
    Gaussian. This guarantees that tier 2 averages always beat tier 3 in seeded
    demo data, which matches real-world expectations.
    """
    dists = _load_real_benchmarks()
    tier_key = f"tier_{bench_tier}"
    dist = dists.get(task_type, {}).get(tier_key)

    if dist:
        mean = dist["mean"]
        noise = random.uniform(-0.03, 0.03)
        return round(max(0.0, min(1.0, mean + noise)), 3)

    # Fallback: tier-based ranges matching normalized MODEL_CATALOG averages
    base = {1: 0.91, 2: 0.79, 3: 0.55}.get(bench_tier, 0.79)
    noise = random.uniform(-0.03, 0.03)
    return round(max(0.0, min(1.0, base + noise)), 3)


def generate_benchmark_results(events: list[EventRow], n: int = 8000) -> list[tuple]:
    """Generate benchmark_results: tier-aware model comparisons."""
    rows = []
    success_events = [e for e in events if e.status == EventStatus.SUCCESS.value]
    sample = random.sample(success_events, min(n, len(success_events)))

    # Group models by tier for downgrade pairing
    tier_models: dict[int, list[SeedModel]] = {}
    for m in MODELS:
        tier_models.setdefault(m.tier, []).append(m)

    for event in sample:
        ts = event.ts + timedelta(seconds=random.randint(5, 60))
        original = MODEL_BY_NAME[event.model]

        # Tier-aware: benchmark against cheaper tier (the downgrade story)
        if original.tier == 1:
            candidates = tier_models.get(2, []) + tier_models.get(3, [])
        elif original.tier == 2:
            candidates = tier_models.get(3, [])
        else:
            # Tier 3: benchmark against other tier-3 models
            candidates = [m for m in tier_models.get(3, []) if m.name != event.model]

        if not candidates:
            continue

        bench_model = random.choice(candidates)

        # Quality scores: sample from real benchmark distributions when available
        quality = _sample_quality(event.task_type, bench_model.tier, original.tier)

        # Cost ratio based on actual model pricing
        if original.cost_in > 0:
            bench_cost = event.estimated_cost * (bench_model.cost_in / original.cost_in)
        else:
            bench_cost = event.estimated_cost * 0.5
        bench_latency = event.latency_ms * random.uniform(0.3, 1.5)

        rows.append((
            uuid.uuid4(),
            ts,
            event.event_id,
            event.model,
            bench_model.name,
            event.task_type,
            quality,
            event.estimated_cost,
            round(bench_cost, 6),
            event.latency_ms,
            round(bench_latency, 1),
            "claude-sonnet-4-6",  # judge_model
            "v1",  # rubric_version
            event.org_id,
        ))

    return rows


def generate_routing_decisions(events: list[EventRow], n: int = 12000) -> list[tuple]:
    """Generate routing_decisions correlated to actual events."""
    rows = []
    sample = random.sample(events, min(n, len(events)))

    for event in sample:
        org = _ORG_BY_ID[event.org_id]
        original_model = MODEL_BY_NAME[event.model]

        # Higher override rate for waste patterns
        is_waste_pattern = (
            original_model.tier <= 2 and event.task_type in _SIMPLE_TASKS
        )
        override_prob = 0.35 if is_waste_pattern else 0.10
        was_overridden = random.random() < override_prob

        if was_overridden:
            cheaper = [m for m in MODELS if m.tier > original_model.tier]
            if cheaper:
                selected_model = random.choice(cheaper).name
            else:
                selected_model = event.model
                was_overridden = False
        else:
            selected_model = event.model

        if was_overridden:
            # Correlated reasons
            if is_waste_pattern:
                reason = random.choice(["cost_optimization", "task_fitness_score"])
            else:
                reason = random.choice(ROUTING_REASONS)
        else:
            reason = None

        rows.append((
            uuid.uuid4(),
            event.ts,
            event.task_type,
            event.model,
            selected_model,
            was_overridden,
            reason,
            random.randint(1, 3),  # policy_version
            org.routing_group,
        ))

    return rows


def generate_mcp_data(events: list[EventRow]) -> tuple[list[tuple], list[tuple]]:
    """Generate mcp_calls and mcp_execution_graph rows."""
    call_rows = []
    graph_rows = []

    # Pick ~2000 events, 1-3 calls each → ~4000 total
    sample = random.sample(events, min(2000, len(events)))
    trace_calls: dict[str, list[uuid.UUID]] = {}

    for event in sample:
        num_calls = random.choices([1, 2, 3], weights=[0.5, 0.3, 0.2], k=1)[0]
        for _ in range(num_calls):
            server = random.choice(MCP_SERVERS)
            method = random.choice(MCP_METHODS[server])

            is_failure = random.random() < 0.05
            call_id = uuid.uuid4()

            call_rows.append((
                call_id,
                event.ts,
                event.event_id,
                event.trace_id,
                server,
                method,
                fake_hash(),  # params_hash
                fake_hash() if not is_failure else None,  # response_hash
                round(random.uniform(10, 500), 1),  # latency_ms
                random.randint(50, 2000) if not is_failure else None,  # response_tokens
                EventStatus.FAILURE.value if is_failure else EventStatus.SUCCESS.value,
                "timeout" if is_failure else None,  # error_type
            ))

            trace_calls.setdefault(event.trace_id, []).append(call_id)

    # Generate ~1200 execution graph edges from traces with multiple calls
    for trace_id, calls in trace_calls.items():
        if len(calls) < 2:
            continue
        for i in range(len(calls) - 1):
            if len(graph_rows) >= 1200:
                break
            graph_rows.append((
                uuid.uuid4(),
                calls[i],      # parent_call_id
                calls[i + 1],  # child_call_id
                trace_id,
            ))
        if len(graph_rows) >= 1200:
            break

    return call_rows, graph_rows


def generate_alert_data(
    events: list[EventRow],
) -> tuple[list[tuple], list[tuple], list[tuple]]:
    """Generate alert_rules, alert_history, and budget_configs.

    Alert history clusters near error spike windows.
    Budget spend is computed from actual event costs.
    """
    rules = []
    history = []
    budgets = []

    rule_configs = [
        ("org-acme", "spend_threshold", {"threshold_usd": 500, "window_hours": 24}, "slack"),
        ("org-acme", "anomaly_zscore", {"z_threshold": 2.5, "metric": "cost"}, "email"),
        ("org-acme", "error_rate", {"threshold_pct": 8, "window_minutes": 60}, "slack"),
        ("org-widgets", "error_rate", {"threshold_pct": 10, "window_minutes": 60}, "both"),
        ("org-widgets", "spend_threshold", {"threshold_usd": 200, "window_hours": 12}, "slack"),
        ("org-labs", "latency_p95", {"threshold_ms": 5000, "window_minutes": 30}, "slack"),
        ("org-labs", "error_rate", {"threshold_pct": 8, "window_minutes": 60}, "email"),
    ]

    # Pre-compute spike timestamps for correlated alerts
    spike_windows = []
    for spike in _ERROR_SPIKES:
        spike_base = SEED_START + timedelta(days=spike.day)
        spike_windows.append((
            spike_base.replace(hour=spike.hour_start),
            spike_base.replace(hour=spike.hour_end),
            spike.error_type,
        ))

    for org_id, rule_type, config, channel in rule_configs:
        rule_id = uuid.uuid4()
        rules.append((
            rule_id,
            org_id,
            rule_type,
            json.dumps(config),
            channel,
            f"https://hooks.slack.com/fake/{org_id}" if channel in ("slack", "both") else None,
            True,
            NOW - timedelta(days=28),
            NOW - timedelta(days=28),
        ))

        # 6-8 alerts per rule
        num_alerts = random.randint(6, 8)
        for i in range(num_alerts):
            # ~50% of error_rate alerts cluster near spike windows
            if rule_type == "error_rate" and spike_windows and random.random() < 0.50:
                spike_start, spike_end, _ = random.choice(spike_windows)
                triggered = spike_start + timedelta(
                    minutes=random.randint(0, int((spike_end - spike_start).total_seconds() / 60) + 30)
                )
            else:
                triggered = realistic_ts()

            is_resolved = random.random() < 0.65
            severity = random.choice(["info", "warning", "critical"])
            history.append((
                uuid.uuid4(),
                rule_id,
                triggered,
                f"{rule_type} alert for {org_id}: threshold breached",
                severity,
                is_resolved,
                triggered + timedelta(minutes=random.randint(5, 120)) if is_resolved else None,
            ))

    # Budget configs with computed spend from actual events
    org_spend: dict[str, float] = {}
    for event in events:
        org_spend[event.org_id] = org_spend.get(event.org_id, 0) + event.estimated_cost

    budget_configs_data = [
        ("org-acme", None, 3500.0, "monthly", "alert"),
        ("org-widgets", "proj-alpha", 400.0, "weekly", "downgrade"),
        ("org-labs", None, 40.0, "daily", "block"),
    ]

    for org_id, project_id, budget_usd, period, action in budget_configs_data:
        actual_spend = org_spend.get(org_id, 0)
        # Scale spend to budget period and cap at 95%
        if period == "monthly":
            period_spend = actual_spend  # 30-day seed ≈ 1 month
        elif period == "weekly":
            period_spend = actual_spend / 4.3  # ~1 week of 30 days
        else:  # daily
            period_spend = actual_spend / 30

        # Cap to 95% of budget to avoid confusing "over budget" state
        current_spend = min(round(period_spend, 2), round(budget_usd * 0.95, 2))

        budgets.append((
            uuid.uuid4(),
            org_id,
            project_id,
            budget_usd,
            period,
            action,
            current_spend,
            NOW - timedelta(days=random.randint(1, 7)),
            NOW - timedelta(days=28),
        ))

    return rules, history, budgets


# ---------------------------------------------------------------------------
# Blockchain seeding — validators + attestation chains via HTTP
# ---------------------------------------------------------------------------

_API_BASE = "http://localhost:8100/api/v1"


def _api_post(path: str, data: dict) -> dict | None:
    """POST JSON to the running API. Returns parsed response or None on error."""
    url = f"{_API_BASE}{path}"
    body = json.dumps(data, default=str).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode() if e.fp else str(e)
        print(f"  WARNING: {path} failed ({e.code}): {detail}")
        return None
    except Exception as e:
        print(f"  WARNING: {path} failed: {e}")
        return None


def _api_get(path: str) -> dict | None:
    """GET JSON from the running API. Returns parsed response or None on error/404."""
    url = f"{_API_BASE}{path}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError:
        return None
    except Exception:
        return None


def seed_blockchain_data(events: list[EventRow]) -> None:
    """Register validators and build attestation chains via the running API.

    Must be called after DB seeding + aggregate refresh, since attestation
    and validator state lives in-memory on the API server (LocalProvider).
    """
    print("\nSeeding blockchain data via API...")

    # -- 1. Register 25 validators (3 tiers by stake) --
    rng = random.Random(42)
    validators = []
    tiers = [
        ("alpha", 5, 10.0, 12.0),   # 5 Tier-1 high-stake
        ("beta", 8, 2.0, 4.0),      # 8 Tier-2 medium-stake
        ("gamma", 12, 0.5, 1.0),    # 12 Tier-3 newcomers
    ]

    for tier_name, count, stake_lo, stake_hi in tiers:
        for i in range(count):
            addr = f"0x{tier_name}_{i:02d}_{rng.randint(0, 2**32 - 1):08x}"
            stake = round(rng.uniform(stake_lo, stake_hi), 2)
            resp = _api_post("/validators/register", {
                "address": addr,
                "stake_amount": stake,
            })
            if resp:
                validators.append(addr)

    print(f"  Registered {len(validators)} validators")

    # -- 2. Build attestation chains: 4 weekly periods × 3 orgs --
    org_ids = [p.org_id for p in ORG_PROFILES]
    week_seconds = 7 * 24 * 3600

    for org_id in org_ids:
        org_hash = hash_org_id(org_id)
        org_events = [e for e in events if e.org_id == org_id]

        # Query existing on-chain state (scheduler or previous seed may have
        # submitted records). Build from the existing chain tip.
        existing = _api_get(f"/attestations/latest/{org_hash}")
        if existing:
            prev_record = AttestationRecord(
                org_id_hash=existing["org_id_hash"],
                period_start=datetime.fromisoformat(existing["period_start"]),
                period_end=datetime.fromisoformat(existing["period_end"]),
                metrics_hash=existing["metrics_hash"],
                benchmark_hash=existing["benchmark_hash"],
                merkle_root=existing["merkle_root"],
                prev_hash=existing["prev_hash"],
                nonce=existing["nonce"],
                timestamp=datetime.fromisoformat(existing["timestamp"]),
            )
            next_nonce = existing["nonce"] + 1
            print(f"  {org_id}: existing chain at nonce {existing['nonce']}, continuing from {next_nonce}")
        else:
            prev_record = None
            next_nonce = 1

        for week_idx in range(4):
            period_start = SEED_START + timedelta(seconds=week_idx * week_seconds)
            period_end = period_start + timedelta(seconds=week_seconds)

            # Gather events in this weekly window
            week_events = [
                e for e in org_events if period_start <= e.ts < period_end
            ]
            if not week_events:
                continue

            # Single-pass aggregation over week_events
            total_spend = 0.0
            failure_count = 0
            waste_count = 0
            model_counts: Counter[str] = Counter()
            trace_groups: dict[str, list[EventRow]] = {}

            for e in week_events:
                total_spend += e.estimated_cost
                if e.status == EventStatus.FAILURE.value:
                    failure_count += 1
                model_counts[e.model] += 1
                if (e.model in MODEL_BY_NAME
                        and MODEL_BY_NAME[e.model].tier == 1
                        and e.task_type in _SIMPLE_TASKS):
                    waste_count += 1
                trace_groups.setdefault(e.trace_id, []).append(e)

            request_count = len(week_events)
            failure_rate = failure_count / request_count
            waste_score = waste_count / request_count

            metrics = AttestationMetrics(
                total_spend=total_spend,
                waste_score=waste_score,
                request_count=request_count,
                failure_rate=failure_rate,
                model_distribution=dict(model_counts),
            )

            # Build TraceEvaluations from grouped events
            trace_evals: list[TraceEvaluation] = []
            for trace_id, trace_events in trace_groups.items():
                models = Counter(e.model for e in trace_events)
                tasks = Counter(e.task_type for e in trace_events)
                trace_cost = sum(e.estimated_cost for e in trace_events)
                successes = sum(
                    1 for e in trace_events
                    if e.status == EventStatus.SUCCESS.value
                )
                quality = successes / len(trace_events)

                ts = trace_events[0].ts
                trace_evals.append(TraceEvaluation(
                    trace_id=trace_id,
                    model=models.most_common(1)[0][0],
                    task_type=tasks.most_common(1)[0][0],
                    cost=trace_cost,
                    quality_score=round(quality, 4),
                    timestamp=ts.replace(tzinfo=timezone.utc)
                    if ts.tzinfo is None else ts,
                ))

            # Hash everything
            metrics_hash = hash_metrics(metrics)
            merkle_root = build_merkle_root(trace_evals)
            # benchmark_hash: hash of the metrics as a stand-in
            benchmark_hash = hashlib.sha256(metrics_hash.encode()).hexdigest()

            nonce = next_nonce
            prev_hash = ZERO_HASH if prev_record is None else compute_chain_hash(prev_record)

            # Build the record so we can chain from it
            record = AttestationRecord(
                org_id_hash=org_hash,
                period_start=period_start.replace(tzinfo=timezone.utc)
                if period_start.tzinfo is None else period_start,
                period_end=period_end.replace(tzinfo=timezone.utc)
                if period_end.tzinfo is None else period_end,
                metrics_hash=metrics_hash,
                benchmark_hash=benchmark_hash,
                merkle_root=merkle_root,
                prev_hash=prev_hash,
                nonce=nonce,
                timestamp=period_end.replace(tzinfo=timezone.utc)
                if period_end.tzinfo is None else period_end,
            )

            resp = _api_post("/attestations/submit", {
                "org_id_hash": record.org_id_hash,
                "period_start": record.period_start.isoformat(),
                "period_end": record.period_end.isoformat(),
                "metrics_hash": record.metrics_hash,
                "benchmark_hash": record.benchmark_hash,
                "merkle_root": record.merkle_root,
                "prev_hash": record.prev_hash,
                "nonce": record.nonce,
                "timestamp": record.timestamp.isoformat(),
            })

            if resp:
                prev_record = record
                next_nonce += 1
            else:
                print(f"  WARNING: chain broken for {org_id} at nonce {nonce}")
                break

        submitted = next_nonce - 1
        print(f"  {org_id}: {submitted} attestations (org_hash={org_hash[:16]}...)")

    print("Blockchain seeding complete.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    print("Connecting to database...")
    conn = await asyncpg.connect(DB_URL)

    try:
        # Generate data before starting the transaction
        print("Generating demo data (50,000 events over 30 days)...")
        events, tool_call_rows = generate_events(50000)
        event_rows = [e.db_row for e in events]
        benchmark_rows = generate_benchmark_results(events, 8000)
        routing_rows = generate_routing_decisions(events, 12000)
        mcp_call_rows, mcp_graph_rows = generate_mcp_data(events)
        alert_rules, alert_history, budget_configs = generate_alert_data(events)

        # Atomic: truncate + insert in a single transaction
        print("Truncating tables and inserting data...")
        async with conn.transaction():
            await conn.execute("""
                TRUNCATE mcp_execution_graph CASCADE;
                TRUNCATE mcp_calls CASCADE;
                TRUNCATE tool_calls CASCADE;
                TRUNCATE benchmark_results CASCADE;
                TRUNCATE routing_decisions CASCADE;
                TRUNCATE alert_history CASCADE;
                TRUNCATE alert_rules CASCADE;
                TRUNCATE budget_configs CASCADE;
                TRUNCATE llm_events CASCADE;
            """)

            print(f"  {len(event_rows)} events...")
            await conn.copy_records_to_table(
                "llm_events", records=event_rows, columns=list(_EVENT_COLUMNS),
            )

            print(f"  {len(tool_call_rows)} tool calls...")
            await conn.copy_records_to_table(
                "tool_calls", records=tool_call_rows, columns=list(_TOOL_CALL_COLUMNS),
            )

            print(f"  {len(benchmark_rows)} benchmark results...")
            await conn.copy_records_to_table("benchmark_results", records=benchmark_rows, columns=[
                "id", "created_at", "original_event_id", "original_model", "benchmark_model",
                "task_type", "quality_score", "original_cost", "benchmark_cost",
                "original_latency_ms", "benchmark_latency_ms", "judge_model", "rubric_version", "org_id",
            ])

            print(f"  {len(routing_rows)} routing decisions...")
            await conn.copy_records_to_table("routing_decisions", records=routing_rows, columns=[
                "id", "created_at", "task_type", "requested_model", "selected_model",
                "was_overridden", "reason", "policy_version", "group_name",
            ])

            print(f"  {len(mcp_call_rows)} MCP calls...")
            await conn.copy_records_to_table("mcp_calls", records=mcp_call_rows, columns=[
                "id", "created_at", "event_id", "trace_id", "server_name", "method",
                "params_hash", "response_hash", "latency_ms", "response_tokens", "status", "error_type",
            ])

            print(f"  {len(mcp_graph_rows)} MCP execution graph edges...")
            await conn.copy_records_to_table("mcp_execution_graph", records=mcp_graph_rows, columns=[
                "id", "parent_call_id", "child_call_id", "trace_id",
            ])

            print(f"  {len(alert_rules)} alert rules...")
            await conn.copy_records_to_table("alert_rules", records=alert_rules, columns=[
                "id", "org_id", "rule_type", "threshold_config", "channel",
                "webhook_url", "enabled", "created_at", "updated_at",
            ])

            print(f"  {len(alert_history)} alert history records...")
            await conn.copy_records_to_table("alert_history", records=alert_history, columns=[
                "id", "rule_id", "triggered_at", "message", "severity", "resolved", "resolved_at",
            ])

            print(f"  {len(budget_configs)} budget configs...")
            await conn.copy_records_to_table("budget_configs", records=budget_configs, columns=[
                "id", "org_id", "project_id", "budget_usd", "period", "action",
                "current_spend", "period_start", "created_at",
            ])

        # Refresh continuous aggregates outside the transaction
        # (TimescaleDB may not support CALL inside transactions in all versions)
        print("Refreshing continuous aggregates...")
        for agg in ["hourly_model_stats", "hourly_task_stats", "daily_summary", "fitness_matrix"]:
            await conn.execute(
                f"CALL refresh_continuous_aggregate('{agg}', NOW() - INTERVAL '31 days', NOW());"
            )
            print(f"  - {agg} refreshed")

        total = (
            len(event_rows) + len(tool_call_rows) + len(benchmark_rows)
            + len(routing_rows) + len(mcp_call_rows) + len(mcp_graph_rows)
            + len(alert_rules) + len(alert_history) + len(budget_configs)
        )
        print(f"\nDone! Seeded {total} records across 9 tables.")

        # Seed blockchain data via HTTP (attestation/validator state is in-memory)
        try:
            seed_blockchain_data(events)
        except Exception as e:
            print(f"\nWARNING: Blockchain seeding failed: {e}")
            print("  (Is the API running on localhost:8100?)")

        print("Dashboard should now show data at http://localhost:8081")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
