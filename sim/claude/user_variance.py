"""Stable per-roster-user variance for Claude Code sim (activity, tokens, productivity, sessions)."""
from __future__ import annotations

import hashlib
import random
from typing import TypedDict


class _ClaudeUserVariance(TypedDict):
    activity: float
    batch_max: int
    token_mult: float
    productivity_mult: float
    session_rotate_mult: float
    session_phase_s: float


_cache: dict[str, _ClaudeUserVariance] = {}


def _user_key(roster_user: dict | None) -> str:
    if roster_user is None:
        return "unknown-user"
    acc = str(roster_user.get("user.account_uuid", "")).strip()
    uid = str(roster_user.get("user.id", "")).strip()
    return acc or uid or "unknown-user"


def _bytes_for_user(roster_user: dict | None) -> bytes:
    return hashlib.sha256(b"otel-ai-agent-sim:claude:user-variance:" + _user_key(roster_user).encode()).digest()


def claude_user_variance(roster_user: dict | None) -> _ClaudeUserVariance:
    """Deterministic per-user knobs (stable across restarts for the same roster row)."""
    key = _user_key(roster_user)
    got = _cache.get(key)
    if got is not None:
        return got
    b = _bytes_for_user(roster_user)
    activity = 0.18 + (b[0] / 255.0) * 0.82
    batch_max = 1 + (b[1] % 5)
    token_mult = 0.22 + (int.from_bytes(b[2:6], "big") / 2**32) * 2.78
    productivity_mult = 0.15 + (int.from_bytes(b[6:10], "big") / 2**32) * 2.85
    session_rotate_mult = 0.45 + (b[10] / 255.0) * 1.85
    session_phase_s = (int.from_bytes(b[11:15], "big") / 2**32) * 1800.0
    got = _ClaudeUserVariance(
        activity=activity,
        batch_max=batch_max,
        token_mult=token_mult,
        productivity_mult=productivity_mult,
        session_rotate_mult=session_rotate_mult,
        session_phase_s=session_phase_s,
    )
    _cache[key] = got
    return got


def claude_user_should_emit_this_cycle(roster_user: dict | None) -> bool:
    """Some users skip a Claude cycle (light / intermittent usage)."""
    if roster_user is None:
        return True
    return random.random() < claude_user_variance(roster_user)["activity"]


def claude_user_emit_turns_this_cycle(roster_user: dict | None, *, default_batch: int = 1) -> int:
    """How many turns to emit for this user this cycle (0 if idle)."""
    if roster_user is None:
        return max(1, default_batch)
    v = claude_user_variance(roster_user)
    hi = max(default_batch, v["batch_max"])
    return random.randint(1, hi)


def claude_user_token_multiplier(roster_user: dict | None) -> float:
    if roster_user is None:
        return 1.0
    return claude_user_variance(roster_user)["token_mult"]


def claude_user_productivity_multiplier(roster_user: dict | None) -> float:
    """Scales lines-of-code, commits, PRs, edit decisions, active time."""
    if roster_user is None:
        return 1.0
    return claude_user_variance(roster_user)["productivity_mult"]


def claude_user_session_rotate_duration(base_sec: float, roster_user: dict | None) -> float:
    """Per-user session window length (desyncs session counts across users)."""
    if base_sec <= 0 or roster_user is None:
        return base_sec
    v = claude_user_variance(roster_user)
    return max(60.0, base_sec * v["session_rotate_mult"])


def claude_user_session_phase_offset(roster_user: dict | None) -> float:
    """Stagger first session deadline so rotations don't align on the same cycles."""
    if roster_user is None:
        return 0.0
    return claude_user_variance(roster_user)["session_phase_s"]


def claude_user_session_rotate_duration_from_env(roster_user: dict | None) -> float:
    """Draw base rotate from env min/max or fixed, then apply per-user scale."""
    from sim.common.env import _env_bool, _env_float
    import os

    if not _env_bool("SIM_CLAUDE_STABLE_SESSION_PER_USER", True):
        return 0.0
    lo_raw = os.environ.get("SIM_CLAUDE_SESSION_ID_ROTATE_SEC_MIN", "").strip()
    hi_raw = os.environ.get("SIM_CLAUDE_SESSION_ID_ROTATE_SEC_MAX", "").strip()
    if lo_raw and hi_raw:
        lo = max(1.0, _env_float("SIM_CLAUDE_SESSION_ID_ROTATE_SEC_MIN", lo_raw))
        hi = max(lo, _env_float("SIM_CLAUDE_SESSION_ID_ROTATE_SEC_MAX", hi_raw))
        base = random.uniform(lo, hi)
    else:
        base = max(0.0, _env_float("SIM_CLAUDE_SESSION_ID_ROTATE_SEC", 3600.0))
    return claude_user_session_rotate_duration(base, roster_user)


def claude_user_long_session_pin_base_sec() -> float:
    """Base roster-user pin window from ``SIM_CLAUDE_LONG_SESSION_SEC`` or optional min/max range."""
    from sim.common.env import _env_float
    import os

    lo_raw = os.environ.get("SIM_CLAUDE_LONG_SESSION_SEC_MIN", "").strip()
    hi_raw = os.environ.get("SIM_CLAUDE_LONG_SESSION_SEC_MAX", "").strip()
    if lo_raw and hi_raw:
        lo = max(60.0, _env_float("SIM_CLAUDE_LONG_SESSION_SEC_MIN", lo_raw))
        hi = max(lo, _env_float("SIM_CLAUDE_LONG_SESSION_SEC_MAX", hi_raw))
        return random.uniform(lo, hi)
    return max(0.0, _env_float("SIM_CLAUDE_LONG_SESSION_SEC", 0.0))


def claude_user_long_session_pin_sec(roster_user: dict | None) -> float:
    """Per-user slot pin duration (scaled + optional env min/max draw)."""
    base = claude_user_long_session_pin_base_sec()
    if base <= 0:
        return 0.0
    return claude_user_session_rotate_duration(base, roster_user)


def claude_slot_pin_deadline(
    now: float,
    *,
    slot_index: int,
    n_slots: int,
    roster_user: dict | None,
    initial: bool,
) -> float:
    """
    Monotonic deadline for a Claude slot user pin.

    ``initial=True`` spreads first expiries across the pin window so pod start does not
    align all slots. Later rotations add per-user jitter so the fleet stays desynced.
    """
    pin = claude_user_long_session_pin_sec(roster_user)
    if pin <= 0:
        return now
    phase = claude_user_session_phase_offset(roster_user) if roster_user is not None else 0.0
    if initial:
        frac = (slot_index + random.uniform(0.15, 0.95)) / max(n_slots, 1)
        spread = max(90.0, pin * max(0.1, frac) + phase * 0.35)
        return now + spread
    jitter = random.uniform(-0.2 * pin, 0.2 * pin) + phase * 0.08
    return now + max(90.0, pin + jitter)


def claude_session_id_rotate_deadline(now: float, rotate_sec: float) -> float:
    """Monotonic deadline for the next ``session.id`` rotation (jittered, not fleet-aligned)."""
    if rotate_sec <= 0:
        return now
    jitter = random.uniform(-0.15 * rotate_sec, 0.25 * rotate_sec)
    return now + max(60.0, rotate_sec + jitter)
