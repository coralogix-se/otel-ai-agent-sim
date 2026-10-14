"""Shared date-seeded daily activity multiplier (onlineboutique brief §9)."""

from __future__ import annotations

import hashlib
import os
from datetime import date, datetime, timezone

from sim.common.env import _env_bool

_WEEKDAY_BASE: tuple[float, ...] = (
    1.0,  # Mon
    1.1,  # Tue
    1.15,  # Wed
    1.05,  # Thu
    0.85,  # Fri
    0.35,  # Sat
    0.30,  # Sun
)


def _parse_start_date() -> date:
    raw = os.environ.get(
        "SIM_DEMO_GROWTH_START_DATE",
        os.environ.get("SIM_CLAUDE_GROWTH_START_DATE", "2026-09-28"),
    ).strip()
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return date(2026, 9, 28)


def date_unit_interval(d: date | None = None) -> float:
    day = d or datetime.now(timezone.utc).date()
    digest = hashlib.sha256(day.isoformat().encode()).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def daily_activity_multiplier(d: date | None = None, *, enabled: bool | None = None) -> float:
    """
    weekday_base × (0.8 + 0.4·u(d)) × (1 + 0.01·n(d)).

    When ``enabled`` is None, honor ``SIM_DAY_VARIATION`` (default true).
    """
    if enabled is None:
        enabled = _env_bool("SIM_DAY_VARIATION", True)
    if not enabled:
        return 1.0
    day = d or datetime.now(timezone.utc).date()
    base = _WEEKDAY_BASE[day.weekday()]
    u = date_unit_interval(day)
    n = max(0, (day - _parse_start_date()).days)
    return max(0.05, base * (0.8 + 0.4 * u) * (1.0 + 0.01 * n))
