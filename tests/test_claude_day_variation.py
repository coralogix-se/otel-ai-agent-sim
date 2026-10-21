"""Claude Code daily / hourly activity multipliers (onlineboutique brief §9)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from sim.claude.day_variation import (
    claude_activity_multiplier,
    claude_daily_multiplier,
    claude_date_unit_interval,
    claude_hourly_shape,
    claude_session_starts_per_cycle,
)
from sim.claude import day_variation as dv


def test_date_unit_interval_stable() -> None:
    d = date(2026, 10, 8)
    a = claude_date_unit_interval(d)
    b = claude_date_unit_interval(d)
    assert a == b
    assert 0.0 <= a < 1.0


def test_weekday_bases_weekend_lower(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIM_CLAUDE_DAY_VARIATION", "true")
    monkeypatch.setenv("SIM_CLAUDE_GROWTH_START_DATE", "2026-09-28")
    # 2026-10-10 = Saturday, 2026-10-07 = Wednesday
    sat = claude_daily_multiplier(date(2026, 10, 10))
    wed = claude_daily_multiplier(date(2026, 10, 7))
    assert sat < wed
    assert sat < 0.6


def test_hourly_shape_day_night(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIM_CLAUDE_HOURLY_SHAPE", "true")
    assert claude_hourly_shape(10) == 1.0
    assert claude_hourly_shape(19) == 0.3
    assert claude_hourly_shape(2) == 0.05


def test_activity_multiplier_compounds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIM_CLAUDE_DAY_VARIATION", "true")
    monkeypatch.setenv("SIM_CLAUDE_HOURLY_SHAPE", "true")
    monkeypatch.setenv("SIM_CLAUDE_GROWTH_START_DATE", "2026-09-28")
    day = datetime(2026, 10, 7, 10, 0, tzinfo=timezone.utc)  # Wed peak
    night = datetime(2026, 10, 7, 2, 0, tzinfo=timezone.utc)
    assert claude_activity_multiplier(when=day) > claude_activity_multiplier(when=night) * 5


def test_session_starts_per_cycle_from_daily_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIM_CLAUDE_DAY_VARIATION", "false")
    monkeypatch.setenv("SIM_CLAUDE_HOURLY_SHAPE", "true")
    monkeypatch.setenv("SIM_CLAUDE_DAILY_SESSIONS", "600")
    monkeypatch.setenv("TRACE_INTERVAL_SEC", "60")
    day = datetime(2026, 10, 7, 0, 0, tzinfo=timezone.utc)
    per_cycle = []
    for minute in range(24 * 60):
        when = day.replace(hour=minute // 60, minute=minute % 60)
        monkeypatch.setattr(dv, "claude_activity_multiplier", lambda when=when: dv.claude_hourly_shape(when.hour))
        per_cycle.append(claude_session_starts_per_cycle())
    assert sum(per_cycle) == pytest.approx(600)
    assert min(per_cycle) > 0  # nights still start sessions, just rarely
