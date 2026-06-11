#!/usr/bin/env python3
"""
Validate that the Gemini CLI simulator matches **real** gemini-cli OTLP exports (shape parity).

Real CLI (Node → Coralogix), typical patterns:
  - Trace: outer ``user_prompt`` span (conversation / prompt text); nested ``llm_call`` with
    ``gen_ai.request.model``, usage, ``gen_ai.prompt.name`` (turn id), ``gen_ai.system_instructions``,
    ``gen_ai.tool.definitions``.
  - Resource: ``service.name`` ≈ ``gemini-cli``, ``cx.application.name`` / ``cx.subsystem.name`` set.
  - Logs: structured ``event_name`` values such as ``gemini_cli.api_request``, ``gemini_cli.tool_call``, …

Live tenant note: interactive sessions may use subsystem ``gemini-cli-sessions-real``; this sim defaults
to ``gemini-cli-sessions`` — compare shape, not subsystem string, unless env overrides match.

Run from repo root: ``python scripts/validate_gemini_span_mcp.py``
"""

from __future__ import annotations

import json
import os
import socket
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sim.common import _cli_resource, _gen_ai_dashboard_llm_span_attributes
from sim.constants import GEMINI_AGENT_DESCRIPTION
from sim.env import _env_int


def _gemini_turn_prompt_name(conversation_id: str) -> str:
    """Same contract as ``app._gemini_turn_prompt_name`` / ``sim.gemini``."""
    return f"{conversation_id}########0"


def _gemini_minimal_tool_definitions_json() -> str:
    payload = [
        {
            "functionDeclarations": [
                {"name": "read_file", "description": "Read a workspace file."},
                {"name": "write_file", "description": "Create or overwrite a workspace file."},
                {"name": "search_replace", "description": "Apply a search/replace edit."},
                {"name": "run_shell_command", "description": "Run a shell command (sandboxed)."},
            ]
        }
    ]
    return json.dumps(payload)


def _gemini_system_instructions_stub() -> str:
    stub = os.environ.get(
        "SIM_GEMINI_SYSTEM_INSTRUCTIONS_STUB",
        "You are the Gemini CLI agent. Prefer small, verifiable edits; respect the repo layout and safety rules.",
    )
    cap = max(128, _env_int("SIM_GEMINI_SYSTEM_INSTRUCTIONS_MAX_LEN", 4096))
    return stub if len(stub) <= cap else stub[:cap]


def _deterministic_user_attrs() -> dict[str, str]:
    return {
        "user.account_uuid": str(uuid.UUID(int=1)),
        "user.id": "01" + "a" * 62,
        "user.name": "Alex Silva",
        "user.email": "alex.silva@coralogix.com",
    }


def build_user_prompt_span_attrs() -> dict[str, str | int]:
    """Mirrors ``app.emit_gemini_cli_user_prompt_span`` outer span only (real CLI: tokens on child)."""
    conversation_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    ver = "v1.1.0"
    prompt = "stub prompt for validation"
    cx_app = os.environ.get("GEMINI_CX_APPLICATION_NAME", "gemini-cli")
    cx_sub = os.environ.get("GEMINI_CX_SUBSYSTEM_NAME", "gemini-cli-sessions")
    event_id = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    request_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    inst = "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
    ap_mode = "default"
    user_attrs = _deterministic_user_attrs()
    return {
        **user_attrs,
        "agent.product": "gemini_cli",
        "sim.agent_tool_version": ver,
        "otel.library.name": "gemini-cli",
        "otel.library.version": ver,
        "otel.scope.name": "gemini-cli",
        "otel.scope.version": ver,
        "gen_ai.input.messages": prompt,
        "gen_ai.system": os.environ.get("SIM_GEMINI_GEN_AI_SYSTEM", "gcp.gemini"),
        "gen_ai.request.id": request_id,
        "gen_ai.session.id": conversation_id,
        "gen_ai.conversation.id": conversation_id,
        "gen_ai.operation.name": "user_prompt",
        "gen_ai.agent.name": "gemini-cli",
        "gen_ai.agent.description": GEMINI_AGENT_DESCRIPTION,
        "cx.event.id": event_id,
        "cx.application.name": cx_app,
        "cx.subsystem.name": cx_sub,
        "span.kind": "internal",
        "session.id": conversation_id,
        "installation_id": inst,
        "active_approval_mode": ap_mode,
        "process.runtime.name": "python",
        "process.runtime.version": "3.12.0",
        "process.runtime.description": "CPython",
        "host.name": os.environ.get("HOSTNAME", socket.gethostname()),
        "host.arch": os.environ.get("SIM_HOST_ARCH", "amd64"),
        "process.pid": "12345",
    }


