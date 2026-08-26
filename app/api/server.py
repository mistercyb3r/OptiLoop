"""FastAPI REST API server for OptiLoop."""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlmodel import SQLModel, Session, select

from app.models.db_models import (
    Task, AgentRun, CostMetric, ExecutionLog,
    create_db_and_tables, engine,
)

logger = logging.getLogger(__name__)

_active_tasks: dict[str, asyncio.Task] = {}
_log_queues: dict[str, list[asyncio.Queue]] = {}


class TaskCreate(BaseModel):
    prompt: str
    target_budget_usd: float = 0.50
    model: str = "auto"


class TaskSummary(BaseModel):
    id: str
    prompt: str
    status: str
    target_budget_usd: float | None
    total_spent_usd: float
    model_used: str
    created_at: str
    updated_at: str


class AgentRunOut(BaseModel):
    id: str
    agent_role: str
    model_name: str
    iteration: int
    status: str
    timestamp: str


class CostMetricOut(BaseModel):
    model_name: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


class TokenCostBreakdown(BaseModel):
    model_name: str
    prompt_tokens: int
    completion_tokens: int
    prompt_cost_usd: float
    completion_cost_usd: float
    total_cost_usd: float


class LogEntry(BaseModel):
    step_type: str
    content: str
    timestamp: str


class TaskDetail(BaseModel):
    id: str
    prompt: str
    status: str
    target_budget_usd: float | None
    total_spent_usd: float
    model_used: str
    created_at: str
    updated_at: str
    agent_runs: list[AgentRunOut]
    cost_metrics: list[CostMetricOut]
    token_breakdown: list[TokenCostBreakdown]
    total_prompt_tokens: int
    total_completion_tokens: int
    total_input_cost: float
    total_output_cost: float
    execution_logs: list[LogEntry]


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    yield


