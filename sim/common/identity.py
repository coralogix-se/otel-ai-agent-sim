"""Synthetic Coralogix roster identities (shared across Gemini, Claude, Codex, generic)."""
from __future__ import annotations

import hashlib
import os
import random
import time
import uuid

from sim.claude.meta import _claude_effective_cx_subsystem, _claude_telemetry_profile
from sim.claude.user_variance import (
    claude_session_id_rotate_deadline,
    claude_slot_pin_deadline,
    claude_user_session_rotate_duration_from_env,
)
from sim.common.otel import tool_version_for, _stable_uuid
from sim.common.constants import _CLAUDE_CODE_MODELS
from sim.common.env import _env_bool, _env_float, _env_int
from sim.common.state import st

_FIRST_NAMES = (
    "Alex",
    "Sam",
    "Jordan",
    "Taylor",
    "Riley",
    "Casey",
    "Morgan",
    "Quinn",
    "Avery",
    "Skyler",
    "Reese",
    "Drew",
    "Jamie",
    "Cameron",
    "Rowan",
)

_LAST_NAMES = (
    "Nguyen",
    "Patel",
    "Garcia",
    "Okonkwo",
    "Silva",
    "Kim",
    "Cohen",
    "Hansen",
    "Iyer",
    "Martinez",
    "Lindberg",
    "Fischer",
    "Okafor",
    "Tanaka",
    "Bernstein",
)

# Fixed roster (~middle-sized org): same 225 identities for every agent sim (Claude, Gemini, Codex, …).
_CORALOGIX_TEAM_ROSTER_SIZE = 225

_CLI_AGENT_PRODUCTS = (
    "claude_code",
    "gemini_cli",
    "codex",
    "cursor",
    "copilot_cli",
)


def _roster_agent_affinity_enabled() -> bool:
    return _env_bool("SIM_ROSTER_AGENT_AFFINITY", True)


def _roster_max_agents_per_user() -> int:
    return max(1, min(len(_CLI_AGENT_PRODUCTS), _env_int("SIM_ROSTER_MAX_AGENTS_PER_USER", 2)))


def _build_roster_agent_affinity() -> tuple[frozenset[str], ...]:
    """Each roster row uses 1–N coding agents (deterministic; not all five for every user)."""
    products = list(_CLI_AGENT_PRODUCTS)
    n_products = len(products)
    max_agents = _roster_max_agents_per_user()
    out: list[frozenset[str]] = []
    for i in range(_CORALOGIX_TEAM_ROSTER_SIZE):
        digest = hashlib.sha256(f"coralogix:sim:agent-affinity:{i}".encode()).digest()
        n_pick = 1 if max_agents <= 1 else 1 + (digest[0] % max_agents)
        picked: list[str] = []
        used: set[int] = set()
        b = 1
        while len(picked) < n_pick:
            j = digest[b % len(digest)] % n_products
            b += 1
            if j in used:
                if b > len(digest) + n_products * 4:
                    break
                continue
            used.add(j)
            picked.append(products[j])
        if not picked:
            picked = [products[digest[1] % n_products]]
        out.append(frozenset(picked))
    return tuple(out)


_ROSTER_AGENT_AFFINITY: tuple[frozenset[str], ...] = _build_roster_agent_affinity()


def products_roster_size() -> int:
    """Roster rows shared with Anthropic Admin / Claude Products (``SIM_ANTHROPIC_ADMIN_USERS``)."""
    default_n = max(1, _env_int("SIM_ANTHROPIC_ADMIN_USERS", 24))
    n = max(1, _env_int("SIM_PRODUCTS_ROSTER_SIZE", default_n))
    return min(n, _CORALOGIX_TEAM_ROSTER_SIZE)


def products_roster_indices() -> tuple[int, ...]:
    return tuple(range(products_roster_size()))


def products_roster_users() -> tuple[dict[str, str], ...]:
    return tuple(dict(u) for u in _CORALOGIX_TEAM_USERS[: products_roster_size()])


def _claude_align_products_roster() -> bool:
    return _env_bool("SIM_CLAUDE_ALIGN_PRODUCTS_ROSTER", True)


