"""MCP server simulator — OTel MCP semconv spans as the MCP Python SDK 2.x / FastMCP emit them.

Feeds AI Center → MCP Servers. Each session is one trace: an agent root span, a CLIENT span per
request (``{method} {target}``) and, as its child, the SERVER span (``{method} {target}``).
Some tool calls get downstream HTTP / DB / RPC children.

Two scripted sessions run once per ``SCENARIO_EVERY_SPANS`` baseline server spans, so the
runaway-trace and one-caller insights fire within a 15-minute window:
- a runaway trace (``RUNAWAY_CALLS`` ``tools/call`` in one trace, insight min 20);
- ``dan.levi`` sending bad params to ``billing-mcp/lookup_customer`` (insight: ≥20 errors, ≥80% share);
- ``support-bot`` retrying ``refund_payment`` through gateway timeouts (feeds slow failures: ≥20 errors).

Baseline error shapes (insight thresholds in ``mcp-insights.ts``):
- ``github-mcp/create_issue`` fails ~25% (failing tool: ≥5 errors and ≥5%);
- ``billing-mcp/refund_payment`` p95 > 1s and times out ~20% at ~5s (slow tool p95 ≥ 1s;
  timeouts; slow failures: ≥20 errors, error p50 ≥ 2× ok p50);
- ``search-mcp`` clients call the removed ``web_search_v2`` (unknown tools) and re-list tools before
  every call with slow ``tools/list`` (discovery overhead: ≥20% of server time);
- ``resources/list`` on servers without resources → -32601 "Method not found" (rejected methods).

Off by default in ``app.py``: ``SIM_MCP_ENABLED=true``; volume ``SIM_MCP_SERVER_SPANS_PER_HOUR``.
Standalone batch runner: ``python -m sim.mcp``.
"""

from __future__ import annotations

import os
import random
import time
import uuid
from dataclasses import dataclass

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.trace import SpanKind, Status, StatusCode

from sim.common.env import _env_bool, _env_int

PROTOCOL_VERSION = "2025-06-18"
SCENARIO_EVERY_SPANS = 250
RUNAWAY_CALLS = 26
MS = 1_000_000

DEPLOYMENTS = (("mcp-prod", 0.7), ("mcp-canary", 0.3))
CLIENTS = (("claude-code-agent", 0.45), ("support-bot", 0.30), ("cursor-agent", 0.15), (None, 0.10))
HUMANS = ("alice.cohen", "bob.martin", "carol.nguyen", "dan.levi", "erin.walsh")
SERVICE_ACCOUNT = "svc-support-bot"
CALLER_USER = "dan.levi"
# client service → (mcp.client.name, mcp.client.version prefix, version patch range)
CLIENT_INFO = {
    "claude-code-agent": ("claude-code", "2.1.", (0, 40)),
    "cursor-agent": ("cursor", "1.", (0, 9)),
    "support-bot": ("support-bot", "0.3.", (0, 0)),
    None: ("mcp-inspector", "0.14.", (0, 5)),
}

# error kinds → (error.type, rpc.status_code)
ERRORS = {
    "tool": ("tool_error", None),
    "timeout": ("timeout", None),
    "validation": ("ValidationError", "-32602"),
    "unknown": ("NotFoundError", None),
    "method": ("-32601", "-32601"),
}


@dataclass(frozen=True)
class Tool:
    name: str
    weight: float
    latency_ms: tuple[float, float]
    # (probability, kind, message, duration_ms or None for normal latency)
    failures: tuple[tuple[float, str, str, tuple[float, float] | None], ...] = ()
    # (kind, name, attributes); kind "http" | "db" | "rpc"
    child: tuple[str, str, dict] | None = None


@dataclass(frozen=True)
class Server:
    name: str
    weight: float
    tools: tuple[Tool, ...]
    discovery_ms: tuple[float, float]
    has_resources: bool
    relist_per_call: bool = False


def _http(host: str, path: str) -> tuple[str, str, dict]:
    return ("http", "GET", {"http.request.method": "GET", "url.full": f"https://{host}{path}", "server.address": host})


