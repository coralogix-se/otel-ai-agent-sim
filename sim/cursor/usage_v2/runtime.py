"""Emit synthetic Cursor Usage ``cursor_*`` activity for the Admin Usage dashboard."""

from __future__ import annotations

import hashlib
import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sim.common.env import _env_float, _env_int
from sim.common.identity import _CORALOGIX_TEAM_USERS, roster_indices_for_agent
from sim.cursor.usage_v2.collector import CursorUsageCollector, get_cursor_usage_collector
from sim.cursor.usage_v2.constants import (
    CURSOR_BILLING_CLASS_WEIGHTS,
    CURSOR_BILLING_CLASSES,
    CURSOR_BILLING_KIND_WEIGHTS,
    CURSOR_BILLING_KINDS,
    CURSOR_BUGBOT_ISSUE_STATES,
    CURSOR_BUGBOT_SEVERITIES,
    CURSOR_CHANGE_SOURCES,
    CURSOR_CHART_SURFACES,
    CURSOR_CLIENT_VERSION_LATEST,
    CURSOR_CLIENT_VERSION_ONE_BEHIND,
    CURSOR_CLIENT_VERSION_STALE,
    CURSOR_COMMANDS,
    CURSOR_COMMIT_SOURCES,
    CURSOR_CONVERSATION_DIMENSIONS,
    CURSOR_CONVERSATION_INTENT_TO_MODE,
    CURSOR_CONVERSATION_SUBCATEGORIES,
    CURSOR_CONVERSATION_SUBCATEGORY_WEIGHTS,
    CURSOR_DIRECTIONS,
    CURSOR_FILE_EXTENSIONS,
    CURSOR_GROUPS,
    CURSOR_MCP_SERVERS,
    CURSOR_MCP_TOOLS,
    CURSOR_REPOS,
    CURSOR_ROLES,
    CURSOR_SERVICE_ACCOUNTS,
    CURSOR_SKILLS,
    CURSOR_SURFACE_ADOPTION,
    CURSOR_SURFACE_WEIGHTS,
    CURSOR_SURFACES,
    CURSOR_TOKEN_TYPES,
    CURSOR_USAGE_MODEL_WEIGHTS,
    CURSOR_USAGE_MODELS,
    cursor_usage_conversations_per_day,
    cursor_usage_events_per_conversation,
    cursor_usage_events_per_user_day,
    cursor_usage_events_per_user_day_max,
    cursor_usage_idle_seats,
    cursor_usage_stale_client_seats,
    cursor_usage_cx_application,
    cursor_usage_cx_subsystem,
    cursor_usage_organization,
    cursor_usage_org_pool_limit_usd,
    cursor_usage_roster_size,
    cursor_usage_team_id,
    cursor_usage_team_name,
)


def _pick(items: tuple[str, ...], weights: tuple[float, ...] | None = None) -> str:
    if weights is None:
        return random.choice(items)
    return random.choices(items, weights=weights, k=1)[0]


# Per-day c4c targets (see scales.py): $204 event cost, $4.3k member daily spend, $3.2k cycle gross.
_EVENT_COST_SCALE = 0.0200
_EVENT_TOKEN_FEE_SHARE = 0.13
_DAILY_SPEND_PER_MEMBER_USD = 22.4
_CYCLE_GROSS_PER_MEMBER_USD = 11.06
# Two seats sit just over a low limit so the members-limited and overage insights always fire.
_LIMITED_SEATS = 2
_LIMITED_SEAT_LIMIT_USD = 525.0
_LIMITED_SEAT_SPEND_RATIO = 1.05
# One runaway max-mode conversation per day (>= the heavy-conversation insight's $100 floor); it also
# makes its owner the cost-concentration outlier and tips the max-mode and premium-model shares.
_HEAVY_CONVERSATION_INDEX = 2
_HEAVY_CONVERSATION_COST_USD = 105.0
_HEAVY_CONVERSATION_MODEL = "claude-opus-5-thinking-high"
# Skewed so the autonomous-complex-work insight (>=40% high complexity, >=50% low guidance) fires.
_DIMENSION_WEIGHTS: dict[str, tuple[float, ...]] = {
    "complexity": (0.10, 0.15, 0.25, 0.50),
    "guidanceLevels": (0.60, 0.25, 0.15),
}


def _work_type_weights(day_ordinal: int) -> tuple[float, ...]:
    """bug / ktlo / new_feature; the KTLO share ramps 45%->70% over 28 days so the rising-share insight fires."""
    ktlo = 0.45 + 0.25 * ((day_ordinal % 28) / 28)
    return ((1 - ktlo) / 2, ktlo, (1 - ktlo) / 2)


@dataclass(frozen=True)
class _UsagePersona:
    """Stable per-member knobs that feed FE Usage Pattern tags (cx498 Ba/al/sl/ll/cl).

    Tags are computed client-side from requests, daysActive, maxModeShare, acceptRate,
    spend/CPR, and cache_read/request — not from a dedicated metric.
    """

    name: str
    # Relative chance to get new conversations / events (power users ≫ light).
    intensity: float
    # P(max_mode=true) on new conversations — >0.5 share → deepThinker.
    max_mode_p: float
    # Mean accepts/applies — heavily weights adoption tier.
    accept_rate: float
    # P(active day) when the member did not already emit — lowers daysActive.
    active_day_p: float
    # Multiplier on cache_read token share → longSessions / shortSessions.
    cache_read_mult: float
    # Multiplier on event cost → overBudget / costEfficient / premiumModel.
    spend_mult: float
    # Cap on events/user/day as a fraction of the global max.
    event_cap_frac: float


# Weighted catalog — keep Balanced from dominating; spread adoption tiers.
_PERSONA_CATALOG: tuple[tuple[_UsagePersona, float], ...] = (
    (
        _UsagePersona(
            "power_user",
            intensity=3.2,
            max_mode_p=0.12,
            accept_rate=0.92,
            active_day_p=0.98,
            cache_read_mult=1.0,
            spend_mult=1.3,
            event_cap_frac=1.0,
        ),
        0.12,
    ),
    (
        _UsagePersona(
            "deep_thinker",
            intensity=1.1,
            max_mode_p=0.72,
            accept_rate=0.70,
            active_day_p=0.85,
            cache_read_mult=1.4,
            spend_mult=2.2,
            event_cap_frac=0.85,
        ),
        0.12,
    ),
    (
        _UsagePersona(
            "balanced",
            intensity=1.0,
            max_mode_p=0.10,
            accept_rate=0.68,
            active_day_p=0.90,
            cache_read_mult=1.0,
            spend_mult=1.0,
            event_cap_frac=0.75,
        ),
        0.16,
    ),
    (
        _UsagePersona(
            "light_user",
            intensity=0.22,
            max_mode_p=0.04,
            accept_rate=0.50,
            active_day_p=0.35,
            cache_read_mult=0.55,
            spend_mult=0.35,
            event_cap_frac=0.12,
        ),
        0.12,
    ),
    (
        _UsagePersona(
            "sporadic",
            intensity=0.12,
            max_mode_p=0.03,
            accept_rate=0.35,
            active_day_p=0.18,
            cache_read_mult=0.40,
            spend_mult=0.25,
            event_cap_frac=0.06,
        ),
        0.10,
    ),
    (
        _UsagePersona(
            "manual_coder",
            intensity=0.70,
            max_mode_p=0.04,
            accept_rate=0.12,
            active_day_p=0.65,
            cache_read_mult=0.35,
            spend_mult=0.55,
            event_cap_frac=0.55,
        ),
        0.10,
    ),
    (
        _UsagePersona(
            "cost_efficient",
            intensity=1.6,
            max_mode_p=0.05,
            accept_rate=0.80,
            active_day_p=0.90,
            cache_read_mult=0.75,
            spend_mult=0.28,
            event_cap_frac=0.90,
        ),
        0.10,
    ),
    (
        _UsagePersona(
            "premium",
            intensity=1.0,
            max_mode_p=0.58,
            accept_rate=0.72,
            active_day_p=0.85,
            cache_read_mult=1.2,
            spend_mult=3.8,
            event_cap_frac=0.80,
        ),
        0.08,
    ),
    (
        _UsagePersona(
            "long_context",
            intensity=1.0,
            max_mode_p=0.10,
            accept_rate=0.65,
            active_day_p=0.80,
            cache_read_mult=4.5,
            spend_mult=1.15,
            event_cap_frac=0.70,
        ),
        0.05,
    ),
    (
        _UsagePersona(
            "short_context",
            intensity=1.15,
            max_mode_p=0.08,
            accept_rate=0.70,
            active_day_p=0.85,
            cache_read_mult=0.12,
            spend_mult=0.85,
            event_cap_frac=0.75,
        ),
        0.05,
    ),
)


