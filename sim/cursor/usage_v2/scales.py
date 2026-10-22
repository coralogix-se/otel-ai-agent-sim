"""Per-metric value scales and series-cut probabilities (onlineboutique brief §8)."""

from __future__ import annotations

import os
import zlib

from sim.common.env import _env_bool, _env_float

# Value multipliers on the raw sim amounts (SIM_CURSOR_USAGE_VOLUME=1). Per-day targets (c4c):
# ~358 conversation_ids x 5-10 events (~2.7k events), cost $204 / token fee $27 / tokens 246M
# (cost and fee are computed in dollars in runtime.py, so they carry no scale here),
# ~1.8k classified conversations, requests x0.05.
_VALUE_SCALE: dict[str, float] = {
    "cursor_accepts_total": 0.349,
    "cursor_active_users_bugbot": 0.0064,
    "cursor_active_users_cli": 0.026,
    "cursor_active_users_cloud_agent": 0.029,
    "cursor_active_users_total": 0.043,
    "cursor_ai_change_file_lines_added_total": 0.017,
    "cursor_ai_change_lines_added_total": 0.0492,
    "cursor_ai_code_lines_total": 0.0047,
    "cursor_ai_code_total_lines_added_total": 0.0447,
    "cursor_ai_code_total_lines_deleted_total": 0.0136,
    "cursor_applies_total": 0.268,
    "cursor_bugbot_pr_reviews_total": 0.033,
    "cursor_bugbot_prs_reviewed": 0.034,
    "cursor_bugbot_repos": 82.0,
    "cursor_commits_total": 0.282,
    "cursor_conversation_subcategory_snapshot": 3.49,
    "cursor_conversation_total": 68.2,
    "cursor_event_request_units_total": 0.581,
    # 246M tokens/day over ~2.7k events at ~4.1k raw tokens/event.
    "cursor_event_tokens_total": 22.3,
    "cursor_events_total": 0.322,
    "cursor_member_active": 0.27,
    # One shared scale so limit - pooled usage == remaining (c4c: $231k limit).
    "cursor_org_pool_limit_usd": 0.0345,
    "cursor_org_pool_remaining_usd": 0.0345,
    "cursor_org_pooled_usage_usd": 0.0345,
    "cursor_requests_by_class_total": 9.39,
    "cursor_requests_total": 0.051,
    "cursor_tab_accepts_total": 0.0492,
    "cursor_tab_suggestions_total": 0.219,
    # Accepted at 0.7x suggested so the rejection-churn insight sees ~35% acceptance.
    "cursor_user_agent_diffs_accepted_total": 1.13,
    "cursor_user_agent_diffs_rejected_total": 1.609,
    "cursor_user_agent_diffs_suggested_total": 1.609,
    "cursor_user_ask_mode_usage_total": 0.0344,
    "cursor_user_command_usage_total": 0.0447,
    # c4c sums ~1.82k over every series (all dimensions), 608 for subcategories.
    "cursor_user_conversation_snapshot": 1.116,
    "cursor_user_conversation_subcategory_snapshot": 2.235,
    "cursor_user_conversation_total": 1.116,
    "cursor_user_lines_total": 0.536,
    "cursor_user_mcp_usage_total": 1.43,
    "cursor_user_model_messages_total": 1.073,
    "cursor_user_plan_usage_total": 0.0094,
    "cursor_user_skill_usage_total": 0.983,
    "cursor_user_tab_accepts_total": 0.0067,
    "cursor_user_tab_rejects_total": 0.0237,
    "cursor_user_tab_suggestions_total": 0.0237,
}

# Emit probability: share of series kept, decided per series key (sticky) so a series that exists
# carries its full value on every sample.
_SERIES_EMIT_PROB: dict[str, float] = {
    "cursor_ai_change_file_lines_added_total": 0.058,
    "cursor_ai_change_lines_added_total": 0.27,
    "cursor_ai_code_lines_total": 0.085,
    "cursor_ai_code_total_lines_added_total": 0.4,
    "cursor_ai_code_total_lines_deleted_total": 0.4,
    "cursor_bugbot_issues_total": 0.16,
    "cursor_bugbot_pr_reviews_total": 0.27,
    "cursor_bugbot_prs_reviewed": 0.27,
    "cursor_commits_total": 0.22,
    "cursor_conversation_subcategory_snapshot": 0.6,
    "cursor_user_agent_diffs_accepted_total": 0.8,
    "cursor_user_agent_diffs_rejected_total": 0.1,  # almost never
    "cursor_user_agent_diffs_suggested_total": 0.8,
    "cursor_user_ask_mode_usage_total": 0.04,
    "cursor_user_command_usage_total": 0.08,
    "cursor_user_lines_total": 0.24,
    "cursor_user_mcp_usage_total": 0.25,
    "cursor_user_model_messages_total": 0.5,
    "cursor_user_plan_usage_total": 0.04,
    "cursor_user_skill_usage_total": 0.42,
    "cursor_user_tab_accepts_total": 0.05,
    "cursor_user_tab_rejects_total": 0.05,
    "cursor_user_tab_suggestions_total": 0.05,
}


def cursor_scales_enabled() -> bool:
    return _env_bool("SIM_CURSOR_USAGE_BRIEF_SCALES", True)


def cursor_value_scale(name: str) -> float:
    if not cursor_scales_enabled():
        return 1.0
    base = _VALUE_SCALE.get(name, 1.0)
    # Optional global override still applies at emit sites via SIM_CURSOR_USAGE_VOLUME.
    return max(0.0, base * _env_float("SIM_CURSOR_USAGE_BRIEF_SCALE_MULT", 1.0))


def cursor_should_emit_series(name: str, series_key: object) -> bool:
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
    return zlib.crc32(repr(series_key).encode()) % 10000 < max(0.0, min(1.0, p)) * 10000


def cursor_scale_amount(name: str, amount: float) -> float:
    if amount == 0:
        return 0.0
    return float(amount) * cursor_value_scale(name)
