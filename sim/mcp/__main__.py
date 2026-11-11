"""``python -m sim.mcp`` — send one MCP batch every ``SIM_MCP_BATCH_SEC`` until stopped.

Export: ``OTLP_TRACES_URL`` (OTLP/HTTP, e.g. ``https://ingress.eu2.coralogix.com/v1/traces``) with
``CX_INGEST_KEY`` as the Bearer token; otherwise OTLP/gRPC to ``OTLP_ENDPOINT`` (default
``localhost:4317``, insecure) for a local debug collector.
``SIM_MCP_SPANS_PER_BATCH`` (default 300) sizes each batch; ``SIM_MCP_BATCHES`` stops after N.
"""

import os
import time

from sim.common.env import _env_float, _env_int
from sim.mcp import McpSim, build_providers


def _exporter():
    url = os.environ.get("OTLP_TRACES_URL", "").strip()
    if url:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        key = os.environ.get("CX_INGEST_KEY", "").strip()
        return OTLPSpanExporter(endpoint=url, headers={"Authorization": f"Bearer {key}"} if key else None)
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

    return OTLPSpanExporter(endpoint=os.environ.get("OTLP_ENDPOINT", "localhost:4317"), insecure=True)


def main() -> None:
    sim = McpSim(build_providers(_exporter()))
    batch_sec = _env_float("SIM_MCP_BATCH_SEC", 120)
    per_batch = _env_int("SIM_MCP_SPANS_PER_BATCH", 300)
    batches = _env_int("SIM_MCP_BATCHES", 0)
    sent = 0
    while True:
        started = time.monotonic()
        n = sim.emit_batch(per_batch, window_sec=batch_sec)
        sim.flush()
        sent += 1
        print(f"mcp batch {sent}: {n} server spans", flush=True)
        if batches and sent >= batches:
            return
        time.sleep(max(0.0, batch_sec - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