def _stable_persona(email: str) -> _UsagePersona:
    """Deterministic persona pick from the weighted catalog."""
    digest = hashlib.sha256(f"cursor-persona:{email}".encode()).hexdigest()
    slot = int(digest[:8], 16) / 0xFFFFFFFF
    acc = 0.0
    for persona, weight in _PERSONA_CATALOG:
        acc += weight
        if slot <= acc:
            return persona
    return _PERSONA_CATALOG[-1][0]


@dataclass(frozen=True)
class _UsageMember:
    email: str
    user_id: str
    name: str
    role: str
    group_id: str
    group_name: str
    is_unassigned: bool
    monthly_limit_usd: float
    client_version: str
    may_exceed_limit: bool
    is_idle: bool
    surfaces: tuple[str, ...]
    persona: _UsagePersona


def _stable_user_id(email: str) -> str:
    digest = hashlib.sha256(f"cursor-usage:{email}".encode()).hexdigest()[:26]
    return f"user_{digest}"


def _stable_limit_usd(email: str) -> float:
    """Deterministic per-member monthly limit in the $500–$1700 band."""
    digest = hashlib.sha256(f"cursor-limit:{email}".encode()).hexdigest()
    # 500..1700 inclusive in $10 steps.
    bucket = int(digest[:8], 16) % 121  # 0..120
    return float(500 + bucket * 10)


def _stable_surface_affinity(email: str) -> tuple[str, ...]:
    """Per-member chart surfaces — drives distinct Active Users by Surface counts."""
    chosen: list[str] = []
    for surface in CURSOR_CHART_SURFACES:
        digest = hashlib.sha256(f"cursor-surface:{email}:{surface}".encode()).hexdigest()
        pct = int(digest[:8], 16) % 100
        if pct < int(CURSOR_SURFACE_ADOPTION[surface] * 100):
            chosen.append(surface)
    if not chosen:
        # Everyone uses at least agent or composer.
        fallback = hashlib.sha256(f"cursor-surface-fallback:{email}".encode()).hexdigest()
        chosen.append("composer" if int(fallback[:2], 16) % 2 else "agent")
    return tuple(chosen)


def _surface_weights(surfaces: tuple[str, ...]) -> tuple[float, ...]:
    idx = {s: i for i, s in enumerate(CURSOR_SURFACES)}
    raw = [CURSOR_SURFACE_WEIGHTS[idx[s]] for s in surfaces]
    total = sum(raw)
    return tuple(w / total for w in raw)


def _pick_member_surface(member: _UsageMember) -> str:
    return _pick(member.surfaces, _surface_weights(member.surfaces))


def _client_version_rank(email: str) -> str:
    return hashlib.sha256(f"cursor-client:{email}".encode()).hexdigest()


def _stale_client_version_map(active_emails: list[str]) -> dict[str, str]:
    """Pick 1–2 stable stale-client seats; everyone else gets latest (or one-behind)."""
    stale_n = min(cursor_usage_stale_client_seats(), max(0, len(active_emails) - 1))
    ranked = sorted(active_emails, key=_client_version_rank)
    stale_map: dict[str, str] = {}
    for i, email in enumerate(ranked[:stale_n]):
        stale_map[email] = CURSOR_CLIENT_VERSION_STALE[i % len(CURSOR_CLIENT_VERSION_STALE)]
    for email in ranked[stale_n:]:
        digest = hashlib.sha256(f"cursor-client-near:{email}".encode()).hexdigest()
        stale_map[email] = (
            CURSOR_CLIENT_VERSION_ONE_BEHIND
            if int(digest[:4], 16) % 10 == 0
            else CURSOR_CLIENT_VERSION_LATEST
        )
    return stale_map


def _build_roster() -> list[_UsageMember]:
    n = cursor_usage_roster_size()
    allowed = list(roster_indices_for_agent("cursor"))
    if not allowed:
        allowed = list(range(min(n, len(_CORALOGIX_TEAM_USERS))))
    # Prefer affinity indices, then fill from the front of the team roster.
    ordered: list[int] = []
    for i in allowed:
        if i not in ordered:
            ordered.append(i)
    for i in range(len(_CORALOGIX_TEAM_USERS)):
        if len(ordered) >= n:
            break
        if i not in ordered:
            ordered.append(i)
    idle_count = min(cursor_usage_idle_seats(), max(0, n - 2))
    roster_rows = [(rank, idx) for rank, idx in enumerate(ordered[:n])]
    active_emails = [
        _CORALOGIX_TEAM_USERS[idx]["user.email"]
        for rank, idx in roster_rows
        if rank < n - idle_count
    ]
    client_versions = _stale_client_version_map(active_emails)
    members: list[_UsageMember] = []
    for rank, idx in roster_rows:
        row = _CORALOGIX_TEAM_USERS[idx]
        email = row["user.email"]
        gid, gname = CURSOR_GROUPS[rank % len(CURSOR_GROUPS)]
        is_unassigned = gid == "unassigned"
        is_idle = rank >= n - idle_count
        may_exceed_limit = (not is_idle) and rank < _LIMITED_SEATS
        members.append(
            _UsageMember(
                email=email,
                user_id=_stable_user_id(email),
                name=row.get("user.name", email.split("@", 1)[0]),
                role=CURSOR_ROLES[rank % len(CURSOR_ROLES)],
                group_id=gid,
                group_name=gname,
                is_unassigned=is_unassigned,
                monthly_limit_usd=(
                    _LIMITED_SEAT_LIMIT_USD if may_exceed_limit else _stable_limit_usd(email)
                ),
                client_version=client_versions.get(email, CURSOR_CLIENT_VERSION_LATEST),
                may_exceed_limit=may_exceed_limit,
                is_idle=is_idle,
                surfaces=() if is_idle else _stable_surface_affinity(email),
                persona=_stable_persona(email),
            )
        )
    return members


_ROSTER: list[_UsageMember] | None = None
_MODEL_USERS_TODAY: dict[str, set[str]] = {}
_ROSTER_SEEDED = False
# Annual pooled budget snapshots (Admin /organizations/pooled-usage).
_ORG_POOL_LIMIT_USD: float | None = None
_ORG_POOL_USAGE_USD: float | None = None
_ORG_POOL_SEEDED = False

# Conversation reuse — keep conversation_id cardinality near cxai-dev (hundreds/day),
# not tens of thousands. One open session is reused for 20–40 events.
@dataclass
class _OpenConversation:
    conversation_id: str
    member: _UsageMember
    model: str
    kind: str
    max_mode: bool
    service_account: str
    events_remaining: int
    day: str
    # Set only on the daily heavy conversation: every event costs this much.
    fixed_event_cost_usd: float | None = None


_OPEN_CONVERSATIONS: list[_OpenConversation] = []
_USAGE_DAY = ""
_CONVERSATIONS_STARTED_TODAY = 0
_EVENTS_EMITTED_TODAY = 0
_EVENTS_BY_USER_TODAY: dict[str, int] = {}
# email → surfaces touched today (for cursor_active_users_* DAU).
_SURFACE_USERS_TODAY: dict[str, set[str]] = {}
# Soft "active day" flags for members who didn't emit events (stable within the UTC day).
_SOFT_ACTIVE_TODAY: set[str] = set()
_SOFT_ACTIVE_DECIDED = False
_BRIEF_EXTRA_DAY = ""
# Full day multiplier (up and down) for the daily conversation budget and spend; set each cycle.
_DAY_MULT = 1.0


def _roster() -> list[_UsageMember]:
    global _ROSTER
    if _ROSTER is None:
        _ROSTER = _build_roster()
    return _ROSTER


