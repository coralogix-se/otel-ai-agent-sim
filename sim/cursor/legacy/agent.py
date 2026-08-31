"""
Cursor IDE (Composer) simulator — OTLP traces aligned with the real ``cursor-coralogix`` hook
(flat ``cursor.*`` attributes, ``gen_ai.system`` = ``cursor``, library ``cursor-coralogix``, ``SERVER`` spans).
"""
from __future__ import annotations

import json
import os
import random
import socket
import sys
import time
import uuid

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from sim.common.otel import _gen_ai_dashboard_llm_span_attributes, tool_version_for
from sim.common.constants import CURSOR_COMPOSER_MODELS, CURSOR_SAMPLE_PROMPTS
from sim.common.env import _env_bool, _env_csv_model_pool, _env_float, _env_int
from sim.common.identity import (
    _claude_otlp_span_user_attrs_from_roster,
    random_coralogix_identity_for_agent,
    roster_core_user_for_agent,
    roster_indices_for_agent,
)
from sim.common.state import st


def _ct(key: str) -> str:
    """Real hook and AI Center expect ``cursor.<field>`` (not ``tags.cursor.*``)."""
    return f"cursor.{key}"


def _trunc(s: str, n: int) -> str:
    s = s.strip()
    return s if len(s) <= n else s[: n - 3] + "..."


def _cursor_stable_session_id_from_roster_user(user_attrs: dict) -> str:
    """Deterministic ``gen_ai.session.id`` / ``cursor.session_id`` root for a roster row (stable across restarts)."""
    acc = str(user_attrs.get("user.account_uuid", "")).strip()
    uid = str(user_attrs.get("user.id", "")).strip()
    key = acc or uid or "unknown-user"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "otel-ai-agent-sim:cursor:session-per-user:" + key))


def _cursor_roster_user_for_emit() -> dict:
    """
    Roster identity for Cursor Composer traces.

    When ``SIM_CURSOR_LONG_SESSION_SEC`` and ``SIM_CLAUDE_LONG_SESSION_SEC`` are both unset / zero:
    each emit draws a fresh roster user (short-lived identity).

    When the effective duration ``dur`` > 0 (``SIM_CURSOR_LONG_SESSION_SEC``, else ``SIM_CLAUDE_LONG_SESSION_SEC``):
    keep **up to** ``SIM_CURSOR_CONCURRENT_LONG_SESSIONS`` (default 18) slots; each pins one user for ``dur`` seconds.
    Slot selection follows ``SIM_CURSOR_SESSION_SLOT_STRATEGY`` (``random`` or ``round_robin``), matching Gemini/Claude slot pools.
    """
    dur = _env_float(
        "SIM_CURSOR_LONG_SESSION_SEC",
        _env_float("SIM_CLAUDE_LONG_SESSION_SEC", 0.0),
    )
    if dur <= 0:
        return roster_core_user_for_agent(str(uuid.uuid4()), "cursor")

    n_slots = max(1, _env_int("SIM_CURSOR_CONCURRENT_LONG_SESSIONS", 18))
    if len(st.cursor_slot_users) != n_slots:
        st.cursor_slot_users = [None] * n_slots
        st.cursor_slot_deadlines = [0.0] * n_slots
        st.cursor_slot_rr = 0
        if dur > 0:
            now = time.monotonic()
            allowed = roster_indices_for_agent("cursor")
            base = random.randrange(len(allowed))
            for j in range(n_slots):
                st.cursor_slot_users[j] = roster_core_user_for_agent(f"cursor-prefill:{j}", "cursor")
                st.cursor_slot_deadlines[j] = now + float(dur)

    strat = os.environ.get("SIM_CURSOR_SESSION_SLOT_STRATEGY", "random").strip().lower().replace("-", "_")
    if strat in ("round_robin", "rr"):
        i = st.cursor_slot_rr % n_slots
        st.cursor_slot_rr += 1
    else:
        i = random.randrange(n_slots)

    now = time.monotonic()
    if st.cursor_slot_users[i] is None or now >= st.cursor_slot_deadlines[i]:
        st.cursor_slot_users[i] = roster_core_user_for_agent(str(uuid.uuid4()) + f":cursor-slot:{i}", "cursor")
        st.cursor_slot_deadlines[i] = now + float(dur)
    return dict(st.cursor_slot_users[i])


