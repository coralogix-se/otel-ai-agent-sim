"""Claude Code Prometheus + OTLP log emission (dashboard-oriented metrics, not trace spans)."""

from __future__ import annotations

import json
import os
import platform
import random
import time
import uuid
from datetime import datetime, timezone

from opentelemetry._logs.severity import SeverityNumber
from opentelemetry.sdk._logs import LogRecord
from opentelemetry.trace import TraceFlags

from sim.claude.day_variation import claude_session_turns
from sim.claude.logs import _cc_claude_log_record_attrs
from sim.claude.meta import _claude_resolved_telemetry_profile
from sim.claude.session_emit import (
    claude_cx_subsystem_for_user,
    claude_organization_id_for_user,
    claude_service_version_for_user,
    claude_session_active_time_slice,
    claude_session_model_for_turn,
    claude_session_models,
    claude_session_note_turn,
    claude_session_plan,
    claude_session_primary_model,
    claude_session_should_emit_repo,
    claude_session_track_series,
    claude_should_inc_session_count,
    claude_terminal_type_for_user,
    claude_turn_share,
)
from sim.common.repos import is_sim_rogue_user, is_sim_top_spender, sim_session_repository_names
from sim.common.otel import (
    _anthropic_style_account_id,
    _cc_base_for_prometheus_labels,
    _map_cc_base_labels,
    _random_partition_nonneg,
    _stable_uuid,
    _cc_api_request_id,
    _cc_tool_use_id,
    tool_version_for,
)
from sim.common.constants import (
    claude_api_response_body_json,
    claude_assistant_reply_for_session,
    claude_prompt_for_session,
)
from sim.common.env import _env_bool, _env_float, _env_int
from sim.common.identity import (
    _apply_claude_dotted_email_domain,
    _claude_metric_label_pin_key,
    _claude_otlp_span_user_attrs_from_roster,
    _claude_pinned_flat_version_and_model,
    _claude_roster_core_user,
    _claude_user_identity_flavor,
)
from sim.common.state import st

# Claude-style tools for logs + decision metrics (weighted toward common tools).
_CC_TOOLS = (
    ["Edit"] * 4
    + ["Write"] * 3
    + ["Bash"] * 3
    + ["Read"] * 2
    + ["Grep"] * 2
    + ["Glob"] * 1
    + ["NotebookEdit"] * 1
)
_CC_EFFORT_MIX: tuple[tuple[str, int], ...] = (
    ("high", 40),
    ("xhigh", 12),
    ("max", 5),
    ("medium", 35),
    ("low", 8),
)
_DECISION_SOURCES = (
    "user_permanent",
    "user_temporary",
    "config",
    "hook",
    "user_reject",
    "user_abort",
)

def _cc_effort(whale: bool) -> str:
    """Whales run on high+ effort, so high/xhigh/max carry >60% of cost (effortOverProvisioned)."""
    if whale:
        return random.choice(("high", "xhigh", "max"))
    efforts, weights = zip(*_CC_EFFORT_MIX)
    return random.choices(efforts, weights=weights, k=1)[0]


def _iso_z(ts_ns: int) -> str:
    return datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _partition_nonneg_repair(total: int, n: int) -> list[int]:
    """Split ``total`` into ``n`` nonnegative integers summing to ``total`` (random; exact sum)."""
    parts = _random_partition_nonneg(total, n)
    diff = int(total) - sum(parts)
    if diff != 0 and parts:
        parts[-1] += diff
    return parts


def _cc_dashboard_base_attrs(
    session_id: str,
    user_attrs: dict,
    app_version: str,
    *,
    flavor: str,
    roster_user: dict | None = None,
) -> dict:
    pod = os.environ.get("POD_NAME") or os.environ.get("HOSTNAME") or "sim-pod"
    svc = os.environ.get("SIM_CLAUDE_SERVICE_NAME", "claude-code")
    acc = str(user_attrs.get("user.account_uuid", ""))
    org_override = os.environ.get("SIM_ORGANIZATION_ID", "").strip()
    if flavor == "dotted":
        org_override = (
            os.environ.get("SIM_CLAUDE_DOTTED_ORGANIZATION_ID", "").strip() or org_override
        )
    if org_override:
        org_id = org_override
    else:
        org_id = claude_organization_id_for_user(roster_user or user_attrs, session_id, flavor=flavor)
    term = claude_terminal_type_for_user(roster_user or user_attrs, session_id)
    return {
        "session.id": session_id,
        **user_attrs,
        "service.name": svc,
        "service.version": app_version,
        "user.account.id": _anthropic_style_account_id(acc),
        "terminal.type": term,
        "organization.id": org_id,
        "os.version": os.environ.get("SIM_OS_VERSION", platform.release()),
        "os.type": os.environ.get("SIM_OS_TYPE", platform.system().lower()),
        "host.arch": os.environ.get("SIM_HOST_ARCH", platform.machine()),
        "app.version": app_version,
        "sim.agent_tool_version": app_version,
        "sim.host.key": _stable_uuid("device:" + pod),
    }


