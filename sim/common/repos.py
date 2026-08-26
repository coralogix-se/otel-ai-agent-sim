"""Shared session repository pools for Claude Code and Copilot CLI simulators."""

from __future__ import annotations

import hashlib
import os
import random
import re

from sim.common.env import _env_float, _env_int
from sim.common.identity import _CORALOGIX_TEAM_USERS

# Managed repos: must appear in the tenant GitHub integration scan (``ScansService/GetScan``),
# not merely exist in the Coralogix GitHub org. cx498 classifies managed vs unmanaged by matching
# ``repository_name`` / ``github.copilot.git.repository`` against that scan set (full name or bare repo).
# onlineboutique-dev / obdev scan (2026-06-23 HAR): only this repo is in the scan today.
# Override with ``SIM_CLAUDE_ORG_REPOS`` (comma-separated).
_DEFAULT_ORG_REPOS: tuple[str, ...] = (
    "coralogix/cxai-observability-demo-playground",
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

# Users whose sessions always span managed + unmanaged repos (Claude ``multiOrgUser`` insight).
_DEFAULT_MULTI_ORG_USER_INDICES: tuple[int, ...] = (17, 42)

# Users with extra token volume so at least one session crosses heavy-session thresholds.
_DEFAULT_HEAVY_SESSION_USER_INDICES: tuple[int, ...] = (17,)

# One roster user per agent doing company-like work on a personal GitHub repo (policy violation).
# Indices must have ``claude_code`` / ``copilot_cli`` in ``SIM_ROSTER_AGENT_AFFINITY`` (default on).
# 27 → quinn.bernstein@coralogix.com (claude_code); 54 → quinn.bernstein2@coralogix.com (copilot_cli).
_DEFAULT_CLAUDE_PERSONAL_REPO_USER_INDEX = 27
_DEFAULT_COPILOT_PERSONAL_REPO_USER_INDEX = 54
_DEFAULT_PERSONAL_VIOLATION_REPO_NAME = "Coralogix-log-explore"


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
    lo = _env_float("SIM_CLAUDE_ROGUE_USER_TOKEN_MULT_MIN", 1.5)
    hi = max(lo, _env_float("SIM_CLAUDE_ROGUE_USER_TOKEN_MULT_MAX", 2.5))
    email = str(roster_user.get("user.email", "")) if roster_user else ""
    rng = random.Random(hashlib.sha256(f"cc:rogue:mult:{email}".encode()).digest())
    return rng.uniform(lo, hi)


claude_rogue_user_token_multiplier = sim_rogue_user_token_multiplier


def sim_multi_org_user_roster_indices() -> frozenset[int]:
    """Roster users that emit two repo owners on the same ``session_id`` (managed + unmanaged)."""
    raw = os.environ.get("SIM_CLAUDE_MULTI_ORG_USER_INDICES", "").strip()
    if not raw:
        return frozenset(_DEFAULT_MULTI_ORG_USER_INDICES)
    out: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return frozenset(out) if out else frozenset(_DEFAULT_MULTI_ORG_USER_INDICES)


def is_sim_multi_org_user(roster_user: dict | None) -> bool:
    if roster_user is None:
        return False
    idx = _roster_index_for_user(roster_user)
    return idx is not None and idx in sim_multi_org_user_roster_indices()


def sim_heavy_session_user_roster_indices() -> frozenset[int]:
    """Roster users with elevated per-session token totals (``heavySessions`` insight)."""
    raw = os.environ.get("SIM_CLAUDE_HEAVY_SESSION_USER_INDICES", "").strip()
    if not raw:
        return frozenset(_DEFAULT_HEAVY_SESSION_USER_INDICES)
    out: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return frozenset(out) if out else frozenset(_DEFAULT_HEAVY_SESSION_USER_INDICES)


def sim_heavy_session_token_multiplier(roster_user: dict | None) -> float:
    """Extra token scale for pinned users so one session exceeds 5M and 10× the daily average."""
    if roster_user is None:
        return 1.0
    idx = _roster_index_for_user(roster_user)
    if idx is None or idx not in sim_heavy_session_user_roster_indices():
        return 1.0
    return max(1.0, _env_float("SIM_CLAUDE_HEAVY_SESSION_TOKEN_MULT", 4.0))


claude_heavy_session_token_multiplier = sim_heavy_session_token_multiplier


def _roster_index_for_user(roster_user: dict | None) -> int | None:
    if roster_user is None:
        return None
    email = str(roster_user.get("user.email", ""))
    account_uuid = str(roster_user.get("user.account_uuid", ""))
    for i, user in enumerate(_CORALOGIX_TEAM_USERS):
        if user.get("user.email") == email or user.get("user.account_uuid") == account_uuid:
            return i
    return None


def sim_personal_repo_violator_roster_index(agent_product: str) -> int:
    """Stable roster index for the one user doing work on a personal repo per agent product."""
    product = agent_product.strip().lower()
    if product == "claude_code":
        return _env_int(
            "SIM_CLAUDE_PERSONAL_REPO_USER_INDEX",
            _DEFAULT_CLAUDE_PERSONAL_REPO_USER_INDEX,
        )
    if product == "copilot_cli":
        return _env_int(
            "SIM_COPILOT_PERSONAL_REPO_USER_INDEX",
            _DEFAULT_COPILOT_PERSONAL_REPO_USER_INDEX,
        )
    return -1


def is_sim_personal_repo_violator(
    roster_user: dict | None,
    *,
    agent_product: str,
) -> bool:
    """True when this user is the dedicated personal-repo policy violator for ``agent_product``."""
    idx = _roster_index_for_user(roster_user)
    if idx is None:
        return False
    return idx == sim_personal_repo_violator_roster_index(agent_product)


def sim_personal_repo_violator_emails(*, agent_product: str) -> frozenset[str]:
    idx = sim_personal_repo_violator_roster_index(agent_product)
    if 0 <= idx < len(_CORALOGIX_TEAM_USERS):
        return frozenset({_CORALOGIX_TEAM_USERS[idx]["user.email"]})
    return frozenset()


def _personal_github_username(roster_user: dict) -> str:
    """
    GitHub login similar to the Coralogix mailbox local-part but not identical.

    ``jordan.garcia4@coralogix.com`` → ``jordangarcia`` / ``jordan-garcia`` / ``jgarcia``.
    """
    email = str(roster_user.get("user.email", "") or "")
    local = email.split("@", 1)[0].strip().lower() if "@" in email else "user"
    local = re.sub(r"\d+$", "", local)
    parts = [p for p in re.split(r"[._]", local) if p]
    if len(parts) >= 2:
        first, last = parts[0], parts[-1]
    elif len(parts) == 1:
        first, last = parts[0], "dev"
    else:
        first, last = "user", "dev"
    variant = hashlib.sha256(f"personal-gh-user:{email}".encode()).digest()[0] % 3
    if variant == 0:
        return f"{first}{last}"
    if variant == 1:
        return f"{first}-{last}"
    return f"{first[0]}{last}"


def sim_personal_violation_repository(roster_user: dict) -> str:
    """Personal GitHub repo with a company-sounding name, e.g. ``jordangarcia/Coralogix-log-explore``."""
    repo_name = (
        os.environ.get("SIM_PERSONAL_VIOLATION_REPO_NAME", _DEFAULT_PERSONAL_VIOLATION_REPO_NAME).strip()
        or _DEFAULT_PERSONAL_VIOLATION_REPO_NAME
    )
    return f"{_personal_github_username(roster_user)}/{repo_name}"


def _repo_class_weights(rogue: bool) -> tuple[tuple[str, float], ...]:
    """Managed vs unmanaged only — every session gets a linkable ``org/repo`` name for repo gauges."""
    if rogue:
        return (
            ("managed", _env_float("SIM_CLAUDE_ROGUE_MANAGED_FRAC", 0.08)),
            ("unmanaged", _env_float("SIM_CLAUDE_ROGUE_UNMANAGED_FRAC", 0.92)),
        )
    return (
        ("managed", _env_float("SIM_CLAUDE_MANAGED_REPO_FRAC", 0.90)),
        ("unmanaged", _env_float("SIM_CLAUDE_UNMANAGED_REPO_FRAC", 0.10)),
    )


def _session_repo_rng(session_id: str) -> random.Random:
    key = session_id.strip() or "unknown-session"
    return random.Random(hashlib.sha256(f"cc:repos:{key}".encode()).digest())


def _pick_repo_name(repo_class: str, rng: random.Random) -> str:
    if repo_class == "managed":
        return rng.choice(_parse_csv("SIM_CLAUDE_ORG_REPOS", _DEFAULT_ORG_REPOS))
    # ``unmanaged`` and legacy ``unknown`` class both resolve to external org/repo names.
    return rng.choice(_parse_csv("SIM_CLAUDE_UNMANAGED_REPOS", _UNMANAGED_REPOS))


def sim_session_repository_names(
    session_id: str,
    roster_user: dict | None,
    *,
    n_repos: int | None = None,
    agent_product: str | None = None,
) -> list[str]:
    """
    Repository names for session repo Prometheus gauges and Copilot git span tags.

    Mix: managed (org owner repos, e.g. ``coralogix/*``) and unmanaged (external ``org/repo``).
    Every session emits at least one linkable repo name (no literal ``unknown`` gauge values).
    Rogue roster users skew heavily toward unmanaged repos; everyone else is ~90% managed.

    Repo count and names are stable for a given ``session_id`` (seeded RNG).

    When ``agent_product`` is ``claude_code`` or ``copilot_cli``, one pinned roster user
    per product uses only their personal ``<username>/Coralogix-log-explore`` repo — simulating
    company work on a home GitHub account (exclusive to that user on that agent).
    """
    if agent_product and roster_user and is_sim_personal_repo_violator(
        roster_user,
        agent_product=agent_product,
    ):
        return [sim_personal_violation_repository(roster_user)]

    rng = _session_repo_rng(session_id)
    if roster_user is not None and is_sim_multi_org_user(roster_user):
        managed = _pick_repo_name("managed", rng)
        unmanaged = _pick_repo_name("unmanaged", rng)
        if managed == unmanaged:
            unmanaged = _pick_repo_name("unmanaged", random.Random(rng.randint(0, 2**31 - 1)))
        return [managed, unmanaged]
    rogue = is_sim_rogue_user(roster_user)
    if n_repos is None:
        lo = _env_int("SIM_CLAUDE_REPOS_PER_SESSION_MIN", 1)
        hi = max(lo, _env_int("SIM_CLAUDE_REPOS_PER_SESSION_MAX", 2))
        if rogue:
            lo = max(lo, _env_int("SIM_CLAUDE_ROGUE_REPOS_PER_SESSION_MIN", 1))
            hi = max(hi, _env_int("SIM_CLAUDE_ROGUE_REPOS_PER_SESSION_MAX", 3))
        n_repos = rng.randint(lo, hi)

    weights = _repo_class_weights(rogue)
    labels, probs = zip(*weights)
    s = sum(probs)
    norm = [p / s for p in probs] if s > 0 else [1 / len(labels)] * len(labels)

    names: list[str] = []
    for _ in range(max(1, n_repos)):
        cls = rng.choices(labels, weights=norm, k=1)[0]
        name = _pick_repo_name(cls, rng)
        if name not in names:
            names.append(name)
    return names or [_pick_repo_name("managed", rng)]


claude_session_repository_names = sim_session_repository_names
