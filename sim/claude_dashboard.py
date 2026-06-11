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

from sim.claude_logs import _cc_claude_log_record_attrs
from sim.claude_meta import _claude_telemetry_profile
from sim.claude_repos import claude_session_repository_names
from sim.common import (
    _anthropic_style_account_id,
    _cc_base_for_prometheus_labels,
    _map_cc_base_labels,
    _random_partition_nonneg,
    _stable_uuid,
    _cc_api_request_id,
    _cc_tool_use_id,
    tool_version_for,
)
from sim.constants import (
    claude_api_response_body_json,
    claude_assistant_reply_for_session,
    claude_prompt_for_session,
)
from sim.env import _env_bool, _env_float, _env_int
from sim.identity import (
    _apply_claude_dotted_email_domain,
    _claude_metric_label_pin_key,
    _claude_otlp_span_user_attrs_from_roster,
    _claude_pinned_flat_version_and_model,
    _claude_roster_core_user,
    _claude_user_identity_flavor,
)
from sim.state import st

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
_LANGS = (
    "TypeScript",
    "Python",
    "JavaScript",
    "Go",
    "Rust",
    "Markdown",
    "YAML",
    "JSON",
)
_DECISION_SOURCES = (
    "user_permanent",
    "user_temporary",
    "config",
    "hook",
    "user_reject",
    "user_abort",
)

# When ``SIM_CLAUDE_PIN_METRIC_LABELS`` is true: split each turn's ``n_decisions`` across this **fixed**
# set only (bounded series count ≈ len × ``lbs``). Weights skew toward Edit like ``_CC_TOOLS``.
_CC_PINNED_EDIT_LABEL_MIX: tuple[tuple[tuple[str, str, str, str], int], ...] = (
    (("Edit", "accept", "config", "TypeScript"), 24),
    (("Edit", "accept", "hook", "Python"), 10),
    (("Edit", "reject", "user_reject", "TypeScript"), 6),
    (("Write", "accept", "config", "Python"), 18),
    (("Write", "accept", "user_permanent", "JavaScript"), 8),
    (("Write", "reject", "user_abort", "Python"), 4),
    (("NotebookEdit", "accept", "config", "Markdown"), 6),
    (("NotebookEdit", "accept", "hook", "JSON"), 3),
    (("NotebookEdit", "reject", "user_reject", "Markdown"), 3),
)


def _cc_weighted_partition_nonneg(total: int, weights: tuple[int, ...]) -> tuple[int, ...]:
    """Split ``total`` across ``len(weights)`` bins proportional to weights (exact sum)."""
    wt = tuple(max(0, int(w)) for w in weights)
    s = sum(wt)
    if s == 0:
        return tuple(0 for _ in wt)
    exact = [total * w / s for w in wt]
    floors = [int(x) for x in exact]
    rem = int(total) - sum(floors)
    frac_order = sorted(range(len(wt)), key=lambda i: exact[i] - floors[i], reverse=True)
    for j in range(rem):
        floors[frac_order[j]] += 1
    return tuple(floors)


def _iso_z(ts_ns: int) -> str:
    return datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _partition_nonneg_repair(total: int, n: int) -> list[int]:
    """Split ``total`` into ``n`` nonnegative integers summing to ``total`` (random; exact sum)."""
    parts = _random_partition_nonneg(total, n)
    diff = int(total) - sum(parts)
    if diff != 0 and parts:
        parts[-1] += diff
    return parts