def emit_claude_code_dashboard(
    profile: dict,
    session_id: str,
    input_tokens: int,
    output_tokens: int,
    active_cli_s: float,
    *,
    app_version: str | None = None,
    roster_user: dict | None = None,
    prompt: str | None = None,
    productivity_mult: float = 1.0,
) -> None:
    """Emit Claude-style **metrics** and **logs** (not extra spans) per Claude Code monitoring docs.

    Token alignment: Prometheus increments the full per-turn ``input`` / ``output`` / cache types once per
    label set. ``claude_code.api_request`` logs use the **same** totals: by default (``SIM_CLAUDE_API_REQUEST_LOG_PARTITIONS=1``)
    a single row carries full ``input_tokens`` / ``output_tokens`` / cache fields matching those metric increments.
    If ``SIM_CLAUDE_API_REQUEST_LOG_PARTITIONS`` > 1, totals are split randomly across that many rows; **sum**
    those columns over ``event_name=api_request`` (per pipeline) to recover the metric deltas. ``user_prompt``
    only has ``prompt_length`` (characters)—exclude it from token sums.
    """
    if profile["agent.product"] != "claude_code":
        return
    if not _env_bool("SIM_CLAUDE_CODE_METRICS", True):
        return

    input_tokens = int(input_tokens)
    output_tokens = int(output_tokens)

    cx_app = st.claude_cx_app
    cx_sub_flat = st.claude_cx_sub_flat
    cx_sub_dotted = st.claude_cx_sub_dotted
    if cx_app is None or cx_sub_flat is None or cx_sub_dotted is None:
        return

    cc_session = st.cc_session
    cc_token = st.cc_token
    cc_token_coralogix = st.cc_token_coralogix
    cc_cost = st.cc_cost
    cc_active = st.cc_active
    cc_loc = st.cc_loc
    cc_commit = st.cc_commit
    cc_pr = st.cc_pr
    cc_edit_decision = st.cc_edit_decision
    cc_session_repo = st.cc_session_repo
    log_provider = st.claude_primary_log_provider
    _cc_log_emitters = st.cc_log_emitters
    if (
        cc_session is None
        or cc_token is None
        or cc_cost is None
        or cc_active is None
        or cc_loc is None
        or cc_commit is None
        or cc_pr is None
        or cc_edit_decision is None
        or cc_session_repo is None
        or log_provider is None
        or not _cc_log_emitters
    ):
        return

    def _cc_inc(metric, lab: dict, amount: float | int) -> None:
        # Create the child even at 0 so a later increment shows up in ``increase()``.
        child = metric.labels(**lab)
        claude_session_track_series(session_id, metric, lab)
        if amount > 0:
            child.inc(amount)

    def _cc_token_inc(lb: dict, typ: str, model: str, amount: float | int) -> None:
        lab = {**lb, "type": typ, "model": model}
        _cc_inc(cc_token, lab, int(amount))
        if cc_token_coralogix is not None:
            _cc_inc(cc_token_coralogix, lab, int(amount))

    _ctp = _claude_resolved_telemetry_profile(session_id)
    turn_idx = claude_session_note_turn(session_id)
    turns_expected = claude_session_turns()
    model = claude_session_model_for_turn(session_id, max(0, turn_idx - 1))
    av_dotted = os.environ.get("SIM_CLAUDE_DOTTED_SERVICE_VERSION", "1.0.33").strip() or "1.0.33"
    _pin_metric_labels = _env_bool("SIM_CLAUDE_PIN_METRIC_LABELS", True)
    _pin_key = _claude_metric_label_pin_key(roster_user, session_id)
    if _pin_metric_labels:
        av_flat, _pinned_model = _claude_pinned_flat_version_and_model(_pin_key)
        # Prefer session model mix (c4c shape) over a single pinned model.
        if os.environ.get("SIM_CLAUDE_MODEL", "").strip():
            model = _pinned_model
    else:
        av_flat = app_version or os.environ.get("SIM_CC_APP_VERSION") or tool_version_for("claude_code")
    av_flat = claude_service_version_for_user(roster_user, session_id) or av_flat

    team_sub = claude_cx_subsystem_for_user(roster_user, session_id)
    if _ctp == "dotted":
        user_attrs = (
            _claude_user_identity_flavor(session_id, "dotted")
            if roster_user is None
            else _claude_otlp_span_user_attrs_from_roster(roster_user)
        )
        _apply_claude_dotted_email_domain(user_attrs)
        ver_s = av_dotted if _pin_metric_labels else (app_version or av_dotted)
        base = _cc_dashboard_base_attrs(
            session_id, user_attrs, ver_s, flavor="dotted", roster_user=roster_user
        )
        base_by_profile = {"dotted": base}
        base_prom = _cc_base_for_prometheus_labels(session_id, base)
        sub = team_sub or cx_sub_dotted
        lbs = [_map_cc_base_labels(base_prom, cx_app, sub)]
    else:
        user_attrs = (
            _claude_user_identity_flavor(session_id, "flat")
            if roster_user is None
            else dict(roster_user)
        )
        base = _cc_dashboard_base_attrs(
            session_id, user_attrs, av_flat, flavor="flat", roster_user=roster_user
        )
        base_by_profile = {"flat": base}
        base_prom = _cc_base_for_prometheus_labels(session_id, base)
        sub = team_sub or cx_sub_flat
        lbs = [_map_cc_base_labels(base_prom, cx_app, sub)]

    _cc_log_emitters = [
        emitter for emitter in st.cc_log_emitters if emitter[1] == _ctp
    ]
    if not _cc_log_emitters:
        return
    # When team subsystems are on, stamp log cx_subsystem to the same team label.
    if team_sub:
        _cc_log_emitters = [(lg, prof, team_sub) for lg, prof, _old in _cc_log_emitters]

    plan = claude_session_plan(session_id, roster_user)
    whale = is_sim_top_spender(roster_user)

    def _share(total: int) -> int:
        return claude_turn_share(total, turn_idx, turns_expected)

    tokens = {typ: _share(n) for typ, n in plan.tokens.items()}
    billable_input = tokens["input"]
    output_tokens = tokens["output"]
    est_cost = plan.cost_usd / turns_expected if turn_idx <= turns_expected else 0.0
    _cost_micro = int(round(float(est_cost) * 1_000_000))
    if _env_bool("SIM_CLAUDE_SESSION_ACTIVE_TIME_BUDGET", True):
        cli_s, user_s = claude_session_active_time_slice(session_id, turns_expected=turns_expected)
    else:
        prod = max(0.08, float(productivity_mult)) * random.uniform(0.75, 1.25)
        _at_floor = _env_float("SIM_CLAUDE_ACTIVE_TIME_MIN_S", 2.0)
        cli_s = max(_at_floor, max(1.0, float(active_cli_s)))
        user_s = max(_at_floor, max(1.0, random.uniform(0.5, 4.0) * prod))
    primary_model = claude_session_primary_model(session_id)

    for lb in lbs:
        if claude_should_inc_session_count(session_id):
            _cc_inc(cc_session, lb, 1)
        # Every session model gets its children on turn 1 so a later Haiku turn is not lost to ``increase()``.
        for session_model in claude_session_models(session_id):
            on = session_model == model
            for typ, amount in tokens.items():
                _cc_token_inc(lb, typ, session_model, amount if on else 0)
            _cc_inc(cc_cost, {**lb, "model": session_model}, est_cost if on else 0)
        # One active-time series per session; the FE sums both types anyway.
        _cc_inc(cc_active, {**lb, "type": "cli"}, cli_s + user_s)
        if plan.loc_added:
            _cc_inc(cc_loc, {**lb, "type": "added", "model": primary_model}, _share(plan.loc_added))
            _cc_inc(cc_loc, {**lb, "type": "removed", "model": primary_model}, _share(plan.loc_removed))
        if plan.commits:
            _cc_inc(cc_commit, lb, _share(plan.commits))
        if plan.prs:
            _cc_inc(cc_pr, lb, _share(plan.prs))
        for (tool_name, decision), n in plan.edit_decisions.items():
            _cc_inc(cc_edit_decision, {**lb, "tool_name": tool_name, "decision": decision}, _share(n))

    # The rogue whale always tags its repos so its spend lands in the unmanaged-repo join.
    if turn_idx == 1 and (is_sim_rogue_user(roster_user) or claude_session_should_emit_repo(session_id)):
        repo_names = sim_session_repository_names(
            session_id,
            roster_user,
            agent_product="claude_code",
        )
        for lb in lbs:
            for repo_name in repo_names:
                lab = {**lb, "repository_name": repo_name}
                cc_session_repo.labels(**lab).set(1)
                claude_session_track_series(session_id, cc_session_repo, lab)

    seq = 0

    def _emit_log(record_body: str, *, event_name: str, ts_ns: int, **fields: object) -> None:
        nonlocal seq
        seq += 1
        for lg, prof, cx_sub in _cc_log_emitters:
            b = base_by_profile[prof]
            attrs = _cc_claude_log_record_attrs(
                b,
                event_name=event_name,
                event_sequence=seq,
                event_timestamp_iso=_iso_z(ts_ns),
                cx_app=cx_app,
                cx_sub=cx_sub,
                extra=dict(fields),
                profile=prof,
            )
            rec = LogRecord(
                timestamp=ts_ns,
                trace_id=0,
                span_id=0,
                trace_flags=TraceFlags.get_default(),
                severity_number=SeverityNumber.INFO,
                severity_text="INFO",
                body=record_body,
                attributes=attrs,
                resource=lg.resource,
            )
            lg.emit(rec)

    up_prompt = prompt or claude_prompt_for_session(session_id)
    # Re-prompt churn sessions send several user prompts in one turn.
    for _ in range(_share(plan.user_prompts)):
        _emit_log(
            "claude_code.user_prompt",
            event_name="user_prompt",
            ts_ns=time.time_ns(),
            prompt=up_prompt,
            prompt_length=len(up_prompt),
        )

    # One log row per turn by default so token fields match Prometheus increments without summing partitions.
    n_api = max(1, _env_int("SIM_CLAUDE_API_REQUEST_LOG_PARTITIONS", 1))
    inp_parts = _partition_nonneg_repair(billable_input, n_api)
    out_parts = _partition_nonneg_repair(output_tokens, n_api)
    cr_parts = _partition_nonneg_repair(tokens["cacheRead"], n_api)
    cc_parts = _partition_nonneg_repair(tokens["cacheCreation"], n_api)
    cost_micro_parts = _partition_nonneg_repair(_cost_micro, n_api)
    for i in range(n_api):
        dur = random.randint(120, 9000)
        api_ts = time.time_ns()
        in_tok = inp_parts[i]
        out_tok = out_parts[i]
        cr_tok = cr_parts[i]
        cc_tok = cc_parts[i]
        _emit_log(
            "claude_code.api_request",
            event_name="api_request",
            ts_ns=api_ts,
            prompt_id=str(uuid.uuid4()),
            request_id=_cc_api_request_id(),
            model=model,
            duration_ms=dur,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_read_tokens=cr_tok,
            cache_creation_tokens=cc_tok,
            cost_usd=cost_micro_parts[i] / 1_000_000.0,
            cost_usd_micros=cost_micro_parts[i],
            effort=_cc_effort(whale),
            query_source=random.choice(["repl_main_thread", "repl_main_thread", "sdk"]),
            speed=random.choice(["fast", "normal"]),
        )

    if _env_bool("SIM_CLAUDE_EMIT_API_RESPONSE_BODY", True):
        reply = claude_assistant_reply_for_session(session_id)
        body_json = claude_api_response_body_json(reply)
        _emit_log(
            "claude_code.api_response_body",
            event_name="api_response_body",
            ts_ns=time.time_ns(),
            model=model,
            body=body_json,
            body_length=len(body_json),
        )

    _CC_TOOL_ERRS = (
        "EISDIR: illegal operation on a directory, read '/Users/dev/project/src/module'",
        "ENOENT: no such file or directory, open '/tmp/missing'",
        "exit status 1",
        "timeout",
    )
    n_tools = random.randint(6, 14)
    for _ in range(n_tools):
        tname = random.choice(_CC_TOOLS)
        ok = random.random() > 0.06
        tr_ts = time.time_ns()
        dur_ms = random.randint(2, 120_000) if ok else random.randint(2, 500)
        fields_tr: dict[str, object] = {
            "tool_name": tname,
            "success": "true" if ok else "false",
            "duration_ms": dur_ms,
            "use_id": _cc_tool_use_id(),
            "tool_result_size_bytes": random.randint(64, 500_000),
            "decision_type": (
                random.choice(["accept", "reject"])
                if tname in ("Edit", "Write", "NotebookEdit")
                else "accept"
            ),
            "decision_source": random.choice(_DECISION_SOURCES),
        }
        if not ok:
            fields_tr["error"] = random.choice(_CC_TOOL_ERRS)
        _emit_log("claude_code.tool_result", event_name="tool_result", ts_ns=tr_ts, **fields_tr)

    if random.random() < 0.08:
        err_ts = time.time_ns()
        _emit_log(
            "claude_code.api_error",
            event_name="api_error",
            ts_ns=err_ts,
            model=model,
            error="rate_limit_exceeded",
            status_code="429",
            duration_ms=random.randint(100, 8000),
            attempt=random.randint(1, 4),
            speed=random.choice(["fast", "normal"]),
        )
