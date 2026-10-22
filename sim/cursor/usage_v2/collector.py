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
    "service_account",
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
    "service_account",
)
_EMAIL_TEAM = _BASE + ("email", "user_id")
_EMAIL_DATE = _BASE + ("email", "date")
_EMAIL_USER_DATE = _BASE + ("email", "user_id", "date")
# Adoption seat KPIs (getActiveUsersByEmail): agent lines + tab suggestions.
_USER_LINES = _BASE + ("email", "source", "colour", "outcome", "date")
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
# cx498 FE renamed conversation metrics to cursor_user_* and keeps email (Sa=[dimension,value,email]).
_CONV_USER = _BASE + ("dimension", "value", "email", "date")
# Topic Mix — FE catalog keeps email (unlike cursor_conversation_total realOmitsLabels).
_CONV_SUBCAT = _BASE + ("mode", "subcategory", "email", "date")
_GROUP = _BASE + ("group_id", "group_name", "is_unassigned")
_ORG_TEAM = _BASE + ("organization", "team_name", "team_role")
_REQUESTS_CLASS = _BASE + ("email", "user_id", "billing_class", "date")
_FILE_EXT = _BASE + ("email", "user_id", "file_extension", "change_source", "model", "date")
_COMMAND = _BASE + ("email", "command_name")
_MCP = _BASE + ("email", "mcp_server_name", "tool_name", "date")
_SKILL = _BASE + ("email", "skill_name", "date")
_REPO_LINES = _BASE + ("email", "user_id", "repo_name", "date")
_COMMITS = _BASE + ("email", "user_id", "repo_name", "branch_name", "date")
_BUGBOT_PR = _BASE + ("repo_name", "date")
_BUGBOT_ISSUES = _BASE + ("repo_name", "severity", "state", "date")
_BUGBOT_ISSUES_SNAP = _BASE + ("state",)
_BUGBOT_REPOS = _BASE + ("enabled", "manual_only")
# Annual Budget / pooled usage (Admin org API). Catalog: cadence=daily, grain=level.
# Team contract adds organization (+ team_name on usage); FE also needs pool_enabled.
_POOL_ORG = ("cx_application_name", "cx_subsystem_name", "organization")
_POOL_USAGE = _POOL_ORG + ("team_id", "team_name")
# Team DAU gauges (Adoption / analytics) — date-labeled daily levels.
_DAU = _BASE + ("date",)


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
        "Cursor requests by surface (daily level; realDateLabeled)",
        _EMAIL_SURFACE,
        "snapshot",
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
    # FE Adoption Active Users / Adoption Rate / Idle Seats (not member_active).
    _SeriesSpec(
        "cursor_user_lines_total",
        "Cursor user agent/tab lines by source/outcome (daily level; realDateLabeled)",
        _USER_LINES,
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_user_tab_suggestions_total",
        "Cursor user tab suggestions (daily level; realDateLabeled)",
        _EMAIL_DATE,
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_user_tab_accepts_total",
        "Cursor user tab accepts (daily level; realDateLabeled)",
        _EMAIL_DATE,
        "snapshot",
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
        "Cursor AI change lines added (daily level; realDateLabeled)",
        _EMAIL_TEAM + ("date",),
        "snapshot",
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
        "Cursor accepts (daily level; realDateLabeled)",
        _EMAIL_USER_DATE,
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_applies_total",
        "Cursor applies (daily level; realDateLabeled)",
        _EMAIL_USER_DATE,
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_member_daily_spend_usd",
        "Cursor member daily spend USD (daily level; realDateLabeled)",
        _SPEND,
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_active_users_total",
        "Cursor team DAU (daily level)",
        _DAU,
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_active_users_cli",
        "Cursor CLI DAU (daily level)",
        _DAU,
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_active_users_cloud_agent",
        "Cursor cloud-agent DAU (daily level)",
        _DAU,
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_active_users_bugbot",
        "Cursor Bugbot DAU (daily level)",
        _DAU,
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_conversation_total",
        "Cursor conversation dimension counts (bucket delta; legacy team-level)",
        _CONV,
        "delta",
    ),
    _SeriesSpec(
        "cursor_user_conversation_total",
        "Cursor per-user conversation dimension counts (bucket delta)",
        _CONV_USER,
        "delta",
    ),
    _SeriesSpec(
        "cursor_user_conversation_snapshot",
        "Cursor per-user conversation dimension counts (bucket delta; Insights)",
        _CONV_USER,
        "delta",
    ),
    _SeriesSpec(
        "cursor_conversation_subcategory_snapshot",
        "Cursor conversation subcategory counts by mode (bucket delta; Topic Mix legacy)",
        _CONV_SUBCAT,
        "delta",
    ),
    _SeriesSpec(
        "cursor_user_conversation_subcategory_snapshot",
        "Cursor conversation subcategory counts by mode (bucket delta; Topic Mix)",
        _CONV_SUBCAT,
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
        _EMAIL_TEAM + ("name", "role"),
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_member_spend_overage_usd",
        "Cursor member cycle overage (level)",
        _EMAIL_TEAM + ("name", "role"),
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_member_monthly_limit_usd",
        "Cursor member monthly limit (level)",
        _EMAIL_TEAM + ("name", "role"),
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_member_effective_limit_usd",
        "Cursor member effective limit (level)",
        _EMAIL_TEAM + ("name", "role"),
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
    # Bugbot
    _SeriesSpec(
        "cursor_bugbot_prs_reviewed",
        "Cursor Bugbot PRs reviewed (bucket delta)",
        _BUGBOT_PR,
        "delta",
    ),
    _SeriesSpec(
        "cursor_bugbot_pr_reviews_total",
        "Cursor Bugbot PR reviews (bucket delta)",
        _BUGBOT_PR,
        "delta",
    ),
    _SeriesSpec(
        "cursor_bugbot_issues_total",
        "Cursor Bugbot issues by severity/state (bucket delta)",
        _BUGBOT_ISSUES,
        "delta",
    ),
    _SeriesSpec(
        "cursor_bugbot_issues_snapshot",
        "Cursor Bugbot findings snapshot by state",
        _BUGBOT_ISSUES_SNAP,
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_bugbot_repos",
        "Cursor Bugbot repo coverage count",
        _BUGBOT_REPOS,
        "snapshot",
    ),
    # Annual Budget widgets (pooled-usage Admin API)
    _SeriesSpec(
        "cursor_org_pool_enabled",
        "Cursor org pooled budget enabled (1 when Annual Budget widgets have data)",
        _POOL_ORG,
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_org_pool_limit_usd",
        "Cursor org annual pooled budget limit USD",
        _POOL_ORG,
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_org_pool_remaining_usd",
        "Cursor org annual pooled budget remaining USD",
        _POOL_ORG,
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_org_pooled_usage_usd",
        "Cursor org pooled usage USD (level; one series per team)",
        _POOL_USAGE,
        "snapshot",
    ),
    # --- Brief §8 Add metrics (c4c parity; labels aligned with existing families) ---
    _SeriesSpec(
        "cursor_lines_added_total",
        "Cursor lines added (bucket delta)",
        _EMAIL_TEAM + ("date",),
        "delta",
    ),
    _SeriesSpec(
        "cursor_lines_deleted_total",
        "Cursor lines deleted (bucket delta)",
        _EMAIL_TEAM + ("date",),
        "delta",
    ),
    _SeriesSpec(
        "cursor_rejects_total",
        "Cursor rejects (bucket delta)",
        _EMAIL_TEAM + ("date",),
        "delta",
    ),
    _SeriesSpec(
        "cursor_login_attempts_total",
        "Cursor login attempts (bucket delta)",
        _BASE + ("date", "result"),
        "delta",
    ),
    _SeriesSpec(
        "cursor_logout_total",
        "Cursor logouts (bucket delta)",
        _BASE + ("date",),
        "delta",
    ),
    _SeriesSpec(
        "cursor_mcp_config_events_total",
        "Cursor MCP config events (bucket delta)",
        _BASE + ("date", "event"),
        "delta",
    ),
    _SeriesSpec(
        "cursor_member_added_total",
        "Cursor members added (bucket delta)",
        _BASE + ("date",),
        "delta",
    ),
    _SeriesSpec(
        "cursor_member_hard_limit_usd",
        "Cursor member hard spend limit USD (level)",
        _EMAIL_TEAM + ("name", "role"),
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_member_fast_premium_requests_total",
        "Cursor member fast premium requests (bucket delta)",
        _EMAIL_TEAM + ("date",),
        "delta",
    ),
    _SeriesSpec(
        "cursor_model_access_enabled",
        "Cursor model access enabled (level)",
        _BASE + ("model",),
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_model_access_new_default",
        "Cursor model access new-default flag (level)",
        _BASE + ("model",),
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_org_contract_start_seconds",
        "Cursor org contract start unix seconds",
        _POOL_ORG,
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_org_contract_end_seconds",
        "Cursor org contract end unix seconds",
        _POOL_ORG,
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_org_members",
        "Cursor org member count (level)",
        _POOL_ORG + ("role",),
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_org_member_contract_spend_usd",
        "Cursor org member contract spend USD (level)",
        _EMAIL_TEAM + ("name",),
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_user_tab_rejects_total",
        "Cursor user tab rejects (daily level; realDateLabeled)",
        _EMAIL_DATE,
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_user_api_key_events_total",
        "Cursor user API key events (bucket delta)",
        _EMAIL_DATE + ("event",),
        "delta",
    ),
    _SeriesSpec(
        "cursor_agent_diffs_accepted_total",
        "Cursor agent diffs accepted (org total; bucket delta)",
        _BASE + ("date",),
        "delta",
    ),
    _SeriesSpec(
        "cursor_agent_diffs_rejected_total",
        "Cursor agent diffs rejected (org total; bucket delta)",
        _BASE + ("date",),
        "delta",
    ),
    _SeriesSpec(
        "cursor_ai_change_file_lines_deleted_total",
        "Cursor AI change file lines deleted (bucket delta)",
        _EMAIL_DATE + ("file_extension",),
        "delta",
    ),
    _SeriesSpec(
        "cursor_ai_change_lines_deleted_total",
        "Cursor AI change lines deleted (bucket delta)",
        _EMAIL_DATE,
        "delta",
    ),
    _SeriesSpec(
        "cursor_event_token_fee_usd",
        "Cursor event token fee USD (bucket delta)",
        _EVENT,
        "delta",
    ),
    _SeriesSpec(
        "cursor_user_extension_accepts_total",
        "Cursor user extension accepts (bucket delta)",
        _EMAIL_DATE + ("extension",),
        "delta",
    ),
    _SeriesSpec(
        "cursor_user_extension_rejects_total",
        "Cursor user extension rejects (bucket delta)",
        _EMAIL_DATE + ("extension",),
        "delta",
    ),
    _SeriesSpec(
        "cursor_user_extension_files_total",
        "Cursor user extension files (bucket delta)",
        _EMAIL_DATE + ("extension",),
        "delta",
    ),
    _SeriesSpec(
        "cursor_user_extension_lines_accepted_total",
        "Cursor user extension lines accepted (bucket delta)",
        _EMAIL_DATE + ("extension",),
        "delta",
    ),
    _SeriesSpec(
        "cursor_user_extension_lines_rejected_total",
        "Cursor user extension lines rejected (bucket delta)",
        _EMAIL_DATE + ("extension",),
        "delta",
    ),
    _SeriesSpec(
        "cursor_user_extension_lines_suggested_total",
        "Cursor user extension lines suggested (bucket delta)",
        _EMAIL_DATE + ("extension",),
        "delta",
    ),
    # Remaining brief §8 Add metrics (c4c label shapes, Oct 2026).
    _SeriesSpec(
        "cursor_agent_green_lines_accepted_total",
        "Cursor agent green lines accepted (org daily total)",
        _BASE + ("date", "organization", "team_name"),
        "delta",
    ),
    _SeriesSpec(
        "cursor_agent_green_lines_rejected_total",
        "Cursor agent green lines rejected (org daily total)",
        _BASE + ("date", "organization", "team_name"),
        "delta",
    ),
    _SeriesSpec(
        "cursor_agent_red_lines_accepted_total",
        "Cursor agent red lines accepted (org daily total)",
        _BASE + ("date", "organization", "team_name"),
        "delta",
    ),
    _SeriesSpec(
        "cursor_agent_red_lines_rejected_total",
        "Cursor agent red lines rejected (org daily total)",
        _BASE + ("date", "organization", "team_name"),
        "delta",
    ),
    _SeriesSpec(
        "cursor_ai_changes_total",
        "Cursor AI changes by source (bucket delta)",
        _BASE + ("date", "organization", "team_name", "source"),
        "delta",
    ),
    _SeriesSpec(
        "cursor_ai_code_commit_range_lines_total",
        "Cursor AI code commit-range lines (bucket delta)",
        _BASE
        + (
            "date",
            "organization",
            "team_name",
            "email",
            "user_id",
            "repo_name",
            "branch_name",
            "commit_source",
            "model",
            "operation_type",
        ),
        "delta",
    ),
    _SeriesSpec(
        "cursor_audit_events_total",
        "Cursor audit events (bucket delta)",
        _BASE + ("date", "organization", "team_name", "event_type", "user_email", "ip_address"),
        "delta",
    ),
    _SeriesSpec(
        "cursor_bugbot_reviews_total",
        "Cursor Bugbot reviews (bucket delta)",
        _BASE
        + (
            "date",
            "organization",
            "team_name",
            "repo_name",
            "dry_run",
            "publication_status",
        ),
        "delta",
    ),
    _SeriesSpec(
        "cursor_bugbot_review_bugs_found_total",
        "Cursor Bugbot review bugs found (bucket delta)",
        _BASE
        + (
            "date",
            "organization",
            "team_name",
            "repo_name",
            "dry_run",
            "publication_status",
        ),
        "delta",
    ),
    _SeriesSpec(
        "cursor_bugbot_review_cost_usd",
        "Cursor Bugbot review cost USD (bucket delta)",
        _BASE
        + (
            "date",
            "organization",
            "team_name",
            "repo_name",
            "dry_run",
            "publication_status",
        ),
        "delta",
    ),
    _SeriesSpec(
        "cursor_client_version_users",
        "Cursor users by client version (daily level)",
        _BASE + ("date", "organization", "team_name", "client_version"),
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_conversation_snapshot",
        "Cursor conversation snapshot by dimension (org; daily level)",
        _BASE + ("date", "organization", "team_name", "dimension", "value"),
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_conversation_intent_top_snapshot",
        "Cursor top conversation intents (org; daily level)",
        _BASE + ("date", "organization", "team_name", "intent"),
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_user_conversation_intent_top_snapshot",
        "Cursor top conversation intents by user (daily level)",
        _BASE + ("date", "organization", "team_name", "email", "intent"),
        "snapshot",
    ),
    _SeriesSpec(
        "cursor_directory_group_member_added_total",
        "Cursor directory group member added (bucket delta)",
        _BASE + ("date", "organization", "team_name", "group_id", "group_name"),
        "delta",
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
        from sim.cursor.usage_v2.scales import cursor_scale_amount, cursor_should_emit_series

        spec = _SPEC_BY_NAME.get(name)
        if spec is None or spec.kind != "delta":
            raise ValueError(f"unknown delta metric: {name}")
        missing = [k for k in spec.labelnames if k not in labels]
        if missing:
            raise ValueError(f"{name} missing labels: {missing}")
        key = (name, _label_key({k: labels[k] for k in spec.labelnames}))
        if not cursor_should_emit_series(name, key):
            return
        amount = cursor_scale_amount(name, amount)
        if amount == 0:
            return
        with self._lock:
            self._deltas[key] += float(amount)

    def set_snapshot(self, name: str, labels: dict[str, str], value: float) -> None:
        from sim.cursor.usage_v2.scales import cursor_scale_amount, cursor_should_emit_series

        spec = _SPEC_BY_NAME.get(name)
        if spec is None or spec.kind != "snapshot":
            raise ValueError(f"unknown snapshot metric: {name}")
        missing = [k for k in spec.labelnames if k not in labels]
        if missing:
            raise ValueError(f"{name} missing labels: {missing}")
        key = (name, _label_key({k: labels[k] for k in spec.labelnames}))
        if not cursor_should_emit_series(name, key):
            return
        value = cursor_scale_amount(name, value)
        with self._lock:
            self._snapshots[key] = float(value)

    def accrue_snapshot(self, name: str, labels: dict[str, str], amount: float) -> None:
        """Accumulate into a date-labeled daily level (FE realDateLabeled + last_over_time)."""
        if amount == 0:
            return
        from sim.cursor.usage_v2.scales import cursor_scale_amount, cursor_should_emit_series

        spec = _SPEC_BY_NAME.get(name)
        if spec is None or spec.kind != "snapshot":
            raise ValueError(f"unknown snapshot metric for accrue: {name}")
        missing = [k for k in spec.labelnames if k not in labels]
        if missing:
            raise ValueError(f"{name} missing labels: {missing}")
        key = (name, _label_key({k: labels[k] for k in spec.labelnames}))
        if not cursor_should_emit_series(name, key):
            return
        amount = cursor_scale_amount(name, amount)
        if amount == 0:
            return
        with self._lock:
            self._snapshots[key] = float(self._snapshots.get(key, 0.0)) + float(amount)

    def prune_snapshots_by_date(self, *, keep_dates: set[str]) -> None:
        """Drop in-memory snapshot series whose ``date`` label is outside keep_dates (OOM guard)."""
        with self._lock:
            drop: list[tuple[str, tuple[tuple[str, str], ...]]] = []
            for key in self._snapshots:
                label_map = dict(key[1])
                d = label_map.get("date")
                if d is not None and d not in keep_dates:
                    drop.append(key)
            for key in drop:
                del self._snapshots[key]

    def clear_snapshots_with_prefix(self, name: str) -> None:
        with self._lock:
            drop = [k for k in self._snapshots if k[0] == name]
            for k in drop:
                del self._snapshots[k]

    def collect(self) -> Iterable[GaugeMetricFamily]:
        from sim.cursor.usage_v2.sample_window import cursor_in_sample_window

        # Brief §8: ~2 samples/series/day (c4c). Outside sample windows, expose nothing
        # so 60s scrapes do not restate long-lived gauges ~1440×/day.
        if not cursor_in_sample_window():
            with self._lock:
                # Keep accruing deltas in memory; do not clear until a sample window.
                pass
            return []

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