def _roll_usage_day(day: str) -> None:
    """Reset daily conversation / event budgets at UTC midnight."""
    global _USAGE_DAY, _CONVERSATIONS_STARTED_TODAY, _EVENTS_EMITTED_TODAY
    global _EVENTS_BY_USER_TODAY, _OPEN_CONVERSATIONS, _MODEL_USERS_TODAY
    global _SURFACE_USERS_TODAY, _SOFT_ACTIVE_TODAY, _SOFT_ACTIVE_DECIDED
    if _USAGE_DAY == day:
        return
    _USAGE_DAY = day
    _CONVERSATIONS_STARTED_TODAY = 0
    _EVENTS_EMITTED_TODAY = 0
    _EVENTS_BY_USER_TODAY = {}
    _OPEN_CONVERSATIONS = []
    _MODEL_USERS_TODAY = {}
    _SURFACE_USERS_TODAY = {}
    _SOFT_ACTIVE_TODAY = set()
    _SOFT_ACTIVE_DECIDED = False


def _day_fraction(now: datetime) -> float:
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return min(1.0, max(0.0, (now - start).total_seconds() / 86400.0))


def _target_events_per_day(active_n: int) -> int:
    lo, hi = cursor_usage_events_per_conversation()
    mid = (lo + hi) / 2.0
    from_convs = int(cursor_usage_conversations_per_day() * mid)
    from_users = active_n * cursor_usage_events_per_user_day()
    capped = active_n * cursor_usage_events_per_user_day_max()
    return max(1, min(from_convs, from_users, capped))


def _events_to_emit_this_cycle(now: datetime, target_day: int) -> int:
    """Pace event volume across the UTC day so 7d series stay under metrics limits."""
    expected = target_day * _day_fraction(now)
    deficit = expected - _EVENTS_EMITTED_TODAY
    if deficit <= 0:
        return 0
    # EMITS_PER_CYCLE is the per-loop burst cap (not "new conversations per loop").
    max_burst = max(1, _env_int("SIM_CURSOR_USAGE_EMITS_PER_CYCLE", 2))
    return min(max_burst, max(1, int(deficit + 0.999)))


def _can_start_conversation(now: datetime) -> bool:
    budget = max(1, round(cursor_usage_conversations_per_day() * _DAY_MULT))
    if _CONVERSATIONS_STARTED_TODAY >= budget:
        return False
    expected = budget * _day_fraction(now)
    # Small slack so we do not starve early in the day.
    return _CONVERSATIONS_STARTED_TODAY < expected + 2


def _member_event_cap(member: _UsageMember) -> int:
    """Per-persona daily event cap — light/sporadic users stay under FE lowUsage thresholds."""
    global_cap = cursor_usage_events_per_user_day_max()
    return max(1, int(global_cap * member.persona.event_cap_frac))


def _pick_member_for_new_conversation(active_members: list[_UsageMember]) -> _UsageMember | None:
    eligible: list[_UsageMember] = []
    weights: list[float] = []
    for m in active_members:
        if _EVENTS_BY_USER_TODAY.get(m.email, 0) >= _member_event_cap(m):
            continue
        # Soft day-off: low active_day_p personas often skip starting work today.
        if (
            _EVENTS_BY_USER_TODAY.get(m.email, 0) == 0
            and random.random() > m.persona.active_day_p
        ):
            continue
        eligible.append(m)
        weights.append(max(0.01, m.persona.intensity))
    if not eligible:
        return None
    return random.choices(eligible, weights=weights, k=1)[0]


def _start_conversation(
    member: _UsageMember, day: str, active_members: list[_UsageMember]
) -> _OpenConversation:
    global _CONVERSATIONS_STARTED_TODAY
    lo, hi = cursor_usage_events_per_conversation()
    billing_class = _pick(CURSOR_BILLING_CLASSES, CURSOR_BILLING_CLASS_WEIGHTS)
    kind = _pick(CURSOR_BILLING_KINDS, CURSOR_BILLING_KIND_WEIGHTS)
    if billing_class == "api_key" or kind == "API Key" or random.random() < 0.12:
        service_account = _pick(tuple(a for a in CURSOR_SERVICE_ACCOUNTS if a != "none"))
    else:
        service_account = "none"
    event_count = random.randint(lo, hi)
    conv = _OpenConversation(
        conversation_id=str(uuid.uuid4()),
        member=member,
        model=_pick(CURSOR_USAGE_MODELS, CURSOR_USAGE_MODEL_WEIGHTS),
        kind=kind,
        max_mode=random.random() < member.persona.max_mode_p,
        service_account=service_account,
        events_remaining=event_count,
        day=day,
    )
    if _CONVERSATIONS_STARTED_TODAY == _HEAVY_CONVERSATION_INDEX:
        conv.member = active_members[min(_HEAVY_CONVERSATION_INDEX, len(active_members) - 1)]
        conv.model = _HEAVY_CONVERSATION_MODEL
        conv.max_mode = True
        conv.service_account = "none"
        conv.fixed_event_cost_usd = _HEAVY_CONVERSATION_COST_USD / event_count
    _OPEN_CONVERSATIONS.append(conv)
    _CONVERSATIONS_STARTED_TODAY += 1
    return conv


def _acquire_conversation(
    now: datetime, active_members: list[_UsageMember], day: str
) -> tuple[_OpenConversation, bool] | None:
    """Return (conversation, is_new) or None when daily budgets are exhausted."""
    eligible_open = [
        c
        for c in _OPEN_CONVERSATIONS
        if c.day == day
        and c.events_remaining > 0
        and _EVENTS_BY_USER_TODAY.get(c.member.email, 0) < _member_event_cap(c.member)
    ]
    # Always finish open conversations (20–40 events) before opening another id.
    # This is what keeps conversation_id cardinality near cxai-dev levels.
    if eligible_open:
        return random.choice(eligible_open), False
    if _can_start_conversation(now):
        member = _pick_member_for_new_conversation(active_members)
        if member is not None:
            return _start_conversation(member, day, active_members), True
    return None


def _consume_conversation_event(conv: _OpenConversation) -> None:
    global _EVENTS_EMITTED_TODAY
    conv.events_remaining -= 1
    _EVENTS_EMITTED_TODAY += 1
    _EVENTS_BY_USER_TODAY[conv.member.email] = (
        _EVENTS_BY_USER_TODAY.get(conv.member.email, 0) + 1
    )
    if conv.events_remaining <= 0 and conv in _OPEN_CONVERSATIONS:
        _OPEN_CONVERSATIONS.remove(conv)


def _ensure_org_pool(now: datetime) -> tuple[float, float]:
    """Seed annual limit + YTD usage so we look ~on pace for the commitment."""
    global _ORG_POOL_LIMIT_USD, _ORG_POOL_USAGE_USD, _ORG_POOL_SEEDED
    if not _ORG_POOL_SEEDED or _ORG_POOL_LIMIT_USD is None or _ORG_POOL_USAGE_USD is None:
        limit = cursor_usage_org_pool_limit_usd()
        # Live within budget: seed at day-of-year fraction of the annual limit.
        doy = float(now.timetuple().tm_yday)
        ytd_frac = min(1.0, max(0.0, doy / 365.0))
        _ORG_POOL_LIMIT_USD = limit
        _ORG_POOL_USAGE_USD = round(limit * ytd_frac, 2)
        _ORG_POOL_SEEDED = True
    return _ORG_POOL_LIMIT_USD, _ORG_POOL_USAGE_USD


def _accrue_org_pool_usage(amount: float) -> None:
    global _ORG_POOL_USAGE_USD
    if amount <= 0 or _ORG_POOL_USAGE_USD is None:
        return
    _ORG_POOL_USAGE_USD = round(_ORG_POOL_USAGE_USD + float(amount), 4)


