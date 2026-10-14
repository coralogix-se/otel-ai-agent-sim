"""Cursor Usage dashboard metrics (Admin API ``cursor_*`` family) — Usage v2.

Disabled by default. Enable with ``SIM_CURSOR_USAGE_METRICS_ENABLED=true``.
Does not replace the OTLP Composer span path in ``sim.cursor.agent``.
"""

from __future__ import annotations

from sim.common.env import _env_bool


def usage_metrics_enabled() -> bool:
    return _env_bool("SIM_CURSOR_USAGE_METRICS_ENABLED", False)


def register_cursor_usage_metrics(registry) -> object | None:
    if not usage_metrics_enabled():
        return None
    from sim.cursor.usage_v2.collector import register_cursor_usage_metrics as _register

    return _register(registry)


def emit_cursor_usage_metrics_cycle() -> None:
    """Accrue Usage-v2 deltas when enabled and collector is registered."""
    if not usage_metrics_enabled():
        return
    from sim.cursor.usage_v2.runtime import emit_cursor_usage_cycle

    emit_cursor_usage_cycle()


__all__ = [
    "usage_metrics_enabled",
    "register_cursor_usage_metrics",
    "emit_cursor_usage_metrics_cycle",
]
