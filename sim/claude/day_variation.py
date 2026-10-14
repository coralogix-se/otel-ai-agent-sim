"""Claude Code daily / hourly activity multipliers (onlineboutique brief §9)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sim.common.day_variation import daily_activity_multiplier, date_unit_interval
from sim.common.env import _env_bool, _env_float

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


def claude_peak_active_user_frac() -> float:
    return min(1.0, max(0.05, _env_float("SIM_CLAUDE_PEAK_ACTIVE_USER_FRAC", 0.25)))


def claude_target_emit_count(n_slots: int) -> int:
    if n_slots <= 0:
        return 0
    if not _env_bool("SIM_CLAUDE_ACTIVITY_PACING", True):
        return n_slots
    target = int(round(n_slots * claude_peak_active_user_frac() * claude_activity_multiplier()))
    return max(1, min(n_slots, target))
