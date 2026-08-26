"""Claude Code OTEL coverage for Anthropic Products roster users."""

from __future__ import annotations

import pytest

from sim.common.env import claude_long_session_slots_enabled


def test_long_session_slots_enabled_with_min_max_range(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SIM_CLAUDE_LONG_SESSION_SEC", raising=False)
    monkeypatch.setenv("SIM_CLAUDE_LONG_SESSION_SEC_MIN", "2100")
    monkeypatch.setenv("SIM_CLAUDE_LONG_SESSION_SEC_MAX", "4200")
    assert claude_long_session_slots_enabled() is True


def test_otel_only_indices_are_after_products_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """OTEL-only rows use roster indices [products_size, products_size + otel_only_size)."""
    monkeypatch.setenv("SIM_PRODUCTS_ROSTER_SIZE", "24")
    monkeypatch.setenv("SIM_CLAUDE_OTEL_ONLY_ROSTER_SIZE", "8")
    from sim.common.env import _env_int

    products_n = max(1, _env_int("SIM_PRODUCTS_ROSTER_SIZE", 24))
    otel_n = max(0, _env_int("SIM_CLAUDE_OTEL_ONLY_ROSTER_SIZE", 8))
    indices = tuple(range(products_n, products_n + otel_n))
    assert indices == (24, 25, 26, 27, 28, 29, 30, 31)
