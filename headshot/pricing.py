"""
Per-model prices in USD per 1M tokens, and cost calculation from usage.

Sources (checked 2026-10-20):
  Anthropic: Claude API model table (platform.claude.com pricing)
  OpenAI:    https://developers.openai.com/api/docs/pricing (Standard tier)

Prices change. When a provider updates them, edit this table; every new span
is costed with the table that was loaded when it ran, and the per-token usage
is stored too, so old runs can be re-costed.
"""

from __future__ import annotations

PRICES_CHECKED_ON = "2026-10-20"

# Anthropic usage keys: input_tokens, output_tokens, cache_write_tokens, cache_read_tokens
CLAUDE_PRICES: dict[str, dict[str, float]] = {
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_write": 2.50, "cache_read": 0.20},
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_write": 5.00, "cache_read": 0.20},
    # Deprecated by Anthropic (retirement date TBD). Legacy list price.
    "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30},
}

# OpenAI image usage keys: text_input_tokens, image_input_tokens, output_tokens
IMAGE_PRICES: dict[str, dict[str, float]] = {
    "gpt-image-2.5-sunburst": {"text_input": 5.00, "image_input": 8.00, "output": 30.00},
    "gpt-image-2.5-flare": {"text_input": 5.00, "image_input": 8.00, "output": 30.00},
    "gpt-image-2": {"text_input": 5.00, "image_input": 8.00, "output": 30.00},
    "gpt-image-1.5": {"text_input": 5.00, "image_input": 8.00, "output": 32.00},
    # OpenAI shuts gpt-image-1 down on 2026-10-23.
    "gpt-image-1": {"text_input": 5.00, "image_input": 10.00, "output": 40.00},
}

_PER_TOKEN = 1_000_000


def _price_table(model: str) -> dict[str, float] | None:
    if model in CLAUDE_PRICES:
        return CLAUDE_PRICES[model]
    if model in IMAGE_PRICES:
        return IMAGE_PRICES[model]
    # Dated snapshots such as "gpt-image-2.5-sunburst-2026-09-08" share the base price.
    for table in (IMAGE_PRICES, CLAUDE_PRICES):
        for name, prices in table.items():
            if model.startswith(name + "-"):
                return prices
    return None


def is_priced(model: str) -> bool:
    return _price_table(model) is not None


def cost_usd(model: str, usage: dict[str, int]) -> float | None:
    """Return the cost of one call, or None when the model has no price entry."""
    prices = _price_table(model)
    if prices is None:
        return None

    if "input" in prices:  # Claude
        total = (
            usage.get("input_tokens", 0) * prices["input"]
            + usage.get("output_tokens", 0) * prices["output"]
            + usage.get("cache_write_tokens", 0) * prices["cache_write"]
            + usage.get("cache_read_tokens", 0) * prices["cache_read"]
        )
    else:  # OpenAI image
        total = (
            usage.get("text_input_tokens", 0) * prices["text_input"]
            + usage.get("image_input_tokens", 0) * prices["image_input"]
            + usage.get("output_tokens", 0) * prices["output"]
        )
    return round(total / _PER_TOKEN, 6)
