"""Tests for OptiLoop Step 1 - DB init, cost calculation, budget checks."""
from __future__ import annotations

import pytest
from sqlmodel import SQLModel, Session, create_engine, text

from app.models.db_models import Task, AgentRun, CostMetric, ExecutionLog
from app.core.cost_calculator import CostCalculator, ModelPricing


@pytest.fixture()
def tmp_db(tmp_path):
    db_path = tmp_path / "test_optiloop.db"
    url = f"sqlite:///{db_path}"
    eng = create_engine(url, echo=False)
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture()
def session(tmp_db):
    with Session(tmp_db) as s:
        yield s


@pytest.fixture()
def calculator():
    return CostCalculator()


class TestDatabaseInitialization:

    def test_tables_are_created(self, tmp_db):
        with Session(tmp_db) as sess:
            result = sess.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            )
            names = {row[0] for row in result.fetchall()}
        assert {"tasks", "agent_runs", "cost_metrics", "execution_logs"}.issubset(names)

    def test_task_insert_and_read(self, session):
        task = Task(prompt="Fix login bug", status="pending")
        session.add(task)
        session.commit()
        session.refresh(task)
        assert task.id
        assert task.prompt == "Fix login bug"
        assert task.total_spent_usd == 0.0
        assert task.target_budget_usd is None

    def test_agent_run_insert(self, session):
        task = Task(prompt="Refactor auth", status="running")
        session.add(task)
        session.commit()
        session.refresh(task)
        run = AgentRun(task_id=task.id, agent_role="executor",
                       model_name="anthropic/claude-3.5-sonnet", iteration=1)
        session.add(run)
        session.commit()
        session.refresh(run)
        assert run.id
        assert run.task_id == task.id

    def test_cost_metric_insert(self, session):
        task = Task(prompt="Write tests", status="running")
        session.add(task)
        session.commit()
        session.refresh(task)
        run = AgentRun(task_id=task.id, agent_role="planner",
                       model_name="openai/gpt-4o-mini")
        session.add(run)
        session.commit()
        session.refresh(run)
        metric = CostMetric(agent_run_id=run.id, model_name="openai/gpt-4o-mini",
                            prompt_tokens=1000, completion_tokens=500,
                            search_calls=2, cost_usd=0.003150)
        session.add(metric)
        session.commit()
        session.refresh(metric)
        assert metric.id
        assert metric.cost_usd == pytest.approx(0.003150)

    def test_execution_log_insert(self, session):
        task = Task(prompt="Add caching", status="running")
        session.add(task)
        session.commit()
        session.refresh(task)
        log = ExecutionLog(task_id=task.id, step_type="command",
                           content="python -m pytest tests/ -v")
        session.add(log)
        session.commit()
        session.refresh(log)
        assert log.id
        assert log.step_type == "command"


