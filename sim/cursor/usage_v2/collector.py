"""
Prometheus collector for Cursor Usage ``cursor_*`` metrics.

Semantics match ``RealCursorQueryEngineService`` / Notion query map:

- **Deltas** (events, lines, requests, …): accrued between scrapes, exposed once, then
  cleared so ``sum_over_time`` sums real bucket grains (never cumulative counters).
- **Snapshots** (roster, spend limits, cycle scalars, membership flags): restated every
  scrape for ``last_over_time`` / ``max_over_time``.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

from sim.cursor.usage_v2.constants import (
    cursor_usage_cx_application,
    cursor_usage_cx_subsystem,
)


def _label_key(labels: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(k), str(v)) for k, v in labels.items()))


@dataclass
class _SeriesSpec:
    name: str
    documentation: str
    labelnames: tuple[str, ...]
    kind: str  # "delta" | "snapshot"


# Label sets aligned with cxai-dev (minus __name__).
_BASE = ("cx_application_name", "cx_subsystem_name", "team_id")
_MEMBER = _BASE + ("email", "user_id", "name")
_EVENT = _BASE + (
    "email",
    "model",
    "conversation_id",
    "kind",
    "max_mode",
    "billing_mode",
    "is_chargeable",
    "is_cloud_agent",
    "is_headless",
    "automation_id",
    "discount_pct",
    "date",
)
_TOKEN_EVENT = _BASE + (
    "email",
    "model",
    "conversation_id",
    "kind",
    "max_mode",
    "billing_mode",
    "is_chargeable",
    "is_headless",
    "token_type",
    "date",
)
_EMAIL_TEAM = _BASE + ("email", "user_id")
_EMAIL_DATE = _BASE + ("email", "date")
_EMAIL_USER_DATE = _BASE + ("email", "user_id", "date")
_EMAIL_MODEL = _BASE + ("email", "model", "date")
_EMAIL_SURFACE = _BASE + ("email", "user_id", "surface", "date")
_AI_CODE = _BASE + (
    "email",
    "user_id",
    "repo_name",
    "surface",
    "direction",
    "commit_source",
    "branch_name",
    "date",
    "is_primary_branch",
)
_SPEND = _BASE + (
    "email",
    "user_id",
    "name",
    "group_id",
    "group_name",
    "date",
    "is_former",
    "is_unassigned",
)
_ACTIVE = _BASE + ("email", "user_id", "date", "client_version")
_CONV = _BASE + ("dimension", "value", "date")
_GROUP = _BASE + ("group_id", "group_name", "is_unassigned")
_ORG_TEAM = _BASE + ("organization", "team_name", "team_role")
_REQUESTS_CLASS = _BASE + ("email", "user_id", "billing_class", "date")
_FILE_EXT = _BASE + ("email", "user_id", "file_extension", "change_source", "model", "date")
_COMMAND = _BASE + ("email", "command_name")
_MCP = _BASE + ("email", "mcp_server_name", "tool_name", "date")
_SKILL = _BASE + ("email", "skill_name", "date")
_REPO_LINES = _BASE + ("email", "user_id", "repo_name", "date")
_COMMITS = _BASE + ("email", "user_id", "repo_name", "branch_name", "date")


_SPECS: tuple[_SeriesSpec, ...] = (
    _SeriesSpec("cursor_events_total", "Cursor usage events (bucket delta)", _EVENT, "delta"),
    _SeriesSpec("cursor_event_cost_usd", "Cursor event cost USD (bucket delta)", _EVENT, "delta"),
    _SeriesSpec(
        "cursor_event_list_price_usd",
        "Cursor event list price USD (bucket delta)",
        _EVENT,
        "delta",
    ),
    _SeriesSpec(
        "cursor_event_request_units_total",
        "Cursor event request units (bucket delta)",
        _EVENT,
        "delta",
    ),
    _SeriesSpec(
        "cursor_event_tokens_total",
        "Cursor event tokens by type (bucket delta)",
        _TOKEN_EVENT,
        "delta",
    ),
    _SeriesSpec(
        "cursor_requests_total",
        "Cursor requests by surface (bucket delta)",
        _EMAIL_SURFACE,
        "delta",
    ),
    _SeriesSpec(
        "cursor_requests_by_class_total",
        "Cursor requests by billing class (bucket delta)",
        _REQUESTS_CLASS,
        "delta",
    ),
    _SeriesSpec(
        "cursor_user_model_messages_total",
        "Cursor user model messages (bucket delta)",
        _EMAIL_MODEL,
        "delta",
    ),
    _SeriesSpec(
        "cursor_user_agent_diffs_accepted_total",
        "Cursor agent diffs accepted (bucket delta)",
        _EMAIL_DATE,
        "delta",
    ),
    _SeriesSpec(
        "cursor_user_agent_diffs_rejected_total",
        "Cursor agent diffs rejected (bucket delta)",
        _EMAIL_DATE,
        "delta",
    ),
    _SeriesSpec(
        "cursor_user_agent_diffs_suggested_total",
        "Cursor agent diffs suggested (bucket delta)",
        _EMAIL_DATE,
        "delta",
    ),
    _SeriesSpec(
        "cursor_tab_accepts_total",
        "Cursor tab accepts (bucket delta)",
        _EMAIL_TEAM + ("date",),
        "delta",
    ),
    _SeriesSpec(
        "cursor_tab_suggestions_total",
        "Cursor tab suggestions (bucket delta)",
        _EMAIL_TEAM + ("date",),
        "delta",
    ),
    _SeriesSpec(
        "cursor_ai_code_lines_total",
        "Cursor AI/non-AI committed lines (bucket delta)",
        _AI_CODE,
        "delta",
    ),
    _SeriesSpec(
        "cursor_ai_code_total_lines_added_total",
        "Cursor AI total lines added by repo (bucket delta)",
        _REPO_LINES,
        "delta",
    ),
    _SeriesSpec(
        "cursor_ai_code_total_lines_deleted_total",
        "Cursor AI total lines deleted by repo (bucket delta)",
        _REPO_LINES,
        "delta",
    ),
    _SeriesSpec(
        "cursor_ai_change_lines_added_total",
        "Cursor AI change lines added (bucket delta)",
        _EMAIL_TEAM + ("date",),
        "delta",
    ),
    _SeriesSpec(
        "cursor_ai_change_file_lines_added_total",
        "Cursor AI change lines by file extension (bucket delta)",
        _FILE_EXT,
        "delta",
    ),
    _SeriesSpec(
        "cursor_accepted_lines_added_total",
        "Cursor accepted lines added (bucket delta)",
        _EMAIL_USER_DATE,
        "delta",
    ),
    _SeriesSpec(
        "cursor_accepted_lines_deleted_total",
        "Cursor accepted lines deleted (bucket delta)",
        _EMAIL_USER_DATE,
        "delta",
    ),
    _SeriesSpec(
        "cursor_commits_total",
        "Cursor commits (bucket delta)",
        _COMMITS,
        "delta",
    ),
    _SeriesSpec(
        "cursor_accepts_total",
        "Cursor accepts (bucket delta)",
        _EMAIL_USER_DATE,
        "delta",
    ),
    _SeriesSpec(
        "cursor_applies_total",
        "Cursor applies (bucket delta)",
        _EMAIL_USER_DATE,
        "delta",
    ),
    _SeriesSpec(
        "cursor_member_daily_spend_usd",
        "Cursor member daily spend USD (bucket delta)",
        _SPEND,
        "delta",
    ),
    _SeriesSpec(
        "cursor_conversation_total",
        "Cursor conversation dimension counts (bucket delta)",
        _CONV,
        "delta",
    ),
    _SeriesSpec(
        "cursor_user_plan_usage_total",
        "Cursor plan-mode usage by model (bucket delta)",
        _EMAIL_MODEL,
        "delta",
    ),
    _SeriesSpec(
        "cursor_user_ask_mode_usage_total",
        "Cursor ask-mode usage by model (bucket delta)",
        _EMAIL_MODEL,
        "delta",
    ),
    _SeriesSpec(
        "cursor_user_command_usage_total",
        "Cursor command invocations (bucket delta)",
        _COMMAND,
        "delta",
    ),
    _SeriesSpec(
        "cursor_user_mcp_usage_total",
        "Cursor MCP server invocations (bucket delta)",
        _MCP,
        "delta",
    ),
    _SeriesSpec(
        "cursor_user_skill_usage_total",
        "Cursor skill invocations (bucket delta)",
        _SKILL,
        "delta",
    ),
    # Snapshots
    _SeriesSpec(
        "cursor_member_info",
        "Cursor member roster row (1 if present)",
        _BASE + ("email", "user_id", "name", "role", "is_removed"),
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_member_active",
        "Cursor member active flag for the day",
        _ACTIVE,
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_group_members",
        "Cursor billing group membership info",
        _GROUP,
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_org_team_membership_info",
        "Cursor org↔team membership",
        _ORG_TEAM,
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_member_spend_gross_usd",
        "Cursor member cycle gross spend (level)",
        _EMAIL_TEAM + ("name",),
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_member_spend_overage_usd",
        "Cursor member cycle overage (level)",
        _EMAIL_TEAM + ("name",),
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_member_monthly_limit_usd",
        "Cursor member monthly limit (level)",
        _EMAIL_TEAM + ("name",),
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_member_effective_limit_usd",
        "Cursor member effective limit (level)",
        _EMAIL_TEAM + ("name",),
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_billing_cycle_start_seconds",
        "Cursor billing cycle start unix seconds",
        _BASE,
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_billing_cycle_end_seconds",
        "Cursor billing cycle end unix seconds",
        _BASE,
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_model_distinct_users",
        "Cursor distinct users per model (daily grain)",
        _BASE + ("model",),
        "snapshot",
    ),
)

_SPEC_BY_NAME = {s.name: s for s in _SPECS}


class CursorUsageCollector(Collector):
    """Expose Cursor Usage metrics for scrape / remote_write."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._deltas: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self._snapshots: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}

    def base_labels(self, team_id: str) -> dict[str, str]:
        return {
            "cx_application_name": cursor_usage_cx_application(),
            "cx_subsystem_name": cursor_usage_cx_subsystem(),
            "team_id": team_id,
        }

    def add_delta(self, name: str, labels: dict[str, str], amount: float) -> None:
        if amount == 0:
            return
        spec = _SPEC_BY_NAME.get(name)
        if spec is None or spec.kind != "delta":
            raise ValueError(f"unknown delta metric: {name}")
        missing = [k for k in spec.labelnames if k not in labels]
        if missing:
            raise ValueError(f"{name} missing labels: {missing}")
        key = (name, _label_key({k: labels[k] for k in spec.labelnames}))
        with self._lock:
            self._deltas[key] += float(amount)

    def set_snapshot(self, name: str, labels: dict[str, str], value: float) -> None:
        spec = _SPEC_BY_NAME.get(name)
        if spec is None or spec.kind != "snapshot":
            raise ValueError(f"unknown snapshot metric: {name}")
        missing = [k for k in spec.labelnames if k not in labels]
        if missing:
            raise ValueError(f"{name} missing labels: {missing}")
        key = (name, _label_key({k: labels[k] for k in spec.labelnames}))
        with self._lock:
            self._snapshots[key] = float(value)

    def clear_snapshots_with_prefix(self, name: str) -> None:
        with self._lock:
            drop = [k for k in self._snapshots if k[0] == name]
            for k in drop:
                del self._snapshots[k]

    def collect(self) -> Iterable[GaugeMetricFamily]:
        with self._lock:
            deltas = dict(self._deltas)
            self._deltas.clear()
            snapshots = dict(self._snapshots)

        families: dict[str, GaugeMetricFamily] = {}
        for name, label_pairs in list(deltas.keys()) + list(snapshots.keys()):
            spec = _SPEC_BY_NAME[name]
            if name not in families:
                families[name] = GaugeMetricFamily(
                    name,
                    spec.documentation,
                    labels=list(spec.labelnames),
                )

        for (name, label_pairs), value in deltas.items():
            spec = _SPEC_BY_NAME[name]
            label_map = dict(label_pairs)
            families[name].add_metric([label_map[k] for k in spec.labelnames], value)

        for (name, label_pairs), value in snapshots.items():
            spec = _SPEC_BY_NAME[name]
            label_map = dict(label_pairs)
            families[name].add_metric([label_map[k] for k in spec.labelnames], value)

        return list(families.values())


_collector: CursorUsageCollector | None = None


def get_cursor_usage_collector() -> CursorUsageCollector | None:
    return _collector


def register_cursor_usage_metrics(registry) -> CursorUsageCollector:
    global _collector
    if _collector is not None:
        return _collector
    _collector = CursorUsageCollector()
    registry.register(_collector)
    return _collector


def reset_cursor_usage_collector_for_tests() -> None:
    """Test helper — drop the process-wide collector singleton."""
    global _collector
    _collector = None
