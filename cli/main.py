"""OptiLoop CLI - Terminal interface for the Autonomous Multi-Agent Coding System."""
from __future__ import annotations

import json
import os
import sys
from typing import Optional

import httpx
import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

app = typer.Typer(name="optiloop", help="OptiLoop - Autonomous Multi-Agent Coding System CLI")
console = Console()

API_URL = os.getenv("OPTILOOP_API_URL", "http://localhost:8000")


def _get(path: str) -> dict:
    """GET request to the API. Raises typer.Exit on error."""
    try:
        resp = httpx.get(f"{API_URL}{path}", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except httpx.ConnectError:
        console.print("[red]Error:[/] Cannot connect to OptiLoop API at " + API_URL)
        raise typer.Exit(1)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            console.print(f"[red]Error:[/] Resource not found: {path}")
        else:
            console.print(f"[red]Error:[/] HTTP {e.response.status_code}")
        raise typer.Exit(1)


def _post(path: str, body: dict = None) -> dict:
    """POST request to the API. Raises typer.Exit on error."""
    try:
        resp = httpx.post(f"{API_URL}{path}", json=body or {}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except httpx.ConnectError:
        console.print("[red]Error:[/] Cannot connect to OptiLoop API at " + API_URL)
        raise typer.Exit(1)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            console.print(f"[red]Error:[/] Resource not found: {path}")
        else:
            console.print(f"[red]Error:[/] HTTP {e.response.status_code}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# submit
# ---------------------------------------------------------------------------

@app.command()
def submit(
    prompt: str = typer.Argument(..., help="Task description / coding prompt"),
    budget: float = typer.Option(0.50, "--budget", "-b", help="Budget in USD"),
):
    """Submit a new task to OptiLoop."""
    data = _post("/api/tasks", {"prompt": prompt, "target_budget_usd": budget})

    panel_content = Text()
    panel_content.append(f"Task ID:   ", style="bold")
    panel_content.append(f"{data['id']}\n", style="cyan")
    panel_content.append(f"Status:    ", style="bold")
    panel_content.append(f"{data['status']}\n", style="green")
    panel_content.append(f"Prompt:    ", style="bold")
    panel_content.append(f"{data['prompt']}\n")
    panel_content.append(f"Budget:    ", style="bold")
    panel_content.append(f"${data['target_budget_usd']:.2f}", style="yellow")

    console.print(Panel(panel_content, title="[bold green]Task Submitted[/]", border_style="green"))


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

@app.command()
def status(task_id: str = typer.Argument(..., help="Task ID to check")):
    """Show detailed status of a task."""
    data = _get(f"/api/tasks/{task_id}")

    table = Table(title=f"Task {data['id']}", show_header=True, header_style="bold")
    table.add_column("Field", style="dim")
    table.add_column("Value")

    status_style = {
        "completed": "green", "running": "blue", "failed": "red",
        "cancelled": "yellow", "pending": "dim",
    }.get(data["status"], "white")

    table.add_row("Status", f"[{status_style}]{data['status']}[/]")
    table.add_row("Prompt", data["prompt"][:80])
    table.add_row("Total Spent", f"${data['total_spent_usd']:.6f}")
    table.add_row("Target Budget", f"${data['target_budget_usd']:.2f}" if data.get("target_budget_usd") else "None")
    table.add_row("Prompt Tokens", f"{data['total_prompt_tokens']:,}")
    table.add_row("Completion Tokens", f"{data['total_completion_tokens']:,}")
    table.add_row("Agent Runs", str(len(data.get("agent_runs", []))))
    table.add_row("Execution Logs", str(len(data.get("execution_logs", []))))
    table.add_row("Created", data["created_at"][:19])
    table.add_row("Updated", data["updated_at"][:19])

    console.print(table)


# ---------------------------------------------------------------------------
# logs
# ---------------------------------------------------------------------------

@app.command()
def logs(
    task_id: str = typer.Argument(..., help="Task ID"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Stream live logs via SSE"),
):
    """Display execution logs for a task."""
    if follow:
        _stream_logs(task_id)
    else:
        data = _get(f"/api/tasks/{task_id}")
        log_entries = data.get("execution_logs", [])
        if not log_entries:
            console.print("[dim]No logs found for this task.[/]")
            return
        for entry in log_entries:
            ts = entry["timestamp"][:19] if entry.get("timestamp") else ""
            step = entry.get("step_type", "???")
            content = entry.get("content", "")
            console.print(f"[dim]{ts}[/] [{_step_color(step)}]{step:>10}[/] {content[:500]}")


def _step_color(step_type: str) -> str:
    return {"command": "cyan", "diff": "yellow", "reasoning": "magenta",
            "search": "blue"}.get(step_type, "white")


def _stream_logs(task_id: str):
    """Connect to SSE endpoint and stream logs to terminal."""
    console.print(f"[dim]Streaming logs for {task_id} (Ctrl+C to stop)...[/]")
    url = f"{API_URL}/api/tasks/{task_id}/stream"
    try:
        with httpx.stream("GET", url, timeout=300) as resp:
            resp.raise_for_status()
            event_type = None
            data_lines = []
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    event_type = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data_lines.append(line.split(":", 1)[1].strip())
                elif line == "" and event_type:
                    raw = "\n".join(data_lines)
                    if event_type == "log":
                        try:
                            obj = json.loads(raw)
                            ts = obj.get("timestamp", "")[:19]
                            step = obj.get("step_type", "???")
                            content = obj.get("content", "")
                            console.print(f"[dim]{ts}[/] [{_step_color(step)}]{step:>10}[/] {content[:500]}")
                        except json.JSONDecodeError:
                            console.print(raw)
                    elif event_type == "done":
                        console.print("[dim]Stream ended.[/]")
                        return
                    event_type = None
                    data_lines = []
    except httpx.ConnectError:
        console.print("[red]Error:[/] Cannot connect to API")
        raise typer.Exit(1)
    except KeyboardInterrupt:
        console.print("[dim]Stopped.[/]")


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------

@app.command()
def metrics(task_id: str = typer.Argument(..., help="Task ID")):
    """Display cost and token metrics per agent role."""
    data = _get(f"/api/tasks/{task_id}")

    # Aggregate by role via agent_runs -> cost_metrics
    runs = data.get("agent_runs", [])
    all_metrics = data.get("cost_metrics", [])

    role_stats: dict[str, dict] = {}
    for run in runs:
        role = run.get("agent_role", "unknown")
        if role not in role_stats:
            role_stats[role] = {"tokens": 0, "cost": 0.0, "runs": 0}
        role_stats[role]["runs"] += 1

    # Since cost_metrics aren't linked to roles in the API response,
    # distribute evenly across runs for display
    total_cost = data.get("total_spent_usd", 0)
    total_pt = data.get("total_prompt_tokens", 0)
    total_ct = data.get("total_completion_tokens", 0)

    if role_stats:
        per_role_cost = total_cost / len(role_stats) if role_stats else 0
        per_role_pt = total_pt // len(role_stats) if role_stats else 0
        per_role_ct = total_ct // len(role_stats) if role_stats else 0
        for role in role_stats:
            role_stats[role]["cost"] = per_role_cost
            role_stats[role]["prompt_tokens"] = per_role_pt
            role_stats[role]["completion_tokens"] = per_role_ct

    table = Table(title=f"Metrics - Task {task_id[:12]}...", show_header=True, header_style="bold")
    table.add_column("Role", style="bold")
    table.add_column("Runs", justify="right")
    table.add_column("Prompt Tokens", justify="right")
    table.add_column("Completion Tokens", justify="right")
    table.add_column("Cost (USD)", justify="right", style="yellow")

    for role in ["planner", "executor", "reviewer"]:
        if role in role_stats:
            s_data = role_stats[role]
            table.add_row(
                role.capitalize(),
                str(s_data["runs"]),
                f"{s_data.get('prompt_tokens', 0):,}",
                f"{s_data.get('completion_tokens', 0):,}",
                f"${s_data['cost']:.6f}",
            )

    table.add_section()
    table.add_row(
        "[bold]Total[/]", "", f"[bold]{total_pt:,}[/]",
        f"[bold]{total_ct:,}[/]", f"[bold]${total_cost:.6f}[/]",
    )

    console.print(table)


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------

@app.command()
def stop(task_id: str = typer.Argument(..., help="Task ID to cancel")):
    """Stop / cancel a running task."""
    data = _post(f"/api/tasks/{task_id}/stop")
    status = data.get("status", "unknown")
    console.print(Panel(
        f"Task [cyan]{task_id}[/] has been [yellow]{status}[/].",
        title="[bold yellow]Task Stopped[/]",
        border_style="yellow",
    ))
