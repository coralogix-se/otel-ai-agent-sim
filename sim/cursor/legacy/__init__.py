"""Frozen snapshot of the span-based Cursor Composer sim (pre Usage-dashboard ``cursor_*`` metrics).

Live code still lives in ``sim.cursor.agent`` and is the default emit path.
This package is a **backup copy** so Usage-v2 work can proceed without rewriting away
the OTLP ``cursor-agent`` / ``cursor-coralogix`` hook shape.

Do not import this package from ``app.py`` unless deliberately A/B testing a restored
snapshot. Prefer ``from sim.cursor.agent import emit_cursor_composer_session``.
"""

from sim.cursor.legacy.agent import (
    _cursor_roster_user_for_emit,
    _cursor_stable_session_id_from_roster_user,
    emit_cursor_composer_session,
)

__all__ = [
    "_cursor_roster_user_for_emit",
    "_cursor_stable_session_id_from_roster_user",
    "emit_cursor_composer_session",
]
