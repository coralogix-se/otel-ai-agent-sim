"""Claude Code repo / token insight shaping (docs/insights.txt)."""

from __future__ import annotations

import pytest

try:
    from sim.common.identity import _CORALOGIX_TEAM_USERS
    from sim.common.repos import (
        is_sim_multi_org_user,
        sim_heavy_session_token_multiplier,
        sim_org_repos,
        sim_session_repository_names,
    )
except ImportError as exc:
    pytest.skip(f"sim runtime deps unavailable: {exc}", allow_module_level=True)


@pytest.fixture(autouse=True)
def _clear_insight_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "SIM_CLAUDE_MULTI_ORG_USER_INDICES",
        "SIM_CLAUDE_HEAVY_SESSION_USER_INDICES",
        "SIM_CLAUDE_HEAVY_SESSION_TOKEN_MULT",
        "SIM_CLAUDE_ORG_REPOS",
    ):
        monkeypatch.delenv(key, raising=False)


def _user_at(index: int) -> dict:
    return dict(_CORALOGIX_TEAM_USERS[index])


def test_multi_org_user_emits_managed_and_unmanaged_repos() -> None:
    user = _user_at(17)
    assert is_sim_multi_org_user(user)
    repos = sim_session_repository_names(
        "session-multi-org-test",
        user,
        agent_product="claude_code",
    )
    assert len(repos) == 2
    managed = sim_org_repos()
    assert any(r in managed for r in repos)
    assert any(r not in managed for r in repos)


def test_heavy_session_multiplier_applies_to_pinned_user() -> None:
    user = _user_at(17)
    assert sim_heavy_session_token_multiplier(user) == pytest.approx(4.0)


def test_heavy_session_multiplier_respects_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIM_CLAUDE_HEAVY_SESSION_TOKEN_MULT", "5")
    user = _user_at(17)
    assert sim_heavy_session_token_multiplier(user) == pytest.approx(5.0)


def test_heavy_session_multiplier_skips_other_users() -> None:
    user = _user_at(0)
    assert sim_heavy_session_token_multiplier(user) == 1.0
