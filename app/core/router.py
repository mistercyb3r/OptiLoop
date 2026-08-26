"""OpenRouter Dynamic Model Router for OptiLoop."""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.cost_calculator import CostCalculator

logger = logging.getLogger(__name__)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
_CATALOGUE_TTL = 300
_BUDGET_DOWNGRADE_RATIO = 0.80

FALLBACK_MODELS: dict[str, dict[str, Any]] = {
    "deepseek/deepseek-v4-flash": {
        "prompt_price_per_token": 0.27 / 1_000_000,
        "completion_price_per_token": 1.10 / 1_000_000,
        "context_length": 128_000,
        "max_completion_tokens": 8192,
    },
    "xiaomi/mimo-v2.5": {
        "prompt_price_per_token": 0.14 / 1_000_000,
        "completion_price_per_token": 0.56 / 1_000_000,
        "context_length": 128_000,
        "max_completion_tokens": 8192,
    },
    "anthropic/claude-3.5-sonnet": {
        "prompt_price_per_token": 3.00 / 1_000_000,
        "completion_price_per_token": 15.00 / 1_000_000,
        "context_length": 200_000,
        "max_completion_tokens": 8192,
    },
    "openai/gpt-4o-mini": {
        "prompt_price_per_token": 0.15 / 1_000_000,
        "completion_price_per_token": 0.60 / 1_000_000,
        "context_length": 128_000,
        "max_completion_tokens": 16384,
    },
}

_TIER1_MODELS = {"deepseek/deepseek-v4-flash", "openai/gpt-4o-mini"}
_TIER2_MODELS = {"deepseek/deepseek-v4-flash", "xiaomi/mimo-v2.5"}
_TIER3_MODELS = {"xiaomi/mimo-v2.5", "anthropic/claude-3.5-sonnet"}


@dataclass
class ModelInfo:
    """Lightweight model catalogue entry."""
    id: str
    prompt_price_per_token: float
    completion_price_per_token: float
    context_length: int
    max_completion_tokens: int = 8192


