"""
Cost Measurement Engine for OptiLoop.

Tracks per-token pricing for supported OpenRouter models and computes
exact USD costs for agent runs.  Pricing data represents standard
OpenRouter rates (USD per token) as of mid-2025.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelPricing:
    """Per-token pricing for a single model."""

    prompt_cost_per_token: float  # USD per prompt token
    completion_cost_per_token: float  # USD per completion token
    search_cost_per_call: float  # USD per search / tool-call


# ---------------------------------------------------------------------------
# Pricing catalogue  (USD per token – not per million)
# These are representative OpenRouter rates for popular models.
# ---------------------------------------------------------------------------

MODEL_PRICING: dict[str, ModelPricing] = {
    # DeepSeek V4 Flash — ultra-low cost
    "deepseek/deepseek-v4-flash": ModelPricing(
        prompt_cost_per_token=0.27 / 1_000_000,      # $0.27/M
        completion_cost_per_token=1.10 / 1_000_000,   # $1.10/M
        search_cost_per_call=0.0,
    ),
    # Xiaomi MiMo v2.5
    "xiaomi/mimo-v2.5": ModelPricing(
        prompt_cost_per_token=0.14 / 1_000_000,       # $0.14/M
        completion_cost_per_token=0.56 / 1_000_000,    # $0.56/M
        search_cost_per_call=0.0,
    ),
    # Anthropic Claude 3.5 Sonnet
    "anthropic/claude-3.5-sonnet": ModelPricing(
        prompt_cost_per_token=3.00 / 1_000_000,       # $3.00/M
        completion_cost_per_token=15.00 / 1_000_000,   # $15.00/M
        search_cost_per_call=0.0,
    ),
    # OpenAI GPT-4o Mini
    "openai/gpt-4o-mini": ModelPricing(
        prompt_cost_per_token=0.15 / 1_000_000,       # $0.15/M
        completion_cost_per_token=0.60 / 1_000_000,    # $0.60/M
        search_cost_per_call=0.0,
    ),
}


# ---------------------------------------------------------------------------
# CostCalculator
# ---------------------------------------------------------------------------

# Default cost per search / tool-call when the model entry doesn't specify one
_DEFAULT_SEARCH_COST = 0.003  # $0.003 per search call


class CostCalculator:
    """Compute USD cost for LLM API calls across supported models.

    Usage::

        calc = CostCalculator()
        cost = calc.calculate_cost(
            model="anthropic/claude-3.5-sonnet",
            prompt_tokens=5000,
            completion_tokens=2000,
            search_calls=3,
        )
        print(cost)  # 0.045 + adjustments …
    """

    def __init__(
        self,
        pricing_catalogue: dict[str, ModelPricing] | None = None,
        default_search_cost: float = _DEFAULT_SEARCH_COST,
    ) -> None:
        self.pricing = pricing_catalogue or MODEL_PRICING.copy()
        self.default_search_cost = default_search_cost

    # ----- core API --------------------------------------------------------

    def calculate_cost(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        search_calls: int = 0,
    ) -> float:
        """Return the exact USD cost for the given usage.

        Args:
            model: Model identifier (e.g. ``"anthropic/claude-3.5-sonnet"``).
            prompt_tokens: Number of tokens in the prompt.
            completion_tokens: Number of tokens in the completion.
            search_calls: Number of search / tool-calls made.

        Returns:
            Cost in USD, rounded to 6 decimal places.

        Raises:
            ValueError: If the model is not in the pricing catalogue.
        """
        if model not in self.pricing:
            raise ValueError(
                f"Unknown model '{model}'. "
                f"Available models: {list(self.pricing.keys())}"
            )

        pricing = self.pricing[model]

        prompt_cost = prompt_tokens * pricing.prompt_cost_per_token
        completion_cost = completion_tokens * pricing.completion_cost_per_token
        search_cost = search_calls * (
            pricing.search_cost_per_call or self.default_search_cost
        )

        total = prompt_cost + completion_cost + search_cost
        return round(total, 6)

    def is_over_budget(self, current_spent: float, target_budget: float) -> bool:
        """Return ``True`` when *current_spent* meets or exceeds *target_budget*.

        A target budget of ``0`` or negative is treated as "no budget" and
        always returns ``False``.
        """
        if target_budget <= 0:
            return False
        return current_spent >= target_budget

    # ----- helpers ---------------------------------------------------------

    def available_models(self) -> list[str]:
        """Return the list of models with known pricing."""
        return list(self.pricing.keys())
