"""MCP sim: 5 batches (one 10–15 min dashboard window) must satisfy every AI Center MCP insight threshold."""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from statistics import median, quantiles

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from sim.mcp import McpSim, build_providers

DISCOVERY = {"initialize", "tools/list", "prompts/list", "resources/list"}


@pytest.fixture(scope="module", params=range(1, 11))
def spans(request):
    exporter = InMemorySpanExporter()
    sim = McpSim(build_providers(exporter), random.Random(request.param))
    for _ in range(5):
        sim.emit_batch(300)
    sim.flush()
    return exporter.get_finished_spans()


def _server(spans):
    return [s for s in spans if s.kind == SpanKind.SERVER]


def _is_error(span):
    return span.status.status_code == StatusCode.ERROR


def _ms(span):
    return (span.end_time - span.start_time) / 1e6


def _tools(spans, server):
    return [s for s in _server(spans) if s.resource.attributes["service.name"] == server and s.attributes["mcp.method.name"] == "tools/call"]


def _by_tool(spans, server):
    grouped = defaultdict(list)
    for s in _tools(spans, server):
        grouped[s.attributes["gen_ai.tool.name"]].append(s)
    return grouped


def test_shape(spans):
    server = _server(spans)
    assert 1400 <= len(server) <= 1700
    by_id = {s.context.span_id: s for s in spans}
    for s in server:
        attrs = s.attributes
        assert s.name == " ".join(filter(None, (attrs["mcp.method.name"], attrs.get("gen_ai.tool.name"))))
        assert {"mcp.protocol.version", "jsonrpc.request.id", "mcp.session.id", "mcp.client.name", "mcp.client.version", "user_agent.original", "client.address", "client.port", "network.protocol.name"} <= set(attrs)
        if s.parent:
            parent = by_id[s.parent.span_id]
            assert parent.kind == SpanKind.CLIENT and parent.name == s.name and parent.attributes["jsonrpc.request.id"] == attrs["jsonrpc.request.id"]
            assert parent.context.trace_id == s.context.trace_id
        if _is_error(s):
            assert s.attributes["error.type"] and s.status.description
    assert len({s.resource.attributes["service.name"] for s in server}) == 4
    assert len({s.resource.attributes["cx.application.name"] for s in server}) == 2
    assert {s.resource.attributes["service.name"] for s in spans if s.kind == SpanKind.CLIENT} >= {"claude-code-agent", "support-bot", "cursor-agent"}
    assert 0.8 < sum(1 for s in server if s.parent) / len(server) < 0.97
    assert len({s.attributes.get("enduser.id") for s in server} - {None}) == 6
    codes = Counter(s.attributes.get("rpc.status_code") or s.attributes["error.type"] for s in server if _is_error(s))
    assert set(codes) == {"tool_error", "timeout", "-32602", "NotFoundError", "-32601"}
    dependencies = {k for s in spans if s.kind == SpanKind.CLIENT and s.parent and by_id[s.parent.span_id].kind == SpanKind.SERVER
                    for k in ("url.full", "db.system.name", "rpc.service") if k in s.attributes}
    assert dependencies == {"url.full", "db.system.name", "rpc.service"}


def test_failing_and_slow_tool(spans):
    create = _by_tool(spans, "github-mcp")["create_issue"]
    errors = sum(map(_is_error, create))
    assert errors >= 5 and errors / len(create) >= 0.05
    refund = _by_tool(spans, "billing-mcp")["refund_payment"]
    assert quantiles([_ms(s) for s in refund], n=20)[-1] >= 1000


def test_timeouts_and_slow_failures(spans):
    refund = _by_tool(spans, "billing-mcp")["refund_payment"]
    failed = [_ms(s) for s in refund if _is_error(s)]
    ok = [_ms(s) for s in refund if not _is_error(s)]
    assert all("Timeout" in s.status.description and s.attributes["error.type"] == "timeout" for s in refund if _is_error(s))
    assert len(failed) >= 20 and median(failed) >= 200 and median(failed) >= 2 * median(ok)


def test_unknown_tools_and_rejected_methods(spans):
    unknown = [s for s in _server(spans) if s.attributes.get("error.type") == "NotFoundError"]
    assert unknown and all("Unknown tool" in s.status.description for s in unknown)
    rejected = [s for s in _server(spans) if s.attributes["mcp.method.name"] == "resources/list" and _is_error(s)]
    assert rejected and all("Method not found" in s.status.description for s in rejected)


def test_runaway_traces(spans):
    per_trace = Counter(s.context.trace_id for s in _tools(spans, "github-mcp"))
    assert max(per_trace.values()) >= 20


def test_caller_errors(spans):
    lookup = _by_tool(spans, "billing-mcp")["lookup_customer"]
    dan = [s for s in lookup if s.attributes.get("enduser.id") == "dan.levi"]
    dan_errors = sum(map(_is_error, dan))
    assert dan_errors >= 20 and dan_errors / sum(map(_is_error, lookup)) >= 0.8 and len(lookup) > len(dan)


def test_discovery_overhead_only_on_search(spans):
    for server in ("github-mcp", "billing-mcp", "docs-mcp", "search-mcp"):
        rows = [s for s in _server(spans) if s.resource.attributes["service.name"] == server]
        share = sum(_ms(s) for s in rows if s.attributes["mcp.method.name"] in DISCOVERY) / sum(map(_ms, rows))
        assert (share >= 0.2) == (server == "search-mcp"), (server, share)
