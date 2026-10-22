"""Per-session emit policy for Claude Code (onlineboutique brief §6)."""

from __future__ import annotations

import hashlib
import os
import random
import time
from dataclasses import dataclass, field

from sim.claude.day_variation import (
    claude_daily_multiplier,
    claude_daily_sessions,
    claude_session_turns,
    claude_whale_session_weight,
)
from sim.common.env import _env_bool, _env_float, _env_int

_HAIKU = "claude-haiku-4-5"

# c4c-like model mix (brief §6). Weights sum to 100.
_MODEL_MIX: tuple[tuple[str, int], ...] = (
    ("claude-opus-5-5", 55),
    ("claude-fable-5-1", 19),
    ("claude-opus-5-5[1m]", 7),
    ("claude-sonnet-5-5", 6),
    ("claude-sonnet-5", 4),
    ("claude-haiku-4-5", 4),
    ("claude-opus-5", 3),
    ("claude-fable-5", 2),
)

_DEFAULT_TEAM_SUBSYSTEMS: tuple[str, ...] = (
    "team1",
    "team2",
    "team3",
    "team4",
    "team5",
    "enterprise",
)

_DEFAULT_ORG_IDS: tuple[str, ...] = (
    "org_coralogix_lab_001",
    "org_coralogix_lab_002",
    "org_coralogix_eng_west",
    "org_coralogix_eng_eu",
    "org_coralogix_platform",
    "org_coralogix_data",
    "org_coralogix_security",
)

_DEFAULT_TERMINAL_TYPES: tuple[str, ...] = (
    "vscode",
    "alacritty",
    "iterm2",
    "ghostty",
    "warp",
    "kitty",
    "wezterm",
    "tmux",
    "gnome-terminal",
    "konsole",
    "hyper",
    "terminus",
    "windows-terminal",
    "jetbrains",
    "cursor",
    "zed",
    "emacs",
    "nvim-term",
    "ssh",
    "codespaces",
)

# ~35 service / app versions (brief §6).
_DEFAULT_SERVICE_VERSIONS: tuple[str, ...] = tuple(
    f"1.{major}.{minor}"
    for major in range(0, 5)
    for minor in range(0, 7)
)[:35]


@dataclass
class _SessionEmitState:
    session_counted: bool = False
    models_used: list[str] = field(default_factory=list)
    emit_edit: bool | None = None
    emit_loc: bool | None = None
    emit_repo: bool | None = None
    emit_commit: bool | None = None
    emit_pr: bool | None = None
    active_cli_budget_s: float = 0.0
    active_user_budget_s: float = 0.0
    active_cli_emitted_s: float = 0.0
    active_user_emitted_s: float = 0.0
    turns: int = 0
    last_emit_s: float = field(default_factory=time.monotonic)
    # (metric, label values) children this session created, removed by ``claude_reap_idle_sessions``.
    series: set = field(default_factory=set)
    # Session totals, drawn on the first turn and spread evenly across turns.
    cost_usd: float | None = None
    tokens: dict[str, int] = field(default_factory=dict)
    user_prompts: int = 0
    loc_added: int = 0
    loc_removed: int = 0
    commits: int = 0
    prs: int = 0
    edit_decisions: dict[tuple[str, str], int] = field(default_factory=dict)


_SESSION_STATE: dict[str, _SessionEmitState] = {}

# Org-wide cacheRead / (input + cacheRead + cacheCreation) ≈ 85%, under the 90% cacheInefficiency floor.
_TOKEN_TYPE_FRACS: tuple[tuple[str, float], ...] = (
    ("input", 0.035),
    ("output", 0.025),
    ("cacheRead", 0.83),
    ("cacheCreation", 0.11),
)
_EDIT_TOOLS: tuple[str, ...] = ("Edit", "Write", "NotebookEdit")


def _parse_csv_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    parts = tuple(p.strip() for p in raw.split(",") if p.strip())
    return parts if parts else default


def _stable_pick(pool: tuple[str, ...], key: str) -> str:
    if not pool:
        return ""
    digest = hashlib.sha256(key.encode()).hexdigest()
    return pool[int(digest[:8], 16) % len(pool)]


