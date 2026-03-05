"""Tests for the eval set loader."""

from __future__ import annotations

import json
from pathlib import Path

from agentproof.benchmarking.eval_set import EvalPrompt, load_eval_set, to_messages
from agentproof.types import TaskType


def _make_jsonl_file(entries: list[dict], tmp_path: Path) -> Path:
    """Write entries to a temp JSONL file and return its path."""
    path = tmp_path / "test.jsonl"
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
    return path


def _make_entry(task_type: str = "code_generation", **overrides) -> dict:
    base = {
        "system_prompt": f"You are a {task_type} assistant.",
        "user_prompt": f"Do a {task_type} task.",
        "has_tools": False,
        "tool_count": 0,
        "has_json_schema": False,
        "has_code_fence_in_system": False,
        "prompt_tokens": 300,
        "completion_tokens": 800,
        "model": "claude-sonnet-4-6",
        "output_format_hint": "code",
        "expected_task_type": task_type,
    }
    base.update(overrides)
    return base


class TestEvalPrompt:
    def test_from_jsonl(self):
        data = _make_entry("classification")
        ep = EvalPrompt.from_jsonl(data)
        assert ep.task_type == TaskType.CLASSIFICATION
        assert ep.system_prompt == data["system_prompt"]
        assert ep.user_prompt == data["user_prompt"]
        assert len(ep.prompt_hash) == 64  # SHA-256 hex

    def test_prompt_hash_deterministic(self):
        data = _make_entry("reasoning")
        ep1 = EvalPrompt.from_jsonl(data)
        ep2 = EvalPrompt.from_jsonl(data)
        assert ep1.prompt_hash == ep2.prompt_hash

    def test_different_prompts_different_hashes(self):
        ep1 = EvalPrompt.from_jsonl(_make_entry("code_generation"))
        ep2 = EvalPrompt.from_jsonl(_make_entry("summarization"))
        assert ep1.prompt_hash != ep2.prompt_hash


class TestToMessages:
    def test_converts_to_litellm_format(self):
        ep = EvalPrompt.from_jsonl(_make_entry("extraction"))
        messages = to_messages(ep)
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == ep.system_prompt
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == ep.user_prompt


class TestLoadEvalSet:
    def test_loads_all_prompts(self, tmp_path):
        entries = [_make_entry(t) for t in ["code_generation", "classification", "reasoning"]]
        path = _make_jsonl_file(entries, tmp_path)
        prompts = load_eval_set(path)
        assert len(prompts) == 3

    def test_filters_by_task_type(self, tmp_path):
        entries = [
            _make_entry("code_generation"),
            _make_entry("classification"),
            _make_entry("reasoning"),
        ]
        path = _make_jsonl_file(entries, tmp_path)
        prompts = load_eval_set(path, task_types={TaskType.CLASSIFICATION})
        assert len(prompts) == 1
        assert prompts[0].task_type == TaskType.CLASSIFICATION

    def test_deduplicates_by_hash(self, tmp_path):
        entry = _make_entry("code_generation")
        path = _make_jsonl_file([entry, entry, entry], tmp_path)
        prompts = load_eval_set(path)
        assert len(prompts) == 1

    def test_skips_malformed_lines(self, tmp_path):
        path = tmp_path / "test.jsonl"
        with open(path, "w") as f:
            f.write(json.dumps(_make_entry("code_generation")) + "\n")
            f.write("not valid json\n")
            f.write(json.dumps(_make_entry("classification")) + "\n")
        prompts = load_eval_set(path)
        assert len(prompts) == 2

    def test_skips_missing_fields(self, tmp_path):
        path = _make_jsonl_file([
            _make_entry("code_generation"),
            {"system_prompt": "hi"},  # missing user_prompt and expected_task_type
        ], tmp_path)
        prompts = load_eval_set(path)
        assert len(prompts) == 1

    def test_empty_file(self, tmp_path):
        path = tmp_path / "test.jsonl"
        path.write_text("")
        prompts = load_eval_set(path)
        assert len(prompts) == 0

    def test_loads_real_fixture(self):
        """Sanity check: the actual fixture file loads without errors."""
        prompts = load_eval_set()
        assert len(prompts) >= 90  # at least the original set
        task_types_seen = {p.task_type for p in prompts}
        # Should cover all non-UNKNOWN task types
        for tt in TaskType:
            if tt == TaskType.UNKNOWN:
                continue
            assert tt in task_types_seen, f"Missing task type: {tt.value}"