def roster_indices_for_agent(agent_product: str) -> tuple[int, ...]:
    if agent_product == "claude_code" and _claude_align_products_roster():
        return products_roster_indices()
    if not _roster_agent_affinity_enabled():
        return tuple(range(len(_CORALOGIX_TEAM_USERS)))
    return tuple(i for i, agents in enumerate(_ROSTER_AGENT_AFFINITY) if agent_product in agents)


def roster_core_user_for_agent(session_id: str, agent_product: str) -> dict:
    allowed = roster_indices_for_agent(agent_product)
    if not allowed:
        return dict(_CORALOGIX_TEAM_USERS[0])
    strat = os.environ.get("SIM_CLAUDE_ROSTER_STRATEGY", "hash").strip().lower().replace("-", "_")
    if strat in ("round_robin", "rr"):
        ptr = st.roster_rr_by_agent.get(agent_product, 0)
        idx = allowed[ptr % len(allowed)]
        st.roster_rr_by_agent[agent_product] = ptr + 1
        return dict(_CORALOGIX_TEAM_USERS[idx])
    base = int(hashlib.sha256((session_id.strip() or "unknown-session").encode()).hexdigest(), 16)
    idx = allowed[base % len(allowed)]
    return dict(_CORALOGIX_TEAM_USERS[idx])


def random_coralogix_identity_for_agent(session_id: str, agent_product: str) -> dict:
    return roster_core_user_for_agent(session_id, agent_product)


def _coralogix_roster_email_local(
    i: int,
    first: str,
    last: str,
    *,
    occurrence: int,
) -> str:
    """
    Mailbox local-part for roster synthetic users.

    Default ``natural`` looks like ``alex.silva`` / ``alex.silva2`` when names collide.
    ``underscore``: ``team067_alex_silva`` (dots avoided for PromQL).
    ``legacy``: ``team.067.alex.silva`` for saved dashboards / drill-downs that relied on it.
    """
    fmt = os.environ.get("SIM_ROSTER_EMAIL_FORMAT", "natural").strip().lower()
    fn = first.lower()
    ln = last.lower()
    if fmt in ("legacy", "dots", "dotted", "period", "periods"):
        return f"team.{i:03d}.{fn}.{ln}"
    if fmt in ("underscore", "team_underscore", "old_underscore"):
        return f"team{i:03d}_{fn}_{ln}"
    base = f"{fn}.{ln}"
    if occurrence <= 1:
        return base
    return f"{base}{occurrence}"


def _build_coralogix_team_users() -> tuple[dict[str, str], ...]:
    users: list[dict[str, str]] = []
    name_counts: dict[str, int] = {}
    for i in range(_CORALOGIX_TEAM_ROSTER_SIZE):
        digest = hashlib.sha256(f"coralogix:sim:team:{i}".encode()).digest()
        first = _FIRST_NAMES[digest[0] % len(_FIRST_NAMES)]
        last = _LAST_NAMES[digest[1] % len(_LAST_NAMES)]
        base_key = f"{first.lower()}.{last.lower()}"
        name_counts[base_key] = name_counts.get(base_key, 0) + 1
        occ = name_counts[base_key]
        local = _coralogix_roster_email_local(i, first, last, occurrence=occ)
        email = f"{local}@coralogix.com"
        account_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"coralogix:sim:team:member:{i}"))
        user_id = hashlib.sha256(f"coralogix:sim:team:uid:{i}".encode()).hexdigest()
        users.append(
            {
                "user.account_uuid": account_uuid,
                "user.id": user_id,
                "user.name": f"{first} {last}",
                "user.email": email,
            }
        )
    return tuple(users)


_CORALOGIX_TEAM_USERS: tuple[dict[str, str], ...] = _build_coralogix_team_users()