def claude_team_subsystems_enabled() -> bool:
    return _env_bool("SIM_CLAUDE_TEAM_SUBSYSTEMS", True)


def claude_cx_subsystem_for_user(roster_user: dict | None, session_id: str) -> str | None:
    """
    When team subsystems are enabled, return one of team1–team5 / enterprise.
    Otherwise return None (caller keeps flat/dotted subsystem).
    """
    if not claude_team_subsystems_enabled():
        return None
    teams = _parse_csv_env("SIM_CLAUDE_CX_SUBSYSTEM_TEAMS", _DEFAULT_TEAM_SUBSYSTEMS)
    email = ""
    if roster_user is not None:
        email = str(roster_user.get("user.email", "") or "").strip()
    key = email or session_id.strip() or "unknown"
    return _stable_pick(teams, f"cx-sub:{key}")


def claude_organization_id_for_user(roster_user: dict | None, session_id: str, *, flavor: str) -> str:
    orgs = _parse_csv_env("SIM_CLAUDE_ORGANIZATION_IDS", _DEFAULT_ORG_IDS)
    email = ""
    if roster_user is not None:
        email = str(roster_user.get("user.email", "") or "").strip()
    key = f"org:{flavor}:{email or session_id}"
    return _stable_pick(orgs, key)


def claude_terminal_type_for_user(roster_user: dict | None, session_id: str) -> str:
    override = os.environ.get("SIM_TERMINAL_TYPE", "").strip()
    if override:
        return override
    terms = _parse_csv_env("SIM_CLAUDE_TERMINAL_TYPES", _DEFAULT_TERMINAL_TYPES)
    email = ""
    if roster_user is not None:
        email = str(roster_user.get("user.email", "") or "").strip()
    return _stable_pick(terms, f"term:{email or session_id}")


def claude_service_version_for_user(roster_user: dict | None, session_id: str) -> str:
    override = os.environ.get("SIM_CC_APP_VERSION", "").strip()
    if override:
        return override
    vers = _parse_csv_env("SIM_CLAUDE_SERVICE_VERSIONS", _DEFAULT_SERVICE_VERSIONS)
    email = ""
    if roster_user is not None:
        email = str(roster_user.get("user.email", "") or "").strip()
    return _stable_pick(vers, f"ver:{email or session_id}")


def _get_state(session_id: str) -> _SessionEmitState:
    sid = session_id.strip() or "unknown-session"
    got = _SESSION_STATE.get(sid)
    if got is not None:
        return got
    st = _SessionEmitState()
    # Active-time budget ≈ 380s/session; cli 97% / user 3% (brief §6).
    total = max(60.0, _env_float("SIM_CLAUDE_SESSION_ACTIVE_TIME_S", 380.0))
    st.active_cli_budget_s = total * 0.97
    st.active_user_budget_s = total * 0.03
    # Per-session metric gates (stable for the session lifetime).
    st.emit_edit = random.random() < _env_float("SIM_CLAUDE_EMIT_EDIT_SESSION_FRAC", 0.18)
    st.emit_loc = random.random() < _env_float("SIM_CLAUDE_EMIT_LOC_SESSION_FRAC", 0.18)
    st.emit_repo = random.random() < _env_float("SIM_CLAUDE_EMIT_REPO_SESSION_FRAC", 0.45)
    st.emit_commit = random.random() < _env_float("SIM_CLAUDE_EMIT_COMMIT_SESSION_FRAC", 0.10)
    st.emit_pr = random.random() < _env_float("SIM_CLAUDE_EMIT_PR_SESSION_FRAC", 0.05)
    # One primary model; some sessions add Haiku for a background turn.
    st.models_used = [_pick_model()]
    if st.models_used[0] != _HAIKU and random.random() < _env_float("SIM_CLAUDE_HAIKU_SESSION_FRAC", 0.2):
        st.models_used.append(_HAIKU)
    _SESSION_STATE[sid] = st
    return st


def _pick_model() -> str:
    override = os.environ.get("SIM_CLAUDE_MODEL", "").strip()
    if override:
        return override
    weights = [w for _, w in _MODEL_MIX]
    models = [m for m, _ in _MODEL_MIX]
    return random.choices(models, weights=weights, k=1)[0]


