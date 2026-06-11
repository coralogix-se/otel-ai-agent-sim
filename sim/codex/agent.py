"""OpenAI Codex CLI simulator (run_turn span + structured OTLP logs)."""
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
from sim.common.constants import CODEX_AGENT_DESCRIPTION, CODEX_CLI_MODELS, CODEX_SAMPLE_PROMPTS
from sim.common.env import _env_bool, _env_csv_model_pool, _env_int
from sim.common.identity import random_coralogix_identity
from sim.common.state import st


def _codex_model_for_turn(profile: dict) -> str:
    """One model per turn: random from pool unless ``SIM_CODEX_MODEL`` pins a single id."""
    pinned = os.environ.get("SIM_CODEX_MODEL", "").strip()
    if pinned:
        return pinned
    pool = _env_csv_model_pool("SIM_CODEX_MODELS", CODEX_CLI_MODELS)
    return random.choice(pool)


def _emit_codex_sse_stream_delta_maybe(
    conversation_id: str,
    model: str,
    user_email: str,
    trace_id: int,
    span_id: int,
) -> None:
    """Sometimes emit a non-terminal ``codex.sse_event`` (e.g. delta) before ``response.completed``."""
    if st.codex_otlp_logger is None:
        return
    if random.random() > 0.42:
        return
    _emit_codex_otlp_structured_log(
        body="codex.sse_event",
        attributes={
            "event.name": "codex.sse_event",
            "event.kind": "delta",
            "stream_event_kind": "delta",
            "success": True,
            "duration_ms": random.randint(4, 120),
            "model": model,
            "conversation.id": conversation_id,
            "user.email": user_email,
        },
        trace_id=trace_id,
        span_id=span_id,
    )


def _emit_codex_sse_response_completed_logs(
    conversation_id: str,
    model: str,
    user_email: str,
    trace_id: int,
    span_id: int,
) -> None:
    """
    ``codex.sse_event`` with ``response.completed`` and token counts — OpenAI Codex Advanced Config
    (Observability → What gets emitted) + Coralogix AI Center facets.
    """
    ts0 = time.time_ns()
    n = random.randint(1, 2)
    for i in range(n):
        inp = random.randint(400, 12_000)
        out = random.randint(50, 4000)
        cached = random.randint(0, 3000)
        if i > 0:
            inp = random.randint(200, 2000)
            out = random.randint(30, 1500)
            cached = random.randint(0, 800)
        duration_ms = random.randint(120, 4200)
        _emit_codex_otlp_structured_log(
            body="codex.sse_event",
            attributes={
                "event.name": "codex.sse_event",
                "event.kind": "response.completed",
                "stream_event_kind": "response.completed",
                "success": True,
                "duration_ms": duration_ms,
                "model": model,
                "input_token_count": inp,
                "output_token_count": out,
                "cached_token_count": cached,
                "conversation.id": conversation_id,
                "user.email": user_email,
            },
            trace_id=trace_id,
            span_id=span_id,
            timestamp_ns=ts0 + i * 75_000_000,
        )


