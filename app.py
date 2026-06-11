import hashlib
import importlib.metadata
import logging
import os
import platform
import sys
import socket
import threading
import time
import random
import secrets
import uuid
import calendar
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

import otlp_metrics
import prometheus_rw
from sim.claude.dashboard import emit_claude_code_dashboard
from sim.common.constants import claude_prompt_for_session
from sim.claude.repos import claude_rogue_user_token_multiplier
from sim.claude.user_variance import (
    claude_user_emit_turns_this_cycle,
    claude_user_productivity_multiplier,
    claude_user_session_phase_offset,
    claude_user_session_rotate_duration_from_env,
    claude_user_should_emit_this_cycle,
    claude_user_token_multiplier,
)
from sim.codex.agent import _codex_model_for_turn
from sim.common.otel import _gen_ai_dashboard_llm_span_attributes
from sim.common.model_pricing import estimate_llm_cost_usd
from sim.cursor.agent import (
    _cursor_roster_user_for_emit,
    _cursor_stable_session_id_from_roster_user,
    emit_cursor_composer_session,
)
from sim.common.constants import (
    CLAUDE_CODE_DEFAULT_MODEL,
    CODEX_DEFAULT_MODEL,
    COPILOT_CLI_MODELS,
    COPILOT_DEFAULT_MODEL,
    CURSOR_DEFAULT_MODEL,
    GEMINI_CLI_MODELS,
    GEMINI_DEFAULT_MODEL,
    _CLAUDE_CODE_MODELS,
    claude_code_gen_ai_system_for_model,
)
from sim.copilot.cli import emit_copilot_cli_session
from sim.copilot.collector_metrics import copilot_collector_enabled, register_copilot_collector_metrics
from sim.common.state import st
from opentelemetry import trace
from opentelemetry._logs import set_logger_provider
from opentelemetry._logs.severity import SeverityNumber
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider, LogRecord
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.semconv.resource import ResourceAttributes
from opentelemetry.trace import Status, StatusCode, TraceFlags
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, start_http_server

# --- Optional override (Prometheus remote_write only) ---
# If non-empty, used as Bearer token instead of ``CORALOGIX_PRIVATE_KEY`` (e.g. local debug).
# Invalid keys return 403 from Coralogix; leave "" in normal deployments.
_CORALOGIX_RW_KEY_HARDCODE_TEST = ""

log = logging.getLogger(__name__)


class _NoopSpanExporter(SpanExporter):
    """Drops ended spans (no network). Used for Claude ``TracerProvider`` when OTLP traces are disabled."""

    def export(self, spans):  # noqa: ANN001
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


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


def _claude_telemetry_profile() -> str:
    """
    ``flat`` (default): EU2-style Coralogix exports (snake_case log attrs, string token/cost fields,
    ``com.anthropic.claude_code.events`` scope, subsystem ``claude-code`` unless overridden).

    ``dotted``: dotted keys (``event.*`` / ``session.id``), numeric token/cost,
    ``com.anthropic.claude_code`` scope, subsystem from ``SIM_CLAUDE_DOTTED_CX_SUBSYSTEM_NAME``.

    ``both`` / ``dual`` / ``all``: emit **flat and dotted** OTLP log pipelines and duplicate Prometheus
    counters with each subsystem's label set (``SIM_CLAUDE_TELEMETRY_PROFILE``).
    """
    raw = os.environ.get("SIM_CLAUDE_TELEMETRY_PROFILE", "").strip().lower()
    if not raw:
        raw = "flat"
    if raw in ("both", "dual", "all"):
        return "both"
    if raw == "dotted":
        return "dotted"
    if raw == "flat":
        return "flat"
    return "flat"


def _claude_effective_cx_subsystem() -> str:
    """Subsystem on Claude **traces** and single-profile metrics; ``both`` keeps flat for trace Resource."""
    p = _claude_telemetry_profile()
    flat_sub = os.environ.get("CLAUDE_CODE_CX_SUBSYSTEM_NAME", "claude-code").strip() or "claude-code"
    dotted_sub = os.environ.get("SIM_CLAUDE_DOTTED_CX_SUBSYSTEM_NAME", "claude-code-sessions").strip() or "claude-code-sessions"
    if p == "dotted":
        return dotted_sub
    return flat_sub


def _resolve_otlp_config() -> tuple[str, bool, tuple[tuple[str, str], ...] | None]:
    """
    OTLP gRPC target, TLS, and optional ``Authorization: Bearer`` for direct Coralogix ingest.

    - ``OTLP_ENDPOINT``: host:port (default: in-cluster collector ``otel-collector:4317``).
    - ``CORALOGIX_REGION``: if set and ``OTLP_ENDPOINT`` is unset, uses
      ``ingress.<region>.coralogix.com:443``.
    - ``CORALOGIX_PRIVATE_KEY``: Send-Your-Data API key; adds Bearer metadata (direct ingest).
    - ``OTLP_INSECURE``: defaults to false for ``:443``, true for local collectors without TLS.
    """
    raw = os.environ.get("OTLP_ENDPOINT", "").strip()
    region = os.environ.get("CORALOGIX_REGION", "").strip().lower()
    key = os.environ.get("CORALOGIX_PRIVATE_KEY", "").strip()

    if raw:
        endpoint = raw
    elif region:
        endpoint = f"ingress.{region}.coralogix.com:443"
    else:
        endpoint = "otel-collector:4317"

    hostport = endpoint.split("://", 1)[-1]
    if hostport.endswith(":443") or ":443" in hostport:
        default_insecure = False
    else:
        default_insecure = True
    insecure = _env_bool("OTLP_INSECURE", default_insecure)

    headers: tuple[tuple[str, str], ...] | None = None
    if key:
        headers = (("authorization", f"Bearer {key}"),)

    return endpoint, insecure, headers


# Prometheus label names aligned with ``_cc_base_attrs`` (dots → underscores).
# Used by ``claude_code_token_usage_tokens_total`` and all other Claude Code counters.
# Sim-only dimensions (``sim.*``) are not attached to Prometheus counters.
_CC_BASE_LABEL_NAMES = (
    "cx_application_name",
    "cx_subsystem_name",
    "service_name",
    "service_version",
    "session_id",
    "user_account_uuid",
    "user_account_id",
    "user_id",
    "user_name",
    "user_email",
    "terminal_type",
    "organization_id",
    "os_version",
    "os_type",
    "host_arch",
    "app_version",
)

# ``claude_code_lines_of_code_count_*`` — same user dimensions as commit/PR counters so
# ``sum by (user_email)`` / ``sum by (user_name)`` panels do not drop LOC while showing commits.
_CC_LOC_LABEL_NAMES = _CC_BASE_LABEL_NAMES + ("type",)


def _map_cc_base_labels(base: dict, cx_app: str, cx_sub: str) -> dict[str, str]:
    acc = str(base.get("user.account_uuid", ""))
    acc_id = str(base.get("user.account.id", acc))
    return {
        "cx_application_name": cx_app,
        "cx_subsystem_name": cx_sub,
        "service_name": str(base.get("service.name", "")),
        "service_version": str(base.get("service.version", "")),
        "session_id": str(base.get("session.id", "")),
        "user_account_uuid": acc,
        "user_account_id": acc_id,
        "user_id": str(base.get("user.id", "")),
        "user_name": str(base.get("user.name", "")),
        "user_email": str(base.get("user.email", "")),
        "terminal_type": str(base.get("terminal.type", "")),
        "organization_id": str(base.get("organization.id", "")),
        "os_version": str(base.get("os.version", "")),
        "os_type": str(base.get("os.type", "")),
        "host_arch": str(base.get("host.arch", "")),
        "app_version": str(base.get("app.version", "")),
    }


def _cc_prometheus_session_id(raw_session_id: str) -> str:
    """
    Label value for ``session_id`` on Claude Code **Prometheus** counters only.

    OTLP ``session.id`` is usually stable per roster user (``SIM_CLAUDE_STABLE_SESSION_PER_USER``); legacy mode
    used a fresh UUID every loop iteration. Attaching raw high-churn session ids to counters
    creates a new time series every few seconds (unbounded cardinality). Many
    backends drop or never aggregate those series, so token/commit/PR panels stay
    empty while OTLP logs still look fine.

    - ``bounded`` (default): map each logical session into a fixed pool of stable
      synthetic session ids (see ``SIM_CLAUDE_PROMETHEUS_SESSION_BUCKETS``).
    - ``trace``: use ``raw_session_id`` unchanged (high-cardinality; debug only).

    When ``SIM_CLAUDE_METRICS_SESSION_ID_ALIGN_LOGS`` is true, emit path uses the raw trace
    ``session.id`` on metrics so logs and counters join on the same session (high churn).

    Default is **false**: counters use the bucketed id here so token/commit panels reuse series.
    """
    mode = os.environ.get("SIM_CLAUDE_PROMETHEUS_SESSION_LABELS", "bounded").strip().lower()
    if mode in ("trace", "unique", "per_trace"):
        return raw_session_id
    n = max(8, _env_int("SIM_CLAUDE_PROMETHEUS_SESSION_BUCKETS", 512))
    idx = int.from_bytes(hashlib.sha256(raw_session_id.encode()).digest()[:8], "big") % n
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"otel-ai-agent-sim:cc:metric-session:{idx}"))


def _cc_base_for_prometheus_labels(raw_session_id: str, base: dict) -> dict:
    """Return ``base`` for ``_map_cc_base_labels``; may rewrite ``session.id`` for low-cardinality metrics."""
    if _env_bool("SIM_CLAUDE_METRICS_SESSION_ID_ALIGN_LOGS", False):
        return dict(base)
    return {**base, "session.id": _cc_prometheus_session_id(raw_session_id)}


def _random_partition_nonneg(total: int, n: int) -> list[int]:
    """Return ``n`` nonnegative integers that sum to ``total`` (random split)."""
    n = max(1, int(n))
    total = max(0, int(total))
    if n == 1:
        return [total]
    cuts = sorted([0] + [secrets.randbelow(total + 1) for _ in range(n - 1)] + [total])
    return [cuts[i + 1] - cuts[i] for i in range(n)]


