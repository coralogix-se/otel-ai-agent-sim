"""Claude Code OTLP trace spans (optional ``user_prompt``)."""
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
from sim.common.constants import CLAUDE_CODE_AGENT_DESCRIPTION, CLAUDE_CODE_DEFAULT_MODEL, claude_code_gen_ai_system_for_model, claude_prompt_for_session
from sim.common.identity import _claude_otlp_span_user_attrs_from_roster, random_claude_user_identity
from sim.common.state import st

def emit_claude_code_user_prompt_span(
    conversation_id: str,
    profile: dict,
    *,
    tool_version: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    roster_user: dict | None = None,
    prompt: str | None = None,
) -> None:
    """Dedicated ``user_prompt`` span on ``claude-code`` scope (OTLP traces).

    Skipped in the main loop when ``SIM_CLAUDE_OTLP_TRACES_ENABLED`` is false; metrics/logs still emit.
    """
    if st.sim_cli is None:
        raise RuntimeError("CLI trace providers not initialized")
    ver = tool_version or tool_version_for("claude_code")
    claude_tracer = st.sim_cli.claude.get_tracer("claude-code", ver)
    event_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    prompt = prompt or claude_prompt_for_session(conversation_id)
    cx_app = os.environ.get("CLAUDE_CODE_CX_APPLICATION_NAME", "claude-code")
    cx_sub = _claude_effective_cx_subsystem()
    model = os.environ.get("SIM_CLAUDE_MODEL", profile.get("gen_ai.request.model", CLAUDE_CODE_DEFAULT_MODEL))
    duration_s = random.uniform(2.2, 6.0)
    if roster_user is not None:
        user_attrs = _claude_otlp_span_user_attrs_from_roster(roster_user)
    else:
        user_attrs = random_claude_user_identity(conversation_id)

    if input_tokens is not None and output_tokens is not None:
        inp, out = input_tokens, output_tokens
    else:
        inp, out = _sim_claude_usage_token_counts()

    with claude_tracer.start_as_current_span(
        "user_prompt",
        kind=trace.SpanKind.INTERNAL,
    ) as span:
        span.set_status(Status(StatusCode.OK))
        span.set_attributes(
            {
                **user_attrs,
                **_gen_ai_dashboard_llm_span_attributes(
                    inp, out, operation_name="user_prompt", model=model
                ),
                "agent.product": "claude_code",
                "sim.agent_tool_version": ver,
                "otel.library.name": "claude-code",
                "otel.library.version": ver,
                "otel.scope.name": "claude-code",
                "otel.scope.version": ver,
                "gen_ai.system": claude_code_gen_ai_system_for_model(model),
                "gen_ai.request.model": model,
                "gen_ai.request.id": request_id,
                "gen_ai.input.messages": prompt,
                "gen_ai.session.id": conversation_id,
                "gen_ai.conversation.id": conversation_id,
                "gen_ai.agent.name": "claude-code",
                "gen_ai.agent.description": CLAUDE_CODE_AGENT_DESCRIPTION,
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
        time.sleep(duration_s)

