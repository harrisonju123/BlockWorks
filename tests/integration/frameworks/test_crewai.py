"""Framework integration tests: CrewAI through the LiteLLM proxy.

Requires the AgentProof stack running (docker compose up -d) and the
crewai package installed. Skips gracefully if either is missing.

Run with:
    pytest tests/integration/frameworks/test_crewai.py -m framework -v
"""

from __future__ import annotations

import pytest

try:
    from crewai import Agent, Crew, Task

    HAS_CREWAI = True
except ImportError:
    HAS_CREWAI = False

from .conftest import PROXY_KEY, PROXY_URL, verify_event_captured

pytestmark = [
    pytest.mark.framework,
    pytest.mark.skipif(not HAS_CREWAI, reason="crewai not installed"),
]


@pytest.fixture(autouse=True)
def _set_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point CrewAI's internal LiteLLM client at the local proxy."""
    monkeypatch.setenv("OPENAI_API_BASE", f"{PROXY_URL}/v1")
    monkeypatch.setenv("OPENAI_API_KEY", PROXY_KEY)


class TestCrewAIProxy:

    def test_single_agent(
        self,
        proxy_available: bool,
        api_available: bool,
        event_count_before: int,
    ) -> None:
        """Run a single CrewAI agent and verify AgentProof captured events."""
        agent = Agent(
            role="Summarizer",
            goal="Summarize topics concisely",
            backstory="You write brief, accurate summaries.",
            llm="claude-haiku",
            verbose=False,
        )

        task = Task(
            description="Summarize the concept of software observability in one sentence.",
            expected_output="A single concise sentence.",
            agent=agent,
        )

        crew = Crew(
            agents=[agent],
            tasks=[task],
            verbose=False,
        )

        result = crew.kickoff()
        assert result is not None, "Crew returned no result"

        # CrewAI typically makes multiple LLM calls per task (planning,
        # execution, validation), so we just check that at least one
        # new event appeared.
        verify_event_captured(event_count_before)
