"""Claude Code per-session emit policy (onlineboutique brief §6)."""

from __future__ import annotations

import pytest

from sim.claude import session_emit as se


@pytest.fixture(autouse=True)
def _clear_session_state() -> None:
    se._SESSION_STATE.clear()
    yield
    se._SESSION_STATE.clear()


def test_session_count_once() -> None:
    sid = "sess-count-1"
    assert se.claude_should_inc_session_count(sid) is True
    assert se.claude_should_inc_session_count(sid) is False
    assert se.claude_should_inc_session_count(sid) is False


def test_team_subsystem_stable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIM_CLAUDE_TEAM_SUBSYSTEMS", "true")
    monkeypatch.setenv(
        "SIM_CLAUDE_CX_SUBSYSTEM_TEAMS",
        "team1,team2,team3,team4,team5,enterprise",
    )
    user = {"user.email": "sam.martinez@coralogix.com"}
    a = se.claude_cx_subsystem_for_user(user, "s1")
    b = se.claude_cx_subsystem_for_user(user, "s2")
    assert a == b
    assert a in {"team1", "team2", "team3", "team4", "team5", "enterprise"}


def test_token_parts_mix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIM_CLAUDE_SESSION_TOKENS_TOTAL", "3700000")
    monkeypatch.setenv("SIM_CLAUDE_SESSION_TURNS_EXPECTED", "6")
    parts = se.claude_session_token_parts("tok-mix", turns_expected=6)
    total = sum(parts.values())
    assert parts["cacheRead"] > parts["cacheCreation"] > parts["output"]
    assert parts["cacheRead"] / total > 0.9


def test_models_two_or_three_per_session() -> None:
    models = se.claude_session_models("model-sess")
    assert 2 <= len(models) <= 3


def test_duplicate_token_metric_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SIM_CLAUDE_EMIT_DUPLICATE_TOKEN_METRIC", raising=False)
    assert se.claude_duplicate_token_metric_enabled() is False


def test_unmanaged_repos_cardinality_in_source() -> None:
    """Brief §6: ~200 repository_name values (avoid importing repos→otel in this env)."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "sim" / "common" / "repos.py"
    text = src.read_text()
    assert "_UNMANAGED_REPOS: tuple[str, ...] = tuple(" in text
    assert ")[:200]" in text
