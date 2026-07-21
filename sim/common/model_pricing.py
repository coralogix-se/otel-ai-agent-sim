"""Per-model USD pricing for simulated LLM cost (input/output/cache tokens).

Rates are USD per 1M tokens, aligned with vendor list prices (July 2026):
- Anthropic: https://platform.claude.com/docs/en/about-claude/models/overview
- OpenAI / Codex: https://developers.openai.com/codex/models and API pricing tiers
- Google Gemini: https://ai.google.dev/gemini-api/docs/pricing
- Cursor list prices: https://cursor.com/docs/models
- GitHub Copilot ``github.copilot.cost`` uses the same API-equivalent rates for the routed model.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelRates:
    """USD per 1M tokens."""

    input: float
    output: float
    cache_read: float | None = None  # default: 10% of input (Anthropic-style)
    cache_write: float | None = None  # default: 125% of input (5-min cache write)

    def cache_read_rate(self) -> float:
        return self.input * 0.10 if self.cache_read is None else self.cache_read

    def cache_write_rate(self) -> float:
        return self.input * 1.25 if self.cache_write is None else self.cache_write


def _r(inp: float, out: float, *, cache_read: float | None = None, cache_write: float | None = None) -> ModelRates:
    return ModelRates(inp, out, cache_read=cache_read, cache_write=cache_write)


# Explicit ids used in sim pools (and common aliases).
_EXACT: dict[str, ModelRates] = {
    # --- Anthropic Claude (Opus/Sonnet/Haiku/Fable) ---
    "claude-fable-5": _r(10.0, 50.0),
    "claude-sonnet-5": _r(3.0, 15.0),
    "claude-opus-4-8": _r(5.0, 25.0),
    "claude-opus-4-7": _r(5.0, 25.0),
    "claude-opus-4-6": _r(5.0, 25.0),
    "claude-opus-4-20250514": _r(15.0, 75.0),  # legacy Opus 4 snapshot (was much pricier)
    "claude-sonnet-4-6": _r(3.0, 15.0),
    "claude-sonnet-4-5": _r(3.0, 15.0),
    "claude-sonnet-4-20250514": _r(3.0, 15.0),
    "claude-haiku-4-5": _r(1.0, 5.0),
    "claude-haiku-4.5": _r(1.0, 5.0),
    "claude-haiku-4-5-20251001": _r(1.0, 5.0),
    "claude-sonnet-4.6": _r(3.0, 15.0),
    "claude-sonnet-4.5": _r(3.0, 15.0),
    "claude-opus-4.8": _r(5.0, 25.0),
    "claude-opus-4.7": _r(5.0, 25.0),
    "claude-opus-4.6": _r(5.0, 25.0),
    "claude-opus-4.5": _r(5.0, 25.0),
    # --- OpenAI / Codex (GPT-5.6 Sol/Terra/Luna + prior) ---
    "gpt-5.6": _r(5.0, 30.0, cache_read=0.50, cache_write=6.25),
    "gpt-5.6-sol": _r(5.0, 30.0, cache_read=0.50, cache_write=6.25),
    "gpt-5.6-terra": _r(2.50, 15.0, cache_read=0.25, cache_write=3.125),
    "gpt-5.6-luna": _r(1.0, 6.0, cache_read=0.10, cache_write=1.25),
    "gpt-5.5": _r(5.0, 30.0, cache_read=0.50),
    "gpt-5.5-pro": _r(30.0, 180.0),
    "gpt-5.4": _r(2.50, 15.0, cache_read=0.25),
    "gpt-5.4-mini": _r(0.75, 4.50, cache_read=0.075),
    "gpt-5.4-nano": _r(0.20, 1.25, cache_read=0.02),
    "gpt-5.3-codex": _r(1.75, 14.0, cache_read=0.175),
    "gpt-5.3-codex-spark": _r(1.75, 14.0, cache_read=0.175),
    "gpt-5.2-codex": _r(1.75, 14.0, cache_read=0.175),
    "gpt-5.1-codex": _r(1.75, 14.0, cache_read=0.175),
    "gpt-5-codex": _r(1.75, 14.0, cache_read=0.175),
    "codex-mini-latest": _r(0.75, 4.50, cache_read=0.075),
    "o4-mini": _r(0.55, 2.20),
    "gpt-5-mini": _r(0.75, 4.50, cache_read=0.075),
    "gpt-4o": _r(2.50, 10.0, cache_read=1.25),
    "gpt-4o-mini": _r(0.15, 0.60, cache_read=0.075),
    "gpt-4.1": _r(2.0, 8.0, cache_read=0.50),
    # --- Google Gemini ---
    "gemini-3.6-flash": _r(1.50, 7.50, cache_read=0.15),
    "gemini-3.5-flash": _r(1.50, 9.00, cache_read=0.15),
    "gemini-3.1-pro-preview": _r(2.00, 12.00),
    "gemini-3.1-pro": _r(2.00, 12.00),
    "gemini-3.1-pro-preview-customtools": _r(2.00, 12.00),
    "gemini-3-pro-preview": _r(2.00, 12.00),
    "gemini-3-flash-preview": _r(0.50, 3.00),
    "gemini-3.1-flash-lite": _r(0.10, 0.40),
    "gemini-3.1-flash-lite-preview": _r(0.10, 0.40),
    "gemini-2.5-pro": _r(1.25, 10.00),
    "gemini-2.5-flash": _r(0.15, 0.60),
    "gemini-2.5-flash-lite": _r(0.10, 0.40),
    "gemini-2.0-flash": _r(0.10, 0.40),
    "gemini-3-flash": _r(0.50, 3.00),
    "gemma-4-31b-it": _r(0.20, 0.80),
    "gemma-4-26b-a4b-it": _r(0.20, 0.80),
    # --- Copilot / Cursor / misc routed ids ---
    "mai-code-1-flash": _r(0.75, 4.50),
    "raptor-mini": _r(0.75, 4.50),
    "kimi-k2.7-code": _r(0.95, 4.00, cache_read=0.19),
    "composer-2": _r(0.50, 2.50),  # legacy Cursor-native
    "composer-2.5": _r(0.50, 2.50),
    "composer-2.5-fast": _r(3.00, 15.00),
    # --- Third-party models routable via Cursor / Claude Code ---
    "grok-4.5": _r(2.00, 6.00, cache_read=0.50),
    "grok-4.3": _r(1.25, 2.50, cache_read=0.20),
    "grok-4.20": _r(2.00, 6.00, cache_read=0.20),
    "grok-build-0.1": _r(1.00, 2.00, cache_read=0.20),
    "qwen3.7-max": _r(2.50, 7.50, cache_read=0.25),
}

# Longest-prefix / pattern fallbacks (order matters).
_PREFIX_RULES: tuple[tuple[str, ModelRates], ...] = (
    ("claude-fable", _r(10.0, 50.0)),
    ("claude-opus", _r(5.0, 25.0)),
    ("claude-sonnet", _r(3.0, 15.0)),
    ("claude-haiku", _r(1.0, 5.0)),
    ("gpt-5.6-sol", _r(5.0, 30.0, cache_read=0.50, cache_write=6.25)),
    ("gpt-5.6-terra", _r(2.50, 15.0, cache_read=0.25, cache_write=3.125)),
    ("gpt-5.6-luna", _r(1.0, 6.0, cache_read=0.10, cache_write=1.25)),
    ("gpt-5.6", _r(5.0, 30.0, cache_read=0.50, cache_write=6.25)),
    ("gpt-5.5-pro", _r(30.0, 180.0)),
    ("gpt-5.5", _r(5.0, 30.0, cache_read=0.50)),
    ("gpt-5.4-mini", _r(0.75, 4.50, cache_read=0.075)),
    ("gpt-5.4-nano", _r(0.20, 1.25, cache_read=0.02)),
    ("gpt-5.4", _r(2.50, 15.0, cache_read=0.25)),
    ("gpt-5.3-codex", _r(1.75, 14.0, cache_read=0.175)),
    ("gpt-5-codex", _r(1.75, 14.0, cache_read=0.175)),
    ("gemini-3.6", _r(1.50, 7.50, cache_read=0.15)),
    ("gemini-3.5", _r(1.50, 9.00, cache_read=0.15)),
    ("gemini-3.1-pro", _r(2.00, 12.00)),
    ("gemini-3-pro", _r(2.00, 12.00)),
    ("gemini-3-flash", _r(0.50, 3.00)),
    ("gemma-4", _r(0.20, 0.80)),
    ("kimi-k2", _r(0.95, 4.00, cache_read=0.19)),
    ("grok-4.5", _r(2.00, 6.00, cache_read=0.50)),
    ("grok-build", _r(1.00, 2.00, cache_read=0.20)),
    ("gemini-3.1-flash", _r(0.10, 0.40)),
    ("gemini-2.5-pro", _r(1.25, 10.00)),
    ("gemini-2.5-flash-lite", _r(0.10, 0.40)),
    ("gemini-2.5-flash", _r(0.15, 0.60)),
    ("gemini-2.0", _r(0.10, 0.40)),
    ("gpt-4o-mini", _r(0.15, 0.60, cache_read=0.075)),
    ("gpt-4o", _r(2.50, 10.0, cache_read=1.25)),
    ("gpt-4.1", _r(2.0, 8.0, cache_read=0.50)),
    ("o4-mini", _r(0.55, 2.20)),
    ("o3-mini", _r(0.55, 2.20)),
    ("composer", _r(2.50, 15.0)),
    ("grok-", _r(1.25, 2.50, cache_read=0.20)),
    ("qwen3.7", _r(2.50, 7.50, cache_read=0.25)),
    ("qwen", _r(2.50, 7.50, cache_read=0.25)),
)

_DEFAULT = _r(3.0, 15.0)  # legacy Sonnet-shaped default (previous sim flat rate)


def model_rates(model: str) -> ModelRates:
    """Resolve pricing for a ``gen_ai.request.model`` id (unknown → Sonnet-tier default)."""
    key = (model or "").strip().lower()
    if not key:
        return _DEFAULT
    if key in _EXACT:
        return _EXACT[key]
    for prefix, rates in _PREFIX_RULES:
        if key.startswith(prefix):
            return rates
    if re.search(r"fable", key):
        return _r(10.0, 50.0)
    if re.search(r"opus", key):
        return _r(5.0, 25.0)
    if re.search(r"haiku", key):
        return _r(1.0, 5.0)
    if re.search(r"sonnet", key):
        return _r(3.0, 15.0)
    if re.search(r"flash", key):
        return _r(0.15, 0.60)
    if re.search(r"mini|nano", key):
        return _r(0.75, 4.50)
    return _DEFAULT


def estimate_llm_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    jitter_usd: float = 0.0,
) -> float:
    """Total USD for a turn (matches Claude ``cost_usd`` / Copilot ``github.copilot.cost`` semantics)."""
    rates = model_rates(model)
    inp = max(0, int(input_tokens))
    out = max(0, int(output_tokens))
    cr = max(0, int(cache_read_tokens))
    cc = max(0, int(cache_creation_tokens))
    # Bill non-cache input at full rate; cache read/write at discounted/premium cache rates.
    billable_input = max(0, inp - cr)
    cost = (
        billable_input * rates.input
        + out * rates.output
        + cr * rates.cache_read_rate()
        + cc * rates.cache_write_rate()
    ) / 1_000_000.0
    if jitter_usd:
        cost += jitter_usd
    return round(max(0.0, cost), 8)


def estimate_span_prices(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    price_jitter: bool = True,
) -> tuple[float, float]:
    """``gen_ai.prompt_price`` / ``gen_ai.response_price`` for dashboard spans."""
    rates = model_rates(model)
    inp = max(0, int(input_tokens))
    out = max(0, int(output_tokens))
    j_in = random.uniform(0.0, 1e-4) if price_jitter else 0.0
    j_out = random.uniform(0.0, 1e-4) if price_jitter else 0.0
    prompt_price = round(inp * rates.input / 1_000_000.0 + j_in, 10)
    response_price = round(out * rates.output / 1_000_000.0 + j_out, 10)
    return prompt_price, response_price
