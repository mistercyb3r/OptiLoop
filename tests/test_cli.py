"""Tests for the OptiLoop CLI."""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock, PropertyMock

import pytest
from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Mock data fixtures
# ---------------------------------------------------------------------------

MOCK_TASK_SUMMARY = {
    "id": "abc-123",
    "prompt": "Build a calculator",
    "status": "completed",
    "target_budget_usd": 1.50,
    "total_spent_usd": 0.025,
    "created_at": "2025-01-15T10:00:00",
    "updated_at": "2025-01-15T10:05:00",
}

MOCK_TASK_DETAIL = {
    **MOCK_TASK_SUMMARY,
    "total_prompt_tokens": 5000,
    "total_completion_tokens": 2000,
    "agent_runs": [
        {"id": "run-1", "agent_role": "planner", "model_name": "test/m",
         "iteration": 1, "status": "completed", "timestamp": "2025-01-15T10:01:00"},
        {"id": "run-2", "agent_role": "executor", "model_name": "test/m",
         "iteration": 1, "status": "completed", "timestamp": "2025-01-15T10:02:00"},
        {"id": "run-3", "agent_role": "reviewer", "model_name": "test/m",
         "iteration": 1, "status": "completed", "timestamp": "2025-01-15T10:03:00"},
    ],
    "cost_metrics": [
        {"model_name": "test/m", "prompt_tokens": 2000,
         "completion_tokens": 800, "cost_usd": 0.010},
        {"model_name": "test/m", "prompt_tokens": 1500,
         "completion_tokens": 700, "cost_usd": 0.008},
        {"model_name": "test/m", "prompt_tokens": 1500,
         "completion_tokens": 500, "cost_usd": 0.007},
    ],
    "execution_logs": [
        {"step_type": "reasoning", "content": "Plan: create calculator",
         "timestamp": "2025-01-15T10:01:00"},
        {"step_type": "diff", "content": "Wrote file: calc.py",
         "timestamp": "2025-01-15T10:02:00"},
        {"step_type": "command", "content": "$ python calc.py\nexit=0",
         "timestamp": "2025-01-15T10:02:30"},
    ],
}


def _mock_get(path):
    """Return a function that mocks httpx.get, always returning MOCK_TASK_DETAIL."""
    def inner(*args, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = MOCK_TASK_DETAIL
        return resp
    return inner


def _mock_post(path, return_data=None):
    """Return a function that mocks httpx.post."""
    def inner(*args, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if return_data is None:
            resp.json.return_value = MOCK_TASK_SUMMARY
        else:
            resp.json.return_value = return_data
        return resp
    return inner


# ---------------------------------------------------------------------------
# submit command
# ---------------------------------------------------------------------------

class TestSubmit:

    def test_submit_shows_task_id(self):
        with patch("cli.main.httpx.post", _mock_post("/api/tasks")):
            result = runner.invoke(app, ["submit", "Fix the bug", "--budget", "1.0"])
        assert result.exit_code == 0
        assert "abc-123" in result.output
        assert "Task Submitted" in result.output

    def test_submit_with_default_budget(self):
        mock_resp = {**MOCK_TASK_SUMMARY, "target_budget_usd": 0.50}
        with patch("cli.main.httpx.post", _mock_post("/api/tasks", mock_resp)):
            result = runner.invoke(app, ["submit", "Build app"])
        assert result.exit_code == 0
        assert "$0.50" in result.output

    def test_submit_connection_error(self):
        import httpx
        with patch("cli.main.httpx.post", side_effect=httpx.ConnectError("refused")):
            result = runner.invoke(app, ["submit", "Test"])
        assert result.exit_code == 1
        assert "Cannot connect" in result.output


# ---------------------------------------------------------------------------
# status command
# ---------------------------------------------------------------------------

class TestStatus:

    def test_status_displays_table(self):
        with patch("cli.main.httpx.get", _mock_get("/api/tasks/abc-123")):
            result = runner.invoke(app, ["status", "abc-123"])
        assert result.exit_code == 0
        assert "completed" in result.output
        assert "Build a calculator" in result.output
        assert "$0.025" in result.output

    def test_status_shows_tokens(self):
        with patch("cli.main.httpx.get", _mock_get("/api/tasks/abc-123")):
            result = runner.invoke(app, ["status", "abc-123"])
        assert result.exit_code == 0
        assert "5,000" in result.output
        assert "2,000" in result.output

    def test_status_404(self):
        import httpx
        resp = MagicMock()
        resp.status_code = 404
        with patch("cli.main.httpx.get", side_effect=httpx.HTTPStatusError("404", request=MagicMock(), response=resp)):
            result = runner.invoke(app, ["status", "nonexistent"])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# logs command
# ---------------------------------------------------------------------------

class TestLogs:

    def test_logs_displays_entries(self):
        with patch("cli.main.httpx.get", _mock_get("/api/tasks/abc-123")):
            result = runner.invoke(app, ["logs", "abc-123"])
        assert result.exit_code == 0
        assert "reasoning" in result.output
        assert "Plan: create calculator" in result.output
        assert "diff" in result.output

    def test_logs_empty(self):
        empty_detail = {**MOCK_TASK_DETAIL, "execution_logs": []}
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = empty_detail
        with patch("cli.main.httpx.get", return_value=resp):
            result = runner.invoke(app, ["logs", "abc-123"])
        assert result.exit_code == 0
        assert "No logs found" in result.output


# ---------------------------------------------------------------------------
# metrics command
# ---------------------------------------------------------------------------

class TestMetrics:

    def test_metrics_displays_table(self):
        with patch("cli.main.httpx.get", _mock_get("/api/tasks/abc-123")):
            result = runner.invoke(app, ["metrics", "abc-123"])
        assert result.exit_code == 0
        assert "Planner" in result.output
        assert "Executor" in result.output
        assert "Reviewer" in result.output
        assert "Total" in result.output
        assert "$" in result.output


# ---------------------------------------------------------------------------
# stop command
# ---------------------------------------------------------------------------

class TestStop:

    def test_stop_shows_confirmation(self):
        with patch("cli.main.httpx.post",
                   _mock_post("/api/tasks/abc-123/stop",
                              {"status": "cancelled", "task_id": "abc-123"})):
            result = runner.invoke(app, ["stop", "abc-123"])
        assert result.exit_code == 0
        assert "cancelled" in result.output
        assert "abc-123" in result.output
