#!/usr/bin/env python3
"""One-shot helper to slice app.py into sim/<agent>/*.py (run from repo root)."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"
L = APP.read_text().splitlines(keepends=True)


def chunk(a: int, b: int) -> str:
    return "".join(L[a - 1 : b])


def sub_st(s: str) -> str:
    reps = [
        (r"\b_sim_cli\b", "st.sim_cli"),
        (r"\b_prom_registry\b", "st.prom_registry"),
        (r"\b_prom_gem_session\b", "st.prom_gem_session"),
        (r"\b_prom_gem_token\b", "st.prom_gem_token"),
        (r"\b_prom_gem_token_coralogix\b", "st.prom_gem_token_coralogix"),
        (r"\b_prom_gem_token_tokens\b", "st.prom_gem_token_tokens"),
        (r"\b_prom_gem_api\b", "st.prom_gem_api"),
        (r"\b_prom_gem_api_latency\b", "st.prom_gem_api_latency"),
        (r"\b_prom_gem_lines\b", "st.prom_gem_lines"),
        (r"\b_prom_gem_lines_coralogix\b", "st.prom_gem_lines_coralogix"),
        (r"\b_prom_gem_file_op\b", "st.prom_gem_file_op"),
        (r"\b_prom_gem_tool_call\b", "st.prom_gem_tool_call"),
        (r"\b_prom_gem_tool_latency\b", "st.prom_gem_tool_latency"),
        (r"\b_prom_gem_model_routing_latency\b", "st.prom_gem_model_routing_latency"),
        (r"\b_prom_gem_agent_duration\b", "st.prom_gem_agent_duration"),
        (r"\b_prom_gem_agent_run\b", "st.prom_gem_agent_run"),
        (r"\b_prom_codex_run_turn\b", "st.prom_codex_run_turn"),
        (r"\b_prom_codex_token\b", "st.prom_codex_token"),
        (r"\b_prom_rw_stop\b", "st.prom_rw_stop"),
        (r"\b_codex_log_provider\b", "st.codex_log_provider"),
        (r"\b_codex_otlp_logger\b", "st.codex_otlp_logger"),
        (r"\b_gemini_log_provider\b", "st.gemini_log_provider"),
        (r"\b_gemini_otlp_logger\b", "st.gemini_otlp_logger"),
        (r"\b_claude_dotted_log_provider\b", "st.claude_dotted_log_provider"),
        (r"\b_gem_metric_pins\b", "st.gem_metric_pins"),
        (r"\b_gem_session_models\b", "st.gem_session_models"),
        (r"\b_gem_slot_users\b", "st.gem_slot_users"),
        (r"\b_gem_slot_deadlines\b", "st.gem_slot_deadlines"),
        (r"\b_gem_slot_rr\b", "st.gem_slot_rr"),
        (r"\b_gem_loc_ema\b", "st.gem_loc_ema"),
        (r"\b_cc_roster_rr_idx\b", "st.cc_roster_rr_idx"),
        (r"\b_cc_long_session_id\b", "st.cc_long_session_id"),
        (r"\b_cc_long_session_deadline\b", "st.cc_long_session_deadline"),
        (r"\b_cc_slot_users\b", "st.cc_slot_users"),
        (r"\b_cc_slot_deadlines\b", "st.cc_slot_deadlines"),
        (r"\b_cc_slot_rr\b", "st.cc_slot_rr"),
        (r"\b_cc_metric_label_pins\b", "st.cc_metric_label_pins"),
    ]
    for pat, repl in reps:
        s = re.sub(pat, repl, s)
    out = []
    for line in s.splitlines(keepends=True):
        if line.lstrip().startswith("global "):
            continue
        out.append(line)
    return "".join(out)


def write(path: str, header: str, body: str) -> None:
    p = ROOT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(header + sub_st(body))
    print("wrote", path)


# --- identity.py: name lists, roster, Claude metric pin helpers (app.py 1749–1988) ---
IDENTITY_HEADER = '''"""Synthetic Coralogix roster identities (shared across Gemini, Claude, Codex, generic)."""
from __future__ import annotations

import hashlib
import os
import random
import time
import uuid

from sim.claude.meta import _claude_effective_cx_subsystem, _claude_telemetry_profile
from sim.common.otel import tool_version_for, _stable_uuid
from sim.common.constants import _CLAUDE_CODE_MODELS
from sim.common.env import _env_bool, _env_float, _env_int
from sim.common.state import st

'''

write(
    "sim/identity.py",
    IDENTITY_HEADER,
    chunk(1749, 1988),
)

# --- gemini/agent.py ---
GEMINI_HEADER = '''"""Gemini CLI simulator: spans, OTLP logs, Prometheus (standard label set)."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import logging
import os
import random
import socket
import sys
import time
import uuid
from datetime import datetime, timezone

