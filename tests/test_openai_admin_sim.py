from __future__ import annotations

from datetime import datetime, timezone

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from sim.openai.constants import (
    OPENAI_ADMIN_MODELS,
    USAGE_COMPLETION_FIELDS,
    USAGE_FAMILY_FIELDS,
    default_api_keys,
    default_projects,
)
from sim.openai.runtime import OpenAIAdminSim


def _series_names(text: bytes) -> set[str]:
    names: set[str] = set()
    for line in text.decode().splitlines():
        if not line or line.startswith("#"):
            continue
        names.add(line.split("{", 1)[0].split(" ", 1)[0])
    return names


def test_projects_and_keys_have_unique_names() -> None:
    projects = default_projects(10)
    names = [p["name"] for p in projects]
    assert len(names) == len(set(names))
    assert sum(1 for p in projects if p["status"] == "archived") == 2
    keys = default_api_keys(projects, 22)
    key_names = [k["name"] for k in keys]
    assert len(key_names) == len(set(key_names))


def test_emit_cycle_creates_api_platform_gauges() -> None:
    registry = CollectorRegistry()
    sim = OpenAIAdminSim(registry=registry)
    assert sim.models == OPENAI_ADMIN_MODELS
    sim.emit_cycle(now=datetime(2026, 9, 30, 18, 0, tzinfo=timezone.utc))
    payload = generate_latest(registry)
    names = _series_names(payload)
    body = payload.decode()

    assert "openai_administration_usage_input_tokens" in names
    assert "openai_administration_cost_usd" in names
    assert "openai_administration_project_info" in names
    assert "openai_administration_org_mtls_enabled" in names
    assert "openai_administration_rate_limit_configured" in names
    assert "openai_administration_model_permissions_configured" in names
    assert "openai_administration_data_retention_info" in names
    assert "openai_administration_hosted_tool_enabled" in names
    for field in USAGE_COMPLETION_FIELDS:
        assert f"openai_administration_usage_{field}" in names

    assert 'date="2026-09-30"' in body
    assert 'model="gpt-5.5"' in body
    assert 'line_item="gpt-5.5, input"' in body
    assert 'cx_application_name="OpenAI"' in body
    assert f'organization="{sim.organization}"' in body
    assert 'organization_name="' in body
    assert 'api_key_id="key_' in body
    assert 'user_email="' in body
    assert 'owner_role="' in body
    assert 'scope="org"' in body
    assert 'tool="web_search"' in body
    # 28d backfill includes prior dates.
    assert 'date="2026-09-29"' in body
    assert 'date="2026-09-16"' in body


def test_usage_families_can_emit() -> None:
    registry = CollectorRegistry()
    sim = OpenAIAdminSim(registry=registry)
    sim.emit_cycle(now=datetime(2026, 9, 30, 18, 0, tzinfo=timezone.utc))
    names = _series_names(generate_latest(registry))
    assert any(f"openai_administration_usage_{f}" in names for f in USAGE_FAMILY_FIELDS)


def test_all_platform_feature_families_nonzero_today() -> None:
    """Platform Features widgets query every family; none should be missing/zero."""
    registry = CollectorRegistry()
    sim = OpenAIAdminSim(registry=registry)
    now = datetime(2026, 10, 7, 15, 0, tzinfo=timezone.utc)
    sim.emit_cycle(now=now)
    body = generate_latest(registry).decode()
    for field in USAGE_FAMILY_FIELDS:
        total = 0.0
        prefix = f"openai_administration_usage_{field}{{"
        for line in body.splitlines():
            if line.startswith(prefix) and 'date="2026-10-07"' in line:
                total += float(line.rsplit(" ", 1)[-1])
        assert total > 0, f"{field} total={total}"


def test_daily_backfill_and_cost_scale() -> None:
    registry = CollectorRegistry()
    sim = OpenAIAdminSim(registry=registry)
    now = datetime(2026, 9, 30, 18, 0, tzinfo=timezone.utc)
    sim.emit_cycle(now=now)
    assert len(sim._closed_days) >= 28
    # Weekday today cost should land in hundreds of dollars, not cents.
    body = generate_latest(registry).decode()
    total = 0.0
    for line in body.splitlines():
        if line.startswith("openai_administration_cost_usd{") and 'date="2026-09-30"' in line:
            total += float(line.rsplit(" ", 1)[-1])
    assert 400.0 < total < 2500.0


def _today_cost_usd(registry: CollectorRegistry, day: str) -> float:
    total = 0.0
    for line in generate_latest(registry).decode().splitlines():
        if line.startswith("openai_administration_cost_usd{") and f'date="{day}"' in line:
            total += float(line.rsplit(" ", 1)[-1])
    return total


def test_history_prune_keeps_only_today_gauges(monkeypatch: pytest.MonkeyPatch) -> None:
    """After grace, non-today date series must leave the scrape (UI last_over_time[1d])."""
    monkeypatch.setenv("SIM_OPENAI_ADMIN_HISTORY_SCRAPE_GRACE_SEC", "0")
    monkeypatch.setenv("SIM_OPENAI_ADMIN_BACKFILL_DAYS", "14")
    registry = CollectorRegistry()
    sim = OpenAIAdminSim(registry=registry)
    now = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
    sim.emit_cycle(now=now)
    body = generate_latest(registry).decode()
    assert 'date="2026-10-07"' in body
    assert 'date="2026-10-06"' not in body
    assert 'openai_administration_cost_usd{' in body


def test_rematerialize_today_does_not_stack_cost() -> None:
    """Regression: force-rematerializing today must .set() stable series, not inflate."""
    registry = CollectorRegistry()
    sim = OpenAIAdminSim(registry=registry)
    now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    sim.emit_cycle(now=now)
    first = _today_cost_usd(registry, "2026-10-06")
    assert 400.0 < first < 2500.0
    for minute in range(1, 15):
        sim.emit_cycle(now=now.replace(minute=minute))
    again = _today_cost_usd(registry, "2026-10-06")
    assert abs(again - first) < 1.0, (first, again)
    # Week of date-labeled gauges stays well under Claude-scale (~$50K+/wk).
    week = 0.0
    body = generate_latest(registry).decode()
    for line in body.splitlines():
        if not line.startswith("openai_administration_cost_usd{"):
            continue
        week += float(line.rsplit(" ", 1)[-1])
    assert week < 80_000.0, week
