"""GitHub Actions cost estimation from PR diffs.

Parses unified diffs to find new or modified LLM call sites, then
estimates monthly cost impact based on heuristics. Outputs a GitHub
PR comment body in markdown format.

This is a standalone utility — not a full GitHub Action. It can be
called from a workflow step that pipes `git diff` into the script.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agentproof.sdk.types import CostEstimate, CostEstimateDetail

# Patterns that indicate an LLM API call in source code.
# Each tuple: (regex, call_type, default_model_hint)
_LLM_CALL_PATTERNS: list[tuple[re.Pattern[str], str, str | None]] = [
    (re.compile(r"openai.*chat\.completions\.create", re.IGNORECASE), "openai_chat", "gpt-4o"),
    (re.compile(r"client\.chat\.completions\.create", re.IGNORECASE), "openai_chat", "gpt-4o"),
    (re.compile(r"anthropic.*messages\.create", re.IGNORECASE), "anthropic_messages", "claude-sonnet-4-20250514"),
    (re.compile(r"client\.messages\.create", re.IGNORECASE), "anthropic_messages", "claude-sonnet-4-20250514"),
    (re.compile(r"litellm\.completion", re.IGNORECASE), "litellm", None),
    (re.compile(r"litellm\.acompletion", re.IGNORECASE), "litellm", None),
    (re.compile(r"agentproof.*\.track\(", re.IGNORECASE), "agentproof_sdk", None),
    (re.compile(r"\.invoke\(.*llm", re.IGNORECASE), "langchain", None),
    (re.compile(r"ChatOpenAI|ChatAnthropic", re.IGNORECASE), "langchain_chat", None),
    (re.compile(r"CrewAI|crew\.kickoff", re.IGNORECASE), "crewai", None),
]

# Model -> rough cost-per-call estimate (avg prompt + completion, ~1k tokens each)
_MODEL_COST_PER_CALL: dict[str, float] = {
    "gpt-4o": 0.0125,
    "gpt-4o-mini": 0.000375,
    "gpt-4": 0.06,
    "claude-opus-4-20250514": 0.045,
    "claude-sonnet-4-20250514": 0.009,
    "claude-haiku-4-5-20251001": 0.0024,
}

# Default cost when model is unknown — average of mid-tier models
_DEFAULT_COST_PER_CALL = 0.01

# Heuristic: how many times per month a call site is invoked,
# based on the type of call. Agent loops run more often than one-shot calls.
_CALLS_PER_MONTH_HEURISTIC: dict[str, int] = {
    "openai_chat": 10_000,
    "anthropic_messages": 10_000,
    "litellm": 10_000,
    "agentproof_sdk": 10_000,
    "langchain": 15_000,
    "langchain_chat": 15_000,
    "crewai": 20_000,
}

_DEFAULT_CALLS_PER_MONTH = 10_000


@dataclass
class _DiffHunk:
    """Parsed hunk from a unified diff."""

    file_path: str
    line_number: int
    added_lines: list[str]


def _parse_diff(diff_text: str) -> list[_DiffHunk]:
    """Extract added lines from a unified diff.

    Only looks at lines starting with '+' (excluding the '+++' file header).
    Tracks the current file path and approximate line number.
    """
    hunks: list[_DiffHunk] = []
    current_file: str | None = None
    current_line = 0

    for line in diff_text.splitlines():
        # New file header
        if line.startswith("+++ b/"):
            current_file = line[6:]
            continue
        if line.startswith("--- "):
            continue

        # Hunk header: @@ -old,count +new,count @@
        hunk_match = re.match(r"^@@ .+\+(\d+)", line)
        if hunk_match:
            current_line = int(hunk_match.group(1))
            continue

        # Added line (not the +++ header)
        if line.startswith("+") and not line.startswith("+++"):
            if current_file:
                hunks.append(
                    _DiffHunk(
                        file_path=current_file,
                        line_number=current_line,
                        added_lines=[line[1:]],  # Strip the leading '+'
                    )
                )
            current_line += 1
        elif not line.startswith("-"):
            # Context line — advance line counter
            current_line += 1

    return hunks


def _extract_model_from_context(lines: list[str]) -> str | None:
    """Try to find a model= argument near an LLM call."""
    for line in lines:
        model_match = re.search(r'model\s*=\s*["\']([^"\']+)["\']', line)
        if model_match:
            return model_match.group(1)
    return None


def estimate_pr_cost(
    diff_text: str,
    current_stats: dict | None = None,
) -> CostEstimate:
    """Analyze a PR diff and estimate its cost impact.

    Scans added lines for LLM call patterns, estimates per-call cost
    based on model heuristics, and projects monthly spend.

    Args:
        diff_text: Unified diff output (e.g., from `git diff main...HEAD`)
        current_stats: Optional dict with current monthly spend for context

    Returns:
        CostEstimate with per-call-site breakdown and summary markdown
    """
    hunks = _parse_diff(diff_text)
    details: list[CostEstimateDetail] = []

    for hunk in hunks:
        for line_content in hunk.added_lines:
            for pattern, call_type, default_model in _LLM_CALL_PATTERNS:
                if pattern.search(line_content):
                    # Try to find a model hint in nearby context
                    model_hint = _extract_model_from_context(hunk.added_lines)
                    if model_hint is None:
                        model_hint = default_model

                    cost_per_call = _MODEL_COST_PER_CALL.get(
                        model_hint or "", _DEFAULT_COST_PER_CALL
                    )
                    calls_per_month = _CALLS_PER_MONTH_HEURISTIC.get(
                        call_type, _DEFAULT_CALLS_PER_MONTH
                    )
                    monthly_cost = cost_per_call * calls_per_month

                    details.append(
                        CostEstimateDetail(
                            file_path=hunk.file_path,
                            line_number=hunk.line_number,
                            model_hint=model_hint,
                            call_type=call_type,
                            estimated_calls_per_month=calls_per_month,
                            estimated_cost_per_call=cost_per_call,
                            estimated_monthly_cost=monthly_cost,
                        )
                    )
                    # Only match the first pattern per line
                    break

    total_monthly = sum(d.estimated_monthly_cost for d in details)
    total_tokens = sum(d.estimated_calls_per_month * 2000 for d in details)  # ~2k tokens/call

    summary = _build_summary(details, total_monthly, current_stats)

    return CostEstimate(
        new_llm_calls_found=len(details),
        estimated_monthly_cost=round(total_monthly, 2),
        estimated_monthly_tokens=total_tokens,
        details=details,
        summary=summary,
    )


def _build_summary(
    details: list[CostEstimateDetail],
    total_monthly: float,
    current_stats: dict | None,
) -> str:
    """Build a markdown summary suitable for a GitHub PR comment."""
    if not details:
        return (
            "## AgentProof Cost Analysis\n\n"
            "No new LLM call sites detected in this PR. :white_check_mark:"
        )

    lines = [
        "## AgentProof Cost Analysis\n",
        f"**{len(details)} new LLM call site(s) detected.**\n",
        f"**Estimated monthly cost impact: ${total_monthly:,.2f}**\n",
    ]

    if current_stats and "monthly_spend" in current_stats:
        current = current_stats["monthly_spend"]
        if current > 0:
            pct = (total_monthly / current) * 100
            lines.append(
                f"This represents a **{pct:.1f}%** increase over current monthly spend "
                f"(${current:,.2f}).\n"
            )

    lines.append("\n### Call Sites\n")
    lines.append("| File | Line | Type | Model | Est. Monthly Cost |")
    lines.append("|------|------|------|-------|-------------------|")

    for d in details:
        model_display = d.model_hint or "unknown"
        lines.append(
            f"| `{d.file_path}` | {d.line_number} | {d.call_type} | "
            f"{model_display} | ${d.estimated_monthly_cost:,.2f} |"
        )

    lines.append(
        "\n---\n"
        "*Estimates based on heuristic call volume (10k-20k calls/month per site) "
        "and published model pricing. Actual costs depend on prompt length, "
        "traffic patterns, and caching. Powered by AgentProof.*"
    )

    return "\n".join(lines)


def format_github_comment(estimate: CostEstimate) -> str:
    """Format a CostEstimate as a GitHub PR comment body.

    Convenience wrapper — just returns estimate.summary since
    _build_summary already produces markdown.
    """
    return estimate.summary
