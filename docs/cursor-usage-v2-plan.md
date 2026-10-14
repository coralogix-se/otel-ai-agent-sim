# Cursor Usage v2 sim — dual-path plan

## Goal

Emit the **Admin API / Usage dashboard** `cursor_*` Prometheus family (see
`docs/cursor-usage-dashboard-query-engine-map.md`) for demos on cxai / obdev,
**without** removing the existing Composer **OTLP span** simulator.

## Dual path (do not collapse)

| Path | Code | Signal | Dashboard |
|------|------|--------|-----------|
| **Legacy (keep)** | `sim/cursor/agent.py` (+ backup `sim/cursor/legacy/`) | OTLP spans `cursor-agent` / `cursor-coralogix` | Overview / AI Center span widgets |
| **Usage v2 (new)** | `sim/cursor/usage_v2/` (to implement) | Prometheus `cursor_*` gauges (per-bucket deltas) | Cursor Usage dashboard (`RealCursorQueryEngineService`) |

Env knobs (proposed):

- `SIM_CURSOR_OTLP_TRACES_ENABLED` — already gates legacy spans (default on).
- `SIM_CURSOR_USAGE_METRICS_ENABLED` — new; when true, emit Usage-v2 `cursor_*` scrapes (default off until ready).
- Both may run together so Overview + Usage dashboards stay populated.

## Critical engine convention

Every Usage-v2 sample is a **per-bucket delta**, never a cumulative counter.
Dashboard PromQL uses only `sum_over_time` / `max_over_time` / `last_over_time`
— **never** `rate()` / `increase()`. The sim must `.set()` (or replace) bucket
grains each cadence tick, not Prometheus Counter `.inc()` totals that grow forever
(unless the scrape series are intentionally reset to the bucket delta each tick).

## Coverage gap (2026-08-30)

### What the legacy sim emits today

- Composer session traces with `cursor.conversation_id`, `cursor.user_email`,
  `gen_ai.request.model`, tool/edit attributes.
- **No** `cursor_events_total`, `cursor_member_*`, cost/spend, bugbot, tab, etc.

Regression: `tests/dashboard_regression/catalogs/cursor.yaml` (spans only).

### What cxai-dev already has (real telemetry to copy)

`search_relevant_metrics cursor_*` → **94** metric names on `user-cxaidev-coralogix-server`.
Sample label sets:

| Metric | Key labels (cxai) |
|--------|-------------------|
| `cursor_events_total` | `email`, `model`, `team_id`, `conversation_id`, `kind`, `max_mode`, `billing_mode`, … |
| `cursor_member_daily_spend_usd` | `email`, `user_id`, `team_id`, `group_id`, `group_name`, `date`, `is_former`, … |
| `cursor_member_info` | `email`, `user_id`, `name`, `role`, `team_id`, `is_removed` |
| `cursor_ai_code_lines_total` | `email`, `user_id`, `team_id`, `repo_name`, `surface`, `direction`, `commit_source`, `branch_name`, `date` |
| `cursor_conversation_total` | `team_id`, `dimension`, `value` (**no** `email` — matches Notion `realOmitsLabels`) |

Reference query map: `docs/cursor-usage-dashboard-query-engine-map.md`  
Notion source export: `docs/newcursor` (zip) → extracted markdown above.

### Priority emit order for Usage v2 (P0 widgets)

1. Roster / entities: `cursor_member_info`, `cursor_group_members`, `cursor_org_team_membership_info`
2. Gate + conversations table: `cursor_events_total`, `cursor_event_cost_usd`, `cursor_event_tokens_total`, `cursor_event_request_units_total`
3. Cost tab: `cursor_member_daily_spend_usd`, spend/limit snapshots, `cursor_requests_by_class_total`
4. Activity overview: `cursor_ai_code_lines_total`, messages, agent diffs, tab accepts/suggestions, `cursor_requests_total`
5. Adoption: `cursor_member_active`
6. Then: conversation dimensions, invocations, bugbot, commits/repos

## Implementation notes

- Prefer a **dedicated Prometheus registry job** or same scrape job with clear
  `job` / `cx_application_name` labels matching cxai (confirm live values before shipping).
- Reuse Products roster emails where useful so Claude + Cursor demos share identities.
- Keep `sim/cursor/legacy/` untouched when iterating on `usage_v2`.
- Add a second regression catalog `tests/dashboard_regression/catalogs/cursor_usage.yaml`
  for PromQL checks; leave `cursor.yaml` span checks as-is.

## Status

Implemented under `sim/cursor/usage_v2/` behind `SIM_CURSOR_USAGE_METRICS_ENABLED`
(default **false**). Unit tests: `tests/test_cursor_usage_v2.py`. Regression catalog:
`tests/dashboard_regression/catalogs/cursor_usage.yaml`. Not deployed until explicitly enabled.

