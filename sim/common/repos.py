"""Shared session repository pools for Claude Code and Copilot CLI simulators."""

from __future__ import annotations

import hashlib
import os
import random

from sim.common.env import _env_float, _env_int
from sim.common.identity import _CORALOGIX_TEAM_USERS

# Fictional company GitHub repos (managed — in the org scan). Plausible shape only; not real repos.
# Includes names seen in Copilot CLI telemetry (``ai-agent-instrumentation``, ``cx-web-workspace``).
# Override with ``SIM_CLAUDE_ORG_REPOS`` (shared env name kept for compatibility).
_DEFAULT_ORG_REPOS: tuple[str, ...] = (
    "coralogix/ai-agent-instrumentation",
    "coralogix/cx-web-workspace",
    "coralogix/platform-web-workspace",
    "coralogix/identity-token-service",
    "coralogix/agent-telemetry-hooks",
    "coralogix/analytics-query-engine",
    "coralogix/deploy-pipeline-handler",
    "coralogix/microshop-demo",
)

# Fictional external / personal repos (unmanaged — not in org scan). Override with ``SIM_CLAUDE_UNMANAGED_REPOS``.
_UNMANAGED_REPOS: tuple[str, ...] = (
    "jchen/dotfiles",
    "devkits/super-cli",
    "metrics/collector-core",
    "observability/trace-profiler",
    "labs/multi-agent-runner",
)

# Roster indices pinned as “rogue” users: heavy unmanaged-repo use + elevated spend (stable across runs).
_DEFAULT_ROGUE_USER_INDICES: tuple[int, ...] = (17, 42, 88)


def _parse_csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    parts = tuple(p.strip() for p in raw.split(",") if p.strip())
    return parts if parts else default


def sim_org_repos() -> frozenset[str]:
    """Managed repository names (full ``org/repo`` and bare repo names for dashboard matching)."""
    repos = _parse_csv("SIM_CLAUDE_ORG_REPOS", _DEFAULT_ORG_REPOS)
    expanded: set[str] = set()
    for r in repos:
        expanded.add(r)
        if "/" in r:
            expanded.add(r.split("/", 1)[1])
    return frozenset(expanded)


# Back-compat alias.
claude_org_repos = sim_org_repos


def sim_rogue_user_roster_indices() -> frozenset[int]:
    raw = os.environ.get("SIM_CLAUDE_ROGUE_USER_INDICES", "").strip()
    if not raw:
        raw = os.environ.get("SIM_CLAUDE_UNMANAGED_POWER_USER_INDICES", "").strip()
    if not raw:
        return frozenset(_DEFAULT_ROGUE_USER_INDICES)
    out: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return frozenset(out) if out else frozenset(_DEFAULT_ROGUE_USER_INDICES)


claude_rogue_user_roster_indices = sim_rogue_user_roster_indices


def sim_rogue_user_emails() -> frozenset[str]:
    indices = sim_rogue_user_roster_indices()
    return frozenset(
        _CORALOGIX_TEAM_USERS[i]["user.email"]
        for i in indices
        if 0 <= i < len(_CORALOGIX_TEAM_USERS)
    )


claude_rogue_user_emails = sim_rogue_user_emails


def is_sim_rogue_user(roster_user: dict | None) -> bool:
    """True for users skewed toward unmanaged repos and high cost (Claude + Copilot)."""
    if roster_user is None:
        return False
    return str(roster_user.get("user.email", "")) in sim_rogue_user_emails()


is_claude_rogue_user = is_sim_rogue_user
is_claude_unmanaged_power_user = is_sim_rogue_user


def sim_rogue_user_token_multiplier(roster_user: dict | None) -> float:
    """Token/cost scale for rogue users so they rank among top spenders on cost panels."""
    if not is_sim_rogue_user(roster_user):
        return 1.0
    lo = _env_float("SIM_CLAUDE_ROGUE_USER_TOKEN_MULT_MIN", 4.0)
    hi = max(lo, _env_float("SIM_CLAUDE_ROGUE_USER_TOKEN_MULT_MAX", 8.0))
    email = str(roster_user.get("user.email", "")) if roster_user else ""
    rng = random.Random(hashlib.sha256(f"cc:rogue:mult:{email}".encode()).digest())
    return rng.uniform(lo, hi)


claude_rogue_user_token_multiplier = sim_rogue_user_token_multiplier


def _repo_class_weights(rogue: bool) -> tuple[tuple[str, float], ...]:
    if rogue:
        return (
            ("managed", _env_float("SIM_CLAUDE_ROGUE_MANAGED_FRAC", 0.08)),
            ("unmanaged", _env_float("SIM_CLAUDE_ROGUE_UNMANAGED_FRAC", 0.85)),
            ("unknown", _env_float("SIM_CLAUDE_ROGUE_UNKNOWN_FRAC", 0.07)),
        )
    return (
        ("managed", _env_float("SIM_CLAUDE_MANAGED_REPO_FRAC", 0.85)),
        ("unmanaged", _env_float("SIM_CLAUDE_UNMANAGED_REPO_FRAC", 0.05)),
        ("unknown", _env_float("SIM_CLAUDE_UNKNOWN_REPO_FRAC", 0.10)),
    )


def _pick_repo_name(repo_class: str, rng: random.Random) -> str:
    if repo_class == "unknown":
        return "unknown"
    if repo_class == "managed":
        return rng.choice(_parse_csv("SIM_CLAUDE_ORG_REPOS", _DEFAULT_ORG_REPOS))
    return rng.choice(_parse_csv("SIM_CLAUDE_UNMANAGED_REPOS", _UNMANAGED_REPOS))


def sim_session_repository_names(
    session_id: str,
    roster_user: dict | None,
    *,
    n_repos: int | None = None,
) -> list[str]:
    """
    Repository names for session repo Prometheus gauges and Copilot git span tags.

    Mix: managed (org GitHub), unmanaged (external), unknown (literal ``unknown``).
    Rogue roster users skew heavily toward unmanaged repos; everyone else is ~85% managed.
    """
    rogue = is_sim_rogue_user(roster_user)
    if n_repos is None:
        lo = _env_int("SIM_CLAUDE_REPOS_PER_SESSION_MIN", 1)
        hi = max(lo, _env_int("SIM_CLAUDE_REPOS_PER_SESSION_MAX", 2))
        if rogue:
            lo = max(lo, _env_int("SIM_CLAUDE_ROGUE_REPOS_PER_SESSION_MIN", 1))
            hi = max(hi, _env_int("SIM_CLAUDE_ROGUE_REPOS_PER_SESSION_MAX", 3))
        n_repos = random.randint(lo, hi)

    weights = _repo_class_weights(rogue)
    labels, probs = zip(*weights)
    s = sum(probs)
    norm = [p / s for p in probs] if s > 0 else [1 / len(labels)] * len(labels)

    rng = random.Random(hashlib.sha256(f"cc:repos:{session_id}".encode()).digest())
    names: list[str] = []
    for _ in range(max(1, n_repos)):
        cls = rng.choices(labels, weights=norm, k=1)[0]
        name = _pick_repo_name(cls, rng)
        if name not in names:
            names.append(name)
    return names or [_pick_repo_name("managed", rng)]


claude_session_repository_names = sim_session_repository_names
