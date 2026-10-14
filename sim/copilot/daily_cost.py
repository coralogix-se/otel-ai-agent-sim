"""Per-user daily Copilot cost accrual.

Real GitHub Copilot billing-style ``github.copilot.cost`` / ``nano_aiu`` values are
effectively daily rollups. The sim used to stamp API-equivalent cost on every ``chat``
and ``invoke_agent`` span (~thousands/day); dashboards that ``sum()`` those tags then
show millions of dollars.

This module:
- Accrues realistic API-equivalent USD from each session's actual token usage
- Allows **one** cost emit per user per UTC day (the accrued total so far that day)
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sim.common.env import _env_bool, _env_float, _env_int

_lock = threading.Lock()


@dataclass
class _UserDayCost:
    day: str  # YYYY-MM-DD UTC
    accrued_usd: float = 0.0
    sessions: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    first_session_mono: float = 0.0
    emitted: bool = False


_by_user: dict[str, _UserDayCost] = {}


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _bucket(user_key: str) -> _UserDayCost:
    day = _utc_day()
    cur = _by_user.get(user_key)
    if cur is None or cur.day != day:
        cur = _UserDayCost(day=day)
        _by_user[user_key] = cur
    return cur


def copilot_cost_once_per_day_enabled() -> bool:
    """When true (default), span/billing cost tags emit at most once per user per UTC day."""
    return _env_bool("SIM_COPILOT_COST_ONCE_PER_DAY", True)


def accrue_copilot_session_cost(
    user_key: str,
    cost_usd: float,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> None:
    """Add this session's API-equivalent cost to today's per-user bucket."""
    key = (user_key or "unknown").strip() or "unknown"
    with _lock:
        b = _bucket(key)
        if b.sessions == 0:
            b.first_session_mono = time.monotonic()
        b.accrued_usd += max(0.0, float(cost_usd))
        b.sessions += 1
        b.input_tokens += max(0, int(input_tokens))
        b.output_tokens += max(0, int(output_tokens))
        b.cache_read_tokens += max(0, int(cache_read_tokens))


@dataclass(frozen=True, slots=True)
class CopilotDailyCostEmit:
    """Payload for a once-per-day cost stamp on spans / billing metrics."""

    cost_usd: float
    sessions: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    day: str


def take_copilot_daily_cost_emit(user_key: str) -> CopilotDailyCostEmit | None:
    """
    Return today's accrued cost once per user per UTC day when ready.

    Ready when accrued > 0 and either:
    - ``sessions >= SIM_COPILOT_COST_EMIT_AFTER_SESSIONS`` (default 8), or
    - seconds since first session today >= ``SIM_COPILOT_COST_EMIT_AFTER_SEC`` (default 14400)

    Thresholds are intentionally late so the stamp reflects most of that user's
    accrued API-equivalent usage for the day (not just the first few sessions).

    Returns ``None`` if already emitted today, not ready yet, or once-per-day mode is off
    (caller should fall back to per-session cost).
    """
    if not copilot_cost_once_per_day_enabled():
        return None

    key = (user_key or "unknown").strip() or "unknown"
    min_sessions = max(1, _env_int("SIM_COPILOT_COST_EMIT_AFTER_SESSIONS", 8))
    after_sec = max(0.0, _env_float("SIM_COPILOT_COST_EMIT_AFTER_SEC", 14400.0))

    with _lock:
        b = _bucket(key)
        if b.emitted or b.accrued_usd <= 0.0:
            return None
        age = time.monotonic() - b.first_session_mono if b.first_session_mono else 0.0
        ready = b.sessions >= min_sessions or (after_sec > 0 and age >= after_sec)
        if not ready:
            return None
        b.emitted = True
        return CopilotDailyCostEmit(
            cost_usd=round(b.accrued_usd, 8),
            sessions=b.sessions,
            input_tokens=b.input_tokens,
            output_tokens=b.output_tokens,
            cache_read_tokens=b.cache_read_tokens,
            day=b.day,
        )
