"""Gemini CLI simulator: spans, OTLP logs, Prometheus (standard label set)."""
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

from sim.common import _gen_ai_dashboard_llm_span_attributes, _stable_uuid, tool_version_for
from sim.constants import GEMINI_AGENT_DESCRIPTION, GEMINI_CLI_MODELS, GEMINI_SAMPLE_PROMPTS
from sim.env import _env_bool, _env_float, _env_int
from sim.identity import random_coralogix_identity
from sim.state import st

log = logging.getLogger(__name__)

def _gemini_metric_label_shape() -> str:
    """
    ``standard`` (default): Prometheus label names aligned with ``metrics__metric_details`` for
    ``gemini_cli_*_total`` (productivity counters join dashboards; extended sim-only labels removed).

    ``extended``: legacy sim dimensions (``user_id``, ``mimetype`` / ``extension`` on file ops, ``http_status`` on API).
    """
    v = os.environ.get("SIM_GEMINI_METRIC_LABEL_SHAPE", "standard").strip().lower()
    return "extended" if v == "extended" else "standard"


def _gemini_otel_sdk_version() -> str:
    try:
        return importlib.metadata.version("opentelemetry-sdk")
    except Exception:
        return os.environ.get("SIM_TELEMETRY_SDK_VERSION", "1.41.0").strip() or "1.41.0"


def _gemini_standard_prom_static(*, tool_version: str) -> dict[str, str]:
    """Label values shared across standard Gemini counters/histograms."""
    return {
        "job": os.environ.get("SIM_GEMINI_PROMETHEUS_JOB", "gemini-cli").strip() or "gemini-cli",
        "service_name": os.environ.get("SIM_GEMINI_SERVICE_NAME", "gemini-cli").strip() or "gemini-cli",
        "service_version": os.environ.get("SIM_GEMINI_SERVICE_VERSION", "").strip() or tool_version,
        "host_arch": os.environ.get("SIM_GEMINI_PROM_HOST_ARCH", "").strip()
        or os.environ.get("SIM_HOST_ARCH", "").strip()
        or "x86_64",
        "os_type": os.environ.get("SIM_OS_TYPE", "linux").strip() or "linux",
        "os_version": os.environ.get("SIM_OS_VERSION", "6.1.163-186.299.amzn2023.x86_64").strip(),
        "telemetry_sdk_language": "python",
        "telemetry_sdk_name": "opentelemetry",
        "telemetry_sdk_version": _gemini_otel_sdk_version(),
    }


# Label *names* for Gemini CLI counters (``metrics__metric_details`` style).
_GEM_STANDARD_SESSION_L = (
    "active_approval_mode",
    "cx_application_name",
    "cx_subsystem_name",
    "host_arch",
    "installation_id",
    "job",
    "os_type",
    "os_version",
    "service_name",
    "service_version",
    "session_id",
    "telemetry_sdk_language",
    "telemetry_sdk_name",
    "telemetry_sdk_version",
    "user_email",
)
# google-gemini/gemini-cli ``packages/core/src/telemetry/metrics.ts``: ``gemini_cli.agent.duration`` histogram.
_GEM_STANDARD_AGENT_DUR_L = _GEM_STANDARD_SESSION_L + ("agent_name",)
# Same source: ``gemini_cli.agent.run.count`` counter (+ ``terminate_reason``).
_GEM_STANDARD_AGENT_RUN_L = _GEM_STANDARD_SESSION_L + ("agent_name", "terminate_reason")
_GEM_STANDARD_TOKEN_L = (
    "active_approval_mode",
    "cx_application_name",
    "cx_subsystem_name",
    "host_arch",
    "installation_id",
    "job",
    "model",
    "os_type",
    "os_version",
    "service_name",
    "service_version",
    "session_id",
    "telemetry_sdk_language",
    "telemetry_sdk_name",
    "telemetry_sdk_version",
    "type",
    "user_email",
)
_GEM_STANDARD_API_L = (
    "active_approval_mode",
    "cx_application_name",
    "cx_subsystem_name",
    "host_arch",
    "installation_id",
    "job",
    "model",
    "os_type",
    "os_version",
    "service_name",
    "service_version",
    "session_id",
    "status_code",
    "telemetry_sdk_language",
    "telemetry_sdk_name",
    "telemetry_sdk_version",
    "user_email",
)
_GEM_STANDARD_HIST_API_L = (
    "cx_application_name",
    "cx_subsystem_name",
    "host_arch",
    "job",
    "model",
    "os_type",
    "os_version",
    "service_name",
    "service_version",
    "telemetry_sdk_language",
    "telemetry_sdk_name",
    "telemetry_sdk_version",
)
_GEM_STANDARD_HIST_TOOL_L = (
    "cx_application_name",
    "cx_subsystem_name",
    "function_name",
    "host_arch",
    "job",
    "os_type",
    "os_version",
    "service_name",
    "service_version",
    "telemetry_sdk_language",
    "telemetry_sdk_name",
    "telemetry_sdk_version",
)
_GEM_STANDARD_LINES_L = (
    "active_approval_mode",
    "cx_application_name",
    "cx_subsystem_name",
    "function_name",
    "host_arch",
    "installation_id",
    "job",
    "os_type",
    "os_version",
    "programming_language",
    "service_name",
    "service_version",
    "session_id",
    "telemetry_sdk_language",
    "telemetry_sdk_name",
    "telemetry_sdk_version",
    "type",
    "user_email",
)
_GEM_STANDARD_FILE_L = (
    "active_approval_mode",
    "cx_application_name",
    "cx_subsystem_name",
    "host_arch",
    "installation_id",
    "job",
    "operation",
    "os_type",
    "os_version",
    "programming_language",
    "service_name",
    "service_version",
    "session_id",
    "telemetry_sdk_language",
    "telemetry_sdk_name",
    "telemetry_sdk_version",
    "user_email",
)
_GEM_STANDARD_TOOL_L = (
    "active_approval_mode",
    "cx_application_name",
    "cx_subsystem_name",
    "decision",
    "function_name",
    "host_arch",
    "installation_id",
    "job",
    "os_type",
    "os_version",
    "service_name",
    "service_version",
    "session_id",
    "success",
    "telemetry_sdk_language",
    "telemetry_sdk_name",
    "telemetry_sdk_version",
    "tool_type",
    "user_email",
)

# Subset of experiment ids from real Gemini CLI exports (Coralogix facets).
_GEMINI_SAMPLE_EXPERIMENT_IDS = (
    106018688,
    105979536,
    105979558,
    106015335,
    105979579,
    105842106,
    105995634,
    106100625,
    105640665,
    104638466,
    105704191,
)

