from __future__ import annotations

import json
from datetime import datetime, timezone

from prometheus_client import CollectorRegistry, generate_latest

from sim.anthropic_admin.constants import TOKEN_TYPES, default_organization_id
from sim.anthropic_admin.runtime import (
    ANALYTICS_COST_LABELS,
    ANALYTICS_TOKEN_LABELS,
    AnthropicAdminSim,
    ORG_REQUEST_LABELS,
    USAGE_LABELS,
    USER_COST_LABELS,
    USER_CHAT_LABELS,
    USER_PRODUCT_LABELS,
    USER_REQUEST_LABELS,
    USER_SESSION_LABELS,
    USER_TOKEN_LABELS,
    USER_TOOL_LABELS,
)


def _series_names(text: bytes) -> set[str]:
    names: set[str] = set()
    for line in text.decode().splitlines():
        if not line or line.startswith("#"):
            continue
        names.add(line.split("{", 1)[0].split(" ", 1)[0])
    return names


def test_emit_cycle_creates_dashboard_gauge_series() -> None:
    registry = CollectorRegistry()
    sim = AnthropicAdminSim(registry=registry, logger=None, emits_per_cycle=16)
    sim.emit_cycle(now=datetime(2026, 8, 17, 18, 0, tzinfo=timezone.utc))
    payload = generate_latest(registry)
    names = _series_names(payload)
    # Gauges must keep these exact names (Counters would append _total and miss the dashboard).
    assert "anthropic_admin_api_key_usage" in names
    assert "anthropic_admin_api_key_usage_total" not in names
    assert "anthropic_admin_rate_limit_value" in names
    assert "anthropic_admin_cost_amount" in names
    assert "anthropic_analytics_user_cost" in names
    assert "anthropic_analytics_user_tokens" in names
    assert "anthropic_analytics_user_requests" in names
    assert "anthropic_analytics_tokens" in names
    assert "anthropic_analytics_cost" in names
    assert "anthropic_org_requests_total" in names
    assert "anthropic_org_cache_creation" in names
    assert "anthropic_analytics_seats_assigned" in names
    assert "anthropic_analytics_active_users" in names
    assert "anthropic_analytics_adoption_rate" in names
    assert "anthropic_analytics_pending_invites" in names
    assert "anthropic_analytics_user_sessions" in names
    assert "anthropic_analytics_user_chat_activity" in names
    assert "anthropic_analytics_user_commits" in names
    assert "anthropic_analytics_user_lines_added" in names
    assert "anthropic_analytics_user_lines_removed" in names
    assert "anthropic_analytics_user_pull_requests" in names
    assert "anthropic_analytics_skill_users" in names
    assert "anthropic_analytics_skill_invocations" in names
    assert "anthropic_analytics_connector_users" in names
    assert "anthropic_analytics_connector_calls" in names
    assert "anthropic_compliance_org_users_total" in names
    assert "anthropic_compliance_activity_events_total" in names
    body = payload.decode()
    assert 'source="admin"' in body
    assert 'source="analytics"' in body
    assert 'source="compliance"' in body
    assert 'amount_type="actual"' in body
    assert 'product="claude_code"' in body
    assert "group" in USER_COST_LABELS
    assert "group" in USER_TOKEN_LABELS
    assert "group" in USER_REQUEST_LABELS
    assert "group" in USER_SESSION_LABELS
    assert "group" in USER_CHAT_LABELS
    assert "group" in USER_PRODUCT_LABELS
    assert "group" in USER_TOOL_LABELS
    assert "model" in USER_SESSION_LABELS
    assert "group" in ANALYTICS_COST_LABELS
    assert "group" in ANALYTICS_TOKEN_LABELS
    assert "group" in ORG_REQUEST_LABELS
    assert any(
        line.startswith("anthropic_analytics_cost{") and 'group="' in line
        for line in body.splitlines()
    )
    assert any(
        line.startswith("anthropic_analytics_user_sessions{")
        and 'group="' in line
        and 'model="' in line
        and 'user_email="' in line
        and 'product="' in line
        for line in body.splitlines()
    )
    assert any(
        line.startswith("anthropic_analytics_user_chat_activity{") and 'group="' in line
        for line in body.splitlines()
    )
    assert any(
        line.startswith("anthropic_analytics_user_skills_used{") and 'group="' in line
        for line in body.splitlines()
    )
    assert any(
        line.startswith("anthropic_analytics_user_tool_decisions{") and 'group="' in line
        for line in body.splitlines()
    )
    assert any(
        line.startswith("anthropic_analytics_skill_sessions{") and 'group="' in line
        for line in body.splitlines()
    )
    assert any(
        line.startswith("anthropic_analytics_connector_sessions{") and 'group="' in line
        for line in body.splitlines()
    )
    assert any(
        line.startswith("anthropic_org_requests_total{") and 'group="' in line
        for line in body.splitlines()
    )
    assert 'token_type="uncached_input_tokens"' in body
    assert 'token_type="output_tokens"' in body
    assert 'token_type="cache_read_input_tokens"' in body
    assert 'token_type="cache_creation_input_tokens"' in body
    assert any(
        line.startswith("anthropic_analytics_user_tokens{") and 'group="' in line
        for line in body.splitlines()
    )
    assert any(
        line.startswith("anthropic_analytics_user_requests{") and 'group="' in line
        for line in body.splitlines()
    )
    assert any(
        line.startswith("anthropic_analytics_user_tokens{") and 'token_type="output_tokens"' in line
        for line in body.splitlines()
    )
    assert 'window="daily"' in body
    assert 'cx_application_name="Claude"' in body
    assert 'cx_subsystem_name="Enterprise API"' in body
    assert f'organization="{default_organization_id()}"' in body
    for token_type in TOKEN_TYPES:
        assert f'token_type="{token_type}"' in body
    for role in ("primary_owner", "owner", "membership_admin", "user"):
        assert f'role="{role}"' in body
    assert "api_key_id" not in USER_COST_LABELS