def claude_session_models(session_id: str) -> list[str]:
    return list(_get_state(session_id).models_used)


def claude_session_primary_model(session_id: str) -> str:
    models = claude_session_models(session_id)
    return models[0] if models else _pick_model()


def claude_session_model_for_turn(session_id: str, turn_index: int) -> str:
    models = claude_session_models(session_id)
    if not models:
        return _pick_model()
    return models[1] if len(models) > 1 and turn_index == 1 else models[0]


def claude_should_inc_session_count(session_id: str) -> bool:
    """True once per session_id (session_count value exactly 1)."""
    st = _get_state(session_id)
    if st.session_counted:
        return False
    st.session_counted = True
    return True


def claude_session_should_emit_repo(session_id: str) -> bool:
    return bool(_get_state(session_id).emit_repo)


def claude_session_note_turn(session_id: str) -> int:
    st = _get_state(session_id)
    st.turns += 1
    st.last_emit_s = time.monotonic()
    return st.turns


def claude_session_track_series(session_id: str, metric, labels: dict) -> None:
    _get_state(session_id).series.add(
        (metric, tuple(str(labels[name]) for name in metric._labelnames))
    )


def claude_reap_idle_sessions(now: float | None = None) -> int:
    """
    Remove label children of sessions idle longer than ``SIM_CLAUDE_SERIES_TTL_S``.

    ``prometheus_client`` exports every ``.labels()`` child forever, so per-session series
    would pile up until a pod restart. The TTL leaves ~2 scrapes after the last turn so the
    final values land before the series stops.
    """
    now = time.monotonic() if now is None else now
    ttl = _env_float("SIM_CLAUDE_SERIES_TTL_S", 150.0)
    idle = [sid for sid, st in _SESSION_STATE.items() if now - st.last_emit_s >= ttl]
    for sid in idle:
        for metric, label_values in _SESSION_STATE.pop(sid).series:
            metric.remove(*label_values)
    return len(idle)


def claude_session_active_time_slice(session_id: str, *, turns_expected: int = 6) -> tuple[float, float]:
    """
    Split remaining active-time budget across remaining turns so the session totals ~380s.
    Returns (cli_s, user_s) for this emit.
    """
    st = _get_state(session_id)
    remaining_turns = max(1, turns_expected - st.turns + 1)
    cli_left = max(0.0, st.active_cli_budget_s - st.active_cli_emitted_s)
    user_left = max(0.0, st.active_user_budget_s - st.active_user_emitted_s)
    cli_s = cli_left / remaining_turns
    user_s = user_left / remaining_turns
    # Slight jitter without overshooting the budget.
    cli_s = min(cli_left, max(0.5, cli_s * random.uniform(0.85, 1.1)))
    user_s = min(user_left, max(0.05, user_s * random.uniform(0.85, 1.1)))
    st.active_cli_emitted_s += cli_s
    st.active_user_emitted_s += user_s
    return cli_s, user_s


def claude_daily_cost_usd() -> float:
    return max(0.0, _env_float("SIM_CLAUDE_DAILY_COST_USD", 4700.0))


def claude_daily_tokens() -> float:
    return max(0.0, _env_float("SIM_CLAUDE_DAILY_TOKENS", 270_000_000.0))


