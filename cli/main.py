"""OptiLoop CLI - Professional terminal interface."""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Optional

import httpx
import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.status import Status

app = typer.Typer(name="optiloop", help="OptiLoop - Autonomous Multi-Agent Coding System CLI")
console = Console()

API_URL = os.getenv("OPTILOOP_API_URL", "http://localhost:8000")


def _get(path: str) -> dict:
    """GET request to the API."""
    try:
        resp = httpx.get(f"{API_URL}{path}", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except httpx.ConnectError:
        console.print("[red]Error:[/] Cannot connect to OptiLoop API at " + API_URL)
        raise typer.Exit(1)
    except httpx.HTTPStatusError as e:
        console.print(f"[red]Error:[/] HTTP {e.response.status_code}")
        raise typer.Exit(1)


def _post(path: str, body: dict = None) -> dict:
    """POST request to the API."""
    try:
        resp = httpx.post(f"{API_URL}{path}", json=body or {}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except httpx.ConnectError:
        console.print("[red]Error:[/] Cannot connect to OptiLoop API at " + API_URL)
        raise typer.Exit(1)
    except httpx.HTTPStatusError as e:
        console.print(f"[red]Error:[/] HTTP {e.response.status_code}")
        raise typer.Exit(1)


ROLE_BADGES = {
    "planner": "[bold blue]Architect[/]",
    "executor": "[bold green]Developer[/]",
    "reviewer": "[bold yellow]Inspector[/]",
}



@app.command()
def submit(
    prompt: str = typer.Argument(..., help="Task description / coding prompt"),
    budget: float = typer.Option(0.50, "--budget", "-b", help="Budget in USD"),
    model: str = typer.Option("auto", "--model", "-m", help="Model ID or 'auto'"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Stream logs after submit"),
):
    """Submit a new task to OptiLoop."""
    with Status("[cyan]Routing task...[/]", console=console, spinner="dots"):
        data = _post("/api/tasks", {
            "prompt": prompt, "target_budget_usd": budget, "model": model,
        })
    task_id = data["id"]
    panel = Text()
    panel.append("Task ID:   ", style="bold")
    panel.append(f"{task_id}\n", style="cyan")
    panel.append("Status:    ", style="bold")
    panel.append(f"{data['status']}\n", style="green")
    panel.append("Prompt:    ", style="bold")
    panel.append(f"{data['prompt']}\n")
    panel.append("Budget:    ", style="bold")
    panel.append(f"${data['target_budget_usd']:.2f}\n", style="yellow")
    panel.append("Model:     ", style="bold")
    panel.append(f"{model}", style="cyan")
    console.print(Panel(panel, title="[bold green]Task Submitted[/]", border_style="green"))
    if follow:
        console.print(f"\n[dim]Streaming logs for {task_id}...[/]\n")
        _stream_logs(task_id)


@app.command()
def status(task_id: str = typer.Argument(..., help="Task ID to check")):
    """Show detailed status of a task."""
    with Status("Fetching status...", console=console, spinner="dots"):
        data = _get(f"/api/tasks/{task_id}")
    t = Table(title=f"Task {task_id[:12]}...", show_header=True, header_style="bold")
    t.add_column("Field", style="dim"); t.add_column("Value")
    ss = {"completed":"green","running":"blue","failed":"red","cancelled":"yellow","pending":"dim"}.get(data["status"],"white")
    t.add_row("Status", f"[{ss}]{data['status']}[/]")
    t.add_row("Prompt", data["prompt"][:80])
    t.add_row("Model Used", data.get("model_used","") or "auto")
    t.add_row("Total Spent", f"${data['total_spent_usd']:.6f}")
    t.add_row("Target Budget", f"${data['target_budget_usd']:.2f}" if data.get("target_budget_usd") else "None")
    t.add_row("Input Cost", f"${data.get('total_input_cost',0):.6f}")
    t.add_row("Output Cost", f"${data.get('total_output_cost',0):.6f}")
    t.add_row("Input Tokens", f"{data.get('total_prompt_tokens',0):,}")
    t.add_row("Output Tokens", f"{data.get('total_completion_tokens',0):,}")
    t.add_row("Agent Runs", str(len(data.get("agent_runs",[]))))
    t.add_row("Execution Logs", str(len(data.get("execution_logs",[]))))
    console.print(t)


@app.command()
def logs(task_id: str = typer.Argument(..., help="Task ID"),
         follow: bool = typer.Option(False, "--follow", "-f", help="Stream live logs via SSE")):
    """Display execution logs for a task."""
    if follow:
        _stream_logs(task_id)
    else:
        data = _get(f"/api/tasks/{task_id}")
        logs_list = data.get("execution_logs", [])
        if not logs_list:
            console.print("[dim]No logs found for this task.[/]")
            return
        for entry in logs_list:
            ts = entry["timestamp"][:19] if entry.get("timestamp") else ""
            step = entry.get("step_type", "???")
            content = entry.get("content", "")
            console.print(f"[dim]{ts}[/] [{_step_color(step)}]{step:>10}[/] {content[:500]}")


def _step_color(step_type):
    return {"command":"cyan","diff":"yellow","reasoning":"magenta","search":"blue"}.get(step_type,"white")



def _stream_logs(task_id):
    console.print(f"[dim]Streaming logs for {task_id} (Ctrl+C to stop)...[/]")
    url = f"{API_URL}/api/tasks/{task_id}/stream"
    try:
        with httpx.stream("GET", url, timeout=300) as resp:
            resp.raise_for_status()
            event_type = None; data_lines = []
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
                            ts = obj.get("timestamp","")[:19]
                            step = obj.get("step_type","???")
                            content = obj.get("content","")
                            console.print(f"[dim]{ts}[/] [{_step_color(step)}]{step:>10}[/] {content[:500]}")
                        except json.JSONDecodeError:
                            console.print(raw)
                    elif event_type == "done":
                        console.print("[dim]Stream ended.[/]"); return
                    event_type = None; data_lines = []
    except httpx.ConnectError:
        console.print("[red]Error:[/] Cannot connect to API"); raise typer.Exit(1)
    except KeyboardInterrupt:
        console.print("[dim]Stopped. [/]")


@app.command()
def metrics(task_id: str = typer.Argument(..., help="Task ID")):
    """Display cost and token metrics per agent role and model."""
    with Status("Fetching metrics...", console=console, spinner="dots"):
        data = _get(f"/api/tasks/{task_id}")
    runs = data.get("agent_runs", [])
    role_stats = {}
    for run in runs:
        role = run.get("agent_role","unknown")
        role_stats.setdefault(role, {"runs": 0})
        role_stats[role]["runs"] += 1

    breakdown = data.get("token_breakdown", [])
    mt = Table(title="Token & Cost Breakdown by Model", show_header=True, header_style="bold")
    mt.add_column("Model", style="bold"); mt.add_column("Prompt Tokens", justify="right")
    mt.add_column("Completion Tokens", justify="right"); mt.add_column("Input Cost", justify="right")
    mt.add_column("Output Cost", justify="right"); mt.add_column("Total", justify="right", style="yellow")
    for b in breakdown:
        mt.add_row(b["model_name"].split("/")[-1], f"{b['prompt_tokens']:,}",
                   f"{b['completion_tokens']:,}", f"${b['prompt_cost_usd']:.6f}",
                   f"${b['completion_cost_usd']:.6f}", f"${b['total_cost_usd']:.6f}")
    ti = data.get("total_input_cost", 0); to = data.get("total_output_cost", 0)
    mt.add_section()
    mt.add_row("[bold]Total[/]", "", f"[bold]{data.get('total_prompt_tokens',0):,}[/]",
               f"[bold]{data.get('total_completion_tokens',0):,}[/]",
               f"[bold]${ti:.6f}[/]", f"[bold]${to:.6f}[/]", f"[bold]${ti+to:.6f}[/]")

    rt = Table(title="Agent Run Summary", show_header=True, header_style="bold")
    rt.add_column("Role", style="bold"); rt.add_column("Runs", justify="right")
    for role in ["planner","executor","reviewer"]:
        badge = ROLE_BADGES.get(role, role)
        rt.add_row(badge, str(role_stats.get(role,{}).get("runs",0)))
    console.print(mt); console.print(rt)


@app.command()
def stop(task_id: str = typer.Argument(..., help="Task ID to cancel")):
    """Stop / cancel a running task."""
    data = _post(f"/api/tasks/{task_id}/stop")
    s = data.get("status","unknown")
    console.print(Panel(f"Task [cyan]{task_id}[/] has been [yellow]{s}[/].",
                        title="[bold yellow]Task Stopped[/]", border_style="yellow"))
