"""Per-metric value scales and series-cut probabilities (onlineboutique brief §8)."""

from __future__ import annotations

import os
import random

from sim.common.env import _env_bool, _env_float

# Value multipliers vs today's sim (brief Change column). Applied on top of SIM_CURSOR_USAGE_VOLUME.
_VALUE_SCALE: dict[str, float] = {
    "cursor_accepts_total": 0.078,
    "cursor_active_users_bugbot": 0.0064,
    "cursor_active_users_cli": 0.026,
    "cursor_active_users_cloud_agent": 0.029,
    "cursor_active_users_total": 0.043,
    "cursor_ai_change_file_lines_added_total": 0.076,
    "cursor_ai_change_lines_added_total": 0.22,
    "cursor_ai_code_lines_total": 0.021,
    "cursor_ai_code_total_lines_added_total": 0.2,
    "cursor_ai_code_total_lines_deleted_total": 0.061,
    "cursor_applies_total": 0.06,
    "cursor_bugbot_pr_reviews_total": 0.033,
    "cursor_bugbot_prs_reviewed": 0.034,
    "cursor_bugbot_repos": 82.0,
    "cursor_commits_total": 0.063,
    "cursor_conversation_subcategory_snapshot": 4.2,
    "cursor_conversation_total": 61.0,
    "cursor_event_cost_usd": 0.021,
    "cursor_event_list_price_usd": 0.016,
    "cursor_event_request_units_total": 0.13,
    "cursor_event_tokens_total": 11.0,
    "cursor_events_total": 0.072,
    "cursor_group_members": 34.0,
    "cursor_member_active": 0.27,
    "cursor_member_spend_gross_usd": 0.23,
    "cursor_org_pool_limit_usd": 0.034,
    "cursor_org_pool_remaining_usd": 0.047,
    "cursor_org_pooled_usage_usd": 0.03,
    "cursor_requests_by_class_total": 2.1,
    "cursor_requests_total": 0.051,
    "cursor_tab_accepts_total": 0.011,
    "cursor_tab_suggestions_total": 0.049,
    "cursor_user_agent_diffs_suggested_total": 0.36,
    "cursor_user_ask_mode_usage_total": 0.0077,
    "cursor_user_command_usage_total": 0.01,
    "cursor_user_conversation_snapshot": 2.6,
    "cursor_user_conversation_subcategory_snapshot": 4.2,
    "cursor_user_conversation_total": 2.5,
    "cursor_user_lines_total": 0.12,
    "cursor_user_mcp_usage_total": 0.32,
    "cursor_user_model_messages_total": 0.24,
    "cursor_user_plan_usage_total": 0.0021,
    "cursor_user_skill_usage_total": 0.22,
    "cursor_user_tab_accepts_total": 0.0015,
    "cursor_user_tab_suggestions_total": 0.0053,
}

# Emit probability to cut high-cardinality series toward c4c counts.
_SERIES_EMIT_PROB: dict[str, float] = {
    "cursor_ai_change_file_lines_added_total": 0.02,  # 4.12k → ~75
    "cursor_ai_change_lines_added_total": 0.15,  # 183 → ~28
    "cursor_ai_code_lines_total": 0.03,  # 6.76k → ~219
    "cursor_ai_code_total_lines_added_total": 0.14,
    "cursor_ai_code_total_lines_deleted_total": 0.14,
    "cursor_bugbot_issues_total": 0.16,
    "cursor_bugbot_pr_reviews_total": 0.27,
    "cursor_bugbot_prs_reviewed": 0.27,
    "cursor_commits_total": 0.05,
    "cursor_conversation_subcategory_snapshot": 0.21,
    "cursor_user_agent_diffs_accepted_total": 0.48,
    "cursor_user_agent_diffs_rejected_total": 0.05,  # almost never
    "cursor_user_agent_diffs_suggested_total": 0.48,
    "cursor_user_ask_mode_usage_total": 0.01,
    "cursor_user_command_usage_total": 0.02,
    "cursor_user_lines_total": 0.15,
    "cursor_user_mcp_usage_total": 0.06,
    "cursor_user_model_messages_total": 0.17,
    "cursor_user_plan_usage_total": 0.01,
    "cursor_user_skill_usage_total": 0.24,
    "cursor_user_tab_accepts_total": 0.02,
    "cursor_user_tab_suggestions_total": 0.02,
}


def cursor_scales_enabled() -> bool:
    return _env_bool("SIM_CURSOR_USAGE_BRIEF_SCALES", True)


def cursor_value_scale(name: str) -> float:
    if not cursor_scales_enabled():
        return 1.0
    base = _VALUE_SCALE.get(name, 1.0)
    # Optional global override still applies at emit sites via SIM_CURSOR_USAGE_VOLUME.
    return max(0.0, base * _env_float("SIM_CURSOR_USAGE_BRIEF_SCALE_MULT", 1.0))


def cursor_should_emit_series(name: str) -> bool:
    if not cursor_scales_enabled():
        return True
    # Allow forcing full cardinality for debugging.
    if _env_bool("SIM_CURSOR_USAGE_FULL_CARDINALITY", False):
        return True
    p = _SERIES_EMIT_PROB.get(name)
    if p is None:
        return True
    override = os.environ.get(f"SIM_CURSOR_EMIT_PROB_{name}", "").strip()
    if override:
        try:
            p = float(override)
        except ValueError:
            pass
    return random.random() < max(0.0, min(1.0, p))


def cursor_scale_amount(name: str, amount: float) -> float:
    if amount == 0:
        return 0.0
    return float(amount) * cursor_value_scale(name)