def _stable_uuid(seed: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


def _anthropic_style_account_id(account_uuid: str) -> str:
    """
    Tagged ``user.account_id`` shape from Claude Code monitoring docs
    (e.g. ``user_01BWBeN28...``), stable per ``user.account_uuid``.
    """
    raw = hashlib.sha256(f"anthropic:account_id:{account_uuid}".encode()).digest()
    alphabet = "0123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    suffix = "".join(alphabet[b % len(alphabet)] for b in raw[:24])
    return "user_01" + suffix


def _cc_tool_use_id() -> str:
    """Anthropic-style tool use id (e.g. ``toolu_01QFjQG8x8BQwxc76PmRpEYV``)."""
    alphabet = "0123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    tail = "".join(secrets.choice(alphabet) for _ in range(22))
    return "toolu_01" + tail


def _cc_api_request_id() -> str:
    """API request id shape from production logs (e.g. ``req_011Ca7ykaEmjoX2vpE7RMaik``)."""
    alphabet = "0123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    return "req_01" + "".join(secrets.choice(alphabet) for _ in range(22))


def _cc_claude_log_attributes_flat(
    base: dict,
    *,
    event_name: str,
    event_sequence: int,
    event_timestamp_iso: str,
    cx_app: str,
    cx_sub: str,
    extra: dict,
) -> dict:
    """
    Coralogix-style log attributes for Claude Code (matches EU2 ``claude_code.api_request`` exports:
    snake_case keys; token counts, ``cost_usd``, and ``duration_ms`` as strings in JSON).
    """
    out: dict[str, str | int | float] = {
        **_cx_log_record_attrs(cx_app, cx_sub),
        "organization_id": str(base.get("organization.id", "")),
        "session_id": str(base.get("session.id", "")),
        "user_account_uuid": str(base.get("user.account_uuid", "")),
        "user_account_id": str(base.get("user.account.id", "")),
        "user_id": str(base.get("user.id", "")),
        "user_email": str(base.get("user.email", "")),
        "terminal_type": str(base.get("terminal.type", "")),
        "event_name": event_name,
        "event_sequence": event_sequence,
        "event_timestamp": event_timestamp_iso,
    }
    _str_int_keys = frozenset(
        {
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_creation_tokens",
        }
    )
    for k, v in extra.items():
        if v is None:
            continue
        if k == "duration_ms" and isinstance(v, (int, float)):
            out[k] = str(int(v))
        elif k in _str_int_keys and isinstance(v, (int, float)):
            out[k] = str(int(v))
        elif k == "cost_usd":
            out[k] = str(v) if isinstance(v, str) else str(float(v))
        else:
            out[k] = v
    return out


def _cc_claude_log_attributes_dotted(
    base: dict,
    *,
    event_name: str,
    event_sequence: int,
    event_timestamp_iso: str,
    cx_app: str,
    cx_sub: str,
    extra: dict,
) -> dict[str, object]:
    """
    Log attributes with dotted keys for ``claude_code.api_request`` samples: dotted keys for
    event/session/user/org/terminal/prompt/request, snake_case numeric counters for tokens and cost.
    """
    out: dict[str, object] = {
        **_cx_log_record_attrs(cx_app, cx_sub),
        "organization.id": str(base.get("organization.id", "")),
        "session.id": str(base.get("session.id", "")),
        "user.account_uuid": str(base.get("user.account_uuid", "")),
        "user.account_id": str(base.get("user.account.id", "")),
        "user.id": str(base.get("user.id", "")),
        "user.email": str(base.get("user.email", "")),
        "terminal.type": str(base.get("terminal.type", "")),
        "event.name": event_name,
        "event.sequence": int(event_sequence),
        "event.timestamp": event_timestamp_iso,
    }
    _rename = {"prompt_id": "prompt.id", "request_id": "request.id", "prompt_length": "prompt.length"}
    _numeric = frozenset(
        {
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_creation_tokens",
            "duration_ms",
            "prompt_length",
            "tool_result_size_bytes",
            "attempt",
        }
    )
    for k, v in extra.items():
        if v is None:
            continue
        nk = _rename.get(k, k)
        if k == "cost_usd":
            out[nk] = float(v) if not isinstance(v, str) else float(v)
        elif k in _numeric and isinstance(v, (int, float)):
            out[nk] = int(v)
        elif k in _numeric and isinstance(v, str) and k != "cost_usd":
            try:
                out[nk] = int(v)
            except ValueError:
                out[nk] = v
        else:
            out[nk] = v
    return out


def _cc_claude_log_record_attrs(
    base: dict,
    *,
    event_name: str,
    event_sequence: int,
    event_timestamp_iso: str,
    cx_app: str,
    cx_sub: str,
    extra: dict,
    profile: str,
) -> dict[str, str | int | float] | dict[str, object]:
    if profile == "dotted":
        return _cc_claude_log_attributes_dotted(
            base,
            event_name=event_name,
            event_sequence=event_sequence,
            event_timestamp_iso=event_timestamp_iso,
            cx_app=cx_app,
            cx_sub=cx_sub,
            extra=extra,
        )
    return _cc_claude_log_attributes_flat(
        base,
        event_name=event_name,
        event_sequence=event_sequence,
        event_timestamp_iso=event_timestamp_iso,
        cx_app=cx_app,
        cx_sub=cx_sub,
        extra=extra,
    )


def _sim_claude_usage_token_counts() -> tuple[int, int]:
    """
    Input/output token counts for Claude Code metrics and the ``user_prompt`` span.
    Combined input+output is drawn in ``[SIM_CLAUDE_TOTAL_TOKENS_MIN, SIM_CLAUDE_TOTAL_TOKENS_MAX]``
    (defaults ~4.5k–~225k per simulated turn so charts do not auto-scale to one giant spike and
    hide everything else). Override env for stress tests (e.g. ``50_000_000`` max).
    """
    t0 = _env_int("SIM_CLAUDE_TOTAL_TOKENS_MIN", 4_500)
    t1 = _env_int("SIM_CLAUDE_TOTAL_TOKENS_MAX", 225_000)
    lo, hi = min(t0, t1), max(t0, t1)
    pair_total = random.randint(lo, hi)
    inp_frac = random.uniform(0.55, 0.75)
    input_tokens = max(1, int(pair_total * inp_frac))
    output_tokens = max(1, pair_total - input_tokens)
    return input_tokens, output_tokens


def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    """ n'th weekday in month (``n`` >= 1); ``weekday`` matches ``datetime.weekday()`` (Mon=0). """
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    first_hit = first + timedelta(days=offset)
    return first_hit + timedelta(weeks=n - 1)


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    """Last ``weekday`` in month."""
    _, ndays = calendar.monthrange(year, month)
    last = date(year, month, ndays)
    while last.weekday() != weekday:
        last -= timedelta(days=1)
    return last


def _us_fixed_holiday_observed(h: date) -> date:
    """Federal Monday–Friday observance when a fixed calendar holiday falls on Sat/Sun."""
    wd = h.weekday()
    if wd == 5:  # Saturday -> Friday
        return h - timedelta(days=1)
    if wd == 6:  # Sunday -> Monday
        return h + timedelta(days=1)
    return h


def _us_federal_observed_dates_one_year(y: int) -> frozenset[date]:
    """US federal holidays for year ``y`` using standard Monday–Friday observed dates."""
    out: set[date] = set()
    # Floating (already weekdays)
    out.add(_nth_weekday_of_month(y, 1, 0, 3))   # MLK Day
    out.add(_nth_weekday_of_month(y, 2, 0, 3))   # Presidents / Washington's Birthday
    out.add(_last_weekday_of_month(y, 5, 0))    # Memorial Day
    if y >= 2021:
        out.add(_us_fixed_holiday_observed(date(y, 6, 19)))  # Juneteenth (federal 2021+)
    out.add(_us_fixed_holiday_observed(date(y, 7, 4)))
    out.add(_nth_weekday_of_month(y, 9, 0, 1))   # Labor Day
    out.add(_nth_weekday_of_month(y, 10, 0, 2))  # Columbus Day (federal)
    out.add(_us_fixed_holiday_observed(date(y, 11, 11)))  # Veterans Day
    out.add(_nth_weekday_of_month(y, 11, 3, 4))  # Thanksgiving (4th Thursday)
    out.add(_us_fixed_holiday_observed(date(y, 12, 25)))
    out.add(_us_fixed_holiday_observed(date(y, 1, 1)))  # New Year's (may spill to adjacent year)
    return frozenset(out)


@lru_cache(maxsize=32)
def _us_federal_observed_dates_near(year: int) -> frozenset[date]:
    """Observed federal holiday calendar dates near ``year`` (handles Dec 31 / Jan 2 spillover)."""
    acc: set[date] = set()
    for yy in (year - 1, year, year + 1):
        acc.update(_us_federal_observed_dates_one_year(yy))
    return frozenset(acc)


def _is_us_federal_holiday_local(d: date) -> bool:
    """True if ``d`` is a federally observed holiday date in the US (local calendar date)."""
    return d in _us_federal_observed_dates_near(d.year)


def _claude_single_region_calendar_scale(
    tz_name: str,
    *,
    off_rate: float,
    lo: int,
    hi: int,
    apply_us_holidays: bool,
) -> float:
    """
    Activity factor ``1.0`` during that region's local office window Mon–Fri, else ``off_rate``.
    When ``apply_us_holidays`` and global ``SIM_CLAUDE_US_HOLIDAYS_ENABLE``, US federal observed dates use ``off_rate``.
    """
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    now = datetime.now(tz)
    local_d = now.date()
    if (
        apply_us_holidays
        and _env_bool("SIM_CLAUDE_US_HOLIDAYS_ENABLE", True)
        and _is_us_federal_holiday_local(local_d)
    ):
        return off_rate
    if now.weekday() >= 5:
        return off_rate
    h = now.hour
    if lo <= h < hi:
        return 1.0
    return off_rate


def _claude_workforce_weights_three_region() -> tuple[float, float, float]:
    """Europe / Asia / Pacific workforce fractions (normalized to sum to 1)."""
    w_e = max(0.0, _env_float("SIM_CLAUDE_WORKFORCE_WEIGHT_EUROPE", 1.0 / 3.0))
    w_a = max(0.0, _env_float("SIM_CLAUDE_WORKFORCE_WEIGHT_ASIA", 1.0 / 3.0))
    w_p = max(0.0, _env_float("SIM_CLAUDE_WORKFORCE_WEIGHT_PACIFIC", 1.0 / 3.0))
    tot = w_e + w_a + w_p
    if tot <= 0:
        return (1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0)
    return (w_e / tot, w_a / tot, w_p / tot)


def _claude_worldwide_workforce_weight_scale(off_rate: float, lo: int, hi: int) -> float:
    """
    Blend three regional calendars (default Europe / Asia / Pacific): each region at full ``1.0`` only on its
    local weekdays during ``[lo, hi)``; nights and weekends in that zone use ``off_rate``. Weights sum the regions.
    """
    ws = _env_int("SIM_CLAUDE_WORKFORCE_OFFICE_HOUR_START", lo)
    we = _env_int("SIM_CLAUDE_WORKFORCE_OFFICE_HOUR_END", hi)
    lo_r, hi_r = min(ws, we), max(ws, we)

    tz_e = os.environ.get("SIM_CLAUDE_WORKFORCE_TZ_EUROPE", "Europe/London").strip() or "Europe/London"
    tz_a = os.environ.get("SIM_CLAUDE_WORKFORCE_TZ_ASIA", "Asia/Tokyo").strip() or "Asia/Tokyo"
    tz_p = os.environ.get("SIM_CLAUDE_WORKFORCE_TZ_PACIFIC", "America/Los_Angeles").strip() or "America/Los_Angeles"

    use_us_e = _env_bool("SIM_CLAUDE_WORKFORCE_US_HOLIDAYS_EUROPE", False)
    use_us_a = _env_bool("SIM_CLAUDE_WORKFORCE_US_HOLIDAYS_ASIA", False)
    use_us_p = _env_bool("SIM_CLAUDE_WORKFORCE_US_HOLIDAYS_PACIFIC", True)

    w_e, w_a, w_p = _claude_workforce_weights_three_region()
    s_e = _claude_single_region_calendar_scale(
        tz_e, off_rate=off_rate, lo=lo_r, hi=hi_r, apply_us_holidays=use_us_e
    )
    s_a = _claude_single_region_calendar_scale(
        tz_a, off_rate=off_rate, lo=lo_r, hi=hi_r, apply_us_holidays=use_us_a
    )
    s_p = _claude_single_region_calendar_scale(
        tz_p, off_rate=off_rate, lo=lo_r, hi=hi_r, apply_us_holidays=use_us_p
    )
    return max(0.0, min(1.0, w_e * s_e + w_a * s_a + w_p * s_p))


def _claude_office_hours_weight_scale() -> float:
    """
    Scale factor for Claude Code **selection weight** for “off” periods.

    **Single region** (``SIM_CLAUDE_WORLDWIDE_WORKFORCE_ENABLE=false``): Mon–Fri in ``SIM_CLAUDE_OFFICE_TZ`` during
    ``SIM_CLAUDE_OFFICE_HOUR_*``; nights/weekends and optional US holidays use ``SIM_CLAUDE_OFF_HOURS_ACTIVITY``.

    **Worldwide** (``SIM_CLAUDE_WORLDWIDE_WORKFORCE_ENABLE=true``): three cohorts (Europe / Asia / Pacific) with
    independent local calendars, weights ``SIM_CLAUDE_WORKFORCE_WEIGHT_*``, timezones ``SIM_CLAUDE_WORKFORCE_TZ_*``,
    office window ``SIM_CLAUDE_WORKFORCE_OFFICE_HOUR_*`` (fallback to ``SIM_CLAUDE_OFFICE_HOUR_*``). Effective scale
    is the weighted sum of each region's factor (each region is ``1.0`` or ``off_rate``). Pacific defaults to US
    federal holiday backoff; EU/Asia default off unless ``SIM_CLAUDE_WORKFORCE_US_HOLIDAYS_*`` is set.

    Set ``SIM_CLAUDE_OFFICE_HOURS_ENABLE=true`` to enable.
    """
    if not _env_bool("SIM_CLAUDE_OFFICE_HOURS_ENABLE", False):
        return 1.0
    off_rate = max(0.0, min(1.0, _env_float("SIM_CLAUDE_OFF_HOURS_ACTIVITY", 0.2)))
    start_h = _env_int("SIM_CLAUDE_OFFICE_HOUR_START", 9)
    end_h = _env_int("SIM_CLAUDE_OFFICE_HOUR_END", 18)
    lo, hi = min(start_h, end_h), max(start_h, end_h)

    if _env_bool("SIM_CLAUDE_WORLDWIDE_WORKFORCE_ENABLE", False):
        return _claude_worldwide_workforce_weight_scale(off_rate, lo, hi)

    tz_name = os.environ.get("SIM_CLAUDE_OFFICE_TZ", "America/Los_Angeles").strip() or "America/Los_Angeles"
    return _claude_single_region_calendar_scale(
        tz_name,
        off_rate=off_rate,
        lo=lo,
        hi=hi,
        apply_us_holidays=_env_bool("SIM_CLAUDE_US_HOLIDAYS_ENABLE", True),
    )


def _agent_selection_weight(agent_product: str) -> int:
    """
    Relative weights for ``random.choices`` (higher = selected more often per iteration).
    Override with env ``SIM_WEIGHT_<PRODUCT>`` e.g. ``SIM_WEIGHT_GEMINI_CLI``, ``SIM_WEIGHT_CODEX``.
    """
    defaults = {
        # Slightly favor Claude so token/cost panels see samples without long waits (override with SIM_WEIGHT_*).
        "claude_code": 6,
        "gemini_cli": 5,
        "codex": 5,
        "cursor": 5,
        "copilot_cli": 5,
    }
    key = agent_product.upper().replace("-", "_")
    return max(1, _env_int(f"SIM_WEIGHT_{key}", defaults.get(agent_product, 1)))


# Three release lines per simulated product (instrumentation scope + app.version / dashboards).
AGENT_TOOL_VERSIONS = {
    "chatgpt": ("1.4.0", "1.5.0", "1.6.0"),
    # Near real Claude Code CLI builds (Coralogix EU2 exports use 2.1.104 / 2.1.107, etc.).
    "claude_code": ("2.1.104", "2.1.107", "2.1.110"),
    "gemini_cli": ("v1.0.0", "v1.1.0", "v1.2.0"),
    "github_copilot": ("1.100.0", "1.150.0", "1.200.0"),
    # C4C ``tags.cursor.cursor_version`` includes builds such as 2.6.22 / 3.2.11 / 3.2.16.
    "cursor": ("2.6.22", "3.2.11", "3.2.16", "0.45.0"),
    "windsurf": ("1.0.0", "1.1.0", "1.2.0"),
    "amazon_q": ("1.0.0", "1.1.0", "1.2.0"),
    "jetbrains_ai": ("242.1", "243.1", "244.1"),
    "azure_openai": ("2024-11", "2025-01", "2025-03"),
    "perplexity": ("1.0.0", "1.1.0", "2.0.0"),
    "groq": ("0.5.0", "0.6.0", "0.7.0"),
    "deepseek": ("1.0.0", "1.1.0", "1.2.0"),
    "mistral": ("1.0.0", "1.1.0", "1.2.0"),
    "grok": ("2.0.0", "2.1.0", "3.0.0-beta"),
    "codex": ("v1.0.0", "v1.1.0", "v2.0.0"),
}


def tool_version_for(agent_product: str) -> str:
    return random.choice(AGENT_TOOL_VERSIONS.get(agent_product, ("1.0.0", "1.1.0", "1.2.0")))


# Coralogix AI Tools: Gemini/Claude use OTLP semantic ``service.name`` on Resource; **Codex** in this sim
# uses ``service_name`` on Resource/logs/spans so DataPrime matches ``$d.resource.attributes['service_name']``.
# - Codex: https://github.com/coralogix/ai-agent-instrumentation/blob/master/codex/README.md
#   traces & logs → ``codex_cli_rs``
#   Log event catalog mirrors OpenAI https://developers.openai.com/codex/config-advanced (Observability).
# - Gemini CLI: service name `gemini-cli`
# - Cursor IDE: flat ``cursor.*`` on spans (see ``sim/cursor.py``); resource ``service.name`` = ``cursor`` by default.
# We use separate TracerProviders so each CLI export carries the real client service name.
@dataclass(frozen=True)
class SimCliTracerProviders:
    gemini: TracerProvider
    codex: TracerProvider
    claude: TracerProvider
    cursor: TracerProvider
    github_copilot: TracerProvider


_sim_cli: SimCliTracerProviders | None = None

# Prometheus Counters (remote_write to Coralogix) — metric basenames match AI Center / HAR-style PromQL (``*_total``).
# Parity checks: compare samples to reference EU2 telemetry (see repo rules / parity docs).
_prom_registry: CollectorRegistry | None = None
_prom_gem_session = None
_prom_gem_token = None
_prom_gem_token_coralogix = None
_prom_gem_token_tokens = None
_prom_gem_api = None
_prom_gem_api_latency = None
_prom_gem_lines = None
_prom_gem_lines_coralogix = None
_prom_gem_file_op = None
_prom_gem_tool_call = None
_prom_gem_tool_latency = None
_prom_gem_model_routing_latency = None
_prom_gem_agent_duration = None
_prom_gem_agent_run = None
_prom_codex_run_turn = None
_prom_codex_token = None
_prom_copilot_session = None
_prom_copilot_token = None
_prom_copilot_tool = None
_prom_copilot_tool_dur = None
_prom_copilot_chat_dur = None
_prom_copilot_agent_dur = None
_prom_copilot_ttft = None
_prom_copilot_premium = None
_prom_copilot_cache = None
_prom_copilot_edit = None
_prom_copilot_session_repo = None
_prom_rw_stop: threading.Event | None = None

# Codex OTLP logs (``codex.sse_event``) — separate Resource ``codex_cli_rs``; global logger stays Claude.
_codex_log_provider: LoggerProvider | None = None
_codex_otlp_logger = None

# Gemini CLI OTLP logs (``gemini_cli.*`` / ``event_name``) — Resource ``gemini-cli`` + ``gemini-cli-sessions``
# (this sim). In some Coralogix tenants, **real** interactive CLI exports are tagged
# ``cx.subsystem.name`` = ``gemini-cli-sessions-real`` for isolation — use that in DataPrime/Traces
# when sampling live sessions, not the sim’s default.
_gemini_log_provider: LoggerProvider | None = None
_gemini_otlp_logger = None

# Copilot CLI OTLP logs (session.start, tool.call, inference events) — Resource ``github-copilot``.
_copilot_log_provider: LoggerProvider | None = None
_copilot_otlp_logger = None

# Second Claude OTLP log provider when ``SIM_CLAUDE_TELEMETRY_PROFILE=both`` (dotted Resource + scope).
_claude_dotted_log_provider: LoggerProvider | None = None

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


def _cli_resource(service_name: str, cx_application_name: str, cx_subsystem_name: str) -> Resource:
    return Resource.create(
        {
            "service.name": service_name,
            "cx.application.name": cx_application_name,
            "cx.subsystem.name": cx_subsystem_name,
            "deployment.environment": os.environ.get("DEPLOYMENT_ENVIRONMENT", "test-cluster"),
        }
    )


def _cursor_hook_resource(service_name: str, cx_application_name: str, cx_subsystem_name: str) -> Resource:
    """Align with real ``cursor-coralogix`` hook process Resource (no ``deployment.environment``)."""
    return Resource.create(
        {
            "service.name": service_name,
            "service.version": os.environ.get("SIM_CURSOR_HOOK_SERVICE_VERSION", "2.0.0").strip() or "2.0.0",
            "cx.application.name": cx_application_name,
            "cx.subsystem.name": cx_subsystem_name,
            "telemetry.sdk.name": os.environ.get("SIM_CURSOR_SDK_NAME", "cursor-coralogix-hook"),
            "telemetry.sdk.language": "python",
            "telemetry.sdk.version": os.environ.get("SIM_CURSOR_SDK_VERSION", "1.40.0").strip() or "1.40.0",
        }
    )


def _dotted_claude_otel_resource(
    service_name: str,
    cx_application_name: str,
    cx_subsystem_name: str,
) -> Resource:
    """
    OTLP log Resource for the **dotted** Claude log profile (semantic ``service.*``, host/os, SDK merge).
    ``cx.application.name`` / ``cx.subsystem.name`` on Resource drive Coralogix ``$l.*`` facets
    (exporters read resource attributes, not log-record attrs). Per-record ``cx.*`` is still duplicated.
    """
    ver = os.environ.get("SIM_CLAUDE_DOTTED_SERVICE_VERSION", "1.0.33").strip() or "1.0.33"
    return Resource.create(
        {
            "service.name": service_name,
            "service.version": ver,
            "cx.application.name": cx_application_name,
            "cx.subsystem.name": cx_subsystem_name,
            "host.arch": os.environ.get("SIM_HOST_ARCH", platform.machine()),
            "os.type": os.environ.get("SIM_OS_TYPE", platform.system().lower()),
            "os.version": os.environ.get("SIM_OS_VERSION", platform.release()),
        }
    )


def _claude_code_otel_resource(
    service_name: str,
    _cx_application_name: str,
    _cx_subsystem_name: str,
) -> Resource:
    """
    OTLP Resource for Claude Code logs/traces.

    Uses **semantic** keys (``service.name``, ``os.type``, …) so the OTLP exporter stays valid.
    Coralogix often **also** shows flattened ``service_name`` / ``os_type`` / ``host_arch`` on JSON
    exports depending on pipeline; facets for app/subsystem remain on each log via ``_cx_log_record_attrs``.
    """
    ver = os.environ.get("SIM_CC_APP_VERSION") or tool_version_for("claude_code")
    return Resource(
        {
            "service.name": service_name,
            "service.version": ver,
            "os.type": os.environ.get("SIM_OS_TYPE", platform.system().lower()),
            "os.version": os.environ.get("SIM_OS_VERSION", platform.release()),
            "host.arch": os.environ.get("SIM_HOST_ARCH", platform.machine()),
        }
    )


def _cx_log_record_attrs(application: str, subsystem: str) -> dict[str, str]:
    """
    Duplicate Coralogix application/subsystem on each log record.
    Resource must also carry ``cx.*`` (see ``_cli_resource`` / ``_dotted_claude_otel_resource``);
    exporters map ``$l.applicationname`` / ``$l.subsystemname`` from Resource attributes.
    """
    return {
        "cx.application.name": application,
        "cx.subsystem.name": subsystem,
    }


def _codex_otel_env_tag() -> str:
    """Mirrors OpenAI Codex ``[otel] environment`` (see Advanced Configuration → Observability)."""
    return os.environ.get("SIM_CODEX_OTEL_ENVIRONMENT", os.environ.get("DEPLOYMENT_ENVIRONMENT", "dev"))


def _codex_service_name() -> str:
    """Codex OTLP resource ``service_name`` (``SIM_CODEX_SERVICE_NAME``, default ``codex_cli_rs``)."""
    return os.environ.get("SIM_CODEX_SERVICE_NAME", "codex_cli_rs")


def _codex_span_service_label_attrs() -> dict[str, str]:
    """Duplicate Codex service on spans: semantic ``service.name`` + legacy ``service_name``."""
    sn = _codex_service_name()
    return {ResourceAttributes.SERVICE_NAME: sn, "service_name": sn}


def _emit_codex_otlp_structured_log(
    *,
    body: str,
    attributes: dict[str, str | int | float | bool],
    trace_id: int,
    span_id: int,
    timestamp_ns: int | None = None,
) -> None:
    """
    Emit one structured OTLP log line for OpenAI Codex CLI observability.

    Event ``body`` values follow the catalog in
    https://developers.openai.com/codex/config-advanced (Observability and telemetry → What gets emitted):
    ``codex.conversation_starts``, ``codex.api_request``, ``codex.sse_event``, ``codex.user_prompt``,
    ``codex.tool_decision``, ``codex.tool_result``, etc.
    """
    if _codex_otlp_logger is None:
        return
    cx_app = os.environ.get("CODEX_CX_APPLICATION_NAME", "codex")
    cx_sub = os.environ.get("CODEX_CX_SUBSYSTEM_NAME", "codex-sessions")
    # Do not put service_name on log record attributes — it belongs on Resource only.
    merged: dict[str, str | int | float | bool] = {
        **_cx_log_record_attrs(cx_app, cx_sub),
        "otel.environment": _codex_otel_env_tag(),
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
        resource=_codex_otlp_logger.resource,
    )
    _codex_otlp_logger.emit(rec)


def _gemini_installation_id() -> str:
    return os.environ.get(
        "SIM_GEMINI_INSTALLATION_ID",
        _stable_uuid(os.environ.get("POD_NAME", socket.gethostname()) + ":gemini-cli-install"),
    )


# Pinned file/lines/tool dimensions per user (``SIM_GEMINI_PIN_METRIC_LABELS``) — low churn for PromQL.
_gem_metric_pins: dict[str, tuple[str, str, str, str, str, str]] = {}
# ``conversation_id`` → ``gen_ai.request.model`` when ``SIM_GEMINI_MODEL`` is unset (stable for session lifetime).
_gem_session_models: dict[str, str] = {}
# Pool: ``sim.common.constants.GEMINI_CLI_MODELS`` (imported above).
# ``SIM_GEMINI_CONCURRENT_LONG_SESSIONS`` independent slots: each holds one roster user until its deadline
# (``SIM_GEMINI_LONG_SESSION_SEC`` or Claude fallback) so many long-lived Gemini sessions overlap in time.
_gem_slot_users: list[dict | None] = []
_gem_slot_deadlines: list[float] = []
_gem_slot_rr: int = 0
# EWMA (lines added, lines removed) per ``_gemini_metric_pin_key`` when ``SIM_GEMINI_SMOOTH_PRODUCTIVITY`` is on.
_gem_loc_ema: dict[str, tuple[float, float]] = {}


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
    global _gem_slot_users, _gem_slot_deadlines, _gem_slot_rr
    if len(_gem_slot_users) != n_slots:
        _gem_slot_users = [None] * n_slots
        _gem_slot_deadlines = [0.0] * n_slots
        _gem_slot_rr = 0

    strat = os.environ.get("SIM_GEMINI_SESSION_SLOT_STRATEGY", "random").strip().lower().replace("-", "_")
    if strat in ("round_robin", "rr"):
        i = _gem_slot_rr % n_slots
        _gem_slot_rr += 1
    else:
        i = random.randrange(n_slots)

    now = time.monotonic()
    if _gem_slot_users[i] is None or now >= _gem_slot_deadlines[i]:
        _gem_slot_users[i] = _claude_roster_core_user(str(uuid.uuid4()))
        _gem_slot_deadlines[i] = now + float(dur)
    return dict(_gem_slot_users[i])


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
    global _gem_session_models
    fixed = os.environ.get("SIM_GEMINI_MODEL", "").strip()
    if fixed:
        return fixed
    cid = conversation_id.strip() or "unknown-session"
    got = _gem_session_models.get(cid)
    if got is not None:
        return got
    got = random.choice(GEMINI_CLI_MODELS)
    _gem_session_models[cid] = got
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
    global _gem_metric_pins
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
    got = _gem_metric_pins.get(pin_key)
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
    _gem_metric_pins[pin_key] = got
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
    global _gem_loc_ema
    prev = _gem_loc_ema.get(pin_key)
    if prev is None:
        ema_a, ema_r = float(raw_add), float(raw_rem)
    else:
        pa, pr = prev
        ema_a = alpha * raw_add + (1.0 - alpha) * pa
        ema_r = alpha * raw_rem + (1.0 - alpha) * pr
    _gem_loc_ema[pin_key] = (ema_a, ema_r)
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
    if _gemini_otlp_logger is None or not _env_bool("SIM_GEMINI_LOGS", True):
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
            resource=_gemini_otlp_logger.resource,
        )
        _gemini_otlp_logger.emit(rec)

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


