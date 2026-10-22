"""Remaining GitHub Copilot collector metrics (onlineboutique brief §7 Add rows).

Registered via ``GitHubCopilotCollector`` counters/gauges without bloating
``CopilotCollectorMetrics``. Label shapes copied from cxai (eu2) Oct 2026.
"""

from __future__ import annotations

import hashlib
import os
import random
from datetime import datetime, timezone

from sim.common.day_variation import date_unit_interval
from sim.common.env import _env_float, _env_int

_SEAT_PRICE_USD = 19.0
_HEAVY_TOKEN_MULT = 100
_DORMANT_TEAM = "legacy-tools"
_TEAMS = ("engineering", "platform", "data", "security", "product")
# Few IDEs/languages keep the per-IDE/per-language series count near the real tenant's.
_IDE_BY_USER = ("vscode", "vscode", "intellij", "pycharm", "neovim")
_FEATURES = (
    "agent_edit",
    "chat_inline",
    "chat_panel_agent_mode",
    "chat_panel_ask_mode",
    "code_completion",
)
_PHASES = ("No Cohort", "Phase 1", "Phase 3")
_COMMENT_TYPES = (
    "accessibility",
    "bug",
    "documentation",
    "error_message",
    "operational_implications",
    "performance",
    "spelling",
)
_LANGUAGES = ("Python", "TypeScript", "Go", "Java", "Rust", "Ruby")


def _stable_hash(*parts: str) -> int:
    return int(hashlib.sha256(":".join(parts).encode()).hexdigest()[:8], 16)


def copilot_user_bucket(purpose: str, login: str) -> int:
    """Stable 0-99 bucket per user and purpose, so a user's role never drifts between restarts."""
    return _stable_hash("copilot", purpose, login) % 100


def copilot_seat_profile(login: str) -> str:
    """``heavy`` (one token outlier), ``idle`` (unused seat) or ``active``."""
    bucket = copilot_user_bucket("seat", login)
    if bucket == 0:
        return "heavy"
    return "idle" if bucket <= 40 else "active"


def copilot_token_mult(login: str) -> int:
    return _HEAVY_TOKEN_MULT if copilot_seat_profile(login) == "heavy" else 1


def copilot_default_model() -> str:
    return os.environ.get("SIM_COPILOT_DEFAULT_MODEL", "claude-sonnet-4-6").strip() or "claude-sonnet-4-6"


def copilot_user_language(user_l: dict[str, str]) -> str:
    return _LANGUAGES[_stable_hash("language", _user_id_for(user_l)) % len(_LANGUAGES)]


def _copilot_user_ide(user_l: dict[str, str]) -> str:
    return _IDE_BY_USER[_stable_hash("ide", _user_id_for(user_l)) % len(_IDE_BY_USER)]


def _user_id_for(user_l: dict[str, str]) -> str:
    """Stable numeric-looking user_id for cxai-shaped user_*_by_* labels."""
    key = (
        str(user_l.get("user_login") or "")
        or str(user_l.get("user_email") or "")
        or "unknown"
    )
    digest = hashlib.sha256(f"copilot-uid:{key}".encode()).hexdigest()
    return str(int(digest[:8], 16) % 90_000_000 + 1_000_000)


def _user_ext(user_l: dict[str, str]) -> dict[str, str]:
    return {
        "organization": user_l["organization"],
        "user_email": user_l.get("user_email", ""),
        "user_login": user_l.get("user_login", ""),
        "user_name": user_l.get("user_name", ""),
        "user_id": _user_id_for(user_l),
    }