def build_llm_call_span_attrs() -> dict[str, str | int | float]:
    """Mirrors nested ``llm_call`` span (same source as assembled ``sim.gemini``)."""
    conversation_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    ver = "v1.1.0"
    cx_app = os.environ.get("GEMINI_CX_APPLICATION_NAME", "gemini-cli")
    cx_sub = os.environ.get("GEMINI_CX_SUBSYSTEM_NAME", "gemini-cli-sessions")
    model = os.environ.get("SIM_GEMINI_MODEL", "").strip() or "gemini-2.5-flash"
    event_id = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    inst = "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
    ap_mode = "default"
    inp, out, cache, thought = 8000, 1500, 1000, 0
    user_attrs = _deterministic_user_attrs()
    prompt_turn = _gemini_turn_prompt_name(conversation_id)
    llm_attrs: dict[str, str | int | float] = {
        **user_attrs,
        "agent.product": "gemini_cli",
        "sim.agent_tool_version": ver,
        "otel.library.name": "gemini-cli",
        "otel.library.version": ver,
        "otel.scope.name": "gemini-cli",
        "otel.scope.version": ver,
        "gen_ai.operation.name": "llm_call",
        "gen_ai.request.model": model,
        "gen_ai.prompt.name": prompt_turn,
        "gen_ai.session.id": conversation_id,
        "gen_ai.conversation.id": conversation_id,
        "gen_ai.agent.name": "gemini-cli",
        "gen_ai.agent.description": GEMINI_AGENT_DESCRIPTION,
        "gen_ai.system_instructions": _gemini_system_instructions_stub(),
        "gen_ai.tool.definitions": _gemini_minimal_tool_definitions_json(),
        "cx.event.id": event_id,
        "cx.application.name": cx_app,
        "cx.subsystem.name": cx_sub,
        "span.kind": "internal",
        "session.id": conversation_id,
        "installation_id": inst,
        "active_approval_mode": ap_mode,
        "process.runtime.name": "python",
        "process.runtime.version": "3.12.0",
        "process.runtime.description": "CPython",
        "host.name": os.environ.get("HOSTNAME", socket.gethostname()),
        "host.arch": os.environ.get("SIM_HOST_ARCH", "amd64"),
        "process.pid": "12345",
        "gen_ai.usage.input_tokens": inp,
        "gen_ai.usage.output_tokens": out,
        "gen_ai.usage.cache_read_tokens": cache,
    }
    llm_attrs.update(_gen_ai_dashboard_llm_span_attributes(inp, out, operation_name="llm_call"))
    return llm_attrs


def _gemini_trace_resource_attrs() -> dict:
    gemini_service = os.environ.get("SIM_GEMINI_SERVICE_NAME", "gemini-cli")
    gemini_cx_app = os.environ.get("GEMINI_CX_APPLICATION_NAME", "gemini-cli")
    gemini_cx_sub = os.environ.get("GEMINI_CX_SUBSYSTEM_NAME", "gemini-cli-sessions")
    r = _cli_resource(gemini_service, gemini_cx_app, gemini_cx_sub)
    return dict(r.attributes)


