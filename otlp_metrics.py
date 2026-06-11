"""
Optional OTLP/gRPC metrics export to Coralogix **Custom Metrics** endpoint.

The previous ``ai_agent_sim_trace_iterations_total`` counter was removed so the sim does not
emit separate OTLP "sim" metrics; Claude/Gemini/Codex metrics use Prometheus remote_write instead.
"""

from __future__ import annotations

import logging
import os
import sys

from opentelemetry.sdk.metrics import MeterProvider

log = logging.getLogger(__name__)

_otlp_log_configured = False


def _ensure_logging_configured() -> None:
    """Emit INFO from this module to stderr (root logger defaults to WARNING)."""
    global _otlp_log_configured
    if _otlp_log_configured:
        return
    _otlp_log_configured = True
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    log.setLevel(level)
    if log.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    log.addHandler(handler)
    log.propagate = False


def _enabled() -> bool:
    v = os.environ.get("CORALOGIX_OTLP_METRICS_ENABLED", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def setup(
    otlp_common: dict,
    resource: object,
    export_interval_ms: int,
) -> tuple[MeterProvider | None, object | None]:
    """
    OTLP custom metrics path is unused (``ai_agent_sim_trace_iterations_total`` removed).

    Returns ``(None, None)``. If ``CORALOGIX_OTLP_METRICS_ENABLED`` is set, logs once that no
    OTLP MetricsService export is performed from this module.
    """
    if _enabled():
        _ensure_logging_configured()
        log.info(
            "CORALOGIX_OTLP_METRICS_ENABLED is set but OTLP MetricsService export is disabled "
            "(ai_agent_sim_trace_iterations_total was removed)."
        )
    return None, None


def record_trace_iteration(counter: object | None, profile: dict) -> None:
    """No-op (legacy hook for removed sim iteration counter)."""
    return


def shutdown(meter_provider: MeterProvider | None) -> None:
    if meter_provider is None:
        return
    try:
        meter_provider.shutdown()
    except Exception:
        log.exception("OTLP metric MeterProvider shutdown failed")
