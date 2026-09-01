"""Emit synthetic Cursor Usage ``cursor_*`` activity for the Admin Usage dashboard."""

from __future__ import annotations

import hashlib
import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

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
    CURSOR_CLIENT_VERSIONS,
    CURSOR_COMMANDS,
    CURSOR_COMMIT_SOURCES,
    CURSOR_CONVERSATION_DIMENSIONS,
    CURSOR_DIRECTIONS,
    CURSOR_FILE_EXTENSIONS,
    CURSOR_GROUPS,
    CURSOR_MCP_SERVERS,
    CURSOR_MCP_TOOLS,
    CURSOR_REPOS,
    CURSOR_ROLES,
    CURSOR_SERVICE_ACCOUNTS,
    CURSOR_SKILLS,
    CURSOR_SURFACE_WEIGHTS,
    CURSOR_SURFACES,
    CURSOR_TOKEN_TYPES,
    CURSOR_USAGE_MODEL_WEIGHTS,
    CURSOR_USAGE_MODELS,
    cursor_usage_roster_size,
    cursor_usage_team_id,
)


def _pick(items: tuple[str, ...], weights: tuple[float, ...] | None = None) -> str:
    if weights is None:
        return random.choice(items)
    return random.choices(items, weights=weights, k=1)[0]


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


def _stable_user_id(email: str) -> str:
    digest = hashlib.sha256(f"cursor-usage:{email}".encode()).hexdigest()[:26]
    return f"user_{digest}"


def _stable_limit_usd(email: str) -> float:
    """Deterministic per-member monthly limit in the $500–$1700 band."""
    digest = hashlib.sha256(f"cursor-limit:{email}".encode()).hexdigest()
    # 500..1700 inclusive in $10 steps.
    bucket = int(digest[:8], 16) % 121  # 0..120
    return float(500 + bucket * 10)


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
    members: list[_UsageMember] = []
    for rank, idx in enumerate(ordered[:n]):
        row = _CORALOGIX_TEAM_USERS[idx]
        email = row["user.email"]
        gid, gname = CURSOR_GROUPS[rank % len(CURSOR_GROUPS)]
        is_unassigned = gid == "unassigned"
        # ~10% of the roster may exceed their monthly limit.
        overage_slots = max(1, round(n * 0.10))
        members.append(
            _UsageMember(
                email=email,
                user_id=_stable_user_id(email),
                name=row.get("user.name", email.split("@", 1)[0]),
                role=CURSOR_ROLES[rank % len(CURSOR_ROLES)],
                group_id=gid,
                group_name=gname,
                is_unassigned=is_unassigned,
                monthly_limit_usd=_stable_limit_usd(email),
                client_version=_pick(CURSOR_CLIENT_VERSIONS),
                may_exceed_limit=rank < overage_slots,
            )
        )
    return members


_ROSTER: list[_UsageMember] | None = None
_CYCLE_GROSS: dict[str, float] = {}
_SPEND_CAPS: dict[str, float] = {}
_MODEL_USERS_TODAY: dict[str, set[str]] = {}
_ROSTER_SEEDED = False


def _roster() -> list[_UsageMember]:
    global _ROSTER
    if _ROSTER is None:
        _ROSTER = _build_roster()
    return _ROSTER