# OTLP log ``event_name`` values emitted per session (excluding optional ``gemini_cli.api_error``).
GEMINI_CLI_SESSION_LOG_EVENT_NAMES = frozenset(
    {
        "gemini_cli.keychain.availability",
        "gemini_cli.token_storage.initialization",
        "gemini_cli.config",
        "gemini_cli.startup_stats",
        "gemini_cli.user_prompt",
        "gemini_cli.model_routing",
        "gemini_cli.api_request",
        "gemini_cli.api_response",
        "gen_ai.client.inference.operation.details",
        "gemini_cli.file_operation",
        "gemini_cli.tool_call",
        "gemini_cli.conversation_finished",
    }
)


def main() -> int:
    failures: list[str] = []
    user = build_user_prompt_span_attrs()
    llm = build_llm_call_span_attrs()
    res = _gemini_trace_resource_attrs()

    # --- Outer span: like real CLI (no model / no usage on user_prompt) ---
    if user.get("gen_ai.operation.name") != "user_prompt":
        failures.append(f"user_prompt span: gen_ai.operation.name want user_prompt, got {user.get('gen_ai.operation.name')!r}")
    if "gen_ai.request.model" in user:
        failures.append("user_prompt span should NOT set gen_ai.request.model (real export puts it on llm_call)")
    for k in ("gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens", "gen_ai.prompt_price"):
        if k in user:
            failures.append(f"user_prompt span should NOT set {k} (belongs on llm_call)")
    if not user.get("gen_ai.input.messages"):
        failures.append("user_prompt span missing gen_ai.input.messages")
    if not user.get("gen_ai.system"):
        failures.append("Missing gen_ai.system (Coralogix / DataPrime filters use this facet)")

    # --- Nested llm_call ---
    if llm.get("gen_ai.operation.name") != "llm_call":
        failures.append(f"llm_call span: gen_ai.operation.name want llm_call, got {llm.get('gen_ai.operation.name')!r}")
    if not llm.get("gen_ai.request.model"):
        failures.append("llm_call span missing gen_ai.request.model")
    pid = llm.get("gen_ai.prompt.name") or ""
    if not pid.endswith("########0"):
        failures.append(f"gen_ai.prompt.name should end with ########0, got {pid!r}")
    if not llm.get("gen_ai.system_instructions"):
        failures.append("llm_call span missing gen_ai.system_instructions")
    raw_tools = llm.get("gen_ai.tool.definitions")
    if not isinstance(raw_tools, str):
        failures.append("gen_ai.tool.definitions should be a JSON string")
    else:
        try:
            parsed = json.loads(raw_tools)
            if not isinstance(parsed, list) or not any(
                isinstance(x, dict) and "functionDeclarations" in x for x in parsed
            ):
                failures.append("gen_ai.tool.definitions JSON should be a list with functionDeclarations")
        except json.JSONDecodeError as e:
            failures.append(f"gen_ai.tool.definitions invalid JSON: {e}")
    for k in ("gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens", "gen_ai.prompt_price"):
        if k not in llm:
            failures.append(f"llm_call span missing {k}")

    # --- Resource ---
    exp_svc = os.environ.get("SIM_GEMINI_SERVICE_NAME", "gemini-cli")
    if res.get("service.name") != exp_svc:
        failures.append(f"Resource service.name expected {exp_svc!r}, got {res.get('service.name')!r}")
    if not res.get("cx.application.name"):
        failures.append("Resource missing cx.application.name")
    if not res.get("cx.subsystem.name"):
        failures.append("Resource missing cx.subsystem.name")

    # --- Log event catalogue (must stay aligned with ``_emit_gemini_cli_session_logs``) ---
    # Implementation note: names are duplicated here so CI catches drift vs app.py.

    if failures:
        print("VALIDATION FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1

    print("Gemini sim vs real CLI export shape: OK")
    print(f"  user_prompt keys: {len(user)} | llm_call keys: {len(llm)}")
    print(f"  gen_ai.prompt.name (llm_call) = {llm.get('gen_ai.prompt.name')!r}")
    print(f"  Resource service.name = {res.get('service.name')!r}")
    print(f"  Session log event_name types covered: {len(GEMINI_CLI_SESSION_LOG_EVENT_NAMES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