# Mirrors real Gemini CLI (Node) spans — see Coralogix/Jaeger export shape.
GEMINI_AGENT_DESCRIPTION = (
    "Gemini CLI is an open-source AI agent that brings the power of Gemini directly "
    "into your terminal. It is designed to be a terminal-first, extensible, and "
    "powerful tool for developers, engineers, SREs, and beyond."
)

GEMINI_SAMPLE_PROMPTS = (
    "tell me something interesting about deep sea diving",
    "explain how vector databases differ from traditional RDBMS for embeddings",
    "what are pragmatic ways to cut cloud egress cost on Kubernetes",
    "summarize OpenTelemetry gen_ai semantic conventions in five bullets",
    "how do I safely rotate database credentials in a CI/CD pipeline",
)

CLAUDE_CODE_AGENT_DESCRIPTION = (
    "Claude Code is Anthropic's agentic coding tool: it runs in your terminal, reads your repo, "
    "edits files, runs commands, and uses tools under your control—aligned with Coralogix "
    "claude_code.* telemetry and Code Agents dashboards."
)

# Model pool: ``sim.common.constants._CLAUDE_CODE_MODELS`` (imported above).

CODEX_AGENT_DESCRIPTION = (
    "OpenAI Codex is an AI coding agent for your terminal and IDE: it reasons over your codebase, "
    "proposes edits, runs commands, and integrates with your workflow—similar export shape to other "
    "CLI agents (user_prompt span, gen_ai.*, cx.*)."
)

CODEX_SAMPLE_PROMPTS = (
    "implement a small LRU cache with TTL in Python",
    "fix the race condition in the connection pool shutdown path",
    "add OpenAPI request validation middleware",
    "write a unit test for edge cases in parse_config",
    "optimize this hot loop for fewer allocations",
)


_FIRST_NAMES = (
    "Alex",
    "Sam",
    "Jordan",
    "Taylor",
    "Riley",
    "Casey",
    "Morgan",
    "Quinn",
    "Avery",
    "Skyler",
    "Reese",
    "Drew",
    "Jamie",
    "Cameron",
    "Rowan",
)

_LAST_NAMES = (
    "Nguyen",
    "Patel",
    "Garcia",
    "Okonkwo",
    "Silva",
    "Kim",
    "Cohen",
    "Hansen",
    "Iyer",
    "Martinez",
    "Lindberg",
    "Fischer",
    "Okafor",
    "Tanaka",
    "Bernstein",
)

# Fixed roster (~middle-sized org): same 225 identities for every agent sim (Claude, Gemini, Codex, …).
_CORALOGIX_TEAM_ROSTER_SIZE = 225


def _coralogix_roster_email_local(
    i: int,
    first: str,
    last: str,
    *,
    occurrence: int,
) -> str:
    """
    Mailbox local-part for roster synthetic users.

    Default ``natural``: ``alex.silva`` / ``alex.silva2`` when names collide.
    ``underscore``: ``team067_alex_silva``. ``legacy``: ``team.067.alex.silva``.
    """
    fmt = os.environ.get("SIM_ROSTER_EMAIL_FORMAT", "natural").strip().lower()
    fn = first.lower()
    ln = last.lower()
    if fmt in ("legacy", "dots", "dotted", "period", "periods"):
        return f"team.{i:03d}.{fn}.{ln}"
    if fmt in ("underscore", "team_underscore", "old_underscore"):
        return f"team{i:03d}_{fn}_{ln}"
    base = f"{fn}.{ln}"
    if occurrence <= 1:
        return base
    return f"{base}{occurrence}"