def _cursor_model_for_session(profile: dict) -> str:
    """One model per trace: random from pool unless ``SIM_CURSOR_MODEL`` pins a single id."""
    pinned = os.environ.get("SIM_CURSOR_MODEL", "").strip()
    if pinned:
        return pinned
    pool = _env_csv_model_pool("SIM_CURSOR_MODELS", CURSOR_COMPOSER_MODELS)
    return random.choice(pool)


def _tool_use_id_value() -> str:
    """C4C samples include UUIDs and ``tool_<hex>``-style ids."""
    if random.random() < 0.55:
        return str(uuid.uuid4())
    return "tool_" + uuid.uuid4().hex[:12]


def _conversation_and_session_ids(session_param: str) -> tuple[str, str]:
    """
    ``cursor.session_id`` and ``cursor.conversation_id`` are sometimes equal,
    sometimes distinct (different composer thread vs shell session).
    """
    session_tag = session_param
    if random.random() < 0.62:
        conv_tag = session_param
    else:
        conv_tag = str(uuid.uuid4())
    return session_tag, conv_tag


def emit_cursor_composer_session(
    conversation_id: str,
    profile: dict,
    *,
    roster_user: dict | None = None,
) -> None:
    """
    Emit traces aligned with the real ``cursor-coralogix`` hook (flat ``cursor.*``, ``gen_ai.system=cursor``).

    Resource ``service.name`` / ``cx.*`` / SDK tags come from the Cursor ``TracerProvider`` in ``app.main``.
    """
    if st.sim_cli is None:
        raise RuntimeError("CLI trace providers not initialized")
    ver = tool_version_for("cursor")
    hook_scope = "cursor-coralogix"
    hook_lib_ver = os.environ.get("SIM_CURSOR_OTEL_LIBRARY_VERSION", "2.0.0").strip() or "2.0.0"
    cursor_tracer = st.sim_cli.cursor.get_tracer(hook_scope, hook_lib_ver)

    cx_app = os.environ.get("CURSOR_CX_APPLICATION_NAME", "cursor")
    cx_sub = os.environ.get("CURSOR_CX_SUBSYSTEM_NAME", "ai-agent")

    if roster_user is not None:
        user_attrs = _claude_otlp_span_user_attrs_from_roster(roster_user)
    else:
        user_attrs = random_coralogix_identity_for_agent(conversation_id, "cursor")
    user_email = user_attrs["user.email"]
    session_id, conv_id = _conversation_and_session_ids(conversation_id)

    composer_mode = random.choice(("agent", "chat"))
    cursor_version = os.environ.get("SIM_CURSOR_VERSION", ver)
    model = _cursor_model_for_session(profile)
    hook_gen_ai_system = "cursor"
    pin_model = os.environ.get("SIM_CURSOR_GEN_AI_REQUEST_MODEL", "").strip()
    # Hook often sends model ``default``; set SIM_CURSOR_GEN_AI_REQUEST_MODEL=default to pin that for dashboards.
    gen_ai_model = pin_model if pin_model else model
    prompt = random.choice(CURSOR_SAMPLE_PROMPTS)
    gen_id = str(uuid.uuid4())
    cwd = os.environ.get(
        "SIM_CURSOR_CWD",
        "/Users/dev/repos/otel-ai-agent-sim",
    )
    sandbox = os.environ.get("SIM_CURSOR_SANDBOX", "true").strip().lower() in ("1", "true", "yes")

    file_path = cwd.rstrip("/") + random.choice(
        ("/app/main.py", "/packages/core/src/index.ts", "/lib/agent.ts", "/README.md", "/cursor/jamf-deploy.sh")
    )

    inp = random.randint(400, 9000)
    out = random.randint(120, 6000)
    lines_added = random.randint(0, 48)
    lines_deleted = random.randint(0, 24)
    edit_count = random.randint(1, max(1, (lines_added + lines_deleted) // 3 + 1))
    final_ok = random.random() > 0.08
    final_status = "completed" if final_ok else "aborted"
    reason = "" if final_ok else "user_close"
    tool_use_id = _tool_use_id_value()
    loop_count = random.randint(0, 3)

    t0 = time.perf_counter()

    def _base_cursor_tags() -> dict[str, str | int]:
        return {
            _ct("session_id"): session_id,
            _ct("conversation_id"): conv_id,
            _ct("user_email"): user_email,
            _ct("cursor_version"): cursor_version,
            _ct("composer_mode"): composer_mode,
            _ct("cwd"): cwd,
        }

    def _lib_attrs() -> dict[str, str]:
        return {
            "otel.library.name": hook_scope,
            "otel.library.version": hook_lib_ver,
            "otel.scope.name": hook_scope,
            "otel.scope.version": hook_lib_ver,
        }

    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    with cursor_tracer.start_as_current_span(
        "cursor.sessionStart",
        kind=trace.SpanKind.SERVER,
    ) as root:
        root.set_status(Status(StatusCode.OK))
        root.set_attributes(
            {
                **user_attrs,
                "agent.product": "cursor",
                "sim.agent_tool_version": ver,
                "gen_ai.operation.name": "session_start",
                "gen_ai.system": hook_gen_ai_system,
                "gen_ai.request.model": gen_ai_model,
                "gen_ai.session.id": session_id,
                "gen_ai.conversation.id": conv_id,
                "cx.application.name": cx_app,
                "cx.subsystem.name": cx_sub,
                **_lib_attrs(),
                **_base_cursor_tags(),
                _ct("generation_id"): gen_id,
                _ct("sandbox"): str(sandbox).lower(),
                _ct("session_duration_ms"): int(0),
                _ct("duration_ms"): int(0),
                _ct("loop_count"): int(loop_count),
                _ct("prompt"): _trunc(prompt, 2000),
                _ct("prompt_length"): int(len(prompt)),
                _ct("lines_added"): int(lines_added),
                _ct("lines_deleted"): int(lines_deleted),
                _ct("lines_net"): int(lines_added - lines_deleted),
                _ct("edit_count"): int(edit_count),
                _ct("response_lines"): int(max(1, out // 80)),
                _ct("final_status"): "",
                _ct("status"): "running",
                "host.name": os.environ.get("HOSTNAME", socket.gethostname()),
                "host.arch": os.environ.get("SIM_HOST_ARCH", "amd64"),
                "process.runtime.name": "python",
                "process.runtime.description": "CPython",
                "process.runtime.version": py_ver,
                "process.pid": str(os.getpid()),
            }
        )

        # --- Prompt submission (hook name) ---
        with cursor_tracer.start_as_current_span(
            "cursor.beforeSubmitPrompt",
            kind=trace.SpanKind.SERVER,
        ) as sub:
            sub.set_status(Status(StatusCode.OK))
            sub.set_attributes(
                {
                    **user_attrs,
                    "gen_ai.operation.name": "chat",
                    "gen_ai.system": hook_gen_ai_system,
                    "gen_ai.request.model": gen_ai_model,
                    "cx.application.name": cx_app,
                    "cx.subsystem.name": cx_sub,
                    **_lib_attrs(),
                    **_base_cursor_tags(),
                    _ct("generation_id"): gen_id,
                    _ct("prompt"): _trunc(prompt, 1500),
                    _ct("prompt_length"): int(len(prompt)),
                }
            )
            time.sleep(random.uniform(0.01, 0.06))

        # --- Tool / shell (matches real hook: native file edits vs tool calls) ---
        # Real ``cursor.afterFileEdit`` from IDE users is usually **line-edit summary** only
        # (``cursor.lines_added`` / ``lines_deleted`` / ``lines_net`` / ``edit_count`` / ``file_path`` /
        # ``generation_id``) — no ``gen_ai.tool.*``. ``read_file`` / ``grep`` appear on ``cursor.postToolUse``.
        tool_roll = random.random()
        include_tool_use_id = True
        if tool_roll < 0.42:
            # Native file edit (same tag shape as real ``cursor-coralogix-hook`` user edits).
            span_nm = "cursor.afterFileEdit"
            include_tool_use_id = False
            extra = {
                _ct("generation_id"): gen_id,
                _ct("file_path"): file_path,
                _ct("lines_added"): int(lines_added),
                _ct("lines_deleted"): int(lines_deleted),
                _ct("lines_net"): int(lines_added - lines_deleted),
                _ct("edit_count"): int(edit_count),
            }
        elif tool_roll < 0.68:
            ti = json.dumps({"file_path": file_path})
            t_out = json.dumps({"file_path": file_path, "success": True})
            span_nm = "cursor.postToolUse"
            extra = {
                "gen_ai.operation.name": "tool_call",
                _ct("file_path"): file_path,
                _ct("tool_input"): ti,
                _ct("tool_output"): _trunc(t_out, 1500),
                "gen_ai.tool.name": "read_file",
                _ct("generation_id"): gen_id,
                _ct("lines_added"): int(max(0, lines_added)),
                _ct("lines_deleted"): int(max(0, lines_deleted)),
                _ct("lines_net"): int(lines_added - lines_deleted),
                _ct("edit_count"): int(edit_count),
            }
        elif tool_roll < 0.88:
            pat = random.choice(
                ("json_metadata|eval_report|RESULT_PATH", "error|Exception|Traceback", "def test_|async def ")
            )
            ti = json.dumps({"pattern": pat, "file_path": cwd + "/src"})
            t_out = json.dumps({"matches": random.randint(0, 24), "truncated": False})
            span_nm = "cursor.postToolUse"
            extra = {
                "gen_ai.operation.name": "tool_call",
                _ct("file_path"): file_path,
                _ct("tool_input"): ti,
                _ct("tool_output"): _trunc(t_out, 1200),
                "gen_ai.tool.name": "grep",
                _ct("generation_id"): gen_id,
            }
        else:
            shell_cmd = random.choice(
                (
                    "bash -n \"cursor/jamf-deploy.sh\"",
                    "pnpm -s test",
                    "python -m pytest -q",
                    "git diff --stat",
                )
            )
            shell_ok = random.random() > 0.12
            exit_code = 0 if shell_ok else 127
            out_txt = "All tests passed.\n" if shell_ok else ""
            t_out_json = json.dumps({"output": out_txt, "exitCode": exit_code})
            span_nm = "cursor.afterShellExecution"
            extra = {
                "gen_ai.operation.name": "shell_execution",
                _ct("shell_command"): shell_cmd,
                _ct("tool_input"): json.dumps({"command": shell_cmd, "cwd": cwd}),
                _ct("tool_output"): t_out_json if random.random() < 0.55 else _trunc(out_txt or "(eval):1: command not found: python\n", 1200),
                "gen_ai.tool.name": "run_terminal_cmd",
                _ct("generation_id"): gen_id,
            }

        mid_attrs = {
            **user_attrs,
            "gen_ai.system": hook_gen_ai_system,
            "gen_ai.request.model": gen_ai_model,
            "cx.application.name": cx_app,
            "cx.subsystem.name": cx_sub,
            **_lib_attrs(),
            **_base_cursor_tags(),
            **extra,
        }
        if include_tool_use_id:
            mid_attrs[_ct("tool_use_id")] = tool_use_id

        with cursor_tracer.start_as_current_span(
            span_nm,
            kind=trace.SpanKind.SERVER,
        ) as tsp:
            tsp.set_status(Status(StatusCode.OK))
            tsp.set_attributes(mid_attrs)
            time.sleep(random.uniform(0.02, 0.2))

        # --- Model completion / response text ---
        dur_ms = random.randint(220, 4200)
        resp_lines = max(1, out // 80)
        response_text = (
            "**Adding logging imports**\n\nI'm adding logging imports and module-level loggers to both test files."
            if final_ok
            else "Stopped after user dismissed composer."
        )
        with cursor_tracer.start_as_current_span(
            "cursor.afterAgentResponse",
            kind=trace.SpanKind.SERVER,
        ) as llm:
            llm.set_status(Status(StatusCode.OK))
            llm.set_attributes(
                {
                    **user_attrs,
                    **_gen_ai_dashboard_llm_span_attributes(
                        inp, out, operation_name="chat", model=gen_ai_model
                    ),
                    "gen_ai.system": hook_gen_ai_system,
                    "gen_ai.request.model": gen_ai_model,
                    "cx.application.name": cx_app,
                    "cx.subsystem.name": cx_sub,
                    **_lib_attrs(),
                    **_base_cursor_tags(),
                    _ct("generation_id"): gen_id,
                    _ct("prompt"): _trunc(prompt, 1200),
                    _ct("prompt_length"): int(len(prompt)),
                    _ct("duration_ms"): int(dur_ms),
                    _ct("response_lines"): int(resp_lines),
                    _ct("status"): "completed" if final_ok else "aborted",
                    _ct("text"): _trunc(response_text, 2500),
                }
            )
            time.sleep(random.uniform(0.05, 0.25))

        # --- Session end (hook name; attributes overlap root summary) ---
        wall_ms = max(1, int((time.perf_counter() - t0) * 1000))
        with cursor_tracer.start_as_current_span(
            "cursor.stop",
            kind=trace.SpanKind.SERVER,
        ) as stop:
            stop.set_status(Status(StatusCode.OK))
            stop.set_attributes(
                {
                    **user_attrs,
                    "gen_ai.operation.name": "stop",
                    "gen_ai.system": hook_gen_ai_system,
                    "gen_ai.request.model": gen_ai_model,
                    "cx.application.name": cx_app,
                    "cx.subsystem.name": cx_sub,
                    **_lib_attrs(),
                    **_base_cursor_tags(),
                    _ct("generation_id"): gen_id,
                    _ct("loop_count"): int(loop_count),
                    _ct("status"): final_status,
                    _ct("session_duration_ms"): wall_ms,
                    _ct("duration_ms"): wall_ms,
                }
            )
            time.sleep(random.uniform(0.01, 0.04))

        n_extra = max(0, _env_int("SIM_CURSOR_EXTRA_SPANS", 2))
        for i in range(n_extra):
            with cursor_tracer.start_as_current_span(
                f"cursor.postToolUse.{i}",
                kind=trace.SpanKind.SERVER,
            ) as ch:
                ch.set_status(Status(StatusCode.OK))
                ch.set_attributes(
                    {
                        **user_attrs,
                        "gen_ai.operation.name": "tool_call",
                        "gen_ai.system": hook_gen_ai_system,
                        "gen_ai.request.model": gen_ai_model,
                        "cx.application.name": cx_app,
                        "cx.subsystem.name": cx_sub,
                        **_lib_attrs(),
                        **_base_cursor_tags(),
                        _ct("tool_use_id"): _tool_use_id_value(),
                        _ct("tool_input"): json.dumps({"file_path": file_path}),
                        _ct("tool_output"): json.dumps({"ok": True}),
                        "gen_ai.tool.name": random.choice(("apply_patch", "list_dir", "grep")),
                        "sim.span.sequence": i + 1,
                    }
                )
                time.sleep(random.uniform(0.01, 0.08))

        if _env_bool("SIM_CURSOR_TRACE_VERBOSE", False):
            log_extra = min(3, _env_int("SIM_CURSOR_TRACE_VERBOSE_CHILDREN", 1))
            for _j in range(log_extra):
                with cursor_tracer.start_as_current_span(
                    "cursor.stream.chunk",
                    kind=trace.SpanKind.SERVER,
                ) as sc:
                    sc.set_attributes(
                        {
                            **_base_cursor_tags(),
                            "gen_ai.system": hook_gen_ai_system,
                            "gen_ai.request.model": gen_ai_model,
                            "cx.application.name": cx_app,
                            "cx.subsystem.name": cx_sub,
                            **_lib_attrs(),
                        }
                    )
                    time.sleep(0.01)

        # Root summary attributes (C4C facets on session span)
        root.set_attribute(_ct("session_duration_ms"), wall_ms)
        root.set_attribute(_ct("duration_ms"), wall_ms)
        root.set_attribute(_ct("response_lines"), int(resp_lines))
        root.set_attribute(_ct("final_status"), final_status)
        root.set_attribute(_ct("status"), final_status)
        if reason:
            root.set_attribute(_ct("reason"), reason)