# ``SIM_CLAUDE_ROSTER_STRATEGY=round_robin``: advance once per ``_claude_roster_core_user`` call (``both`` uses one call per emit).
st.cc_roster_rr_idx = 0
# When ``SIM_CLAUDE_STABLE_SESSION_PER_USER`` is false and ``SIM_CLAUDE_LONG_SESSION_SEC`` > 0: reuse one
# random ``session.id`` (UUID) across iterations so ``random_coralogix_identity(session_id)`` stays on one roster user.
st.cc_long_session_id: str | None = None
st.cc_long_session_deadline: float = 0.0
# When ``SIM_CLAUDE_LONG_SESSION_SEC`` > 0: **parallel slots** (``SIM_CLAUDE_CONCURRENT_LONG_SESSIONS``) each pin one
# roster user for ``dur`` seconds — multiple long-lived Claude identities at once (like Gemini slots).
st.cc_slot_users: list[dict | None] = []
st.cc_slot_deadlines: list[float] = []
st.cc_slot_rr: int = 0
# ``SIM_CLAUDE_PIN_METRIC_LABELS`` (default true): stable ``app.version``/``service.version``/``model`` per pin key
# so Prometheus scrapes hit the **same** time series across iterations (``increase()`` needs ≥2 samples).
st.cc_metric_label_pins: dict[str, tuple[str, str]] = {}


def _apply_claude_dotted_email_domain(user_attrs: dict) -> None:
    """Normalize ``user.email`` to ``local@coralogix.com`` (same suffix as roster users)."""
    raw = str(user_attrs.get("user.email", "user@coralogix.com"))
    if "@" in raw:
        local = raw.split("@", 1)[0].strip() or "user"
    else:
        local = raw.strip() or "user"
    user_attrs["user.email"] = f"{local}@coralogix.com"


def _claude_roster_core_user(session_id: str) -> dict:
    """
    Pick one roster row for Claude Code metrics/logs.

    - ``hash`` (default): stable per ``session_id`` (``random_coralogix_identity``).
    - ``round_robin`` / ``rr``: cycle ``_CORALOGIX_TEAM_USERS`` so many users get non-zero totals per wall clock.
    """
    return roster_core_user_for_agent(session_id, "claude_code")


def _claude_user_identity_flavor(session_id: str, flavor: str) -> dict:
    """``flat``: roster @coralogix.com; ``dotted``: same local-part @coralogix.com (normalized)."""
    d = dict(_claude_roster_core_user(session_id))
    if flavor == "dotted":
        _apply_claude_dotted_email_domain(d)
    return d


def random_coralogix_identity(session_id: str) -> dict:
    """
    Stable end-user identity for a session/trace: same ``session_id`` always maps to the same
    roster entry from ``_CORALOGIX_TEAM_USERS`` (225 fixed @coralogix.com users shared by all sims).
    """
    key = session_id.strip() or "unknown-session"
    idx = int(hashlib.sha256(key.encode()).hexdigest(), 16) % len(_CORALOGIX_TEAM_USERS)
    return dict(_CORALOGIX_TEAM_USERS[idx])


def random_claude_user_identity(session_id: str) -> dict:
    """Roster user; dotted profile normalizes email when active telemetry profile is ``dotted`` (not ``both``)."""
    p = _claude_telemetry_profile()
    if p == "dotted":
        return _claude_user_identity_flavor(session_id, "dotted")
    return _claude_user_identity_flavor(session_id, "flat")


def _claude_long_session_trace_id() -> str:
    """
    Return ``session.id`` for Claude Code when ``SIM_CLAUDE_STABLE_SESSION_PER_USER`` is **false**.

    Default (``SIM_CLAUDE_LONG_SESSION_SEC`` unset or 0): new UUID each iteration — many roster users
    per hour when ``SIM_FORCE_AGENT=claude_code``.

    When ``SIM_CLAUDE_LONG_SESSION_SEC`` > 0: reuse one UUID until wall-clock duration expires so
    ``sum by (user_id, user_email) (increase(...[60m]))`` shows the same user for hours, like the flat profile.
    """
    dur = _env_float("SIM_CLAUDE_LONG_SESSION_SEC", 0.0)
    if dur <= 0:
        return str(uuid.uuid4())
    now = time.monotonic()
    if st.cc_long_session_id is None or now >= st.cc_long_session_deadline:
        st.cc_long_session_id = str(uuid.uuid4())
        st.cc_long_session_deadline = now + float(dur)
    return st.cc_long_session_id


