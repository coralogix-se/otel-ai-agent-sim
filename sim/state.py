"""
Mutable runtime state set from ``app.main()`` (tracers, Prometheus series, OTLP loggers).

Using a single namespace avoids circular imports between agent modules and the entrypoint.
"""

from __future__ import annotations

import threading
from typing import Any

from prometheus_client import CollectorRegistry


class SimState:
    """Process-wide handles for OTLP + Prometheus (populated in ``main()``)."""

    sim_cli: Any = None
    prom_registry: CollectorRegistry | None = None
    prom_gem_session: Any = None
    prom_gem_token: Any = None
    prom_gem_token_coralogix: Any = None
    prom_gem_token_tokens: Any = None
    prom_gem_api: Any = None
    prom_gem_api_latency: Any = None
    prom_gem_lines: Any = None
    prom_gem_lines_coralogix: Any = None
    prom_gem_file_op: Any = None
    prom_gem_tool_call: Any = None
    prom_gem_tool_latency: Any = None
    prom_gem_model_routing_latency: Any = None
    prom_gem_agent_duration: Any = None
    prom_gem_agent_run: Any = None
    prom_codex_run_turn: Any = None
    prom_codex_token: Any = None
    prom_copilot_session: Any = None
    prom_copilot_token: Any = None
    prom_copilot_tool: Any = None
    prom_copilot_tool_dur: Any = None
    prom_copilot_chat_dur: Any = None
    prom_copilot_agent_dur: Any = None
    prom_copilot_ttft: Any = None
    prom_copilot_premium: Any = None
    prom_copilot_cache: Any = None
    prom_copilot_edit: Any = None
    prom_copilot_session_repo: Any = None
    copilot_collector: Any = None
    prom_rw_stop: threading.Event | None = None

    codex_log_provider: Any = None
    codex_otlp_logger: Any = None
    gemini_log_provider: Any = None
    gemini_otlp_logger: Any = None
    copilot_log_provider: Any = None
    copilot_otlp_logger: Any = None
    # Primary Claude LoggerProvider (``set_logger_provider``); dashboard ``force_flush`` target.
    claude_primary_log_provider: Any = None
    # Second provider when ``SIM_CLAUDE_TELEMETRY_PROFILE=both`` (dotted pipeline Resource).
    claude_dotted_log_provider: Any = None

    # Claude Code Prometheus counters (registered on ``prom_registry`` in main).
    cc_session: Any = None
    cc_token: Any = None
    cc_token_coralogix: Any = None
    cc_cost: Any = None
    cc_active: Any = None
    cc_loc: Any = None
    cc_commit: Any = None
    cc_pr: Any = None
    cc_edit_decision: Any = None
    cc_session_repo: Any = None

    # list[tuple[Logger, str, str]] — (logger, profile name, cx_subsystem)
    cc_log_emitters: list | None = None

    claude_cx_app: str | None = None
    claude_cx_sub_flat: str | None = None
    claude_cx_sub_dotted: str | None = None

    # Gemini sim mutable pins (per-process)
    gem_metric_pins: dict = {}
    gem_session_models: dict = {}
    gem_slot_users: list = []
    gem_slot_deadlines: list = []
    gem_slot_rr: int = 0
    gem_loc_ema: dict = {}

    # Cursor Composer: parallel long-session slots (see ``sim/cursor.py``).
    cursor_slot_users: list = []
    cursor_slot_deadlines: list = []
    cursor_slot_rr: int = 0

    # Claude roster / session (legacy globals from monolith)
    cc_roster_rr_idx: int = 0
    cc_long_session_id: str | None = None
    cc_long_session_deadline: float = 0.0
    cc_slot_users: list = []
    cc_slot_deadlines: list = []
    cc_slot_rr: int = 0
    cc_metric_label_pins: dict = {}
    # Per roster user: ``user_key -> (session_id, monotonic deadline)`` for rotating ``session.id``.
    cc_user_session_ids: dict = {}


st = SimState()
