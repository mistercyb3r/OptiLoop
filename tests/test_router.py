"""Tests for the OpenRouter Dynamic Model Router."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import SQLModel, Session, create_engine

from app.models.db_models import Task, AgentRun, CostMetric
from app.core.router import (
    ModelRouter, ModelInfo, FALLBACK_MODELS,
    _BUDGET_DOWNGRADE_RATIO,
)


@pytest.fixture()
def tmp_db(tmp_path):
    db_path = tmp_path / "test_router.db"
    eng = create_engine(f"sqlite:///{db_path}", echo=False)
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture()
def session(tmp_db):
    with Session(tmp_db) as s:
        yield s


@pytest.fixture()
def router():
    return ModelRouter(api_key="test-key-000")


# ---- Catalogue & Fallback ----

class TestCatalogue:

    def test_fallback_loads_all_models(self, router):
        cat = router._load_fallback()
        assert len(cat) == len(FALLBACK_MODELS)
        for mid in FALLBACK_MODELS:
            assert mid in cat

    def test_fallback_model_info_fields(self, router):
        cat = router._load_fallback()
        info = cat["deepseek/deepseek-v4-flash"]
        assert info.prompt_price_per_token == pytest.approx(0.0826e-6)
        assert info.completion_price_per_token == pytest.approx(0.1652e-6)
        assert info.context_length == 1_048_576

    def test_fetch_catalogue_fallback_on_error(self, router):
        with patch("app.core.router.httpx.get", side_effect=Exception("offline")):
            cat = router.fetch_catalogue()
        assert "deepseek/deepseek-v4-flash" in cat

    def test_fetch_catalogue_parses_live_data(self, router):
        fake_resp = MagicMock()
        fake_resp.raise_for_status = MagicMock()
        fake_resp.json.return_value = {
            "data": [
                {
                    "id": "test/model-a",
                    "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                    "context_length": 64000,
                    "top_provider": {"max_completion_tokens": 4096},
                },
                {
                    "id": "test/model-b",
                    "pricing": {"prompt": "0.000010", "completion": "0.000020"},
                    "context_length": 128000,
                    "top_provider": {"max_completion_tokens": 8192},
                },
            ]
        }
        with patch("app.core.router.httpx.get", return_value=fake_resp):
            cat = router.fetch_catalogue()
        assert len(cat) == 2
        assert "test/model-a" in cat
        assert cat["test/model-a"].prompt_price_per_token == 1e-6

    def test_get_catalogue_caches(self, router):
        cat1 = router.get_catalogue()
        cat2 = router.get_catalogue()
        assert cat1 is cat2


# ---- Tier Classification ----

class TestTiers:

    def test_classify_tiers_returns_three_groups(self, router):
        tiers = router.classify_tiers()
        assert set(tiers.keys()) == {"1", "2", "3"}

    def test_canonical_tiers_for_known_models(self, router):
        router._load_fallback()
        tiers = router._tiers()
        assert "deepseek/deepseek-v4-flash" in tiers["1"]
        assert "openai/gpt-4o-mini" in tiers["1"]
        assert "xiaomi/mimo-v2.5" in tiers["2"]
        assert "anthropic/claude-3.5-sonnet" in tiers["3"]

    def test_dynamic_tiers_from_custom_catalogue(self):
        r = ModelRouter()
        r._catalogue = {
            "cheap-a": ModelInfo("cheap-a", 0.1e-6, 0.3e-6, 8000),
            "mid-b": ModelInfo("mid-b", 1.0e-6, 5.0e-6, 16000),
            "exp-c": ModelInfo("exp-c", 10.0e-6, 30.0e-6, 32000),
        }
        r._catalogue_ts = 9999999999  # prevent refresh
        tiers = r.classify_tiers()
        assert "cheap-a" in tiers["1"]
        assert "mid-b" in tiers["2"]
        assert "exp-c" in tiers["3"]


# ---- Model Selection ----

class TestModelSelection:

    def test_planner_defaults_to_tier3(self, router):
        model = router.select_model("planner")
        tiers = router._tiers()
        assert model in tiers["3"]

    def test_executor_defaults_to_tier2(self, router):
        model = router.select_model("executor")
        tiers = router._tiers()
        assert model in tiers["2"]

    def test_executor_high_complexity_to_tier3(self, router):
        model = router.select_model("executor", complexity="high")
        tiers = router._tiers()
        assert model in tiers["3"]

    def test_reviewer_defaults_to_tier2(self, router):
        model = router.select_model("reviewer")
        tiers = router._tiers()
        assert model in tiers["2"]

    def test_reviewer_high_complexity_to_tier3(self, router):
        model = router.select_model("reviewer", complexity="high")
        tiers = router._tiers()
        assert model in tiers["3"]

    def test_unknown_role_defaults_to_tier2(self, router):
        model = router.select_model("debugger")
        tiers = router._tiers()
        assert model in tiers["2"]


# ---- Budget Guard ----

class TestBudgetGuard:

    def test_force_tier1_at_80_percent(self, router):
        model = router.select_model(
            "planner", target_budget_usd=10.0, total_spent_usd=8.0,
        )
        tiers = router._tiers()
        assert model in tiers["1"]

    def test_force_tier1_above_80_percent(self, router):
        model = router.select_model(
            "planner", target_budget_usd=10.0, total_spent_usd=9.5,
        )
        tiers = router._tiers()
        assert model in tiers["1"]

    def test_normal_tier_below_80_percent(self, router):
        model = router.select_model(
            "planner", target_budget_usd=10.0, total_spent_usd=5.0,
        )
        tiers = router._tiers()
        assert model in tiers["3"]

    def test_zero_budget_no_guard(self, router):
        model = router.select_model(
            "planner", target_budget_usd=0.0, total_spent_usd=999.0,
        )
        tiers = router._tiers()
        assert model in tiers["3"]

    def test_executor_budget_guard(self, router):
        model = router.select_model(
            "executor", target_budget_usd=10.0, total_spent_usd=8.5,
        )
        tiers = router._tiers()
        assert model in tiers["1"]

    def test_exact_80_percent_triggers(self, router):
        model = router.select_model(
            "executor", target_budget_usd=10.0, total_spent_usd=8.0,
        )
        tiers = router._tiers()
        assert model in tiers["1"]


# ---- API Request Payload Construction ----

class TestApiPayload:

    def test_call_builds_correct_headers(self, router):
        assert router.api_key == "test-key-000"

    def test_call_uses_correct_url(self):
        from app.core.router import OPENROUTER_CHAT_URL
        assert OPENROUTER_CHAT_URL == "https://openrouter.ai/api/v1/chat/completions"

    def test_env_key_read(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "env-key-123")
        r = ModelRouter()
        assert r.api_key == "env-key-123"

    def test_explicit_key_overrides_env(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
        r = ModelRouter(api_key="explicit-key")
        assert r.api_key == "explicit-key"


# ---- Async call_llm with mocked HTTP ----

class TestCallLlm:

    def _run(self, coro):
        return asyncio.run(coro)

    def _make_db(self, tmp_path):
        from sqlmodel import create_engine, SQLModel
        eng = create_engine(f"sqlite:///{tmp_path / 'call_llm.db'}", echo=False)
        SQLModel.metadata.create_all(eng)
        return eng

    def _seed_task_and_run(self, session):
        task = Task(prompt="Write tests", status="running")
        session.add(task)
        session.commit()
        session.refresh(task)
        run = AgentRun(task_id=task.id, agent_role="executor",
                       model_name="deepseek/deepseek-v4-flash")
        session.add(run)
        session.commit()
        session.refresh(run)
        return run

    def test_call_llm_records_metric(self, tmp_path, router):
        eng = self._make_db(tmp_path)
        with Session(eng) as sess:
            run = self._seed_task_and_run(sess)

            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = ""
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {
                "choices": [{"message": {"content": "Hello world"}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            }
            mock_client = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            with patch("app.core.router.httpx.AsyncClient", return_value=mock_client):
                result = self._run(router.call_llm(
                    messages=[{"role": "user", "content": "hi"}],
                    model="deepseek/deepseek-v4-flash",
                    agent_run_id=run.id,
                    db_session=sess,
                ))

            assert result["text"] == "Hello world"
            assert result["model_used"] == "deepseek/deepseek-v4-flash"
            assert result["prompt_tokens"] == 100
            assert result["completion_tokens"] == 50
            assert result["cost_usd"] > 0

            metrics = sess.query(CostMetric).all()
            assert len(metrics) == 1
            m = metrics[0]
            assert m.agent_run_id == run.id
            assert m.model_name == "deepseek/deepseek-v4-flash"
            assert m.prompt_tokens == 100
            assert m.completion_tokens == 50
            assert m.cost_usd == pytest.approx(0.000017, abs=1e-5)

    def test_call_llm_sends_correct_payload(self, tmp_path, router):
        eng = self._make_db(tmp_path)
        with Session(eng) as sess:
            run = self._seed_task_and_run(sess)

            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = ""
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
            mock_client = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            with patch("app.core.router.httpx.AsyncClient", return_value=mock_client):
                self._run(router.call_llm(
                    messages=[{"role": "user", "content": "test"}],
                    model="deepseek/deepseek-v4-flash",
                    agent_run_id=run.id,
                    db_session=sess,
                ))

            call_args = mock_client.post.call_args
            assert call_args[0][0] == "https://openrouter.ai/api/v1/chat/completions"
            payload = call_args[1]["json"]
            assert payload["model"] == "deepseek/deepseek-v4-flash"
            assert payload["messages"] == [{"role": "user", "content": "test"}]
            headers = call_args[1]["headers"]
            assert headers["Authorization"] == "Bearer test-key-000"
