"""Claude Code simulator (dashboard metrics, logs, spans, repo/user variance)."""

from __future__ import annotations

from typing import Any

__all__ = ["emit_claude_code_dashboard"]


def __getattr__(name: str) -> Any:
    if name == "emit_claude_code_dashboard":
        from sim.claude.dashboard import emit_claude_code_dashboard

        return emit_claude_code_dashboard
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
