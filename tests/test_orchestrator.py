"""Tests for the Tri-Agent Orchestrator Loop."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import SQLModel, Session, create_engine

from app.models.db_models import Task, AgentRun, CostMetric, ExecutionLog
from app.core.orchestrator import Orchestrator, _parse_json, clean_json_response
from app.core.router import ModelRouter
from app.core.cost_calculator import CostCalculator
from app.core.sandbox import DockerSandbox


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    """Create an isolated SQLite DB for testing."""
    eng = create_engine(f"sqlite:///{tmp_path / 'orch.db'}", echo=False)
    SQLModel.metadata.create_all(eng)
    with Session(eng) as sess:
        yield sess


@pytest.fixture()
def mock_sandbox():
    """Return a mock DockerSandbox."""
    sb = MagicMock(spec=DockerSandbox)
    sb.start.return_value = None
    sb.stop.return_value = None
    sb.write_file.return_value = None
    sb.read_file.return_value = ""
    sb.get_diff.return_value = ""
    sb.run_command.return_value = {
        "stdout": "", "stderr": "", "exit_code": 0, "duration_sec": 0.1
    }
    return sb


def _make_task(db, prompt="Fix the bug", budget=None):
    """Insert a test task and return it."""
    task = Task(prompt=prompt, target_budget_usd=budget)
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def _mock_llm_return(text, cost=0.01):
    """Build the dict that call_llm returns."""
    return {
        "text": text,
        "model_used": "test/model",
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "cost_usd": cost,
    }


def _plan_json(steps=None):
    """Build a valid planner JSON response."""
    if steps is None:
        steps = [
            {"action": "write_file", "path": "main.py", "content": "print(1)"},
            {"action": "run_command", "command": "python main.py"},
        ]
    return json.dumps({"steps": steps, "summary": "Execute plan"})


def _exec_json(results=None):
    """Build a valid executor JSON response."""
    if results is None:
        results = [
            {"action": "write_file", "path": "main.py", "status": "ok"},
            {"action": "run_command", "command": "python main.py",
             "exit_code": 0, "stdout": "", "stderr": ""},
        ]
    return json.dumps({"results": results, "diff_summary": "Created main.py"})


def _review_json(status="APPROVED", feedback="Looks good"):
    return json.dumps({"status": status, "feedback": feedback})


class _FakeRouter:
    """Lightweight ModelRouter stand-in that uses mocked call_llm."""

    def __init__(self, responses):
        """responses: dict mapping role -> list of (text, cost) tuples."""
        self._responses = {}
        for role, items in responses.items():
            self._responses[role] = list(items)
        self.calculator = CostCalculator()

    def select_model(self, agent_role, complexity="medium",
                     target_budget_usd=0.0, total_spent_usd=0.0):
        return "test/model"

    async def call_llm(self, messages, model, agent_run_id, db_session,
                       override_model=None):
        role = None
        for m in messages:
            if m.get("role") == "system":
                content = m["content"]
                if "PLANNER" in content:
                    role = "planner"
                elif "EXECUTOR" in content:
                    role = "executor"
                elif "REVIEWER" in content:
                    role = "reviewer"
                break
        if role and self._responses.get(role):
            text, cost = self._responses[role].pop(0)
        else:
            text, cost = '{"status": "APPROVED", "feedback": "ok"}', 0.001
        return _mock_llm_return(text, cost)

# ---------------------------------------------------------------------------
# _parse_json helper
# ---------------------------------------------------------------------------

class TestParseJson:

    def test_direct_json(self):
        assert _parse_json('{"a": 1}') == {"a": 1}

    def test_code_block(self):
        text = "Here is the result:\n```json\n{\"x\": 42}\n```"
        assert _parse_json(text) == {"x": 42}

    def test_garbage_returns_empty(self):
        assert _parse_json("not json at all") == {}


class TestCleanJsonResponse:

    def test_clean_plain_json(self):
        assert clean_json_response('{"a": 1}') == '{"a": 1}'

    def test_clean_fenced_json(self):
        text = '```json\n{"key": "value"}\n```'
        assert clean_json_response(text) == '{"key": "value"}'

    def test_clean_fence_with_text_before(self):
        """Text before fences stays; clean_json_response only strips start/end fences.
        _parse_json handles full extraction via JSON boundary detection."""
        text = 'Here is the plan:\n```json\n{"steps": []}\n```'
        cleaned = clean_json_response(text)
        # The closing ``` at the end gets stripped
        assert cleaned.endswith('{"steps": []}')
        # _parse_json extracts the JSON
        assert _parse_json(text) == {"steps": []}

    def test_clean_inline_fence(self):
        text = '```{"x": 1}```'
        assert clean_json_response(text) == '{"x": 1}'

    def test_clean_only_fences(self):
        text = '```json\n```'
        assert clean_json_response(text) == ''

    def test_clean_whitespace_around(self):
        text = '  \n  {"a": 1}  \n  '
        assert clean_json_response(text) == '{"a": 1}'


# ---------------------------------------------------------------------------
# End-to-end: reviewer approves on iteration 1
# ---------------------------------------------------------------------------

class TestTaskCompletion:

    def test_approve_on_first_iteration(self, db, mock_sandbox):
        task = _make_task(db, "Build hello world app")

        router = _FakeRouter({
            "planner": [(_plan_json(), 0.005)],
            "executor": [(_exec_json(), 0.003)],
            "reviewer": [(_review_json("APPROVED", "All tests pass"), 0.004)],
        })

        with patch("app.core.orchestrator.DockerSandbox", return_value=mock_sandbox):
            orch = Orchestrator(db, router=router)
            result = asyncio.run(orch.run_task(task.id, max_iterations=3))

        assert result.status == "completed"
        mock_sandbox.start.assert_called_once()
        mock_sandbox.stop.assert_called_once()
        assert result.total_spent_usd > 0

    def test_creates_agent_runs(self, db, mock_sandbox):
        task = _make_task(db, "Test runs creation")

        router = _FakeRouter({
            "planner": [(_plan_json(), 0.001)],
            "executor": [(_exec_json(), 0.001)],
            "reviewer": [(_review_json(), 0.001)],
        })

        with patch("app.core.orchestrator.DockerSandbox", return_value=mock_sandbox):
            orch = Orchestrator(db, router=router)
            asyncio.run(orch.run_task(task.id, max_iterations=3))

        runs = db.query(AgentRun).filter(AgentRun.task_id == task.id).all()
        roles = [r.agent_role for r in runs]
        assert "planner" in roles
        assert "executor" in roles
        assert "reviewer" in roles

# ---------------------------------------------------------------------------
# Revision retry loop
# ---------------------------------------------------------------------------

class TestRevisionRetry:

    def test_revision_then_approval(self, db, mock_sandbox):
        task = _make_task(db, "Fix failing test")
        router = _FakeRouter({
            "planner": [
                (_plan_json(), 0.005),
                (_plan_json([{"action": "run_command", "command": "echo fixed"}]), 0.005),
            ],
            "executor": [(_exec_json(), 0.003), (_exec_json(), 0.003)],
            "reviewer": [
                (_review_json("NEEDS_REVISION", "Tests still failing"), 0.004),
                (_review_json("APPROVED", "All good now"), 0.004),
            ],
        })
        with patch("app.core.orchestrator.DockerSandbox", return_value=mock_sandbox):
            orch = Orchestrator(db, router=router)
            result = asyncio.run(orch.run_task(task.id, max_iterations=5))
        assert result.status == "completed"
        runs = db.query(AgentRun).filter(AgentRun.task_id == task.id).all()
        assert len(runs) >= 4


# ---------------------------------------------------------------------------
# Max iteration boundary
# ---------------------------------------------------------------------------

class TestMaxIterations:

    def test_fails_after_max_iterations(self, db, mock_sandbox):
        task = _make_task(db, "Impossible task")
        router = _FakeRouter({
            "planner": [(_plan_json(), 0.001)] * 5,
            "executor": [(_exec_json(), 0.001)] * 5,
            "reviewer": [(_review_json("NEEDS_REVISION", "nope"), 0.001)] * 5,
        })
        with patch("app.core.orchestrator.DockerSandbox", return_value=mock_sandbox):
            orch = Orchestrator(db, router=router)
            result = asyncio.run(orch.run_task(task.id, max_iterations=3))
        assert result.status == "failed"
        mock_sandbox.stop.assert_called_once()

# ---------------------------------------------------------------------------
# Budget cutoff
# ---------------------------------------------------------------------------

class TestBudgetCutoff:

    def test_fails_when_budget_exceeded(self, db, mock_sandbox):
        task = _make_task(db, "Expensive task", budget=0.05)
        router = _FakeRouter({
            "planner": [(_plan_json(), 0.03), (_plan_json(), 0.03)],
            "executor": [(_exec_json(), 0.03), (_exec_json(), 0.03)],
            "reviewer": [(_review_json("NEEDS_REVISION", "keep going"), 0.03)] * 5,
        })
        with patch("app.core.orchestrator.DockerSandbox", return_value=mock_sandbox):
            orch = Orchestrator(db, router=router)
            result = asyncio.run(orch.run_task(task.id, max_iterations=5))
        assert result.status == "failed"
        assert result.total_spent_usd >= 0.05
        mock_sandbox.stop.assert_called_once()

    def test_no_budget_check_when_zero(self, db, mock_sandbox):
        task = _make_task(db, "Unlimited task", budget=0)
        router = _FakeRouter({
            "planner": [(_plan_json(), 0.01)],
            "executor": [(_exec_json(), 0.01)],
            "reviewer": [(_review_json(), 0.01)],
        })
        with patch("app.core.orchestrator.DockerSandbox", return_value=mock_sandbox):
            orch = Orchestrator(db, router=router)
            result = asyncio.run(orch.run_task(task.id, max_iterations=3))
        assert result.status == "completed"


# ---------------------------------------------------------------------------
# Container cleanup
# ---------------------------------------------------------------------------

class TestCleanup:

    def test_stop_called_on_success(self, db, mock_sandbox):
        task = _make_task(db, "Quick task")
        router = _FakeRouter({
            "planner": [(_plan_json(), 0.001)],
            "executor": [(_exec_json(), 0.001)],
            "reviewer": [(_review_json(), 0.001)],
        })
        with patch("app.core.orchestrator.DockerSandbox", return_value=mock_sandbox):
            orch = Orchestrator(db, router=router)
            asyncio.run(orch.run_task(task.id, max_iterations=3))
        mock_sandbox.stop.assert_called_once()

    def test_stop_called_on_failure(self, db, mock_sandbox):
        task = _make_task(db, "Failing task", budget=0.001)
        router = _FakeRouter({
            "planner": [(_plan_json(), 0.01)],
            "executor": [(_exec_json(), 0.01)],
            "reviewer": [(_review_json("NEEDS_REVISION", "bad"), 0.01)],
        })
        with patch("app.core.orchestrator.DockerSandbox", return_value=mock_sandbox):
            orch = Orchestrator(db, router=router)
            asyncio.run(orch.run_task(task.id, max_iterations=5))
        mock_sandbox.stop.assert_called_once()

    def test_stop_called_on_exception(self, db, mock_sandbox):
        mock_sandbox.start.side_effect = RuntimeError("Docker is broken")
        task = _make_task(db, "Crash task")
        router = _FakeRouter({
            "planner": [(_plan_json(), 0.001)],
            "executor": [(_exec_json(), 0.001)],
            "reviewer": [(_review_json(), 0.001)],
        })
        with patch("app.core.orchestrator.DockerSandbox", return_value=mock_sandbox):
            orch = Orchestrator(db, router=router)
            with pytest.raises(RuntimeError, match="Docker is broken"):
                asyncio.run(orch.run_task(task.id, max_iterations=3))
        mock_sandbox.stop.assert_called_once()


# ---------------------------------------------------------------------------
# Task not found
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_nonexistent_task_raises(self, db, mock_sandbox):
        router = _FakeRouter({})
        with patch("app.core.orchestrator.DockerSandbox", return_value=mock_sandbox):
            orch = Orchestrator(db, router=router)
            with pytest.raises(ValueError, match="not found"):
                asyncio.run(orch.run_task("nonexistent-id"))
