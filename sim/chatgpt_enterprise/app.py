"""Standalone process: ChatGPT Enterprise adoption + compliance posture gauges.

Run with ``python -m sim.chatgpt_enterprise``. Separate from ``sim.openai`` (API Platform)
and the generic ChatGPT agent loop in ``app.py``.
"""

from __future__ import annotations

import logging
import os
import sys
import time

from prometheus_client import CollectorRegistry, start_http_server

import prometheus_rw
from sim.chatgpt_enterprise.constants import cx_app, cx_sub
from sim.chatgpt_enterprise.runtime import ChatGPTEnterpriseSim
from sim.common.env import _env_bool, _env_int

log = logging.getLogger("sim.chatgpt_enterprise")


def _configure_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )


def main() -> None:
    _configure_logging()
    registry = CollectorRegistry()
    sim = ChatGPTEnterpriseSim(registry=registry)

    metrics_port = _env_int("PROMETHEUS_METRICS_PORT", 9093)
    start_http_server(metrics_port, registry=registry)

    rw_url = prometheus_rw.resolve_prometheus_remote_write_url()
    rw_key = os.environ.get("CORALOGIX_PRIVATE_KEY", "").strip()
    do_rw = bool(rw_key) and _env_bool("PROMETHEUS_REMOTE_WRITE_ENABLED", False)
    rw_stop = None
    if do_rw:
        os.environ.setdefault("PROMETHEUS_RW_JOB", "otel-chatgpt-enterprise-sim")
        os.environ.setdefault("PROMETHEUS_RW_INSTANCE", f"localhost:{metrics_port}")
        try:
            prometheus_rw.push_remote_write(registry, rw_url, rw_key)
        except Exception:
            log.exception("prometheus remote_write: initial push failed")
        export_sec = max(5, _env_int("PROMETHEUS_RW_INTERVAL_SEC", 15))
        rw_stop, _ = prometheus_rw.start_push_thread(registry, export_sec, rw_url, rw_key)

    interval = max(15, _env_int("SIM_CHATGPT_ENTERPRISE_INTERVAL_SEC", 60))
    iterations = _env_int("TRACE_ITERATIONS", 0)
    log.info(
        "ChatGPT Enterprise simulator started (Prometheus :%d; remote_write=%s; interval=%ss; "
        "app=%s subsystem=%s; workspaces=%d; seats=%d)",
        metrics_port,
        "on" if do_rw else "off",
        interval,
        cx_app(),
        cx_sub(),
        len(sim.workspaces),
        len(sim.seats),
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
            prometheus_rw.push_once_safe(registry, rw_url, rw_key)


if __name__ == "__main__":
    main()
