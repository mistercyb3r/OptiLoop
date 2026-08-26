"""
Database models for OptiLoop - Autonomous Multi-Agent Coding System.

Uses SQLModel with SQLite for persistent storage.
Tables: Task, AgentRun, CostMetric, ExecutionLog
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import SQLModel, Field, Session, create_engine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uuid() -> str:
    """Return a new UUID4 string."""
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    """Return current UTC time."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------

DATABASE_URL = "sqlite:///optiloop.db"
engine = create_engine(DATABASE_URL, echo=False)


def create_db_and_tables() -> None:
    """Create all tables defined by SQLModel metadata."""
    SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    """Return a new SQLModel Session bound to the engine."""
    return Session(engine)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class Task(SQLModel, table=True):
    """Top-level coding task that the multi-agent system works on."""

    __tablename__ = "tasks"

    id: str = Field(default_factory=_uuid, primary_key=True)
    prompt: str
    status: str = Field(default="pending")
    target_budget_usd: Optional[float] = Field(default=None)
    total_spent_usd: float = Field(default=0.0)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class AgentRun(SQLModel, table=True):
    """A single run/iteration of an agent role on a task."""

    __tablename__ = "agent_runs"

    id: str = Field(default_factory=_uuid, primary_key=True)
    task_id: str = Field(foreign_key="tasks.id", index=True)
    agent_role: str  # "planner" | "executor" | "reviewer"
    model_name: str
    iteration: int = Field(default=1)
    status: str = Field(default="running")
    timestamp: datetime = Field(default_factory=_utcnow)


class CostMetric(SQLModel, table=True):
    """Token usage and cost data for one agent-run."""

    __tablename__ = "cost_metrics"

    id: str = Field(default_factory=_uuid, primary_key=True)
    agent_run_id: str = Field(foreign_key="agent_runs.id", index=True)
    model_name: str
    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    search_calls: int = Field(default=0)
    cost_usd: float = Field(default=0.0)
    timestamp: datetime = Field(default_factory=_utcnow)


class ExecutionLog(SQLModel, table=True):
    """Discrete step log for debugging / auditing a task run."""

    __tablename__ = "execution_logs"

    id: str = Field(default_factory=_uuid, primary_key=True)
    task_id: str = Field(foreign_key="tasks.id", index=True)
    step_type: str  # "command" | "diff" | "search" | "reasoning"
    content: str
    timestamp: datetime = Field(default_factory=_utcnow)