SERVERS = (
    Server(
        "github-mcp",
        0.30,
        (
            Tool("search_code", 3, (120, 450), child=_http("api.github.com", "/search/code?q=checkout")),
            Tool(
                "create_issue",
                2,
                (200, 600),
                ((0.25, "tool", "GitHub API error 403: secondary rate limit exceeded", None),),
                _http("api.github.com", "/repos/onlineboutique/frontend/issues"),
            ),
            Tool("get_pull_request", 2, (90, 300), child=_http("api.github.com", "/repos/onlineboutique/cart/pulls/412")),
            Tool("list_commits", 1, (80, 250)),
        ),
        (10, 40),
        has_resources=False,
    ),
    Server(
        "billing-mcp",
        0.25,
        (
            Tool(
                "lookup_customer",
                3,
                (40, 160),
                ((0.005, "validation", "Invalid params: customer_id must match cus_[A-Za-z0-9]{14}", None),),
                ("db", "SELECT customers", {"db.system.name": "postgresql", "db.name": "billing", "db.operation.name": "SELECT"}),
            ),
            Tool("get_invoice", 2, (60, 220), child=("db", "SELECT invoices", {"db.system.name": "postgresql", "db.name": "billing", "db.operation.name": "SELECT"})),
            Tool(
                "refund_payment",
                3,
                (500, 1800),
                ((0.20, "timeout", "Timeout: payment gateway did not respond within 5s", (5000, 5300)),),
                ("rpc", "payments.PaymentService/Refund", {"rpc.system": "grpc", "rpc.service": "payments.PaymentService", "rpc.method": "Refund"}),
            ),
        ),
        (10, 40),
        has_resources=True,
    ),
    Server(
        "docs-mcp",
        0.25,
        (
            Tool("search_docs", 3, (60, 240), child=("db", "search docs-index", {"db.system.name": "elasticsearch", "db.operation.name": "search"})),
            Tool("get_page", 2, (30, 120), child=("db", "GET", {"db.system.name": "redis", "db.operation.name": "GET"})),
            Tool("list_sections", 1, (20, 60)),
        ),
        (10, 40),
        has_resources=True,
    ),
    Server(
        "search-mcp",
        0.20,
        (
            Tool("web_search", 3, (100, 300), child=_http("api.search.brave.com", "/res/v1/web/search")),
            Tool("fetch_url", 2, (150, 600), child=_http("docs.onlineboutique.dev", "/guides/checkout")),
            Tool(
                "web_search_v2",
                0.4,
                (2, 8),
                ((1.0, "unknown", "Unknown tool: web_search_v2", None),),
            ),
        ),
        (400, 900),
        has_resources=False,
        relist_per_call=True,
    ),
)
SERVERS_BY_NAME = {server.name: server for server in SERVERS}


def mcp_enabled() -> bool:
    return _env_bool("SIM_MCP_ENABLED", False)


def build_providers(exporter: SpanExporter) -> dict:
    """One TracerProvider per Resource: each server × deployment, plus each client service."""

    def provider(service: str, app: str) -> TracerProvider:
        tp = TracerProvider(
            resource=Resource.create(
                {
                    "service.name": service,
                    "cx.application.name": app,
                    "cx.subsystem.name": service,
                    "deployment.environment": os.environ.get("DEPLOYMENT_ENVIRONMENT", "test-cluster"),
                }
            )
        )
        tp.add_span_processor(BatchSpanProcessor(exporter, max_queue_size=8192))
        return tp

    providers = {(s.name, app): provider(s.name, app) for s in SERVERS for app, _ in DEPLOYMENTS}
    providers.update({client: provider(client, "mcp-clients") for client, _ in CLIENTS if client})
    return providers


