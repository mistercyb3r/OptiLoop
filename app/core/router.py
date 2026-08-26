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
    "deepseek/deepseek-chat": {
        "prompt_price_per_token": 0.14 / 1_000_000,
        "completion_price_per_token": 0.28 / 1_000_000,
        "context_length": 64_000,
        "max_completion_tokens": 8192,
    },
    "deepseek/deepseek-v4-flash": {
        "prompt_price_per_token": 0.0826 / 1_000_000,
        "completion_price_per_token": 0.1652 / 1_000_000,
        "context_length": 1_048_576,
        "max_completion_tokens": 8192,
    },
    "qwen/qwen-2.5-coder-32b-instruct": {
        "prompt_price_per_token": 0.10 / 1_000_000,
        "completion_price_per_token": 0.10 / 1_000_000,
        "context_length": 131_072,
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
    "openai/gpt-4o": {
        "prompt_price_per_token": 2.50 / 1_000_000,
        "completion_price_per_token": 10.00 / 1_000_000,
        "context_length": 128_000,
        "max_completion_tokens": 16384,
    },
}

_TIER1_MODELS = {"deepseek/deepseek-v4-flash", "openai/gpt-4o-mini", "qwen/qwen-2.5-coder-32b-instruct"}
_TIER2_MODELS = {"deepseek/deepseek-chat", "openai/gpt-4o-mini", "qwen/qwen-2.5-coder-32b-instruct"}
_TIER3_MODELS = {"anthropic/claude-3.5-sonnet", "openai/gpt-4o"}

# Verified fallback model used when any model returns 404 or errors
DEFAULT_PLANNER_MODEL = "anthropic/claude-3.5-sonnet"
DEFAULT_EXECUTOR_MODEL = "deepseek/deepseek-chat"
DEFAULT_REVIEWER_MODEL = "deepseek/deepseek-chat"

# Verified fallback model used when any model returns 404 or errors
_FALLBACK_MODEL = "openai/gpt-4o-mini"

# Models shown in the Web UI dropdown
AVAILABLE_MODELS = [
    {"id": "auto", "label": "Auto-Route (Smart)", "tier": "auto"},
    {"id": "openai/gpt-4o-mini", "label": "GPT-4o Mini", "tier": "cheap"},
    {"id": "deepseek/deepseek-chat", "label": "DeepSeek Chat", "tier": "mid"},
    {"id": "deepseek/deepseek-v4-flash", "label": "DeepSeek V4 Flash", "tier": "cheap"},
    {"id": "qwen/qwen-2.5-coder-32b-instruct", "label": "Qwen 2.5 Coder 32B", "tier": "cheap"},
    {"id": "xiaomi/mimo-v2.5", "label": "Xiaomi MiMo v2.5", "tier": "mid"},
    {"id": "anthropic/claude-3.5-sonnet", "label": "Claude 3.5 Sonnet", "tier": "high"},
    {"id": "openai/gpt-4o", "label": "GPT-4o", "tier": "high"},
]


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
        """Return tiers: always use canonical assignments for known models,
        fall back to dynamic classification only for truly unknown catalogues."""
        from app.core.cost_calculator import MODEL_PRICING

        # Always use canonical tiers — they are intersectioned with MODEL_PRICING
        # in select_model(), so unpriced models are safely excluded.
        known_priced = set(FALLBACK_MODELS.keys()) & set(MODEL_PRICING.keys())
        return {
            "1": sorted(_TIER1_MODELS & known_priced),
            "2": sorted(_TIER2_MODELS & known_priced),
            "3": sorted(_TIER3_MODELS & known_priced),
        }

    # -- Model selection ----------------------------------------------------

    def select_model(self, agent_role, complexity="medium",
                     target_budget_usd=0.0, total_spent_usd=0.0):
        """Pick the best model for the given agent role and constraints.

        Only returns models that exist in MODEL_PRICING to prevent
        'Unknown model' errors downstream.
        """
        from app.core.cost_calculator import MODEL_PRICING

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

        # Filter to only models with known pricing
        priced = [m for m in candidates if m in MODEL_PRICING]
        if priced:
            return priced[0]

        # Last resort: return the hardcoded fallback
        logger.warning("No priced models in tier %s, using fallback", tier_key)
        return _FALLBACK_MODEL

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

    async def call_llm(self, messages, model, agent_run_id, db_session,
                       override_model=None):
        """POST a chat-completion request and persist a CostMetric.

        If override_model is set, use that model directly.
        If the primary model returns a 404 or any HTTP error, retries
        automatically with the verified fallback model (openai/gpt-4o-mini).
        """
        from app.models.db_models import CostMetric

        # Use override if provided, otherwise use auto-routed model
        effective_model = override_model or model

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        tried_models = []
        last_error = None

        for attempt_model in [effective_model, _FALLBACK_MODEL]:
            if attempt_model in tried_models:
                continue
            tried_models.append(attempt_model)

            payload = {"model": attempt_model, "messages": messages}

            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        OPENROUTER_CHAT_URL, json=payload,
                        headers=headers, timeout=60.0,
                    )
                    if resp.status_code >= 400:
                        error_body = resp.text[:500]
                        logger.error(
                            "OpenRouter %s for model %s: %s",
                            resp.status_code, attempt_model, error_body,
                        )
                        resp.raise_for_status()

                body = resp.json()
                usage = body.get("usage", {})
                prompt_tokens = usage.get("prompt_tokens", 0)
                completion_tokens = usage.get("completion_tokens", 0)

                cost = self.calculator.calculate_cost(
                    model=attempt_model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )

                metric = CostMetric(
                    agent_run_id=agent_run_id,
                    model_name=attempt_model,
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

                if attempt_model != effective_model:
                    logger.warning(
                        "Model %s failed (%s) - fell back to %s",
                        effective_model, last_error, attempt_model,
                    )

                return {
                    "text": text,
                    "model_used": attempt_model,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "cost_usd": cost,
                }

            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                last_error = str(exc)
                logger.warning(
                    "LLM call failed for %s: %s", attempt_model, last_error,
                )
                continue

        # All attempts failed — return empty result instead of crashing
        logger.error("All LLM models failed for agent_run %s", agent_run_id)
        return {
            "text": "",
            "model_used": "",
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cost_usd": 0.0,
        }