def emit_copilot_extra_session(
    collector,
    *,
    org: str,
    user_l: dict[str, str],
    generations: int,
    acceptances: int,
    interactions: int,
    loc_added: int,
    loc_deleted: int,
    loc_suggested: int,
) -> None:
    """Increment brief §7 Add counters for one session."""
    today = datetime.now(timezone.utc).date().isoformat()
    feature = _FEATURES[_stable_hash("feature", org, today) % len(_FEATURES)]
    ide = _copilot_user_ide(user_l)
    language = copilot_user_language(user_l)
    model = copilot_default_model()
    u = _user_ext(user_l)
    # Only a fraction of users get the per-dimension breakdown series (keeps series near the real tenant's).
    breakdown = copilot_user_bucket("breakdown", u["user_login"]) < _env_float(
        "SIM_COPILOT_USER_BREAKDOWN_FRAC", 0.2
    ) * 100
    user_breakdown_inc = collector._inc_counter if breakdown else (lambda *_: None)

    # Org activity_count by_* (★ generation/acceptance already renamed on primary handles).
    if generations:
        collector._inc_counter(
            "github_copilot_org_code_generation_activity_count_by_feature",
            {"organization": org, "feature": feature},
            generations,
        )
        collector._inc_counter(
            "github_copilot_org_code_generation_activity_count_by_ide",
            {"organization": org, "ide": ide},
            generations,
        )
        collector._inc_counter(
            "github_copilot_org_code_generation_activity_count_by_language_feature",
            {"organization": org, "language": language, "feature": feature},
            generations,
        )
        collector._inc_counter(
            "github_copilot_org_code_generation_activity_count_by_model_feature",
            {"organization": org, "model": model, "feature": feature},
            generations,
        )
        user_breakdown_inc(
            "github_copilot_user_code_generation_activity_count_by_feature",
            {**u, "feature": feature},
            generations,
        )
        user_breakdown_inc(
            "github_copilot_user_code_generation_activity_count_by_ide",
            {**u, "ide": ide},
            generations,
        )
        user_breakdown_inc(
            "github_copilot_user_code_generation_activity_count_by_language_feature",
            {**u, "language": language, "feature": feature},
            generations,
        )
        user_breakdown_inc(
            "github_copilot_user_code_generation_activity_count_by_model_feature",
            {**u, "model": model, "feature": feature},
            generations,
        )

    if acceptances:
        collector._inc_counter(
            "github_copilot_org_code_acceptance_activity_count_by_feature",
            {"organization": org, "feature": feature},
            acceptances,
        )
        collector._inc_counter(
            "github_copilot_org_code_acceptance_activity_count_by_ide",
            {"organization": org, "ide": ide},
            acceptances,
        )
        collector._inc_counter(
            "github_copilot_org_code_acceptance_activity_count_by_language_feature",
            {"organization": org, "language": language, "feature": feature},
            acceptances,
        )
        user_breakdown_inc(
            "github_copilot_user_code_acceptance_activity_count_by_feature",
            {**u, "feature": feature},
            acceptances,
        )
        user_breakdown_inc(
            "github_copilot_user_code_acceptance_activity_count_by_ide",
            {**u, "ide": ide},
            acceptances,
        )
        user_breakdown_inc(
            "github_copilot_user_code_acceptance_activity_count_by_language_feature",
            {**u, "language": language, "feature": feature},
            acceptances,
        )

    if interactions:
        user_breakdown_inc(
            "github_copilot_user_user_initiated_interaction_count_by_feature",
            {**u, "feature": feature},
            interactions,
        )
        user_breakdown_inc(
            "github_copilot_user_user_initiated_interaction_count_by_ide",
            {**u, "ide": ide},
            interactions,
        )

    # LOC org + user extras.
    if loc_added:
        collector._inc_counter(
            "github_copilot_org_loc_added_sum", {"organization": org}, loc_added
        )
        collector._inc_counter(
            "github_copilot_org_loc_added_sum_by_feature",
            {"organization": org, "feature": feature},
            loc_added,
        )
        collector._inc_counter(
            "github_copilot_org_loc_added_sum_by_ide",
            {"organization": org, "ide": ide},
            loc_added,
        )
        collector._inc_counter(
            "github_copilot_org_loc_added_sum_by_model_feature",
            {"organization": org, "model": model, "feature": feature},
            loc_added,
        )
        user_breakdown_inc(
            "github_copilot_user_loc_added_sum_by_feature",
            {**u, "feature": feature},
            loc_added,
        )
        user_breakdown_inc(
            "github_copilot_user_loc_added_sum_by_ide",
            {**u, "ide": ide},
            loc_added,
        )
        user_breakdown_inc(
            "github_copilot_user_loc_added_sum_by_language_feature",
            {**u, "language": language, "feature": feature},
            loc_added,
        )
        user_breakdown_inc(
            "github_copilot_user_loc_added_sum_by_model_feature",
            {**u, "model": model, "feature": feature},
            loc_added,
        )

    if loc_deleted:
        collector._inc_counter(
            "github_copilot_org_loc_deleted_sum", {"organization": org}, loc_deleted
        )
        collector._inc_counter(
            "github_copilot_org_loc_deleted_sum_by_feature",
            {"organization": org, "feature": feature},
            loc_deleted,
        )
        collector._inc_counter(
            "github_copilot_org_loc_deleted_sum_by_ide",
            {"organization": org, "ide": ide},
            loc_deleted,
        )
        collector._inc_counter(
            "github_copilot_user_loc_deleted_sum", u, loc_deleted
        )
        user_breakdown_inc(
            "github_copilot_user_loc_deleted_sum_by_feature",
            {**u, "feature": feature},
            loc_deleted,
        )
        user_breakdown_inc(
            "github_copilot_user_loc_deleted_sum_by_ide",
            {**u, "ide": ide},
            loc_deleted,
        )
        user_breakdown_inc(
            "github_copilot_user_loc_deleted_sum_by_language_feature",
            {**u, "language": language, "feature": feature},
            loc_deleted,
        )

    if loc_suggested:
        collector._inc_counter(
            "github_copilot_org_loc_suggested_to_add_sum_by_feature",
            {"organization": org, "feature": feature},
            loc_suggested,
        )
        collector._inc_counter(
            "github_copilot_org_loc_suggested_to_add_sum_by_ide",
            {"organization": org, "ide": ide},
            loc_suggested,
        )
        collector._inc_counter(
            "github_copilot_org_loc_suggested_to_add_sum_by_language_feature",
            {"organization": org, "language": language, "feature": feature},
            loc_suggested,
        )
        collector._inc_counter(
            "github_copilot_user_loc_suggested_to_add_sum", u, loc_suggested
        )
        user_breakdown_inc(
            "github_copilot_user_loc_suggested_to_add_sum_by_feature",
            {**u, "feature": feature},
            loc_suggested,
        )
        user_breakdown_inc(
            "github_copilot_user_loc_suggested_to_add_sum_by_ide",
            {**u, "ide": ide},
            loc_suggested,
        )
        user_breakdown_inc(
            "github_copilot_user_loc_suggested_to_add_sum_by_language_feature",
            {**u, "language": language, "feature": feature},
            loc_suggested,
        )

    # suggested_to_delete mostly 0 on cxai — emit rarely.
    if random.random() < 0.05 and loc_deleted:
        collector._inc_counter(
            "github_copilot_org_loc_suggested_to_delete_sum",
            {"organization": org},
            loc_deleted,
        )
        collector._inc_counter(
            "github_copilot_org_loc_suggested_to_delete_sum_by_feature",
            {"organization": org, "feature": feature},
            loc_deleted,
        )
        collector._inc_counter(
            "github_copilot_org_loc_suggested_to_delete_sum_by_ide",
            {"organization": org, "ide": ide},
            loc_deleted,
        )
        collector._inc_counter(
            "github_copilot_org_loc_suggested_to_delete_sum_by_language_feature",
            {"organization": org, "language": language, "feature": feature},
            loc_deleted,
        )
        user_breakdown_inc(
            "github_copilot_user_loc_suggested_to_delete_sum", u, loc_deleted
        )
        user_breakdown_inc(
            "github_copilot_user_loc_suggested_to_delete_sum_by_feature",
            {**u, "feature": feature},
            loc_deleted,
        )
        user_breakdown_inc(
            "github_copilot_user_loc_suggested_to_delete_sum_by_ide",
            {**u, "ide": ide},
            loc_deleted,
        )
        user_breakdown_inc(
            "github_copilot_user_loc_suggested_to_delete_sum_by_language_feature",
            {**u, "language": language, "feature": feature},
            loc_deleted,
        )


