"""Emit Anthropic Admin / analytics / compliance gauges and integration logs.

Metric names match the Governance & Risk Audit dashboard (gauges + ``max_over_time``)
and AI Center Claude Products (``source=analytics``, ``date`` + ``product`` labels).
Usage series are Prometheus **gauges** named ``anthropic_admin_api_key_usage`` (not ``_total``).
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from opentelemetry._logs.severity import SeverityNumber
from opentelemetry.trace import TraceFlags
from prometheus_client import CollectorRegistry, Gauge

try:
    from opentelemetry.sdk._logs import LogRecord
except ImportError:  # SDK 1.39+ no longer re-exports LogRecord
    from opentelemetry.sdk._logs._internal import LogRecord

from sim.anthropic_admin.constants import (
    ACTIVE_USER_PRODUCTS,
    ACTIVE_WINDOWS,
    ACTIVITY_TYPES,
    ANALYTICS_CONNECTORS,
    ANALYTICS_GROUPS,
    ANALYTICS_PRODUCT_WEIGHTS,
    ANALYTICS_PRODUCTS,
    ANALYTICS_SKILLS,
    ANALYTICS_SOURCE,
    ANALYTICS_TOKEN_TYPES,
    ANALYTICS_TOOLS,
    ANTHROPIC_ADMIN_MODELS,
    CACHE_CREATION_TOKEN_TYPES,
    LIMIT_TYPES,
    LIST_PRICE_FACTOR,
    ORG_ROLES,
    RATE_LIMIT_EXTRA_MODELS,
    SERVICE_TIERS,
    SOURCE,
    SUMMARY_PRODUCT_PREFIXES,
    TOKEN_TYPE_LOG_FIELD,
    TOKEN_TYPES,
    cost_description,
    default_api_key_ids,
    default_organization_api_id,
    default_organization_id,
    default_roster_rows,
    default_workspace_ids,
    _stable_id,
)
from sim.common.env import _env_bool, _env_csv_model_pool, _env_float, _env_int
from sim.common.model_pricing import estimate_llm_cost_usd, model_rates

log = logging.getLogger(__name__)

USAGE_LABELS = (
    "cx_application_name",
    "cx_subsystem_name",
    "organization",
    "source",
    "model",
    "api_key_id",
    "context_window",
    "token_type",
)
RATE_LABELS = (
    "cx_application_name",
    "cx_subsystem_name",
    "organization",
    "source",
    "model",
    "limit_type",
)
COST_LABELS = (
    "cx_application_name",
    "cx_subsystem_name",
    "organization",
    "source",
    "workspace_id",
    "currency",
    "description",
)
# Dashboard filters inject user_email, api_key_id, model onto every metrics widget.
# Claude Products analytics series match live cxai label sets (date + product, no api_key_id).
_ANALYTICS_BASE = (
    "cx_application_name",
    "cx_subsystem_name",
    "organization",
    "source",
    "date",
)
ANALYTICS_COST_LABELS = _ANALYTICS_BASE + (
    "product",
    "model",
    "context_window",
    "speed",
    "group",
    "token_type",
    "cost_type",
    "amount_type",
    "currency",
)
ANALYTICS_TOKEN_LABELS = _ANALYTICS_BASE + (
    "product",
    "model",
    "context_window",
    "speed",
    "group",
    "token_type",
)
USER_COST_LABELS = _ANALYTICS_BASE + (
    "product",
    "model",
    "user_email",
    "user_id",
    "user_name",
    "group",
    "amount_type",
    "currency",
)
USER_TOKEN_LABELS = _ANALYTICS_BASE + (
    "product",
    "model",
    "user_email",
    "user_id",
    "user_name",
    "group",
    "token_type",
)
USER_REQUEST_LABELS = _ANALYTICS_BASE + (
    "product",
    "model",
    "user_email",
    "user_id",
    "user_name",
    "group",
    "currency",
)
USER_TOOL_LABELS = _ANALYTICS_BASE + (
    "product",
    "user_email",
    "user_id",
    "tool",
    "decision",
    "group",
)
USER_PRODUCT_LABELS = _ANALYTICS_BASE + (
    "product",
    "user_email",
    "user_id",
    "group",
)
# Claude Products session filters: Organization / Application / Subsystem (via _ANALYTICS_BASE),
# Product, Model, User, Group.
USER_SESSION_LABELS = _ANALYTICS_BASE + (
    "product",
    "model",
    "user_email",
    "user_id",
    "group",
)
USER_CHAT_LABELS = _ANALYTICS_BASE + (
    "product",
    "user_email",
    "user_id",
    "group",
)
SKILL_SESSION_LABELS = _ANALYTICS_BASE + ("skill_name", "surface", "group")
SKILL_COST_LABELS = _ANALYTICS_BASE + ("skill_name", "amount_type", "currency", "group")
SKILL_USAGE_LABELS = _ANALYTICS_BASE + ("skill_name", "share_status", "group")
CONNECTOR_SESSION_LABELS = _ANALYTICS_BASE + ("connector_name", "surface", "group")
CONNECTOR_USER_LABELS = _ANALYTICS_BASE + ("connector_name", "group")
CONNECTOR_CALL_LABELS = _ANALYTICS_BASE + ("connector_name", "call_type", "group")
SEAT_LABELS = _ANALYTICS_BASE
ACTIVE_USER_LABELS = _ANALYTICS_BASE + ("product", "window")
ADOPTION_LABELS = _ANALYTICS_BASE + ("window",)
ORG_REQUEST_LABELS = _ANALYTICS_BASE + ("product", "model", "context_window", "speed", "group")
ORG_CACHE_LABELS = _ANALYTICS_BASE + ("product", "model", "context_window", "speed", "token_type")
# Kept for Governance widgets that still join on user_email/model.
ANALYTICS_LABELS = USER_COST_LABELS
TOKEN_ANALYTICS_LABELS = USER_TOKEN_LABELS
ORG_USER_LABELS = (
    "cx_application_name",
    "cx_subsystem_name",
    "organization",
    "source",
    "role",
)
ACTIVITY_LABELS = (
    "cx_application_name",
    "cx_subsystem_name",
    "organization",
    "source",
    "type",
    "user_id",
    "user_email",
    "user_ip",
    "user_type",
    "api_key_id",
)

_WEB_SEARCH_USD_EACH = 0.01
_MODEL_WEIGHTS: dict[str, float] = {
    "claude-sonnet-5": 0.34,
    "claude-sonnet-4-6": 0.28,
    "claude-haiku-4-5-20251001": 0.18,
    "claude-opus-5": 0.10,
    "claude-opus-4-8": 0.06,
    "claude-fable-5": 0.04,
}
_ACTIVITY_WEIGHTS: dict[str, float] = {
    "claude_chat_viewed": 0.40,
    "claude_chat_created": 0.18,
    "claude_artifact_viewed": 0.14,
    "claude_file_viewed": 0.08,
    "claude_artifact_published": 0.06,
    "claude_file_uploaded": 0.05,
    "claude_file_exported": 0.04,
    "admin_request_created": 0.03,
    "claude_organization_settings_updated": 0.02,
}

# Deliberate skews for Claude Products insights (see docs/insights.txt).
_INSIGHT_COST_OUTLIER_INDEX = 0
_INSIGHT_TOOL_REJECTION_INDEX = 1


def _insight_model_weights() -> dict[str, float]:
    """Opus-family ≥60% of org spend (opus-spend-concentration)."""
    if not _env_bool("SIM_ANTHROPIC_INSIGHT_OPUS_CONCENTRATION", True):
        return _MODEL_WEIGHTS
    return {
        "claude-opus-5": 0.35,
        "claude-opus-4-8": 0.25,
        "claude-sonnet-5": 0.15,
        "claude-sonnet-4-6": 0.12,
        "claude-haiku-4-5-20251001": 0.08,
        "claude-fable-5": 0.05,
    }


def _insight_user_volume_mult(roster_index: int, *, now: datetime) -> float:
    """user-cost-concentration + cost-spike shaping."""
    mult = 1.0
    if roster_index == _INSIGHT_COST_OUTLIER_INDEX:
        mult *= _env_float("SIM_ANTHROPIC_INSIGHT_COST_OUTLIER_MULT", 12.0)
    if _env_bool("SIM_ANTHROPIC_INSIGHT_COST_SPIKE", True) and now.hour >= 14:
        mult *= _env_float("SIM_ANTHROPIC_INSIGHT_COST_SPIKE_MULT", 1.35)
    return mult


def _insight_tool_rejection_rate(roster_index: int) -> float:
    """high-tool-rejection: rejected / total > 30% with min 200 decisions."""
    if roster_index == _INSIGHT_TOOL_REJECTION_INDEX:
        return _env_float("SIM_ANTHROPIC_INSIGHT_TOOL_REJECTION_RATE", 0.42)
    return 0.02


def _cx_app() -> str:
    return os.environ.get("ANTHROPIC_ADMIN_CX_APPLICATION_NAME", "Claude").strip() or "Claude"


def _cx_sub() -> str:
    return (
        os.environ.get("ANTHROPIC_ADMIN_CX_SUBSYSTEM_NAME", "Enterprise API").strip()
        or "Enterprise API"
    )


def _log_app() -> str:
    return os.environ.get("ANTHROPIC_ADMIN_LOG_APPLICATION_NAME", "anthropic").strip() or "anthropic"


def _log_sub() -> str:
    return os.environ.get("ANTHROPIC_ADMIN_LOG_SUBSYSTEM_NAME", "testing").strip() or "testing"


def _pick_weighted(items: tuple[str, ...], weights: dict[str, float]) -> str:
    w = [max(0.01, weights.get(i, 0.05)) for i in items]
    return random.choices(list(items), weights=w, k=1)[0]


def _rate_limit_value(model: str, limit_type: str) -> float:
    is_opus = "opus" in model or "fable" in model
    is_batch = model == "batch"
    is_search = model == "web_search"
    if limit_type == "enqueued_batch_requests":
        return 100_000.0 if is_batch else 0.0
    if limit_type == "tool_uses_per_second":
        return 10.0 if is_search else (50.0 if not is_batch else 0.0)
    if limit_type == "requests_per_minute":
        return 4_000.0 if (is_batch or not is_opus) else 400.0
    if limit_type in ("input_tokens_per_minute_cache_aware", "fast_itpmca"):
        if is_search:
            return 0.0
        return 2_000_000.0 if (is_batch or not is_opus) else 400_000.0
    if limit_type in ("output_tokens_per_minute", "fast_otpm"):
        if is_search:
            return 0.0
        return 400_000.0 if (is_batch or not is_opus) else 80_000.0
    return 0.0


def _usage_delta(token_type: str, model: str, volume: float) -> int:
    heavy = "opus" in model or "fable" in model
    base = {
        "uncached_input_tokens": (8_000, 80_000) if not heavy else (4_000, 40_000),
        "output_tokens": (2_500, 28_000) if not heavy else (1_200, 12_000),
        "cache_read_input_tokens": (40_000, 400_000) if not heavy else (15_000, 160_000),
        "cache_creation.ephemeral_5m_input_tokens": (2_000, 24_000),
        "cache_creation.ephemeral_1h_input_tokens": (400, 6_000),
        "server_tool_use.web_search_requests": (0, 6),
    }[token_type]
    lo, hi = base
    raw = random.uniform(lo, hi) * volume
    if token_type == "server_tool_use.web_search_requests":
        if random.random() > 0.35:
            return 0
        return max(0, int(round(raw)))
    return max(0, int(round(raw)))


def _usd_for_tokens(model: str, token_type: str, amount: int) -> float:
    if amount <= 0:
        return 0.0
    if token_type == "server_tool_use.web_search_requests":
        return amount * _WEB_SEARCH_USD_EACH
    rates = model_rates(model)
    if token_type == "uncached_input_tokens":
        return estimate_llm_cost_usd(model, amount, 0)
    if token_type == "output_tokens":
        return estimate_llm_cost_usd(model, 0, amount)
    if token_type == "cache_read_input_tokens":
        return amount * rates.cache_read_rate() / 1_000_000.0
    if token_type.startswith("cache_creation."):
        return amount * rates.cache_write_rate() / 1_000_000.0
    return 0.0


def _integration_resource() -> dict[str, str]:
    return {
        "cx.integration.source.type": "anthropic_api_integration",
        "cx.integration.source.version": "1.0.0",
    }


@dataclass
class _SimUser:
    email: str
    name: str
    user_id: str
    api_key_id: str
    ip: str
    group: str
    roster_index: int = 0


@dataclass
class AnthropicAdminSim:
    registry: CollectorRegistry
    logger: object | None = None
    usage: Gauge = field(init=False)
    rate_limit: Gauge = field(init=False)
    cost: Gauge = field(init=False)
    user_cost: Gauge = field(init=False)
    user_tokens: Gauge = field(init=False)
    user_requests: Gauge = field(init=False)
    analytics_tokens: Gauge = field(init=False)
    analytics_cost: Gauge = field(init=False)
    user_tool_decisions: Gauge = field(init=False)
    user_skills_used: Gauge = field(init=False)
    user_distinct_skills: Gauge = field(init=False)
    user_connectors_used: Gauge = field(init=False)
    user_distinct_connectors: Gauge = field(init=False)
    user_office_connectors: Gauge = field(init=False)
    user_sessions: Gauge = field(init=False)
    user_chat_activity: Gauge = field(init=False)
    user_commits: Gauge = field(init=False)
    user_lines_added: Gauge = field(init=False)
    user_lines_removed: Gauge = field(init=False)
    user_pull_requests: Gauge = field(init=False)
    skill_sessions: Gauge = field(init=False)
    skill_cost: Gauge = field(init=False)
    skill_users: Gauge = field(init=False)
    skill_invocations: Gauge = field(init=False)
    connector_sessions: Gauge = field(init=False)
    connector_users: Gauge = field(init=False)
    connector_calls: Gauge = field(init=False)
    seats_assigned: Gauge = field(init=False)
    active_users: Gauge = field(init=False)
    adoption_rate: Gauge = field(init=False)
    pending_invites: Gauge = field(init=False)
    org_requests: Gauge = field(init=False)
    org_cache_creation: Gauge = field(init=False)
    org_users: Gauge = field(init=False)
    activity: Gauge = field(init=False)
    models: tuple[str, ...] = field(default_factory=lambda: ANTHROPIC_ADMIN_MODELS)
    api_keys: tuple[str, ...] = field(default_factory=default_api_key_ids)
    workspaces: tuple[str, ...] = field(default_factory=default_workspace_ids)
    organization: str = field(default_factory=default_organization_id)
    organization_api_id: str = field(default_factory=default_organization_api_id)
    emits_per_cycle: int = 8
    users: tuple[_SimUser, ...] = field(default_factory=tuple)
    _cost_accrued_usd: dict[tuple[str, str], float] = field(default_factory=dict)
    _user_cost_usd: dict[tuple[str, str, str], float] = field(default_factory=dict)
    # (email, product, model, token_type) — token_type must match Claude Products UI Mt map
    # (uncached_input_tokens|output_tokens|cache_read_input_tokens|cache_creation_input_tokens).
    _user_tokens: dict[tuple[str, str, str, str], int] = field(default_factory=dict)
    _user_tokens_total: dict[tuple[str, str, str], int] = field(default_factory=dict)
    _user_requests: dict[tuple[str, str, str], int] = field(default_factory=dict)
    _analytics_tokens: dict[tuple[str, ...], int] = field(default_factory=dict)
    _analytics_cost_usd: dict[tuple[str, ...], float] = field(default_factory=dict)
    _org_requests: dict[tuple[str, ...], int] = field(default_factory=dict)
    _org_cache: dict[tuple[str, ...], int] = field(default_factory=dict)
    _user_sessions: dict[tuple[str, str, str], int] = field(default_factory=dict)
    _user_chats: dict[tuple[str, str], int] = field(default_factory=dict)
    _user_commits: dict[tuple[str, str], int] = field(default_factory=dict)
    _user_lines_added: dict[tuple[str, str], int] = field(default_factory=dict)
    _user_lines_removed: dict[tuple[str, str], int] = field(default_factory=dict)
    _user_pull_requests: dict[tuple[str, str], int] = field(default_factory=dict)
    _user_tools: dict[tuple[str, ...], int] = field(default_factory=dict)
    _user_skills: dict[tuple[str, str], int] = field(default_factory=dict)
    _user_skill_set: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    _user_connectors: dict[tuple[str, str], int] = field(default_factory=dict)
    _user_connector_set: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    _skill_sessions: dict[tuple[str, str, str], int] = field(default_factory=dict)
    _skill_cost_usd: dict[tuple[str, str], float] = field(default_factory=dict)
    _skill_invocations: dict[tuple[str, str], int] = field(default_factory=dict)
    _skill_user_emails: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    _connector_sessions: dict[tuple[str, str, str], int] = field(default_factory=dict)
    _connector_calls: dict[tuple[str, str, str], int] = field(default_factory=dict)
    _connector_user_emails: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    _activity_counts: dict[tuple[str, ...], int] = field(default_factory=dict)
    _cost_day: str = ""
    _last_cost_log_ts: float = 0.0

    def __post_init__(self) -> None:
        self.models = _env_csv_model_pool("SIM_ANTHROPIC_ADMIN_MODELS", self.models)
        n_keys = max(1, _env_int("SIM_ANTHROPIC_ADMIN_API_KEYS", 6))
        self.api_keys = default_api_key_ids(n_keys)
        n_ws = max(1, _env_int("SIM_ANTHROPIC_ADMIN_WORKSPACES", 2))
        self.workspaces = default_workspace_ids(n_ws)
        org = os.environ.get("SIM_ANTHROPIC_ADMIN_ORGANIZATION", "").strip()
        self.organization = org or self.organization
        self.emits_per_cycle = max(1, _env_int("SIM_ANTHROPIC_ADMIN_EMITS_PER_CYCLE", self.emits_per_cycle))
        n_users = max(4, _env_int("SIM_ANTHROPIC_ADMIN_USERS", 24))
        self.users = self._build_users(n_users)
        self.usage = Gauge(
            "anthropic_admin_api_key_usage",
            "Anthropic Admin API token usage for the current scrape window (gauge)",
            labelnames=USAGE_LABELS,
            registry=self.registry,
        )
        self.rate_limit = Gauge(
            "anthropic_admin_rate_limit_value",
            "Anthropic Admin API rate-limit quotas",
            labelnames=RATE_LABELS,
            registry=self.registry,
        )
        self.cost = Gauge(
            "anthropic_admin_cost_amount",
            "Anthropic Admin API cost for the current UTC day (minor currency units)",
            labelnames=COST_LABELS,
            registry=self.registry,
        )
        self.user_cost = Gauge(
            "anthropic_analytics_user_cost",
            "Per-user Anthropic spend (USD) for the current UTC day",
            labelnames=USER_COST_LABELS,
            registry=self.registry,
        )
        self.user_tokens = Gauge(
            "anthropic_analytics_user_tokens",
            "Per-user Anthropic token usage for the current UTC day",
            labelnames=USER_TOKEN_LABELS,
            registry=self.registry,
        )
        self.user_requests = Gauge(
            "anthropic_analytics_user_requests",
            "Per-user Anthropic request count for the current UTC day",
            labelnames=USER_REQUEST_LABELS,
            registry=self.registry,
        )
        self.analytics_tokens = Gauge(
            "anthropic_analytics_tokens",
            "Org Anthropic token usage by product/model for the current UTC day",
            labelnames=ANALYTICS_TOKEN_LABELS,
            registry=self.registry,
        )
        self.analytics_cost = Gauge(
            "anthropic_analytics_cost",
            "Org Anthropic spend (USD) by product/model for the current UTC day",
            labelnames=ANALYTICS_COST_LABELS,
            registry=self.registry,
        )
        self.user_tool_decisions = Gauge(
            "anthropic_analytics_user_tool_decisions",
            "Per-user Claude tool accept/reject counts for the current UTC day",
            labelnames=USER_TOOL_LABELS,
            registry=self.registry,
        )
        self.user_skills_used = Gauge(
            "anthropic_analytics_user_skills_used",
            "Per-user skill invocation count for the current UTC day",
            labelnames=USER_PRODUCT_LABELS,
            registry=self.registry,
        )
        self.user_distinct_skills = Gauge(
            "anthropic_analytics_user_distinct_skills_used",
            "Per-user distinct skills used for the current UTC day",
            labelnames=USER_PRODUCT_LABELS,
            registry=self.registry,
        )
        self.user_connectors_used = Gauge(
            "anthropic_analytics_user_connectors_used",
            "Per-user connector call count for the current UTC day",
            labelnames=USER_PRODUCT_LABELS,
            registry=self.registry,
        )
        self.user_distinct_connectors = Gauge(
            "anthropic_analytics_user_distinct_connectors_used",
            "Per-user distinct connectors used for the current UTC day",
            labelnames=USER_PRODUCT_LABELS,
            registry=self.registry,
        )
        self.user_office_connectors = Gauge(
            "anthropic_analytics_user_office_distinct_connectors_used",
            "Per-user distinct Office connectors for the current UTC day",
            labelnames=USER_PRODUCT_LABELS,
            registry=self.registry,
        )
        self.user_sessions = Gauge(
            "anthropic_analytics_user_sessions",
            "Per-user Claude product sessions for the current UTC day",
            labelnames=USER_SESSION_LABELS,
            registry=self.registry,
        )
        self.user_chat_activity = Gauge(
            "anthropic_analytics_user_chat_activity",
            "Per-user Claude chat activity for the current UTC day",
            labelnames=USER_CHAT_LABELS,
            registry=self.registry,
        )
        self.user_commits = Gauge(
            "anthropic_analytics_user_commits",
            "Per-user Claude Code commits for the current UTC day",
            labelnames=USER_PRODUCT_LABELS,
            registry=self.registry,
        )
        self.user_lines_added = Gauge(
            "anthropic_analytics_user_lines_added",
            "Per-user Claude Code lines added for the current UTC day",
            labelnames=USER_PRODUCT_LABELS,
            registry=self.registry,
        )
        self.user_lines_removed = Gauge(
            "anthropic_analytics_user_lines_removed",
            "Per-user Claude Code lines removed for the current UTC day",
            labelnames=USER_PRODUCT_LABELS,
            registry=self.registry,
        )
        self.user_pull_requests = Gauge(
            "anthropic_analytics_user_pull_requests",
            "Per-user Claude Code pull requests for the current UTC day",
            labelnames=USER_PRODUCT_LABELS,
            registry=self.registry,
        )
        self.skill_sessions = Gauge(
            "anthropic_analytics_skill_sessions",
            "Skill sessions for the current UTC day",
            labelnames=SKILL_SESSION_LABELS,
            registry=self.registry,
        )
        self.skill_cost = Gauge(
            "anthropic_analytics_skill_cost",
            "Skill-attributed spend (USD) for the current UTC day",
            labelnames=SKILL_COST_LABELS,
            registry=self.registry,
        )
        self.skill_users = Gauge(
            "anthropic_analytics_skill_users",
            "Distinct users per skill for the current UTC day",
            labelnames=SKILL_USAGE_LABELS,
            registry=self.registry,
        )
        self.skill_invocations = Gauge(
            "anthropic_analytics_skill_invocations",
            "Skill invocations for the current UTC day",
            labelnames=SKILL_USAGE_LABELS,
            registry=self.registry,
        )
        self.connector_sessions = Gauge(
            "anthropic_analytics_connector_sessions",
            "Connector sessions for the current UTC day",
            labelnames=CONNECTOR_SESSION_LABELS,
            registry=self.registry,
        )
        self.connector_users = Gauge(
            "anthropic_analytics_connector_users",
            "Distinct users per connector for the current UTC day",
            labelnames=CONNECTOR_USER_LABELS,
            registry=self.registry,
        )
        self.connector_calls = Gauge(
            "anthropic_analytics_connector_calls",
            "Connector calls by type for the current UTC day",
            labelnames=CONNECTOR_CALL_LABELS,
            registry=self.registry,
        )
        self.seats_assigned = Gauge(
            "anthropic_analytics_seats_assigned",
            "Assigned Anthropic seats",
            labelnames=SEAT_LABELS,
            registry=self.registry,
        )
        self.active_users = Gauge(
            "anthropic_analytics_active_users",
            "Active Anthropic users by product and window",
            labelnames=ACTIVE_USER_LABELS,
            registry=self.registry,
        )
        self.adoption_rate = Gauge(
            "anthropic_analytics_adoption_rate",
            "Seat adoption rate (percent)",
            labelnames=ADOPTION_LABELS,
            registry=self.registry,
        )
        self.pending_invites = Gauge(
            "anthropic_analytics_pending_invites",
            "Pending Anthropic seat invites",
            labelnames=SEAT_LABELS,
            registry=self.registry,
        )
        self.org_requests = Gauge(
            "anthropic_org_requests_total",
            "Org Anthropic request count by product/model for the current UTC day",
            labelnames=ORG_REQUEST_LABELS,
            registry=self.registry,
        )
        self.org_cache_creation = Gauge(
            "anthropic_org_cache_creation",
            "Org Anthropic cache-write tokens for the current UTC day",
            labelnames=ORG_CACHE_LABELS,
            registry=self.registry,
        )
        self.org_users = Gauge(
            "anthropic_compliance_org_users_total",
            "Licensed Anthropic org members by role",
            labelnames=ORG_USER_LABELS,
            registry=self.registry,
        )
        self.activity = Gauge(
            "anthropic_compliance_activity_events_total",
            "Anthropic compliance activity events (cumulative)",
            labelnames=ACTIVITY_LABELS,
            registry=self.registry,
        )
        self._seed_rate_limits()
        self._seed_org_users()
        self._seed_usage_series()

    def _build_users(self, n: int) -> tuple[_SimUser, ...]:
        roster = default_roster_rows(n)
        out: list[_SimUser] = []
        for i, row in enumerate(roster):
            out.append(
                _SimUser(
                    email=str(row["user.email"]),
                    name=str(row["user.name"]),
                    user_id=_stable_id("user_", f"user:{i}", 24),
                    api_key_id=self.api_keys[i % len(self.api_keys)],
                    ip=f"203.0.113.{(i % 250) + 1}",
                    group=ANALYTICS_GROUPS[i % len(ANALYTICS_GROUPS)],
                    roster_index=i,
                )
            )
        return tuple(out)

    def _metric_base(self, source: str) -> dict[str, str]:
        return {
            "cx_application_name": _cx_app(),
            "cx_subsystem_name": _cx_sub(),
            "organization": self.organization,
            "source": source,
        }

    def _analytics_base(self) -> dict[str, str]:
        day = self._cost_day or datetime.now(timezone.utc).date().isoformat()
        return {**self._metric_base(ANALYTICS_SOURCE), "date": day}

    def _clear_analytics_day(self) -> None:
        self._analytics_tokens.clear()
        self._analytics_cost_usd.clear()
        self._org_requests.clear()
        self._org_cache.clear()
        self._user_cost_usd.clear()
        self._user_tokens.clear()
        self._user_tokens_total.clear()
        self._user_requests.clear()
        self._user_sessions.clear()
        self._user_chats.clear()
        self._user_commits.clear()
        self._user_lines_added.clear()
        self._user_lines_removed.clear()
        self._user_pull_requests.clear()
        self._user_tools.clear()
        self._user_skills.clear()
        self._user_skill_set.clear()
        self._user_connectors.clear()
        self._user_connector_set.clear()
        self._skill_sessions.clear()
        self._skill_cost_usd.clear()
        self._skill_invocations.clear()
        self._skill_user_emails.clear()
        self._connector_sessions.clear()
        self._connector_calls.clear()
        self._connector_user_emails.clear()

    def _seed_org_users(self) -> None:
        for role, count in ORG_ROLES:
            self.org_users.labels(**self._metric_base("compliance"), role=role).set(count)

    def _seed_usage_series(self) -> None:
        """Create one series per key so Distinct API Keys is non-zero on the first scrape."""
        for key in self.api_keys:
            for model in self.models:
                for token_type in TOKEN_TYPES:
                    self.usage.labels(
                        **self._metric_base(SOURCE),
                        model=model,
                        api_key_id=key,
                        context_window="0-200k",
                        token_type=token_type,
                    ).set(0)

    def _seed_rate_limits(self) -> None:
        models = tuple(self.models) + RATE_LIMIT_EXTRA_MODELS
        for model in models:
            for limit_type in LIMIT_TYPES:
                val = _rate_limit_value(model, limit_type)
                if val <= 0 and limit_type == "enqueued_batch_requests" and model not in RATE_LIMIT_EXTRA_MODELS:
                    continue
                if val <= 0 and limit_type == "tool_uses_per_second" and model != "web_search":
                    continue
                self.rate_limit.labels(**self._metric_base(SOURCE), model=model, limit_type=limit_type).set(val)

    def _maybe_roll_cost_day(self, now: datetime) -> None:
        # User analytics stay cumulative so max_over_time spend/request widgets keep climbing.
        # Workspace cost lines reset at UTC midnight to mimic Admin daily cost reports.
        day = now.date().isoformat()
        if self._cost_day != day:
            self._cost_day = day
            self._cost_accrued_usd.clear()
            self._clear_analytics_day()
            self._seed_chat_activity_for_day()

    def _seed_chat_activity_for_day(self) -> None:
        """Seed chat/cowork gauges with group so group-filtered max_over_time works from UTC midnight."""
        base = self._analytics_base()
        for user in self.users:
            for product in ("chat", "cowork"):
                chats = random.randint(4, 28) + (user.roster_index % 9)
                self._user_chats[(user.email, product)] = chats
                self.user_chat_activity.labels(
                    **base,
                    product=product,
                    user_email=user.email,
                    user_id=user.user_id,
                    group=user.group,
                ).set(chats)

    def _accrue_line_cost(self, *, workspace_id: str, description: str, usd: float) -> None:
        if usd <= 0:
            return
        key = (workspace_id, description)
        self._cost_accrued_usd[key] = self._cost_accrued_usd.get(key, 0.0) + usd
        self.cost.labels(
            **self._metric_base(SOURCE),
            workspace_id=workspace_id,
            currency="USD",
            description=description,
        ).set(round(self._cost_accrued_usd[key] * 100.0, 2))

    def _inc(self, store: dict, key, amt) -> None:
        store[key] = store.get(key, 0) + amt

    def _record_product_analytics(
        self,
        *,
        user: _SimUser,
        product: str,
        model: str,
        context_window: str,
        amounts: dict[str, int],
        usd_this: float,
        tokens_this: int,
    ) -> None:
        speed = "standard"
        group = user.group
        base = self._analytics_base()
        # Include group so Cost-by-group can sum org cost/tokens/requests by group label.
        org_key = (product, model, context_window, speed, group)
        cache_key = (product, model, context_window, speed)
        self._inc(self._org_requests, org_key, 1)
        self.org_requests.labels(
            **base,
            product=product,
            model=model,
            context_window=context_window,
            speed=speed,
            group=group,
        ).set(self._org_requests[org_key])

        for token_type in ANALYTICS_TOKEN_TYPES:
            amt = int(amounts.get(token_type, 0))
            tkey = org_key + (token_type,)
            self._inc(self._analytics_tokens, tkey, amt)
            self.analytics_tokens.labels(
                **base,
                product=product,
                model=model,
                context_window=context_window,
                speed=speed,
                group=group,
                token_type=token_type,
            ).set(self._analytics_tokens[tkey])
            token_usd = _usd_for_tokens(model, token_type, amt)
            for amount_type, usd in (
                ("actual", token_usd),
                ("list", token_usd * LIST_PRICE_FACTOR),
            ):
                ckey = tkey + (amount_type,)
                self._inc(self._analytics_cost_usd, ckey, usd)
                self.analytics_cost.labels(
                    **base,
                    product=product,
                    model=model,
                    context_window=context_window,
                    speed=speed,
                    group=group,
                    token_type=token_type,
                    cost_type="tokens",
                    amount_type=amount_type,
                    currency="USD",
                ).set(round(self._analytics_cost_usd[ckey], 4))

        _cache_src = {
            "ephemeral_5m_input_tokens": "cache_creation.ephemeral_5m_input_tokens",
            "ephemeral_1h_input_tokens": "cache_creation.ephemeral_1h_input_tokens",
        }
        for token_type in CACHE_CREATION_TOKEN_TYPES:
            amt = int(amounts.get(_cache_src[token_type], 0))
            ckey = cache_key + (token_type,)
            self._inc(self._org_cache, ckey, amt)
            self.org_cache_creation.labels(
                **base,
                product=product,
                model=model,
                context_window=context_window,
                speed=speed,
                token_type=token_type,
            ).set(self._org_cache[ckey])

        uk = (user.email, product, model)
        self._inc(self._user_cost_usd, uk, usd_this)
        self._inc(self._user_tokens_total, uk, tokens_this)
        self._inc(self._user_requests, uk, 1)
        user_l = {
            **base,
            "product": product,
            "model": model,
            "user_email": user.email,
            "user_id": user.user_id,
            "user_name": user.name,
            "group": group,
        }
        self.user_cost.labels(**user_l, amount_type="actual", currency="USD").set(
            round(self._user_cost_usd[uk], 4)
        )
        self.user_cost.labels(**user_l, amount_type="list", currency="USD").set(
            round(self._user_cost_usd[uk] * LIST_PRICE_FACTOR, 4)
        )
        # User-detail "Tokens over time" maps these token_type ids (not total_tokens).
        user_token_parts = {
            "uncached_input_tokens": int(amounts.get("uncached_input_tokens", 0)),
            "output_tokens": int(amounts.get("output_tokens", 0)),
            "cache_read_input_tokens": int(amounts.get("cache_read_input_tokens", 0)),
            "cache_creation_input_tokens": int(
                amounts.get("cache_creation.ephemeral_5m_input_tokens", 0)
            )
            + int(amounts.get("cache_creation.ephemeral_1h_input_tokens", 0)),
        }
        for token_type, amt in user_token_parts.items():
            tkey = uk + (token_type,)
            self._inc(self._user_tokens, tkey, amt)
            self.user_tokens.labels(**user_l, token_type=token_type).set(self._user_tokens[tkey])
        self.user_requests.labels(**user_l, currency="USD").set(self._user_requests[uk])

        session_key = (user.email, product, model)
        if product in ("claude_code", "cowork", "design", "claude_in_chrome"):
            self._inc(self._user_sessions, session_key, 1)
            self.user_sessions.labels(
                **base,
                product=product,
                model=model,
                user_email=user.email,
                user_id=user.user_id,
                group=user.group,
            ).set(self._user_sessions[session_key])
        if random.random() < 0.05:
            dkey = (user.email, "design", model)
            self._inc(self._user_sessions, dkey, 1)
            self.user_sessions.labels(
                **base,
                product="design",
                model=model,
                user_email=user.email,
                user_id=user.user_id,
                group=user.group,
            ).set(self._user_sessions[dkey])
        if product in ("chat", "cowork"):
            self._inc(self._user_chats, (user.email, product), 1)
            self.user_chat_activity.labels(
                **base,
                product=product,
                user_email=user.email,
                user_id=user.user_id,
                group=user.group,
            ).set(self._user_chats[(user.email, product)])

        if product == "claude_code":
            tool = random.choices(ANALYTICS_TOOLS, weights=(0.78, 0.18, 0.03, 0.01), k=1)[0]
            decision = (
                "rejected"
                if random.random() < _insight_tool_rejection_rate(user.roster_index)
                else "accepted"
            )
            tkey = (user.email, product, tool, decision)
            self._inc(self._user_tools, tkey, 1)
            self.user_tool_decisions.labels(
                **base,
                product=product,
                user_email=user.email,
                user_id=user.user_id,
                tool=tool,
                decision=decision,
                group=user.group,
            ).set(self._user_tools[tkey])
            # Claude Products user-detail widgets: max_over_time(anthropic_analytics_user_commits|lines_*|pull_requests).
            # Labels match USER_PRODUCT_LABELS (product + user + group; no model).
            commits = random.randint(0, 3)
            lines_added = random.randint(40, 400)
            lines_removed = random.randint(5, 80)
            prs = 1 if random.random() < 0.28 else 0
            product_key = (user.email, product)
            self._inc(self._user_commits, product_key, commits)
            self._inc(self._user_lines_added, product_key, lines_added)
            self._inc(self._user_lines_removed, product_key, lines_removed)
            self._inc(self._user_pull_requests, product_key, prs)
            user_prod = {
                **base,
                "product": product,
                "user_email": user.email,
                "user_id": user.user_id,
                "group": user.group,
            }
            self.user_commits.labels(**user_prod).set(self._user_commits[product_key])
            self.user_lines_added.labels(**user_prod).set(self._user_lines_added[product_key])
            self.user_lines_removed.labels(**user_prod).set(self._user_lines_removed[product_key])
            self.user_pull_requests.labels(**user_prod).set(self._user_pull_requests[product_key])

        if product in ("claude_code", "cowork") and random.random() < 0.35:
            skill = random.choice(ANALYTICS_SKILLS)
            surface = "cowork" if product == "cowork" else "claude_code"
            skey = (user.email, product)
            self._inc(self._user_skills, skey, 1)
            self._user_skill_set.setdefault(skey, set()).add(skill)
            self.user_skills_used.labels(
                **base,
                product=product,
                user_email=user.email,
                user_id=user.user_id,
                group=user.group,
            ).set(self._user_skills[skey])
            self.user_distinct_skills.labels(
                **base,
                product=product,
                user_email=user.email,
                user_id=user.user_id,
                group=user.group,
            ).set(len(self._user_skill_set[skey]))
            sk = (skill, surface, user.group)
            self._inc(self._skill_sessions, sk, 1)
            self._inc(self._skill_cost_usd, (skill, user.group), usd_this * 0.08)
            self._inc(self._skill_invocations, (skill, user.group), 1)
            self._skill_user_emails.setdefault((skill, user.group), set()).add(user.email)
            share = "public" if skill in ("cx-catalog", "review", "brainstorming", "create-pr") else "private"
            self.skill_sessions.labels(
                **base, skill_name=skill, surface=surface, group=user.group
            ).set(self._skill_sessions[sk])
            self.skill_cost.labels(
                **base,
                skill_name=skill,
                amount_type="list",
                currency="USD",
                group=user.group,
            ).set(round(self._skill_cost_usd[(skill, user.group)], 4))
            self.skill_cost.labels(
                **base,
                skill_name=skill,
                amount_type="overage",
                currency="USD",
                group=user.group,
            ).set(round(self._skill_cost_usd[(skill, user.group)] * 0.99, 4))
            self.skill_invocations.labels(
                **base, skill_name=skill, share_status=share, group=user.group
            ).set(self._skill_invocations[(skill, user.group)])
            self.skill_users.labels(
                **base, skill_name=skill, share_status=share, group=user.group
            ).set(len(self._skill_user_emails[(skill, user.group)]))

        if product in ("cowork", "claude_in_chrome", "chat") and random.random() < 0.28:
            connector = random.choice(ANALYTICS_CONNECTORS)
            surface = "cowork" if product != "claude_code" else "claude_code"
            ckey = (user.email, product)
            self._inc(self._user_connectors, ckey, 1)
            self._user_connector_set.setdefault(ckey, set()).add(connector)
            self.user_connectors_used.labels(
                **base,
                product=product,
                user_email=user.email,
                user_id=user.user_id,
                group=user.group,
            ).set(self._user_connectors[ckey])
            self.user_distinct_connectors.labels(
                **base,
                product=product,
                user_email=user.email,
                user_id=user.user_id,
                group=user.group,
            ).set(len(self._user_connector_set[ckey]))
            ck = (connector, surface, user.group)
            self._inc(self._connector_sessions, ck, 1)
            self.connector_sessions.labels(
                **base, connector_name=connector, surface=surface, group=user.group
            ).set(self._connector_sessions[ck])
            self._connector_user_emails.setdefault((connector, user.group), set()).add(user.email)
            self.connector_users.labels(**base, connector_name=connector, group=user.group).set(
                len(self._connector_user_emails[(connector, user.group)])
            )
            call_type = random.choices(("read", "write", "unclassified"), weights=(0.70, 0.18, 0.12), k=1)[0]
            call_n = random.randint(1, 24)
            call_key = (connector, call_type, user.group)
            self._inc(self._connector_calls, call_key, call_n)
            self.connector_calls.labels(
                **base, connector_name=connector, call_type=call_type, group=user.group
            ).set(self._connector_calls[call_key])

        if random.random() < 0.04:
            office_product = random.choice(("office.excel", "office.word", "office.outlook"))
            okey = (user.email, office_product, model)
            self._user_connector_set.setdefault((user.email, office_product), set()).add("office")
            self.user_office_connectors.labels(
                **base,
                product=office_product,
                user_email=user.email,
                user_id=user.user_id,
                group=user.group,
            ).set(len(self._user_connector_set[(user.email, office_product)]))
            self._inc(self._user_sessions, okey, 1)
            self.user_sessions.labels(
                **base,
                product=office_product,
                model=model,
                user_email=user.email,
                user_id=user.user_id,
                group=user.group,
            ).set(self._user_sessions[okey])

    def _ensure_skill_and_connector_samples(self) -> None:
        """Keep Claude Products skill/connector tiles and daily logs non-empty."""
        user = self.users[0]
        base = self._analytics_base()
        if not self._skill_sessions:
            skill = ANALYTICS_SKILLS[0]
            surface = "claude_code"
            skey = (user.email, "claude_code")
            self._inc(self._user_skills, skey, 1)
            self._user_skill_set.setdefault(skey, set()).add(skill)
            self._inc(self._skill_sessions, (skill, surface, user.group), 1)
            self._inc(self._skill_cost_usd, (skill, user.group), 0.12)
            self.user_skills_used.labels(
                **base,
                product="claude_code",
                user_email=user.email,
                user_id=user.user_id,
                group=user.group,
            ).set(self._user_skills[skey])
            self.user_distinct_skills.labels(
                **base,
                product="claude_code",
                user_email=user.email,
                user_id=user.user_id,
                group=user.group,
            ).set(len(self._user_skill_set[skey]))
            self.skill_sessions.labels(
                **base, skill_name=skill, surface=surface, group=user.group
            ).set(1)
            self.skill_cost.labels(
                **base,
                skill_name=skill,
                amount_type="list",
                currency="USD",
                group=user.group,
            ).set(0.12)
            self.skill_cost.labels(
                **base,
                skill_name=skill,
                amount_type="overage",
                currency="USD",
                group=user.group,
            ).set(0.12)
            self._inc(self._skill_invocations, (skill, user.group), 1)
            self._skill_user_emails.setdefault((skill, user.group), set()).add(user.email)
            self.skill_invocations.labels(
                **base, skill_name=skill, share_status="public", group=user.group
            ).set(1)
            self.skill_users.labels(
                **base, skill_name=skill, share_status="public", group=user.group
            ).set(1)
        if not self._connector_sessions:
            connector = ANALYTICS_CONNECTORS[0]
            surface = "cowork"
            ckey = (user.email, "cowork")
            self._inc(self._user_connectors, ckey, 1)
            self._user_connector_set.setdefault(ckey, set()).add(connector)
            self._inc(self._connector_sessions, (connector, surface, user.group), 1)
            self.user_connectors_used.labels(
                **base,
                product="cowork",
                user_email=user.email,
                user_id=user.user_id,
                group=user.group,
            ).set(1)
            self.user_distinct_connectors.labels(
                **base,
                product="cowork",
                user_email=user.email,
                user_id=user.user_id,
                group=user.group,
            ).set(1)
            self.connector_sessions.labels(
                **base, connector_name=connector, surface=surface, group=user.group
            ).set(1)
            self._connector_user_emails.setdefault((connector, user.group), set()).add(user.email)
            self.connector_users.labels(**base, connector_name=connector, group=user.group).set(1)
            self._inc(self._connector_calls, (connector, "read", user.group), 12)
            self.connector_calls.labels(
                **base, connector_name=connector, call_type="read", group=user.group
            ).set(12)

    def _refresh_seat_gauges(self) -> None:
        base = self._analytics_base()
        seats = max(1, sum(n for _, n in ORG_ROLES))
        pending = max(0, int(round(seats * 0.06)))
        self.seats_assigned.labels(**base).set(seats)
        self.pending_invites.labels(**base).set(pending)
        n_users = max(1, len(self.users))
        # Unique users with any analytics activity today, plus a floor so tiles are non-zero.
        active_emails = {email for email, _product, _model in self._user_sessions} | {
            email for email, _p in self._user_chats
        }
        daily_all = max(3, len(active_emails) or int(n_users * 0.45))
        weekly_all = min(seats, max(daily_all, int(n_users * 0.75)))
        monthly_all = min(seats, max(weekly_all, int(n_users * 0.9)))
        by_product = {
            "all": (daily_all, weekly_all, monthly_all),
            "claude_code": (max(2, daily_all // 2), max(4, weekly_all // 2), max(6, monthly_all // 2)),
            "chat": (max(1, daily_all // 3), max(2, weekly_all // 3), max(4, monthly_all // 2)),
            "cowork": (max(1, daily_all // 4), max(2, weekly_all // 4), max(3, monthly_all // 3)),
            "claude_design": (0, 1, 2),
            "office_agent": (0, 1, 1),
            "science": (0, 0, 0),
        }
        for product in ACTIVE_USER_PRODUCTS:
            daily, weekly, monthly = by_product.get(product, (0, 0, 0))
            window_vals = {"daily": daily, "weekly": weekly, "monthly": monthly}
            for window in ACTIVE_WINDOWS:
                self.active_users.labels(**base, product=product, window=window).set(window_vals[window])
        adoption = {"daily": daily_all, "weekly": weekly_all, "monthly": monthly_all}
        for window in ACTIVE_WINDOWS:
            self.adoption_rate.labels(**base, window=window).set(round(100.0 * adoption[window] / seats, 2))

    def _emit_log(self, *, stream: str, data: dict, extra_attrs: dict | None = None) -> None:
        if self.logger is None:
            return
        body = {
            "data": data,
            "resource": {"attributes": _integration_resource()},
            "stream": stream,
        }
        attrs: dict[str, str | int | float | bool] = {
            "cx.application.name": _log_app(),
            "cx.subsystem.name": _log_sub(),
            "stream": stream,
            **_integration_resource(),
        }
        if extra_attrs:
            attrs.update(extra_attrs)
        kw: dict = {
            "timestamp": time.time_ns(),
            "trace_id": 0,
            "span_id": 0,
            "trace_flags": TraceFlags.get_default(),
            "severity_number": SeverityNumber.INFO,
            "severity_text": "INFO",
            "body": json.dumps(body, separators=(",", ":")),
            "attributes": attrs,
        }
        if "resource" in inspect.signature(LogRecord.__init__).parameters:
            resource = getattr(self.logger, "resource", None)
            if resource is not None:
                kw["resource"] = resource
        self.logger.emit(LogRecord(**kw))

    def emit_cycle(self, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        self._maybe_roll_cost_day(now)
        if not self._user_chats:
            self._seed_chat_activity_for_day()
        volume = max(0.01, _env_float("SIM_ANTHROPIC_ADMIN_VOLUME", 0.08))
        day = now.date().isoformat()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)

        for _ in range(self.emits_per_cycle):
            user = random.choice(self.users)
            vol_mult = _insight_user_volume_mult(user.roster_index, now=now)
            model = _pick_weighted(self.models, _insight_model_weights())
            product = _pick_weighted(ANALYTICS_PRODUCTS, ANALYTICS_PRODUCT_WEIGHTS)
            context_window = "200k-1M" if random.random() < 0.12 else "0-200k"
            service_tier = random.choices(SERVICE_TIERS, weights=(0.82, 0.12, 0.06), k=1)[0]
            workspace = random.choice(self.workspaces)
            amounts: dict[str, int] = {}
            usd_this = 0.0
            tokens_this = 0
            for token_type in TOKEN_TYPES:
                amt = _usage_delta(token_type, model, volume * vol_mult)
                amounts[token_type] = amt
                self.usage.labels(
                    **self._metric_base(SOURCE),
                    model=model,
                    api_key_id=user.api_key_id,
                    context_window=context_window,
                    token_type=token_type,
                ).set(amt)
                usd_this += _usd_for_tokens(model, token_type, amt)
                if token_type != "server_tool_use.web_search_requests":
                    tokens_this += amt
                if amt > 0:
                    self._accrue_line_cost(
                        workspace_id=workspace,
                        description=cost_description(model=model, kind=token_type, service_tier=service_tier),
                        usd=_usd_for_tokens(model, token_type, amt),
                    )

            self._record_product_analytics(
                user=user,
                product=product,
                model=model,
                context_window=context_window,
                amounts=amounts,
                usd_this=usd_this,
                tokens_this=tokens_this,
            )

            log_data = {
                "api_key_id": user.api_key_id,
                "context_window": context_window,
                "model": model,
                "organization": self.organization,
            }
            for token_type in TOKEN_TYPES:
                log_data[TOKEN_TYPE_LOG_FIELD[token_type]] = amounts.get(token_type, 0)
            self._emit_log(stream="anthropic.api_keys_usage", data=log_data)

            act_type = _pick_weighted(ACTIVITY_TYPES, _ACTIVITY_WEIGHTS)
            act_key = (act_type, user.user_id, user.email, user.ip, "user_actor", user.api_key_id)
            self._activity_counts[act_key] = self._activity_counts.get(act_key, 0) + 1
            self.activity.labels(
                **self._metric_base("compliance"),
                type=act_type,
                user_id=user.user_id,
                user_email=user.email,
                user_ip=user.ip,
                user_type="user_actor",
                api_key_id=user.api_key_id,
            ).set(self._activity_counts[act_key])
            self._emit_log(
                stream="anthropic.activity",
                data={
                    "id": _stable_id("activity_", f"{now.timestamp()}:{user.user_id}:{act_type}", 24),
                    "type": act_type,
                    "created_at": now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                    "organization": self.organization,
                    "user_id": user.user_id,
                    "user_email": user.email,
                    "user_type": "user_actor",
                    "user_ip": user.ip,
                    "user_agent": "Claude/1.0 sim",
                },
            )

        for model in tuple(self.models) + RATE_LIMIT_EXTRA_MODELS:
            for limit_type in ("requests_per_minute", "input_tokens_per_minute_cache_aware"):
                base = _rate_limit_value(model, limit_type)
                if base <= 0:
                    continue
                self.rate_limit.labels(
                    **self._metric_base(SOURCE),
                    model=model,
                    limit_type=limit_type,
                ).set(round(base * (1.0 + random.uniform(-0.02, 0.02)), 2))

        self._ensure_skill_and_connector_samples()
        self._refresh_seat_gauges()
        self._maybe_emit_daily_logs(now, day, day_start, day_end)

    def _maybe_emit_daily_logs(self, now: datetime, day: str, day_start: datetime, day_end: datetime) -> None:
        interval_s = max(60, _env_int("SIM_ANTHROPIC_ADMIN_COST_LOG_SEC", 86400))
        ts = now.timestamp()
        if self._last_cost_log_ts and (ts - self._last_cost_log_ts) < interval_s:
            return
        if not self._user_cost_usd and not self._cost_accrued_usd:
            return
        self._last_cost_log_ts = ts
        start_s = day_start.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_s = day_end.strftime("%Y-%m-%dT%H:%M:%SZ")
        org_id = self.organization_api_id
        for (email, product, model), usd in self._user_cost_usd.items():
            user = next((u for u in self.users if u.email == email), None)
            reqs = self._user_requests.get((email, product, model), 0)
            toks = self._user_tokens_total.get((email, product, model), 0)
            self._emit_log(
                stream="anthropic.user_cost",
                data={
                    "amount_cents": f"{usd * 100:.6f}",
                    "list_amount_cents": f"{usd * 108:.6f}",
                    "currency": "USD",
                    "model": model,
                    "organization": self.organization,
                    "organization_id": org_id,
                    "product": product,
                    "report_date": day,
                    "report_ending_at": end_s,
                    "report_starting_at": start_s,
                    "requests": reqs,
                    "user_email": email,
                    "user_id": user.user_id if user else "",
                    "user_name": user.name if user else "",
                    "user_type": "user_actor",
                    "group": user.group if user else "Default",
                },
            )
            self._emit_log(
                stream="anthropic.user.usage",
                data={
                    "model": model,
                    "organization": self.organization,
                    "organization_id": org_id,
                    "product": product,
                    "report_date": day,
                    "report_ending_at": end_s,
                    "report_starting_at": start_s,
                    "requests": reqs,
                    "total_tokens": toks,
                    "uncached_input_tokens": max(1, toks // 8),
                    "output_tokens": max(1, toks // 12),
                    "cache_read_input_tokens": max(0, toks - toks // 8 - toks // 12),
                    "cache_creation_5m_input_tokens": 0,
                    "cache_creation_1h_input_tokens": 0,
                    "web_search_requests": 0,
                    "user_email": email,
                    "user_id": user.user_id if user else "",
                    "user_name": user.name if user else "",
                    "user_type": "user_actor",
                },
            )
            self._emit_log(
                stream="anthropic.models.cost",
                data={
                    "amount_cents": f"{usd * 100:.6f}",
                    "list_amount_cents": f"{usd * 108:.6f}",
                    "context_window": "0-200k",
                    "cost_type": "tokens",
                    "currency": "USD",
                    "model": model,
                    "organization": self.organization,
                    "organization_id": org_id,
                    "product": product,
                    "report_date": day,
                    "report_ending_at": end_s,
                    "report_starting_at": start_s,
                    "speed": "standard",
                    "token_type": "uncached_input_tokens",
                },
            )

        seats = max(1, sum(n for _, n in ORG_ROLES))
        pending = max(0, int(round(seats * 0.06)))
        summary: dict[str, object] = {
            "assigned_seat_count": seats,
            "pending_invite_count": pending,
            "organization": self.organization,
            "organization_id": org_id,
            "report_date": day,
            "report_ending_at": end_s,
            "report_starting_at": start_s,
            "science_entitled_user_count": 0,
        }
        n_users = max(1, len(self.users))
        daily_all = max(3, int(n_users * 0.45))
        weekly_all = min(seats, max(daily_all, int(n_users * 0.75)))
        monthly_all = min(seats, max(weekly_all, int(n_users * 0.9)))
        window_vals = {"daily": daily_all, "weekly": weekly_all, "monthly": monthly_all}
        product_share = {
            "": 1.0,
            "chat_": 0.35,
            "claude_code_": 0.50,
            "cowork_": 0.28,
            "office_agent_": 0.04,
            "claude_design_": 0.06,
            "science_": 0.0,
        }
        for _product, prefix in SUMMARY_PRODUCT_PREFIXES:
            share = product_share.get(prefix, 0.2)
            for window, total in window_vals.items():
                key = f"{prefix}{window}_active_users"
                summary[key] = int(round(total * share)) if prefix else total
            if prefix == "":
                for window, total in window_vals.items():
                    summary[f"{window}_adoption_rate"] = round(100.0 * total / seats, 2)
        self._emit_log(stream="anthropic.summary.active_users", data=summary)

        for user in self.users:
            sessions = sum(v for (email, _p, _m), v in self._user_sessions.items() if email == user.email)
            chats = sum(v for (email, _p), v in self._user_chats.items() if email == user.email)
            commits = sum(v for (email, _p), v in self._user_commits.items() if email == user.email)
            lines_added = sum(v for (email, _p), v in self._user_lines_added.items() if email == user.email)
            lines_removed = sum(v for (email, _p), v in self._user_lines_removed.items() if email == user.email)
            pull_requests = sum(v for (email, _p), v in self._user_pull_requests.items() if email == user.email)
            self._emit_log(
                stream="anthropic.user_activity",
                data={
                    "chat_distinct_conversation_count": max(0, chats // 2),
                    "chat_message_count": chats * random.randint(2, 8),
                    "commit_count": commits,
                    "distinct_session_count": max(sessions, 1 if random.random() < 0.8 else 0),
                    "last_activity_date": day,
                    "lines_added": lines_added,
                    "lines_removed": lines_removed,
                    "organization": self.organization,
                    "organization_id": org_id,
                    "pull_request_count": pull_requests,
                    "report_date": day,
                    "user_email": user.email,
                    "user_id": user.user_id,
                    "group": user.group,
                },
            )

        for skill in ANALYTICS_SKILLS:
            invocations = sum(
                v for (sname, _surface, _group), v in self._skill_sessions.items() if sname == skill
            )
            if invocations <= 0:
                continue
            cents = sum(v for (sname, _group), v in self._skill_cost_usd.items() if sname == skill) * 100.0
            users_n = sum(
                1
                for (_email, _product), names in self._user_skill_set.items()
                if skill in names
            )
            self._emit_log(
                stream="anthropic.skills",
                data={
                    "attributed_list_price_cents": f"{cents:.4f}",
                    "currency": "USD",
                    "distinct_user_count": max(1, users_n),
                    "estimated_overage_spend_cents": f"{cents * 0.99:.4f}",
                    "invocation_count": invocations,
                    "organization": self.organization,
                    "organization_id": org_id,
                    "report_date": day,
                    "skill_name": skill,
                },
            )

        for connector in ANALYTICS_CONNECTORS:
            sess = sum(
                v for (name, _surface, _group), v in self._connector_sessions.items() if name == connector
            )
            if sess <= 0:
                continue
            users_n = sum(1 for names in self._user_connector_set.values() if connector in names)
            self._emit_log(
                stream="anthropic.connectors",
                data={
                    "connector_name": connector,
                    "distinct_user_count": max(1, users_n),
                    "organization": self.organization,
                    "organization_id": org_id,
                    "read_call_count": sess * random.randint(2, 12),
                    "report_date": day,
                    "unclassified_call_count": 0,
                    "write_call_count": sess * random.randint(0, 3),
                },
            )
