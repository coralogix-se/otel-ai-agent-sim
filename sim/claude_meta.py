"""Claude Code telemetry profile: ``flat``, ``dotted``, or ``both`` (dual OTLP log pipelines)."""

import os


def _claude_telemetry_profile() -> str:
    """
    ``flat`` (default): EU2-style exports (snake_case log attrs, string token/cost fields,
    ``com.anthropic.claude_code.events`` scope, subsystem from ``CLAUDE_CODE_CX_SUBSYSTEM_NAME``).

    ``dotted``: dotted keys (``event.*`` / ``session.id``), numeric token/cost,
    ``com.anthropic.claude_code`` scope, subsystem from ``SIM_CLAUDE_DOTTED_CX_SUBSYSTEM_NAME``.

    ``both`` / ``dual`` / ``all``: emit **flat and dotted** OTLP log pipelines and duplicate Prometheus
    counters with each subsystem's label set (``SIM_CLAUDE_TELEMETRY_PROFILE``).
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