class ModelRouter:
    """Select and invoke LLMs via OpenRouter with automatic cost control."""

    def __init__(self, cost_calculator=None, api_key=None):
        self.calculator = cost_calculator or CostCalculator()
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY", "")
        self._catalogue: dict[str, ModelInfo] = {}
        self._catalogue_ts: float = 0.0

    def fetch_catalogue(self, *, timeout=10.0):
        """Fetch live model data from OpenRouter; fallback on error."""
        try:
            resp = httpx.get(OPENROUTER_MODELS_URL, timeout=timeout)
            resp.raise_for_status()
            raw = resp.json().get("data", [])
            cat = {}
            for m in raw:
                mid = m.get("id", "")
                if not mid:
                    continue
                p = m.get("pricing", {})
                cat[mid] = ModelInfo(
                    id=mid,
                    prompt_price_per_token=float(p.get("prompt", 0)),
                    completion_price_per_token=float(p.get("completion", 0)),
                    context_length=m.get("context_length", 0),
                    max_completion_tokens=m.get("top_provider", {}).get(
                        "max_completion_tokens", 8192),
                )
            self._catalogue = cat
            self._catalogue_ts = time.time()
            return cat
        except Exception as exc:
            logger.warning("OpenRouter fetch failed (%s) - using fallback", exc)
            return self._load_fallback()

    def get_catalogue(self):
        """Return current catalogue, fetching if stale or empty."""
        if not self._catalogue or (time.time() - self._catalogue_ts) > _CATALOGUE_TTL:
            self.fetch_catalogue()
        return self._catalogue

    def _load_fallback(self):
        """Populate catalogue from FALLBACK_MODELS."""
        cat = {}
        for mid, d in FALLBACK_MODELS.items():
            cat[mid] = ModelInfo(
                id=mid,
                prompt_price_per_token=d["prompt_price_per_token"],
                completion_price_per_token=d["completion_price_per_token"],
                context_length=d.get("context_length", 0),
                max_completion_tokens=d.get("max_completion_tokens", 8192),
            )
        self._catalogue = cat
        self._catalogue_ts = time.time()
        return cat


    # -- Tier classification ------------------------------------------------

    def classify_tiers(self):
        """Classify catalogue models into cost-based tiers."""
        cat = self.get_catalogue()
        if not cat:
            return {"1": [], "2": [], "3": []}
        scored = []
        for mid, info in cat.items():
            avg = (info.prompt_price_per_token + info.completion_price_per_token) / 2
            scored.append((mid, avg))
        scored.sort(key=lambda x: x[1])
        n = len(scored)
        if n == 0:
            return {"1": [], "2": [], "3": []}
        third = max(1, n // 3)
        return {
            "1": [s[0] for s in scored[:third]],
            "2": [s[0] for s in scored[third: 2 * third]],
            "3": [s[0] for s in scored[2 * third:]],
        }

    def _tiers(self):
        """Return tiers: canonical for known models, dynamic otherwise."""
        cat = self.get_catalogue()
        known = set(cat.keys())
        if known == set(FALLBACK_MODELS.keys()):
            return {
                "1": sorted(_TIER1_MODELS & known),
                "2": sorted(_TIER2_MODELS & known),
                "3": sorted(_TIER3_MODELS & known),
            }
        return self.classify_tiers()

    # -- Model selection ----------------------------------------------------

    def select_model(self, agent_role, complexity="medium",
                     target_budget_usd=0.0, total_spent_usd=0.0):
        """Pick the best model for the given agent role and constraints.

        - Planner defaults to Tier 3.
        - Executor defaults to Tier 2 (Tier 3 if complexity=high).
        - Reviewer defaults to Tier 2 (Tier 3 if complexity=high).
        - Budget guard: if >=80% spent, forces Tier 1 regardless of role.
        """
        tiers = self._tiers()

        if target_budget_usd > 0:
            utilisation = total_spent_usd / target_budget_usd
            if utilisation >= _BUDGET_DOWNGRADE_RATIO:
                logger.info("Budget guard triggered (%.0f%% used) -> forcing Tier 1",
                            utilisation * 100)
                tier_key = "1"
            else:
                tier_key = self._role_default_tier(agent_role, complexity)
        else:
            tier_key = self._role_default_tier(agent_role, complexity)

        candidates = tiers.get(tier_key, [])
        if not candidates:
            for alt in ("2", "1", "3"):
                candidates = tiers.get(alt, [])
                if candidates:
                    break
        if not candidates:
            raise RuntimeError("No models available in any tier")
        return candidates[0]

    @staticmethod
    def _role_default_tier(agent_role, complexity):
        """Map (role, complexity) -> tier key."""
        role = agent_role.lower()
        cplx = complexity.lower()
        if role == "planner":
            return "3"
        if role == "executor":
            return "3" if cplx == "high" else "2"
        if role == "reviewer":
            return "3" if cplx == "high" else "2"
        return "2"


    # -- LLM execution -----------------------------------------------------

    async def call_llm(self, messages, model, agent_run_id, db_session):
        """POST a chat-completion request and persist a CostMetric."""
        from app.models.db_models import CostMetric

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {"model": model, "messages": messages}

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                OPENROUTER_CHAT_URL, json=payload,
                headers=headers, timeout=60.0,
            )
            resp.raise_for_status()

        body = resp.json()
        usage = body.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        cost = self.calculator.calculate_cost(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

        metric = CostMetric(
            agent_run_id=agent_run_id,
            model_name=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            search_calls=0,
            cost_usd=cost,
        )
        db_session.add(metric)
        db_session.commit()
        db_session.refresh(metric)

        choices = body.get("choices", [])
        text = choices[0]["message"]["content"] if choices else ""

        return {
            "text": text,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost_usd": cost,
        }
