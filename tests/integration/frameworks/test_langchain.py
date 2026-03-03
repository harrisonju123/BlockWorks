"""Framework integration tests: LangChain through the LiteLLM proxy.

Requires the AgentProof stack running (docker compose up -d) and the
langchain-openai package installed. Skips gracefully if either is missing.

Run with:
    pytest tests/integration/frameworks/test_langchain.py -m framework -v
"""

from __future__ import annotations

import pytest

try:
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage

    HAS_LANGCHAIN = True
except ImportError:
    HAS_LANGCHAIN = False

from .conftest import PROXY_KEY, PROXY_URL, verify_event_captured

pytestmark = [
    pytest.mark.framework,
    pytest.mark.skipif(not HAS_LANGCHAIN, reason="langchain-openai not installed"),
]


def _make_llm(model: str = "claude-haiku") -> "ChatOpenAI":
    """Create a ChatOpenAI client pointed at the local proxy."""
    return ChatOpenAI(
        base_url=f"{PROXY_URL}/v1",
        api_key=PROXY_KEY,
        model=model,
        max_tokens=20,
    )


class TestLangChainProxy:

    def test_chat_through_proxy(
        self,
        proxy_available: bool,
        api_available: bool,
        event_count_before: int,
    ) -> None:
        """Send a simple message through the proxy and verify AgentProof captured it."""
        llm = _make_llm()
        response = llm.invoke([HumanMessage(content="Say OK")])

        assert response.content, "LLM returned empty response"

        verify_event_captured(event_count_before)

    def test_with_tools(
        self,
        proxy_available: bool,
        api_available: bool,
        event_count_before: int,
    ) -> None:
        """Make a tool-calling request through the proxy and verify capture."""
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get the current weather for a city.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {
                                "type": "string",
                                "description": "City name",
                            },
                        },
                        "required": ["city"],
                    },
                },
            },
        ]

        llm = _make_llm()
        llm_with_tools = llm.bind(tools=tools)

        response = llm_with_tools.invoke(
            [HumanMessage(content="What is the weather in San Francisco?")]
        )

        # The model may respond with tool_calls or text — either way,
        # the event should be captured by AgentProof.
        assert response is not None

        verify_event_captured(event_count_before)