app = FastAPI(title="OptiLoop API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dt_str(dt: datetime) -> str:
    if dt is None:
        return ""
    return dt.isoformat()


def _task_to_summary(task: Task) -> TaskSummary:
    return TaskSummary(
        id=task.id, prompt=task.prompt, status=task.status,
        target_budget_usd=task.target_budget_usd,
        total_spent_usd=task.total_spent_usd,
        model_used=getattr(task, "model_override", "") or "",
        created_at=_dt_str(task.created_at),
        updated_at=_dt_str(task.updated_at),
    )


# ---------------------------------------------------------------------------
# POST /api/tasks - Create task
# ---------------------------------------------------------------------------

@app.post("/api/tasks", response_model=TaskSummary, status_code=201)
async def create_task(body: TaskCreate):
    """Create a new task and start the orchestrator loop in the background."""
    model_override = body.model if body.model != "auto" else ""
    task = Task(prompt=body.prompt, target_budget_usd=body.target_budget_usd)
    with Session(engine) as sess:
        sess.add(task)
        sess.commit()
        sess.refresh(task)
        task_id = task.id

    # Spawn background orchestrator
    async def _run():
        try:
            from app.core.orchestrator import Orchestrator
            with Session(engine) as sess:
                orch = Orchestrator(db_session=sess)
                await orch.run_task(task_id, model_override=model_override)
        except Exception as exc:
            logger.error("Task %s failed: %s", task_id, exc)
            with Session(engine) as sess:
                t = sess.get(Task, task_id)
                if t:
                    t.status = "failed"
                    sess.add(t)
                    sess.commit()
        finally:
            _active_tasks.pop(task_id, None)

    bg = asyncio.create_task(_run())
    _active_tasks[task_id] = bg

    with Session(engine) as sess:
        t = sess.get(Task, task_id)
        return _task_to_summary(t)


# ---------------------------------------------------------------------------
# GET /api/tasks - List tasks
# ---------------------------------------------------------------------------

@app.get("/api/tasks", response_model=list[TaskSummary])
async def list_tasks():
    """Return all tasks ordered by creation time (newest first)."""
    with Session(engine) as sess:
        stmt = select(Task).order_by(Task.created_at.desc())
        tasks = sess.exec(stmt).all()
        return [_task_to_summary(t) for t in tasks]


# ---------------------------------------------------------------------------
# GET /api/tasks/{task_id} - Task detail
# ---------------------------------------------------------------------------

@app.get("/api/tasks/{task_id}", response_model=TaskDetail)
async def get_task(task_id: str):
    """Return full task detail with runs, costs, and logs."""
    with Session(engine) as sess:
        task = sess.get(Task, task_id)
        if not task:
            raise HTTPException(404, "Task not found")

        runs = sess.exec(
            select(AgentRun).where(AgentRun.task_id == task_id)
        ).all()

        all_metrics: list[CostMetric] = []
        total_pt = 0
        total_ct = 0
        for run in runs:
            metrics = sess.exec(
                select(CostMetric).where(CostMetric.agent_run_id == run.id)
            ).all()
            all_metrics.extend(metrics)
            for m in metrics:
                total_pt += m.prompt_tokens
                total_ct += m.completion_tokens

        logs = sess.exec(
            select(ExecutionLog).where(ExecutionLog.task_id == task_id)
            .order_by(ExecutionLog.timestamp)
        ).all()

        # Build per-model token cost breakdown
        from app.core.cost_calculator import CostCalculator
        calc = CostCalculator()
        breakdown_by_model: dict[str, dict] = {}
        total_input_cost = 0.0
        total_output_cost = 0.0
        for m in all_metrics:
            key = m.model_name
            if key not in breakdown_by_model:
                breakdown_by_model[key] = {
                    "model_name": key, "prompt_tokens": 0,
                    "completion_tokens": 0, "prompt_cost_usd": 0.0,
                    "completion_cost_usd": 0.0, "total_cost_usd": 0.0,
                }
            entry = breakdown_by_model[key]
            entry["prompt_tokens"] += m.prompt_tokens
            entry["completion_tokens"] += m.completion_tokens
            if key in calc.pricing:
                pc = m.prompt_tokens * calc.pricing[key].prompt_cost_per_token
                cc = m.completion_tokens * calc.pricing[key].completion_cost_per_token
            else:
                pc = m.cost_usd * 0.3  # rough estimate
                cc = m.cost_usd * 0.7
            entry["prompt_cost_usd"] = round(pc, 8)
            entry["completion_cost_usd"] = round(cc, 8)
            entry["total_cost_usd"] = round(pc + cc, 8)
            total_input_cost += pc
            total_output_cost += cc

        # Determine model_used from most frequent metric model
        model_used = ""
        if all_metrics:
            from collections import Counter
            counts = Counter(m.model_name for m in all_metrics)
            model_used = counts.most_common(1)[0][0]

        return TaskDetail(
            id=task.id, prompt=task.prompt, status=task.status,
            target_budget_usd=task.target_budget_usd,
            total_spent_usd=task.total_spent_usd,
            model_used=model_used,
            created_at=_dt_str(task.created_at),
            updated_at=_dt_str(task.updated_at),
            agent_runs=[AgentRunOut(
                id=r.id, agent_role=r.agent_role, model_name=r.model_name,
                iteration=r.iteration, status=r.status,
                timestamp=_dt_str(r.timestamp),
            ) for r in runs],
            cost_metrics=[CostMetricOut(
                model_name=m.model_name, prompt_tokens=m.prompt_tokens,
                completion_tokens=m.completion_tokens, cost_usd=m.cost_usd,
            ) for m in all_metrics],
            token_breakdown=[TokenCostBreakdown(**v) for v in breakdown_by_model.values()],
            total_prompt_tokens=total_pt,
            total_completion_tokens=total_ct,
            total_input_cost=round(total_input_cost, 8),
            total_output_cost=round(total_output_cost, 8),
            execution_logs=[LogEntry(
                step_type=l.step_type, content=l.content,
                timestamp=_dt_str(l.timestamp),
            ) for l in logs],
        )


# ---------------------------------------------------------------------------
# POST /api/tasks/{task_id}/stop - Cancel task
# ---------------------------------------------------------------------------

@app.post("/api/tasks/{task_id}/stop")
async def stop_task(task_id: str):
    """Cancel a running task."""
    with Session(engine) as sess:
        task = sess.get(Task, task_id)
        if not task:
            raise HTTPException(404, "Task not found")
        task.status = "cancelled"
        task.updated_at = datetime.now(timezone.utc)
        sess.add(task)
        sess.commit()

    # Cancel the background asyncio task if active
    bg = _active_tasks.pop(task_id, None)
    if bg and not bg.done():
        bg.cancel()

    return {"status": "cancelled", "task_id": task_id}


# ---------------------------------------------------------------------------
# GET /api/tasks/{task_id}/stream - SSE log stream
# ---------------------------------------------------------------------------

@app.get("/api/tasks/{task_id}/stream")
async def stream_task(task_id: str):
    """Stream ExecutionLog entries as SSE events."""
    from sse_starlette.sse import EventSourceResponse

    queue: asyncio.Queue = asyncio.Queue()
    _log_queues.setdefault(task_id, []).append(queue)

    async def event_generator():
        try:
            # Send existing logs first
            with Session(engine) as sess:
                logs = sess.exec(
                    select(ExecutionLog)
                    .where(ExecutionLog.task_id == task_id)
                    .order_by(ExecutionLog.timestamp)
                ).all()
                for log in logs:
                    yield {
                        "event": "log",
                        "data": json.dumps({
                            "step_type": log.step_type,
                            "content": log.content,
                            "timestamp": _dt_str(log.timestamp),
                        }),
                    }

            # Stream new logs
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    if event is None:
                        yield {"event": "done", "data": ""}
                        break
                    yield {
                        "event": "log",
                        "data": json.dumps(event),
                    }
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield {"event": "ping", "data": ""}
        finally:
            # Cleanup queue reference
            if task_id in _log_queues:
                try:
                    _log_queues[task_id].remove(queue)
                except ValueError:
                    pass

    return EventSourceResponse(event_generator())


# ---------------------------------------------------------------------------
# Broadcast helper (called by orchestrator to push log events)
# ---------------------------------------------------------------------------

def broadcast_log(task_id: str, step_type: str, content: str) -> None:
    """Push a log event to all SSE subscribers for *task_id*."""
    event = {
        "step_type": step_type,
        "content": content,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    for q in _log_queues.get(task_id, []):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


# ---------------------------------------------------------------------------
# GET /api/models - Available model list
# ---------------------------------------------------------------------------

@app.get("/api/models")
async def list_models():
    """Return the list of available models for the UI dropdown."""
    from app.core.router import AVAILABLE_MODELS
    return AVAILABLE_MODELS