def emit_codex_user_prompt_span(conversation_id: str, profile: dict) -> None:
    """
    Codex CLI: ``run_turn`` + nested spans, plus OTLP **logs** aligned with OpenAI
    `Advanced Configuration → Observability and telemetry` (structured event types such as
    ``codex.conversation_starts``, ``codex.api_request``, ``codex.sse_event``, ``codex.user_prompt``,
    ``codex.tool_decision``, ``codex.tool_result``).
    """
    if st.sim_cli is None:
        raise RuntimeError("CLI trace providers not initialized")
    ver = tool_version_for("codex")
    codex_tracer = st.sim_cli.codex.get_tracer("codex", ver)
    event_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    prompt = random.choice(CODEX_SAMPLE_PROMPTS)
    cx_app = os.environ.get("CODEX_CX_APPLICATION_NAME", "codex")
    cx_sub = os.environ.get("CODEX_CX_SUBSYSTEM_NAME", "codex-sessions")
    model = _codex_model_for_turn(profile)
    duration_s = random.uniform(2.0, 5.5)
    user_attrs = random_coralogix_identity(conversation_id)
    user_email = user_attrs["user.email"]

    with codex_tracer.start_as_current_span(
        "run_turn",
        kind=trace.SpanKind.INTERNAL,
    ) as run_span:
        run_span.set_status(Status(StatusCode.OK))
        run_span.set_attributes(
            {
                **_codex_span_service_label_attrs(),
                **user_attrs,
                "agent.product": "codex",
                "sim.agent_tool_version": ver,
                "otel.library.name": "codex",
                "otel.library.version": ver,
                "otel.scope.name": "codex",
                "otel.scope.version": ver,
                "gen_ai.system": "openai",
                "gen_ai.request.model": model,
                "gen_ai.session.id": conversation_id,
                "gen_ai.conversation.id": conversation_id,
                "gen_ai.operation.name": "run_turn",
                "gen_ai.agent.name": "codex",
                "cx.event.id": str(uuid.uuid4()),
                "cx.application.name": cx_app,
                "cx.subsystem.name": cx_sub,
                "span.kind": "internal",
                "session.id": conversation_id,
                "process.runtime.name": "python",
                "process.runtime.version": sys.version.split()[0],
                "process.runtime.description": "CPython",
                "host.name": os.environ.get("HOSTNAME", socket.gethostname()),
                "host.arch": os.environ.get("SIM_HOST_ARCH", "amd64"),
                "process.pid": str(os.getpid()),
            }
        )
        ctx_run = run_span.get_span_context()
        # OpenAI Codex Advanced Config: observability log ``codex.conversation_starts``
        _emit_codex_otlp_structured_log(
            body="codex.conversation_starts",
            attributes={
                "event.name": "codex.conversation_starts",
                "model": model,
                "sandbox_mode": os.environ.get("SIM_CODEX_SANDBOX_MODE", "workspace-write"),
                "approval_policy": os.environ.get("SIM_CODEX_APPROVAL_POLICY", "on-request"),
                "model_reasoning_effort": os.environ.get("SIM_CODEX_MODEL_REASONING_EFFORT", "medium"),
                "app.version": ver,
                "conversation.id": conversation_id,
            },
            trace_id=ctx_run.trace_id,
            span_id=ctx_run.span_id,
        )
        with codex_tracer.start_as_current_span(
            "user_prompt",
            kind=trace.SpanKind.INTERNAL,
        ) as span:
            span.set_status(Status(StatusCode.OK))
            inp = random.randint(400, 12_000)
            out = random.randint(50, 4000)
            span.set_attributes(
                {
                    **_codex_span_service_label_attrs(),
                    **user_attrs,
                    **_gen_ai_dashboard_llm_span_attributes(
                        inp, out, operation_name="user_prompt", model=model
                    ),
                    "agent.product": "codex",
                    "sim.agent_tool_version": ver,
                    "otel.library.name": "codex",
                    "otel.library.version": ver,
                    "otel.scope.name": "codex",
                    "otel.scope.version": ver,
                    "gen_ai.system": "openai",
                    "gen_ai.request.model": model,
                    "gen_ai.request.id": request_id,
                    "gen_ai.input.messages": prompt,
                    "gen_ai.session.id": conversation_id,
                    "gen_ai.conversation.id": conversation_id,
                    "gen_ai.agent.name": "codex",
                    "gen_ai.agent.description": CODEX_AGENT_DESCRIPTION,
                    "cx.event.id": event_id,
                    "cx.application.name": cx_app,
                    "cx.subsystem.name": cx_sub,
                    "span.kind": "internal",
                    "session.id": conversation_id,
                    "process.runtime.name": "python",
                    "process.runtime.version": sys.version.split()[0],
                    "process.runtime.description": "CPython",
                    "host.name": os.environ.get("HOSTNAME", socket.gethostname()),
                    "host.arch": os.environ.get("SIM_HOST_ARCH", "amd64"),
                    "process.pid": str(os.getpid()),
                }
            )
            time.sleep(duration_s * random.uniform(0.35, 0.55))
            ctx_up = span.get_span_context()
            up_log: dict[str, str | int | float | bool] = {
                "event.name": "codex.user_prompt",
                "prompt_length": len(prompt),
                "model": model,
                "conversation.id": conversation_id,
            }
            if _env_bool("SIM_CODEX_LOG_USER_PROMPT", False):
                up_log["prompt"] = prompt
            _emit_codex_otlp_structured_log(
                body="codex.user_prompt",
                attributes=up_log,
                trace_id=ctx_up.trace_id,
                span_id=ctx_up.span_id,
            )

        # ``codex.api_request`` (HTTP) before stream — OpenAI Codex OTLP log catalog.
        api_ms = random.randint(95, 2200)
        _emit_codex_otlp_structured_log(
            body="codex.api_request",
            attributes={
                "event.name": "codex.api_request",
                "attempt": 1,
                "http_status": 200,
                "success": True,
                "duration_ms": api_ms,
                "model": model,
                "conversation.id": conversation_id,
                **user_attrs,
            },
            trace_id=ctx_run.trace_id,
            span_id=ctx_run.span_id,
        )

        # Extra spans under ``run_turn`` so traces look like multi-step Codex turns (not only 2 spans).
        n_extra = max(0, _env_int("SIM_CODEX_EXTRA_SPANS", 4))
        last_tool = "apply_patch"
        for i in range(n_extra):
            name = random.choice(
                ("codex.api_request", "codex.stream_chunk", "gen_ai.tool.invoke", "codex.apply_patch")
            )
            if "apply_patch" in name:
                last_tool = "apply_patch"
            elif "tool.invoke" in name:
                last_tool = "shell"
            elif "stream" in name:
                last_tool = "stream"
            else:
                last_tool = "api"
            with codex_tracer.start_as_current_span(name, kind=trace.SpanKind.INTERNAL) as ch:
                ch.set_status(Status(StatusCode.OK))
                ch.set_attributes(
                    {
                        **_codex_span_service_label_attrs(),
                        **user_attrs,
                        "agent.product": "codex",
                        "gen_ai.system": "openai",
                        "gen_ai.request.model": model,
                        "gen_ai.session.id": conversation_id,
                        "cx.application.name": cx_app,
                        "cx.subsystem.name": cx_sub,
                        "sim.agent_tool_version": ver,
                        "sim.span.sequence": i + 1,
                    }
                )
                time.sleep(random.uniform(0.02, 0.35))

        _emit_codex_otlp_structured_log(
            body="codex.tool_decision",
            attributes={
                "event.name": "codex.tool_decision",
                "tool": last_tool,
                "decision": random.choice(
                    ("approved", "approved_with_amendment", "approved_for_session", "denied", "abort")
                ),
                "source": random.choice(("config", "user")),
                "conversation.id": conversation_id,
            },
            trace_id=ctx_run.trace_id,
            span_id=ctx_run.span_id,
        )
        tool_ok = random.random() > 0.06
        _emit_codex_otlp_structured_log(
            body="codex.tool_result",
            attributes={
                "event.name": "codex.tool_result",
                "tool": last_tool,
                "success": tool_ok,
                "duration_ms": random.randint(15, 1200),
                "output_snippet": ("ok" if tool_ok else "error: rejected")[:200],
                "conversation.id": conversation_id,
            },
            trace_id=ctx_run.trace_id,
            span_id=ctx_run.span_id,
        )

        ctx = run_span.get_span_context()
        _emit_codex_sse_stream_delta_maybe(conversation_id, model, user_email, ctx.trace_id, ctx.span_id)
        _emit_codex_sse_response_completed_logs(
            conversation_id,
            model,
            user_email,
            ctx.trace_id,
            ctx.span_id,
        )

    if st.prom_codex_run_turn is not None and st.prom_codex_token is not None:
        st.prom_codex_run_turn.labels(cx_app, cx_sub, model).inc()
        st.prom_codex_token.labels(cx_app, cx_sub, model, "input").inc(inp)
        st.prom_codex_token.labels(cx_app, cx_sub, model, "output").inc(out)
