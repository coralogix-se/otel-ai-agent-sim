"""Standalone process: Anthropic Admin API usage + Claude Products analytics.

Run with ``python -m sim.anthropic_admin``. Not mixed into the Claude/Gemini/Copilot agent loop.
"""

from __future__ import annotations

import logging
import os
import sys
import time

from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from prometheus_client import CollectorRegistry, start_http_server

import prometheus_rw
from sim.anthropic_admin.runtime import AnthropicAdminSim, _cx_app, _cx_sub, _log_app, _log_sub
from sim.common.env import _env_bool, _env_int

log = logging.getLogger("sim.anthropic_admin")


def _configure_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )


def _resolve_otlp_config() -> tuple[str, bool, tuple[tuple[str, str], ...] | None]:
    raw = os.environ.get("OTLP_ENDPOINT", "").strip()
    region = os.environ.get("CORALOGIX_REGION", "").strip().lower()
    key = os.environ.get("CORALOGIX_PRIVATE_KEY", "").strip()
    if raw:
        endpoint = raw
    elif region:
        endpoint = f"ingress.{region}.coralogix.com:443"
    else:
        endpoint = "otel-collector-codeagentsim:24317"
    hostport = endpoint.split("://", 1)[-1]
    default_insecure = not (hostport.endswith(":443") or ":443" in hostport)
    insecure = _env_bool("OTLP_INSECURE", default_insecure)
    headers: tuple[tuple[str, str], ...] | None = None
    if key:
        headers = (("authorization", f"Bearer {key}"),)
    return endpoint, insecure, headers


def main() -> None:
    _configure_logging()
    endpoint, insecure, otlp_headers = _resolve_otlp_config()
    otlp_kw: dict = {"endpoint": endpoint, "insecure": insecure}
    if otlp_headers:
        otlp_kw["headers"] = otlp_headers

    service_name = os.environ.get("OTEL_SERVICE_NAME", "anthropic-admin-api").strip() or "anthropic-admin-api"
    cx_app, cx_sub = _cx_app(), _cx_sub()
    resource = Resource.create(
        {
            "service.name": service_name,
            "cx.application.name": _log_app(),
            "cx.subsystem.name": _log_sub(),
            "deployment.environment": os.environ.get("DEPLOYMENT_ENVIRONMENT", "test-cluster"),
        }
    )

    log_exporter = OTLPLogExporter(**otlp_kw)
    log_provider = LoggerProvider(resource=resource)
    log_provider.add_log_record_processor(
        BatchLogRecordProcessor(
            log_exporter,
            max_queue_size=512,
            schedule_delay_millis=1000.0,
            max_export_batch_size=128,
        )
    )
    otlp_logger = log_provider.get_logger("anthropic.admin", "1.0.0")

    registry = CollectorRegistry()
    sim = AnthropicAdminSim(registry=registry, logger=otlp_logger)

    metrics_port = _env_int("PROMETHEUS_METRICS_PORT", 9090)
    start_http_server(metrics_port, registry=registry)

    rw_url = prometheus_rw.resolve_prometheus_remote_write_url()
    rw_key = os.environ.get("CORALOGIX_PRIVATE_KEY", "").strip()
    do_rw = bool(rw_key) and _env_bool("PROMETHEUS_REMOTE_WRITE_ENABLED", False)
    rw_stop = None
    if do_rw:
        try:
            prometheus_rw.push_remote_write(registry, rw_url, rw_key)
        except Exception:
            log.exception("prometheus remote_write: initial push failed")
        export_sec = max(5, _env_int("PROMETHEUS_RW_INTERVAL_SEC", 15))
        rw_stop, _ = prometheus_rw.start_push_thread(registry, export_sec, rw_url, rw_key)

    interval = max(15, _env_int("SIM_ANTHROPIC_ADMIN_INTERVAL_SEC", 60))
    iterations = _env_int("TRACE_ITERATIONS", 0)
    log.info(
        "Anthropic Admin simulator started (OTLP logs -> %s; Prometheus :%d; interval=%ss; "
        "app=%s subsystem=%s; models=%s)",
        endpoint,
        metrics_port,
        interval,
        cx_app,
        cx_sub,
        ",".join(sim.models),
    )
    n = 0
    try:
        while True:
            sim.emit_cycle()
            n += 1
            if iterations and n >= iterations:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("shutting down")
    finally:
        if rw_stop is not None:
            rw_stop.set()
        log_provider.shutdown()


if __name__ == "__main__":
    main()