# ``gemini_cli.api_error`` — ``error_type`` mirrors gRPC-style surfaces common on Gemini / Google APIs.
_GEMINI_CLI_API_ERROR_PROFILES: tuple[tuple[str, int, str], ...] = (
    ("RESOURCE_EXHAUSTED", 429, "Quota exceeded; retry after backoff"),
    ("DEADLINE_EXCEEDED", 504, "Request timed out waiting for model response"),
    ("INVALID_ARGUMENT", 400, "Malformed request payload or unsupported parameter"),
    ("PERMISSION_DENIED", 403, "API key lacks permission for this model"),
    ("UNAUTHENTICATED", 401, "Invalid or revoked API credentials"),
    ("UNAVAILABLE", 503, "Model endpoint temporarily unavailable"),
    ("FAILED_PRECONDITION", 412, "Model version or tool precondition not met"),
    ("ABORTED", 409, "Concurrent modification; request aborted"),
)

def _gemini_installation_id() -> str:
    return os.environ.get(
        "SIM_GEMINI_INSTALLATION_ID",
        _stable_uuid(os.environ.get("POD_NAME", socket.gethostname()) + ":gemini-cli-install"),
    )


# Pinned file/lines/tool dimensions per user (``SIM_GEMINI_PIN_METRIC_LABELS``) — low churn for PromQL.
st.gem_metric_pins: dict[str, tuple[str, str, str, str, str, str]] = {}
# ``conversation_id`` → ``gen_ai.request.model`` when ``SIM_GEMINI_MODEL`` is unset (stable for session lifetime).
st.gem_session_models: dict[str, str] = {}
# Pool for ``_gemini_model_for_conversation``: ``sim.constants.GEMINI_CLI_MODELS``.
# ``SIM_GEMINI_CONCURRENT_LONG_SESSIONS`` independent slots: each holds one roster user until its deadline
# (``SIM_GEMINI_LONG_SESSION_SEC`` or Claude fallback) so many long-lived Gemini sessions overlap in time.
st.gem_slot_users: list[dict | None] = []
st.gem_slot_deadlines: list[float] = []
st.gem_slot_rr: int = 0
# EWMA (lines added, lines removed) per ``_gemini_metric_pin_key`` when ``SIM_GEMINI_SMOOTH_PRODUCTIVITY`` is on.
st.gem_loc_ema: dict[str, tuple[float, float]] = {}


def _gemini_stable_session_id_from_user_attrs(user_attrs: dict) -> str:
    """Deterministic ``session_id`` label / span session for a roster identity (stable across restarts)."""
    acc = str(user_attrs.get("user.account_uuid", "")).strip()
    uid = str(user_attrs.get("user.id", "")).strip()
    key = acc or uid or "unknown-user"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "otel-ai-agent-sim:gemini:session-per-user:" + key))


def _gemini_roster_user_for_emit() -> dict:
    """
    Pick roster row for Gemini.

    When ``SIM_GEMINI_LONG_SESSION_SEC`` and ``SIM_CLAUDE_LONG_SESSION_SEC`` are both unset / zero:
    each emit uses a fresh roster draw (short-lived identity).

    When the effective duration ``dur`` > 0 (``SIM_GEMINI_LONG_SESSION_SEC``, else ``SIM_CLAUDE_LONG_SESSION_SEC``):
    keep **up to** ``SIM_GEMINI_CONCURRENT_LONG_SESSIONS`` (default 18) slots in parallel. Each slot pins
    one user for ``dur`` seconds independently, and each emit picks a slot (``random`` or ``round_robin``
    via ``SIM_GEMINI_SESSION_SLOT_STRATEGY``). That yields **multiple overlapping long sessions** instead
    of a single global Gemini user.
    """
    dur = _env_float(
        "SIM_GEMINI_LONG_SESSION_SEC",
        _env_float("SIM_CLAUDE_LONG_SESSION_SEC", 0.0),
    )
    if dur <= 0:
        return _claude_roster_core_user(str(uuid.uuid4()))

    n_slots = max(1, _env_int("SIM_GEMINI_CONCURRENT_LONG_SESSIONS", 18))
    if len(st.gem_slot_users) != n_slots:
        st.gem_slot_users = [None] * n_slots
        st.gem_slot_deadlines = [0.0] * n_slots
        st.gem_slot_rr = 0

    strat = os.environ.get("SIM_GEMINI_SESSION_SLOT_STRATEGY", "random").strip().lower().replace("-", "_")
    if strat in ("round_robin", "rr"):
        i = st.gem_slot_rr % n_slots
        st.gem_slot_rr += 1
    else:
        i = random.randrange(n_slots)

    now = time.monotonic()
    if st.gem_slot_users[i] is None or now >= st.gem_slot_deadlines[i]:
        st.gem_slot_users[i] = _claude_roster_core_user(str(uuid.uuid4()))
        st.gem_slot_deadlines[i] = now + float(dur)
    return dict(st.gem_slot_users[i])


def _gemini_user_attrs_from_roster(roster_user: dict) -> dict:
    """Roster user on Gemini spans/metrics; dotted Claude profile normalizes to @coralogix.com."""
    d = dict(roster_user)
    if _claude_telemetry_profile() == "dotted":
        _apply_claude_dotted_email_domain(d)
    return d


def _gemini_metric_pin_key(user_attrs: dict, conversation_id: str) -> str:
    rk = str(user_attrs.get("user.account_uuid", "")).strip() or str(user_attrs.get("user.id", "")).strip()
    return rk or conversation_id.strip() or "unknown-gemini-pin"


def _gemini_model_for_conversation(conversation_id: str) -> str:
    """
    Gemini ``gen_ai.request.model`` for this session.

    If ``SIM_GEMINI_MODEL`` is set, it applies to every session. Otherwise the first emit for a given
    ``conversation_id`` draws uniformly from ``GEMINI_CLI_MODELS`` and that choice is cached for the
    lifetime of the process (same id → same model on every subsequent turn).
    """
    fixed = os.environ.get("SIM_GEMINI_MODEL", "").strip()
    if fixed:
        return fixed
    cid = conversation_id.strip() or "unknown-session"
    got = st.gem_session_models.get(cid)
    if got is not None:
        return got
    got = random.choice(GEMINI_CLI_MODELS)
    st.gem_session_models[cid] = got
    return got


def _gemini_prometheus_session_id(user_attrs: dict, conversation_id: str) -> str:
    """Prometheus ``session_id`` label (and span ``gen_ai.session.id`` when caller passes this value)."""
    if not _env_bool("SIM_GEMINI_STABLE_SESSION_PER_USER", True):
        return conversation_id.strip() or str(uuid.uuid4())
    return _gemini_stable_session_id_from_user_attrs(user_attrs)


