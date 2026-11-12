from __future__ import annotations

from prometheus_client import CollectorRegistry

from sim.chatgpt_enterprise.runtime import ChatGPTEnterpriseSim
from sim.codex_compliance.runtime import CodexComplianceSim
from sim.common.openai_compliance import build_seats, build_workspaces, shared_roster_seed


def test_build_seats_varies_last_names_for_small_roster() -> None:
    workspaces = build_workspaces(1, seed="roster-test")
    seats = build_seats(workspaces, users_per_workspace=16, seed="roster-test")
    lasts = {seat.name.rsplit(" ", 1)[-1] for seat in seats}
    assert len(lasts) > 1, lasts
    assert "Nguyen" in lasts
    assert lasts != {"Nguyen"}


def test_chatgpt_and_codex_share_one_workspace_and_users() -> None:
    chatgpt = ChatGPTEnterpriseSim(registry=CollectorRegistry())
    codex = CodexComplianceSim(registry=CollectorRegistry())
    assert shared_roster_seed(chatgpt.organization) == shared_roster_seed(codex.organization)
    assert [w.workspace_id for w in chatgpt.workspaces] == [
        w.workspace_id for w in codex.workspaces
    ]
    assert {(s.user_id, s.email, s.name, s.workspace_id) for s in chatgpt.seats} == {
        (s.user_id, s.email, s.name, s.workspace_id) for s in codex.seats
    }