def emit_copilot_team_membership(collector, *, org: str, user_l: dict[str, str], profile: str) -> None:
    """★ user_team_membership — cxai labels: organization, team_id, team_slug, user_id, user_login.

    ~3.3 teams per user; idle seats also join the dormant team so one team is mostly unused.
    """
    u = _user_ext(user_l)
    login = u["user_login"] or "unknown"
    n_teams = max(1, _env_int("SIM_COPILOT_TEAMS_PER_USER", 3)) + (
        1 if copilot_user_bucket("teams", login) < 30 else 0
    )
    first_team = copilot_user_bucket("team-start", login) % len(_TEAMS)
    slugs = [_TEAMS[(first_team + i) % len(_TEAMS)] for i in range(n_teams)]
    if profile == "idle":
        slugs[0] = _DORMANT_TEAM
    for slug in slugs:
        collector._set_gauge(
            "github_copilot_user_team_membership",
            {
                "organization": org,
                "team_id": str(10_000_000 + _stable_hash("team", org, slug) % 9_000_000),
                "team_slug": slug,
                "user_id": u["user_id"],
                "user_login": login,
            },
            1.0,
        )


def emit_copilot_extra_gauges(
    collector,
    *,
    org: str,
    dau: int,
    wau: int,
    mau: int,
) -> None:
    """Refresh brief §7 Add gauges (adoption, PR extras, monthly agent/chat)."""
    # Adoption phases (~3 series).
    engaged = max(1.0, float(dau))
    collector._set_gauge(
        "github_copilot_org_adoption_phase_engaged_users",
        {"organization": org, "phase": "Phase 1"},
        engaged * 0.45,
    )
    collector._set_gauge(
        "github_copilot_org_adoption_phase_engaged_users",
        {"organization": org, "phase": "Phase 3"},
        engaged * 0.40,
    )
    collector._set_gauge(
        "github_copilot_org_adoption_phase_engaged_users",
        {"organization": org, "phase": "No Cohort"},
        engaged * 0.15,
    )
    for phase, avg_accept, avg_gen, avg_loc, avg_ui in (
        ("Phase 1", 1.8, 20.0, 4.0, 0.12),
        ("Phase 3", 1.4, 16.0, 3.2, 0.08),
        ("No Cohort", 0.4, 4.0, 0.8, 0.02),
    ):
        pl = {"organization": org, "phase": phase}
        collector._set_gauge(
            "github_copilot_org_adoption_phase_avg_code_acceptance_activities",
            pl,
            avg_accept,
        )
        collector._set_gauge(
            "github_copilot_org_adoption_phase_avg_code_generation_activities",
            pl,
            avg_gen,
        )
        collector._set_gauge(
            "github_copilot_org_adoption_phase_avg_loc_added",
            pl,
            avg_loc,
        )
        collector._set_gauge(
            "github_copilot_org_adoption_phase_avg_user_initiated_interactions",
            pl,
            avg_ui,
        )

    collector._set_gauge(
        "github_copilot_org_monthly_active_agent_users",
        {"organization": org},
        max(0.0, float(mau) * _env_float("SIM_COPILOT_MONTHLY_AGENT_FRAC", 0.02)),
    )
    collector._set_gauge(
        "github_copilot_org_monthly_active_chat_users",
        {"organization": org},
        max(0.0, float(mau) * _env_float("SIM_COPILOT_MONTHLY_CHAT_FRAC", 0.04)),
    )