def _accrue_seat_activity(
    collector: CursorUsageCollector,
    *,
    base: dict[str, str],
    email: str,
    day: str,
    agent_suggested_lines: float,
    agent_accepted_lines: float,
    tab_suggestions: float,
    tab_accepts: float = 0.0,
) -> None:
    """Emit FE seat-activity series (Adoption Active Users / Adoption Rate / Idle Seats)."""
    for outcome, amount in (
        ("suggested", agent_suggested_lines),
        ("accepted", agent_accepted_lines),
    ):
        if amount <= 0:
            continue
        collector.accrue_snapshot(
            "cursor_user_lines_total",
            {
                **base,
                "email": email,
                "source": "agent",
                "colour": "green",
                "outcome": outcome,
                "date": day,
            },
            amount,
        )
    if tab_suggestions > 0:
        collector.accrue_snapshot(
            "cursor_user_tab_suggestions_total",
            {**base, "email": email, "date": day},
            tab_suggestions,
        )
    if tab_accepts > 0:
        collector.accrue_snapshot(
            "cursor_user_tab_accepts_total",
            {**base, "email": email, "date": day},
            tab_accepts,
        )


def _refresh_org_pool_snapshots(collector: CursorUsageCollector, *, now: datetime) -> None:
    """Restate Annual Budget gauges (last_over_time / sum widgets)."""
    limit, usage = _ensure_org_pool(now)
    remaining = round(limit - usage, 4)
    org = cursor_usage_organization()
    team_id = cursor_usage_team_id()
    team_name = cursor_usage_team_name()
    base_org = {
        "cx_application_name": cursor_usage_cx_application(),
        "cx_subsystem_name": cursor_usage_cx_subsystem(),
        "organization": org,
    }
    collector.clear_snapshots_with_prefix("cursor_org_pool_enabled")
    collector.clear_snapshots_with_prefix("cursor_org_pool_limit_usd")
    collector.clear_snapshots_with_prefix("cursor_org_pool_remaining_usd")
    collector.clear_snapshots_with_prefix("cursor_org_pooled_usage_usd")
    collector.set_snapshot("cursor_org_pool_enabled", base_org, 1.0)
    collector.set_snapshot("cursor_org_pool_limit_usd", base_org, float(limit))
    collector.set_snapshot("cursor_org_pool_remaining_usd", base_org, float(remaining))
    collector.set_snapshot(
        "cursor_org_pooled_usage_usd",
        {**base_org, "team_id": team_id, "team_name": team_name},
        float(usage),
    )


def _seed_snapshots(collector: CursorUsageCollector, *, now: datetime) -> None:
    """Idempotent roster / org / cycle snapshot refresh."""
    global _ROSTER_SEEDED
    team_id = cursor_usage_team_id()
    base = collector.base_labels(team_id)
    day = now.date().isoformat()

    collector.clear_snapshots_with_prefix("cursor_member_info")
    collector.clear_snapshots_with_prefix("cursor_group_members")
    collector.clear_snapshots_with_prefix("cursor_org_team_membership_info")
    collector.clear_snapshots_with_prefix("cursor_member_monthly_limit_usd")
    collector.clear_snapshots_with_prefix("cursor_member_effective_limit_usd")
    collector.clear_snapshots_with_prefix("cursor_billing_cycle_start_seconds")
    collector.clear_snapshots_with_prefix("cursor_billing_cycle_end_seconds")
    collector.clear_snapshots_with_prefix("cursor_bugbot_repos")
    collector.clear_snapshots_with_prefix("cursor_bugbot_issues_snapshot")

    # Billing cycle: 1st of month → 1st of next month.
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    collector.set_snapshot(
        "cursor_billing_cycle_start_seconds",
        base,
        float(int(start.timestamp())),
    )
    collector.set_snapshot(
        "cursor_billing_cycle_end_seconds",
        base,
        float(int(end.timestamp())),
    )
    # Brief §8 Add: org contract + members + model access.
    org_labels = {
        "cx_application_name": base["cx_application_name"],
        "cx_subsystem_name": base["cx_subsystem_name"],
        "organization": cursor_usage_organization(),
    }
    collector.set_snapshot(
        "cursor_org_contract_start_seconds",
        org_labels,
        float(int(start.timestamp())),
    )
    collector.set_snapshot(
        "cursor_org_contract_end_seconds",
        org_labels,
        float(int(end.timestamp()) + 86400 * 365),
    )
    roster_n = len(_roster())
    collector.set_snapshot(
        "cursor_org_members",
        {**org_labels, "role": "member"},
        float(max(1, roster_n - 2)),
    )
    collector.set_snapshot(
        "cursor_org_members",
        {**org_labels, "role": "admin"},
        2.0,
    )
    for i, model in enumerate(CURSOR_USAGE_MODELS):
        collector.set_snapshot(
            "cursor_model_access_enabled",
            {**base, "model": model},
            1.0,
        )
        if i < 2:
            collector.set_snapshot(
                "cursor_model_access_new_default",
                {**base, "model": model},
                1.0 if i == 0 else 0.0,
            )
    # Low-rate org events (login/logout/mcp/member_added) — accrue once per seed refresh.
    if random.random() < 0.35:
        collector.add_delta(
            "cursor_login_attempts_total",
            {**base, "date": day, "result": "success"},
            1,
        )
    if random.random() < 0.15:
        collector.add_delta(
            "cursor_logout_total",
            {**base, "date": day},
            1,
        )
    if random.random() < 0.2:
        collector.add_delta(
            "cursor_mcp_config_events_total",
            {**base, "date": day, "event": random.choice(("add", "update", "remove"))},
            1,
        )
    if random.random() < 0.05:
        collector.add_delta(
            "cursor_member_added_total",
            {**base, "date": day},
            1,
        )

    for m in _roster():
        collector.set_snapshot(
            "cursor_member_info",
            {
                **base,
                "email": m.email,
                "user_id": m.user_id,
                "name": m.name,
                "role": m.role,
                "is_removed": "false",
            },
            1.0,
        )
        collector.set_snapshot(
            "cursor_member_monthly_limit_usd",
            {**base, "email": m.email, "user_id": m.user_id, "name": m.name, "role": m.role},
            m.monthly_limit_usd,
        )
        collector.set_snapshot(
            "cursor_member_effective_limit_usd",
            {**base, "email": m.email, "user_id": m.user_id, "name": m.name, "role": m.role},
            m.monthly_limit_usd,
        )
        collector.set_snapshot(
            "cursor_member_hard_limit_usd",
            {**base, "email": m.email, "user_id": m.user_id, "name": m.name, "role": m.role},
            m.monthly_limit_usd * 1.5,
        )
        collector.set_snapshot(
            "cursor_org_member_contract_spend_usd",
            {**base, "email": m.email, "user_id": m.user_id, "name": m.name},
            round(m.monthly_limit_usd * random.uniform(0.05, 0.45), 2),
        )
    # Brief §8: cut group_members series 5 → ~1.
    if _roster():
        m0 = _roster()[0]
        collector.set_snapshot(
            "cursor_group_members",
            {
                **base,
                "group_id": m0.group_id,
                "group_name": m0.group_name,
                "is_unassigned": "true" if m0.is_unassigned else "false",
            },
            float(len(_roster())),
        )

    # Brief §8: raise series 1 → ~3 (owner / member / admin).
    for role in ("owner", "member", "admin"):
        collector.set_snapshot(
            "cursor_org_team_membership_info",
            {
                **base,
                "organization": cursor_usage_organization(),
                "team_name": cursor_usage_team_name(),
                "team_role": role,
            },
            1.0,
        )

    _refresh_org_pool_snapshots(collector, now=now)

    # Bugbot coverage snapshot (~60% of catalog repos enabled).
    enabled_n = max(1, int(len(CURSOR_REPOS) * 0.6))
    collector.set_snapshot(
        "cursor_bugbot_repos",
        {**base, "enabled": "true", "manual_only": "false"},
        float(enabled_n),
    )
    collector.set_snapshot(
        "cursor_bugbot_repos",
        {**base, "enabled": "false", "manual_only": "false"},
        float(max(0, len(CURSOR_REPOS) - enabled_n)),
    )
    # Findings snapshot: resolved ⊆ found.
    found = float(random.randint(40, 120))
    resolved = float(random.randint(10, int(found * 0.7)))
    collector.set_snapshot(
        "cursor_bugbot_issues_snapshot",
        {**base, "state": "found"},
        found,
    )
    collector.set_snapshot(
        "cursor_bugbot_issues_snapshot",
        {**base, "state": "resolved"},
        resolved,
    )
    _ = day
    _ROSTER_SEEDED = True


