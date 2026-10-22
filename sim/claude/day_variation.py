"""Claude Code daily / hourly activity multipliers (onlineboutique brief §9)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sim.common.day_variation import daily_activity_multiplier, date_unit_interval
from sim.common.env import _env_bool, _env_float, _env_int

# Re-export for existing imports/tests.
claude_date_unit_interval = date_unit_interval


def claude_daily_multiplier(d: date | None = None) -> float:
    enabled = _env_bool("SIM_CLAUDE_DAY_VARIATION", True) and _env_bool("SIM_DAY_VARIATION", True)
    return daily_activity_multiplier(d, enabled=enabled)


def claude_hourly_shape(hour_utc: int | None = None) -> float:
    """Claude Code only: 1.0 for 07–16 UTC, 0.3 for 17–22 UTC, 0.05 for 23–06 UTC."""
    if not _env_bool("SIM_CLAUDE_HOURLY_SHAPE", True):
        return 1.0
    h = hour_utc if hour_utc is not None else datetime.now(timezone.utc).hour
    h = int(h) % 24
    if 7 <= h <= 16:
        return 1.0
    if 17 <= h <= 22:
        return 0.3
    return 0.05


def claude_activity_multiplier(*, when: datetime | None = None) -> float:
    now = when or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    return claude_daily_multiplier(now.date()) * claude_hourly_shape(now.hour)


def claude_daily_sessions() -> float:
    """Sessions on a day with daily multiplier 1.0; drives slot pacing and the per-session cost/token split."""
    return max(1.0, _env_float("SIM_CLAUDE_DAILY_SESSIONS", 2100.0))


def claude_session_turns() -> int:
    return max(1, _env_int("SIM_CLAUDE_SESSION_TURNS_EXPECTED", 6))


def claude_session_starts_per_cycle() -> float:
    """Expected new sessions this cycle; each runs its turns one per cycle, so live ≈ this × turns."""
    cycles_per_hour = 3600.0 / max(1.0, _env_float("TRACE_INTERVAL_SEC", 3.0))
    shape_hours = sum(claude_hourly_shape(h) for h in range(24))
    return claude_daily_sessions() * claude_activity_multiplier() / (shape_hours * cycles_per_hour)


def claude_whale_session_weight() -> float:
    """How much likelier a top spender is to start the next session than any other user."""
    return max(1.0, _env_float("SIM_CLAUDE_WHALE_SESSION_WEIGHT", 4.0))