def claude_session_cost_usd(roster_user: dict | None) -> float:
    """
    Session cost so a day totals ``SIM_CLAUDE_DAILY_COST_USD``.

    Top spenders ("whales") pace toward ``SIM_CLAUDE_TOP_SPENDER_DAILY_USD`` each and some of their
    sessions are heavy ($100–150); everyone else splits the rest of the daily budget evenly.
    """
    from sim.claude.spend_pacing import (
        claude_top_spender_daily_usd_target,
        claude_user_accrued_spend_usd,
        sim_top_spender_pace_multiplier,
    )
    from sim.common.identity import claude_aligned_slot_count
    from sim.common.repos import is_sim_top_spender, sim_top_spender_roster_indices

    whale_usd = claude_top_spender_daily_usd_target()
    n_whales = len(sim_top_spender_roster_indices())
    weight = claude_whale_session_weight()
    # Session starts pick whales ``weight``× as often (see app ``_claude_roster_users_for_claude_code_emit``).
    whale_sessions = claude_daily_sessions() * weight / (
        max(1, claude_aligned_slot_count()) - n_whales + n_whales * weight
    )
    if is_sim_top_spender(roster_user):
        heavy_frac = _env_float("SIM_CLAUDE_HEAVY_SESSION_FRAC", 0.15)
        lo = _env_float("SIM_CLAUDE_HEAVY_SESSION_COST_USD_MIN", 100.0)
        hi = max(lo, _env_float("SIM_CLAUDE_HEAVY_SESSION_COST_USD_MAX", 150.0))
        # No heavy session once the whale is at its daily target, so heavies can't overshoot it.
        day_target = whale_usd * claude_daily_multiplier()
        if claude_user_accrued_spend_usd(roster_user) < day_target and random.random() < heavy_frac:
            return random.uniform(lo, hi)
        pace = sim_top_spender_pace_multiplier(roster_user)
        return whale_usd / whale_sessions * pace * random.uniform(0.85, 1.15)
    rest = max(0.0, claude_daily_cost_usd() - n_whales * whale_usd)
    other_sessions = max(1.0, claude_daily_sessions() - n_whales * whale_sessions)
    return rest / other_sessions * random.uniform(0.7, 1.3)


def claude_session_plan(session_id: str, roster_user: dict | None) -> _SessionEmitState:
    """Draw the session totals once (first turn); later turns reuse them."""
    st = _get_state(session_id)
    if st.cost_usd is not None:
        return st
    from sim.claude.spend_pacing import note_claude_user_spend_usd

    st.cost_usd = claude_session_cost_usd(roster_user)
    note_claude_user_spend_usd(roster_user, st.cost_usd)
    # Tokens track cost so the day totals both targets; cost is not tokens × list price.
    total_tokens = st.cost_usd * claude_daily_tokens() / max(0.01, claude_daily_cost_usd())
    st.tokens = {typ: max(1, int(total_tokens * frac)) for typ, frac in _TOKEN_TYPE_FRACS}
    turns = claude_session_turns()
    churn = random.random() < _env_float("SIM_CLAUDE_REPROMPT_SESSION_FRAC", 0.08)
    st.user_prompts = random.randint(10, 14) if churn else turns
    if st.emit_loc:
        loc = _env_int("SIM_CLAUDE_LOC_PER_SESSION", 260) * random.uniform(0.85, 1.15)
        st.loc_added = max(1, int(round(loc * 0.88)))
        st.loc_removed = int(round(loc * 0.12))
    if st.emit_commit:
        st.commits = 1 if random.random() < 0.8 else 2
    if st.emit_pr:
        st.prs = 1 if random.random() < 0.9 else 2
    if st.emit_edit:
        accept_rate = _env_float("SIM_TOOL_ACCEPT_RATE", 0.78)
        n_decisions = max(1, _env_int("SIM_CLAUDE_EDIT_DECISIONS_PER_SESSION", 5))
        # One or two (tool, decision) pairs per session keeps the series count bounded.
        pairs = [
            (random.choice(_EDIT_TOOLS), "accept" if random.random() < accept_rate else "reject")
            for _ in range(random.randint(1, 2))
        ]
        for i in range(n_decisions):
            pair = pairs[i % len(pairs)]
            st.edit_decisions[pair] = st.edit_decisions.get(pair, 0) + 1
    return st


def claude_turn_share(total: int, turn_idx: int, turns: int) -> int:
    """This turn's slice of a session total; slices over turns 1..turns sum to ``total``.

    The remainder lands on the last turns so a small counter (1 commit) starts at 0 and
    ``increase()`` still sees it.
    """
    if turn_idx < 1 or turn_idx > turns:
        return 0
    base, rem = divmod(int(total), turns)
    return base + (1 if turn_idx > turns - rem else 0)


def claude_duplicate_token_metric_enabled() -> bool:
    """Broken ``claude_code_token_usage__token__`` twin — off by default (brief §6 Delete)."""
    return _env_bool("SIM_CLAUDE_EMIT_DUPLICATE_TOKEN_METRIC", False)
