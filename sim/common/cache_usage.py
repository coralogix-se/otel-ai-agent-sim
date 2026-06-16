"""Prompt-cache simulation for agent CLIs (OpenAI / Anthropic / Copilot-style billing).

Real multi-turn coding sessions typically read ~90–95% of the prompt from cache after the
first turn (system instructions, tool defs, repo context). Dashboard hit rate is usually
``cache_read / (cache_read + billable_input)``, so billable input must exclude cached tokens.
"""

from __future__ import annotations

import random

from sim.common.env import _env_float


def sim_prompt_cache_token_split(
    total_input_tokens: int,
    *,
    turn_index: int = 0,
    hit_prob_env: str = "SIM_PROMPT_CACHE_HIT_RATE",
    hit_prob_default: float = 0.96,
    frac_min_env: str = "SIM_PROMPT_CACHE_HIT_FRAC_MIN",
    frac_max_env: str = "SIM_PROMPT_CACHE_HIT_FRAC_MAX",
    frac_min_default: float = 0.92,
    frac_max_default: float = 0.98,
    first_turn_miss: bool = False,
) -> tuple[int, int, bool]:
    """
    Split a simulated prompt into billable input vs cache-read tokens.

    Returns ``(billable_input, cache_read, cache_hit)``.
    """
    total = max(1, int(total_input_tokens))
    if first_turn_miss and turn_index == 0:
        return total, 0, False

    hit_prob = _env_float(hit_prob_env, hit_prob_default)
    if random.random() >= hit_prob:
        return total, 0, False

    f_lo = min(
        _env_float(frac_min_env, frac_min_default),
        _env_float(frac_max_env, frac_max_default),
    )
    f_hi = max(
        _env_float(frac_min_env, frac_min_default),
        _env_float(frac_max_env, frac_max_default),
    )
    cache_read = max(1, min(total - 1, int(total * random.uniform(f_lo, f_hi))))
    billable = max(1, total - cache_read)
    return billable, cache_read, True