def _claude_stable_session_id_from_roster_user(user: dict) -> str:
    """Deterministic ``session.id`` for a roster user (stable across process restarts for the same account uuid)."""
    acc = str(user.get("user.account_uuid", "")).strip()
    uid = str(user.get("user.id", "")).strip()
    key = acc or uid or "unknown-user"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "otel-ai-agent-sim:claude:session-per-user:" + key))


def _claude_roster_user_key(user: dict) -> str:
    acc = str(user.get("user.account_uuid", "")).strip()
    uid = str(user.get("user.id", "")).strip()
    return acc or uid or "unknown-user"


def _claude_session_id_rotate_sec() -> float:
    """
    Wall-clock window to reuse the same ``session.id`` for one roster user.

    ``SIM_CLAUDE_SESSION_ID_ROTATE_SEC`` (default 3600 when ``SIM_CLAUDE_STABLE_SESSION_PER_USER``).
    Optional ``SIM_CLAUDE_SESSION_ID_ROTATE_SEC_MIN`` / ``_MAX`` pick a random duration per session.
    Set ``SIM_CLAUDE_SESSION_ID_ROTATE_SEC=0`` for eternal uuid5 per user.
    """
    if not _env_bool("SIM_CLAUDE_STABLE_SESSION_PER_USER", True):
        return 0.0
    lo_raw = os.environ.get("SIM_CLAUDE_SESSION_ID_ROTATE_SEC_MIN", "").strip()
    hi_raw = os.environ.get("SIM_CLAUDE_SESSION_ID_ROTATE_SEC_MAX", "").strip()
    if lo_raw and hi_raw:
        lo = max(1.0, _env_float("SIM_CLAUDE_SESSION_ID_ROTATE_SEC_MIN", lo_raw))
        hi = max(lo, _env_float("SIM_CLAUDE_SESSION_ID_ROTATE_SEC_MAX", hi_raw))
        return random.uniform(lo, hi)
    return max(0.0, _env_float("SIM_CLAUDE_SESSION_ID_ROTATE_SEC", 3600.0))


def _claude_session_id_for_roster_user(user: dict) -> str:
    """
    ``session.id`` for a roster user: stable across emits/logs/metrics in one session window, then rotates.

    Same ``user.email`` / account labels throughout; only ``session.id`` changes when the rotate window expires.
    """
    rotate = claude_user_session_rotate_duration_from_env(user)
    if rotate <= 0:
        return _claude_stable_session_id_from_roster_user(user)

    key = _claude_roster_user_key(user)
    now = time.monotonic()
    cached = st.cc_user_session_ids.get(key)
    if cached is not None:
        sid, deadline = cached
        if now < deadline:
            return sid
    sid = str(uuid.uuid4())
    st.cc_user_session_ids[key] = (sid, claude_session_id_rotate_deadline(now, float(rotate)))
    return sid


def _claude_emit_all_session_slots() -> bool:
    """When true and ``SIM_CLAUDE_LONG_SESSION_SEC`` > 0, emit once per active slot user each Claude cycle."""
    if not _env_bool("SIM_CLAUDE_EMIT_ALL_SESSION_SLOTS", True):
        return False
    return _env_float("SIM_CLAUDE_LONG_SESSION_SEC", 0.0) > 0


def _claude_emit_session_slot_count(n_slots: int) -> int:
    """
    How many parallel slot users emit when Claude Code wins agent selection.

    ``SIM_CLAUDE_EMIT_ALL_SESSION_SLOTS=true`` → all slots. Else ``SIM_CLAUDE_EMIT_SESSION_SLOT_COUNT``
    (default 1) caps fan-out so overview session share stays balanced vs span-based agents.
    """
    if n_slots <= 0:
        return 0
    if _claude_emit_all_session_slots():
        return n_slots
    cap = _env_int("SIM_CLAUDE_EMIT_SESSION_SLOT_COUNT", 1)
    return min(n_slots, max(1, cap))