class McpSim:
    def __init__(self, providers: dict, rng: random.Random | None = None):
        self.providers = providers
        self.tracers = {key: tp.get_tracer("mcp") for key, tp in providers.items()}
        self.rng = rng or random.Random()
        self.carry = 0.0
        self.scenario_carry = 0

    def emit_batch(self, server_spans: float, end_ns: int | None = None, window_sec: float = 120) -> int:
        """Emit about ``server_spans`` server spans, started over the ``window_sec`` before ``end_ns``."""
        end_ns = end_ns or time.time_ns()
        self.carry += server_spans
        emitted = 0
        while self.carry > 0:
            start_ns = end_ns - int((60 + window_sec * self.rng.random()) * 1e9)
            n = self._baseline_session(start_ns)
            self.scenario_carry += n
            if self.scenario_carry >= SCENARIO_EVERY_SPANS:
                self.scenario_carry -= SCENARIO_EVERY_SPANS
                n += self._runaway_session(start_ns) + self._caller_session(start_ns) + self._retry_session(start_ns)
            self.carry -= n
            emitted += n
        return emitted

    def flush(self) -> None:
        for tp in self.providers.values():
            tp.force_flush()

    def _pick(self, pairs):
        return self.rng.choices([value for value, _ in pairs], [weight for _, weight in pairs])[0]

    def _baseline_session(self, start_ns: int) -> int:
        rng = self.rng
        server = self._pick([(s, s.weight) for s in SERVERS])
        client = self._pick(CLIENTS)
        if client == "support-bot" and rng.random() < 0.8:
            user = SERVICE_ACCOUNT
        else:
            user = rng.choice([h for h in HUMANS if h != CALLER_USER])
        session = _Session(self, server, client, None if rng.random() < 0.1 else user, start_ns)
        session.request("initialize")
        session.request("tools/list")
        if rng.random() < 0.25:
            session.request("resources/list")
        if rng.random() < 0.15:
            session.request("prompts/list")
        tools = [(tool, tool.weight) for tool in server.tools]
        for _ in range(rng.randint(1, 6)):
            if server.relist_per_call:
                session.request("tools/list")
            session.request("tools/call", self._pick(tools))
        if rng.random() < 0.2:
            session.request("ping")
        return session.finish()

    def _runaway_session(self, start_ns: int) -> int:
        server = SERVERS_BY_NAME["github-mcp"]
        session = _Session(self, server, "claude-code-agent", self.rng.choice(HUMANS[:3]), start_ns)
        session.request("initialize")
        session.request("tools/list")
        search = server.tools[0]
        for _ in range(RUNAWAY_CALLS):
            session.request("tools/call", search)
        return session.finish()

    def _retry_session(self, start_ns: int) -> int:
        server = SERVERS_BY_NAME["billing-mcp"]
        session = _Session(self, server, "support-bot", SERVICE_ACCOUNT, start_ns)
        session.request("initialize")
        session.request("tools/list")
        refund = server.tools[2]
        for _ in range(3):
            session.request("tools/call", refund, force=refund.failures[0])
        session.request("tools/call", refund)
        return session.finish()

    def _caller_session(self, start_ns: int) -> int:
        server = SERVERS_BY_NAME["billing-mcp"]
        session = _Session(self, server, "claude-code-agent", CALLER_USER, start_ns)
        session.request("initialize")
        session.request("tools/list")
        lookup = server.tools[0]
        session.request("tools/call", lookup)
        for _ in range(7):
            session.request("tools/call", lookup, force=lookup.failures[0])
        return session.finish()


