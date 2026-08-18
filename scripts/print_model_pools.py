#!/usr/bin/env python3
"""Print simulator model pools and validate USD rates vs ``sim.common.model_pricing``.

Usage (from repo root)::

    python3 scripts/print_model_pools.py
    python3 scripts/print_model_pools.py --check   # exit 1 on pricing / coverage failures

Pricing expectations are the vendor list rates the sim intends to mirror (USD / 1M tokens).
Update ``EXPECTED_RATES`` when vendors change list prices; update pools in
``sim/common/constants.py``.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim.anthropic_admin.constants import ANTHROPIC_ADMIN_MODELS  # noqa: E402
from sim.common.constants import (  # noqa: E402
    CLAUDE_CODE_DEFAULT_MODEL,
    CODEX_CLI_MODELS,
    CODEX_DEFAULT_MODEL,
    COPILOT_CLI_MODELS,
    COPILOT_DEFAULT_MODEL,
    CURSOR_COMPOSER_MODELS,
    CURSOR_DEFAULT_MODEL,
    GEMINI_CLI_MODELS,
    GEMINI_DEFAULT_MODEL,
    _CLAUDE_CODE_MODELS,
)
from sim.common.model_pricing import (  # noqa: E402
    _DEFAULT,
    _EXACT,
    model_rates,
)

POOLS: dict[str, tuple[str, ...]] = {
    "claude_code": _CLAUDE_CODE_MODELS,
    "codex": CODEX_CLI_MODELS,
    "cursor": CURSOR_COMPOSER_MODELS,
    "copilot": COPILOT_CLI_MODELS,
    "gemini": GEMINI_CLI_MODELS,
    "anthropic_admin": ANTHROPIC_ADMIN_MODELS,
}

DEFAULTS: dict[str, str] = {
    "claude_code": CLAUDE_CODE_DEFAULT_MODEL,
    "codex": CODEX_DEFAULT_MODEL,
    "cursor": CURSOR_DEFAULT_MODEL,
    "copilot": COPILOT_DEFAULT_MODEL,
    "gemini": GEMINI_DEFAULT_MODEL,
    "anthropic_admin": "claude-sonnet-5",
}

# Glasswing / limited — must stay out of general Claude pool.
EXCLUDED_FROM_CLAUDE_POOL = ("claude-mythos-5",)

# Vendor list rates the sim should charge (input, output) USD per 1M tokens.
# Source notes: Anthropic pricing page (Sonnet 5 intro through 2026-08-31),
# OpenAI GPT-5.6 table (Terra/Luna cut 2026-07-30). Extend as pools grow.
EXPECTED_RATES: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.0, 50.0),
    "claude-sonnet-5": (2.0, 10.0),  # intro; bump to (3.0, 15.0) on 2026-09-01
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-sonnet-4-5-20250929": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "gpt-5.6": (5.0, 30.0),
    "gpt-5.6-sol": (5.0, 30.0),
    "gpt-5.6-terra": (2.0, 12.0),
    "gpt-5.6-luna": (0.20, 1.20),
    "gpt-5.5": (5.0, 30.0),
    "gpt-5.4": (2.50, 15.0),
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-5.4-nano": (0.20, 1.25),
    "gpt-5.3-codex": (1.75, 14.0),
    "gpt-5-codex": (1.75, 14.0),
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-3.1-pro": (2.00, 12.00),
    "gemini-3-flash-preview": (0.50, 3.00),
    "gemini-3.1-flash-lite": (0.10, 0.40),
    "composer-2.5-fast": (3.00, 15.00),
}


def _uniq(pool: tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in pool:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


def print_pools() -> None:
    for name, pool in POOLS.items():
        counts = Counter(pool)
        print(f"[{name}] default={DEFAULTS[name]}  entries={len(pool)} unique={len(counts)}")
        for model in _uniq(pool):
            n = counts[model]
            weight = f"  ×{n}" if n > 1 else ""
            rates = model_rates(model)
            print(f"  - {model}{weight}  ${rates.input:g}/${rates.output:g}")
        print()

    print("[excluded_from_claude_pool]")
    for model in EXCLUDED_FROM_CLAUDE_POOL:
        status = "PRESENT (bug)" if model in _CLAUDE_CODE_MODELS else "absent (ok)"
        print(f"  - {model}: {status}")
    print()


def check_pricing() -> list[str]:
    """Return human-readable failure strings (empty = ok)."""
    failures: list[str] = []

    for model in EXCLUDED_FROM_CLAUDE_POOL:
        if model in _CLAUDE_CODE_MODELS:
            failures.append(f"excluded model {model!r} is in _CLAUDE_CODE_MODELS")

    all_pool_models = sorted({m for pool in POOLS.values() for m in pool} | set(DEFAULTS.values()))
    for model in all_pool_models:
        rates = model_rates(model)
        key = model.strip().lower()
        if key not in _EXACT and (rates.input, rates.output) == (_DEFAULT.input, _DEFAULT.output):
            # Prefix rules may still price it; only warn via expected table miss below.
            pass
        if model in EXPECTED_RATES:
            want_in, want_out = EXPECTED_RATES[model]
            if (rates.input, rates.output) != (want_in, want_out):
                failures.append(
                    f"{model}: configured ${rates.input:g}/${rates.output:g} "
                    f"!= expected ${want_in:g}/${want_out:g}"
                )
        elif key not in _EXACT:
            failures.append(
                f"{model}: no exact pricing entry in model_pricing._EXACT "
                f"(resolves to ${rates.input:g}/${rates.output:g})"
            )

    for model, (want_in, want_out) in sorted(EXPECTED_RATES.items()):
        rates = model_rates(model)
        if (rates.input, rates.output) != (want_in, want_out):
            # Already reported when in pools; still catch orphan expected rows.
            msg = (
                f"{model}: configured ${rates.input:g}/${rates.output:g} "
                f"!= expected ${want_in:g}/${want_out:g}"
            )
            if msg not in failures:
                failures.append(msg)

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate pool coverage + EXPECTED_RATES; exit 1 on mismatch",
    )
    args = parser.parse_args()

    print_pools()

    if not args.check:
        return 0

    print("[pricing_check]")
    failures = check_pricing()
    if failures:
        for f in failures:
            print(f"  FAIL: {f}")
        print(f"\n{len(failures)} pricing/pool check(s) failed.")
        return 1
    print("  ok — pool models priced; EXPECTED_RATES match model_rates()")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