class TestCostCalculation:

    def test_deepseek_v4_flash(self, calculator):
        cost = calculator.calculate_cost(
            "deepseek/deepseek-v4-flash", 1_000_000, 1_000_000
        )
        # 1M * $0.0826/M + 1M * $0.1652/M = $0.2478
        assert cost == pytest.approx(0.2478, abs=1e-4)

    def test_xiaomi_mimo_v25(self, calculator):
        cost = calculator.calculate_cost("xiaomi/mimo-v2.5", 2_000_000, 500_000)
        # 2M * $0.14/M + 0.5M * $0.56/M = $0.56
        assert cost == pytest.approx(0.56, abs=1e-6)

    def test_claude_sonnet_4(self, calculator):
        cost = calculator.calculate_cost(
            "anthropic/claude-3.5-sonnet", 5000, 2000
        )
        # 5000*$3/M + 2000*$15/M = $0.015 + $0.03 = $0.045
        assert cost == pytest.approx(0.045, abs=1e-6)

    def test_gpt4o_mini(self, calculator):
        cost = calculator.calculate_cost("openai/gpt-4o-mini", 10_000, 4_000)
        # 10000*$0.15/M + 4000*$0.60/M = $0.0015 + $0.0024 = $0.0039
        assert cost == pytest.approx(0.0039, abs=1e-6)

    def test_zero_tokens(self, calculator):
        assert calculator.calculate_cost("openai/gpt-4o-mini", 0, 0) == 0.0

    def test_search_calls_add_cost(self, calculator):
        cost = calculator.calculate_cost(
            "deepseek/deepseek-v4-flash", 0, 0, search_calls=10
        )
        # 10 * $0.003 = $0.03
        assert cost == pytest.approx(0.03, abs=1e-6)

    def test_unknown_model_uses_fallback_pricing(self, calculator):
        """Unknown models fall back to gpt-4o-mini pricing instead of raising."""
        cost = calculator.calculate_cost("nonexistent/model", 1000, 500)
        # Same cost as gpt-4o-mini: 1000*$0.15/M + 500*$0.60/M = $0.00045
        assert cost == pytest.approx(0.00045, abs=1e-6)

    def test_cost_rounded_six_decimals(self, calculator):
        cost = calculator.calculate_cost(
            "deepseek/deepseek-v4-flash", 1, 1
        )
        assert round(cost, 6) == cost


class TestBudgetChecks:

    def test_under_budget(self, calculator):
        assert calculator.is_over_budget(5.0, 10.0) is False

    def test_at_budget_exact(self, calculator):
        assert calculator.is_over_budget(10.0, 10.0) is True

    def test_over_budget(self, calculator):
        assert calculator.is_over_budget(15.0, 10.0) is True

    def test_zero_budget_no_limit(self, calculator):
        assert calculator.is_over_budget(999.99, 0.0) is False

    def test_negative_budget_no_limit(self, calculator):
        assert calculator.is_over_budget(999.99, -1.0) is False

    def test_zero_spending_positive_budget(self, calculator):
        assert calculator.is_over_budget(0.0, 5.0) is False

    def test_tiny_budget(self, calculator):
        assert calculator.is_over_budget(0.0001, 0.0001) is True
        assert calculator.is_over_budget(0.00009, 0.0001) is False


class TestHelpers:

    def test_available_models(self, calculator):
        models = calculator.available_models()
        assert len(models) == 6
        assert "anthropic/claude-3.5-sonnet" in models
        assert "qwen/qwen-2.5-coder-32b-instruct" in models

    def test_custom_pricing(self):
        custom = {"m": ModelPricing(1.0 / 1e6, 2.0 / 1e6, 0.0)}
        calc = CostCalculator(pricing_catalogue=custom)
        assert calc.calculate_cost("m", 1000, 1000) == pytest.approx(0.003, abs=1e-6)


class TestIntegration:

    def test_store_and_check_budget(self, session, calculator):
        task = Task(prompt="Optimize queries", status="running")
        session.add(task)
        session.commit()
        session.refresh(task)

        run = AgentRun(
            task_id=task.id, agent_role="executor",
            model_name="anthropic/claude-3.5-sonnet",
        )
        session.add(run)
        session.commit()
        session.refresh(run)

        cost = calculator.calculate_cost(
            "anthropic/claude-3.5-sonnet", 5000, 2000
        )
        metric = CostMetric(
            agent_run_id=run.id, model_name="anthropic/claude-3.5-sonnet",
            prompt_tokens=5000, completion_tokens=2000,
            search_calls=0, cost_usd=cost,
        )
        session.add(metric)
        session.commit()
        session.refresh(metric)

        assert metric.cost_usd == pytest.approx(0.045, abs=1e-6)

        task.total_spent_usd = metric.cost_usd
        session.add(task)
        session.commit()
        session.refresh(task)

        assert task.total_spent_usd == pytest.approx(0.045, abs=1e-6)
        assert not calculator.is_over_budget(task.total_spent_usd, 1.0)
