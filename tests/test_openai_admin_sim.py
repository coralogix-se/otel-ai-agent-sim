from __future__ import annotations

from datetime import datetime, timezone

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