def _build_coralogix_team_users() -> tuple[dict[str, str], ...]:
    users: list[dict[str, str]] = []
    name_counts: dict[str, int] = {}
    for i in range(_CORALOGIX_TEAM_ROSTER_SIZE):
        digest = hashlib.sha256(f"coralogix:sim:team:{i}".encode()).digest()
        first = _FIRST_NAMES[digest[0] % len(_FIRST_NAMES)]
        last = _LAST_NAMES[digest[1] % len(_LAST_NAMES)]
        base_key = f"{first.lower()}.{last.lower()}"
        name_counts[base_key] = name_counts.get(base_key, 0) + 1
        occ = name_counts[base_key]
        local = _coralogix_roster_email_local(i, first, last, occurrence=occ)
        email = f"{local}@coralogix.com"
        account_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"coralogix:sim:team:member:{i}"))
        user_id = hashlib.sha256(f"coralogix:sim:team:uid:{i}".encode()).hexdigest()
        users.append(
            {
                "user.account_uuid": account_uuid,
                "user.id": user_id,
                "user.name": f"{first} {last}",
                "user.email": email,
            }
        )
    return tuple(users)


_CORALOGIX_TEAM_USERS: tuple[dict[str, str], ...] = _build_coralogix_team_users()

# ``SIM_CLAUDE_ROSTER_STRATEGY=round_robin``: advance once per ``_claude_roster_core_user`` call (``both`` uses one call per emit).
_cc_roster_rr_idx = 0
# When ``SIM_CLAUDE_STABLE_SESSION_PER_USER`` is false and ``SIM_CLAUDE_LONG_SESSION_SEC`` > 0: reuse one
# random ``session.id`` (UUID) across iterations so ``random_coralogix_identity(session_id)`` stays on one roster user.
_cc_long_session_id: str | None = None
_cc_long_session_deadline: float = 0.0
# When ``SIM_CLAUDE_LONG_SESSION_SEC`` > 0: **parallel slots** (``SIM_CLAUDE_CONCURRENT_LONG_SESSIONS``) each pin one
# roster user for ``dur`` seconds — multiple long-lived Claude identities at once (like Gemini slots).
_cc_slot_users: list[dict | None] = []
_cc_slot_deadlines: list[float] = []
_cc_slot_rr: int = 0
# ``SIM_CLAUDE_PIN_METRIC_LABELS`` (default true): stable ``app.version``/``service.version``/``model`` per pin key
# so Prometheus scrapes hit the **same** time series across iterations (``increase()`` needs ≥2 samples).
_cc_metric_label_pins: dict[str, tuple[str, str]] = {}
# Per roster user: ``user_key -> (session_id, monotonic deadline)`` for rotating ``session.id``.
_cc_user_session_ids: dict[str, tuple[str, float]] = {}


def _apply_claude_dotted_email_domain(user_attrs: dict) -> None:
    """Normalize ``user.email`` to ``local@coralogix.com`` (same suffix as roster users)."""
    raw = str(user_attrs.get("user.email", "user@coralogix.com"))
    if "@" in raw:
        local = raw.split("@", 1)[0].strip() or "user"
    else:
        local = raw.strip() or "user"
    user_attrs["user.email"] = f"{local}@coralogix.com"


def _claude_roster_core_user(session_id: str) -> dict:
    """
    Pick one roster row for Claude Code metrics/logs.

    - ``hash`` (default): stable per ``session_id`` (``random_coralogix_identity``).
    - ``round_robin`` / ``rr``: cycle ``_CORALOGIX_TEAM_USERS`` so many users get non-zero totals per wall clock.
    """
    strat = os.environ.get("SIM_CLAUDE_ROSTER_STRATEGY", "hash").strip().lower().replace("-", "_")
    if strat in ("round_robin", "rr"):
        global _cc_roster_rr_idx
        idx = _cc_roster_rr_idx % len(_CORALOGIX_TEAM_USERS)
        _cc_roster_rr_idx += 1
        return dict(_CORALOGIX_TEAM_USERS[idx])
    return random_coralogix_identity(session_id)


def _claude_user_identity_flavor(session_id: str, flavor: str) -> dict:
    """``flat``: roster @coralogix.com; ``dotted``: same local-part @coralogix.com (normalized)."""
    d = dict(_claude_roster_core_user(session_id))
    if flavor == "dotted":
        _apply_claude_dotted_email_domain(d)
    return d


def random_coralogix_identity(session_id: str) -> dict:
    """
    Stable end-user identity for a session/trace: same ``session_id`` always maps to the same
    roster entry from ``_CORALOGIX_TEAM_USERS`` (225 fixed @coralogix.com users shared by all sims).
    """
    key = session_id.strip() or "unknown-session"
    idx = int(hashlib.sha256(key.encode()).hexdigest(), 16) % len(_CORALOGIX_TEAM_USERS)
    return dict(_CORALOGIX_TEAM_USERS[idx])


def random_claude_user_identity(session_id: str) -> dict:
    """Roster user; dotted profile normalizes email when active telemetry profile is ``dotted`` (not ``both``)."""
    p = _claude_telemetry_profile()
    if p == "dotted":
        return _claude_user_identity_flavor(session_id, "dotted")
    return _claude_user_identity_flavor(session_id, "flat")


def _claude_long_session_trace_id() -> str:
    """
    Return ``session.id`` for Claude Code when ``SIM_CLAUDE_STABLE_SESSION_PER_USER`` is **false**.

    Default (``SIM_CLAUDE_LONG_SESSION_SEC`` unset or 0): new UUID each iteration — many roster users
    per hour when ``SIM_FORCE_AGENT=claude_code``.

    When ``SIM_CLAUDE_LONG_SESSION_SEC`` > 0: reuse one UUID until wall-clock duration expires so
    ``sum by (user_id, user_email) (increase(...[60m]))`` shows the same user for hours, like the flat profile.
    """
    global _cc_long_session_id, _cc_long_session_deadline
    dur = _env_float("SIM_CLAUDE_LONG_SESSION_SEC", 0.0)
    if dur <= 0:
        return str(uuid.uuid4())
    now = time.monotonic()
    if _cc_long_session_id is None or now >= _cc_long_session_deadline:
        _cc_long_session_id = str(uuid.uuid4())
        _cc_long_session_deadline = now + float(dur)
    return _cc_long_session_id


def _claude_stable_session_id_from_roster_user(user: dict) -> str:
    """Deterministic ``session.id`` for a roster user (stable across process restarts for the same account uuid)."""
    acc = str(user.get("user.account_uuid", "")).strip()
    uid = str(user.get("user.id", "")).strip()
    key = acc or uid or "unknown-user"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "otel-ai-agent-sim:claude:session-per-user:" + key))


def _claude_roster_user_key(user: dict) -> str:
    acc = str(user.get("user.account_uuid", "")).strip()
    uid = str(user.get("user.id", "")).strip()
    return acc or uid or "unknown-user"


def _claude_session_id_rotate_sec() -> float:
    """
    Wall-clock window to reuse the same ``session.id`` for one roster user.

    ``SIM_CLAUDE_SESSION_ID_ROTATE_SEC`` (default 3600 when ``SIM_CLAUDE_STABLE_SESSION_PER_USER``).
    Optional ``SIM_CLAUDE_SESSION_ID_ROTATE_SEC_MIN`` / ``_MAX`` pick a random duration per session (e.g. 386–900
    for ~3–7 sessions in a 45m user window). Set ``SIM_CLAUDE_SESSION_ID_ROTATE_SEC=0`` for eternal uuid5 per user.
    """
    if not _env_bool("SIM_CLAUDE_STABLE_SESSION_PER_USER", True):
        return 0.0
    lo_raw = os.environ.get("SIM_CLAUDE_SESSION_ID_ROTATE_SEC_MIN", "").strip()
    hi_raw = os.environ.get("SIM_CLAUDE_SESSION_ID_ROTATE_SEC_MAX", "").strip()
    if lo_raw and hi_raw:
        lo = max(1.0, _env_float("SIM_CLAUDE_SESSION_ID_ROTATE_SEC_MIN", lo_raw))
        hi = max(lo, _env_float("SIM_CLAUDE_SESSION_ID_ROTATE_SEC_MAX", hi_raw))
        return random.uniform(lo, hi)
    return max(0.0, _env_float("SIM_CLAUDE_SESSION_ID_ROTATE_SEC", 3600.0))


def _claude_session_id_for_roster_user(user: dict) -> str:
    """
    ``session.id`` for a roster user: stable across emits/logs/metrics in one session window, then rotates.

    Same ``user.email`` / account labels throughout; only ``session.id`` changes when the rotate window expires.
    """
    rotate = claude_user_session_rotate_duration_from_env(user)
    if rotate <= 0:
        return _claude_stable_session_id_from_roster_user(user)

    key = _claude_roster_user_key(user)
    now = time.monotonic()
    cached = _cc_user_session_ids.get(key)
    if cached is not None:
        sid, deadline = cached
        if now < deadline:
            return sid
    sid = str(uuid.uuid4())
    phase = claude_user_session_phase_offset(user)
    _cc_user_session_ids[key] = (sid, now + float(rotate) + phase)
    return sid


def _claude_emit_all_session_slots() -> bool:
    """When true and ``SIM_CLAUDE_LONG_SESSION_SEC`` > 0, emit once per active slot user each Claude cycle."""
    if not _env_bool("SIM_CLAUDE_EMIT_ALL_SESSION_SLOTS", True):
        return False
    return _env_float("SIM_CLAUDE_LONG_SESSION_SEC", 0.0) > 0


def _claude_ensure_session_slots() -> int:
    """Initialize / refresh parallel Claude user slots; return slot count."""
    dur = _env_float("SIM_CLAUDE_LONG_SESSION_SEC", 0.0)
    n_slots = max(1, _env_int("SIM_CLAUDE_CONCURRENT_LONG_SESSIONS", 15))
    global _cc_slot_users, _cc_slot_deadlines, _cc_slot_rr
    if len(_cc_slot_users) != n_slots:
        _cc_slot_users = [None] * n_slots
        _cc_slot_deadlines = [0.0] * n_slots
        _cc_slot_rr = 0
        if dur > 0 and _env_bool("SIM_CLAUDE_PREFILL_SESSION_SLOTS", True):
            now = time.monotonic()
            base = random.randrange(len(_CORALOGIX_TEAM_USERS))
            for i in range(n_slots):
                _cc_slot_users[i] = dict(_CORALOGIX_TEAM_USERS[(base + i) % len(_CORALOGIX_TEAM_USERS)])
                _cc_slot_deadlines[i] = now + float(dur)
    if dur > 0:
        now = time.monotonic()
        for i in range(n_slots):
            if _cc_slot_users[i] is None or now >= _cc_slot_deadlines[i]:
                _cc_slot_users[i] = _claude_roster_core_user(str(uuid.uuid4()) + f":slot:{i}")
                _cc_slot_deadlines[i] = now + float(dur)
    return n_slots


def _claude_roster_users_for_claude_code_emit() -> list[dict]:
    """All active slot users when fan-out is enabled; otherwise a one-element list."""
    dur = _env_float("SIM_CLAUDE_LONG_SESSION_SEC", 0.0)
    if dur <= 0:
        return [_claude_roster_core_user(str(uuid.uuid4()))]
    _claude_ensure_session_slots()
    return [dict(u) for u in _cc_slot_users if u is not None]


def _claude_roster_user_for_claude_code_emit() -> dict:
    """
    Pick the roster row for this Claude Code iteration.

    When ``SIM_CLAUDE_LONG_SESSION_SEC`` <= 0: fresh roster draw each call.

    When ``dur`` > 0: maintain ``SIM_CLAUDE_CONCURRENT_LONG_SESSIONS`` (default 15) independent slots; each slot keeps
    one user until its deadline. Emits pick a slot via ``SIM_CLAUDE_SESSION_SLOT_STRATEGY`` (``random`` or ``round_robin``).
    Use ``SIM_CLAUDE_EMIT_ALL_SESSION_SLOTS=true`` (default when ``dur`` > 0) to emit for every active slot each cycle.
    Each user maps to a rotating ``session.id`` via ``_claude_session_id_for_roster_user`` (see
    ``SIM_CLAUDE_SESSION_ID_ROTATE_SEC``).
    """
    dur = _env_float("SIM_CLAUDE_LONG_SESSION_SEC", 0.0)
    if dur <= 0:
        return _claude_roster_core_user(str(uuid.uuid4()))

    n_slots = _claude_ensure_session_slots()

    global _cc_slot_rr
    strat = os.environ.get("SIM_CLAUDE_SESSION_SLOT_STRATEGY", "random").strip().lower().replace("-", "_")
    if strat in ("round_robin", "rr"):
        i = _cc_slot_rr % n_slots
        _cc_slot_rr += 1
    else:
        i = random.randrange(n_slots)

    return dict(_cc_slot_users[i])


def _claude_otlp_span_user_attrs_from_roster(roster_user: dict) -> dict:
    """User attributes on Claude ``user_prompt`` spans (matches ``random_claude_user_identity`` profile rules)."""
    d = dict(roster_user)
    if _claude_telemetry_profile() == "dotted":
        _apply_claude_dotted_email_domain(d)
    return d


def _claude_metric_label_pin_key(roster_user: dict | None, session_id: str) -> str:
    """Stable key for pinning Prometheus label sets (prefer roster account uuid)."""
    if roster_user is not None:
        rk = str(roster_user.get("user.account_uuid", "")).strip() or str(roster_user.get("user.id", "")).strip()
        return rk or session_id.strip() or "unknown-pin"
    return session_id.strip() or "unknown-session"


def _claude_pinned_flat_version_and_model(pin_key: str) -> tuple[str, str]:
    """
    Return ``(app_version, model)`` pinned for ``pin_key`` so counter label sets stop rotating every emit.

    Only used when ``SIM_CLAUDE_PIN_METRIC_LABELS`` is true (see ``emit_claude_code_dashboard``).
    """
    global _cc_metric_label_pins
    got = _cc_metric_label_pins.get(pin_key)
    if got is not None:
        return got
    ver = os.environ.get("SIM_CC_APP_VERSION", "").strip() or tool_version_for("claude_code")
    mod = os.environ.get("SIM_CLAUDE_MODEL", "").strip() or random.choice(_CLAUDE_CODE_MODELS)
    got = (ver, mod)
    _cc_metric_label_pins[pin_key] = got
    return got


