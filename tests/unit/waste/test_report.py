"""Tests for the weekly waste report formatting."""

from __future__ import annotations

from datetime import datetime, timezone

from agentproof.waste.report import format_plain_text, format_slack_blocks
from agentproof.waste.types import (
    WasteCategory,
    WasteItem,
    WasteReport,
    WasteSeverity,
)


def _report(items: list[WasteItem] | None = None, **kwargs) -> WasteReport:
    defaults = {
        "items": items or [],
        "total_savings": sum(i.savings for i in (items or [])),
        "total_spend": 1000.0,
        "waste_score": 0.3,
        "generated_at": datetime(2026, 3, 1, tzinfo=timezone.utc),
    }
    defaults.update(kwargs)
    return WasteReport(**defaults)


def _item(
    category: WasteCategory = WasteCategory.MODEL_OVERKILL,
    severity: WasteSeverity = WasteSeverity.WARNING,
    savings: float = 50.0,
    **kwargs,
) -> WasteItem:
    defaults = {
        "category": category,
        "severity": severity,
        "call_count": 100,
        "current_cost": 100.0,
        "projected_cost": 50.0,
        "savings": savings,
        "description": "Switch classification calls from Opus to Haiku",
        "confidence": 0.9,
    }
    defaults.update(kwargs)
    return WasteItem(**defaults)


class TestPlainTextFormatting:

    def test_empty_report(self) -> None:
        report = _report()
        text = format_plain_text(report)
        assert "No waste detected" in text
        assert "AgentProof" in text

    def test_includes_waste_score(self) -> None:
        report = _report(items=[_item()], waste_score=0.3)
        text = format_plain_text(report)
        assert "30%" in text

    def test_includes_savings(self) -> None:
        report = _report(items=[_item(savings=150.0)], total_savings=150.0)
        text = format_plain_text(report)
        assert "$150.00" in text

    def test_top_n_limits_output(self) -> None:
        items = [_item(savings=float(i)) for i in range(10, 0, -1)]
        report = _report(items=items)
        text = format_plain_text(report, top_n=3)
        assert "Top 3" in text

    def test_category_summary_present(self) -> None:
        items = [
            _item(category=WasteCategory.MODEL_OVERKILL, savings=100.0),
            _item(category=WasteCategory.REDUNDANT_CALLS, savings=50.0),
        ]
        report = _report(items=items)
        text = format_plain_text(report)
        assert "Model Overkill" in text
        assert "Redundant Calls" in text
        assert "Savings by Category" in text

    def test_description_included(self) -> None:
        items = [_item(description="Switch 340 classification calls from Opus to Haiku")]
        report = _report(items=items)
        text = format_plain_text(report)
        assert "Switch 340 classification calls" in text


class TestSlackBlocksFormatting:

    def test_empty_report(self) -> None:
        report = _report()
        blocks = format_slack_blocks(report)
        assert len(blocks) >= 1
        assert blocks[0]["type"] == "header"
        # Should have a "no waste" message
        found_no_waste = any(
            "No waste" in str(b.get("text", {}).get("text", ""))
            for b in blocks
        )
        assert found_no_waste

    def test_includes_header(self) -> None:
        report = _report(items=[_item()])
        blocks = format_slack_blocks(report)
        assert blocks[0]["type"] == "header"
        assert "Waste Report" in blocks[0]["text"]["text"]

    def test_includes_score_and_savings(self) -> None:
        report = _report(items=[_item(savings=200.0)], waste_score=0.2, total_savings=200.0)
        blocks = format_slack_blocks(report)
        text_content = " ".join(str(b) for b in blocks)
        assert "20%" in text_content
        assert "$200.00" in text_content

    def test_top_n_items(self) -> None:
        items = [_item(savings=float(i)) for i in range(10, 0, -1)]
        report = _report(items=items)
        blocks = format_slack_blocks(report, top_n=3)
        # Count section blocks that contain item descriptions
        item_blocks = [
            b for b in blocks
            if b.get("type") == "section"
            and "Switch" in str(b.get("text", {}).get("text", ""))
        ]
        assert len(item_blocks) == 3

    def test_includes_dividers(self) -> None:
        report = _report(items=[_item()])
        blocks = format_slack_blocks(report)
        dividers = [b for b in blocks if b.get("type") == "divider"]
        assert len(dividers) >= 1

    def test_severity_emoji_included(self) -> None:
        items = [_item(severity=WasteSeverity.CRITICAL)]
        report = _report(items=items)
        blocks = format_slack_blocks(report)
        text_content = " ".join(str(b) for b in blocks)
        assert ":rotating_light:" in text_content
