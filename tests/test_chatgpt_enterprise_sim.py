from __future__ import annotations

from datetime import datetime, timezone

from prometheus_client import CollectorRegistry, generate_latest

from sim.chatgpt_enterprise.runtime import ChatGPTEnterpriseSim


def _series_names(text: bytes) -> set[str]:
    names: set[str] = set()
    for line in text.decode().splitlines():
        if not line or line.startswith("#"):
            continue
        names.add(line.split("{", 1)[0].split(" ", 1)[0])
    return names


def test_chatgpt_enterprise_emit_cycle_covers_dashboard_and_posture() -> None:
    registry = CollectorRegistry()
    sim = ChatGPTEnterpriseSim(registry=registry)
    sim.emit_cycle(now=datetime(2026, 11, 8, 18, 0, tzinfo=timezone.utc))
    payload = generate_latest(registry)
    names = _series_names(payload)
    required = {
        # ChatGPT dashboard
        "openai_compliance_costs_credits",
        "openai_compliance_costs_usd",
        "openai_compliance_costs_tokens",
        "openai_compliance_events_total",
        "openai_compliance_connector_calls_total",
        "openai_compliance_user_state",
        "openai_compliance_invites_total",
        "openai_compliance_user_identity",
        "openai_compliance_gpts_total",
        "openai_compliance_projects_total",
        "openai_compliance_plugins_total",
        "openai_compliance_agents_total",
        "openai_compliance_skills_total",
        "openai_compliance_users_total",
        "openai_compliance_log_feed_lag_seconds",
        # Compliance posture
        "openai_compliance_automations_total",
        "openai_compliance_library_files_by_user",
        "openai_compliance_library_bytes_by_user",
        "openai_compliance_recordings_total",
        "openai_compliance_memories_users_total",
        "openai_compliance_remote_control_devices_by_user",
        "openai_compliance_remote_control_clients_total",
        "openai_compliance_remote_control_environments_total",
        "openai_compliance_fanout_sweep_users",
        "openai_administration_admin_key_info",
        "openai_administration_project_api_key_info",
    }
    missing = required - names
    assert not missing, missing
    body = payload.decode()
    assert 'event_type="CONVERSATION_MESSAGE"' in body
    assert 'product="ChatGPT"' in body
    assert 'product="Work"' in body
    assert 'product="codex"' not in body
    assert 'family="automations"' in body
    assert 'user_approvals_mode="never"' in body
