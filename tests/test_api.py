"""Tests for the FastAPI REST API."""
from __future__ import annotations

import asyncio
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine

from app.models.db_models import Task, AgentRun, CostMetric, ExecutionLog


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path):
    """Create a TestClient with an isolated DB."""
    from app.api.server import app

    db_url = f"sqlite:///{tmp_path / 'api_test.db'}"

    # Patch the engine and create tables
    test_engine = create_engine(db_url, echo=False)
    SQLModel.metadata.create_all(test_engine)

    def override_session():
        return Session(test_engine)

    # Patch the module-level engine and session factory
    import app.api.server as srv_mod
    import app.models.db_models as db_mod

    old_engine = srv_mod.engine
    srv_mod.engine = test_engine

    # Also patch the engine used inside endpoint functions
    with patch.object(srv_mod, "engine", test_engine):
        with TestClient(app) as c:
            yield c, test_engine, override_session

    srv_mod.engine = old_engine


def _seed_task(db_engine, prompt="Test task", budget=1.0, status="completed"):
    """Insert a task with related data and return its ID."""
    with Session(db_engine) as sess:
        task = Task(prompt=prompt, target_budget_usd=budget, status=status)
        sess.add(task)
        sess.commit()
        sess.refresh(task)

        run = AgentRun(task_id=task.id, agent_role="planner",
                       model_name="test/model", iteration=1, status="completed")
        sess.add(run)
        sess.commit()
        sess.refresh(run)

        metric = CostMetric(agent_run_id=run.id, model_name="test/model",
                            prompt_tokens=100, completion_tokens=50, cost_usd=0.01)
        sess.add(metric)

        log = ExecutionLog(task_id=task.id, step_type="reasoning",
                           content="Plan created")
        sess.add(log)
        sess.commit()
        return task.id


# ---------------------------------------------------------------------------
# POST /api/tasks
# ---------------------------------------------------------------------------

class TestCreateTask:

    def test_create_task_returns_201(self, client):
        c, db_eng, _ = client
        with patch("app.api.server._active_tasks", {}):
            resp = c.post("/api/tasks", json={
                "prompt": "Build a calculator",
                "target_budget_usd": 1.50,
            })
        assert resp.status_code == 201
        data = resp.json()
        assert data["prompt"] == "Build a calculator"
        assert data["target_budget_usd"] == 1.50
        assert data["status"] in ("pending", "running")
        assert data["id"]

    def test_create_task_persists_to_db(self, client):
        c, db_eng, _ = client
        with patch("app.api.server._active_tasks", {}):
            resp = c.post("/api/tasks", json={
                "prompt": "Write tests",
                "target_budget_usd": 0.50,
            })
        task_id = resp.json()["id"]
        with Session(db_eng) as sess:
            task = sess.get(Task, task_id)
            assert task is not None
            assert task.prompt == "Write tests"
            assert task.target_budget_usd == 0.50

# ---------------------------------------------------------------------------
# GET /api/tasks
# ---------------------------------------------------------------------------

class TestListTasks:

    def test_list_tasks_returns_array(self, client):
        c, db_eng, _ = client
        tid = _seed_task(db_eng)
        resp = c.get("/api/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        ids = [t["id"] for t in data]
        assert tid in ids

    def test_list_tasks_returns_summary_fields(self, client):
        c, db_eng, _ = client
        _seed_task(db_eng, prompt="Field check")
        resp = c.get("/api/tasks")
        item = resp.json()[0]
        for key in ("id", "prompt", "status", "target_budget_usd",
                     "total_spent_usd", "created_at", "updated_at"):
            assert key in item


# ---------------------------------------------------------------------------
# GET /api/tasks/{task_id}
# ---------------------------------------------------------------------------

class TestGetTask:

    def test_get_task_detail(self, client):
        c, db_eng, _ = client
        tid = _seed_task(db_eng)
        resp = c.get(f"/api/tasks/{tid}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == tid
        assert data["prompt"] == "Test task"
        assert len(data["agent_runs"]) >= 1
        assert len(data["cost_metrics"]) >= 1
        assert len(data["execution_logs"]) >= 1
        assert data["total_prompt_tokens"] >= 100
        assert data["total_completion_tokens"] >= 50

    def test_get_task_404(self, client):
        c, _, _ = client
        resp = c.get("/api/tasks/nonexistent")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/tasks/{task_id}/stop
# ---------------------------------------------------------------------------

class TestStopTask:

    def test_stop_updates_status(self, client):
        c, db_eng, _ = client
        tid = _seed_task(db_eng, status="running")
        resp = c.post(f"/api/tasks/{tid}/stop")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

        with Session(db_eng) as sess:
            task = sess.get(Task, tid)
            assert task.status == "cancelled"

    def test_stop_404(self, client):
        c, _, _ = client
        resp = c.post("/api/tasks/nonexistent/stop")
        assert resp.status_code == 404

    def test_stop_cancels_background_task(self, client):
        c, db_eng, _ = client
        tid = _seed_task(db_eng, status="running")

        mock_bg = MagicMock()
        mock_bg.done.return_value = False

        with patch("app.api.server._active_tasks", {tid: mock_bg}):
            resp = c.post(f"/api/tasks/{tid}/stop")
            assert resp.status_code == 200
            mock_bg.cancel.assert_called_once()