def _gemini_pinned_dims(pin_key: str) -> tuple[str, str, str, str, str, str]:
    """
    Return ``(function_name, programming_language, mimetype, extension, active_approval_mode, tool_function_name)``
    for ``gemini_cli_lines_changed`` / file ops / tool metrics (telemetry.md + standard label shapes).
    """
    tool_choices = ("write_file", "search_replace", "read_file", "run_shell_command")
    if not _env_bool("SIM_GEMINI_PIN_METRIC_LABELS", True):
        pl = random.choice(("TypeScript", "Python", "Go", "Rust", "JSON", "Markdown", "YAML", "Java"))
        ext_map = {
            "TypeScript": ("ts", "text/typescript"),
            "Python": ("py", "text/x-python"),
            "Go": ("go", "text/x-go"),
            "Rust": ("rs", "text/rust"),
            "JSON": ("json", "application/json"),
            "Markdown": ("md", "text/markdown"),
            "YAML": ("yaml", "text/yaml"),
            "Java": ("java", "text/x-java"),
        }
        ext, mime = ext_map[pl]
        return (
            random.choice(("edit_file", "write_file")),
            pl,
            mime,
            ext,
            random.choice(("auto", "full-auto", "plan", "suggest")),
            random.choice(tool_choices),
        )
    got = st.gem_metric_pins.get(pin_key)
    if got is not None:
        return got
    ap = os.environ.get("SIM_GEMINI_ACTIVE_APPROVAL_MODE", "").strip() or random.choice(
        ("auto", "full-auto", "plan", "suggest")
    )
    fn = os.environ.get("SIM_GEMINI_LINES_FUNCTION_NAME", "").strip() or random.choice(("edit_file", "write_file"))
    pl = os.environ.get("SIM_GEMINI_PROGRAMMING_LANGUAGE", "").strip() or random.choice(
        ("TypeScript", "Python", "Go", "Rust", "JSON", "Markdown")
    )
    ext_m = {
        "TypeScript": ("ts", "text/typescript"),
        "Python": ("py", "text/x-python"),
        "Go": ("go", "text/x-go"),
        "Rust": ("rs", "text/rust"),
        "JSON": ("json", "application/json"),
        "Markdown": ("md", "text/markdown"),
    }.get(pl, ("ts", "text/typescript"))
    ext, mime = ext_m
    tfn = os.environ.get("SIM_GEMINI_TOOL_FUNCTION_NAME", "").strip() or random.choice(tool_choices)
    got = (fn, pl, mime, ext, ap, tfn)
    st.gem_metric_pins[pin_key] = got
    return got


