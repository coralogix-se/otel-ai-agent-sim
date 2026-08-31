from __future__ import annotations

from datetime import datetime, timezone

from prometheus_client import CollectorRegistry, generate_latest

from sim.cursor.usage_v2.collector import register_cursor_usage_metrics
from sim.cursor.usage_v2.constants import DEFAULT_CX_APPLICATION, DEFAULT_TEAM_ID
from sim.cursor.usage_v2.runtime import emit_cursor_usage_cycle, reset_cursor_usage_runtime_for_tests


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
        for line in _metric_lines(payload, "cursor_events_total")
    )
    assert any(
        'date="2026-08-28"' in line
        for line in _metric_lines(payload, "cursor_event_cost_usd")
    )
    assert any(
        'date="2026-08-28"' in line
        for line in _metric_lines(payload, "cursor_member_daily_spend_usd")
    )
    assert any(
        'commit_source="ide"' in line
        for line in _metric_lines(payload, "cursor_ai_code_lines_total")
    )
    # Probabilistic MCP / file-line series — force a second dense cycle and check if present.
    emit_cursor_usage_cycle(now=datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc))
    # Don't scrape yet; accumulate then scrape once.
    from prometheus_client import generate_latest as _gl

    payload2 = _gl(registry)
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

    # No new emit: deltas should be gone; roster snapshots remain.
    second = generate_latest(registry)
    names2 = _series_names(second)
    assert "cursor_events_total" not in names2
    assert "cursor_member_info" in names2
    assert "cursor_billing_cycle_start_seconds" in names2


def test_usage_metrics_gated_off_by_default(monkeypatch) -> None:
    monkeypatch.delenv("SIM_CURSOR_USAGE_METRICS_ENABLED", raising=False)
    from sim.cursor.usage_v2 import usage_metrics_enabled

    assert usage_metrics_enabled() is False
    monkeypatch.setenv("SIM_CURSOR_USAGE_METRICS_ENABLED", "true")
    assert usage_metrics_enabled() is True
