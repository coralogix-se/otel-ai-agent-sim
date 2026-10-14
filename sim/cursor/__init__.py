"""Cursor IDE simulator.

Two emit paths (keep both):

- **Legacy spans** — ``sim.cursor.agent`` / ``emit_cursor_composer_session``
  (OTLP ``cursor-agent`` / ``cursor-coralogix``). Frozen copy: ``sim.cursor.legacy``.
- **Usage v2 metrics** — ``sim.cursor.usage_v2`` (Prometheus ``cursor_*`` for the
  Usage dashboard). Gated by ``SIM_CURSOR_USAGE_METRICS_ENABLED`` (default off).

See ``docs/cursor-usage-v2-plan.md``.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "_cursor_roster_user_for_emit",
    "_cursor_stable_session_id_from_roster_user",
    "emit_cursor_composer_session",
    "emit_cursor_usage_metrics_cycle",
    "usage_metrics_enabled",
]


def __getattr__(name: str) -> Any:
    if name in (
        "_cursor_roster_user_for_emit",
        "_cursor_stable_session_id_from_roster_user",
        "emit_cursor_composer_session",
    ):
        from sim.cursor import agent as _agent

        return getattr(_agent, name)
    if name in ("emit_cursor_usage_metrics_cycle", "usage_metrics_enabled"):
        from sim.cursor import usage_v2 as _usage_v2

        return getattr(_usage_v2, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