def emit_gemini_cli_user_prompt_span(conversation_id: str, roster_user: dict | None = None) -> None:
    """Replicate a real `gemini-cli` `user_prompt` span (OpenTelemetry → Coralogix shape)."""
    global _prom_gem_session, _prom_gem_token, _prom_gem_token_coralogix, _prom_gem_token_tokens, _prom_gem_api
    global _prom_gem_api_latency, _prom_gem_lines, _prom_gem_lines_coralogix, _prom_gem_file_op, _prom_gem_tool_call, _prom_gem_tool_latency
    global _prom_gem_model_routing_latency, _prom_gem_agent_duration, _prom_gem_agent_run
    if _sim_cli is None:
        raise RuntimeError("CLI trace providers not initialized")
    _gem_emit_started = time.perf_counter()
    ver = tool_version_for("gemini_cli")
    gemini_tracer = _sim_cli.gemini.get_tracer("gemini-cli", ver)
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
        if _prom_gem_session is not None and _prom_gem_token is not None:
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
                    _prom_gem_token.labels(*tv).inc(n)
                    if _prom_gem_token_coralogix is not None:
                        _prom_gem_token_coralogix.labels(*tv).inc(n)
                    if _prom_gem_token_tokens is not None:
                        _prom_gem_token_tokens.labels(*tv).inc(n)

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

                _prom_gem_session.labels(*_gem_standard_sess()).inc()
                _gem_standard_tok("input", inp)
                _gem_standard_tok("output", out)
                if cache > 0:
                    _gem_standard_tok("cache", cache)
                if thought > 0:
                    _gem_standard_tok("thought", thought)
                _tt_rate = 0.22 if _gem_smooth else 0.38
                if random.random() < _env_float("SIM_GEMINI_TOOL_TOKEN_SAMPLE_RATE", _tt_rate):
                    _gem_standard_tok("tool", random.randint(28, 220) if _gem_smooth else random.randint(8, 1200))
                if _prom_gem_api is not None:
                    _prom_gem_api.labels(*_gem_standard_api()).inc()
                if _prom_gem_api_latency is not None:
                    _prom_gem_api_latency.labels(*_gem_standard_hist_lat()).observe(float(api_ms))
                if _prom_gem_model_routing_latency is not None:
                    _prom_gem_model_routing_latency.labels(*_gem_standard_hist_lat()).observe(float(routing_ms))
                if _prom_gem_lines is not None:
                    # Upstream ``recordLinesChanged`` skips ``lines <= 0`` (metrics.ts).
                    if loc_added > 0:
                        _prom_gem_lines.labels(*_gem_standard_line("added")).inc(loc_added)
                        if _prom_gem_lines_coralogix is not None:
                            _prom_gem_lines_coralogix.labels(*_gem_standard_line("added")).inc(loc_added)
                    if loc_removed > 0:
                        _prom_gem_lines.labels(*_gem_standard_line("removed")).inc(loc_removed)
                        if _prom_gem_lines_coralogix is not None:
                            _prom_gem_lines_coralogix.labels(*_gem_standard_line("removed")).inc(loc_removed)
                if _prom_gem_file_op is not None:
                    file_op_fixed = os.environ.get("SIM_GEMINI_FILE_OPERATION", "").strip() or (
                        "update" if pin_metrics else ""
                    )
                    f_lo = _env_int("SIM_GEMINI_FILE_OPS_MIN", 4 if _gem_smooth else 2)
                    f_hi = max(f_lo, _env_int("SIM_GEMINI_FILE_OPS_MAX", 6 if _gem_smooth else 7))
                    n_file = random.randint(f_lo, f_hi)
                    for _ in range(n_file):
                        op = file_op_fixed or random.choice(("create", "read", "update"))
                        _prom_gem_file_op.labels(*_gem_standard_file(op)).inc()
                if _prom_gem_tool_call is not None:
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
                        _prom_gem_tool_call.labels(
                            *_gem_standard_tool(
                                dec,
                                fn,
                                "true" if random.random() > 0.08 else "false",
                                ttyp,
                            )
                        ).inc()
                        if _prom_gem_tool_latency is not None:
                            if _gem_smooth:
                                tl = int(random.triangular(80.0, 4200.0, 900.0))
                            else:
                                tl = random.randint(15, 12_000)
                            _prom_gem_tool_latency.labels(*_gem_standard_hist_tool(fn)).observe(float(tl))
            else:

                def _gem_tok(typ: str, n: float | int) -> None:
                    _prom_gem_token.labels(*gem_common, typ).inc(n)
                    if _prom_gem_token_coralogix is not None:
                        _prom_gem_token_coralogix.labels(*gem_common, typ).inc(n)

                _prom_gem_session.labels(cx_app, cx_sub, model).inc()
                _gem_tok("input", inp)
                _gem_tok("output", out)
                if cache > 0:
                    _gem_tok("cache", cache)
                if thought > 0:
                    _gem_tok("thought", thought)
                _tt_rate = 0.22 if _gem_smooth else 0.38
                if random.random() < _env_float("SIM_GEMINI_TOOL_TOKEN_SAMPLE_RATE", _tt_rate):
                    _gem_tok("tool", random.randint(28, 220) if _gem_smooth else random.randint(8, 1200))
                if _prom_gem_api is not None:
                    _prom_gem_api.labels(*gem_common, http_status).inc()
                if _prom_gem_api_latency is not None:
                    _prom_gem_api_latency.labels(*gem_common).observe(float(api_ms))
                if _prom_gem_model_routing_latency is not None:
                    _prom_gem_model_routing_latency.labels(*gem_common).observe(float(routing_ms))
                if _prom_gem_lines is not None:
                    if loc_added > 0:
                        _prom_gem_lines.labels(*gem_common, lines_fn, lang, "added").inc(loc_added)
                        if _prom_gem_lines_coralogix is not None:
                            _prom_gem_lines_coralogix.labels(*gem_common, lines_fn, lang, "added").inc(loc_added)
                    if loc_removed > 0:
                        _prom_gem_lines.labels(*gem_common, lines_fn, lang, "removed").inc(loc_removed)
                        if _prom_gem_lines_coralogix is not None:
                            _prom_gem_lines_coralogix.labels(*gem_common, lines_fn, lang, "removed").inc(loc_removed)
                if _prom_gem_file_op is not None:
                    file_op_fixed = os.environ.get("SIM_GEMINI_FILE_OPERATION", "").strip() or (
                        "update" if pin_metrics else ""
                    )
                    f_lo = _env_int("SIM_GEMINI_FILE_OPS_MIN", 4 if _gem_smooth else 2)
                    f_hi = max(f_lo, _env_int("SIM_GEMINI_FILE_OPS_MAX", 6 if _gem_smooth else 7))
                    n_file = random.randint(f_lo, f_hi)
                    for _ in range(n_file):
                        op = file_op_fixed or random.choice(("create", "read", "update"))
                        _prom_gem_file_op.labels(*gem_common, op, mime, ext, lang).inc()
                if _prom_gem_tool_call is not None:
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
                        _prom_gem_tool_call.labels(
                            *gem_common,
                            fn,
                            "true" if random.random() > 0.08 else "false",
                            dec,
                            ttyp,
                        ).inc()
                        if _prom_gem_tool_latency is not None:
                            if _gem_smooth:
                                tl = int(random.triangular(80.0, 4200.0, 900.0))
                            else:
                                tl = random.randint(15, 12_000)
                            _prom_gem_tool_latency.labels(*gem_common, fn).observe(float(tl))
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
        if _prom_gem_agent_duration is not None or _prom_gem_agent_run is not None:
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
                if _prom_gem_agent_duration is not None:
                    _prom_gem_agent_duration.labels(*_base_agent, _an).observe(float(_wall_ms))
                if _prom_gem_agent_run is not None:
                    _prom_gem_agent_run.labels(*_base_agent, _an, _tr).inc()
            elif _gem_shape == "extended":
                if _prom_gem_agent_duration is not None:
                    _prom_gem_agent_duration.labels(*gem_common, _an).observe(float(_wall_ms))
                if _prom_gem_agent_run is not None:
                    _prom_gem_agent_run.labels(*gem_common, _an, _tr).inc()


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
    if _sim_cli is None:
        raise RuntimeError("CLI trace providers not initialized")
    ver = tool_version or tool_version_for("claude_code")
    claude_tracer = _sim_cli.claude.get_tracer("claude-code", ver)
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


def _emit_codex_sse_stream_delta_maybe(
    conversation_id: str,
    model: str,
    user_email: str,
    trace_id: int,
    span_id: int,
) -> None:
    """Sometimes emit a non-terminal ``codex.sse_event`` (e.g. delta) before ``response.completed``."""
    if _codex_otlp_logger is None:
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
    if _sim_cli is None:
        raise RuntimeError("CLI trace providers not initialized")
    ver = tool_version_for("codex")
    codex_tracer = _sim_cli.codex.get_tracer("codex", ver)
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

    if _prom_codex_run_turn is not None and _prom_codex_token is not None:
        _prom_codex_run_turn.labels(cx_app, cx_sub, model).inc()
        _prom_codex_token.labels(cx_app, cx_sub, model, "input").inc(inp)
        _prom_codex_token.labels(cx_app, cx_sub, model, "output").inc(out)


# Longest generic workflow: root + planning subtree + N tools + rag + completion + validate.
_DEEP_TOOL_NAMES = (
    "tool.read_file",
    "tool.grep",
    "tool.bash",
    "tool.apply_patch",
    "tool.run_tests",
    "tool.web_fetch",
    "tool.db_query",
    "tool.git_status",
    "tool.linter",
    "tool.formatter",
    "tool.deploy_preview",
)


def emit_generic_agent_workflow(
    session_id: str,
    profile: dict,
    input_tokens: int,
    output_tokens: int,
    *,
    deep: bool,
    deep_span_target: int,
) -> None:
    """
    Generic multi-step agent trace (not Gemini/Claude/Codex single-span CLI shape).

    - Shallow (``deep=False``): **4** spans — root, planning, one tool, completion.
    - Deep (``deep=True``): **at least 10** spans by default — root, planning + 2 children,
      multiple tools, rag, completion, validate. ``deep_span_target`` sets total span count
      (clamped to a feasible range).
    """
    ver = tool_version_for(profile["agent.product"])
    # Not Gemini CLI: generic multi-vendor workflows stay on service ``ai-agent-engine`` (do not use ``gemini-cli`` name).
    tracer = trace.get_tracer("generic.agent.workflow", ver)
    user_attrs = random_coralogix_identity(session_id)
    workflow_request_id = str(uuid.uuid4())
    if not deep:
        with tracer.start_as_current_span(
            "agent.workflow.root",
            attributes={
                **user_attrs,
                "gen_ai.system": profile["gen_ai.system"],
                "gen_ai.request.model": profile["gen_ai.request.model"],
                "gen_ai.request.id": workflow_request_id,
                "gen_ai.session.id": session_id,
                "gen_ai.conversation.id": session_id,
                "sim.workflow.kind": "agent.workflow",
                "agent.product": profile["agent.product"],
                "sim.agent_tool_version": ver,
                "sim.trace.depth": "shallow",
                "sim.trace.span_count_expected": 4,
            },
        ) as root:
            with tracer.start_as_current_span("gen_ai.planning") as plan:
                plan.set_attributes(
                    {
                        "gen_ai.system": profile["gen_ai.system"],
                        "gen_ai.request.model": profile["gen_ai.request.model"],
                        "gen_ai.usage.input_tokens": input_tokens,
                    }
                )
                time.sleep(random.uniform(1.5, 4.0))

            tool_name = random.choice(["git_patch", "web_search", "db_query"])
            with tracer.start_as_current_span(f"tool.{tool_name}") as tool:
                if random.random() < 0.10:
                    tool.set_status(Status(StatusCode.ERROR, "Context window exceeded"))
                    root.add_event("agent_hallucination_detected", {"confidence": 0.42})
                else:
                    time.sleep(0.5)
                    tool.set_attribute("gen_ai.tool.status", "success")

            with tracer.start_as_current_span("gen_ai.completion") as comp:
                comp.set_attributes(
                    _gen_ai_dashboard_llm_span_attributes(
                        input_tokens,
                        output_tokens,
                        operation_name="chat",
                        model=profile["gen_ai.request.model"],
                    )
                )
        return

    # Deep tree: 1 root + 1 planning + 2 under planning + N tools + rag + completion + validate
    # total spans = 7 + N  =>  N = total - 7  (need total >= 10 => N >= 3)
    target = max(10, deep_span_target)
    n_tools = max(3, target - 7)

    with tracer.start_as_current_span(
        "agent.workflow.root",
        attributes={
            **user_attrs,
            "gen_ai.system": profile["gen_ai.system"],
            "gen_ai.request.model": profile["gen_ai.request.model"],
            "gen_ai.request.id": workflow_request_id,
            "gen_ai.session.id": session_id,
            "gen_ai.conversation.id": session_id,
            "sim.workflow.kind": "agent.workflow",
            "agent.product": profile["agent.product"],
            "sim.agent_tool_version": ver,
            "sim.trace.depth": "deep",
            "sim.trace.span_count_expected": target,
        },
    ) as root:
        with tracer.start_as_current_span("gen_ai.planning") as plan:
            plan.set_attributes(
                {
                    "gen_ai.system": profile["gen_ai.system"],
                    "gen_ai.request.model": profile["gen_ai.request.model"],
                    "gen_ai.usage.input_tokens": input_tokens,
                }
            )
            time.sleep(random.uniform(0.8, 2.2))
            with tracer.start_as_current_span("gen_ai.context.load") as ctx:
                ctx.set_attribute("gen_ai.context.chars", random.randint(4_000, 120_000))
                time.sleep(random.uniform(0.05, 0.35))
            with tracer.start_as_current_span("gen_ai.token_estimate") as est:
                est.set_attribute(
                    "gen_ai.estimated_cost_usd",
                    round(
                        estimate_llm_cost_usd(
                            profile["gen_ai.request.model"],
                            input_tokens,
                            output_tokens,
                        ),
                        4,
                    ),
                )
                time.sleep(random.uniform(0.03, 0.2))

        for i in range(n_tools):
            name = _DEEP_TOOL_NAMES[i % len(_DEEP_TOOL_NAMES)]
            with tracer.start_as_current_span(name) as sp:
                if random.random() < 0.06:
                    sp.set_status(Status(StatusCode.ERROR, "tool_timeout"))
                    root.add_event("tool_retry_scheduled", {"attempt": i + 1})
                else:
                    time.sleep(random.uniform(0.08, 0.45))
                    sp.set_attribute("gen_ai.tool.status", "success")

        with tracer.start_as_current_span("gen_ai.rag.retrieve") as rag:
            rag.set_attribute("gen_ai.rag.chunks", random.randint(1, 24))
            time.sleep(random.uniform(0.1, 0.5))

        with tracer.start_as_current_span("gen_ai.completion") as comp:
            comp.set_attributes(
                _gen_ai_dashboard_llm_span_attributes(
                    input_tokens,
                    output_tokens,
                    operation_name="chat",
                    model=profile["gen_ai.request.model"],
                )
            )
            time.sleep(random.uniform(0.2, 1.0))

        with tracer.start_as_current_span("gen_ai.output.validate") as val:
            val.set_attribute("gen_ai.validation.passed", random.random() > 0.05)
            time.sleep(random.uniform(0.05, 0.25))