def emit_copilot_pr_gauges(collector, *, org: str) -> None:
    """Daily PR values, set (not accumulated) each session so every PR metric has data every day.

    Applied/suggested stays ~12%, below the code-reviewer-ROI insight's 20% floor.
    """
    day_factor = 0.8 + 0.4 * date_unit_interval()
    suggestions = round(42 * day_factor)
    applied = round(suggestions * 0.12)
    created_by_copilot = round(13 * day_factor)
    merged = round(21 * day_factor)
    org_l = {"organization": org}
    daily_values = {
        "pull_requests_created": 38 * day_factor,
        "pull_requests_reviewed": 24 * day_factor,
        "pull_requests_merged": merged,
        "pull_requests_created_by_copilot": created_by_copilot,
        "pull_requests_reviewed_by_copilot": 9 * day_factor,
        "pull_requests_copilot_suggestions": suggestions,
        "pull_requests_copilot_applied_suggestions": applied,
        "pull_requests_suggestions": suggestions,
        "pull_requests_applied_suggestions": applied,
        "pull_requests_median_minutes_to_merge": 240 * day_factor,
        "pull_requests_median_minutes_to_merge_copilot_reviewed": 180 * day_factor,
        "pull_requests_merged_reviewed_by_copilot": merged * 0.4,
        "pull_requests_merged_created_by_copilot": created_by_copilot * 0.5,
    }
    for name, value in daily_values.items():
        collector._set_gauge(f"github_copilot_org_{name}", org_l, float(round(value)))
    for comment_type in _COMMENT_TYPES[:3]:
        by_type = {"organization": org, "comment_type": comment_type}
        collector._set_gauge(
            "github_copilot_org_pull_requests_copilot_suggestions_by_comment_type",
            by_type,
            float(round(suggestions / 3)),
        )
        collector._set_gauge(
            "github_copilot_org_pull_requests_copilot_applied_suggestions_by_comment_type",
            by_type,
            float(round(applied / 3)),
        )


def enrich_billing_families(
    *,
    organization: str,
    sku: str,
    net_usd: float,
    gross_usd: float,
    discount_usd: float,
    quantity: float,
) -> list[tuple[str, dict[str, str], float]]:
    """Extra billing gauges (quantity/price) matching cxai unit_type labels."""
    unit_type = "ai-units" if "ai" in sku or sku.endswith("_unit") else "seats"
    if sku in ("copilot_enterprise", "copilot_business"):
        unit_type = "ai-units"
    price = _SEAT_PRICE_USD
    labels = {"organization": organization, "sku": sku, "unit_type": unit_type}
    return [
        ("github_copilot_billing_discount_quantity", labels, quantity * 0.9),
        ("github_copilot_billing_gross_quantity", labels, quantity),
        ("github_copilot_billing_price_per_unit", labels, price),
    ]
