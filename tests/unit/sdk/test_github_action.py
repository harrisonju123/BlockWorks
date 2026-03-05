"""Tests for the GitHub Actions cost estimation utility.

Validates diff parsing, LLM call detection, cost estimation,
and markdown output formatting.
"""

from __future__ import annotations

from blockthrough.sdk.github_action import (
    CostEstimate,
    _parse_diff,
    estimate_pr_cost,
    format_github_comment,
)


# -- Sample diffs for testing -------------------------------------------------

DIFF_OPENAI_CALL = """\
diff --git a/agent/planner.py b/agent/planner.py
new file mode 100644
--- /dev/null
+++ b/agent/planner.py
@@ -0,0 +1,15 @@
+import openai
+
+def plan_task(prompt: str) -> str:
+    client = openai.OpenAI()
+    response = client.chat.completions.create(
+        model="gpt-4o",
+        messages=[{"role": "user", "content": prompt}],
+    )
+    return response.choices[0].message.content
"""

DIFF_ANTHROPIC_CALL = """\
diff --git a/agent/executor.py b/agent/executor.py
--- a/agent/executor.py
+++ b/agent/executor.py
@@ -10,6 +10,12 @@
 def execute(plan: str) -> str:
-    return plan.upper()
+    import anthropic
+    client = anthropic.Anthropic()
+    response = client.messages.create(
+        model="claude-sonnet-4-20250514",
+        messages=[{"role": "user", "content": plan}],
+    )
+    return response.content[0].text
"""

DIFF_MULTIPLE_CALLS = """\
diff --git a/workflows/main.py b/workflows/main.py
new file mode 100644
--- /dev/null
+++ b/workflows/main.py
@@ -0,0 +1,20 @@
+import litellm
+
+def step_one():
+    return litellm.completion(model="gpt-4o-mini", messages=[])
+
+def step_two():
+    return litellm.acompletion(model="gpt-4o", messages=[])
"""

DIFF_NO_LLM_CALLS = """\
diff --git a/utils/helpers.py b/utils/helpers.py
--- a/utils/helpers.py
+++ b/utils/helpers.py
@@ -1,3 +1,5 @@
 def add(a, b):
     return a + b
+
+def subtract(a, b):
+    return a - b
"""


class TestDiffParsing:

    def test_parse_added_lines(self) -> None:
        hunks = _parse_diff(DIFF_OPENAI_CALL)
        assert len(hunks) > 0
        assert all(h.file_path == "agent/planner.py" for h in hunks)

    def test_parse_modified_file(self) -> None:
        hunks = _parse_diff(DIFF_ANTHROPIC_CALL)
        assert len(hunks) > 0
        assert all(h.file_path == "agent/executor.py" for h in hunks)

    def test_empty_diff(self) -> None:
        hunks = _parse_diff("")
        assert hunks == []


class TestEstimatePrCost:

    def test_detects_openai_call(self) -> None:
        estimate = estimate_pr_cost(DIFF_OPENAI_CALL)
        assert estimate.new_llm_calls_found >= 1

        # Should detect the gpt-4o model hint from the diff context
        call_types = [d.call_type for d in estimate.details]
        assert "openai_chat" in call_types

    def test_detects_anthropic_call(self) -> None:
        estimate = estimate_pr_cost(DIFF_ANTHROPIC_CALL)
        assert estimate.new_llm_calls_found >= 1

        call_types = [d.call_type for d in estimate.details]
        assert "anthropic_messages" in call_types

    def test_detects_multiple_calls(self) -> None:
        estimate = estimate_pr_cost(DIFF_MULTIPLE_CALLS)
        assert estimate.new_llm_calls_found >= 2

    def test_no_calls_detected(self) -> None:
        estimate = estimate_pr_cost(DIFF_NO_LLM_CALLS)
        assert estimate.new_llm_calls_found == 0
        assert estimate.estimated_monthly_cost == 0.0

    def test_cost_is_positive_when_calls_found(self) -> None:
        estimate = estimate_pr_cost(DIFF_OPENAI_CALL)
        assert estimate.estimated_monthly_cost > 0

    def test_token_estimate_scales_with_calls(self) -> None:
        single = estimate_pr_cost(DIFF_OPENAI_CALL)
        multi = estimate_pr_cost(DIFF_MULTIPLE_CALLS)
        assert multi.estimated_monthly_tokens >= single.estimated_monthly_tokens

    def test_detail_fields_populated(self) -> None:
        estimate = estimate_pr_cost(DIFF_OPENAI_CALL)
        for detail in estimate.details:
            assert detail.file_path
            assert detail.line_number > 0
            assert detail.call_type
            assert detail.estimated_calls_per_month > 0
            assert detail.estimated_cost_per_call >= 0
            assert detail.estimated_monthly_cost >= 0


class TestCostEstimateWithContext:

    def test_current_stats_percentage(self) -> None:
        """When current stats are provided, summary should mention percentage."""
        estimate = estimate_pr_cost(
            DIFF_OPENAI_CALL,
            current_stats={"monthly_spend": 1000.0},
        )
        assert "%" in estimate.summary

    def test_no_current_stats(self) -> None:
        estimate = estimate_pr_cost(DIFF_OPENAI_CALL)
        # Should still produce a valid summary without percentage context
        assert "Blockthrough" in estimate.summary


class TestFormatGithubComment:

    def test_no_calls_message(self) -> None:
        estimate = estimate_pr_cost(DIFF_NO_LLM_CALLS)
        comment = format_github_comment(estimate)
        assert "No new LLM call sites" in comment

    def test_with_calls_has_table(self) -> None:
        estimate = estimate_pr_cost(DIFF_OPENAI_CALL)
        comment = format_github_comment(estimate)
        assert "| File" in comment
        assert "agent/planner.py" in comment

    def test_markdown_formatting(self) -> None:
        estimate = estimate_pr_cost(DIFF_MULTIPLE_CALLS)
        comment = format_github_comment(estimate)
        # Should have markdown headers
        assert "## Blockthrough" in comment
        # Should have a table
        assert "|" in comment