def main() -> None:
    otlp_mp = None
    otlp_iter_counter = None
    endpoint, insecure, otlp_headers = _resolve_otlp_config()
    _otlp_common: dict = {"endpoint": endpoint, "insecure": insecure}
    if otlp_headers:
        _otlp_common["headers"] = otlp_headers

    tp_gemini: TracerProvider | None = None
    tp_codex: TracerProvider | None = None
    tp_claude: TracerProvider | None = None
    tp_cursor: TracerProvider | None = None
    tp_github_copilot: TracerProvider | None = None

    span_exporter = OTLPSpanExporter(**_otlp_common)

    # Separate providers so OTLP Resource carries the service names Coralogix AI Tools expect.
    gemini_cx_app = os.environ.get("GEMINI_CX_APPLICATION_NAME", "gemini-cli")
    gemini_cx_sub = os.environ.get("GEMINI_CX_SUBSYSTEM_NAME", "gemini-cli-sessions")
    gemini_service = os.environ.get("SIM_GEMINI_SERVICE_NAME", "gemini-cli")
    gemini_res = _cli_resource(gemini_service, gemini_cx_app, gemini_cx_sub)
    tp_gemini = TracerProvider(resource=gemini_res)
    gemini_span_out: SpanExporter = (
        span_exporter if _env_bool("SIM_GEMINI_OTLP_TRACES_ENABLED", True) else _NoopSpanExporter()
    )
    tp_gemini.add_span_processor(BatchSpanProcessor(gemini_span_out))

    codex_cx_app = os.environ.get("CODEX_CX_APPLICATION_NAME", "codex")
    codex_cx_sub = os.environ.get("CODEX_CX_SUBSYSTEM_NAME", "codex-sessions")
    codex_service = os.environ.get("SIM_CODEX_SERVICE_NAME", "codex_cli_rs")
    # Same Resource layout as Gemini CLI — duplicate ``service_name`` on Resource breaks Coralogix ``serviceName``.
    tp_codex = TracerProvider(resource=_cli_resource(codex_service, codex_cx_app, codex_cx_sub))
    tp_codex.add_span_processor(BatchSpanProcessor(span_exporter))

    claude_cx_app = os.environ.get("CLAUDE_CODE_CX_APPLICATION_NAME", "claude-code")
    claude_cx_sub_flat = os.environ.get("CLAUDE_CODE_CX_SUBSYSTEM_NAME", "claude-code").strip() or "claude-code"
    claude_cx_sub_dotted = os.environ.get("SIM_CLAUDE_DOTTED_CX_SUBSYSTEM_NAME", "claude-code-sessions").strip() or "claude-code-sessions"
    claude_cx_sub = _claude_effective_cx_subsystem()
    claude_service = os.environ.get("SIM_CLAUDE_SERVICE_NAME", "claude-code")
    # Traces: cx Resource + effective subsystem (``both`` uses flat subsystem on the trace Resource).
    claude_trace_res = _cli_resource(claude_service, claude_cx_app, claude_cx_sub)
    tp_claude = TracerProvider(resource=claude_trace_res)
    claude_span_out: SpanExporter = (
        span_exporter if _env_bool("SIM_CLAUDE_OTLP_TRACES_ENABLED", False) else _NoopSpanExporter()
    )
    tp_claude.add_span_processor(BatchSpanProcessor(claude_span_out))

    cursor_cx_app = os.environ.get("CURSOR_CX_APPLICATION_NAME", "cursor")
    cursor_cx_sub = os.environ.get("CURSOR_CX_SUBSYSTEM_NAME", "ai-agent")
    cursor_service = os.environ.get("SIM_CURSOR_SERVICE_NAME", "cursor-agent")
    tp_cursor = TracerProvider(resource=_cursor_hook_resource(cursor_service, cursor_cx_app, cursor_cx_sub))
    cursor_span_out: SpanExporter = (
        span_exporter if _env_bool("SIM_CURSOR_OTLP_TRACES_ENABLED", True) else _NoopSpanExporter()
    )
    tp_cursor.add_span_processor(BatchSpanProcessor(cursor_span_out))

    copilot_cx_app = os.environ.get("COPILOT_CX_APPLICATION_NAME", "github-copilot")
    copilot_cx_sub = os.environ.get("COPILOT_CX_SUBSYSTEM_NAME", "copilot-cli-sessions")
    copilot_service = os.environ.get("SIM_GITHUB_COPILOT_SERVICE_NAME", "github-copilot")
    copilot_res = _cli_resource(copilot_service, copilot_cx_app, copilot_cx_sub)
    tp_github_copilot = TracerProvider(resource=copilot_res)
    copilot_span_out: SpanExporter = (
        span_exporter if _env_bool("SIM_COPILOT_OTLP_TRACES_ENABLED", True) else _NoopSpanExporter()
    )
    tp_github_copilot.add_span_processor(BatchSpanProcessor(copilot_span_out))

    global _sim_cli
    _sim_cli = SimCliTracerProviders(tp_gemini, tp_codex, tp_claude, tp_cursor, tp_github_copilot)
    st.sim_cli = _sim_cli

    resource = Resource.create(
        {
            "service.name": os.environ.get("OTEL_SERVICE_NAME", "ai-agent-engine"),
            "cx.application.name": os.environ.get("CX_APPLICATION_NAME", "AI-Production-Sim"),
            "cx.subsystem.name": os.environ.get("CX_SUBSYSTEM_NAME", "Agent-Worker-Node"),
            "deployment.environment": os.environ.get("DEPLOYMENT_ENVIRONMENT", "test-cluster"),
        }
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(span_exporter))
    trace.set_tracer_provider(provider)

    export_ms = int(os.environ.get("OTEL_METRIC_EXPORT_INTERVAL_MS", "15000"))
    export_sec = max(1.0, export_ms / 1000.0)
    otlp_mp, otlp_iter_counter = otlp_metrics.setup(_otlp_common, resource, export_ms)
    rw_url = prometheus_rw.resolve_prometheus_remote_write_url()
    rw_key = _CORALOGIX_RW_KEY_HARDCODE_TEST.strip() or os.environ.get(
        "CORALOGIX_PRIVATE_KEY", ""
    ).strip()

    # Prometheus → Coralogix: token counters are **duplicated** (HAR basename + ``...__token__...``) for Claude
    # Code and Gemini CLI — Coralogix often records volume on the ``__token__`` series; HAR queries still get
    # the classic ``*_token_usage*_total`` names. LOC mirrors ``gemini_cli_lines_changed_total`` with
    # ``gemini_cli_lines_changed__lines__total`` (see ``SIM_GEMINI_EMIT_LINES_CHANGED_LINES_ALIAS``). Traces/logs OTLP; ``cx_*`` on metrics.
    # Claude traces use ``claude_trace_res``; OTLP logs use one or two LoggerProviders (see ``main()``).
    global _prom_registry, _prom_gem_session, _prom_gem_token, _prom_gem_token_coralogix, _prom_gem_token_tokens
    global _prom_gem_api, _prom_gem_api_latency, _prom_gem_lines, _prom_gem_lines_coralogix, _prom_gem_file_op, _prom_gem_tool_call
    global _prom_gem_tool_latency, _prom_gem_model_routing_latency, _prom_gem_agent_duration, _prom_gem_agent_run
    global _prom_codex_run_turn, _prom_codex_token
    global _prom_copilot_session, _prom_copilot_token, _prom_copilot_tool, _prom_copilot_tool_dur
    global _prom_copilot_chat_dur, _prom_copilot_agent_dur, _prom_copilot_ttft, _prom_copilot_premium
    global _prom_copilot_cache, _prom_copilot_edit, _prom_copilot_session_repo, _prom_rw_stop
    _prom_registry = CollectorRegistry()
    _gem_shape = _gemini_metric_label_shape()
    _prom_gem_token_tokens = None
    _prom_gem_lines_coralogix = None
    if _gem_shape == "standard":
        _prom_gem_session = Counter(
            "gemini_cli_session_count",
            "Gemini CLI sessions (standard label set)",
            labelnames=_GEM_STANDARD_SESSION_L,
            registry=_prom_registry,
        )
        _prom_gem_token = Counter(
            "gemini_cli_token_usage",
            "Gemini token usage by type (HAR ``gemini_cli_token_usage_total``)",
            labelnames=_GEM_STANDARD_TOKEN_L,
            registry=_prom_registry,
        )
        _prom_gem_token_coralogix = Counter(
            "gemini_cli_token_usage__token_",
            "Gemini token usage (Coralogix volume series; mirrors HAR counter)",
            labelnames=_GEM_STANDARD_TOKEN_L,
            registry=_prom_registry,
        )
        if _env_bool("SIM_GEMINI_EMIT_TOKEN_USAGE_TOKENS_ALIAS", True):
            _prom_gem_token_tokens = Counter(
                "gemini_cli_token_usage_tokens",
                "Gemini token usage (alias series ``gemini_cli_token_usage_tokens_total``)",
                labelnames=_GEM_STANDARD_TOKEN_L,
                registry=_prom_registry,
            )
        _prom_gem_api = Counter(
            "gemini_cli_api_request_count",
            "Gemini API requests",
            labelnames=_GEM_STANDARD_API_L,
            registry=_prom_registry,
        )
        _prom_gem_api_latency = Histogram(
            "gemini_cli_api_request_latency_ms",
            "Gemini CLI model API request latency in milliseconds",
            labelnames=_GEM_STANDARD_HIST_API_L,
            registry=_prom_registry,
            buckets=(
                50,
                100,
                250,
                500,
                1000,
                2500,
                5000,
                10_000,
                30_000,
                60_000,
                120_000,
                300_000,
                float("inf"),
            ),
        )
        _prom_gem_model_routing_latency = Histogram(
            "gemini_cli_model_routing_latency_ms",
            "Gemini CLI model routing latency in milliseconds",
            labelnames=_GEM_STANDARD_HIST_API_L,
            registry=_prom_registry,
            buckets=(
                1,
                2,
                5,
                10,
                25,
                50,
                100,
                250,
                500,
                1000,
                2500,
                5000,
                float("inf"),
            ),
        )
        _prom_gem_agent_duration = Histogram(
            "gemini_cli_agent_duration_ms",
            "Agent duration in ms (upstream OTEL ``gemini_cli.agent.duration``; histogram)",
            labelnames=_GEM_STANDARD_AGENT_DUR_L,
            registry=_prom_registry,
            buckets=(
                250,
                500,
                1000,
                2500,
                5000,
                10_000,
                30_000,
                60_000,
                120_000,
                300_000,
                600_000,
                float("inf"),
            ),
        )
        _prom_gem_agent_run = Counter(
            "gemini_cli_agent_run_count",
            "Agent run count (upstream ``gemini_cli.agent.run.count``)",
            labelnames=_GEM_STANDARD_AGENT_RUN_L,
            registry=_prom_registry,
        )
        _prom_gem_lines = Counter(
            "gemini_cli_lines_changed",
            "Gemini CLI lines changed (telemetry ``gemini_cli.lines.changed``)",
            labelnames=_GEM_STANDARD_LINES_L,
            registry=_prom_registry,
        )
        if _env_bool("SIM_GEMINI_EMIT_LINES_CHANGED_LINES_ALIAS", True):
            _prom_gem_lines_coralogix = Counter(
                "gemini_cli_lines_changed__lines_",
                "Gemini CLI lines changed (Coralogix volume series; mirrors ``gemini_cli_lines_changed_total``)",
                labelnames=_GEM_STANDARD_LINES_L,
                registry=_prom_registry,
            )
        _prom_gem_file_op = Counter(
            "gemini_cli_file_operation_count",
            "Gemini CLI file operations (telemetry ``gemini_cli.file.operation.count``)",
            labelnames=_GEM_STANDARD_FILE_L,
            registry=_prom_registry,
        )
        _prom_gem_tool_call = Counter(
            "gemini_cli_tool_call_count",
            "Gemini CLI tool calls (telemetry ``gemini_cli.tool.call.count``)",
            labelnames=_GEM_STANDARD_TOOL_L,
            registry=_prom_registry,
        )
        _prom_gem_tool_latency = Histogram(
            "gemini_cli_tool_call_latency_ms",
            "Gemini CLI tool call latency in milliseconds (per function_name)",
            labelnames=_GEM_STANDARD_HIST_TOOL_L,
            registry=_prom_registry,
            buckets=(
                5,
                10,
                25,
                50,
                100,
                250,
                500,
                1000,
                2500,
                5000,
                10_000,
                30_000,
                float("inf"),
            ),
        )
    else:
        _gem_session_l = ("cx_application_name", "cx_subsystem_name", "model")
        _gem_common_l = _gem_session_l + (
            "installation_id",
            "session_id",
            "user_id",
            "user_email",
            "active_approval_mode",
        )
        _gem_token_l = _gem_common_l + ("type",)
        _gem_api_l = _gem_common_l + ("http_status",)
        _gem_api_latency_l = _gem_common_l
        _gem_routing_l = _gem_common_l
        _gem_lines_l = _gem_common_l + ("function_name", "programming_language", "type")
        _gem_file_l = _gem_common_l + ("operation", "mimetype", "extension", "programming_language")
        _gem_tool_l = _gem_common_l + ("function_name", "success", "decision", "tool_type")
        _gem_tool_latency_l = _gem_common_l + ("function_name",)
        _gem_agent_dur_l = _gem_common_l + ("agent_name",)
        _gem_agent_run_l = _gem_common_l + ("agent_name", "terminate_reason")
        _prom_gem_session = Counter(
            "gemini_cli_session_count",
            "Gemini CLI sessions",
            labelnames=_gem_session_l,
            registry=_prom_registry,
        )
        _prom_gem_token = Counter(
            "gemini_cli_token_usage",
            "Gemini token usage by type (HAR ``gemini_cli_token_usage_total``)",
            labelnames=_gem_token_l,
            registry=_prom_registry,
        )
        _prom_gem_token_coralogix = Counter(
            "gemini_cli_token_usage__token_",
            "Gemini token usage (Coralogix volume series; mirrors HAR counter)",
            labelnames=_gem_token_l,
            registry=_prom_registry,
        )
        _prom_gem_api = Counter(
            "gemini_cli_api_request_count",
            "Gemini API requests",
            labelnames=_gem_api_l,
            registry=_prom_registry,
        )
        _prom_gem_api_latency = Histogram(
            "gemini_cli_api_request_latency_ms",
            "Gemini CLI model API request latency in milliseconds",
            labelnames=_gem_api_latency_l,
            registry=_prom_registry,
            buckets=(
                50,
                100,
                250,
                500,
                1000,
                2500,
                5000,
                10_000,
                30_000,
                60_000,
                120_000,
                300_000,
                float("inf"),
            ),
        )
        _prom_gem_model_routing_latency = Histogram(
            "gemini_cli_model_routing_latency_ms",
            "Gemini CLI model routing latency in milliseconds",
            labelnames=_gem_routing_l,
            registry=_prom_registry,
            buckets=(
                1,
                2,
                5,
                10,
                25,
                50,
                100,
                250,
                500,
                1000,
                2500,
                5000,
                float("inf"),
            ),
        )
        _prom_gem_agent_duration = Histogram(
            "gemini_cli_agent_duration_ms",
            "Agent duration in ms (upstream OTEL ``gemini_cli.agent.duration``; histogram)",
            labelnames=_gem_agent_dur_l,
            registry=_prom_registry,
            buckets=(
                250,
                500,
                1000,
                2500,
                5000,
                10_000,
                30_000,
                60_000,
                120_000,
                300_000,
                600_000,
                float("inf"),
            ),
        )
        _prom_gem_agent_run = Counter(
            "gemini_cli_agent_run_count",
            "Agent run count (upstream ``gemini_cli.agent.run.count``)",
            labelnames=_gem_agent_run_l,
            registry=_prom_registry,
        )
        _prom_gem_lines = Counter(
            "gemini_cli_lines_changed",
            "Gemini CLI lines changed (telemetry ``gemini_cli.lines.changed``)",
            labelnames=_gem_lines_l,
            registry=_prom_registry,
        )
        if _env_bool("SIM_GEMINI_EMIT_LINES_CHANGED_LINES_ALIAS", True):
            _prom_gem_lines_coralogix = Counter(
                "gemini_cli_lines_changed__lines_",
                "Gemini CLI lines changed (Coralogix volume series; mirrors ``gemini_cli_lines_changed_total``)",
                labelnames=_gem_lines_l,
                registry=_prom_registry,
            )
        _prom_gem_file_op = Counter(
            "gemini_cli_file_operation_count",
            "Gemini CLI file operations (telemetry ``gemini_cli.file.operation.count``)",
            labelnames=_gem_file_l,
            registry=_prom_registry,
        )
        _prom_gem_tool_call = Counter(
            "gemini_cli_tool_call_count",
            "Gemini CLI tool calls (telemetry ``gemini_cli.tool.call.count``)",
            labelnames=_gem_tool_l,
            registry=_prom_registry,
        )
        _prom_gem_tool_latency = Histogram(
            "gemini_cli_tool_call_latency_ms",
            "Gemini CLI tool call latency in milliseconds (per function_name)",
            labelnames=_gem_tool_latency_l,
            registry=_prom_registry,
            buckets=(
                5,
                10,
                25,
                50,
                100,
                250,
                500,
                1000,
                2500,
                5000,
                10_000,
                30_000,
                float("inf"),
            ),
        )

    _codex_l = ("cx_application_name", "cx_subsystem_name", "model")
    _codex_tok_l = _codex_l + ("type",)
    _prom_codex_run_turn = Counter(
        "codex_cli_run_turn_count",
        "Codex CLI run_turn sessions (sim)",
        labelnames=_codex_l,
        registry=_prom_registry,
    )
    _prom_codex_token = Counter(
        "codex_cli_token_usage",
        "Codex token usage by type (sim, from user_prompt span)",
        labelnames=_codex_tok_l,
        registry=_prom_registry,
    )

    _copilot_l = ("cx_application_name", "cx_subsystem_name", "model")
    _copilot_tok_l = _copilot_l + ("type",)
    _copilot_tool_l = ("cx_application_name", "cx_subsystem_name", "gen_ai_tool_name", "outcome")
    _copilot_tool_hist_l = ("cx_application_name", "cx_subsystem_name", "gen_ai_tool_name")
    _copilot_hist_l = ("cx_application_name", "cx_subsystem_name", "model")
    _copilot_cache_l = ("cx_application_name", "cx_subsystem_name", "model", "result")
    _copilot_edit_l = ("cx_application_name", "cx_subsystem_name", "decision")
    _prom_copilot_session = Counter(
        "copilot_chat_session_count",
        "Copilot CLI sessions (VS Code copilot_chat.session.count)",
        labelnames=_copilot_l,
        registry=_prom_registry,
    )
    _prom_copilot_token = Counter(
        "copilot_chat_token_usage",
        "Copilot token usage by type (gen_ai.usage.*)",
        labelnames=_copilot_tok_l,
        registry=_prom_registry,
    )
    _prom_copilot_tool = Counter(
        "copilot_chat_tool_call_count",
        "Copilot tool invocations (copilot_chat.tool.call.count)",
        labelnames=_copilot_tool_l,
        registry=_prom_registry,
    )
    _prom_copilot_tool_dur = Histogram(
        "copilot_chat_tool_call_duration_seconds",
        "Tool latency seconds (copilot_chat.tool.call.duration)",
        labelnames=_copilot_tool_hist_l,
        registry=_prom_registry,
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, float("inf")),
    )
    _prom_copilot_chat_dur = Histogram(
        "copilot_chat_llm_round_duration_seconds",
        "Per-chat span duration (nested chat spans)",
        labelnames=_copilot_hist_l,
        registry=_prom_registry,
        buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 15, 30, 60, 120, float("inf")),
    )
    _prom_copilot_agent_dur = Histogram(
        "copilot_chat_agent_invocation_duration_seconds",
        "End-to-end invoke_agent duration (copilot_chat.agent.invocation.duration)",
        labelnames=_copilot_hist_l,
        registry=_prom_registry,
        buckets=(0.1, 0.5, 1, 2, 5, 15, 30, 60, 120, 300, 600, float("inf")),
    )
    _prom_copilot_ttft = Histogram(
        "copilot_chat_time_to_first_token_seconds",
        "Time to first token (copilot_chat.time_to_first_token)",
        labelnames=_copilot_hist_l,
        registry=_prom_registry,
        buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, float("inf")),
    )
    _prom_copilot_premium = Counter(
        "copilot_chat_premium_request_count",
        "Premium-tier Copilot requests",
        labelnames=_copilot_l,
        registry=_prom_registry,
    )
    _prom_copilot_cache = Counter(
        "copilot_chat_cache_outcome_count",
        "Cache hit/miss outcomes",
        labelnames=_copilot_cache_l,
        registry=_prom_registry,
    )
    _prom_copilot_edit = Counter(
        "copilot_chat_edit_acceptance_count",
        "Edit-style acceptance ratio proxy (copilot_chat.edit.acceptance.count)",
        labelnames=_copilot_edit_l,
        registry=_prom_registry,
    )
    _copilot_repo_l = (
        "cx_application_name",
        "cx_subsystem_name",
        "service_name",
        "session_id",
        "user_email",
        "repository_name",
    )
    _prom_copilot_session_repo = Gauge(
        "copilot_cli_session_repo_info",
        "Copilot CLI session repository association (cxai-demo hook metric)",
        labelnames=_copilot_repo_l,
        registry=_prom_registry,
    )

    _copilot_collector = (
        register_copilot_collector_metrics(_prom_registry)
        if copilot_collector_enabled()
        else None
    )

    _cc_tok_l = _CC_BASE_LABEL_NAMES + ("type", "model")
    _cc_cost_l = _CC_BASE_LABEL_NAMES + ("model",)
    _cc_act_l = _CC_BASE_LABEL_NAMES + ("type",)
    _cc_ed_l = _CC_BASE_LABEL_NAMES + ("tool_name", "decision", "source", "language")

    cc_session = Counter(
        "claude_code_session_count",
        "Claude Code sessions",
        labelnames=_CC_BASE_LABEL_NAMES,
        registry=_prom_registry,
    )
    # HAR name + Coralogix volume name (both incremented together; see ``_cc_token_inc`` in ``main``).
    cc_token = Counter(
        "claude_code_token_usage_tokens",
        "Claude Code token usage (HAR metric name)",
        labelnames=_cc_tok_l,
        registry=_prom_registry,
    )
    cc_token_coralogix = Counter(
        "claude_code_token_usage__token_",
        "Claude Code token usage (Coralogix token volume series)",
        labelnames=_cc_tok_l,
        registry=_prom_registry,
    )
    cc_cost = Counter(
        "claude_code_cost_usage_USD",
        "Claude Code cost USD",
        labelnames=_cc_cost_l,
        registry=_prom_registry,
    )
    cc_active = Counter(
        "claude_code_active_time_total_s",
        "Claude Code active time (seconds)",
        labelnames=_cc_act_l,
        registry=_prom_registry,
    )
    cc_loc = Counter(
        "claude_code_lines_of_code_count",
        "Claude Code lines of code",
        labelnames=_CC_LOC_LABEL_NAMES,
        registry=_prom_registry,
    )
    cc_commit = Counter(
        "claude_code_commit_count",
        "Claude Code commits",
        labelnames=_CC_BASE_LABEL_NAMES,
        registry=_prom_registry,
    )
    cc_pr = Counter(
        "claude_code_pull_request_count",
        "Claude Code pull requests",
        labelnames=_CC_BASE_LABEL_NAMES,
        registry=_prom_registry,
    )
    cc_edit_decision = Counter(
        "claude_code_code_edit_tool_decision",
        "Claude Code edit tool decisions",
        labelnames=_cc_ed_l,
        registry=_prom_registry,
    )
    _cc_repo_l = _CC_BASE_LABEL_NAMES + ("repository_name",)
    cc_session_repo = Gauge(
        "claude_code_session_repo_info",
        "Claude Code session repository association (1 when session used repo)",
        labelnames=_cc_repo_l,
        registry=_prom_registry,
    )

    _scrape_port = os.environ.get("PROMETHEUS_METRICS_PORT", "").strip()
    if _scrape_port:
        try:
            start_http_server(int(_scrape_port), registry=_prom_registry)
            print(
                "Prometheus scrape endpoint listening on 0.0.0.0:%s/metrics (same registry as remote_write)"
                % _scrape_port,
                flush=True,
            )
        except OSError as e:
            print(
                "PROMETHEUS_METRICS_PORT=%s: could not bind HTTP scrape server: %s"
                % (_scrape_port, e),
                flush=True,
            )

    # When using in-cluster collector scrape (PROMETHEUS_METRICS_PORT), set PROMETHEUS_REMOTE_WRITE_ENABLED=false
    # to avoid duplicating the same series via direct remote_write.
    _do_prom_rw = rw_key and _env_bool("PROMETHEUS_REMOTE_WRITE_ENABLED", True)
    if _do_prom_rw:
        try:
            prometheus_rw.push_remote_write(_prom_registry, rw_url, rw_key)
        except Exception:
            log.exception("prometheus remote_write: initial push failed (check URL, key, network)")
        _prom_rw_stop, _ = prometheus_rw.start_push_thread(_prom_registry, export_sec, rw_url, rw_key)
    else:
        _prom_rw_stop = None

    log_exporter = OTLPLogExporter(**_otlp_common)

    codex_res = _cli_resource(codex_service, codex_cx_app, codex_cx_sub)
    codex_log_provider = LoggerProvider(resource=codex_res)
    codex_log_provider.add_log_record_processor(
        BatchLogRecordProcessor(
            log_exporter,
            max_queue_size=2048,
            schedule_delay_millis=1000.0,
            max_export_batch_size=512,
        )
    )
    global _codex_log_provider, _codex_otlp_logger
    _codex_log_provider = codex_log_provider
    _codex_otlp_logger = codex_log_provider.get_logger("codex", "1.0.0-sim")

    gemini_log_provider = LoggerProvider(resource=gemini_res)
    gemini_log_provider.add_log_record_processor(
        BatchLogRecordProcessor(
            log_exporter,
            max_queue_size=2048,
            schedule_delay_millis=1000.0,
            max_export_batch_size=512,
        )
    )
    global _gemini_log_provider, _gemini_otlp_logger
    _gemini_log_provider = gemini_log_provider
    _gemini_otlp_logger = gemini_log_provider.get_logger("gemini-cli", "1.0.0-sim")

    copilot_log_provider = LoggerProvider(resource=copilot_res)
    copilot_log_provider.add_log_record_processor(
        BatchLogRecordProcessor(
            log_exporter,
            max_queue_size=2048,
            schedule_delay_millis=1000.0,
            max_export_batch_size=512,
        )
    )
    global _copilot_log_provider, _copilot_otlp_logger
    _copilot_log_provider = copilot_log_provider
    _copilot_otlp_logger = copilot_log_provider.get_logger("github-copilot", "1.0.0-sim")

    global _claude_dotted_log_provider
    _claude_dotted_log_provider = None
    _cc_prof = _claude_telemetry_profile()
    _claude_blp_kw = dict(
        max_queue_size=2048,
        schedule_delay_millis=1000.0,
        max_export_batch_size=512,
    )
    if _cc_prof == "both":
        log_provider = LoggerProvider(resource=claude_trace_res)
        log_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter, **_claude_blp_kw))
        _claude_dotted_log_provider = LoggerProvider(
            resource=_dotted_claude_otel_resource(claude_service, claude_cx_app, claude_cx_sub_dotted),
        )
        _claude_dotted_log_provider.add_log_record_processor(
            BatchLogRecordProcessor(log_exporter, **_claude_blp_kw),
        )
        set_logger_provider(log_provider)
        _cc_events_scope_ver = os.environ.get("SIM_CC_APP_VERSION") or tool_version_for("claude_code")
        _cc_ev_flat = log_provider.get_logger("com.anthropic.claude_code.events", _cc_events_scope_ver)
        _sc_inst_ver_both = os.environ.get("SIM_CLAUDE_DOTTED_INSTRUMENTATION_VERSION", "").strip()
        _cc_ev_dotted = _claude_dotted_log_provider.get_logger(
            "com.anthropic.claude_code",
            _sc_inst_ver_both or "0.0.0",
        )
        _cc_log_emitters = [
            (_cc_ev_flat, "flat", claude_cx_sub_flat),
            (_cc_ev_dotted, "dotted", claude_cx_sub_dotted),
        ]
    elif _cc_prof == "dotted":
        log_provider = LoggerProvider(
            resource=_dotted_claude_otel_resource(claude_service, claude_cx_app, claude_cx_sub_dotted),
        )
        log_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter, **_claude_blp_kw))
        set_logger_provider(log_provider)
        _sc_inst_ver = os.environ.get("SIM_CLAUDE_DOTTED_INSTRUMENTATION_VERSION", "").strip()
        cc_events = log_provider.get_logger("com.anthropic.claude_code", _sc_inst_ver or "0.0.0")
        _cc_log_emitters = [(cc_events, "dotted", claude_cx_sub_dotted)]
    else:
        log_provider = LoggerProvider(resource=claude_trace_res)
        log_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter, **_claude_blp_kw))
        set_logger_provider(log_provider)
        _cc_events_scope_ver = os.environ.get("SIM_CC_APP_VERSION") or tool_version_for("claude_code")
        cc_events = log_provider.get_logger("com.anthropic.claude_code.events", _cc_events_scope_ver)
        _cc_log_emitters = [(cc_events, "flat", claude_cx_sub_flat)]

    st.claude_cx_app = claude_cx_app
    st.claude_cx_sub_flat = claude_cx_sub_flat
    st.claude_cx_sub_dotted = claude_cx_sub_dotted
    st.cc_session = cc_session
    st.cc_token = cc_token
    st.cc_token_coralogix = cc_token_coralogix
    st.cc_cost = cc_cost
    st.cc_active = cc_active
    st.cc_loc = cc_loc
    st.cc_commit = cc_commit
    st.cc_pr = cc_pr
    st.cc_edit_decision = cc_edit_decision
    st.cc_session_repo = cc_session_repo
    st.prom_gem_lines_coralogix = _prom_gem_lines_coralogix
    st.prom_copilot_session = _prom_copilot_session
    st.prom_copilot_token = _prom_copilot_token
    st.prom_copilot_tool = _prom_copilot_tool
    st.prom_copilot_tool_dur = _prom_copilot_tool_dur
    st.prom_copilot_chat_dur = _prom_copilot_chat_dur
    st.prom_copilot_agent_dur = _prom_copilot_agent_dur
    st.prom_copilot_ttft = _prom_copilot_ttft
    st.prom_copilot_premium = _prom_copilot_premium
    st.prom_copilot_cache = _prom_copilot_cache
    st.prom_copilot_edit = _prom_copilot_edit
    st.prom_copilot_session_repo = _prom_copilot_session_repo
    st.copilot_collector = _copilot_collector
    st.copilot_otlp_logger = _copilot_otlp_logger
    st.cc_log_emitters = _cc_log_emitters
    st.claude_primary_log_provider = log_provider
    st.claude_dotted_log_provider = _claude_dotted_log_provider

    agent_profiles_all = (
        {"gen_ai.system": "openai", "gen_ai.request.model": "gpt-4.5-preview", "agent.product": "chatgpt"},
        {"gen_ai.system": "anthropic", "gen_ai.request.model": CLAUDE_CODE_DEFAULT_MODEL, "agent.product": "claude_code"},
        {"gen_ai.system": "gcp.gemini", "gen_ai.request.model": GEMINI_DEFAULT_MODEL, "agent.product": "gemini_cli"},
        {
            "gen_ai.system": "azure.openai",
            "gen_ai.request.model": COPILOT_DEFAULT_MODEL,
            "agent.product": "copilot_cli",
        },
        {"gen_ai.system": "openai", "gen_ai.request.model": CURSOR_DEFAULT_MODEL, "agent.product": "cursor"},
        {"gen_ai.system": "anthropic", "gen_ai.request.model": "claude-3-5-sonnet-20241022", "agent.product": "windsurf"},
        {"gen_ai.system": "aws.bedrock", "gen_ai.request.model": "anthropic.claude-3-5-sonnet-20240620-v1:0", "agent.product": "amazon_q"},
        {"gen_ai.system": "openai", "gen_ai.request.model": "gpt-5.4-mini", "agent.product": "jetbrains_ai"},
        {"gen_ai.system": "azure.ai.openai", "gen_ai.request.model": "gpt-5.4", "agent.product": "azure_openai"},
        {"gen_ai.system": "perplexity", "gen_ai.request.model": "sonar", "agent.product": "perplexity"},
        {"gen_ai.system": "groq", "gen_ai.request.model": "llama-3.3-70b-versatile", "agent.product": "groq"},
        {"gen_ai.system": "deepseek", "gen_ai.request.model": "deepseek-chat", "agent.product": "deepseek"},
        {"gen_ai.system": "mistral_ai", "gen_ai.request.model": "mistral-large-latest", "agent.product": "mistral"},
        {"gen_ai.system": "xai", "gen_ai.request.model": "grok-3", "agent.product": "grok"},
        {
            "gen_ai.system": "openai",
            "gen_ai.request.model": os.environ.get("SIM_CODEX_MODEL", CODEX_DEFAULT_MODEL),
            "agent.product": "codex",
        },
    )
    # When true, weighted selection uses only Coralogix-instrumented CLIs (Codex / Gemini / Claude / Cursor).
    # Without this, many generic agents (default weight 1 each) dilute CLI traffic.
    _cli_only = _env_bool("SIM_CLI_AGENTS_ONLY", False)
    if _cli_only:
        _cli_products = frozenset({"claude_code", "gemini_cli", "codex", "cursor", "copilot_cli"})
        agent_profiles = tuple(p for p in agent_profiles_all if p["agent.product"] in _cli_products)
    else:
        agent_profiles = agent_profiles_all
    by_product = {p["agent.product"]: p for p in agent_profiles_all}
    _weights = tuple(_agent_selection_weight(p["agent.product"]) for p in agent_profiles)

    def run_sophisticated_trace() -> None:
        session_id = str(uuid.uuid4())
        _ru: dict | None = None
        force = os.environ.get("SIM_FORCE_AGENT", "").strip().lower()
        if force and force in by_product:
            profile = by_product[force]
        else:
            co = _claude_office_hours_weight_scale()
            if co >= 1.0 - 1e-9:
                weights_run = _weights
            else:
                w_adj = list(_weights)
                for i, p in enumerate(agent_profiles):
                    if p["agent.product"] == "claude_code":
                        base = w_adj[i]
                        w_adj[i] = max(1, round(float(base) * co))
                        break
                weights_run = tuple(w_adj)
            profile = random.choices(agent_profiles, weights=weights_run, k=1)[0]

        if profile["agent.product"] == "claude_code":
            profile = {
                **profile,
                "gen_ai.request.model": (
                    os.environ.get("SIM_CLAUDE_MODEL", "").strip()
                    or random.choice(_CLAUDE_CODE_MODELS)
                ),
            }
            # Roster users + ``session.id`` are resolved in the Claude emit loop (single slot or all slots).

        otlp_metrics.record_trace_iteration(otlp_iter_counter, profile)

        # Raw Gemini CLI shape: single INTERNAL span `user_prompt` on tracer `gemini-cli`
        # with a version from ``AGENT_TOOL_VERSIONS`` (see sample: gen_ai.* / cx.* / otel.library.*).
        if profile["agent.product"] == "gemini_cli":
            gid = session_id
            gru: dict | None = None
            if _env_bool("SIM_GEMINI_STABLE_SESSION_PER_USER", True):
                gru = _gemini_roster_user_for_emit()
                gid = _gemini_stable_session_id_from_user_attrs(gru)
            emit_gemini_cli_user_prompt_span(gid, roster_user=gru)
            return

        if profile["agent.product"] == "claude_code":
            cc_ver = os.environ.get("SIM_CC_APP_VERSION") or tool_version_for("claude_code")
            mult = max(0.01, _env_float("SIM_CLAUDE_PER_EMIT_TOKEN_MULT", 1.0))
            batch = max(1, _env_int("SIM_CLAUDE_TOKEN_EMIT_BATCH", 1))
            stable_user = _env_bool("SIM_CLAUDE_STABLE_SESSION_PER_USER", True)
            if stable_user and _claude_emit_all_session_slots():
                roster_users = _claude_roster_users_for_claude_code_emit()
            elif stable_user:
                roster_users = [_claude_roster_user_for_claude_code_emit()]
            else:
                roster_users = [None]
            for user_idx, _ru in enumerate(roster_users):
                if _ru is not None and not claude_user_should_emit_this_cycle(_ru):
                    continue
                turns = (
                    claude_user_emit_turns_this_cycle(_ru, default_batch=batch)
                    if _ru is not None
                    else batch
                )
                if turns <= 0:
                    continue
                if stable_user and _ru is not None:
                    session_id = _claude_session_id_for_roster_user(_ru)
                else:
                    session_id = _claude_long_session_trace_id()
                user_tok_mult = claude_user_token_multiplier(_ru)
                user_prod_mult = claude_user_productivity_multiplier(_ru)
                for b in range(turns):
                    # Same ``session_id`` per user for every sub-emit in this batch window.
                    emit_sid = session_id
                    turn_prompt = claude_prompt_for_session(emit_sid)
                    input_tokens, output_tokens = _sim_claude_usage_token_counts()
                    rogue_mult = claude_rogue_user_token_multiplier(_ru)
                    turn_jitter = random.uniform(0.82, 1.18)
                    if mult != 1.0:
                        input_tokens = max(1, int(input_tokens * mult))
                        output_tokens = max(1, int(output_tokens * mult))
                    input_tokens = max(1, int(input_tokens * user_tok_mult * turn_jitter))
                    output_tokens = max(1, int(output_tokens * user_tok_mult * turn_jitter))
                    if rogue_mult != 1.0:
                        input_tokens = max(1, int(input_tokens * rogue_mult))
                        output_tokens = max(1, int(output_tokens * rogue_mult))
                    if _env_bool("SIM_CLAUDE_OTLP_TRACES_ENABLED", False) and user_idx == 0 and b == 0:
                        t0 = time.perf_counter()
                        emit_claude_code_user_prompt_span(
                            session_id,
                            profile,
                            tool_version=cc_ver,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            roster_user=_ru,
                            prompt=turn_prompt,
                        )
                        active_cli_s = max(0.01, time.perf_counter() - t0)
                    else:
                        active_cli_s = random.uniform(2.2, 6.0) * max(0.35, user_prod_mult)
                    emit_claude_code_dashboard(
                        profile,
                        emit_sid,
                        input_tokens,
                        output_tokens,
                        active_cli_s,
                        app_version=cc_ver,
                        roster_user=_ru,
                        prompt=turn_prompt,
                        productivity_mult=user_prod_mult,
                    )
            return

        if profile["agent.product"] == "codex":
            emit_codex_user_prompt_span(session_id, profile)
            return

        if profile["agent.product"] == "copilot_cli":
            profile = {
                **profile,
                "gen_ai.request.model": (
                    os.environ.get("SIM_COPILOT_MODEL", "").strip()
                    or random.choice(COPILOT_CLI_MODELS)
                ),
            }
            gid = session_id
            pru: dict | None = None
            if _env_bool("SIM_COPILOT_STABLE_SESSION_PER_USER", True):
                pru = _cursor_roster_user_for_emit()
                gid = _cursor_stable_session_id_from_roster_user(pru)
            emit_copilot_cli_session(gid, profile, roster_user=pru)
            return

        if profile["agent.product"] == "cursor":
            gid = session_id
            cru: dict | None = None
            if _env_bool("SIM_CURSOR_STABLE_SESSION_PER_USER", True):
                cru = _cursor_roster_user_for_emit()
                gid = _cursor_stable_session_id_from_roster_user(cru)
            emit_cursor_composer_session(gid, profile, roster_user=cru)
            return

        input_tokens = random.randint(2000, 5000)
        output_tokens = random.randint(500, 1500)
        t0 = time.perf_counter()

        dmin = _env_int("SIM_DEEP_TRACE_MIN_SPANS", 10)
        dmax = _env_int("SIM_DEEP_TRACE_MAX_SPANS", 16)
        if dmax < dmin:
            dmax = dmin
        min_want = _env_int("SIM_MIN_SPAN_COUNT", 0)

        if min_want >= 10:
            use_deep = True
            deep_target = max(min_want, random.randint(dmin, dmax))
        else:
            use_deep = random.random() < _env_float("SIM_DEEP_TRACE_RATIO", 0.45)
            deep_target = random.randint(dmin, dmax) if use_deep else 4

        emit_generic_agent_workflow(
            session_id,
            profile,
            input_tokens,
            output_tokens,
            deep=use_deep,
            deep_span_target=deep_target if use_deep else 4,
        )

        active_cli_s = max(0.01, time.perf_counter() - t0)
        emit_claude_code_dashboard(profile, session_id, input_tokens, output_tokens, active_cli_s)

    iterations_raw = os.environ.get("TRACE_ITERATIONS")
    interval = float(os.environ.get("TRACE_INTERVAL_SEC", "3"))

    _otlp_m = "on (Custom Metrics OTLP)" if otlp_mp is not None else "off"
    _gem_tr = "on" if _env_bool("SIM_GEMINI_OTLP_TRACES_ENABLED", True) else "off"
    _cc_tr = "on" if _env_bool("SIM_CLAUDE_OTLP_TRACES_ENABLED", False) else "off"
    _ww = (
        "worldwide(EU/Asia/Pac)"
        if _env_bool("SIM_CLAUDE_WORLDWIDE_WORKFORCE_ENABLE", False)
        else "single-TZ"
    )
    _rfmt = os.environ.get("SIM_ROSTER_EMAIL_FORMAT", "natural").strip().lower() or "natural"
    _ex67 = _CORALOGIX_TEAM_USERS[67]["user.email"] if len(_CORALOGIX_TEAM_USERS) > 67 else "n/a"
    print(
        "AI Agent Simulation started (logs/traces OTLP -> %s; metrics remote_write -> %s; OTLP metrics %s; Gemini OTLP traces %s; Claude OTLP traces %s; Claude office-hours mode=%s; Claude log profile %s; roster email format=%s example[67]=%s)..."
        % (endpoint, rw_url, _otlp_m, _gem_tr, _cc_tr, _ww, _claude_telemetry_profile(), _rfmt, _ex67),
        flush=True,
    )

    try:
        if iterations_raw is not None and iterations_raw != "":
            for _ in range(int(iterations_raw)):
                run_sophisticated_trace()
        else:
            while True:
                run_sophisticated_trace()
                time.sleep(interval)
    finally:
        if _prom_rw_stop is not None:
            _prom_rw_stop.set()
        if _prom_registry is not None and _do_prom_rw:
            prometheus_rw.push_once_safe(_prom_registry, rw_url, rw_key)
        log_provider.force_flush(timeout_millis=30000)
        if _claude_dotted_log_provider is not None:
            _claude_dotted_log_provider.force_flush(timeout_millis=30000)
        if _codex_log_provider is not None:
            _codex_log_provider.force_flush(timeout_millis=30000)
        if _gemini_log_provider is not None:
            _gemini_log_provider.force_flush(timeout_millis=30000)
        if _copilot_log_provider is not None:
            _copilot_log_provider.force_flush(timeout_millis=30000)
        log_provider.shutdown()
        if _claude_dotted_log_provider is not None:
            _claude_dotted_log_provider.shutdown()
        if _codex_log_provider is not None:
            _codex_log_provider.shutdown()
        if _gemini_log_provider is not None:
            _gemini_log_provider.shutdown()
        if _copilot_log_provider is not None:
            _copilot_log_provider.shutdown()
        provider.shutdown()
        if tp_gemini is not None:
            tp_gemini.shutdown()
        if tp_codex is not None:
            tp_codex.shutdown()
        if tp_claude is not None:
            tp_claude.shutdown()
        if tp_github_copilot is not None:
            tp_github_copilot.shutdown()
        otlp_metrics.shutdown(otlp_mp)


if __name__ == "__main__":
    main()