def _spend_cap_for(member: _UsageMember) -> float:
    """Stable per-member gross cap: under-limit for 90%, slightly over for ~10%."""
    if member.email not in _SPEND_CAPS:
        if member.may_exceed_limit:
            _SPEND_CAPS[member.email] = member.monthly_limit_usd * random.uniform(1.08, 1.35)
        else:
            digest = hashlib.sha256(f"cursor-cap:{member.email}".encode()).hexdigest()
            frac = 0.35 + (int(digest[:4], 16) % 58) / 100.0  # 0.35..0.92
            _SPEND_CAPS[member.email] = member.monthly_limit_usd * frac
    return _SPEND_CAPS[member.email]


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

    seen_groups: set[str] = set()
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
        if m.group_id not in seen_groups:
            seen_groups.add(m.group_id)
            collector.set_snapshot(
                "cursor_group_members",
                {
                    **base,
                    "group_id": m.group_id,
                    "group_name": m.group_name,
                    "is_unassigned": "true" if m.is_unassigned else "false",
                },
                1.0,
            )

    collector.set_snapshot(
        "cursor_org_team_membership_info",
        {
            **base,
            "organization": "coralogix",
            "team_name": "Coralogix Engineering",
            "team_role": "owner",
        },
        1.0,
    )

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
    emits = max(1, _env_int("SIM_CURSOR_USAGE_EMITS_PER_CYCLE", 6))
    volume = max(0.05, _env_float("SIM_CURSOR_USAGE_VOLUME", 1.0))

    active_today: set[str] = set()

    for _ in range(emits):
        member = random.choice(_roster())
        active_today.add(member.email)
        model = _pick(CURSOR_USAGE_MODELS, CURSOR_USAGE_MODEL_WEIGHTS)
        _MODEL_USERS_TODAY.setdefault(model, set()).add(member.email)
        surface = _pick(CURSOR_SURFACES, CURSOR_SURFACE_WEIGHTS)
        kind = _pick(CURSOR_BILLING_KINDS, CURSOR_BILLING_KIND_WEIGHTS)
        billing_class = _pick(CURSOR_BILLING_CLASSES, CURSOR_BILLING_CLASS_WEIGHTS)
        # ~15% API-key / automation traffic gets a real service_account (not "none").
        if billing_class == "api_key" or kind == "API Key" or random.random() < 0.12:
            service_account = _pick(tuple(a for a in CURSOR_SERVICE_ACCOUNTS if a != "none"))
        else:
            service_account = "none"
        max_mode = random.random() < 0.12
        conversation_id = str(uuid.uuid4())
        event_n = max(1, int(random.randint(1, 4) * volume))
        cost = round(random.uniform(0.02, 1.8) * volume * (2.5 if max_mode else 1.0), 4)
        list_price = round(cost * 1.15, 4)
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

        for token_type, share in zip(
            CURSOR_TOKEN_TYPES,
            (0.45, 0.30, 0.18, 0.07),
            strict=True,
        ):
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
            tokens = int(random.randint(200, 8000) * share * volume)
            if tokens:
                collector.add_delta("cursor_event_tokens_total", tok_labels, tokens)

        collector.add_delta(
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

        tab_sugg = random.randint(2, 20)
        tab_acc = random.randint(0, tab_sugg)
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

        repo = _pick(CURSOR_REPOS)
        direction = _pick(CURSOR_DIRECTIONS)
        commit_source = _pick(CURSOR_COMMIT_SOURCES)
        # Live ai_code_lines uses surfaces composer/tab/non_ai/unattributed more than agent.
        code_surface = (
            "non_ai"
            if random.random() < 0.28
            else _pick(("composer", "tab", "agent", "unattributed"), (0.40, 0.30, 0.20, 0.10))
        )
        lines = int(random.randint(5, 120) * volume)
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
            collector.add_delta(
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
        accepts = random.randint(0, applies)
        collector.add_delta(
            "cursor_applies_total",
            {**base, "email": member.email, "user_id": member.user_id, "date": day},
            applies,
        )
        collector.add_delta(
            "cursor_accepts_total",
            {**base, "email": member.email, "user_id": member.user_id, "date": day},
            accepts,
        )

        collector.add_delta(
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
            cost,
        )
        # Cap cycle gross so ~90% stay under limit; ~10% (may_exceed_limit) go over.
        prev = _CYCLE_GROSS.get(member.email)
        cap = _spend_cap_for(member)
        if prev is None:
            # Seed overage users already past limit so demos show ~10% over immediately.
            prev = member.monthly_limit_usd * 1.1 if member.may_exceed_limit else 0.0
        step = cost * (random.uniform(2.0, 6.0) if member.may_exceed_limit else 1.0)
        gross = min(prev + step, cap)
        _CYCLE_GROSS[member.email] = gross
        collector.set_snapshot(
            "cursor_member_spend_gross_usd",
            {
                **base,
                "email": member.email,
                "user_id": member.user_id,
                "name": member.name,
                "role": member.role,
            },
            round(gross, 4),
        )
        overage = max(0.0, gross - member.monthly_limit_usd)
        collector.set_snapshot(
            "cursor_member_spend_overage_usd",
            {
                **base,
                "email": member.email,
                "user_id": member.user_id,
                "name": member.name,
                "role": member.role,
            },
            round(overage, 4),
        )

        # Conversation dimensions (team-level — no email).
        for dimension, values in CURSOR_CONVERSATION_DIMENSIONS.items():
            if random.random() < 0.55:
                collector.add_delta(
                    "cursor_conversation_total",
                    {**base, "dimension": dimension, "value": _pick(values), "date": day},
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

    # Active flags for members touched this cycle (+ a few idle roster rows stay unset).
    for m in _roster():
        if m.email in active_today or random.random() < 0.15:
            collector.set_snapshot(
                "cursor_member_active",
                {
                    **base,
                    "email": m.email,
                    "user_id": m.user_id,
                    "date": day,
                    "client_version": m.client_version,
                },
                1.0,
            )

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


def reset_cursor_usage_runtime_for_tests() -> None:
    """Test helper — clear module state."""
    global _ROSTER, _CYCLE_GROSS, _SPEND_CAPS, _MODEL_USERS_TODAY, _ROSTER_SEEDED
    from sim.cursor.usage_v2.collector import reset_cursor_usage_collector_for_tests

    _ROSTER = None
    _CYCLE_GROSS = {}
    _SPEND_CAPS = {}
    _MODEL_USERS_TODAY = {}
    _ROSTER_SEEDED = False
    reset_cursor_usage_collector_for_tests()
