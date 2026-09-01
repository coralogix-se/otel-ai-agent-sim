from __future__ import annotations

from datetime import datetime, timezone

from prometheus_client import CollectorRegistry, generate_latest

from sim.cursor.usage_v2.collector import register_cursor_usage_metrics
from sim.cursor.usage_v2.constants import (
    CURSOR_CONVERSATION_DIMENSIONS,
    DEFAULT_CX_APPLICATION,
    DEFAULT_TEAM_ID,
)
from sim.cursor.usage_v2.runtime import (
    emit_cursor_usage_cycle,
    reset_cursor_usage_runtime_for_tests,
    _roster,
)


def _users_by_surface(payload: bytes) -> dict[str, set[str]]:
    by_surface: dict[str, set[str]] = {}
    for line in _metric_lines(payload, "cursor_requests_total"):
        if 'surface="' not in line:
            continue
        surface = line.split('surface="', 1)[1].split('"', 1)[0]
        email = line.split('email="', 1)[1].split('"', 1)[0]
        by_surface.setdefault(surface, set()).add(email)
    return by_surface


def _series_names(text: bytes) -> set[str]:
    names: set[str] = set()
    for line in text.decode().splitlines():
        if not line or line.startswith("#"):
            continue
        names.add(line.split("{", 1)[0].split(" ", 1)[0])
    return names


def _metric_lines(text: bytes, name: str) -> list[str]:
    prefix = f"{name}{{"
    return [line for line in text.decode().splitlines() if line.startswith(prefix)]


def test_emit_cycle_exposes_p0_cursor_usage_gauges(monkeypatch) -> None:
    reset_cursor_usage_runtime_for_tests()
    monkeypatch.setenv("SIM_CURSOR_USAGE_ROSTER_SIZE", "8")
    monkeypatch.setenv("SIM_CURSOR_USAGE_EMITS_PER_CYCLE", "12")
    monkeypatch.setenv("SIM_CURSOR_USAGE_VOLUME", "1")

    registry = CollectorRegistry()
    register_cursor_usage_metrics(registry)
    emit_cursor_usage_cycle(now=datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc))
    payload = generate_latest(registry)
    names = _series_names(payload)

    for required in (
        "cursor_events_total",
        "cursor_event_cost_usd",
        "cursor_event_tokens_total",
        "cursor_event_request_units_total",
        "cursor_member_info",
        "cursor_member_active",
        "cursor_member_daily_spend_usd",
        "cursor_member_spend_gross_usd",
        "cursor_member_monthly_limit_usd",
        "cursor_group_members",
        "cursor_org_team_membership_info",
        "cursor_requests_total",
        "cursor_ai_code_lines_total",
        "cursor_billing_cycle_start_seconds",
        "cursor_billing_cycle_end_seconds",
        "cursor_bugbot_repos",
        "cursor_bugbot_issues_snapshot",
        "cursor_bugbot_prs_reviewed",
        "cursor_bugbot_pr_reviews_total",
        "cursor_bugbot_issues_total",
    ):
        assert required in names, required

    body = payload.decode()
    assert f'cx_application_name="{DEFAULT_CX_APPLICATION}"' in body
    assert f'team_id="{DEFAULT_TEAM_ID}"' in body
    assert 'cx_subsystem_name="Admin APIs"' in body
    assert any(
        'email="' in line
        and 'conversation_id="' in line
        and 'model="' in line
        and 'date="2026-08-28"' in line
        and 'service_account="' in line
        for line in _metric_lines(payload, "cursor_events_total")
    )
    assert any(
        'service_account="' in line and 'date="2026-08-28"' in line
        for line in _metric_lines(payload, "cursor_event_cost_usd")
    )
    assert any(
        'role="' in line for line in _metric_lines(payload, "cursor_member_monthly_limit_usd")
    )
    assert any(
        'enabled="true"' in line and 'manual_only="false"' in line
        for line in _metric_lines(payload, "cursor_bugbot_repos")
    )
    assert any(
        'severity="' in line and 'state="' in line
        for line in _metric_lines(payload, "cursor_bugbot_issues_total")
    )
    assert CURSOR_CONVERSATION_DIMENSIONS["intents"] == (
        "Ask",
        "Plan",
        "Task Automation",
        "Write Code",
    )
    assert CURSOR_CONVERSATION_DIMENSIONS["workTypes"] == ("bug", "ktlo", "new_feature")
    assert any(
        'dimension="intents"' in line
        and (
            'value="Ask"' in line
            or 'value="Plan"' in line
            or 'value="Task Automation"' in line
            or 'value="Write Code"' in line
        )
        for line in _metric_lines(payload, "cursor_conversation_total")
    )

    # Probabilistic MCP / file-line series — force a second dense cycle and check if present.
    emit_cursor_usage_cycle(now=datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc))
    payload2 = generate_latest(registry)
    mcp_lines = _metric_lines(payload2, "cursor_user_mcp_usage_total")
    if mcp_lines:
        assert any('tool_name="' in line and 'mcp_server_name="' in line for line in mcp_lines)
    file_lines = _metric_lines(payload2, "cursor_ai_change_file_lines_added_total")
    if file_lines:
        assert any('model="' in line and 'change_source="' in line for line in file_lines)
    assert any(
        line.startswith("cursor_member_info{") and 'role="' in line and 'is_removed="false"' in line
        for line in body.splitlines()
    )


