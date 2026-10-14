"""Once-per-day Prometheus sample exposure for Copilot collector (brief §7 / cxai)."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from sim.common.env import _env_bool, _env_int


def copilot_sparse_samples_enabled() -> bool:
    return _env_bool("SIM_COPILOT_SPARSE_SAMPLES", True)


def copilot_sample_hour_utc() -> int:
    return int(os.environ.get("SIM_COPILOT_SAMPLE_HOUR_UTC", "20").strip() or "20") % 24


def copilot_sample_window_sec() -> int:
    return max(30, min(600, _env_int("SIM_COPILOT_SAMPLE_WINDOW_SEC", 90)))


def copilot_in_sample_window(now: datetime | None = None) -> bool:
    if not copilot_sparse_samples_enabled():
        return True
    if _env_bool("SIM_COPILOT_SAMPLE_ALWAYS", False):
        return True
    ts = now or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    else:
        ts = ts.astimezone(timezone.utc)
    start = copilot_sample_hour_utc() * 3600
    seconds = ts.hour * 3600 + ts.minute * 60 + ts.second
    return start <= seconds < start + copilot_sample_window_sec()
