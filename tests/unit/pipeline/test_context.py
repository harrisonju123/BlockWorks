"""Tests for trace context propagation and framework detection."""

from agentproof.pipeline.context import detect_agent_framework, extract_trace_context


class TestExtractTraceContext:
    def test_extracts_from_metadata(self):
        kwargs = {
            "litellm_params": {
                "metadata": {
                    "trace_id": "t-123",
                    "session_id": "s-456",
                    "parent_span_id": "p-789",
                }
            },
            "litellm_call_id": "call-001",
        }
        ctx = extract_trace_context(kwargs)
        assert ctx["trace_id"] == "t-123"
        assert ctx["span_id"] == "call-001"
        assert ctx["session_id"] == "s-456"
        assert ctx["parent_span_id"] == "p-789"

    def test_falls_back_to_call_id(self):
        kwargs = {
            "litellm_params": {"metadata": {}},
            "litellm_call_id": "call-999",
        }
        ctx = extract_trace_context(kwargs)
        assert ctx["trace_id"] == "call-999"
        assert ctx["span_id"] == "call-999"


class TestDetectAgentFramework:
    def test_explicit_metadata(self):
        kwargs = {
            "litellm_params": {
                "metadata": {
                    "agent_framework": "crewai",
                    "agent_name": "researcher",
                },
            }
        }
        framework, name = detect_agent_framework(kwargs)
        assert framework == "crewai"
        assert name == "researcher"

    def test_user_agent_detection(self):
        kwargs = {
            "litellm_params": {
                "metadata": {},
                "headers": {"User-Agent": "LangChain/0.3.0"},
            }
        }
        framework, _ = detect_agent_framework(kwargs)
        assert framework == "langchain"

    def test_no_detection(self):
        kwargs = {"litellm_params": {"metadata": {}, "headers": {}}}
        framework, name = detect_agent_framework(kwargs)
        assert framework is None
        assert name is None
