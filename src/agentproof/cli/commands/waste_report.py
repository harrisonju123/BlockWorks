"""agentproof waste-report — generate and print the weekly waste report."""

from datetime import timedelta

import httpx
import typer
from rich.console import Console

from agentproof.config import get_config
from agentproof.utils import utcnow
from agentproof.waste.report import format_plain_text
from agentproof.waste.types import WasteCategory, WasteItem, WasteReport, WasteSeverity

console = Console()


def _api_base() -> str:
    config = get_config()
    host = "localhost" if config.api_host == "0.0.0.0" else config.api_host
    return f"http://{host}:{config.api_port}/api/v1"


def waste_report(
    period: str = typer.Option("7d", help="Time period: 24h, 7d, 30d"),
    api_url: str = typer.Option("", help="API base URL (default: from config)"),
    format: str = typer.Option("text", help="Output format: text, json"),
) -> None:
    """Generate and display the waste analysis report."""
    base = api_url or _api_base()
    now = utcnow()
    delta_map = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
    delta = delta_map.get(period, timedelta(days=7))
    start = now - delta

    with httpx.Client(timeout=30) as client:
        resp = client.get(
            f"{base}/stats/waste/details",
            params={"start": start.isoformat(), "end": now.isoformat()},
        )
        resp.raise_for_status()
        data = resp.json()

    if format == "json":
        import json

        console.print(json.dumps(data, indent=2))
        return

    # Convert API response back to WasteReport for formatting
    report = WasteReport(
        items=[
            WasteItem(
                category=WasteCategory(item["category"]),
                severity=WasteSeverity(item["severity"]),
                affected_trace_ids=item.get("affected_trace_ids", []),
                call_count=item.get("call_count", 0),
                current_cost=item.get("current_cost", 0),
                projected_cost=item.get("projected_cost", 0),
                savings=item.get("savings", 0),
                description=item.get("description", ""),
                confidence=item.get("confidence", 0.5),
            )
            for item in data.get("items", [])
        ],
        total_savings=data.get("total_savings", 0),
        total_spend=data.get("total_spend", 0),
        waste_score=data.get("waste_score", 0),
    )

    text_output = format_plain_text(report)
    console.print(text_output)