class _Session:
    def __init__(self, sim: McpSim, server: Server, client: str | None, user: str | None, start_ns: int):
        self.sim = sim
        self.rng = sim.rng
        self.server = server
        self.client = client
        self.user = user
        self.t = start_ns
        self.start_ns = start_ns
        self.request_id = 0
        self.server_spans = 0
        name, prefix, (lo, hi) = CLIENT_INFO[client]
        self.client_name, self.client_version = name, f"{prefix}{self.rng.randint(lo, hi)}".rstrip(".")
        self.client_address = f"10.{self.rng.randint(0, 255)}.{self.rng.randint(0, 255)}.{self.rng.randint(1, 254)}"
        self.client_port = self.rng.randint(32768, 65535)
        self.session_id = uuid.UUID(int=self.rng.getrandbits(128)).hex
        app = self.sim._pick(DEPLOYMENTS)
        self.server_tracer = sim.tracers[(server.name, app)]
        # Uninstrumented clients send no trace context: each server span is its own trace root.
        self.root = (
            sim.tracers[client].start_span("invoke_agent", kind=SpanKind.INTERNAL, start_time=start_ns)
            if client
            else None
        )

    def request(self, method: str, tool: Tool | None = None, force=None) -> None:
        rng = self.rng
        target = tool.name if tool else None
        name = f"{method} {target}" if target else method
        failure = force or _roll_failure(rng, tool, self.server, method)
        lo, hi = (failure and failure[3]) or (tool.latency_ms if tool else self._method_latency(method))
        server_ms = rng.uniform(lo, hi) * (3 if failure is None and rng.random() < 0.04 else 1)
        net_ms = rng.uniform(2, 25)

        attrs = {
            "mcp.method.name": method,
            "mcp.protocol.version": PROTOCOL_VERSION,
            "mcp.session.id": self.session_id,
            "jsonrpc.request.id": str(self.request_id),
            "network.transport": "tcp",
            "network.protocol.name": "http",
        }
        if tool:
            attrs.update({"gen_ai.tool.name": tool.name, "gen_ai.operation.name": "execute_tool"})
        self.request_id += 1

        client_span = None
        if self.root:
            client_span = self.sim.tracers[self.client].start_span(
                name,
                context=trace.set_span_in_context(self.root),
                kind=SpanKind.CLIENT,
                attributes=attrs | {"server.address": f"{self.server.name}.mcp.internal", "server.port": 8080},
                start_time=self.t,
            )
        server_start = self.t + int(net_ms / 2 * MS)
        server_end = server_start + int(server_ms * MS)
        server_attrs = attrs | {
            "mcp.client.name": self.client_name,
            "mcp.client.version": self.client_version,
            "user_agent.original": f"{self.client_name}/{self.client_version}",
            "client.address": self.client_address,
            "client.port": self.client_port,
        } | ({"enduser.id": self.user} if self.user else {})
        server_span = self.server_tracer.start_span(
            name,
            context=trace.set_span_in_context(client_span) if client_span else None,
            kind=SpanKind.SERVER,
            attributes=server_attrs,
            start_time=server_start,
        )
        if tool and tool.child and (failure is None or failure[1] in ("tool", "timeout")):
            self._child(server_span, tool.child, server_start, server_ms)
        _set_error(server_span, failure)
        server_span.end(end_time=server_end)
        self.server_spans += 1

        self.t = server_end + int(net_ms / 2 * MS)
        if client_span:
            _set_error(client_span, failure)
            client_span.end(end_time=self.t)
        self.t += int(rng.uniform(50, 600) * MS)

    def finish(self) -> int:
        if self.root:
            self.root.end(end_time=self.t)
        return self.server_spans

    def _method_latency(self, method: str) -> tuple[float, float]:
        if method in ("tools/list", "initialize"):
            return self.server.discovery_ms
        return (2, 15)

    def _child(self, parent, child: tuple[str, str, dict], start_ns: int, parent_ms: float) -> None:
        kind, name, attrs = child
        offset_ms = parent_ms * self.rng.uniform(0.05, 0.15)
        span = self.server_tracer.start_span(
            name,
            context=trace.set_span_in_context(parent),
            kind=SpanKind.CLIENT,
            attributes=attrs | ({"http.response.status_code": 200} if kind == "http" else {}),
            start_time=start_ns + int(offset_ms * MS),
        )
        span.end(end_time=start_ns + int(parent_ms * self.rng.uniform(0.7, 0.9) * MS))


def _roll_failure(rng: random.Random, tool: Tool | None, server: Server, method: str):
    if method == "resources/list" and not server.has_resources:
        return (1.0, "method", "Method not found: resources/list", (1, 4))
    for failure in tool.failures if tool else ():
        if rng.random() < failure[0]:
            return failure
    return None


def _set_error(span, failure) -> None:
    if failure is None:
        return
    error_type, code = ERRORS[failure[1]]
    span.set_attribute("error.type", error_type)
    if code:
        span.set_attribute("rpc.status_code", code)
    span.set_status(Status(StatusCode.ERROR, failure[2]))


# --- app.py wiring -------------------------------------------------------------------------------

_sim: McpSim | None = None


def init_mcp(exporter: SpanExporter) -> None:
    global _sim
    if mcp_enabled():
        _sim = McpSim(build_providers(exporter))


def emit_mcp_cycle(interval_sec: float) -> None:
    if _sim is not None:
        _sim.emit_batch(_env_int("SIM_MCP_SERVER_SPANS_PER_HOUR", 3600) * interval_sec / 3600, window_sec=interval_sec)


def flush_mcp() -> None:
    # Flush only: the exporter is shared with app.py's providers, which shut it down.
    if _sim is not None:
        _sim.flush()
