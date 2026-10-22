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


def test_models_primary_plus_optional_haiku() -> None:
    models = se.claude_session_models("model-sess")
    assert 1 <= len(models) <= 2
    assert models[1:] in ([], ["claude-haiku-4-5"])


def test_emit_cycles_give_six_turns_then_reap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drive the app's slot loop: one turn per live session per cycle, end at 6, series gone after the TTL."""
    app = pytest.importorskip("app")
    from types import SimpleNamespace

    from prometheus_client import CollectorRegistry, Counter

    for key, value in {
        "SIM_CLAUDE_ALIGN_PRODUCTS_ROSTER": "true",
        "SIM_PRODUCTS_ROSTER_SIZE": "24",
        "SIM_CLAUDE_OTEL_ONLY_ROSTER_SIZE": "76",
        "SIM_CLAUDE_LONG_SESSION_SEC_MIN": "300",
        "SIM_CLAUDE_LONG_SESSION_SEC_MAX": "420",
        "SIM_CLAUDE_SESSION_ID_ROTATE_SEC_MIN": "3600",
        "SIM_CLAUDE_SESSION_ID_ROTATE_SEC_MAX": "3600",
        "SIM_CLAUDE_SESSION_PROMPTS_MIN": "6",
        "SIM_CLAUDE_SESSION_PROMPTS_MAX": "6",
        "SIM_CLAUDE_SESSION_TURNS_EXPECTED": "6",
        "SIM_CLAUDE_ACTIVITY_PACING": "true",
        "SIM_CLAUDE_DAY_VARIATION": "false",
        "SIM_CLAUDE_HOURLY_SHAPE": "false",
        "SIM_CLAUDE_DAILY_SESSIONS": "2100",
        "SIM_CLAUDE_TOP_SPENDER_INDICES": "17,14,3",
        "TRACE_INTERVAL_SEC": "60",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(app, "_cc_live_slots", set())
    monkeypatch.setattr(app, "_cc_slot_users", [])
    monkeypatch.setattr(app, "_cc_user_session_ids", {})
    monkeypatch.setattr(app, "_cc_start_carry", 0.0)
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(se, "time", SimpleNamespace(monotonic=lambda: clock.now))
    metric = Counter("sim_test_turns", "turns", ["session_id"], registry=CollectorRegistry())

    turns: dict[str, int] = {}
    last_cycle: dict[str, int] = {}
    for cycle in range(20):
        clock.now = cycle * 60.0
        se.claude_reap_idle_sessions()
        live_before = {sid for sid, n in turns.items() if n < 6}
        started = 0
        emitted = set()
        for user in app._claude_roster_users_for_claude_code_emit():
            sid = app._claude_session_id_for_roster_user(user)
            started += sid not in turns
            se.claude_session_note_turn(sid)
            metric.labels(session_id=sid).inc()
            se.claude_session_track_series(sid, metric, {"session_id": sid})
            app._claude_note_session_user_prompt(user)
            turns[sid] = turns.get(sid, 0) + 1
            last_cycle[sid] = cycle
            emitted.add(sid)
        # 2100 sessions / 1440 cycles ≈ 1.46 starts per cycle → 8–9 live after warm-up.
        assert started <= 2
        if cycle >= 6:
            assert 8 <= len(emitted) <= 9
        assert live_before <= emitted
        exported = {sample.labels["session_id"] for sample in metric.collect()[0].samples}
        reaped = {sid for sid, c in last_cycle.items() if (cycle - c) * 60 >= 150}
        assert not exported & reaped
        assert exported >= emitted

    assert max(turns.values()) == 6
    assert sum(n == 6 for n in turns.values()) >= 18


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
