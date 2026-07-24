"""GitHub Copilot CLI simulator — OTLP traces/logs + Prometheus.

VS Code Copilot OTel: ``invoke_agent`` → ``chat`` / ``execute_tool``, ``gen_ai.agent.name=copilotcli``,
Resource ``service.name=github-copilot``. Span tags aligned with Coralogix AI Center Copilot CLI dashboards:

- cxai-demo: ``otel.scope.name=github.copilot``, ``enduser.pseudo.id``, ``github.copilot.cost``
  (once-per-day rollup reflecting accrued session usage — not per-turn), ``gen_ai.usage.*``
  on ``invoke_agent``.
- cx498 repo panels: ``github.copilot.git.*``, ``github.copilot.nano_aiu``, ``$d.process.tags['user.email']``
  (via per-session Resource), ``gen_ai.response.model`` on ``chat``.
- cx498 session breakdown: ``gen_ai.conversation.id``, ``gen_ai.input.messages`` / ``gen_ai.output.messages``
  on ``chat`` (GenAI JSON message shape for ``sessionsWithMessages`` / AI analysis).

Cost: set ``SIM_COPILOT_COST_ONCE_PER_DAY=false`` to restore legacy per-session ``github.copilot.cost``
on ``invoke_agent``. Chat spans never carry cost (avoids sum()-inflated dashboards).

See https://code.visualstudio.com/docs/agents/guides/monitoring-agents
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import socket
import sys
import time
import uuid

from opentelemetry import trace
from opentelemetry.sdk._logs import LogRecord
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExportResult
from opentelemetry.trace import Status, StatusCode, TraceFlags
from opentelemetry._logs.severity import SeverityNumber

from sim.common.otel import (
    _cx_log_record_attrs,
    tool_version_for,
)
from sim.common.repos import sim_session_repository_names
from sim.copilot.repos import copilot_session_git_repo_segments
from sim.common.constants import (
    COPILOT_CLI_MODELS,
    copilot_prompt_reply_for_turn,
)
from sim.common.cache_usage import sim_prompt_cache_token_split
from sim.common.env import _env_bool, _env_csv_model_pool, _env_float, _env_int
from sim.common.identity import _claude_otlp_span_user_attrs_from_roster, random_coralogix_identity_for_agent
from sim.common.state import st

_COPILOT_TOOLS: tuple[tuple[str, str, str], ...] = (
    ("view", "Tool for viewing files and directories.", '{"path":"src/main.ts"}'),
    ("grep", "Search file contents.", '{"pattern":"TODO","path":"."}'),
    ("bash", "Run a shell command.", '{"command":"npm test"}'),
    ("read", "Read a file.", '{"path":"README.md"}'),
    ("glob", "Find files by pattern.", '{"pattern":"**/*.py"}'),
)

_RESPONSE_MODEL_SUFFIXES: tuple[str, ...] = (
    "2024-08-06",
    "2025-04-14",
    "2025-06-01",
)

class _SharedSpanExporter:
    """Wrap the process-wide OTLP exporter so per-session providers can shut down safely."""

    def __init__(self, inner) -> None:
        self._inner = inner

    def export(self, spans):  # noqa: ANN001 — OTel SpanExporter protocol
        return self._inner.export(spans)

    def shutdown(self, timeout_millis: int = 30000) -> SpanExportResult:
        return SpanExportResult.SUCCESS

    def force_flush(self, timeout_millis: int = 30000) -> SpanExportResult:
        return self._inner.force_flush(timeout_millis)


def _copilot_repo_metric_labels(
    *,
    conversation_id: str,
    user_attrs: dict,
) -> dict[str, str]:
    """
    Label set for ``copilot_cli_session_repo_info`` (cxai-demo live shape).

    Real series use ``cx_application_name=copilot-cli``, ``cx_subsystem_name=production``,
    ``service_name=copilot-cli-hook``, plus ``session_id``, ``user_email``, ``repository_name``.
    """
    return {
        "cx_application_name": os.environ.get(
            "SIM_COPILOT_REPO_CX_APPLICATION_NAME", "copilot-cli"
        ).strip()
        or "copilot-cli",
        "cx_subsystem_name": os.environ.get(
            "SIM_COPILOT_REPO_CX_SUBSYSTEM_NAME", "production"
        ).strip()
        or "production",
        "service_name": os.environ.get("SIM_COPILOT_REPO_SERVICE_NAME", "copilot-cli-hook").strip()
        or "copilot-cli-hook",
        "session_id": conversation_id,
        "user_email": str(user_attrs.get("user.email", "") or "unknown@coralogix.com"),
    }


def _emit_copilot_session_repo_metrics(
    conversation_id: str,
    user_attrs: dict,
    *,
    roster_user: dict | None,
) -> None:
    if st.prom_copilot_session_repo is None or not _env_bool("SIM_COPILOT_REPO_METRICS", True):
        return
    base = _copilot_repo_metric_labels(conversation_id=conversation_id, user_attrs=user_attrs)
    for repo_name in sim_session_repository_names(
        conversation_id,
        roster_user,
        agent_product="copilot_cli",
    ):
        st.prom_copilot_session_repo.labels(**base, repository_name=repo_name).set(1)


def _copilot_otel_scope_name() -> str:
    """
    Must match AI Center DataPrime: ``tags['otel.scope.name'] == 'github.copilot'``
    (cxai-demo dashboard HAR).
    """
    return os.environ.get("SIM_COPILOT_OTEL_SCOPE_NAME", "github.copilot").strip() or "github.copilot"


def _copilot_enduser_pseudo_id(user_attrs: dict) -> str:
    """
    Dashboard panels filter ``tags['enduser.pseudo.id']`` (unique users / power user).

    Default: roster ``user.email`` (``@coralogix.com``), consistent with other CLI sims.

    - ``SIM_COPILOT_ENDUSER_PSEUDO_OPAQUE=true``: stable hex digest (cxai-demo HAR-style).
    - Legacy: explicit ``SIM_COPILOT_ENDUSER_PSEUDO_RAW_EMAIL=false`` also selects opaque ids.
    """
    email = str(user_attrs.get("user.email", "") or "unknown@coralogix.com")
    legacy_raw = os.environ.get("SIM_COPILOT_ENDUSER_PSEUDO_RAW_EMAIL")
    if legacy_raw is not None and not _env_bool("SIM_COPILOT_ENDUSER_PSEUDO_RAW_EMAIL", True):
        return hashlib.sha256(f"github-copilot-pseudo:{email}".encode()).hexdigest()[:32]
    if _env_bool("SIM_COPILOT_ENDUSER_PSEUDO_OPAQUE", True):
        return hashlib.sha256(f"github-copilot-pseudo:{email}".encode()).hexdigest()[:32]
    return email


def _copilot_github_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    cache_read_tokens: int = 0,
) -> float:
    """API-equivalent USD for Copilot usage (model-aware rates).

    ``input_tokens`` should be the **full** prompt size (billable + cache-read). Cache-read
    tokens are billed at the discounted cache rate inside ``estimate_llm_cost_usd``.

    ``SIM_COPILOT_COST_SCALE`` (default 1.0) multiplies the result so demo spend can be
    dialed independently of per-turn token ranges (still accrued once/day when enabled).
    """
    from sim.common.model_pricing import estimate_llm_cost_usd

    raw = estimate_llm_cost_usd(
        model,
        input_tokens,
        output_tokens,
        cache_read_tokens=cache_read_tokens,
        jitter_usd=random.uniform(0.0, 1e-6),
    )
    scale = max(0.0, _env_float("SIM_COPILOT_COST_SCALE", 1.0))
    return raw * scale


def _copilot_turn_token_counts() -> tuple[int, int]:
    """Prompt/output tokens per Copilot ``chat`` turn (realistic CLI ranges)."""
    p_lo = max(64, _env_int("SIM_COPILOT_PROMPT_TOKENS_MIN", 400))
    p_hi = max(p_lo + 1, _env_int("SIM_COPILOT_PROMPT_TOKENS_MAX", 4_000))
    o_lo = max(32, _env_int("SIM_COPILOT_OUTPUT_TOKENS_MIN", 80))
    o_hi = max(o_lo + 1, _env_int("SIM_COPILOT_OUTPUT_TOKENS_MAX", 1_500))
    return random.randint(p_lo, p_hi), random.randint(o_lo, o_hi)


def _copilot_nano_aiu(cost_usd: float) -> int:
    """cx498 dashboards divide ``github.copilot.nano_aiu`` by 1e9 for session/user cost."""
    return int(round(max(0.0, cost_usd) * 1_000_000_000))


def _copilot_telemetry_model_id(model: str) -> str:
    m = re.match(r"^(claude-(?:opus|sonnet|haiku))-(\d+)-(\d+)$", model)
    if m:
        return f"{m.group(1)}-{m.group(2)}.{m.group(3)}"
    return model


def _copilot_finish_reasons_json(*reasons: str) -> str:
    return json.dumps(list(reasons) if reasons else ["stop"], separators=(",", ":"))


def _copilot_gen_ai_messages_json(role: str, text: str, *, finish_reason: str | None = None) -> str:
    msg: dict[str, object] = {"role": role, "parts": [{"type": "text", "content": text}]}
    if finish_reason and role == "assistant":
        msg["finish_reason"] = finish_reason
    return json.dumps([msg], separators=(",", ":"))


def _copilot_invoke_message_attrs(
    prompt: str,
    turns: list[tuple[str, str, str]],
) -> dict[str, str]:
    if not _env_bool("SIM_COPILOT_CAPTURE_MESSAGES", True) or not turns:
        return {}
    input_msgs = []
    output_msgs = []
    for user_text, assistant_text, finish_reason in turns:
        input_msgs.extend(json.loads(_copilot_gen_ai_messages_json("user", user_text)))
        output_msgs.extend(
            json.loads(_copilot_gen_ai_messages_json("assistant", assistant_text, finish_reason=finish_reason))
        )
    return {
        "gen_ai.input.messages": json.dumps(input_msgs, separators=(",", ":")),
        "gen_ai.output.messages": json.dumps(output_msgs, separators=(",", ":")),
    }


def _copilot_tool_call_attrs(tool_name: str, tool_desc: str, tool_args: str, *, ok: bool) -> dict[str, str]:
    call_id = f"toolu_{hashlib.sha256(f'{tool_name}:{time.time_ns()}'.encode()).hexdigest()[:24]}"
    result = "README.md\nsrc/\npackage.json\ntsconfig.json" if ok and tool_name == "view" else ("ok" if ok else "error: tool failed")
    return {
        "gen_ai.tool.name": tool_name,
        "gen_ai.tool.type": "function",
        "gen_ai.tool.call.id": call_id,
        "gen_ai.tool.call.arguments": tool_args,
        "gen_ai.tool.call.result": result,
        "gen_ai.tool.description": tool_desc,
    }


def _copilot_response_model(request_model: str, conversation_id: str, turn: int) -> str:
    telemetry_model = _copilot_telemetry_model_id(request_model)
    rng = random.Random(hashlib.sha256(f"copilot:resp:{conversation_id}:{turn}".encode()).digest())
    tail = telemetry_model.rsplit("-", 1)[-1]
    if rng.random() < 0.12 and "." not in tail:
        return f"{telemetry_model}-{rng.choice(_RESPONSE_MODEL_SUFFIXES)}"
    return telemetry_model


def _copilot_user_model_pool(conversation_id: str, pool: tuple[str, ...]) -> tuple[str, ...]:
    """
    Deterministic 2–3 model ids per stable session (not the full GA pool).

    Most users get two models; ~35% get three so dashboards show modest per-user variety.
    """
    if not pool:
        return (COPILOT_CLI_MODELS[0],)
    cid = conversation_id.strip() or "unknown-session"
    digest = hashlib.sha256(f"otel-ai-agent-sim:copilot:model-pool:{cid}".encode()).digest()
    n_pick = 3 if digest[0] % 100 >= 65 else 2
    n_pick = min(n_pick, len(pool))
    choices: list[str] = []
    used: set[int] = set()
    b = 1
    while len(choices) < n_pick:
        idx = digest[b % len(digest)] % len(pool)
        b += 1
        if idx in used:
            if b > len(digest) + len(pool) * 4:
                break
            continue
        used.add(idx)
        choices.append(pool[idx])
    if not choices:
        choices.append(pool[digest[1] % len(pool)])
    return tuple(choices)


def _copilot_model_for_session(conversation_id: str, profile: dict) -> str:
    """
    Model for one Copilot CLI emit.

    Pins ``SIM_COPILOT_MODEL`` when set. Otherwise each stable ``conversation_id`` keeps a small
    deterministic model pool (2–3 ids). The primary model is used most of the time; alternates
    appear occasionally on a time bucket so invoke counts do not accumulate one id per tick.
    """
    pinned = os.environ.get("SIM_COPILOT_MODEL", "").strip()
    if pinned:
        return pinned
    profile_model = str(profile.get("gen_ai.request.model", "") or "").strip()
    if profile_model and _env_bool("SIM_COPILOT_USE_PROFILE_MODEL", False):
        return profile_model
    pool = _env_csv_model_pool("SIM_COPILOT_MODELS", COPILOT_CLI_MODELS)
    choices = _copilot_user_model_pool(conversation_id, pool)
    if len(choices) == 1:
        return choices[0]
    rotate_sec = max(0, _env_int("SIM_COPILOT_MODEL_ROTATE_SEC", 3600))
    if rotate_sec <= 0:
        return choices[0]
    cid = conversation_id.strip() or "unknown-session"
    bucket = int(time.time()) // rotate_sec
    digest = hashlib.sha256(f"otel-ai-agent-sim:copilot:model-pick:{cid}:{bucket}".encode()).digest()
    primary_weight = min(1.0, max(0.5, _env_float("SIM_COPILOT_PRIMARY_MODEL_WEIGHT", 0.82)))
    r = digest[0] / 255.0
    if r < primary_weight:
        return choices[0]
    alt = choices[1:]
    idx = digest[1] % len(alt)
    return alt[idx]


def _copilot_model_for_turn(profile: dict) -> str:
    """Backward-compatible alias when no stable conversation id is available."""
    return _copilot_model_for_session(str(profile.get("gen_ai.conversation.id", "") or ""), profile)


def _copilot_log_conversation_attrs(
    conversation_id: str,
    *,
    include_session_id: bool = False,
) -> dict[str, str]:
    """
    Dual-emit legacy ``conversation.id`` and GenAI ``gen_ai.conversation.id`` on OTLP logs.

    ``copilot_chat.session.start`` also carries ``session.id`` per VS Code agent monitoring spec.
    """
    attrs: dict[str, str] = {
        "conversation.id": conversation_id,
        "gen_ai.conversation.id": conversation_id,
    }
    if include_session_id:
        attrs["session.id"] = conversation_id
    return attrs


def _emit_copilot_otlp_log(
    *,
    body: str,
    attributes: dict[str, str | int | float | bool],
    trace_id: int,
    span_id: int,
    timestamp_ns: int | None = None,
) -> None:
    if st.copilot_otlp_logger is None:
        return
    cx_app = os.environ.get("COPILOT_CX_APPLICATION_NAME", "copilot-cli")
    cx_sub = os.environ.get("COPILOT_CX_SUBSYSTEM_NAME", "copilot-sessions")
    merged: dict[str, str | int | float | bool] = {
        **_cx_log_record_attrs(cx_app, cx_sub),
        **attributes,
    }
    rec = LogRecord(
        timestamp=timestamp_ns or time.time_ns(),
        trace_id=trace_id,
        span_id=span_id,
        trace_flags=TraceFlags.get_default(),
        severity_number=SeverityNumber.INFO,
        severity_text="INFO",
        body=body,
        attributes=merged,
        resource=st.copilot_otlp_logger.resource,
    )
    st.copilot_otlp_logger.emit(rec)


def _copilot_session_tracer(user_email: str) -> tuple[trace.Tracer, TracerProvider | None]:
    """
    Return a tracer whose Resource carries ``user.email`` for Coralogix ``$d.process.tags``.

    Falls back to the shared provider when disabled or not initialized.
    """
    ver = tool_version_for("github_copilot")
    scope_nm = _copilot_otel_scope_name()
    if st.sim_cli is None:
        raise RuntimeError("CLI trace providers not initialized")

    if not _env_bool("SIM_COPILOT_PROCESS_USER_EMAIL", True):
        return st.sim_cli.github_copilot.get_tracer(scope_nm, ver), None

    base_res = st.copilot_trace_base_resource
    exporter = st.copilot_span_exporter
    if base_res is None or exporter is None:
        return st.sim_cli.github_copilot.get_tracer(scope_nm, ver), None

    session_res = base_res.merge(Resource.create({"user.email": user_email}))
    session_tp = TracerProvider(resource=session_res)
    session_tp.add_span_processor(BatchSpanProcessor(_SharedSpanExporter(exporter)))
    return session_tp.get_tracer(scope_nm, ver), session_tp


def emit_copilot_cli_session(
    conversation_id: str,
    profile: dict,
    *,
    roster_user: dict | None = None,
) -> None:
    """
    Terminal Copilot CLI session: one or more ``invoke_agent`` roots (multi-repo sessions emit
    one root per repo, shared ``gen_ai.conversation.id``) with nested ``chat`` / ``execute_tool``
    spans, OTLP logs, and ``copilot_chat_*`` Prometheus counters/histograms.
    """
    if st.sim_cli is None:
        raise RuntimeError("CLI trace providers not initialized")
    ver = tool_version_for("github_copilot")
    scope_nm = _copilot_otel_scope_name()
    cx_app = os.environ.get("COPILOT_CX_APPLICATION_NAME", "copilot-cli")
    cx_sub = os.environ.get("COPILOT_CX_SUBSYSTEM_NAME", "copilot-sessions")
    model = _copilot_telemetry_model_id(_copilot_model_for_session(conversation_id, profile))
    if roster_user is not None:
        user_attrs = _claude_otlp_span_user_attrs_from_roster(roster_user)
    else:
        user_attrs = random_coralogix_identity_for_agent(conversation_id, "copilot_cli")
    user_email = str(user_attrs.get("user.email", "") or "unknown@coralogix.com")
    pseudo_id = _copilot_enduser_pseudo_id(user_attrs)

    n_turns = max(1, _env_int("SIM_COPILOT_CHAT_ROUNDS", 2))
    repo_segments = copilot_session_git_repo_segments(conversation_id, roster_user, n_turns)
    wall_start = time.perf_counter()

    tracer, session_tp = _copilot_session_tracer(user_email)
    try:
        total_in = 0
        total_out = 0
        total_cache_read_in = 0
        premium_req = 0
        cache_hits = 0
        n_tools_total = 0
        session_productivity_ok = False
        session_start_logged = False
        turn_global = 0

        for _repo_short, git_attrs, seg_turns in repo_segments:
            segment_messages: list[tuple[str, str, str]] = []
            last_finish_reason = "stop"
            seg_cache_creation = 0
            seg_reasoning_out = 0

            with tracer.start_as_current_span(
                "invoke_agent",
                kind=trace.SpanKind.CLIENT,
            ) as root:
                root.set_status(Status(StatusCode.OK))
                root.set_attributes(
                    {
                        **git_attrs,
                        "enduser.pseudo.id": pseudo_id,
                        "agent.product": "copilot_cli",
                        "sim.agent_tool_version": ver,
                        "otel.library.name": "github-copilot",
                        "otel.library.version": ver,
                        "otel.scope.name": scope_nm,
                        "otel.scope.version": ver,
                        "gen_ai.provider.name": "github",
                        "gen_ai.agent.id": "github.copilot.default",
                        "gen_ai.agent.version": ver,
                        "gen_ai.session.id": conversation_id,
                        "gen_ai.conversation.id": conversation_id,
                        "gen_ai.operation.name": "invoke_agent",
                        "gen_ai.request.model": model,
                        "cx.application.name": cx_app,
                        "cx.subsystem.name": cx_sub,
                        "session.id": conversation_id,
                        "process.runtime.name": "python",
                        "process.runtime.version": sys.version.split()[0],
                        "host.name": os.environ.get("HOSTNAME", socket.gethostname()),
                        "process.pid": str(os.getpid()),
                    }
                )
                ctx_root = root.get_span_context()
                if not session_start_logged:
                    _emit_copilot_otlp_log(
                        body="copilot_chat.session.start",
                        attributes={
                            "event.name": "copilot_chat.session.start",
                            "gen_ai.agent.id": "github.copilot.default",
                            "gen_ai.request.model": model,
                            **_copilot_log_conversation_attrs(conversation_id, include_session_id=True),
                        },
                        trace_id=ctx_root.trace_id,
                        span_id=ctx_root.span_id,
                    )
                    session_start_logged = True

                seg_in = 0
                seg_out = 0
                seg_cache_read_in = 0

                for _ in range(seg_turns):
                    prompt_tokens, out = _copilot_turn_token_counts()
                    billable_in, cache_read_in, cached = sim_prompt_cache_token_split(
                        prompt_tokens,
                        turn_index=turn_global,
                        hit_prob_env="SIM_COPILOT_CACHE_HIT_RATE",
                        hit_prob_default=_env_float("SIM_PROMPT_CACHE_HIT_RATE", 0.96),
                        first_turn_miss=_env_bool("SIM_PROMPT_CACHE_FIRST_TURN_MISS", False),
                    )
                    cache_creation = random.randint(0, max(0, billable_in // 2)) if not cached else 0
                    reasoning_out = random.randint(0, max(0, out // 8))
                    seg_cache_creation += cache_creation
                    seg_reasoning_out += reasoning_out
                    seg_in += billable_in
                    seg_out += out
                    seg_cache_read_in += cache_read_in
                    total_in += billable_in
                    total_out += out
                    total_cache_read_in += cache_read_in
                    is_premium = random.random() < float(os.environ.get("SIM_COPILOT_PREMIUM_RATE", "0.22"))
                    if is_premium:
                        premium_req += 1
                    if cached:
                        cache_hits += 1

                    chat_duration_s = random.uniform(0.8, 8.0)
                    ttft_ms = random.randint(80, 2200)
                    finish_reason = random.choice(("stop", "tool_calls", "length"))
                    last_finish_reason = finish_reason
                    response_model = _copilot_response_model(model, conversation_id, turn_global)
                    prompt, assistant_text = copilot_prompt_reply_for_turn(conversation_id, turn_global)
                    user_text = prompt if turn_global == 0 else f"Continue: {prompt[:160]}"
                    segment_messages.append((user_text, assistant_text, finish_reason))
                    interaction_id = str(uuid.uuid4())
                    service_request_id = str(uuid.uuid4())
                    response_id = f"msg_{hashlib.sha256(f'{conversation_id}:{turn_global}'.encode()).hexdigest()[:20]}"

                    with tracer.start_as_current_span(
                        "chat",
                        kind=trace.SpanKind.CLIENT,
                    ) as chat_sp:
                        chat_sp.set_status(Status(StatusCode.OK))
                        # Cost / nano_aiu intentionally omitted on chat — dashboards that sum
                        # ``github.copilot.cost`` expect ~once/day rollups, not per-turn stamps.
                        chat_sp.set_attributes(
                            {
                                "agent.product": "copilot_cli",
                                "gen_ai.provider.name": "github",
                                "gen_ai.operation.name": "chat",
                                "gen_ai.request.model": model,
                                "gen_ai.response.model": response_model,
                                "gen_ai.response.id": response_id,
                                "gen_ai.session.id": conversation_id,
                                "gen_ai.conversation.id": conversation_id,
                                "gen_ai.usage.input_tokens": billable_in,
                                "gen_ai.usage.output_tokens": out,
                                "gen_ai.usage.cache_creation_input_tokens": cache_creation,
                                "gen_ai.usage.cache_read_input_tokens": cache_read_in,
                                "gen_ai.usage.reasoning_output_tokens": reasoning_out,
                                "gen_ai.response.finish_reasons": _copilot_finish_reasons_json(finish_reason),
                                "github.copilot.server_duration": str(int(chat_duration_s * 1000)),
                                "github.copilot.initiator": "user",
                                "github.copilot.turn_id": str(turn_global),
                                "github.copilot.interaction_id": interaction_id,
                                "github.copilot.service_request_id": service_request_id,
                                "copilot.request.tier": ("premium" if is_premium else "standard"),
                                "copilot.cache.status": ("hit" if cached else "miss"),
                                "cx.application.name": cx_app,
                                "cx.subsystem.name": cx_sub,
                                "otel.library.name": "github-copilot",
                                "otel.scope.name": scope_nm,
                            }
                        )
                        ctx_chat = chat_sp.get_span_context()
                        time.sleep(chat_duration_s * random.uniform(0.08, 0.18))
                        _emit_copilot_otlp_log(
                            body="gen_ai.client.inference.operation.details",
                            attributes={
                                "event.name": "gen_ai.client.inference.operation.details",
                                "gen_ai.operation.name": "chat",
                                "gen_ai.request.model": model,
                                "gen_ai.response.model": response_model,
                                "gen_ai.usage.input_tokens": billable_in,
                                "gen_ai.usage.output_tokens": out,
                                "gen_ai.usage.cache_read_input_tokens": cache_read_in,
                                "duration_ms": int(chat_duration_s * 1000),
                                "finish_reason": finish_reason,
                                "gen_ai.response.finish_reasons": finish_reason,
                                **_copilot_log_conversation_attrs(conversation_id),
                                "copilot.request.tier": ("premium" if is_premium else "standard"),
                            },
                            trace_id=ctx_chat.trace_id,
                            span_id=ctx_chat.span_id,
                        )

                    if st.prom_copilot_ttft is not None:
                        st.prom_copilot_ttft.labels(cx_app, cx_sub, model).observe(ttft_ms / 1000.0)

                    if st.prom_copilot_chat_dur is not None:
                        st.prom_copilot_chat_dur.labels(cx_app, cx_sub, model).observe(chat_duration_s)

                    n_tools = random.randint(1, 3)
                    n_tools_total += n_tools
                    for _ in range(n_tools):
                        tool_name, tool_desc, tool_args = random.choice(_COPILOT_TOOLS)
                        tool_ms = random.randint(12, 3200)
                        ok = random.random() > 0.05
                        with tracer.start_as_current_span(
                            f"execute_tool {tool_name}",
                            kind=trace.SpanKind.INTERNAL,
                        ) as tool_sp:
                            tool_sp.set_status(
                                Status(StatusCode.OK if ok else StatusCode.ERROR, None if ok else "tool_error")
                            )
                            tool_sp.set_attributes(
                                {
                                    "agent.product": "copilot_cli",
                                    "gen_ai.operation.name": "execute_tool",
                                    "gen_ai.provider.name": "github",
                                    "gen_ai.conversation.id": conversation_id,
                                    **_copilot_tool_call_attrs(
                                        tool_name, tool_desc, tool_args, ok=ok
                                    ),
                                    "cx.application.name": cx_app,
                                    "cx.subsystem.name": cx_sub,
                                    "otel.library.name": "github-copilot",
                                    "otel.scope.name": scope_nm,
                                }
                            )
                            ctx_tool = tool_sp.get_span_context()
                            time.sleep(tool_ms / 1000.0 * random.uniform(0.05, 0.25))
                            _emit_copilot_otlp_log(
                                body="copilot_chat.tool.call",
                                attributes={
                                    "event.name": "copilot_chat.tool.call",
                                    "gen_ai.tool.name": tool_name,
                                    "success": ok,
                                    "duration_ms": tool_ms,
                                    **_copilot_log_conversation_attrs(conversation_id),
                                },
                                trace_id=ctx_tool.trace_id,
                                span_id=ctx_tool.span_id,
                            )

                        if st.prom_copilot_tool is not None:
                            st.prom_copilot_tool.labels(
                                cx_app, cx_sub, tool_name, "success" if ok else "error"
                            ).inc()
                        if st.prom_copilot_tool_dur is not None:
                            st.prom_copilot_tool_dur.labels(cx_app, cx_sub, tool_name).observe(tool_ms / 1000.0)

                    productivity_ok = random.random() < float(
                        os.environ.get("SIM_COPILOT_PRODUCTIVITY_ACCEPT_RATE", "0.74")
                    )
                    session_productivity_ok = session_productivity_ok or productivity_ok
                    if st.prom_copilot_edit is not None:
                        st.prom_copilot_edit.labels(cx_app, cx_sub, "accepted" if productivity_ok else "rejected").inc()

                    turn_global += 1

                # Full prompt = billable + cache-read (pricing helper subtracts cache-read once).
                seg_prompt = seg_in + seg_cache_read_in
                seg_cost_usd = _copilot_github_cost_usd(
                    model,
                    seg_prompt,
                    seg_out,
                    cache_read_tokens=seg_cache_read_in,
                )
                root.set_attributes(
                    {
                        **_copilot_invoke_message_attrs(prompt, segment_messages),
                        "gen_ai.usage.input_tokens": seg_in,
                        "gen_ai.usage.output_tokens": seg_out,
                        "gen_ai.usage.cache_read_input_tokens": seg_cache_read_in,
                        "gen_ai.usage.cache_creation_input_tokens": seg_cache_creation,
                        "gen_ai.usage.reasoning_output_tokens": seg_reasoning_out,
                        "gen_ai.response.finish_reasons": _copilot_finish_reasons_json(last_finish_reason),
                        "github.copilot.turn_count": str(seg_turns),
                    }
                )
                # Defer cost/nano_aiu until after all segments so we can attach a once/day rollup.

        wall_s = max(0.01, time.perf_counter() - wall_start)
        session_prompt = total_in + total_cache_read_in
        session_cost_usd = _copilot_github_cost_usd(
            model,
            session_prompt,
            total_out,
            cache_read_tokens=total_cache_read_in,
        )

        from sim.copilot.daily_cost import (
            accrue_copilot_session_cost,
            copilot_cost_once_per_day_enabled,
            take_copilot_daily_cost_emit,
        )

        accrue_copilot_session_cost(
            user_email,
            session_cost_usd,
            input_tokens=total_in,
            output_tokens=total_out,
            cache_read_tokens=total_cache_read_in,
        )

        daily_emit = take_copilot_daily_cost_emit(user_email)
        emit_cost_usd: float | None = None
        if daily_emit is not None:
            emit_cost_usd = daily_emit.cost_usd
        elif not copilot_cost_once_per_day_enabled():
            # Legacy: stamp per-session cost on invoke_agent (optional null rate).
            if random.random() >= float(os.environ.get("SIM_COPILOT_NULL_INVOKE_COST_RATE", "0.12")):
                emit_cost_usd = session_cost_usd

        if emit_cost_usd is not None and emit_cost_usd > 0:
            # Attach daily (or legacy session) cost to the most recent invoke_agent via a
            # lightweight follow-up attribute span is not available after close — re-open is
            # messy; stamp via OTLP log + Prometheus billing instead, and set on a dedicated
            # cost span under a new short-lived root when needed.
            with tracer.start_as_current_span(
                "invoke_agent",
                kind=trace.SpanKind.CLIENT,
            ) as cost_root:
                cost_root.set_status(Status(StatusCode.OK))
                cost_root.set_attributes(
                    {
                        "agent.product": "copilot_cli",
                        "gen_ai.provider.name": "github",
                        "gen_ai.operation.name": "invoke_agent",
                        "gen_ai.request.model": model,
                        "gen_ai.session.id": conversation_id,
                        "gen_ai.conversation.id": conversation_id,
                        "cx.application.name": cx_app,
                        "cx.subsystem.name": cx_sub,
                        "otel.scope.name": scope_nm,
                        "enduser.pseudo.id": pseudo_id,
                        "github.copilot.cost": emit_cost_usd,
                        "github.copilot.nano_aiu": _copilot_nano_aiu(emit_cost_usd),
                        "github.copilot.cost.rollup": "daily" if daily_emit is not None else "session",
                        "github.copilot.cost.day": (
                            daily_emit.day if daily_emit is not None else ""
                        ),
                        "github.copilot.cost.sessions": (
                            daily_emit.sessions if daily_emit is not None else 1
                        ),
                    }
                )

        if st.prom_copilot_agent_dur is not None:
            st.prom_copilot_agent_dur.labels(cx_app, cx_sub, model).observe(wall_s)

        # Prometheus increments after root span closes (same pattern as Codex).
        if st.prom_copilot_session is not None:
            st.prom_copilot_session.labels(cx_app, cx_sub, model).inc()
        if st.prom_copilot_token is not None:
            st.prom_copilot_token.labels(cx_app, cx_sub, model, "input").inc(total_in)
            st.prom_copilot_token.labels(cx_app, cx_sub, model, "output").inc(total_out)
        if st.prom_copilot_premium is not None:
            st.prom_copilot_premium.labels(cx_app, cx_sub, model).inc(premium_req)
        if st.prom_copilot_cache is not None:
            st.prom_copilot_cache.labels(cx_app, cx_sub, model, "hit").inc(cache_hits)
            st.prom_copilot_cache.labels(cx_app, cx_sub, model, "miss").inc(max(0, n_turns - cache_hits))

        if st.copilot_collector is not None:
            from sim.copilot.collector_metrics import record_copilot_collector_session

            record_copilot_collector_session(
                st.copilot_collector,
                user_attrs=user_attrs,
                model=model,
                n_turns=n_turns,
                n_tools=n_tools_total,
                total_in=total_in,
                total_out=total_out,
                # Billing amount only on the daily rollup emit (reflects accrued usage).
                cost_usd=emit_cost_usd if emit_cost_usd is not None else 0.0,
                productivity_ok=session_productivity_ok,
                record_billing=emit_cost_usd is not None,
            )

        _emit_copilot_session_repo_metrics(
            conversation_id,
            user_attrs,
            roster_user=roster_user,
        )
    finally:
        if session_tp is not None:
            session_tp.force_flush()
            session_tp.shutdown()