def test_limits_and_overage_share(monkeypatch) -> None:
    reset_cursor_usage_runtime_for_tests()
    monkeypatch.setenv("SIM_CURSOR_USAGE_ROSTER_SIZE", "20")
    monkeypatch.setenv("SIM_CURSOR_USAGE_EMITS_PER_CYCLE", "40")

    registry = CollectorRegistry()
    register_cursor_usage_metrics(registry)
    now = datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc)
    for _ in range(30):
        emit_cursor_usage_cycle(now=now)
    # Keep latest snapshots without clearing via an unused scrape mid-loop.
    payload = generate_latest(registry)

    limits = []
    for line in _metric_lines(payload, "cursor_member_monthly_limit_usd"):
        val = float(line.rsplit(" ", 1)[-1])
        limits.append(val)
        assert 500.0 <= val <= 1700.0

    assert limits
    overages = []
    for line in _metric_lines(payload, "cursor_member_spend_overage_usd"):
        overages.append(float(line.rsplit(" ", 1)[-1]))
    assert overages
    over_count = sum(1 for v in overages if v > 0)
    # ~10% of roster (2 of 20) should exceed; allow 1..4 after many emits.
    assert 1 <= over_count <= 4, over_count
    roster = _roster()
    assert sum(1 for m in roster if m.may_exceed_limit) == 2


def test_deltas_clear_on_scrape_snapshots_restated(monkeypatch) -> None:
    reset_cursor_usage_runtime_for_tests()
    monkeypatch.setenv("SIM_CURSOR_USAGE_ROSTER_SIZE", "6")
    monkeypatch.setenv("SIM_CURSOR_USAGE_EMITS_PER_CYCLE", "8")

    registry = CollectorRegistry()
    register_cursor_usage_metrics(registry)
    now = datetime(2026, 8, 28, 19, 0, tzinfo=timezone.utc)
    emit_cursor_usage_cycle(now=now)

    first = generate_latest(registry)
    assert "cursor_events_total" in _series_names(first)
    assert "cursor_member_info" in _series_names(first)
    assert "cursor_bugbot_repos" in _series_names(first)

    # No new emit: deltas should be gone; roster snapshots remain.
    second = generate_latest(registry)
    names2 = _series_names(second)
    assert "cursor_events_total" not in names2
    assert "cursor_member_info" in names2
    assert "cursor_billing_cycle_start_seconds" in names2
    assert "cursor_bugbot_repos" in names2


def test_usage_metrics_gated_off_by_default(monkeypatch) -> None:
    monkeypatch.delenv("SIM_CURSOR_USAGE_METRICS_ENABLED", raising=False)
    from sim.cursor.usage_v2 import usage_metrics_enabled

    assert usage_metrics_enabled() is False
    monkeypatch.setenv("SIM_CURSOR_USAGE_METRICS_ENABLED", "true")
    assert usage_metrics_enabled() is True


def test_idle_seats_and_surface_user_diversity(monkeypatch) -> None:
    reset_cursor_usage_runtime_for_tests()
    monkeypatch.setenv("SIM_CURSOR_USAGE_ROSTER_SIZE", "24")
    monkeypatch.setenv("SIM_CURSOR_USAGE_IDLE_SEATS", "2")
    monkeypatch.setenv("SIM_CURSOR_USAGE_EMITS_PER_CYCLE", "24")
    monkeypatch.setenv("SIM_CURSOR_USAGE_VOLUME", "1.5")

    registry = CollectorRegistry()
    register_cursor_usage_metrics(registry)
    now = datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc)
    for _ in range(8):
        emit_cursor_usage_cycle(now=now)
    payload = generate_latest(registry)

    roster = _roster()
    idle = [m for m in roster if m.is_idle]
    assert len(idle) == 2
    idle_emails = {m.email for m in idle}

    active_lines = _metric_lines(payload, "cursor_member_active")
    active_emails = {line.split('email="', 1)[1].split('"', 1)[0] for line in active_lines}
    assert idle_emails.isdisjoint(active_emails)

    request_emails = {
        line.split('email="', 1)[1].split('"', 1)[0]
        for line in _metric_lines(payload, "cursor_requests_total")
    }
    assert idle_emails.isdisjoint(request_emails)

    by_surface = _users_by_surface(payload)
    chart_counts = {s: len(by_surface.get(s, set())) for s in ("agent", "chat", "composer", "cmdk", "bugbot")}
    assert chart_counts["agent"] > chart_counts["composer"] > chart_counts["chat"]
    assert chart_counts["cmdk"] < chart_counts["chat"]
    assert chart_counts["bugbot"] < chart_counts["cmdk"]
    assert len(set(chart_counts.values())) > 1


def test_client_versions_mostly_latest_two_stale(monkeypatch) -> None:
    reset_cursor_usage_runtime_for_tests()
    monkeypatch.setenv("SIM_CURSOR_USAGE_ROSTER_SIZE", "24")
    monkeypatch.setenv("SIM_CURSOR_USAGE_IDLE_SEATS", "2")
    monkeypatch.setenv("SIM_CURSOR_USAGE_STALE_CLIENT_SEATS", "2")

    roster = _roster()
    active = [m for m in roster if not m.is_idle]
    stale = [m for m in active if m.client_version in ("0.48.2", "0.47.8")]
    latest = [m for m in active if m.client_version == "0.50.5"]

    assert len(stale) == 2
    assert len(latest) >= len(active) - 4  # allow ~2 on 0.49.6
