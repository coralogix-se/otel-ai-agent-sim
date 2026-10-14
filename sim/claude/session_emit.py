"""Per-session emit policy for Claude Code (onlineboutique brief §6)."""

from __future__ import annotations

import hashlib
import os
import random
from dataclasses import dataclass, field

from sim.common.env import _env_bool, _env_float, _env_int

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

_LANGS_EXPANDED: tuple[str, ...] = (
    "TypeScript",
    "Python",
    "JavaScript",
    "Go",
    "Rust",
    "Markdown",
    "YAML",
    "JSON",
    "Java",
    "Kotlin",
    "Swift",
    "Ruby",
    "PHP",
    "C",
    "C++",
    "C#",
    "Scala",
    "Haskell",
    "Elixir",
    "Shell",
    "SQL",
    "HCL",
    "Protobuf",
    "Toml",
    "HTML",
)


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


_SESSION_STATE: dict[str, _SessionEmitState] = {}
_SESSION_STATE_MAX = 12_000


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


def claude_edit_languages() -> tuple[str, ...]:
    return _parse_csv_env("SIM_CLAUDE_EDIT_LANGUAGES", _LANGS_EXPANDED)


def _get_state(session_id: str) -> _SessionEmitState:
    sid = session_id.strip() or "unknown-session"
    got = _SESSION_STATE.get(sid)
    if got is not None:
        return got
    if len(_SESSION_STATE) > _SESSION_STATE_MAX:
        # Drop an arbitrary half to bound memory (sessions are short-lived).
        for i, k in enumerate(list(_SESSION_STATE.keys())):
            if i % 2 == 0:
                _SESSION_STATE.pop(k, None)
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
    # 2–3 models per session.
    n_models = random.randint(2, 3)
    st.models_used = [_pick_model() for _ in range(n_models)]
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
    return models[turn_index % len(models)]


def claude_should_inc_session_count(session_id: str) -> bool:
    """True once per session_id (session_count value exactly 1)."""
    st = _get_state(session_id)
    if st.session_counted:
        return False
    st.session_counted = True
    return True


def claude_session_should_emit_edit(session_id: str) -> bool:
    return bool(_get_state(session_id).emit_edit)


def claude_session_should_emit_loc(session_id: str) -> bool:
    return bool(_get_state(session_id).emit_loc)


def claude_session_should_emit_repo(session_id: str) -> bool:
    return bool(_get_state(session_id).emit_repo)


def claude_session_should_emit_commit(session_id: str) -> bool:
    return bool(_get_state(session_id).emit_commit)


def claude_session_should_emit_pr(session_id: str) -> bool:
    return bool(_get_state(session_id).emit_pr)


def claude_session_note_turn(session_id: str) -> int:
    st = _get_state(session_id)
    st.turns += 1
    return st.turns


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


def claude_session_token_parts(session_id: str, *, turns_expected: int = 6) -> dict[str, int]:
    """
    Per-turn token slice so the session totals ~3.7M tokens with c4c type mix:
    cacheRead 95%, cacheCreation 4%, output 0.5%, input 0.3%.
    """
    st = _get_state(session_id)
    remaining_turns = max(1, turns_expected - st.turns + 1)
    session_total = max(100_000, _env_int("SIM_CLAUDE_SESSION_TOKENS_TOTAL", 3_700_000))
    # First call: store budget on first turn via turns==0 before note.
    # Allocate evenly across expected turns with light jitter.
    turn_total = session_total / max(1, turns_expected)
    turn_total *= random.uniform(0.85, 1.15)
    # On last expected turn, don't special-case — jitter keeps totals near target.
    _ = remaining_turns
    cache_read = int(turn_total * _env_float("SIM_CLAUDE_TOKEN_TYPE_CACHE_READ_FRAC", 0.95))
    cache_creation = int(turn_total * _env_float("SIM_CLAUDE_TOKEN_TYPE_CACHE_CREATION_FRAC", 0.04))
    output = max(1, int(turn_total * _env_float("SIM_CLAUDE_TOKEN_TYPE_OUTPUT_FRAC", 0.005)))
    inp = max(1, int(turn_total * _env_float("SIM_CLAUDE_TOKEN_TYPE_INPUT_FRAC", 0.003)))
    return {
        "input": inp,
        "output": output,
        "cacheRead": cache_read,
        "cacheCreation": cache_creation,
    }


def claude_session_cost_usd_target() -> float:
    """Target ~$2.2 per session split across models/turns."""
    return max(0.1, _env_float("SIM_CLAUDE_SESSION_COST_USD", 2.2))


def claude_duplicate_token_metric_enabled() -> bool:
    """Broken ``claude_code_token_usage__token__`` twin — off by default (brief §6 Delete)."""
    return _env_bool("SIM_CLAUDE_EMIT_DUPLICATE_TOKEN_METRIC", False)
