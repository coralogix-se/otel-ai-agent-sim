"""Claude Code roster alignment with Anthropic Admin Claude Products users."""

from __future__ import annotations

import pytest

from sim.anthropic_admin.constants import default_roster_rows


@pytest.fixture(autouse=True)
def _align_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIM_CLAUDE_ALIGN_PRODUCTS_ROSTER", "true")
    monkeypatch.setenv("SIM_PRODUCTS_ROSTER_SIZE", "24")
    monkeypatch.setenv("SIM_ANTHROPIC_ADMIN_USERS", "24")


def test_products_roster_matches_anthropic_admin_emails() -> None:
    from sim.common.identity import _CORALOGIX_TEAM_USERS, products_roster_users

    admin = default_roster_rows(24)
    claude = products_roster_users()
    assert len(claude) == 24
    for i in range(24):
        assert claude[i]["user.email"] == admin[i]["user.email"]
        assert claude[i]["user.email"] == _CORALOGIX_TEAM_USERS[i]["user.email"]


def test_claude_code_roster_indices_limited_to_products_roster() -> None:
    from sim.common.identity import products_roster_indices, roster_indices_for_agent

    assert roster_indices_for_agent("claude_code") == products_roster_indices()
    assert roster_indices_for_agent("claude_code") == tuple(range(24))


def test_other_agents_not_limited_to_products_roster() -> None:
    from sim.common.identity import roster_indices_for_agent

    gemini = roster_indices_for_agent("gemini_cli")
    claude = roster_indices_for_agent("claude_code")
    assert len(gemini) > len(claude)