def _gemini_lines_changed_deltas(pin_key: str, out_tokens: int) -> tuple[int, int]:
    """
    Lines added/removed for ``gemini_cli_lines_changed_total``.

    When ``SIM_GEMINI_SMOOTH_PRODUCTIVITY`` (default true): narrow raw draw + EWMA per user pin so
    ``rate()`` / ``increase()`` over an hour looks closer to steady production-like telemetry than
    wide ``out_tokens * uniform(0.02, 0.14)`` spikes. Set ``SIM_GEMINI_SMOOTH_PRODUCTIVITY=false``
    to restore the older high-variance behavior.
    """
    if not _env_bool("SIM_GEMINI_SMOOTH_PRODUCTIVITY", True):
        frac_lo = _env_float("SIM_GEMINI_LOC_FRAC_MIN", 0.02)
        frac_hi = max(frac_lo + 1e-6, _env_float("SIM_GEMINI_LOC_FRAC_MAX", 0.14))
        la = max(1, int(out_tokens * random.uniform(frac_lo, frac_hi)))
        lr = random.randint(0, max(1, la // 4))
        return la, lr

    frac_lo = _env_float("SIM_GEMINI_LOC_FRAC_MIN", 0.042)
    frac_hi = max(frac_lo + 1e-6, _env_float("SIM_GEMINI_LOC_FRAC_MAX", 0.062))
    noise = random.uniform(0.94, 1.06)
    raw_add = max(6, int(out_tokens * random.uniform(frac_lo, frac_hi) * noise))
    rem_ratio = random.uniform(0.20, 0.28)
    raw_rem = max(1, min(raw_add - 1, int(raw_add * rem_ratio)))
    alpha = min(1.0, max(0.05, _env_float("SIM_GEMINI_LOC_EWMA_ALPHA", 0.26)))
    prev = st.gem_loc_ema.get(pin_key)
    if prev is None:
        ema_a, ema_r = float(raw_add), float(raw_rem)
    else:
        pa, pr = prev
        ema_a = alpha * raw_add + (1.0 - alpha) * pa
        ema_r = alpha * raw_rem + (1.0 - alpha) * pr
    st.gem_loc_ema[pin_key] = (ema_a, ema_r)
    return max(1, int(round(ema_a))), max(0, int(round(ema_r)))


def _gemini_log_resource_mirror(service_name: str) -> dict[str, str | int | bool]:
    """
    Real Gemini CLI logs nest ``service_name``, ``process.*``, ``host.*`` under OTLP **Resource**.
    Many collectors replace that resource with K8s metadata (e.g. ``kube-events``), so we also
    emit the same keys on **log record attributes** for stable facets / DataPrime filters.
    """
    ver = os.environ.get("SIM_GEMINI_SERVICE_VERSION", "1.0.0-sim")
    pod = os.environ.get("POD_NAME") or os.environ.get("HOSTNAME") or socket.gethostname()
    return {
        # Match keys seen in production ``resource.attributes`` (snake_case in Coralogix JSON).
        "service_name": service_name,
        "service.version": ver,
        "service_version": ver,
        "process.runtime.name": "python",
        "process.runtime.version": sys.version.split()[0],
        "process.runtime.description": "CPython",
        "process.pid": os.getpid(),
        "host.name": os.environ.get("HOSTNAME", socket.gethostname()),
        "host.arch": os.environ.get("SIM_HOST_ARCH", "amd64"),
        "k8s.pod.name": pod,
        "telemetry.simulator": "otel-ai-agent-sim",
    }


def _iso_z(ts_ns: int) -> str:
    return datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _sim_gemini_usage_tokens() -> tuple[int, int, int]:
    """
    Simulated ``input`` / ``output`` / ``cache`` token counts for ``gemini_cli_token_usage_total``.

    Defaults reflect long-context + codegen style usage so ``sum(increase(...[24h]))`` is not
    dominated by scrape noise. Tune with ``SIM_GEMINI_INPUT_TOKEN_{MIN,MAX}``,
    ``SIM_GEMINI_OUTPUT_*``, ``SIM_GEMINI_CACHE_*``.
    """
    lo = max(256, _env_int("SIM_GEMINI_INPUT_TOKEN_MIN", 8_000))
    hi = max(lo + 1, _env_int("SIM_GEMINI_INPUT_TOKEN_MAX", 200_000))
    inp = random.randint(lo, hi)
    out_lo = max(128, _env_int("SIM_GEMINI_OUTPUT_TOKEN_MIN", 600))
    out_cap = max(out_lo + 1, _env_int("SIM_GEMINI_OUTPUT_TOKEN_CAP", 80_000))
    out_frac_hi = min(0.95, max(0.02, float(os.environ.get("SIM_GEMINI_OUTPUT_FRAC_MAX", "0.52"))))
    out = max(
        out_lo,
        min(out_cap, int(inp * random.uniform(0.035, out_frac_hi))),
    )
    cache = 0
    if random.random() < float(os.environ.get("SIM_GEMINI_CACHE_HIT_PROB", "0.82")):
        c_lo = max(400, inp // 6)
        c_frac = min(0.98, max(0.08, float(os.environ.get("SIM_GEMINI_CACHE_FRAC_MAX", "0.92"))))
        c_hi = min(240_000, max(c_lo + 200, int(inp * c_frac)))
        cache = random.randint(c_lo, c_hi)
    return inp, out, cache


def _gemini_thought_token_count(model: str, inp: int, out: int) -> int:
    """
    Gemini API ``usageMetadata.thoughtsTokenCount``: internal "thinking" / reasoning tokens for
    models with thinking enabled (see Gemini API docs / Thinking). Coralogix maps these to
    ``gemini_cli_token_usage*_total{type="thought"}``.
    """
    if not _env_bool("SIM_GEMINI_THOUGHT_TOKENS", True):
        return 0
    ml = model.lower()
    # Prefer non-zero when model name suggests a thinking-capable line; still sample sometimes otherwise.
    likely = any(
        s in ml
        for s in (
            "2.5",
            "gemini-3",
            "3-pro",
            "3-flash",
            "thinking",
            "flash-thinking",
            "2.0-flash-thinking",
        )
    )
    rate = float(os.environ.get("SIM_GEMINI_THOUGHT_SAMPLE_RATE", "0.65"))
    if not likely and random.random() > rate:
        return 0
    cap = max(256, min(600_000, (inp + out) * 2 + 500))
    return random.randint(1, min(cap, max(128, int(out * random.uniform(0.04, 0.62)))))


def _emit_gemini_cli_session_logs(
    *,
    conversation_id: str,
    prompt: str,
    model: str,
    inp: int,
    out: int,
    cache: int,
    thought: int,
    user_email: str,
    cx_app: str,
    cx_sub: str,
    trace_id: int,
    span_id: int,
    api_duration_ms: int,
    lines_added: int,
    lines_removed: int,
    routing_latency_ms: int = 0,
    active_approval_mode: str = "auto",
    programming_language: str = "TypeScript",
    file_mimetype: str = "text/plain",
    file_extension: str = "ts",
    lines_function_name: str = "edit_file",
    tool_function_name: str = "search_replace",
    tool_decision: str = "accept",
    tool_type: str = "native",
    llm_call_span_id: int | None = None,
) -> None:
    """
    Structured logs aligned with real Node ``gemini-cli`` OTLP (``event_name``, messages, trace correlation).
    Includes optional file/tool events matching
    https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/telemetry.md
    """
    if st.gemini_otlp_logger is None or not _env_bool("SIM_GEMINI_LOGS", True):
        return

    auth = os.environ.get("SIM_GEMINI_AUTH_TYPE", "oauth-personal")
    interactive = _env_bool("SIM_GEMINI_INTERACTIVE", True)
    inst = _gemini_installation_id()
    cx = _cx_log_record_attrs(cx_app, cx_sub)
    svc = os.environ.get("SIM_GEMINI_SERVICE_NAME", "gemini-cli")
    base_common: dict = {
        **cx,
        **_gemini_log_resource_mirror(svc),
        "installation_id": inst,
        "session_id": conversation_id,
        "user_email": user_email,
        "active_approval_mode": active_approval_mode,
        "auth_type": auth,
        "interactive": interactive,
    }

    def _one(
        ts_ns: int,
        body: str,
        extra: dict,
        *,
        child_span_id: int | None = None,
        severity: SeverityNumber = SeverityNumber.INFO,
        severity_text: str = "INFO",
    ) -> None:
        sid = span_id if child_span_id is None else child_span_id
        attrs = {**base_common, **extra}
        rec = LogRecord(
            timestamp=ts_ns,
            trace_id=trace_id,
            span_id=sid,
            trace_flags=TraceFlags.get_default(),
            severity_number=severity,
            severity_text=severity_text,
            body=body,
            attributes=attrs,
            resource=st.gemini_otlp_logger.resource,
        )
        st.gemini_otlp_logger.emit(rec)

    t0 = time.time_ns()
    step = 2_000_000  # 2ms between startup lines (ordering)

    # Startup sequence (same shapes as real macOS/Homebrew exports; runtime is this process — Python).
    _one(
        t0,
        "Keychain availability: true",
        {
            "event_name": "gemini_cli.keychain.availability",
            "event_timestamp": _iso_z(t0),
            "available": True,
            "experiments_ids": list(_GEMINI_SAMPLE_EXPERIMENT_IDS),
        },
    )
    _one(
        t0 + step,
        "Token storage initialized. Type: keychain. Forced: false",
        {
            "event_name": "gemini_cli.token_storage.initialization",
            "event_timestamp": _iso_z(t0 + step),
            "type": "keychain",
            "forced": False,
            "experiments_ids": list(_GEMINI_SAMPLE_EXPERIMENT_IDS),
        },
    )
    embedding = os.environ.get("SIM_GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
    _cfg_payload: dict = {
        "event_name": "gemini_cli.config",
        "event_timestamp": _iso_z(t0 + 2 * step),
        "model": model,
        "embedding_model": embedding,
        "output_format": "text",
        "sandbox_enabled": True,
        "vertex_ai_enabled": False,
        "api_key_enabled": False,
        "debug_mode": False,
        "approval_mode": "default",
        "log_user_prompts_enabled": False,
        "experiments_ids": list(_GEMINI_SAMPLE_EXPERIMENT_IDS),
    }
    if _env_bool("SIM_GEMINI_SIMULATE_GITHUB_CONTEXT", False):
        _cfg_payload.update(
            {
                "github_workflow_name": os.environ.get("SIM_GEMINI_GITHUB_WORKFLOW", "ci"),
                "github_repository_hash": os.environ.get("SIM_GEMINI_GITHUB_REPO_HASH", "a1b2c3d4e5f6789012345678abcdefabcd"),
                "github_ref_name": os.environ.get("SIM_GEMINI_GITHUB_REF", "refs/heads/main"),
            }
        )
    _one(t0 + 2 * step, "CLI configuration loaded.", _cfg_payload)
    phases = (
        '[{"name":"cli_startup","duration_ms":120,"cpu_usage_user_usec":50000,'
        '"cpu_usage_system_usec":12000},{"name":"initialize_app","duration_ms":80,'
        '"cpu_usage_user_usec":8000,"cpu_usage_system_usec":2000}]'
    )
    _one(
        t0 + 3 * step,
        "Startup stats: 2 phases recorded.",
        {
            "event_name": "gemini_cli.startup_stats",
            "event_timestamp": _iso_z(t0 + 3 * step),
            "os_platform": sys.platform,
            "os_release": os.environ.get("SIM_OS_RELEASE", "linux"),
            "is_docker": False,
            "phases": phases,
            "experiments_ids": list(_GEMINI_SAMPLE_EXPERIMENT_IDS),
        },
    )

    prompt_len = len(prompt)
    pid_tag = _gemini_turn_prompt_name(conversation_id)
    tu = t0 + 4 * step
    _one(
        tu,
        f"User prompt. Length: {prompt_len}.",
        {
            "event_name": "gemini_cli.user_prompt",
            "event_timestamp": _iso_z(tu),
            "prompt_id": pid_tag,
            "prompt_length": prompt_len,
            "experiments_ids": list(_GEMINI_SAMPLE_EXPERIMENT_IDS),
        },
    )
    _one(
        tu + step,
        f"Model routing decision. Model: {model}, Source: agent-router/override",
        {
            "event_name": "gemini_cli.model_routing",
            "event_timestamp": _iso_z(tu + step),
            "decision_model": model,
            "decision_source": "agent-router/override",
            "failed": False,
            "routing_latency_ms": max(0, int(routing_latency_ms)),
            "reasoning": "Simulated routing (otel-ai-agent-sim).",
            "programming_language": programming_language,
            "experiments_ids": list(_GEMINI_SAMPLE_EXPERIMENT_IDS),
        },
    )
    # Prefer the real nested ``llm_call`` span id when present (else stable synthetic id).
    api_span = (
        llm_call_span_id
        if llm_call_span_id is not None
        else ((span_id ^ 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF)
    )
    _one(
        tu + 2 * step,
        f"API request to {model}.",
        {
            "event_name": "gemini_cli.api_request",
            "event_timestamp": _iso_z(tu + 2 * step),
            "model": model,
            "prompt_id": pid_tag,
            "role": "user",
            "experiments_ids": list(_GEMINI_SAMPLE_EXPERIMENT_IDS),
        },
        child_span_id=api_span,
    )
    http_status = 200
    total_tok = inp + out + (cache if cache > 0 else 0) + thought
    _one(
        tu + 3 * step,
        f"API response from {model}. Status: {http_status}. Duration: {api_duration_ms}ms.",
        {
            "event_name": "gemini_cli.api_response",
            "event_timestamp": _iso_z(tu + 3 * step),
            "model": model,
            "prompt_id": pid_tag,
            "duration_ms": api_duration_ms,
            "http_status_code": http_status,
            "status_code": http_status,
            "input_token_count": inp,
            "output_token_count": out,
            "cached_content_token_count": cache,
            "total_token_count": total_tok,
            "thoughts_token_count": thought,
            "experiments_ids": list(_GEMINI_SAMPLE_EXPERIMENT_IDS),
        },
        child_span_id=api_span,
    )
    # Between ``api_response`` (``tu + 3*step``) and file/tool (``tu + 4*step``).
    inf_ts = tu + 3 * step + max(400_000, step // 4)
    _one(
        inf_ts,
        "Inference operation completed",
        {
            "event_name": "gen_ai.client.inference.operation.details",
            "event_timestamp": _iso_z(inf_ts),
            "gen_ai.request.model": model,
            "gen_ai.operation.name": "generate_content",
            "gen_ai.conversation.id": conversation_id,
            "prompt_id": pid_tag,
            "input_token_count": inp,
            "output_token_count": out,
            "cached_content_token_count": cache,
            "thoughts_token_count": thought,
            "experiments_ids": list(_GEMINI_SAMPLE_EXPERIMENT_IDS),
        },
        child_span_id=api_span,
    )
    # ``gemini_cli.file_operation`` / ``gemini_cli.tool_call`` (telemetry.md → Files / Tools logs).
    fo_ts = tu + 4 * step
    _one(
        fo_ts,
        "File operation: update workspace file",
        {
            "event_name": "gemini_cli.file_operation",
            "event_timestamp": _iso_z(fo_ts),
            "tool_name": tool_function_name,
            "function_name": lines_function_name,
            "operation": "update",
            "lines": max(1, lines_added + lines_removed),
            "mimetype": file_mimetype,
            "extension": file_extension,
            "programming_language": programming_language,
            "prompt_id": pid_tag,
            "experiments_ids": list(_GEMINI_SAMPLE_EXPERIMENT_IDS),
        },
    )
    tc_ts = tu + 5 * step
    _one(
        tc_ts,
        f"Tool call: {tool_function_name} completed",
        {
            "event_name": "gemini_cli.tool_call",
            "event_timestamp": _iso_z(tc_ts),
            "function_name": tool_function_name,
            "function_args": '{"path":"src/app.ts"}',
            "duration_ms": random.randint(35, 1200),
            "success": True,
            "decision": tool_decision,
            "prompt_id": pid_tag,
            "tool_type": tool_type,
            "model_added_lines": lines_added,
            "model_removed_lines": lines_removed,
            "user_added_lines": 0,
            "user_removed_lines": 0,
            "experiments_ids": list(_GEMINI_SAMPLE_EXPERIMENT_IDS),
        },
    )
    done_ts = tu + 8 * step
    _one(
        done_ts,
        "Conversation turn finished",
        {
            "event_name": "gemini_cli.conversation_finished",
            "event_timestamp": _iso_z(done_ts),
            "session_id": conversation_id,
            "approval_mode": active_approval_mode,
            "turn_count": 1,
            "lines_added": lines_added,
            "lines_removed": lines_removed,
            "programming_language": programming_language,
            "experiments_ids": list(_GEMINI_SAMPLE_EXPERIMENT_IDS),
        },
    )
    # Optional ``gemini_cli.api_error`` (DataPrime: ``$d.attributes['event_name']`` / ``error_type``).
    api_error_rate = _env_float("SIM_GEMINI_API_ERROR_RATE", 0.22)
    if random.random() < api_error_rate:
        n_err = random.randint(1, 2)
        picks = random.sample(_GEMINI_CLI_API_ERROR_PROFILES, k=min(n_err, len(_GEMINI_CLI_API_ERROR_PROFILES)))
        for i, (err_type, http_code, err_msg) in enumerate(picks):
            ae_ts = tu + (6 + i) * step
            _one(
                ae_ts,
                f"API error: {err_type}",
                {
                    "event_name": "gemini_cli.api_error",
                    "event_timestamp": _iso_z(ae_ts),
                    "error_type": err_type,
                    "error_message": err_msg,
                    "http_status_code": http_code,
                    "model": model,
                    "prompt_id": pid_tag,
                    "experiments_ids": list(_GEMINI_SAMPLE_EXPERIMENT_IDS),
                },
                severity=SeverityNumber.ERROR,
                severity_text="ERROR",
            )


def _gemini_turn_prompt_name(conversation_id: str) -> str:
    """Turn id used on OTLP spans/logs (matches real ``prompt_id`` suffix pattern)."""
    return f"{conversation_id}########0"


def _gemini_minimal_tool_definitions_json() -> str:
    """Compact ``gen_ai.tool.definitions`` string akin to Node CLI exports (truncated in UIs)."""
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
    """Readable stand-in for large ``gen_ai.system_instructions`` on ``llm_call`` spans."""
    stub = os.environ.get(
        "SIM_GEMINI_SYSTEM_INSTRUCTIONS_STUB",
        "You are the Gemini CLI agent. Prefer small, verifiable edits; respect the repo layout and safety rules.",
    )
    cap = max(128, _env_int("SIM_GEMINI_SYSTEM_INSTRUCTIONS_MAX_LEN", 4096))
    return stub if len(stub) <= cap else stub[:cap]



def emit_gemini_cli_user_prompt_span(conversation_id: str, roster_user: dict | None = None) -> None:
    """Replicate a real `gemini-cli` `user_prompt` span (OpenTelemetry → Coralogix shape)."""
    if st.sim_cli is None:
        raise RuntimeError("CLI trace providers not initialized")
    _gem_emit_started = time.perf_counter()
    ver = tool_version_for("gemini_cli")
    gemini_tracer = st.sim_cli.gemini.get_tracer("gemini-cli", ver)
    event_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    prompt = random.choice(GEMINI_SAMPLE_PROMPTS)
    cx_app = os.environ.get("GEMINI_CX_APPLICATION_NAME", "gemini-cli")
    cx_sub = os.environ.get("GEMINI_CX_SUBSYSTEM_NAME", "gemini-cli-sessions")
    model = _gemini_model_for_conversation(conversation_id)

    _gem_smooth = _env_bool("SIM_GEMINI_SMOOTH_PRODUCTIVITY", True)
    # ~3.7s in the sample (microseconds in JSON); tighter span when smoothing (steady turns).
    duration_s = random.uniform(3.0, 4.1) if _gem_smooth else random.uniform(2.4, 5.2)
    if roster_user is not None:
        user_attrs = _gemini_user_attrs_from_roster(roster_user)
    else:
        user_attrs = random_coralogix_identity(conversation_id)
    inst = _gemini_installation_id()
    metric_sid = _gemini_prometheus_session_id(user_attrs, conversation_id)
    pin_key = _gemini_metric_pin_key(user_attrs, conversation_id)
    lines_fn, lang, mime, ext, ap_mode, tool_fn = _gemini_pinned_dims(pin_key)
    uid = str(user_attrs["user.id"])
    uem = str(user_attrs["user.email"])
    _gem_shape = _gemini_metric_label_shape()
    gem_common = (cx_app, cx_sub, model, inst, metric_sid, uid, uem, ap_mode)
    pin_metrics = _env_bool("SIM_GEMINI_PIN_METRIC_LABELS", True)
    log_tool_decision = "accept"
    log_tool_type = "native"

    inp, out, cache = _sim_gemini_usage_tokens()
    # Lines changed (telemetry ``gemini_cli.lines.changed``) — smoothed per user when ``SIM_GEMINI_SMOOTH_PRODUCTIVITY``.
    loc_added, loc_removed = _gemini_lines_changed_deltas(pin_key, out)
    thought = _gemini_thought_token_count(model, inp, out)
    # Model HTTP latency (ms) for ``gemini_cli_api_request_latency_ms_*`` histogram — same order of magnitude as logs.
    if _gem_smooth:
        api_ms = max(220, int(duration_s * 1000 * random.uniform(0.28, 0.38)))
        routing_ms = int(random.triangular(45.0, 280.0, 130.0))
        http_status = "429" if random.random() < 0.012 else "200"
    else:
        api_ms = max(200, int(duration_s * 1000 * random.uniform(0.18, 0.48)))
        routing_ms = random.randint(3, 720)
        http_status = "429" if random.random() < 0.04 else "200"

    llm_call_span_id_for_logs: int | None = None
    with gemini_tracer.start_as_current_span(
        "user_prompt",
        kind=trace.SpanKind.INTERNAL,
    ) as span:
        span.set_status(Status(StatusCode.OK))
        span_attrs: dict[str, str | int] = {
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
            # Many UIs also persist legacy Jaeger-style kind on tags:
            "span.kind": "internal",
            # processTags-like fields (sample used Node on macOS; we mirror keys for K8s/Python):
            "session.id": conversation_id,
            "installation_id": inst,
            "active_approval_mode": ap_mode,
            "process.runtime.name": "python",
            "process.runtime.version": sys.version.split()[0],
            "process.runtime.description": "CPython",
            "host.name": os.environ.get("HOSTNAME", socket.gethostname()),
            "host.arch": os.environ.get("SIM_HOST_ARCH", "amd64"),
            "process.pid": str(os.getpid()),
        }
        span.set_attributes(span_attrs)
        # Nested ``llm_call`` matches real Gemini CLI exports (tokens + model live here).
        with gemini_tracer.start_as_current_span(
            "llm_call",
            kind=trace.SpanKind.INTERNAL,
        ) as llm_span:
            llm_span.set_status(Status(StatusCode.OK))
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
                "process.runtime.version": sys.version.split()[0],
                "process.runtime.description": "CPython",
                "host.name": os.environ.get("HOSTNAME", socket.gethostname()),
                "host.arch": os.environ.get("SIM_HOST_ARCH", "amd64"),
                "process.pid": str(os.getpid()),
                "gen_ai.usage.input_tokens": inp,
                "gen_ai.usage.output_tokens": out,
            }
            if cache > 0:
                llm_attrs["gen_ai.usage.cache_read_tokens"] = cache
            if thought > 0:
                llm_attrs["gen_ai.usage.thought_tokens"] = thought
            llm_attrs.update(
                _gen_ai_dashboard_llm_span_attributes(inp, out, operation_name="llm_call", model=model)
            )
            llm_span.set_attributes(llm_attrs)
            llm_call_span_id_for_logs = llm_span.get_span_context().span_id
        # HAR: ``gemini_cli_token_usage_total``; mirror to ``gemini_cli_token_usage__token__total`` for Coralogix volume.
        if st.prom_gem_session is not None and st.prom_gem_token is not None:
            if _gem_shape == "standard":
                gem_static = _gemini_standard_prom_static(tool_version=ver)

                def _gem_standard_tok(typ: str, n: float | int) -> None:
                    tv = (
                        ap_mode,
                        cx_app,
                        cx_sub,
                        gem_static["host_arch"],
                        inst,
                        gem_static["job"],
                        model,
                        gem_static["os_type"],
                        gem_static["os_version"],
                        gem_static["service_name"],
                        gem_static["service_version"],
                        metric_sid,
                        gem_static["telemetry_sdk_language"],
                        gem_static["telemetry_sdk_name"],
                        gem_static["telemetry_sdk_version"],
                        typ,
                        uem,
                    )
                    st.prom_gem_token.labels(*tv).inc(n)
                    if st.prom_gem_token_coralogix is not None:
                        st.prom_gem_token_coralogix.labels(*tv).inc(n)
                    if st.prom_gem_token_tokens is not None:
                        st.prom_gem_token_tokens.labels(*tv).inc(n)

                def _gem_standard_sess() -> tuple:
                    return (
                        ap_mode,
                        cx_app,
                        cx_sub,
                        gem_static["host_arch"],
                        inst,
                        gem_static["job"],
                        gem_static["os_type"],
                        gem_static["os_version"],
                        gem_static["service_name"],
                        gem_static["service_version"],
                        metric_sid,
                        gem_static["telemetry_sdk_language"],
                        gem_static["telemetry_sdk_name"],
                        gem_static["telemetry_sdk_version"],
                        uem,
                    )

                def _gem_standard_line(line_type: str) -> tuple:
                    return (
                        ap_mode,
                        cx_app,
                        cx_sub,
                        lines_fn,
                        gem_static["host_arch"],
                        inst,
                        gem_static["job"],
                        gem_static["os_type"],
                        gem_static["os_version"],
                        lang,
                        gem_static["service_name"],
                        gem_static["service_version"],
                        metric_sid,
                        gem_static["telemetry_sdk_language"],
                        gem_static["telemetry_sdk_name"],
                        gem_static["telemetry_sdk_version"],
                        line_type,
                        uem,
                    )

                def _gem_standard_file(op: str) -> tuple:
                    return (
                        ap_mode,
                        cx_app,
                        cx_sub,
                        gem_static["host_arch"],
                        inst,
                        gem_static["job"],
                        op,
                        gem_static["os_type"],
                        gem_static["os_version"],
                        lang,
                        gem_static["service_name"],
                        gem_static["service_version"],
                        metric_sid,
                        gem_static["telemetry_sdk_language"],
                        gem_static["telemetry_sdk_name"],
                        gem_static["telemetry_sdk_version"],
                        uem,
                    )

                def _gem_standard_tool(decision: str, fn: str, succ: str, ttyp: str) -> tuple:
                    return (
                        ap_mode,
                        cx_app,
                        cx_sub,
                        decision,
                        fn,
                        gem_static["host_arch"],
                        inst,
                        gem_static["job"],
                        gem_static["os_type"],
                        gem_static["os_version"],
                        gem_static["service_name"],
                        gem_static["service_version"],
                        metric_sid,
                        succ,
                        gem_static["telemetry_sdk_language"],
                        gem_static["telemetry_sdk_name"],
                        gem_static["telemetry_sdk_version"],
                        ttyp,
                        uem,
                    )

                def _gem_standard_api() -> tuple:
                    return (
                        ap_mode,
                        cx_app,
                        cx_sub,
                        gem_static["host_arch"],
                        inst,
                        gem_static["job"],
                        model,
                        gem_static["os_type"],
                        gem_static["os_version"],
                        gem_static["service_name"],
                        gem_static["service_version"],
                        metric_sid,
                        http_status,
                        gem_static["telemetry_sdk_language"],
                        gem_static["telemetry_sdk_name"],
                        gem_static["telemetry_sdk_version"],
                        uem,
                    )

                def _gem_standard_hist_lat() -> tuple:
                    return (
                        cx_app,
                        cx_sub,
                        gem_static["host_arch"],
                        gem_static["job"],
                        model,
                        gem_static["os_type"],
                        gem_static["os_version"],
                        gem_static["service_name"],
                        gem_static["service_version"],
                        gem_static["telemetry_sdk_language"],
                        gem_static["telemetry_sdk_name"],
                        gem_static["telemetry_sdk_version"],
                    )

                def _gem_standard_hist_tool(fn: str) -> tuple:
                    return (
                        cx_app,
                        cx_sub,
                        fn,
                        gem_static["host_arch"],
                        gem_static["job"],
                        gem_static["os_type"],
                        gem_static["os_version"],
                        gem_static["service_name"],
                        gem_static["service_version"],
                        gem_static["telemetry_sdk_language"],
                        gem_static["telemetry_sdk_name"],
                        gem_static["telemetry_sdk_version"],
                    )

                st.prom_gem_session.labels(*_gem_standard_sess()).inc()
                _gem_standard_tok("input", inp)
                _gem_standard_tok("output", out)
                if cache > 0:
                    _gem_standard_tok("cache", cache)
                if thought > 0:
                    _gem_standard_tok("thought", thought)
                _tt_rate = 0.22 if _gem_smooth else 0.38
                if random.random() < _env_float("SIM_GEMINI_TOOL_TOKEN_SAMPLE_RATE", _tt_rate):
                    _gem_standard_tok("tool", random.randint(28, 220) if _gem_smooth else random.randint(8, 1200))
                if st.prom_gem_api is not None:
                    st.prom_gem_api.labels(*_gem_standard_api()).inc()
                if st.prom_gem_api_latency is not None:
                    st.prom_gem_api_latency.labels(*_gem_standard_hist_lat()).observe(float(api_ms))
                if st.prom_gem_model_routing_latency is not None:
                    st.prom_gem_model_routing_latency.labels(*_gem_standard_hist_lat()).observe(float(routing_ms))
                if st.prom_gem_lines is not None:
                    # Upstream ``recordLinesChanged`` skips ``lines <= 0`` (metrics.ts).
                    if loc_added > 0:
                        st.prom_gem_lines.labels(*_gem_standard_line("added")).inc(loc_added)
                        if st.prom_gem_lines_coralogix is not None:
                            st.prom_gem_lines_coralogix.labels(*_gem_standard_line("added")).inc(loc_added)
                    if loc_removed > 0:
                        st.prom_gem_lines.labels(*_gem_standard_line("removed")).inc(loc_removed)
                        if st.prom_gem_lines_coralogix is not None:
                            st.prom_gem_lines_coralogix.labels(*_gem_standard_line("removed")).inc(loc_removed)
                if st.prom_gem_file_op is not None:
                    file_op_fixed = os.environ.get("SIM_GEMINI_FILE_OPERATION", "").strip() or (
                        "update" if pin_metrics else ""
                    )
                    f_lo = _env_int("SIM_GEMINI_FILE_OPS_MIN", 4 if _gem_smooth else 2)
                    f_hi = max(f_lo, _env_int("SIM_GEMINI_FILE_OPS_MAX", 6 if _gem_smooth else 7))
                    n_file = random.randint(f_lo, f_hi)
                    for _ in range(n_file):
                        op = file_op_fixed or random.choice(("create", "read", "update"))
                        st.prom_gem_file_op.labels(*_gem_standard_file(op)).inc()
                if st.prom_gem_tool_call is not None:
                    tool_names = ("write_file", "search_replace", "read_file", "run_shell_command")
                    decisions = ("accept", "auto_accept", "modify", "reject")
                    t_lo = _env_int("SIM_GEMINI_TOOL_CALLS_MIN", 3 if _gem_smooth else 2)
                    t_hi = max(t_lo, _env_int("SIM_GEMINI_TOOL_CALLS_MAX", 4 if _gem_smooth else 6))
                    n_tool = random.randint(t_lo, t_hi)
                    _mcp_p = _env_float("SIM_GEMINI_TOOL_TYPE_MCP_RATE", 0.35)
                    for i in range(n_tool):
                        fn = tool_fn if pin_metrics else random.choice(tool_names)
                        dec = random.choice(decisions)
                        ttyp = "mcp" if random.random() < _mcp_p else "native"
                        if i == 0:
                            log_tool_decision, log_tool_type = dec, ttyp
                        st.prom_gem_tool_call.labels(
                            *_gem_standard_tool(
                                dec,
                                fn,
                                "true" if random.random() > 0.08 else "false",
                                ttyp,
                            )
                        ).inc()
                        if st.prom_gem_tool_latency is not None:
                            if _gem_smooth:
                                tl = int(random.triangular(80.0, 4200.0, 900.0))
                            else:
                                tl = random.randint(15, 12_000)
                            st.prom_gem_tool_latency.labels(*_gem_standard_hist_tool(fn)).observe(float(tl))
            else:

                def _gem_tok(typ: str, n: float | int) -> None:
                    st.prom_gem_token.labels(*gem_common, typ).inc(n)
                    if st.prom_gem_token_coralogix is not None:
                        st.prom_gem_token_coralogix.labels(*gem_common, typ).inc(n)

                st.prom_gem_session.labels(cx_app, cx_sub, model).inc()
                _gem_tok("input", inp)
                _gem_tok("output", out)
                if cache > 0:
                    _gem_tok("cache", cache)
                if thought > 0:
                    _gem_tok("thought", thought)
                _tt_rate = 0.22 if _gem_smooth else 0.38
                if random.random() < _env_float("SIM_GEMINI_TOOL_TOKEN_SAMPLE_RATE", _tt_rate):
                    _gem_tok("tool", random.randint(28, 220) if _gem_smooth else random.randint(8, 1200))
                if st.prom_gem_api is not None:
                    st.prom_gem_api.labels(*gem_common, http_status).inc()
                if st.prom_gem_api_latency is not None:
                    st.prom_gem_api_latency.labels(*gem_common).observe(float(api_ms))
                if st.prom_gem_model_routing_latency is not None:
                    st.prom_gem_model_routing_latency.labels(*gem_common).observe(float(routing_ms))
                if st.prom_gem_lines is not None:
                    if loc_added > 0:
                        st.prom_gem_lines.labels(*gem_common, lines_fn, lang, "added").inc(loc_added)
                        if st.prom_gem_lines_coralogix is not None:
                            st.prom_gem_lines_coralogix.labels(*gem_common, lines_fn, lang, "added").inc(loc_added)
                    if loc_removed > 0:
                        st.prom_gem_lines.labels(*gem_common, lines_fn, lang, "removed").inc(loc_removed)
                        if st.prom_gem_lines_coralogix is not None:
                            st.prom_gem_lines_coralogix.labels(*gem_common, lines_fn, lang, "removed").inc(loc_removed)
                if st.prom_gem_file_op is not None:
                    file_op_fixed = os.environ.get("SIM_GEMINI_FILE_OPERATION", "").strip() or (
                        "update" if pin_metrics else ""
                    )
                    f_lo = _env_int("SIM_GEMINI_FILE_OPS_MIN", 4 if _gem_smooth else 2)
                    f_hi = max(f_lo, _env_int("SIM_GEMINI_FILE_OPS_MAX", 6 if _gem_smooth else 7))
                    n_file = random.randint(f_lo, f_hi)
                    for _ in range(n_file):
                        op = file_op_fixed or random.choice(("create", "read", "update"))
                        st.prom_gem_file_op.labels(*gem_common, op, mime, ext, lang).inc()
                if st.prom_gem_tool_call is not None:
                    tool_names = ("write_file", "search_replace", "read_file", "run_shell_command")
                    t_lo = _env_int("SIM_GEMINI_TOOL_CALLS_MIN", 3 if _gem_smooth else 2)
                    t_hi = max(t_lo, _env_int("SIM_GEMINI_TOOL_CALLS_MAX", 4 if _gem_smooth else 6))
                    n_tool = random.randint(t_lo, t_hi)
                    for i in range(n_tool):
                        fn = tool_fn if pin_metrics else random.choice(tool_names)
                        dec = random.choice(("accept", "auto_accept", "reject"))
                        ttyp = "native"
                        if i == 0:
                            log_tool_decision, log_tool_type = dec, ttyp
                        st.prom_gem_tool_call.labels(
                            *gem_common,
                            fn,
                            "true" if random.random() > 0.08 else "false",
                            dec,
                            ttyp,
                        ).inc()
                        if st.prom_gem_tool_latency is not None:
                            if _gem_smooth:
                                tl = int(random.triangular(80.0, 4200.0, 900.0))
                            else:
                                tl = random.randint(15, 12_000)
                            st.prom_gem_tool_latency.labels(*gem_common, fn).observe(float(tl))
        ctx = span.get_span_context()
        _emit_gemini_cli_session_logs(
            conversation_id=conversation_id,
            prompt=prompt,
            model=model,
            inp=inp,
            out=out,
            cache=cache,
            thought=thought,
            user_email=str(user_attrs["user.email"]),
            cx_app=cx_app,
            cx_sub=cx_sub,
            trace_id=ctx.trace_id,
            span_id=ctx.span_id,
            api_duration_ms=api_ms,
            lines_added=loc_added,
            lines_removed=loc_removed,
            routing_latency_ms=routing_ms,
            active_approval_mode=ap_mode,
            programming_language=lang,
            file_mimetype=mime,
            file_extension=ext,
            lines_function_name=lines_fn,
            tool_function_name=tool_fn,
            tool_decision=log_tool_decision,
            tool_type=log_tool_type,
            llm_call_span_id=llm_call_span_id_for_logs,
        )
        time.sleep(duration_s)
        # Upstream: ``recordAgentRunMetrics`` / ``gemini_cli.agent.duration`` (ms) + ``gemini_cli.agent.run.count``.
        _wall_ms = max(1, int((time.perf_counter() - _gem_emit_started) * 1000))
        _an = os.environ.get("SIM_GEMINI_AGENT_METRIC_NAME", "gemini-cli").strip() or "gemini-cli"
        _tr = os.environ.get("SIM_GEMINI_AGENT_TERMINATE_REASON", "completed").strip() or "completed"
        if st.prom_gem_agent_duration is not None or st.prom_gem_agent_run is not None:
            if _gem_shape == "standard":
                _gss = _gemini_standard_prom_static(tool_version=ver)
                _base_agent = (
                    ap_mode,
                    cx_app,
                    cx_sub,
                    _gss["host_arch"],
                    inst,
                    _gss["job"],
                    _gss["os_type"],
                    _gss["os_version"],
                    _gss["service_name"],
                    _gss["service_version"],
                    metric_sid,
                    _gss["telemetry_sdk_language"],
                    _gss["telemetry_sdk_name"],
                    _gss["telemetry_sdk_version"],
                    uem,
                )
                if st.prom_gem_agent_duration is not None:
                    st.prom_gem_agent_duration.labels(*_base_agent, _an).observe(float(_wall_ms))
                if st.prom_gem_agent_run is not None:
                    st.prom_gem_agent_run.labels(*_base_agent, _an, _tr).inc()
            elif _gem_shape == "extended":
                if st.prom_gem_agent_duration is not None:
                    st.prom_gem_agent_duration.labels(*gem_common, _an).observe(float(_wall_ms))
                if st.prom_gem_agent_run is not None:
                    st.prom_gem_agent_run.labels(*gem_common, _an, _tr).inc()

