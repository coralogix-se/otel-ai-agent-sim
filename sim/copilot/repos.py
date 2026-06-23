"""Copilot CLI git context on ``invoke_agent`` spans (``github.copilot.git.*``)."""

from __future__ import annotations

import hashlib
import random

from sim.common.env import _env_bool
from sim.common.repos import sim_org_repos, sim_session_repository_names

_COPILOT_GIT_BRANCHES: tuple[str, ...] = (
    "main",
    "master",
    "develop",
    "release/1.2",
    "feature/agent-hooks",
)


def copilot_git_otel_attrs_from_repo_short(repo_short: str) -> dict[str, str]:
    """
    VS Code Copilot ``github.copilot.git.*`` span tags for cx498 repo dashboards.

    ``github.copilot.git.repository`` is ``org/repo`` (not a remote URL).
    ``github.copilot.github.org`` is set for managed GitHub org remotes only.
    """
    if not repo_short or repo_short == "unknown":
        return {}

    rng = random.Random(hashlib.sha256(f"copilot:git:{repo_short}".encode()).digest())
    branch = rng.choice(_COPILOT_GIT_BRANCHES)
    commit = hashlib.sha256(f"copilot:commit:{repo_short}".encode()).hexdigest()[:40]
    attrs: dict[str, str] = {
        "github.copilot.git.branch": branch,
        "github.copilot.git.commit_sha": commit,
        "github.copilot.agent.type": "builtin",
    }
    if "/" in repo_short:
        org, repo = repo_short.split("/", 1)
        attrs["github.copilot.git.repository"] = f"{org}/{repo}"
        if repo_short in sim_org_repos():
            attrs["github.copilot.github.org"] = org
    else:
        attrs["github.copilot.git.repository"] = repo_short
    return attrs


def _partition_turns_across_repos(n_turns: int, n_repos: int) -> list[int]:
    """Split chat turns across repos (at least one turn per repo when possible)."""
    n_repos = max(1, n_repos)
    n_turns = max(1, n_turns)
    if n_repos == 1:
        return [n_turns]
    if n_turns <= n_repos:
        return [1 if i < n_turns else 0 for i in range(n_repos)]
    parts = [1] * n_repos
    for _ in range(n_turns - n_repos):
        parts[random.randint(0, n_repos - 1)] += 1
    return parts


def copilot_session_git_repo_segments(
    session_id: str,
    roster_user: dict | None,
    n_turns: int,
) -> list[tuple[str, dict[str, str], int]]:
    """
    Per-repo ``invoke_agent`` segments for cx498 ``sessionRepoUserInfo``.

    The dashboard groups ``invoke_agent`` spans by ``gen_ai.conversation.id`` and
    ``github.copilot.git.repository``; multiple repos in one session require multiple
    ``invoke_agent`` spans sharing the same conversation id.
    """
    if not _env_bool("SIM_COPILOT_GIT_SPAN_ATTRS", True):
        return [("", {}, max(1, n_turns))]

    repo_names = sim_session_repository_names(
        session_id,
        roster_user,
        agent_product="copilot_cli",
    )
    if not repo_names:
        return [("", {}, max(1, n_turns))]

    if not _env_bool("SIM_COPILOT_MULTI_REPO_SPANS", True) or len(repo_names) == 1:
        return [(repo_names[0], copilot_git_otel_attrs_from_repo_short(repo_names[0]), max(1, n_turns))]

    turn_parts = _partition_turns_across_repos(max(1, n_turns), len(repo_names))
    segments: list[tuple[str, dict[str, str], int]] = []
    for repo_name, seg_turns in zip(repo_names, turn_parts):
        if seg_turns <= 0:
            continue
        segments.append((repo_name, copilot_git_otel_attrs_from_repo_short(repo_name), seg_turns))
    if segments:
        return segments
    return [(repo_names[0], copilot_git_otel_attrs_from_repo_short(repo_names[0]), max(1, n_turns))]


def copilot_primary_session_git_attrs(
    session_id: str,
    roster_user: dict | None,
) -> dict[str, str]:
    """Primary repo git context for a single Copilot ``invoke_agent`` span."""
    segments = copilot_session_git_repo_segments(
        session_id,
        roster_user,
        n_turns=1,
    )
    return segments[0][1] if segments else {}
