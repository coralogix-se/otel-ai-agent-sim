"""Twice-daily Prometheus sample exposure for Cursor Usage (onlineboutique brief §8).

c4c emits ~2 samples/series/day. Continuous scrape restatement of in-memory gauges
would yield ~1440 samples/day at a 60s scrape. ``collect()`` therefore yields series
only inside short UTC windows (default 11:00 and 23:00, ±90s).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from sim.common.env import _env_bool, _env_int


def cursor_sparse_samples_enabled() -> bool:
    return _env_bool("SIM_CURSOR_USAGE_SPARSE_SAMPLES", True)


def cursor_sample_hours_utc() -> tuple[int, ...]:
    raw = os.environ.get("SIM_CURSOR_USAGE_SAMPLE_HOURS_UTC", "11,23").strip()
    if not raw:
        return (11, 23)
    hours: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            hours.append(int(part) % 24)
        except ValueError:
            continue
    return tuple(hours) if hours else (11, 23)


def cursor_sample_window_sec() -> int:
    """Half-open window length after each sample hour (must cover ≥1 scrape)."""
    return max(30, min(600, _env_int("SIM_CURSOR_USAGE_SAMPLE_WINDOW_SEC", 90)))


def cursor_in_sample_window(now: datetime | None = None) -> bool:
    """True when Cursor Usage metrics should appear on /metrics."""
    if not cursor_sparse_samples_enabled():
        return True
    if _env_bool("SIM_CURSOR_USAGE_SAMPLE_ALWAYS", False):
        return True
    ts = now or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    else:
        ts = ts.astimezone(timezone.utc)
    window = cursor_sample_window_sec()
    seconds_since_midnight = ts.hour * 3600 + ts.minute * 60 + ts.second
    for hour in cursor_sample_hours_utc():
        start = hour * 3600
        if start <= seconds_since_midnight < start + window:
            return True
    return False
