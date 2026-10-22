"""Copilot collector counters/gauges restart from zero each UTC day (one sample = one day's value)."""

from __future__ import annotations

from sim.copilot.collector_metrics import GitHubCopilotCollector


def test_counters_and_gauges_reset_on_utc_day_rollover() -> None:
    collector = GitHubCopilotCollector()
    labels = {"organization": "o"}
    collector._inc_counter("github_copilot_org_cli_request_count", labels, 5)
    collector._set_gauge("github_copilot_org_daily_active_users", labels, 9.0)

    collector._billing_day = "1999-01-01"
    collector._inc_counter("github_copilot_org_cli_request_count", labels, 1)

    assert list(collector._counters.values()) == [1.0]
    assert collector._gauges == {}