from opentelemetry._logs.severity import SeverityNumber
from opentelemetry.sdk._logs import LogRecord
from opentelemetry.trace import Status, StatusCode, TraceFlags
from opentelemetry import trace

from sim.common.otel import _gen_ai_dashboard_llm_span_attributes, _stable_uuid, tool_version_for
from sim.common.constants import GEMINI_AGENT_DESCRIPTION, GEMINI_SAMPLE_PROMPTS
from sim.common.env import _env_bool, _env_float, _env_int
from sim.common.identity import random_coralogix_identity
from sim.common.state import st

log = logging.getLogger(__name__)

'''

gemini_body = (
    chunk(79, 261)
    + "\n"
    + chunk(869, 894)
    + "\n"
    + chunk(1060, 1663)
    + "\n"
    + chunk(1665, 1694)
    + "\n"
    + chunk(1991, 2487)
)
write("sim/gemini/agent.py", GEMINI_HEADER, gemini_body)

# --- codex/agent.py ---
CODEX_HEADER = '''"""OpenAI Codex CLI simulator (run_turn span + structured OTLP logs)."""
from __future__ import annotations

import os
import random
import socket
import sys
import time
import uuid

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from sim.common.otel import (
    _emit_codex_otlp_structured_log,
    _codex_span_service_label_attrs,
    _gen_ai_dashboard_llm_span_attributes,
    tool_version_for,
)
from sim.common.constants import CODEX_AGENT_DESCRIPTION, CODEX_SAMPLE_PROMPTS
from sim.common.env import _env_bool, _env_int
from sim.common.identity import random_coralogix_identity
from sim.common.state import st

'''

write(
    "sim/codex.py",
    CODEX_HEADER,
    chunk(2562, 2854),
)

# --- common/constants.py ---
Path(ROOT / "sim/common/constants.py").write_text(
    '"""Agent marketing descriptions, sample prompts, and model pools (shared)."""\n\n' + chunk(1696, 1746)
)

# --- generic/agent.py ---
GENERIC_HEADER = '''"""Generic multi-step agent workflow (non-CLI agents: ChatGPT, Copilot, Grok, …)."""
from __future__ import annotations

import random
import time
import uuid

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from sim.common.otel import _gen_ai_dashboard_llm_span_attributes, tool_version_for
from sim.common.identity import random_coralogix_identity

'''

write("sim/generic/agent.py", GENERIC_HEADER, chunk(2857, 3001))

# --- claude/spans.py ---
CLAUDE_SPANS_HEADER = '''"""Claude Code OTLP trace spans (optional ``user_prompt``)."""
from __future__ import annotations

import os
import random
import socket
import sys
import time
import uuid

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from sim.claude.meta import _claude_effective_cx_subsystem
from sim.common.otel import _gen_ai_dashboard_llm_span_attributes, _sim_claude_usage_token_counts, tool_version_for
from sim.common.constants import CLAUDE_CODE_AGENT_DESCRIPTION, CLAUDE_CODE_SAMPLE_PROMPTS
from sim.common.identity import _claude_otlp_span_user_attrs_from_roster, random_claude_user_identity
from sim.common.state import st

'''

write("sim/claude/spans.py", CLAUDE_SPANS_HEADER, chunk(2489, 2560))

print("done.")
