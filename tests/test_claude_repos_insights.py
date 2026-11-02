"""Claude Code repo / token insight shaping (docs/insights.txt)."""

from __future__ import annotations

import pytest

try:
    from sim.common.identity import _CORALOGIX_TEAM_USERS
    from sim.common.repos import (
        is_sim_multi_org_user,
        is_sim_rogue_user,
        is_sim_top_spender,
        sim_heavy_session_token_multiplier,
        sim_org_repos,
        sim_session_repository_names,
        sim_top_spender_rank,
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
        "SIM_CLAUDE_TOP_SPENDER_INDICES",
        "SIM_CLAUDE_ROGUE_USER_INDICES",
        "SIM_CLAUDE_ROGUE_UNMANAGED_FRAC",
        "SIM_CLAUDE_ROGUE_MANAGED_FRAC",
    ):
        monkeypatch.delenv(key, raising=False)


def _user_at(index: int) -> dict:
    return dict(_CORALOGIX_TEAM_USERS[index])


def test_multi_org_user_emits_managed_and_unmanaged_repos() -> None:
    user = _user_at(14)
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


def test_repo_pools_are_eight_weighted_names() -> None:
    """Repository Token Distribution: ~8 managed + ~8 unmanaged repos, top managed ≈30% of managed picks."""
    from collections import Counter

    managed = sim_org_repos()
    counts: Counter[str] = Counter()
    for i in range(4000):
        counts.update(sim_session_repository_names(f"session-dist-{i}", _user_at(0), n_repos=1))
    managed_counts = {r: n for r, n in counts.items() if r in managed}
    unmanaged_counts = {r: n for r, n in counts.items() if r not in managed}
    assert len(managed_counts) == 8
    assert len(unmanaged_counts) == 8
    top_repo, top_n = max(managed_counts.items(), key=lambda kv: kv[1])
    assert top_repo == "coralogix/cxai-observability-demo-playground"
    assert 0.26 < top_n / sum(managed_counts.values()) < 0.34


def test_top_spender_rank_order() -> None:
    assert sim_top_spender_rank(_user_at(17)) == 1
    assert sim_top_spender_rank(_user_at(14)) == 2
    assert sim_top_spender_rank(_user_at(3)) == 3
    assert is_sim_top_spender(_user_at(17))
    assert not is_sim_top_spender(_user_at(0))


def test_highest_spender_is_unmanaged_rogue_not_multi_org() -> None:
    user = _user_at(17)
    assert is_sim_rogue_user(user)
    assert not is_sim_multi_org_user(user)
    managed = sim_org_repos()
    unmanaged_hits = 0
    for i in range(40):
        repos = sim_session_repository_names(
            f"session-rogue-{i}",
            user,
            agent_product="claude_code",
            n_repos=1,
        )
        assert len(repos) == 1
        if repos[0] not in managed:
            unmanaged_hits += 1
    assert unmanaged_hits >= 30


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


def test_session_budgets_sum_to_daily_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    """One simulated day (whales picked 4× as often, like the slot sampler) lands on the daily knobs."""
    import random

    from sim.claude import session_emit as se
    from sim.claude import spend_pacing

    for key, value in {
        "SIM_CLAUDE_ALIGN_PRODUCTS_ROSTER": "true",
        "SIM_PRODUCTS_ROSTER_SIZE": "24",
        "SIM_CLAUDE_OTEL_ONLY_ROSTER_SIZE": "76",
        "SIM_CLAUDE_DAILY_SESSIONS": "600",
        "SIM_CLAUDE_DAILY_COST_USD": "4700",
        "SIM_CLAUDE_DAILY_TOKENS": "270000000",
        "SIM_CLAUDE_TOP_SPENDER_DAILY_USD": "650",
        "SIM_DAY_VARIATION": "false",
    }.items():
        monkeypatch.setenv(key, value)
    progress = {"n": 0}
    monkeypatch.setattr(spend_pacing, "_elapsed_day_fraction", lambda: max(0.02, progress["n"] / 600))
    monkeypatch.setattr(spend_pacing, "_accrued_usd", {})
    monkeypatch.setattr(se, "_SESSION_STATE", {})
    monkeypatch.setattr(se, "_TOKEN_HEAVY_DAY", ("", 0))
    random.seed(7)

    cost_by_user: dict[int, float] = {}
    tokens: dict[str, int] = {}
    weights = [4.0 if i in (17, 14, 3) else 1.0 for i in range(100)]
    for n in range(600):
        progress["n"] = n
        user_idx = random.choices(range(100), weights=weights, k=1)[0]
        plan = se.claude_session_plan(f"day-{n}", _user_at(user_idx))
        cost_by_user[user_idx] = cost_by_user.get(user_idx, 0.0) + plan.cost_usd
        for typ, amount in plan.tokens.items():
            tokens[typ] = tokens.get(typ, 0) + amount

    total_cost = sum(cost_by_user.values())
    assert total_cost == pytest.approx(4700, rel=0.15)
    assert sum(tokens.values()) == pytest.approx(270_000_000, rel=0.15)
    cache_hit = tokens["cacheRead"] / (tokens["input"] + tokens["cacheRead"] + tokens["cacheCreation"])
    assert 0.8 < cache_hit < 0.9
    avg_user_cost = total_cost / len(cost_by_user)
    for whale in (17, 14, 3):
        assert cost_by_user[whale] >= 12 * avg_user_cost


def test_rank_one_whale_starts_two_token_heavy_sessions_per_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """heavySessions: 2 sessions/day ≥ 5M tokens and ≥ 10× a ~1.3M session average."""
    from sim.claude import session_emit as se

    monkeypatch.setattr(se, "_SESSION_STATE", {})
    monkeypatch.setattr(se, "_TOKEN_HEAVY_DAY", ("", 0))
    monkeypatch.setenv("SIM_CLAUDE_HEAVY_SESSION_FRAC", "0")
    assert sum(se.claude_session_plan("avery-0", _user_at(14)).tokens.values()) < 5_000_000
    heavy = [sum(se.claude_session_plan(f"sam-{i}", _user_at(17)).tokens.values()) for i in range(4)]
    assert all(tokens >= 13_000_000 for tokens in heavy[:2])
    assert all(tokens < 5_000_000 for tokens in heavy[2:])
