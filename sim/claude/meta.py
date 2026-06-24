"""Claude Code telemetry profile: ``flat``, ``dotted``, or ``both`` (dual OTLP log pipelines)."""

from __future__ import annotations

import hashlib
import os


def _claude_telemetry_profile() -> str:
    """
    ``flat`` (default): EU2-style exports (snake_case log attrs, string token/cost fields,
    ``com.anthropic.claude_code.events`` scope, subsystem from ``CLAUDE_CODE_CX_SUBSYSTEM_NAME``).

    ``dotted``: dotted keys (``event.*`` / ``session.id``), numeric token/cost,
    ``com.anthropic.claude_code`` scope, subsystem from ``SIM_CLAUDE_DOTTED_CX_SUBSYSTEM_NAME``.

    ``both`` / ``dual`` / ``all``: fleet emits **flat and dotted** pipelines, but each session is
    routed to exactly one flavor (stable ~50/50 split via ``_claude_resolved_telemetry_profile``).
    """
    raw = os.environ.get("SIM_CLAUDE_TELEMETRY_PROFILE", "").strip().lower()
    if not raw:
        raw = "flat"
    if raw in ("both", "dual", "all"):
        return "both"
    if raw == "dotted":
        return "dotted"
    if raw == "flat":
        return "flat"
    return "flat"


def _claude_effective_cx_subsystem() -> str:
    """Subsystem on Claude **traces** and single-profile metrics; ``both`` keeps flat for trace Resource."""
    p = _claude_telemetry_profile()
    flat_sub = os.environ.get("CLAUDE_CODE_CX_SUBSYSTEM_NAME", "claude-code").strip() or "claude-code"
    dotted_sub = os.environ.get("SIM_CLAUDE_DOTTED_CX_SUBSYSTEM_NAME", "claude-code-sessions").strip() or "claude-code-sessions"
    if p == "dotted":
        return dotted_sub
    return flat_sub


def _claude_session_telemetry_flavor(session_id: str) -> str:
    """When profile is ``both``, assign each session to ``flat`` or ``dotted`` (stable ~50/50 split)."""
    key = session_id.strip() or "unknown-session"
    if int(hashlib.sha256(key.encode()).hexdigest(), 16) % 2:
        return "dotted"
    return "flat"


def _claude_resolved_telemetry_profile(session_id: str) -> str:
    """``flat`` / ``dotted`` profile for a session; ``both`` resolves to one flavor per session."""
    p = _claude_telemetry_profile()
    if p == "both":
        return _claude_session_telemetry_flavor(session_id)
    return p