def _claude_pick_session_slot_indices(n_emit: int, n_slots: int) -> list[int]:
    """Pick ``n_emit`` distinct slot indices (``SIM_CLAUDE_SESSION_SLOT_STRATEGY``)."""
    if n_emit >= n_slots:
        return list(range(n_slots))
    strat = os.environ.get("SIM_CLAUDE_SESSION_SLOT_STRATEGY", "random").strip().lower().replace("-", "_")
    if strat in ("round_robin", "rr"):
        start = st.cc_slot_rr % n_slots
        st.cc_slot_rr += n_emit
        return [(start + i) % n_slots for i in range(n_emit)]
    return random.sample(range(n_slots), n_emit)


def _claude_ensure_session_slots() -> int:
    """Initialize / refresh parallel Claude user slots; return slot count."""
    dur = _env_float("SIM_CLAUDE_LONG_SESSION_SEC", 0.0)
    n_slots = max(1, _env_int("SIM_CLAUDE_CONCURRENT_LONG_SESSIONS", 15))
    if len(st.cc_slot_users) != n_slots:
        st.cc_slot_users = [None] * n_slots
        st.cc_slot_deadlines = [0.0] * n_slots
        st.cc_slot_rr = 0
        if dur > 0 and _env_bool("SIM_CLAUDE_PREFILL_SESSION_SLOTS", True):
            now = time.monotonic()
            allowed = roster_indices_for_agent("claude_code")
            base = random.randrange(len(allowed))
            for i in range(n_slots):
                idx = allowed[(base + i) % len(allowed)]
                user = dict(_CORALOGIX_TEAM_USERS[idx])
                st.cc_slot_users[i] = user
                st.cc_slot_deadlines[i] = claude_slot_pin_deadline(
                    now, slot_index=i, n_slots=n_slots, roster_user=user, initial=True
                )
    if dur > 0:
        now = time.monotonic()
        for i in range(n_slots):
            if st.cc_slot_users[i] is None or now >= st.cc_slot_deadlines[i]:
                user = _claude_roster_core_user(str(uuid.uuid4()) + f":slot:{i}")
                st.cc_slot_users[i] = user
                st.cc_slot_deadlines[i] = claude_slot_pin_deadline(
                    now, slot_index=i, n_slots=n_slots, roster_user=user, initial=False
                )
    return n_slots


def _claude_roster_users_for_claude_code_emit() -> list[dict]:
    """Active slot users to emit this Claude cycle (1, capped N, or all slots)."""
    dur = _env_float("SIM_CLAUDE_LONG_SESSION_SEC", 0.0)
    if dur <= 0:
        return [_claude_roster_core_user(str(uuid.uuid4()))]
    n_slots = _claude_ensure_session_slots()
    n_emit = _claude_emit_session_slot_count(n_slots)
    indices = _claude_pick_session_slot_indices(n_emit, n_slots)
    return [dict(st.cc_slot_users[i]) for i in indices if st.cc_slot_users[i] is not None]


def _claude_roster_user_for_claude_code_emit() -> dict:
    """
    Pick the roster row for this Claude Code iteration.

    When ``SIM_CLAUDE_LONG_SESSION_SEC`` <= 0: fresh roster draw each call.

    When ``dur`` > 0: maintain ``SIM_CLAUDE_CONCURRENT_LONG_SESSIONS`` (default 15) independent slots; each slot keeps
    one user until its deadline. Emits pick a slot via ``SIM_CLAUDE_SESSION_SLOT_STRATEGY`` (``random`` or ``round_robin``).
    Use ``SIM_CLAUDE_EMIT_ALL_SESSION_SLOTS=true`` (default when ``dur`` > 0) to emit for every active slot each cycle.
    Each user maps to a rotating ``session.id`` via ``_claude_session_id_for_roster_user`` (see
    ``SIM_CLAUDE_SESSION_ID_ROTATE_SEC``).
    """
    dur = _env_float("SIM_CLAUDE_LONG_SESSION_SEC", 0.0)
    if dur <= 0:
        return _claude_roster_core_user(str(uuid.uuid4()))

    n_slots = _claude_ensure_session_slots()

    strat = os.environ.get("SIM_CLAUDE_SESSION_SLOT_STRATEGY", "random").strip().lower().replace("-", "_")
    if strat in ("round_robin", "rr"):
        i = st.cc_slot_rr % n_slots
        st.cc_slot_rr += 1
    else:
        i = random.randrange(n_slots)

    return dict(st.cc_slot_users[i])


