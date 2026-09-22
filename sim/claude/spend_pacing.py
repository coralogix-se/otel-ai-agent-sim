"""Pace Claude Code top spenders toward a daily USD target (demo-friendly)."""

from __future__ import annotations

from datetime import datetime, timezone

from sim.common.env import _env_float
from sim.common.repos import is_sim_top_spender, sim_top_spender_rank


# email -> (utc_day, accrued_usd)
_accrued_usd: dict[str, tuple[str, float]] = {}


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _elapsed_day_fraction() -> float:
    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return max(0.02, min(1.0, (now - midnight).total_seconds() / 86_400.0))


def claude_top_spender_daily_usd_target() -> float:
    return max(0.0, _env_float("SIM_CLAUDE_TOP_SPENDER_DAILY_USD", 2000.0))


def note_claude_user_spend_usd(roster_user: dict | None, usd: float) -> None:
    """Accumulate estimated USD for top spenders (used by pace multiplier)."""
    if roster_user is None or not is_sim_top_spender(roster_user):
        return
    email = str(roster_user.get("user.email", "")).strip()
    if not email or usd <= 0:
        return
    day = _utc_day()
    prev = _accrued_usd.get(email)
    if prev is None or prev[0] != day:
        _accrued_usd[email] = (day, float(usd))
    else:
        _accrued_usd[email] = (day, prev[1] + float(usd))


def claude_user_accrued_spend_usd(roster_user: dict | None) -> float:
    if roster_user is None:
        return 0.0
    email = str(roster_user.get("user.email", "")).strip()
    prev = _accrued_usd.get(email)
    if prev is None or prev[0] != _utc_day():
        return 0.0
    return float(prev[1])


def sim_top_spender_pace_multiplier(roster_user: dict | None) -> float:
    """Scale tokens so each pinned top spender tracks ~``SIM_CLAUDE_TOP_SPENDER_DAILY_USD``.

    Rank 1 (highest) may overshoot slightly so they stay #1; ranks 2–3 stay near the target.
    """
    if not is_sim_top_spender(roster_user):
        return 1.0
    target = claude_top_spender_daily_usd_target()
    if target <= 0:
        return 1.0
    rank = sim_top_spender_rank(roster_user) or 2
    # Keep #1 a bit above #2/#3 so the unmanaged rogue is the clear top spender.
    rank_target = target * (1.08 if rank == 1 else 1.0)
    expected = rank_target * _elapsed_day_fraction()
    accrued = claude_user_accrued_spend_usd(roster_user)
    if accrued < 1.0:
        # Front-load a bit so demos see spend early in the day.
        return 2.2 if rank == 1 else 1.8
    ratio = expected / max(accrued, 0.01)
    return max(0.12, min(3.5, ratio))
