"""Standalone Cursor Usage metrics process (kind / local scrape).

    PROMETHEUS_METRICS_PORT=9090 \\
    SIM_CURSOR_USAGE_METRICS_ENABLED=true \\
    python -m sim.cursor.usage_v2
"""

from __future__ import annotations

import logging
import os
import sys
import time

from prometheus_client import CollectorRegistry, start_http_server

from sim.common.env import _env_int
from sim.cursor.usage_v2 import (
    emit_cursor_usage_metrics_cycle,
    register_cursor_usage_metrics,
    usage_metrics_enabled,
)

log = logging.getLogger("sim.cursor.usage_v2")


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    # Standalone process always enables Usage metrics (env can still override to false).
    os.environ.setdefault("SIM_CURSOR_USAGE_METRICS_ENABLED", "true")
    if not usage_metrics_enabled():
        log.error("SIM_CURSOR_USAGE_METRICS_ENABLED is false — nothing to emit")
        sys.exit(1)

    registry = CollectorRegistry()
    register_cursor_usage_metrics(registry)
    port = _env_int("PROMETHEUS_METRICS_PORT", 9090)
    start_http_server(port, registry=registry)

    interval = max(5, _env_int("SIM_CURSOR_USAGE_INTERVAL_SEC", 30))
    iterations = _env_int("TRACE_ITERATIONS", 0)
    log.info(
        "Cursor Usage simulator started (Prometheus :%d; interval=%ss; pooled budget on)",
        port,
        interval,
    )
    n = 0
    try:
        while True:
            emit_cursor_usage_metrics_cycle()
            n += 1
            if iterations and n >= iterations:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("shutting down")


if __name__ == "__main__":
    main()