def _event_labels(
    base: dict[str, str],
    *,
    member: _UsageMember,
    model: str,
    conversation_id: str,
    kind: str,
    max_mode: bool,
    day: str,
    service_account: str,
) -> dict[str, str]:
    return {
        **base,
        "email": member.email,
        "model": model,
        "conversation_id": conversation_id,
        "kind": kind,
        "max_mode": "true" if max_mode else "false",
        "billing_mode": "true",
        "is_chargeable": "true",
        "is_cloud_agent": "false",
        "is_headless": "false",
        "automation_id": "none",
        "discount_pct": "0",
        "date": day,
        "service_account": service_account,
    }


def emit_cursor_usage_cycle(*, now: datetime | None = None) -> None:
    """Accrue one cycle of Usage-dashboard deltas + refresh snapshots."""
    collector = get_cursor_usage_collector()
    if collector is None:
        return
    now = now or datetime.now(timezone.utc)
    if not _ROSTER_SEEDED:
        _seed_snapshots(collector, now=now)
    else:
        # Refresh cycle scalars / limits periodically (cheap).
        if random.random() < 0.05:
            _seed_snapshots(collector, now=now)

    team_id = cursor_usage_team_id()
    base = collector.base_labels(team_id)
    day = now.date().isoformat()
    _roll_usage_day(day)
    volume = max(0.05, _env_float("SIM_CURSOR_USAGE_VOLUME", 1.0))

    active_today: set[str] = set()
    active_members = [m for m in _roster() if not m.is_idle]
    # Brief §9: date-seeded weekday/weekend multiplier on active members + event volume.
    from sim.common.day_variation import daily_activity_multiplier

    global _DAY_MULT
    day_mult = _DAY_MULT = daily_activity_multiplier(now.date())
    if day_mult < 0.999 and active_members:
        keep_n = max(1, int(round(len(active_members) * day_mult)))
        active_members = active_members[:keep_n]
    target_events = _target_events_per_day(max(1, len(active_members)))
    target_events = max(1, int(round(target_events * day_mult)))
    emits = _events_to_emit_this_cycle(now, target_events)
    cycle_pool_cost = 0.0

    for _ in range(emits):
        acquired = _acquire_conversation(now, active_members, day)
        if acquired is None:
            break
        conv, is_new = acquired
        member = conv.member
        model = conv.model
        kind = conv.kind
        max_mode = conv.max_mode
        service_account = conv.service_account
        conversation_id = conv.conversation_id
        _consume_conversation_event(conv)

        active_today.add(member.email)
        _MODEL_USERS_TODAY.setdefault(model, set()).add(member.email)
        surface = _pick_member_surface(member)
        billing_class = _pick(CURSOR_BILLING_CLASSES, CURSOR_BILLING_CLASS_WEIGHTS)
        # One event per emit — conversation_id is reused across 20–40 of these.
        event_n = 1
        if conv.fixed_event_cost_usd is not None:
            cost = round(conv.fixed_event_cost_usd, 6)
        else:
            cost = round(
                random.uniform(0.02, 1.8)
                * volume
                * (2.5 if max_mode else 1.0)
                * member.persona.spend_mult
                * _EVENT_COST_SCALE,
                6,
            )
        cycle_pool_cost += cost
        list_price = round(cost * 1.15, 6)
        units = float(random.randint(1, 12))
        ev = _event_labels(
            base,
            member=member,
            model=model,
            conversation_id=conversation_id,
            kind=kind,
            max_mode=max_mode,
            day=day,
            service_account=service_account,
        )
        collector.add_delta("cursor_events_total", ev, event_n)
        collector.add_delta("cursor_event_cost_usd", ev, cost)
        collector.add_delta("cursor_event_list_price_usd", ev, list_price)
        collector.add_delta("cursor_event_request_units_total", ev, units)
        # Brief §8 Add: token fee accompanies cost (c4c ~358 series).
        collector.add_delta(
            "cursor_event_token_fee_usd", ev, round(cost * _EVENT_TOKEN_FEE_SHARE, 6)
        )

        # Persona-skewed token mix — cache_read/request drives longSessions/shortSessions.
        base_shares = [0.45, 0.30, 0.18, 0.07]  # input, output, cache_read, cache_write
        shares = list(base_shares)
        shares[2] = max(0.02, base_shares[2] * member.persona.cache_read_mult)
        share_total = sum(shares)
        shares = [s / share_total for s in shares]
        for token_type, share in zip(CURSOR_TOKEN_TYPES, shares, strict=True):
            tok_labels = {
                **base,
                "email": member.email,
                "model": model,
                "conversation_id": conversation_id,
                "kind": kind,
                "max_mode": "true" if max_mode else "false",
                "billing_mode": "true",
                "is_chargeable": "true",
                "is_headless": "false",
                "token_type": token_type,
                "date": day,
                "service_account": service_account,
            }
            tokens = round(random.randint(200, 8000) * share * volume)
            if tokens:
                collector.add_delta("cursor_event_tokens_total", tok_labels, tokens)

        collector.accrue_snapshot(
            "cursor_requests_total",
            {
                **base,
                "email": member.email,
                "user_id": member.user_id,
                "surface": surface,
                "date": day,
            },
            event_n,
        )
        _SURFACE_USERS_TODAY.setdefault(surface, set()).add(member.email)
        collector.add_delta(
            "cursor_requests_by_class_total",
            {
                **base,
                "email": member.email,
                "user_id": member.user_id,
                "billing_class": billing_class,
                "date": day,
            },
            event_n,
        )
        collector.add_delta(
            "cursor_user_model_messages_total",
            {**base, "email": member.email, "model": model, "date": day},
            random.randint(1, 3),
        )

        suggested = random.randint(1, 6)
        accepted = random.randint(0, suggested)
        rejected = suggested - accepted
        collector.add_delta(
            "cursor_user_agent_diffs_suggested_total",
            {**base, "email": member.email, "date": day},
            suggested,
        )
        collector.add_delta(
            "cursor_user_agent_diffs_accepted_total",
            {**base, "email": member.email, "date": day},
            accepted,
        )
        collector.add_delta(
            "cursor_user_agent_diffs_rejected_total",
            {**base, "email": member.email, "date": day},
            rejected,
        )
        # Org-level agent diffs (brief §8 Add — c4c ~1 series/day).
        collector.add_delta(
            "cursor_agent_diffs_accepted_total",
            {**base, "date": day},
            accepted,
        )
        if rejected and random.random() < 0.08:
            collector.add_delta(
                "cursor_agent_diffs_rejected_total",
                {**base, "date": day},
                rejected,
            )

        tab_sugg = random.randint(2, 20)
        tab_acc = random.randint(0, tab_sugg)
        tab_rej = max(0, tab_sugg - tab_acc)
        collector.add_delta(
            "cursor_tab_suggestions_total",
            {**base, "email": member.email, "user_id": member.user_id, "date": day},
            tab_sugg,
        )
        collector.add_delta(
            "cursor_tab_accepts_total",
            {**base, "email": member.email, "user_id": member.user_id, "date": day},
            tab_acc,
        )
        collector.accrue_snapshot(
            "cursor_user_tab_rejects_total",
            {**base, "email": member.email, "date": day},
            float(tab_rej),
        )
        # Brief §8 Add: lines / rejects / extension / AI deleted lines.
        added_lines = round(random.randint(4, 40) * volume)
        deleted_lines = round(random.randint(0, 12) * volume)
        collector.add_delta(
            "cursor_lines_added_total",
            {**base, "email": member.email, "user_id": member.user_id, "date": day},
            added_lines,
        )
        if deleted_lines:
            collector.add_delta(
                "cursor_lines_deleted_total",
                {**base, "email": member.email, "user_id": member.user_id, "date": day},
                deleted_lines,
            )
        if random.random() < 0.12:
            collector.add_delta(
                "cursor_rejects_total",
                {**base, "email": member.email, "user_id": member.user_id, "date": day},
                1,
            )
        if random.random() < 0.15:
            ext = _pick(CURSOR_FILE_EXTENSIONS)
            collector.add_delta(
                "cursor_ai_change_file_lines_deleted_total",
                {**base, "email": member.email, "date": day, "file_extension": ext},
                max(1, deleted_lines),
            )
            collector.add_delta(
                "cursor_ai_change_lines_deleted_total",
                {**base, "email": member.email, "date": day},
                max(1, deleted_lines),
            )
        if random.random() < 0.10:
            ext_name = random.choice(("python", "typescript", "go", "rust", "java"))
            collector.add_delta(
                "cursor_user_extension_files_total",
                {**base, "email": member.email, "date": day, "extension": ext_name},
                random.randint(1, 3),
            )
            collector.add_delta(
                "cursor_user_extension_lines_suggested_total",
                {**base, "email": member.email, "date": day, "extension": ext_name},
                random.randint(5, 40),
            )
            collector.add_delta(
                "cursor_user_extension_lines_accepted_total",
                {**base, "email": member.email, "date": day, "extension": ext_name},
                random.randint(1, 20),
            )
            if random.random() < 0.05:
                collector.add_delta(
                    "cursor_user_extension_rejects_total",
                    {**base, "email": member.email, "date": day, "extension": ext_name},
                    1,
                )
                collector.add_delta(
                    "cursor_user_extension_lines_rejected_total",
                    {**base, "email": member.email, "date": day, "extension": ext_name},
                    random.randint(1, 4),
                )
            else:
                collector.add_delta(
                    "cursor_user_extension_accepts_total",
                    {**base, "email": member.email, "date": day, "extension": ext_name},
                    1,
                )
        if random.random() < 0.04:
            collector.add_delta(
                "cursor_member_fast_premium_requests_total",
                {**base, "email": member.email, "user_id": member.user_id, "date": day},
                random.randint(1, 5),
            )
        if service_account != "none" and random.random() < 0.2:
            collector.add_delta(
                "cursor_user_api_key_events_total",
                {**base, "email": member.email, "date": day, "event": "use"},
                1,
            )
        # Seat KPIs (Active Users / Adoption Rate) join roster to these two metrics.
        _accrue_seat_activity(
            collector,
            base=base,
            email=member.email,
            day=day,
            agent_suggested_lines=float(random.randint(8, 80)),
            agent_accepted_lines=float(random.randint(2, 40)),
            tab_suggestions=float(tab_sugg),
            tab_accepts=float(tab_acc),
        )

        repo = _pick(CURSOR_REPOS)
        direction = _pick(CURSOR_DIRECTIONS)
        commit_source = _pick(CURSOR_COMMIT_SOURCES)
        # Live ai_code_lines uses surfaces composer/tab/non_ai/unattributed more than agent.
        code_surface = (
            "non_ai"
            if random.random() < 0.28
            else _pick(("composer", "tab", "agent", "unattributed"), (0.40, 0.30, 0.20, 0.10))
        )
        lines = round(random.randint(5, 120) * volume)
        collector.add_delta(
            "cursor_ai_code_lines_total",
            {
                **base,
                "email": member.email,
                "user_id": member.user_id,
                "repo_name": repo,
                "surface": code_surface,
                "direction": direction,
                "commit_source": commit_source,
                "branch_name": random.choice(("main", "develop", "feature/sim")),
                "date": day,
                "is_primary_branch": "true" if random.random() < 0.7 else "false",
            },
            lines,
        )
        if direction == "added":
            collector.add_delta(
                "cursor_ai_code_total_lines_added_total",
                {
                    **base,
                    "email": member.email,
                    "user_id": member.user_id,
                    "repo_name": repo,
                    "date": day,
                },
                lines,
            )
            collector.accrue_snapshot(
                "cursor_ai_change_lines_added_total",
                {**base, "email": member.email, "user_id": member.user_id, "date": day},
                lines,
            )
            collector.add_delta(
                "cursor_ai_change_file_lines_added_total",
                {
                    **base,
                    "email": member.email,
                    "user_id": member.user_id,
                    "file_extension": _pick(CURSOR_FILE_EXTENSIONS),
                    "change_source": _pick(CURSOR_CHANGE_SOURCES),
                    "model": model if random.random() < 0.7 else "unattributed",
                    "date": day,
                },
                lines,
            )
            collector.add_delta(
                "cursor_accepted_lines_added_total",
                {
                    **base,
                    "email": member.email,
                    "user_id": member.user_id,
                    "date": day,
                },
                max(1, lines // 2),
            )
        else:
            collector.add_delta(
                "cursor_ai_code_total_lines_deleted_total",
                {
                    **base,
                    "email": member.email,
                    "user_id": member.user_id,
                    "repo_name": repo,
                    "date": day,
                },
                lines,
            )
            collector.add_delta(
                "cursor_accepted_lines_deleted_total",
                {
                    **base,
                    "email": member.email,
                    "user_id": member.user_id,
                    "date": day,
                },
                max(1, lines // 3),
            )

        if random.random() < 0.35:
            collector.add_delta(
                "cursor_commits_total",
                {
                    **base,
                    "email": member.email,
                    "user_id": member.user_id,
                    "repo_name": repo,
                    "branch_name": random.choice(("main", "feature/sim", "fix/bug")),
                    "date": day,
                },
                1,
            )

        applies = random.randint(1, 5)
        # Persona accept_rate — drives adoption tier (acceptRate * 40 in FE score).
        accepts = sum(1 for _ in range(applies) if random.random() < member.persona.accept_rate)
        collector.accrue_snapshot(
            "cursor_applies_total",
            {**base, "email": member.email, "user_id": member.user_id, "date": day},
            applies,
        )
        collector.accrue_snapshot(
            "cursor_accepts_total",
            {**base, "email": member.email, "user_id": member.user_id, "date": day},
            accepts,
        )

        # Conversation dimensions once per new conversation.
        # Dual-emit: legacy team-level cursor_conversation_* (no email) + cx498
        # cursor_user_conversation_* (with email). Snapshot is a delta despite the
        # name — Insights uses sum_over_time on complexity / guidanceLevels.
        if is_new:
            intent = _pick(CURSOR_CONVERSATION_DIMENSIONS["intents"])

            def _emit_conversation_dimension(dimension: str, value: str) -> None:
                team_labels = {
                    **base,
                    "dimension": dimension,
                    "value": value,
                    "date": day,
                }
                user_labels = {**team_labels, "email": member.email}
                collector.add_delta("cursor_conversation_total", team_labels, 1)
                collector.add_delta("cursor_user_conversation_total", user_labels, 1)
                collector.add_delta("cursor_user_conversation_snapshot", user_labels, 1)

            _emit_conversation_dimension("intents", intent)
            # Always classify work/complexity/guidance so Work Type + Insights populate.
            dimension_weights = {
                **_DIMENSION_WEIGHTS,
                "workTypes": _work_type_weights(now.date().toordinal()),
            }
            for dimension in ("workTypes", "complexity", "guidanceLevels"):
                _emit_conversation_dimension(
                    dimension,
                    _pick(CURSOR_CONVERSATION_DIMENSIONS[dimension], dimension_weights[dimension]),
                )
            for dimension, values in CURSOR_CONVERSATION_DIMENSIONS.items():
                if dimension in ("intents", "workTypes", "complexity", "guidanceLevels"):
                    continue
                if random.random() < 0.55:
                    _emit_conversation_dimension(dimension, _pick(values))
            # Topic Mix — mode-scoped subcategory tied to this conversation's intent + member.
            mode = CURSOR_CONVERSATION_INTENT_TO_MODE.get(intent)
            if mode:
                subs = CURSOR_CONVERSATION_SUBCATEGORIES[mode]
                weights = CURSOR_CONVERSATION_SUBCATEGORY_WEIGHTS.get(mode)
                subcat_labels = {
                    **base,
                    "mode": mode,
                    "subcategory": _pick(subs, weights),
                    "email": member.email,
                    "date": day,
                }
                collector.add_delta(
                    "cursor_conversation_subcategory_snapshot",
                    subcat_labels,
                    1,
                )
                collector.add_delta(
                    "cursor_user_conversation_subcategory_snapshot",
                    subcat_labels,
                    1,
                )
            # Align ask/plan usage pies with the same intent when applicable.
            if intent == "Ask":
                collector.add_delta(
                    "cursor_user_ask_mode_usage_total",
                    {**base, "email": member.email, "model": model, "date": day},
                    1,
                )
            elif intent == "Plan":
                collector.add_delta(
                    "cursor_user_plan_usage_total",
                    {**base, "email": member.email, "model": model, "date": day},
                    1,
                )

        if random.random() < 0.4:
            collector.add_delta(
                "cursor_user_plan_usage_total",
                {**base, "email": member.email, "model": model, "date": day},
                1,
            )
        if random.random() < 0.35:
            collector.add_delta(
                "cursor_user_ask_mode_usage_total",
                {**base, "email": member.email, "model": model, "date": day},
                1,
            )
        if random.random() < 0.45:
            collector.add_delta(
                "cursor_user_command_usage_total",
                {**base, "email": member.email, "command_name": _pick(CURSOR_COMMANDS)},
                1,
            )
        if random.random() < 0.3:
            mcp_server = _pick(CURSOR_MCP_SERVERS)
            collector.add_delta(
                "cursor_user_mcp_usage_total",
                {
                    **base,
                    "email": member.email,
                    "mcp_server_name": mcp_server,
                    "tool_name": _pick(CURSOR_MCP_TOOLS),
                    "date": day,
                },
                1,
            )
        if random.random() < 0.25:
            collector.add_delta(
                "cursor_user_skill_usage_total",
                {
                    **base,
                    "email": member.email,
                    "skill_name": _pick(CURSOR_SKILLS),
                    "date": day,
                },
                1,
            )

    # Per-surface request bursts — distinct user counts differ by surface adoption.
    # No conversation_id on these series; keep light so request volume stays realistic.
    if emits > 0 or random.random() < 0.15:
        for surface in CURSOR_CHART_SURFACES:
            candidates = [m for m in active_members if surface in m.surfaces]
            if not candidates:
                continue
            member = random.choice(candidates)
            active_today.add(member.email)
            collector.accrue_snapshot(
                "cursor_requests_total",
                {
                    **base,
                    "email": member.email,
                    "user_id": member.user_id,
                    "surface": surface,
                    "date": day,
                },
                1,
            )
            _SURFACE_USERS_TODAY.setdefault(surface, set()).add(member.email)

    # Bugbot activity (team-level — no email).
    for repo in CURSOR_REPOS:
        if random.random() < 0.55:
            prs = random.randint(1, 4)
            reviews = prs + random.randint(0, 3)
            collector.add_delta(
                "cursor_bugbot_prs_reviewed",
                {**base, "repo_name": repo, "date": day},
                prs,
            )
            collector.add_delta(
                "cursor_bugbot_pr_reviews_total",
                {**base, "repo_name": repo, "date": day},
                reviews,
            )
        for severity in CURSOR_BUGBOT_SEVERITIES:
            if random.random() < 0.4:
                found_n = random.randint(1, 8)
                for state in CURSOR_BUGBOT_ISSUE_STATES:
                    n = found_n if state == "found" else random.randint(0, found_n)
                    if not n:
                        continue
                    collector.add_delta(
                        "cursor_bugbot_issues_total",
                        {
                            **base,
                            "repo_name": repo,
                            "severity": severity,
                            "state": state,
                            "date": day,
                        },
                        n,
                    )

    # Daily spend and cycle gross are stable per-member levels (persona-weighted, +-10% per day) so the
    # Spend tab shows no spikes; only the trimmed active_members list moves with the day multiplier.
    for member in active_members:
        daily_jitter = 0.9 + 0.2 * (
            int(hashlib.sha256(f"cursor-spend:{member.email}:{day}".encode()).hexdigest()[:4], 16)
            / 0xFFFF
        )
        collector.set_snapshot(
            "cursor_member_daily_spend_usd",
            {
                **base,
                "email": member.email,
                "user_id": member.user_id,
                "name": member.name,
                "group_id": member.group_id,
                "group_name": member.group_name,
                "date": day,
                "is_former": "false",
                "is_unassigned": "true" if member.is_unassigned else "false",
            },
            round(
                _DAILY_SPEND_PER_MEMBER_USD
                * max(1.0, day_mult)
                * member.persona.spend_mult
                * daily_jitter,
                4,
            ),
        )
    for member in _roster():
        if member.is_idle:
            continue
        gross = (
            member.monthly_limit_usd * _LIMITED_SEAT_SPEND_RATIO
            if member.may_exceed_limit
            else _CYCLE_GROSS_PER_MEMBER_USD * member.persona.spend_mult
        )
        gross_labels = {
            **base,
            "email": member.email,
            "user_id": member.user_id,
            "name": member.name,
            "role": member.role,
        }
        collector.set_snapshot("cursor_member_spend_gross_usd", gross_labels, round(gross, 4))
        collector.set_snapshot(
            "cursor_member_spend_overage_usd",
            gross_labels,
            round(max(0.0, gross - member.monthly_limit_usd), 4),
        )

    # member_active flags (Idle Seats drawer / insights). KPI Active Users / Adoption Rate use
    # cursor_user_lines_total{source=agent,outcome=suggested} + cursor_user_tab_suggestions_total.
    global _SOFT_ACTIVE_DECIDED, _SOFT_ACTIVE_TODAY
    if not _SOFT_ACTIVE_DECIDED:
        for m in _roster():
            if m.is_idle:
                continue
            if random.random() < m.persona.active_day_p * 0.25:
                _SOFT_ACTIVE_TODAY.add(m.email)
        # Soft-active seats must also get lines/tab so Adoption KPIs count them (not member_active).
        for email in _SOFT_ACTIVE_TODAY:
            _accrue_seat_activity(
                collector,
                base=base,
                email=email,
                day=day,
                agent_suggested_lines=float(random.randint(12, 60)),
                agent_accepted_lines=float(random.randint(4, 30)),
                tab_suggestions=float(random.randint(5, 25)),
                tab_accepts=float(random.randint(1, 12)),
            )
        _SOFT_ACTIVE_DECIDED = True

    for m in _roster():
        if m.is_idle:
            val = 0.0
        elif m.email in active_today or m.email in _SOFT_ACTIVE_TODAY:
            val = 1.0
        else:
            val = 0.0
        collector.set_snapshot(
            "cursor_member_active",
            {
                **base,
                "email": m.email,
                "user_id": m.user_id,
                "date": day,
                "client_version": m.client_version,
            },
            val,
        )

    # Team DAU gauges (Adoption Active Users KPI + surface chips) — date-labeled daily levels.
    dau_total = float(len(active_today | _SOFT_ACTIVE_TODAY))
    dau_cli = float(len(_SURFACE_USERS_TODAY.get("cli", set())))
    if dau_cli <= 0 and dau_total:
        dau_cli = float(max(1, int(dau_total * 0.10)))
    # Cloud agent: ~18% of active seats (no dedicated chart surface).
    dau_cloud = float(max(1, int(dau_total * 0.18))) if dau_total else 0.0
    dau_bugbot = float(len(_SURFACE_USERS_TODAY.get("bugbot", set())))
    if dau_bugbot <= 0 and dau_total:
        dau_bugbot = float(max(1, int(dau_total * 0.12)))
    for metric_name, value in (
        ("cursor_active_users_total", dau_total),
        ("cursor_active_users_cli", dau_cli),
        ("cursor_active_users_cloud_agent", dau_cloud),
        ("cursor_active_users_bugbot", dau_bugbot),
    ):
        collector.set_snapshot(metric_name, {**base, "date": day}, value)

    # Today + yesterday only: each date-labeled series is sampled on at most two UTC days. Earlier
    # days live in the TSDB from the days the sim already ran.
    keep_dates = {(now.date() - timedelta(days=i)).isoformat() for i in range(0, 2)}
    collector.prune_snapshots_by_date(keep_dates=keep_dates)

    # Restate bugbot snapshots every cycle for last_over_time widgets.
    enabled_n = max(1, int(len(CURSOR_REPOS) * 0.6))
    collector.set_snapshot(
        "cursor_bugbot_repos",
        {**base, "enabled": "true", "manual_only": "false"},
        float(enabled_n),
    )
    collector.set_snapshot(
        "cursor_bugbot_repos",
        {**base, "enabled": "false", "manual_only": "false"},
        float(max(0, len(CURSOR_REPOS) - enabled_n)),
    )
    found = float(random.randint(40, 120))
    resolved = float(random.randint(10, int(found * 0.7)))
    collector.set_snapshot(
        "cursor_bugbot_issues_snapshot",
        {**base, "state": "found"},
        found,
    )
    collector.set_snapshot(
        "cursor_bugbot_issues_snapshot",
        {**base, "state": "resolved"},
        resolved,
    )

    collector.clear_snapshots_with_prefix("cursor_model_distinct_users")
    for model, emails in _MODEL_USERS_TODAY.items():
        collector.set_snapshot(
            "cursor_model_distinct_users",
            {**base, "model": model},
            float(len(emails)),
        )

    # Brief §8 remaining Add metrics (c4c label shapes): daily amounts, once per UTC day. Waits until
    # 10:00 UTC so active_today is populated before the first (11:00) sample.
    global _BRIEF_EXTRA_DAY
    if _BRIEF_EXTRA_DAY != day and now.hour >= 10:
        _BRIEF_EXTRA_DAY = day
        _emit_cursor_brief_extra_metrics(
            collector,
            base=base,
            day=day,
            active_today=active_today | _SOFT_ACTIVE_TODAY,
        )

    # Annual Budget: accrue this cycle's event $ into pooled usage, then restate gauges.
    _accrue_org_pool_usage(cycle_pool_cost)
    _refresh_org_pool_snapshots(collector, now=now)


def _emit_cursor_brief_extra_metrics(
    collector: CursorUsageCollector,
    *,
    base: dict[str, str],
    day: str,
    active_today: set[str],
) -> None:
    """Emit remaining c4c-parity metrics from brief §8 Add rows."""
    org = cursor_usage_organization()
    team_name = cursor_usage_team_name()
    org_base = {**base, "organization": org, "team_name": team_name, "date": day}

    # Org agent line totals (green=added / red=deleted).
    green_acc = random.randint(800, 4000) * 10
    red_acc = random.randint(100, 900) * 10
    collector.add_delta("cursor_agent_green_lines_accepted_total", org_base, green_acc)
    collector.add_delta("cursor_agent_red_lines_accepted_total", org_base, red_acc)
    if random.random() < 0.08:
        collector.add_delta(
            "cursor_agent_green_lines_rejected_total",
            org_base,
            max(1, int(green_acc * 0.001)),
        )
    if random.random() < 0.05:
        collector.add_delta(
            "cursor_agent_red_lines_rejected_total",
            org_base,
            max(1, int(red_acc * 0.001)),
        )

    for source in ("COMPOSER", "TAB"):
        collector.add_delta(
            "cursor_ai_changes_total",
            {**org_base, "source": source},
            random.randint(200, 1200),
        )

    # Commit-range lines: keep series near ~60/day (sample a few active users × repos).
    members = [m for m in _roster() if m.email in active_today][:8]
    for m in members:
        if random.random() > 0.35:
            continue
        collector.add_delta(
            "cursor_ai_code_commit_range_lines_total",
            {
                **org_base,
                "email": m.email,
                "user_id": m.user_id,
                "repo_name": _pick(CURSOR_REPOS),
                "branch_name": random.choice(("main", "develop", "feature/sim")),
                "commit_source": _pick(CURSOR_COMMIT_SOURCES),
                "model": _pick(CURSOR_USAGE_MODELS, CURSOR_USAGE_MODEL_WEIGHTS),
                "operation_type": random.choice(("addition", "deletion")),
            },
            random.randint(25, 400),
        )

    if random.random() < 0.25:
        collector.add_delta(
            "cursor_audit_events_total",
            {
                **org_base,
                "event_type": random.choice(
                    ("login", "logout", "team_role_updated", "api_key_created", "grok_bot_created")
                ),
                "user_email": random.choice(list(active_today) or ["sim@coralogix.com"]),
                "ip_address": f"10.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 254)}",
            },
            1,
        )

    # Dry-run reviews are 36-60% of the total, above the unpublished-reviews insight's 30% floor.
    repo = _pick(CURSOR_REPOS)
    for dry_run, reviews in (("false", random.randint(4, 7)), ("true", random.randint(4, 6))):
        bb = {
            **org_base,
            "repo_name": repo if repo.startswith("github.com/") else f"github.com/{repo}",
            "dry_run": dry_run,
            "publication_status": "pending" if dry_run == "true" else "posted",
        }
        collector.add_delta("cursor_bugbot_reviews_total", bb, reviews)
        collector.add_delta("cursor_bugbot_review_bugs_found_total", bb, random.randint(0, 4))
        collector.add_delta(
            "cursor_bugbot_review_cost_usd",
            bb,
            round(random.uniform(0.5, 4.0) * reviews, 4),
        )

    # Client versions — restated daily snapshot (~22 series on c4c).
    versions = (
        CURSOR_CLIENT_VERSION_LATEST,
        CURSOR_CLIENT_VERSION_ONE_BEHIND,
        CURSOR_CLIENT_VERSION_STALE,
        "0.68.1",
        "0.67.0",
        "1.0.0",
    )
    version_counts: dict[str, int] = {v: 0 for v in versions}
    for m in _roster():
        if m.email not in active_today:
            continue
        version_counts[m.client_version] = version_counts.get(m.client_version, 0) + 1
    for ver, n in version_counts.items():
        if n <= 0:
            continue
        collector.set_snapshot(
            "cursor_client_version_users",
            {**org_base, "client_version": ver},
            float(n),
        )

    # Org conversation snapshots (no email) — mirror user conversation dimensions.
    for dim, values in CURSOR_CONVERSATION_DIMENSIONS.items():
        for value in values[:4]:
            collector.set_snapshot(
                "cursor_conversation_snapshot",
                {**org_base, "dimension": dim, "value": value},
                float(random.randint(5, 80)),
            )
    for intent in ("Ask", "Agent", "Edit", "Debug"):
        collector.set_snapshot(
            "cursor_conversation_intent_top_snapshot",
            {**org_base, "intent": intent},
            float(random.randint(20, 200)),
        )
        for m in members[:6]:
            if random.random() > 0.4:
                continue
            collector.set_snapshot(
                "cursor_user_conversation_intent_top_snapshot",
                {**org_base, "email": m.email, "intent": intent},
                float(random.randint(1, 40)),
            )

    if random.random() < 0.1:
        gid, gname = CURSOR_GROUPS[random.randrange(len(CURSOR_GROUPS))]
        collector.add_delta(
            "cursor_directory_group_member_added_total",
            {**org_base, "group_id": gid, "group_name": gname},
            1,
        )


def reset_cursor_usage_runtime_for_tests() -> None:
    """Test helper — clear module state."""
    global _ROSTER, _MODEL_USERS_TODAY, _ROSTER_SEEDED
    global _OPEN_CONVERSATIONS, _USAGE_DAY, _CONVERSATIONS_STARTED_TODAY
    global _EVENTS_EMITTED_TODAY, _EVENTS_BY_USER_TODAY
    global _ORG_POOL_LIMIT_USD, _ORG_POOL_USAGE_USD, _ORG_POOL_SEEDED
    global _SURFACE_USERS_TODAY, _SOFT_ACTIVE_TODAY, _SOFT_ACTIVE_DECIDED, _BRIEF_EXTRA_DAY
    global _DAY_MULT
    from sim.cursor.usage_v2.collector import reset_cursor_usage_collector_for_tests

    _ROSTER = None
    _MODEL_USERS_TODAY = {}
    _ROSTER_SEEDED = False
    _ORG_POOL_LIMIT_USD = None
    _ORG_POOL_USAGE_USD = None
    _ORG_POOL_SEEDED = False
    _OPEN_CONVERSATIONS = []
    _USAGE_DAY = ""
    _CONVERSATIONS_STARTED_TODAY = 0
    _EVENTS_EMITTED_TODAY = 0
    _EVENTS_BY_USER_TODAY = {}
    _SURFACE_USERS_TODAY = {}
    _SOFT_ACTIVE_TODAY = set()
    _SOFT_ACTIVE_DECIDED = False
    _BRIEF_EXTRA_DAY = ""
    _DAY_MULT = 1.0
    reset_cursor_usage_collector_for_tests()