def _claude_otlp_span_user_attrs_from_roster(roster_user: dict) -> dict:
    """User attributes on Claude ``user_prompt`` spans (matches ``random_claude_user_identity`` profile rules)."""
    d = dict(roster_user)
    if _claude_telemetry_profile() == "dotted":
        _apply_claude_dotted_email_domain(d)
    return d


def _copilot_collector_user_login(user_attrs: dict) -> str:
    """GitHub-style login for collector ``user_login`` (local-part of roster email when present)."""
    email = str(user_attrs.get("user.email", "") or "").strip()
    if "@" in email:
        local = email.split("@", 1)[0].strip()
        if local:
            return local
    name = str(user_attrs.get("user.name", "") or "").strip()
    return name.replace(" ", "-").lower() or "unknown"


def _copilot_collector_omit_user_email(user_attrs: dict) -> bool:
    """
    Deterministic subset of roster users with empty ``user_email`` on collector series.

    Exercises cx498 PromQL ``label_join`` fallback (login + name) while spans keep
    ``process.tags['user.email']`` for users that have a roster address.
    """
    rate = min(1.0, max(0.0, _env_float("SIM_COPILOT_COLLECTOR_EMPTY_EMAIL_RATE", 0.25)))
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    key = str(user_attrs.get("user.account_uuid", "") or user_attrs.get("user.id", "") or "").strip()
    if not key:
        return random.random() < rate
    digest = hashlib.sha256(f"otel-ai-agent-sim:copilot:collector:no-email:{key}".encode()).digest()
    return (digest[0] / 255.0) < rate


def copilot_collector_user_metric_labels(user_attrs: dict, *, org: str) -> dict[str, str]:
    """Prometheus ``user_*`` label set for ``github_copilot_user_*`` metrics."""
    login = _copilot_collector_user_login(user_attrs)
    name = str(user_attrs.get("user.name", login) or login)
    email = str(user_attrs.get("user.email", "") or "").strip()
    if email.endswith("@coralogix.com") and _copilot_collector_omit_user_email(user_attrs):
        email = ""
    elif not email or email == "unknown@coralogix.com":
        email = ""
    return {
        "organization": org,
        "user_email": email,
        "user_login": login,
        "user_name": name,
    }


def copilot_collector_dau_identity(user_metric_labels: dict[str, str]) -> str:
    """Stable DAU/WAU key (email when set, else login)."""
    email = str(user_metric_labels.get("user_email", "") or "").strip()
    if email:
        return email
    login = str(user_metric_labels.get("user_login", "") or "").strip()
    return f"login:{login}" if login else "unknown"


def _claude_metric_label_pin_key(roster_user: dict | None, session_id: str) -> str:
    """Stable key for pinning Prometheus label sets (prefer roster account uuid)."""
    if roster_user is not None:
        rk = str(roster_user.get("user.account_uuid", "")).strip() or str(roster_user.get("user.id", "")).strip()
        return rk or session_id.strip() or "unknown-pin"
    return session_id.strip() or "unknown-session"


def _claude_pinned_flat_version_and_model(pin_key: str) -> tuple[str, str]:
    """
    Return ``(app_version, model)`` pinned for ``pin_key`` so counter label sets stop rotating every emit.

    Only used when ``SIM_CLAUDE_PIN_METRIC_LABELS`` is true (see ``emit_claude_code_dashboard``).
    """
    got = st.cc_metric_label_pins.get(pin_key)
    if got is not None:
        return got
    ver = os.environ.get("SIM_CC_APP_VERSION", "").strip() or tool_version_for("claude_code")
    mod = os.environ.get("SIM_CLAUDE_MODEL", "").strip() or random.choice(_CLAUDE_CODE_MODELS)
    got = (ver, mod)
    st.cc_metric_label_pins[pin_key] = got
    return got
