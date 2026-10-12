"""Remaining GitHub Copilot collector metrics (onlineboutique brief §7 Add rows).

Registered via ``GitHubCopilotCollector`` counters/gauges without bloating
``CopilotCollectorMetrics``. Label shapes copied from cxai (eu2) Oct 2026.
"""

from __future__ import annotations

import hashlib
import os
import random

from sim.common.env import _env_float, _env_int

_FEATURES = (
    "agent_edit",
    "chat_inline",
    "chat_panel_agent_mode",
    "chat_panel_ask_mode",
    "code_completion",
)
_IDES = ("intellij", "neovim", "pycharm", "rustrover", "vscode", "webstorm", "zed")
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
_LANGUAGES = ("Python", "TypeScript", "Go", "Java", "Rust", "Ruby", "jsonc", "YAML")


def _pick(items: tuple[str, ...]) -> str:
    return random.choice(items)


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
    model: str,
    generations: int,
    acceptances: int,
    interactions: int,
    loc_added: int,
    loc_deleted: int,
    loc_suggested: int,
) -> None:
    """Increment brief §7 Add counters for one session."""
    feature = _pick(_FEATURES)
    # Prefer vscode for CLI sim traffic; still emit other IDEs for cardinality.
    ide = "vscode" if random.random() < 0.55 else _pick(_IDES)
    language = _pick(_LANGUAGES)
    u = _user_ext(user_l)

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
        collector._inc_counter(
            "github_copilot_user_code_generation_activity_count_by_feature",
            {**u, "feature": feature},
            generations,
        )
        collector._inc_counter(
            "github_copilot_user_code_generation_activity_count_by_ide",
            {**u, "ide": ide},
            generations,
        )
        collector._inc_counter(
            "github_copilot_user_code_generation_activity_count_by_language_feature",
            {**u, "language": language, "feature": feature},
            generations,
        )
        collector._inc_counter(
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
        collector._inc_counter(
            "github_copilot_user_code_acceptance_activity_count_by_feature",
            {**u, "feature": feature},
            acceptances,
        )
        collector._inc_counter(
            "github_copilot_user_code_acceptance_activity_count_by_ide",
            {**u, "ide": ide},
            acceptances,
        )
        collector._inc_counter(
            "github_copilot_user_code_acceptance_activity_count_by_language_feature",
            {**u, "language": language, "feature": feature},
            acceptances,
        )

    if interactions:
        collector._inc_counter(
            "github_copilot_user_user_initiated_interaction_count_by_feature",
            {**u, "feature": feature},
            interactions,
        )
        collector._inc_counter(
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
        collector._inc_counter(
            "github_copilot_user_loc_added_sum_by_feature",
            {**u, "feature": feature},
            loc_added,
        )
        collector._inc_counter(
            "github_copilot_user_loc_added_sum_by_ide",
            {**u, "ide": ide},
            loc_added,
        )
        collector._inc_counter(
            "github_copilot_user_loc_added_sum_by_language_feature",
            {**u, "language": language, "feature": feature},
            loc_added,
        )
        collector._inc_counter(
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
        collector._inc_counter(
            "github_copilot_user_loc_deleted_sum_by_feature",
            {**u, "feature": feature},
            loc_deleted,
        )
        collector._inc_counter(
            "github_copilot_user_loc_deleted_sum_by_ide",
            {**u, "ide": ide},
            loc_deleted,
        )
        collector._inc_counter(
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
        collector._inc_counter(
            "github_copilot_user_loc_suggested_to_add_sum_by_feature",
            {**u, "feature": feature},
            loc_suggested,
        )
        collector._inc_counter(
            "github_copilot_user_loc_suggested_to_add_sum_by_ide",
            {**u, "ide": ide},
            loc_suggested,
        )
        collector._inc_counter(
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
        collector._inc_counter(
            "github_copilot_user_loc_suggested_to_delete_sum", u, loc_deleted
        )
        collector._inc_counter(
            "github_copilot_user_loc_suggested_to_delete_sum_by_feature",
            {**u, "feature": feature},
            loc_deleted,
        )
        collector._inc_counter(
            "github_copilot_user_loc_suggested_to_delete_sum_by_ide",
            {**u, "ide": ide},
            loc_deleted,
        )
        collector._inc_counter(
            "github_copilot_user_loc_suggested_to_delete_sum_by_language_feature",
            {**u, "language": language, "feature": feature},
            loc_deleted,
        )


def emit_copilot_extra_gauges(
    collector,
    *,
    org: str,
    user_l: dict[str, str],
    dau: int,
    wau: int,
    mau: int,
) -> None:
    """Refresh brief §7 Add gauges (adoption, team membership, PR extras, monthly agent/chat)."""
    u = _user_ext(user_l)
    # ★ user_team_membership — cxai labels: organization, team_id, team_slug, user_id, user_login
    n_teams = max(1, _env_int("SIM_COPILOT_TEAMS_PER_USER", 2))
    login = u["user_login"] or "unknown"
    for i in range(n_teams):
        slug = ("engineering", "platform", "data", "security", "product")[i % 5]
        team_id = str(10_000_000 + (hash(f"{org}:{slug}") % 9_000_000))
        collector._set_gauge(
            "github_copilot_user_team_membership",
            {
                "organization": org,
                "team_id": team_id,
                "team_slug": slug if i == 0 else f"team-{slug}",
                "user_id": u["user_id"],
                "user_login": login,
            },
            1.0,
        )

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

    # PR extras (org-level).
    if random.random() < 0.15:
        collector._inc_counter(
            "github_copilot_org_pull_requests_suggestions",
            {"organization": org},
            random.randint(1, 4),
        )
        collector._inc_counter(
            "github_copilot_org_pull_requests_applied_suggestions",
            {"organization": org},
            random.randint(0, 2),
        )
        ct = _pick(_COMMENT_TYPES)
        collector._inc_counter(
            "github_copilot_org_pull_requests_copilot_suggestions_by_comment_type",
            {"organization": org, "comment_type": ct},
            random.randint(1, 3),
        )
        if random.random() < 0.4:
            collector._inc_counter(
                "github_copilot_org_pull_requests_copilot_applied_suggestions_by_comment_type",
                {"organization": org, "comment_type": ct},
                1,
            )
    if random.random() < 0.08:
        collector._set_gauge(
            "github_copilot_org_pull_requests_median_minutes_to_merge_copilot_reviewed",
            {"organization": org},
            random.uniform(30.0, 2400.0),
        )
    if random.random() < 0.05:
        collector._inc_counter(
            "github_copilot_org_pull_requests_merged_reviewed_by_copilot",
            {"organization": org},
            1,
        )
    if random.random() < 0.02:
        collector._inc_counter(
            "github_copilot_org_pull_requests_merged_created_by_copilot",
            {"organization": org},
            1,
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
    # Map enterprise/business SKUs to ai-unit style when configured.
    if sku in ("copilot_enterprise", "copilot_business"):
        sku_out = os.environ.get("SIM_COPILOT_BILLING_QUANTITY_SKU", "copilot_ai_unit")
        unit_type = "ai-units"
    else:
        sku_out = sku
    price = (gross_usd / quantity) if quantity > 0 else 19.0
    labels = {"organization": organization, "sku": sku_out, "unit_type": unit_type}
    return [
        ("github_copilot_billing_discount_quantity", labels, quantity * 0.9),
        ("github_copilot_billing_gross_quantity", labels, quantity),
        ("github_copilot_billing_price_per_unit", labels, price),
    ]
