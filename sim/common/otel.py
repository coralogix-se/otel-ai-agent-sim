"""Shared OTLP resources, Claude label helpers, Codex structured logs, tool versions."""
from __future__ import annotations

import hashlib
import logging
import os
import platform
import random
import secrets
import socket
import sys
import time
import uuid
from dataclasses import dataclass

from opentelemetry._logs.severity import SeverityNumber
from opentelemetry.sdk._logs import LogRecord
from opentelemetry.sdk.resources import Resource
from opentelemetry.semconv.resource import ResourceAttributes
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import TraceFlags

from sim.common.env import _env_bool, _env_float, _env_int
from sim.common.state import st

log = logging.getLogger(__name__)


class _NoopSpanExporter(SpanExporter):
    """Drops ended spans (no network). Used for Claude ``TracerProvider`` when OTLP traces are disabled."""

    def export(self, spans):  # noqa: ANN001
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


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


def _sim_claude_usage_token_counts() -> tuple[int, int]:
    """
    Input/output token counts for Claude Code metrics and the ``user_prompt`` span.
    Combined input+output is drawn in ``[SIM_CLAUDE_TOTAL_TOKENS_MIN, SIM_CLAUDE_TOTAL_TOKENS_MAX]``
    (defaults ~2.5k–~18k per simulated turn — realistic CLI turn sizes; was up to ~225k which
    produced absurd weekly spend). Override env for stress tests (e.g. ``50_000_000`` max).
    """
    t0 = _env_int("SIM_CLAUDE_TOTAL_TOKENS_MIN", 2_500)
    t1 = _env_int("SIM_CLAUDE_TOTAL_TOKENS_MAX", 18_000)
    lo, hi = min(t0, t1), max(t0, t1)
    pair_total = random.randint(lo, hi)
    inp_frac = random.uniform(0.55, 0.75)
    input_tokens = max(1, int(pair_total * inp_frac))
    output_tokens = max(1, pair_total - input_tokens)
    return input_tokens, output_tokens


def _agent_selection_weight(agent_product: str) -> int:
    """
    Relative weights for ``random.choices`` (higher = selected more often per iteration).
    Override with env ``SIM_WEIGHT_<PRODUCT>`` e.g. ``SIM_WEIGHT_GEMINI_CLI``, ``SIM_WEIGHT_CODEX``.
    """
    defaults = {
        # Slightly favor Claude so token/cost panels see samples without long waits (override with SIM_WEIGHT_*).
        "claude_code": 5,
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
# We use separate TracerProviders so each CLI export carries the real client service name.
@dataclass(frozen=True)
class SimCliTracerProviders:
    gemini: TracerProvider
    codex: TracerProvider
    claude: TracerProvider
    cursor: TracerProvider
    github_copilot: TracerProvider

def _cli_resource(service_name: str, cx_application_name: str, cx_subsystem_name: str) -> Resource:
    return Resource.create(
        {
            "service.name": service_name,
            "cx.application.name": cx_application_name,
            "cx.subsystem.name": cx_subsystem_name,
            "deployment.environment": os.environ.get("DEPLOYMENT_ENVIRONMENT", "test-cluster"),
        }
    )


def _dotted_claude_otel_resource(
    service_name: str,
    cx_application_name: str,
    cx_subsystem_name: str,
) -> Resource:
    """
    OTLP log Resource for the **dotted** Claude log profile (semantic ``service.*``, host/os, SDK merge).
    ``cx.application.name`` / ``cx.subsystem.name`` on Resource drive Coralogix ``$l.*`` facets.
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


def _codex_otlp_resource(service_name: str, cx_application_name: str, cx_subsystem_name: str) -> Resource:
    """Codex traces/logs use the same OTLP Resource as Gemini CLI — avoids Coralogix ``OTLPResourceNoServiceName``."""
    return _cli_resource(service_name, cx_application_name, cx_subsystem_name)


def _cx_log_record_attrs(application: str, subsystem: str) -> dict[str, str]:
    """
    Duplicate Coralogix application/subsystem on each log record.
    Resource must also carry ``cx.*`` (see ``_cli_resource`` / ``_dotted_claude_otel_resource``).
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
    if st.codex_otlp_logger is None:
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
        resource=st.codex_otlp_logger.resource,
    )
    st.codex_otlp_logger.emit(rec)


def _gen_ai_dashboard_llm_span_attributes(
    input_tokens: int,
    output_tokens: int,
    *,
    operation_name: str = "chat",
    model: str | None = None,
) -> dict[str, str | int | float]:
    """
    Span tags for GenAI observability dashboards: ``gen_ai.prompt_price``,
    ``gen_ai.response_price``, token usage, and evaluator score fields.

    When ``model`` is set, prices follow ``sim.common.model_pricing`` (Opus ≫ Sonnet ≫ Haiku, etc.).
    """
    from sim.common.model_pricing import estimate_span_prices

    prompt_price, response_price = estimate_span_prices(
        model or "",
        input_tokens,
        output_tokens,
    )
    restricted = 1 if random.random() < 0.02 else 0
    allowed_prompt = 1 if random.random() < 0.04 else 0
    allowed_resp = 1 if random.random() < 0.04 else 0
    return {
        "gen_ai.operation.name": operation_name,
        "gen_ai.usage.input_tokens": input_tokens,
        "gen_ai.usage.output_tokens": output_tokens,
        "gen_ai.prompt_price": prompt_price,
        "gen_ai.response_price": response_price,
        "gen_ai.prompt.evaluations.restricted_topics.score": restricted,
        "gen_ai.prompt.evaluations.allowed_topics.score": allowed_prompt,
        "gen_ai.response.evaluations.allowed_topics.score": allowed_resp,
    }
