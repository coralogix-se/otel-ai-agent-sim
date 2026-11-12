from __future__ import annotations

from datetime import datetime, timezone

from prometheus_client import CollectorRegistry, generate_latest

from sim.codex_compliance.runtime import CodexComplianceSim


def _series_names(text: bytes) -> set[str]:
    names: set[str] = set()
    for line in text.decode().splitlines():
        if not line or line.startswith("#"):
            continue
        names.add(line.split("{", 1)[0].split(" ", 1)[0])
    return names


def test_codex_compliance_emit_cycle_creates_required_families() -> None:
    registry = CollectorRegistry()
    sim = CodexComplianceSim(registry=registry)
    sim.emit_cycle(now=datetime(2026, 11, 8, 18, 0, tzinfo=timezone.utc))
    payload = generate_latest(registry)
    names = _series_names(payload)
    required = {
        "openai_compliance_events_total",
        "openai_compliance_codex_environments_total",
        "openai_compliance_remote_control_devices_by_user",
        "openai_compliance_fanout_sweep_users",
        "openai_compliance_source_ips_by_user",
        "openai_compliance_access_geo_total",
        "openai_compliance_user_state",
        "openai_compliance_user_identity",
        "openai_compliance_log_feed_lag_seconds",
        "openai_analytics_codex_credits",
        "openai_analytics_codex_cost_usd",
        "openai_analytics_codex_threads",
        "openai_analytics_codex_client_credits",
        "openai_analytics_codex_client_uncached_text_input_tokens",
        "openai_analytics_codex_client_cached_text_input_tokens",
        "openai_analytics_codex_client_text_output_tokens",
        "openai_analytics_codex_pr_reviews",
        "openai_compliance_codex_tokens_total",
        "openai_compliance_codex_session_tokens_total",
        "openai_compliance_codex_session_turns_total",
        "openai_compliance_codex_tool_calls_total",
        "codex_session_repo_info",
        "codex_turn_e2e_duration_ms_count",
    }
    missing = required - names
    assert not missing, missing
    body = payload.decode()
    assert 'event_type="CODEX_LOG"' in body
    assert 'event_type="CODEX_SECURITY_LOG"' in body
    assert 'sub_event_type="PROPOSED_PATCH_PR_CREATED"' in body
    assert 'share_settings="organization"' in body or 'share_settings="workspace"' in body
    assert 'family="remote_control_clients"' in body
    assert 'client_id="CODEX_CLI"' in body
    assert "openai_analytics_codex_client_credits{" in body
