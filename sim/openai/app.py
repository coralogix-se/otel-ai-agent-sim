"""Standalone process: OpenAI API Platform (Administration) metrics.

Run with ``python -m sim.openai``. Not mixed into the Claude/Gemini/Copilot agent loop.

Local Coralogix (dmb-sm) example::

    export CORALOGIX_PRIVATE_KEY=...          # Send-Your-Data key for the dmb-sm tenant
    export CORALOGIX_REGION=us2
    export PROMETHEUS_REMOTE_WRITE_ENABLED=true
    export PROMETHEUS_RW_JOB=otel-openai-admin-sim
    .venv/bin/python -m sim.openai
"""

from __future__ import annotations

import logging
import os
import sys
import time

from prometheus_client import CollectorRegistry, start_http_server

import prometheus_rw
from sim.common.env import _env_bool, _env_int
from sim.openai.runtime import OpenAIAdminSim, _cx_app, _cx_sub

log = logging.getLogger("sim.openai")


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
    sim = OpenAIAdminSim(registry=registry)

    metrics_port = _env_int("PROMETHEUS_METRICS_PORT", 9091)
    start_http_server(metrics_port, registry=registry)

    rw_url = prometheus_rw.resolve_prometheus_remote_write_url()
    rw_key = os.environ.get("CORALOGIX_PRIVATE_KEY", "").strip()
    do_rw = bool(rw_key) and _env_bool("PROMETHEUS_REMOTE_WRITE_ENABLED", False)
    rw_stop = None
    if do_rw:
        # Match scraped-target identity so PromQL with job= works.
        os.environ.setdefault("PROMETHEUS_RW_JOB", "otel-openai-admin-sim")
        os.environ.setdefault("PROMETHEUS_RW_INSTANCE", f"localhost:{metrics_port}")
        try:
            prometheus_rw.push_remote_write(registry, rw_url, rw_key)
        except Exception:
            log.exception("prometheus remote_write: initial push failed")
        export_sec = max(5, _env_int("PROMETHEUS_RW_INTERVAL_SEC", 15))
        rw_stop, _ = prometheus_rw.start_push_thread(registry, export_sec, rw_url, rw_key)
    elif not rw_key:
        log.warning(
            "CORALOGIX_PRIVATE_KEY unset — serving Prometheus on :%d only "
            "(set key + PROMETHEUS_REMOTE_WRITE_ENABLED=true to push to Coralogix)",
            metrics_port,
        )

    interval = max(1, _env_int("SIM_OPENAI_ADMIN_INTERVAL_SEC", 60))
    if interval < 15 and not _env_int("TRACE_ITERATIONS", 0):
        log.warning("SIM_OPENAI_ADMIN_INTERVAL_SEC=%s is aggressive for continuous runs", interval)
    iterations = _env_int("TRACE_ITERATIONS", 0)
    log.info(
        "OpenAI Admin simulator started (Prometheus :%d; remote_write=%s; interval=%ss; "
        "app=%s subsystem=%s; models=%s; projects=%d; keys=%d; users=%d)",
        metrics_port,
        "on" if do_rw else "off",
        interval,
        _cx_app(),
        _cx_sub(),
        ",".join(sim.models),
        len(sim.projects),
        len(sim.api_keys),
        len(sim.users),
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
