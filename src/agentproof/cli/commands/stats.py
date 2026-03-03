"""agentproof stats — spend summary, top traces, waste score."""

from datetime import timedelta

import httpx
import typer
from rich.console import Console
from rich.table import Table

from agentproof.config import get_config
from agentproof.utils import utcnow

console = Console()


def _api_base() -> str:
    config = get_config()
    host = "localhost" if config.api_host == "0.0.0.0" else config.api_host
    return f"http://{host}:{config.api_port}/api/v1"


def stats(
    period: str = typer.Option("24h", help="Time period: 24h, 7d, 30d"),
    group_by: str = typer.Option("model", help="Group by: model, provider, task_type"),
    api_url: str = typer.Option("", help="API base URL (default: from config)"),
) -> None:
    """Show spend summary, top traces, and waste score."""
    base = api_url or _api_base()
    now = utcnow()
    delta_map = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
    delta = delta_map.get(period, timedelta(hours=24))
    start = now - delta

    with httpx.Client(timeout=10) as client:
        _show_summary(client, base, start, now, group_by)
        _show_top_traces(client, base, start, now)
        _show_waste_score(client, base, start, now)


def _show_summary(
    client: httpx.Client, base: str, start: datetime, end: datetime, group_by: str
) -> None:
    resp = client.get(
        f"{base}/stats/summary",
        params={"start": start.isoformat(), "end": end.isoformat(), "group_by": group_by},
    )
    resp.raise_for_status()
    data = resp.json()

    console.print(f"\n[bold]Spend Summary[/bold] ({data['period']['start'][:10]} to {data['period']['end'][:10]})")
    console.print(f"  Total requests: {data['total_requests']:,}")
    console.print(f"  Total cost:     ${data['total_cost_usd']:.2f}")
    console.print(f"  Total tokens:   {data['total_tokens']:,}")
    console.print(f"  Failure rate:   {data['failure_rate']:.1%}")

    if data["groups"]:
        table = Table(title=f"\nBy {group_by}")
        table.add_column(group_by.title(), style="cyan")
        table.add_column("Requests", justify="right")
        table.add_column("Cost", justify="right", style="green")
        table.add_column("Avg Latency", justify="right")
        table.add_column("P95 Latency", justify="right")
        table.add_column("Failures", justify="right", style="red")

        for g in data["groups"]:
            table.add_row(
                g["key"],
                f"{g['request_count']:,}",
                f"${g['total_cost_usd']:.2f}",
                f"{g['avg_latency_ms']:.0f}ms",
                f"{g['p95_latency_ms']:.0f}ms",
                str(g["failure_count"]),
            )

        console.print(table)


def _show_top_traces(client: httpx.Client, base: str, start: datetime, end: datetime) -> None:
    resp = client.get(
        f"{base}/stats/top-traces",
        params={"start": start.isoformat(), "end": end.isoformat(), "limit": 10},
    )
    resp.raise_for_status()
    data = resp.json()

    if not data["traces"]:
        return

    table = Table(title="\nTop 10 Most Expensive Traces")
    table.add_column("Trace ID", style="cyan", max_width=12)
    table.add_column("Cost", justify="right", style="green")
    table.add_column("Tokens", justify="right")
    table.add_column("Calls", justify="right")
    table.add_column("Models", max_width=30)
    table.add_column("Framework")

    for t in data["traces"]:
        table.add_row(
            t["trace_id"][:12],
            f"${t['total_cost_usd']:.2f}",
            f"{t['total_tokens']:,}",
            str(t["event_count"]),
            ", ".join(t["models_used"]),
            t["agent_framework"] or "—",
        )

    console.print(table)


def _show_waste_score(client: httpx.Client, base: str, start: datetime, end: datetime) -> None:
    resp = client.get(
        f"{base}/stats/waste-score",
        params={"start": start.isoformat(), "end": end.isoformat()},
    )
    resp.raise_for_status()
    data = resp.json()

    score = data["waste_score"]
    savings = data["total_potential_savings_usd"]

    style = "green" if score < 0.2 else "yellow" if score < 0.5 else "red"
    console.print(f"\n[bold]Waste Score:[/bold] [{style}]{score:.0%}[/{style}]")
    console.print(f"  Potential savings: ${savings:.2f}")

    if data["breakdown"]:
        table = Table(title="\nWaste Breakdown")
        table.add_column("Task Type", style="cyan")
        table.add_column("Current Model")
        table.add_column("Suggested")
        table.add_column("Calls", justify="right")
        table.add_column("Savings", justify="right", style="green")

        for item in data["breakdown"]:
            table.add_row(
                item["task_type"],
                item["current_model"],
                item["suggested_model"],
                f"{item['call_count']:,}",
                f"${item['savings_usd']:.2f}",
            )

        console.print(table)