def test_analytics_list_cost_above_actual() -> None:
    """Users table ``costList`` reads ``amount_type=\"list\"``; org token cost must not mirror actual."""
    from sim.anthropic_admin.constants import LIST_PRICE_FACTOR

    registry = CollectorRegistry()
    sim = AnthropicAdminSim(registry=registry, logger=None, emits_per_cycle=32)
    sim.emit_cycle(now=datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc))
    body = generate_latest(registry).decode()
    org_actual = org_list = user_actual = user_list = 0.0
    for line in body.splitlines():
        if line.startswith("#"):
            continue
        if line.startswith("anthropic_analytics_cost{"):
            val = float(line.rsplit(" ", 1)[-1])
            if 'amount_type="actual"' in line:
                org_actual += val
            elif 'amount_type="list"' in line:
                org_list += val
        if line.startswith("anthropic_analytics_user_cost{"):
            val = float(line.rsplit(" ", 1)[-1])
            if 'amount_type="actual"' in line:
                user_actual += val
            elif 'amount_type="list"' in line:
                user_list += val
    assert org_actual > 0 and org_list > org_actual
    assert user_actual > 0 and user_list > user_actual
    assert abs(org_list / org_actual - LIST_PRICE_FACTOR) < 0.05
    assert abs(user_list / user_actual - LIST_PRICE_FACTOR) < 0.05


def test_usage_label_set_matches_live_admin_series() -> None:
    assert USAGE_LABELS == (
        "cx_application_name",
        "cx_subsystem_name",
        "organization",
        "source",
        "model",
        "api_key_id",
        "context_window",
        "token_type",
    )


def test_cardinality_stays_bounded_across_cycles() -> None:
    registry = CollectorRegistry()
    sim = AnthropicAdminSim(registry=registry, logger=None, emits_per_cycle=8)
    for i in range(12):
        sim.emit_cycle(now=datetime(2026, 8, 17, 18, i, tzinfo=timezone.utc))
    lines = [
        ln
        for ln in generate_latest(registry).decode().splitlines()
        if ln and not ln.startswith("#")
    ]
    # Seeded usage (6 keys × 6 models × 6 token types) plus Claude Products analytics.
    assert len(lines) < 5000


class _CaptureLogger:
    def __init__(self) -> None:
        self.records: list[object] = []
        self.resource = None

    def emit(self, record: object) -> None:
        self.records.append(record)


def test_usage_logs_use_integration_stream() -> None:
    registry = CollectorRegistry()
    logger = _CaptureLogger()
    sim = AnthropicAdminSim(registry=registry, logger=logger, emits_per_cycle=1)
    sim.emit_cycle(now=datetime(2026, 8, 17, 18, 0, tzinfo=timezone.utc))
    bodies = []
    for rec in logger.records:
        raw = getattr(rec, "body", "")
        bodies.append(json.loads(raw) if isinstance(raw, str) else raw)
    streams = {row.get("stream") for row in bodies if isinstance(row, dict)}
    assert "anthropic.api_keys_usage" in streams
    assert "anthropic.activity" in streams
    assert "anthropic.summary.active_users" in streams
    assert "anthropic.user_activity" in streams
    assert "anthropic.user_cost" in streams
    assert "anthropic.skills" in streams
    assert "anthropic.connectors" in streams
    usage = next(row for row in bodies if row.get("stream") == "anthropic.api_keys_usage")
    data = usage["data"]
    assert "api_key_id" in data
    assert "uncached_input_tokens" in data
    assert "web_search_requests" in data
    summary = next(row for row in bodies if row.get("stream") == "anthropic.summary.active_users")
    summary_data = summary["data"]
    assert str(summary_data.get("organization_id", "")).startswith("org_")
    assert "assigned_seat_count" in summary_data
    assert "claude_code_daily_active_users" in summary_data
    activity = next(row for row in bodies if row.get("stream") == "anthropic.user_activity")
    assert "user_email" in activity["data"]
    assert "distinct_session_count" in activity["data"]
