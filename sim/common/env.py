"""Environment helpers (shared across all simulators)."""

import os


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _env_csv_model_pool(name: str, default_pool: tuple[str, ...]) -> tuple[str, ...]:
    """
    Comma-separated model ids (e.g. ``SIM_CODEX_MODELS=gpt-5.4,codex-mini-latest``).
    Empty / unset → ``default_pool``.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default_pool
    parts = tuple(p.strip() for p in raw.split(",") if p.strip())
    return parts if parts else default_pool


def claude_long_session_slots_enabled() -> bool:
    """True when parallel Claude slot pins are active (fixed sec or min/max range)."""
    if _env_float("SIM_CLAUDE_LONG_SESSION_SEC", 0.0) > 0:
        return True
    lo_raw = os.environ.get("SIM_CLAUDE_LONG_SESSION_SEC_MIN", "").strip()
    hi_raw = os.environ.get("SIM_CLAUDE_LONG_SESSION_SEC_MAX", "").strip()
    return bool(lo_raw and hi_raw)