def _cc_dashboard_base_attrs(session_id: str, user_attrs: dict, app_version: str, *, flavor: str) -> dict:
    pod = os.environ.get("POD_NAME") or os.environ.get("HOSTNAME") or "sim-pod"
    svc = os.environ.get("SIM_CLAUDE_SERVICE_NAME", "claude-code")
    acc = str(user_attrs.get("user.account_uuid", ""))
    if flavor == "dotted":
        org_id = (
            os.environ.get("SIM_CLAUDE_DOTTED_ORGANIZATION_ID", "").strip()
            or os.environ.get("SIM_ORGANIZATION_ID", "").strip()
            or "org_coralogix_lab_002"
        )
        term_default = "alacritty"
    else:
        org_id = os.environ.get(
            "SIM_ORGANIZATION_ID",
            str(uuid.uuid5(uuid.NAMESPACE_URL, "coralogix:sim:organization")),
        )
        term_default = "vscode"
    return {
        "session.id": session_id,
        **user_attrs,
        "service.name": svc,
        "service.version": app_version,
        "user.account.id": _anthropic_style_account_id(acc),
        "terminal.type": os.environ.get("SIM_TERMINAL_TYPE", term_default),
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
        or cc_token_coralogix is None
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

    def _cc_token_inc(lb: dict, typ: str, model: str, amount: float | int) -> None:
        amt = int(amount)
        lab = {**lb, "type": typ, "model": model}
        cc_token.labels(**lab).inc(amt)
        cc_token_coralogix.labels(**lab).inc(amt)

    _ctp = _claude_telemetry_profile()
    av_dotted = os.environ.get("SIM_CLAUDE_DOTTED_SERVICE_VERSION", "1.0.33").strip() or "1.0.33"
    _pin_metric_labels = _env_bool("SIM_CLAUDE_PIN_METRIC_LABELS", True)
    _pin_key = _claude_metric_label_pin_key(roster_user, session_id)
    if _pin_metric_labels:
        av_flat, model = _claude_pinned_flat_version_and_model(_pin_key)
    else:
        av_flat = app_version or os.environ.get("SIM_CC_APP_VERSION") or tool_version_for("claude_code")
        model = profile["gen_ai.request.model"]

    if _ctp == "both":
        core = dict(roster_user) if roster_user is not None else _claude_roster_core_user(session_id)
        user_flat = dict(core)
        user_dotted = dict(core)
        _apply_claude_dotted_email_domain(user_dotted)
        base_flat = _cc_dashboard_base_attrs(session_id, user_flat, av_flat, flavor="flat")
        base_dotted = _cc_dashboard_base_attrs(session_id, user_dotted, av_dotted, flavor="dotted")
        base_by_profile = {"flat": base_flat, "dotted": base_dotted}
        _bps = (
            (_cc_base_for_prometheus_labels(session_id, base_flat), cx_sub_flat),
            (_cc_base_for_prometheus_labels(session_id, base_dotted), cx_sub_dotted),
        )
        lbs = [_map_cc_base_labels(bp[0], cx_app, bp[1]) for bp in _bps]
    elif _ctp == "dotted":
        user_attrs = (
            _claude_user_identity_flavor(session_id, "dotted")
            if roster_user is None
            else _claude_otlp_span_user_attrs_from_roster(roster_user)
        )
        ver_s = av_dotted if _pin_metric_labels else (app_version or av_dotted)
        base = _cc_dashboard_base_attrs(session_id, user_attrs, ver_s, flavor="dotted")
        base_by_profile = {"dotted": base}
        base_prom = _cc_base_for_prometheus_labels(session_id, base)
        lbs = [_map_cc_base_labels(base_prom, cx_app, cx_sub_dotted)]
    else:
        user_attrs = (
            _claude_user_identity_flavor(session_id, "flat")
            if roster_user is None
            else dict(roster_user)
        )
        base = _cc_dashboard_base_attrs(session_id, user_attrs, av_flat, flavor="flat")
        base_by_profile = {"flat": base}
        base_prom = _cc_base_for_prometheus_labels(session_id, base)
        lbs = [_map_cc_base_labels(base_prom, cx_app, cx_sub_flat)]

    pair_tok = int(input_tokens) + int(output_tokens)
    cr_prob = _env_float("SIM_CLAUDE_CACHE_READ_PROB", 0.92)
    cache_read_amt: int | None = None
    if random.random() < cr_prob:
        cr_lo = max(5000, int(pair_tok * _env_float("SIM_CLAUDE_CACHE_READ_FRAC_MIN", 0.008)))
        cr_hi = max(cr_lo + 1, int(pair_tok * _env_float("SIM_CLAUDE_CACHE_READ_FRAC_MAX", 0.15)))
        cr_hi = min(cr_hi, int(pair_tok * 3) + 1)
        cache_read_amt = random.randint(cr_lo, cr_hi)
    ccn_prob = _env_float("SIM_CLAUDE_CACHE_CREATION_PROB", 0.45)
    cache_creation_amt: int | None = None
    if random.random() < ccn_prob:
        cc_lo = max(500, int(pair_tok * _env_float("SIM_CLAUDE_CACHE_CREATION_FRAC_MIN", 0.0005)))
        cc_hi = max(cc_lo + 1, int(pair_tok * _env_float("SIM_CLAUDE_CACHE_CREATION_FRAC_MAX", 0.04)))
        cache_creation_amt = random.randint(cc_lo, cc_hi)

    # Single canonical USD total (micro-dollars) for ``cc_cost`` and log ``cost_usd`` rows (exact sum).
    from sim.model_pricing import estimate_llm_cost_usd

    _est_cost_raw = round(
        estimate_llm_cost_usd(
            model,
            input_tokens,
            output_tokens,
            cache_read_tokens=int(cache_read_amt or 0),
            cache_creation_tokens=int(cache_creation_amt or 0),
            jitter_usd=random.uniform(0.001, 0.02),
        ),
        6,
    )
    _cost_micro = int(round(float(_est_cost_raw) * 1_000_000))
    est_cost = _cost_micro / 1_000_000.0
    _at_floor = _env_float("SIM_CLAUDE_ACTIVE_TIME_MIN_S", 2.0)
    prod = max(0.08, float(productivity_mult)) * random.uniform(0.75, 1.25)
    cli_s = max(_at_floor, max(1.0, float(active_cli_s)))
    user_s = max(_at_floor, max(1.0, random.uniform(0.5, 4.0) * prod))

    loc_a0 = _env_int("SIM_CLAUDE_LOC_ADDED_MIN", 2)
    loc_a1 = _env_int("SIM_CLAUDE_LOC_ADDED_MAX", 72)
    loc_lo, loc_hi = min(loc_a0, loc_a1), max(loc_a0, loc_a1)
    loc_span = max(1, loc_hi - loc_lo + 1)
    added = max(0, int(round(random.randint(loc_lo, loc_hi) * prod * random.uniform(0.6, 1.4))))
    if added == 0 and random.random() < min(0.92, 0.35 + prod * 0.4):
        added = random.randint(max(1, loc_lo), max(loc_lo, min(loc_hi, loc_lo + loc_span // 3)))
    added = max(1, added)
    rem_cap = _env_int("SIM_CLAUDE_LOC_REMOVED_MAX", 48)
    removed = random.randint(0, min(added - 1, rem_cap, max(0, added // 2)))
    c0, c1 = _env_int("SIM_CLAUDE_COMMITS_MIN", 0), _env_int("SIM_CLAUDE_COMMITS_MAX", 1)
    p0, p1 = _env_int("SIM_CLAUDE_PRS_MIN", 0), _env_int("SIM_CLAUDE_PRS_MAX", 1)
    commit_hi = max(c0, c1, int(round(max(c0, c1) * prod * random.uniform(0.5, 1.5))))
    pr_hi = max(p0, p1, int(round(max(p0, p1) * prod * random.uniform(0.5, 1.5))))
    commit_delta = random.randint(min(c0, c1), commit_hi) if commit_hi > 0 else 0
    pr_delta = random.randint(min(p0, p1), pr_hi) if pr_hi > 0 and random.random() < min(0.95, 0.25 + prod * 0.55) else 0
    n_decisions = max(
        1,
        int(
            round(
                random.randint(
                    _env_int("SIM_CLAUDE_EDIT_DECISIONS_MIN", 6),
                    _env_int("SIM_CLAUDE_EDIT_DECISIONS_MAX", 36),
                )
                * prod
                * random.uniform(0.65, 1.35),
            ),
        ),
    )
    accept_rate = float(os.environ.get("SIM_TOOL_ACCEPT_RATE", "0.78"))
    edit_rows: list[tuple[str, str, str, str]] = []
    for _ in range(n_decisions):
        tool_name = random.choice(["Edit", "Write", "NotebookEdit"])
        decision = "accept" if random.random() < accept_rate else "reject"
        edit_rows.append(
            (tool_name, decision, random.choice(_DECISION_SOURCES), random.choice(_LANGS)),
        )

    for lb in lbs:
        cc_session.labels(**lb).inc()
        _cc_token_inc(lb, "input", model, input_tokens)
        _cc_token_inc(lb, "output", model, output_tokens)
        if cache_read_amt is not None:
            _cc_token_inc(lb, "cacheRead", model, cache_read_amt)
        if cache_creation_amt is not None:
            _cc_token_inc(lb, "cacheCreation", model, cache_creation_amt)
        cc_cost.labels(**{**lb, "model": model}).inc(est_cost)
        cc_active.labels(**{**lb, "type": "cli"}).inc(cli_s)
        cc_active.labels(**{**lb, "type": "user"}).inc(user_s)
        cc_loc.labels(**{**lb, "type": "added"}).inc(added)
        cc_loc.labels(**{**lb, "type": "removed"}).inc(removed)
        cc_commit.labels(**lb).inc(commit_delta)
        cc_pr.labels(**lb).inc(pr_delta)
        if _pin_metric_labels:
            _mix = _CC_PINNED_EDIT_LABEL_MIX
            _counts = _cc_weighted_partition_nonneg(n_decisions, tuple(w for _, w in _mix))
            for (tool_name, decision, src, lang), k in zip((t for t, _ in _mix), _counts):
                if k:
                    cc_edit_decision.labels(
                        **{
                            **lb,
                            "tool_name": tool_name,
                            "decision": decision,
                            "source": src,
                            "language": lang,
                        }
                    ).inc(k)
        else:
            for tool_name, decision, src, lang in edit_rows:
                cc_edit_decision.labels(
                    **{
                        **lb,
                        "tool_name": tool_name,
                        "decision": decision,
                        "source": src,
                        "language": lang,
                    }
                ).inc()

    repo_names = claude_session_repository_names(session_id, roster_user)
    for lb in lbs:
        # Use the same ``session_id`` label set as ``claude_code_cost_usage_USD`` (``lb``). When
        # ``SIM_CLAUDE_METRICS_SESSION_ID_ALIGN_LOGS=true``, that matches OTLP log ``session.id``;
        # otherwise counters/repo use bucketed ids and logs stay on the raw trace id.
        for repo_name in repo_names:
            cc_session_repo.labels(**{**lb, "repository_name": repo_name}).set(1)

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

    up_ts = time.time_ns()
    up_prompt = prompt or claude_prompt_for_session(session_id)
    _emit_log(
        "claude_code.user_prompt",
        event_name="user_prompt",
        ts_ns=up_ts,
        prompt=up_prompt,
        prompt_length=len(up_prompt),
    )

    # One log row per turn by default so token fields match Prometheus increments without summing partitions.
    n_api = max(1, _env_int("SIM_CLAUDE_API_REQUEST_LOG_PARTITIONS", 1))
    inp_parts = _partition_nonneg_repair(input_tokens, n_api)
    out_parts = _partition_nonneg_repair(output_tokens, n_api)
    cr_tot = int(cache_read_amt) if cache_read_amt is not None else 0
    cc_tot = int(cache_creation_amt) if cache_creation_amt is not None else 0
    cr_parts = _partition_nonneg_repair(cr_tot, n_api)
    cc_parts = _partition_nonneg_repair(cc_tot, n_api)
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
            effort=random.choice(["high", "medium", "high"]),
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

    log_provider.force_flush(timeout_millis=30000)
    if st.claude_dotted_log_provider is not None:
        st.claude_dotted_log_provider.force_flush(timeout_millis=30000)
