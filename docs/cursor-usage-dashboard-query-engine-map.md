# Cursor Usage Dashboard — Real Query Engine Map

Full map of **widget → metric → labels → PromQL** for the new Cursor Usage dashboard's real query engine (`RealCursorQueryEngineService`, `libs/ai-center/code-agents/src/lib/cursor-usage/engine/real-cursor-query-engine.service.ts`, branch `yoavshaked/aic-931-cursor-usage-v2`). Traced code-level on 2026-08-29: component → panel service → `CursorQueryEngine` op → PromQL builder. Queries run against the live `cursor_*` metrics (eu2, team 3405693). Sibling page: [Cursor Usage Dashboard — Widget × Filter Support Matrix](https://app.notion.com/p/Cursor-Usage-Dashboard-Widget-Filter-Support-Matrix-3cac048defad815aba01fe3e7c42ec2e?pvs=21).

<aside>
⚙️

Every `cursor_*` sample is a **per-bucket delta** (one sample per native cadence tick), never a cumulative counter — so the engine only ever uses `sum_over_time` / `max_over_time` / `last_over_time`, never `rate()`/`increase()`.

</aside>

# Conventions

- `F` — matchers derived from the active page filters (see *Filters → matchers* below). Emitted **leniently**: a matcher is added only when the metric actually carries the label, so an inapplicable filter silently leaves a widget team-wide.
- `[W]` — the picked time window as seconds. Every `window()`/`breakdown()`/`perUserTable()` query is evaluated **twice as instant queries**: at `to` (current) and at `from` (previous window), which is where every trend/delta comes from.
- `[B]` — chart bucket width: the shared aggregation-interval grid for hourly-cadence metrics, whole UTC days for daily metrics. Series are range queries with `step = B`, each bucket evaluated at its END (the last bucket deliberately past `now` so today's still-forming daily sample is seen), then shifted back to bucket start and zero-filled.
- **Snapshot/level metrics** (`/teams/spend` family, cycle scalars, rosters) are daily-restated levels → read with `last_over_time(…[93600s])` (26 h lookback so one missed poll doesn't blank a tile).
- `cursor_member_active` is a per-user-day **flag** → OR-reduced with `max by (user_id, email) (max_over_time(…))`, never summed.

# Engine operation → PromQL shape

| Op | Query shape | Evaluation |
| --- | --- | --- |
| `window(M)` — counter grains | `sum(sum_over_time(M{F,scope}[W]))` | instant @ `to`  • @ `from` |
| `window(cursor_member_active)` | `sum(max by (user_id, email) (max_over_time(M{F}[W])))` | instant @ `to`  • @ `from` |
| `window(M)` — snapshot/level grain | `sum(last_over_time(M{F,scope}[93600s]))` | instant @ `to`  • @ `from` |
| `window(M)` — team-DAU set (`cursor_active_users_*`) | `sum(sum_over_time(M{F}[1d]))` per day, **peak day** taken | range step 1d over window, and over the previous window |
| `series(M)` ungrouped | `sum(sum_over_time(M{F,scope}[Bs]))` | range step `B`, zero-filled |
| `series(M, groupBy: g)` | `sum by (g) (sum_over_time(M{F,scope}[Bs]))` | range step `B`, zero-filled per group×bucket |
| `dailySeries(M, range)` | value `sum(sum_over_time(M{F}[1d]))`  • presence `sum(count_over_time(M[1d]))` (presence deliberately unfiltered) | range step 1d over the caller's own range (ignores the time picker) |
| `peakDailyUsersSeries(M, groupBy: g)` | `count by (g)((sum by (g, user_id, email) (sum_over_time(M{F}[1d]))) > 0)` | range step 1d, re-bucketed to chart buckets taking the MAX day per bucket |
| `breakdown(M, byLabel: L)` | `sum by (L) (sum_over_time(M{F,scope}[W]))` (snapshot → `last_over_time`; `cursor_member_active` → `max by (L)(max_over_time(…))`) | instant @ `to`  • @ `from`, sorted desc, optional client-side `limit` |
| `perUserTable([M…])` | per metric: `sum by (email) (sum_over_time(M{F}[W]))` (same snapshot/flag variants) | instant @ `to`  • @ `from`, joined onto the harvested roster (Billing-Group filter resolved to member emails first) |
| `estimatedCostByRepo()` | `sum by (email) (sum_over_time(cursor_event_cost_usd{F}[W]))`  • `sum by (email, repo_name) (sum_over_time(cursor_ai_code_total_lines_added_total{F}[W]))` | 4 instants (both queries @ `to` and @ `from`); each member's cost split across repos ∝ their AI-lines-added share — **derived allocation, never metered** (billing events carry no repo) |
| `conversationsTable()` | `sum by (conversation_id, email) (sum_over_time(M{F,email?}[W]))` for `cursor_event_cost_usd` / `cursor_event_tokens_total` / `cursor_event_request_units_total` / `cursor_events_total`, plus `count by (conversation_id, model) (sum_over_time(cursor_events_total{F}[W]))`; then start times via `sum by (conversation_id) (sum_over_time(cursor_events_total{conversation_id=~"id1|id2|…"}[3600s]))` | 5 instants @ `to` → top-N by cost → 1 range query step 1h for hour-aligned `firstTs` |
| `checkHasData()` | `count(last_over_time(cursor_events_total[48h]))` | instant @ `to`; gates the whole-dashboard docs empty state |

# Filters → matchers (`buildCursorMatchers`)

| Page filter | Matcher emitted | Condition / translation |
| --- | --- | --- |
| User | `email=~"a|b"` (or `user_email` on audit metrics) | only when the metric carries `email`/`user_email`. Exception: the `cursor_conversation_*` family declares `email` but the real backend never emits it (`realOmitsLabels`) → matcher dropped, User/Group are a silent no-op there |
| Billing Group | `group_id=~"…"` when the metric has it, else `email=~"member1|member2…"` | group ids resolved to member emails via the harvested `cursor_group_members`  • `cursor_member_daily_spend_usd`; empty resolution pins `email="__no_match__"` (matches nothing, never drops the filter) |
| Model | `model=~"…"` | only on metrics carrying `model`; dropdown shows display names, filters service maps back to ids |
| Surface | `surface=~"…"` | only on metrics with a `surface` label (`cursor_requests_total`, `cursor_ai_code_lines_total`). Usage events name the same axis `kind` with different values — deliberately NOT matched |
| Team | `team_id=~"…"` | always emitted — the integration stamps `team_id` on every metric |
| Organization | `organization=~"…"` on `cursor_org_*`, else `team_id=~"<teams of that org>"` | org→teams resolved from `cursor_org_team_membership_info`; empty resolution pins `team_id="__no_match__"` |
| Label scope (hard) | `name="value"` / `name=~"a|b"` | widget-pinned constraints (e.g. `direction="added"`) — always emitted, never lenient |

# Entities harvest (filter dropdowns & rosters)

Harvested once per page load (`entities$()`, shareReplay). These also feed roster joins, model display names, and `limit` values.

| Feeds | Query |
| --- | --- |
| User dropdown, rosters, `perUserTable` row source | `last_over_time(cursor_member_info[26h])` — rows with `is_removed!="true"`; labels `user_id, email, name, role` |
| Billing Group dropdown + group→email resolution | `last_over_time(cursor_group_members[26h])`  • membership from `last_over_time(cursor_member_daily_spend_usd[26h])` (skips `is_former="true"`) |
| Model dropdown | label-values `model` on `cursor_events_total` |
| Surface dropdown | label-values `surface` on `cursor_requests_total` |
| Team / Organization dropdowns | `last_over_time(cursor_org_team_membership_info[26h])`; without an org key falls back to label-values `team_id` on `cursor_events_total` (label "Team ID") |
| Repo lists (overview repo select, bugbot zero-fill) | label-values `repo_name` on `cursor_commits_total` ∪ `cursor_ai_code_lines_total` |
| Client versions (stale-clients insight) | label-values `client_version` on `cursor_member_active`, semver-desc |

# Activity tab

## Overview

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| KPI "AI Share of Committed Code" | `cursor_ai_code_lines_total` | `direction="added"`, optional `repo_name=~"<selected repos>"`, 2nd leg adds `surface="non_ai"`; F | 2× `sum(sum_over_time(cursor_ai_code_lines_total{direction="added",…,F}[W]))` @ `to`+`from` | KPI = `(total − nonAi)/total`; repo scope comes from the repo multi-select / Managed-Unmanaged control (classification via git-repos validation) |
| KPI "Messages" | `cursor_user_model_messages_total` | F | `sum(sum_over_time(M{F}[W]))` @ `to`+`from` | matches the chart's Model split only |
| KPI "Agent Edits" | `cursor_user_agent_diffs_accepted_total`, `cursor_user_agent_diffs_rejected_total` | F | 2 window queries | KPI = accepted + rejected |
| KPI "Tab Completions" | `cursor_tab_accepts_total`, `cursor_tab_suggestions_total` | F | 2 window queries | value = accepts; suggestions only feeds the tooltip denominator |
| Chart "AI Share of Committed Code" | `cursor_ai_code_lines_total` | by `commit_source`; `direction="added"`  • repo scope; second ungrouped leg `surface="non_ai"`; F | `sum by (commit_source)(sum_over_time(M{…}[Bs]))`  • ungrouped non-AI leg, range step B | AI-% line = `(total−nonAi)/total` per bucket, gap when total 0 |
| Chart "Agent Edits" — Outcome split | `cursor_user_agent_diffs_accepted_total`, `…rejected_total` | F | 2 ungrouped series | bands Accepted / Rejected |
| Chart "Agent Edits" — Language split | `cursor_ai_change_file_lines_added_total` | by `file_extension`; `change_source="composer"`; F | `sum by (file_extension)(sum_over_time(M{change_source="composer",F}[Bs]))` | top 6 + Other; unit switches to lines added |
| Chart "Tab Completions" | `cursor_tab_accepts_total`, `cursor_tab_suggestions_total` | F | 2 ungrouped series | "Not accepted" = `max(0, suggested − accepted)` |
| Chart "Messages" — Model split | `cursor_user_model_messages_total` | by `model`; F | `sum by (model)(sum_over_time(M{F}[Bs]))` | top 6 + Other; band click adds a Model page filter |
| Chart "Messages" — Surface split | `cursor_requests_total` | by `surface`; F | `sum by (surface)(sum_over_time(M{F}[Bs]))` | counts requests, not messages — deliberately ≠ the KPI |

Chart point clicks open the breakdown drawer from already-rendered points — no extra query.

## Adoption

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| KPIs "Active Users" / "Adoption Rate" / "Idle Seats" | `cursor_member_active` | by `email`; F; limit = roster size | `max by (email)(max_over_time(cursor_member_active{F}[W]))` @ `to`+`from` | active = rows > 0; adoption = active/roster; idle = roster − active |
| Drawers "Active members" / "Idle members" (KPI click) | `cursor_member_active`  • enrichment | as above; enrichment = `perUserTable(['cursor_member_daily_spend_usd','cursor_event_tokens_total'])` | same breakdown re-issued + 2 per-email window queries | rows filtered `>0` / `=0`; drawer adds Group/Cost/Tokens columns joined by email |
| Chart "Active Users by Surface" | `cursor_requests_total` | by `surface` (+ ungrouped total for the drawer); F | `count by (surface)((sum by (surface,user_id,email)(sum_over_time(M{F}[1d]))) > 0)` range step 1d, peak-day re-bucketed | distinct users per day; overlaid, never stacked |
| Card "Coding Activity" (lines calendar) | `cursor_ai_change_lines_added_total` | F | `sum(sum_over_time(M{F}[1d]))`  • presence `sum(count_over_time(M[1d]))`, range step 1d | own fixed 364-day window ending today — ignores the time picker; streaks/most-active derived client-side |

## Conversations

The `cursor_conversation_*` family is team-level on the real backend (`email` never emitted) → User/Billing-Group filters are a silent no-op on every row below except the two mode pies.

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| KPI "Conversations" | `cursor_conversation_total` | `dimension="intents"`; F | `sum(sum_over_time(M{dimension="intents",F}[W]))` @ `to`+`from` | metric counts each conversation once per dimension → must be dimension-pinned |
| KPI "Autonomy Rate" | `cursor_conversation_total` | numerator `value="low"`, denominator `dimension="guidanceLevels"`; F | 2 window queries; share = num/denom | ⚠ numerator scopes on `value` alone and `low/medium/high` exist in two dimensions → merges complexity + guidance counts |
| KPI "High-Complexity Share" | `cursor_conversation_total` | numerator `value="high"`, denominator `dimension="complexity"`; F | 2 window queries | same `value`-ambiguity caveat |
| Chart "Conversations Over Time" | `cursor_conversation_total` | by `value`; F | `sum by (value)(sum_over_time(M{F}[Bs]))` | client-filtered to the intents catalog; a second `by (dimension)` reconstruction leg is fetched but discarded (dead) |
| Drawer "Conversations by Intent — date" (point click) | `cursor_conversation_total` | by `value`; F | `sum by (value)(sum_over_time(M{F}[W]))` @ `to`+`from` (prefetched) | guidance + complexity twins are also prefetched but never rendered (dead) |
| Pie "Plan Mode by Model" | `cursor_user_plan_usage_total` | by `model`; F (carries `email` → User/Group DO apply) | `sum by (model)(sum_over_time(M{F}[W]))` @ `to`+`from` | slice click adds a Model page filter; a `window()` total is forkJoined but only its loading state is used |
| Pie "Ask Mode by Model" | `cursor_user_ask_mode_usage_total` | by `model`; F | same shape | same unrendered total leg |

## Invocations

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| KPIs "Commands / MCP Servers / Skills Invoked" | `cursor_user_command_usage_total`, `cursor_user_mcp_usage_total`, `cursor_user_skill_usage_total` | F | 3 window queries @ `to`+`from` | all three fetched regardless of active tab |
| Chart "… Invocations Over Time" (per tab / MCP toggle) | same three metrics | by `command_name` / `mcp_server_name` / `tool_name` / `skill_name`; F | `sum by (<entity>)(sum_over_time(M{F}[Bs]))` | top 6 + Other client-side; MCP Server↔Tool toggle refetches |
| Drawer "<entity> — by user" (band click) | same metric as the tab | by `email`; pinned `command_name|mcp_server_name|tool_name|skill_name="<clicked>"`; F | `sum by (email)(sum_over_time(M{<entity>="…",F}[W]))` @ `to`+`from` | click-only; `rowsAreUsers` → drawer enrichment `perUserTable` fires too |

## Users grid

Row source + implicit columns (Email/Role/Group) come from the roster join; Billing-Group filter resolved to emails inside `perUserTable`.

| Column | Metric | Labels used | Query | Notes |  |
| --- | --- | --- | --- | --- | --- |
| Cost (+delta) | `cursor_member_daily_spend_usd` | by `email`; F | `sum by (email)(sum_over_time(M{F}[W]))` @ `to`+`from` (via `perUserTable`) | windowed — agrees with the Cost KPI and drawer |  |
| Monthly Usage % (numerator) | `cursor_member_spend_gross_usd` | by `email`; F | `sum by (email)(last_over_time(M{F}[93600s]))` @ `to`+`from` | cycle-to-date; ignores the time picker |  |
| Limit (+ Usage % denominator) | `cursor_member_monthly_limit_usd` | by `email`; F | snapshot breakdown | 0 → N/A; amber ≥85 %, red ≥100 % |  |
| Tokens | `cursor_event_tokens_total` | by `email`; F | counter breakdown | all token types summed |  |
| Commits (+delta) | `cursor_commits_total` | by `email`; F | counter breakdown | — |  |
| Models (chips) | `cursor_event_cost_usd` | by `email`; pinned `model="<id>"` — one breakdown per catalog model; F | N × `sum by (email)(sum_over_time(M{model="…",F}[W]))` | chips ranked by that member's spend on the model |  |
| Usage Patterns (tags) | see User tags section | — | — | tags joined post-filter; row click opens the user drawer |  |

# Cost tab

## Cost section

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| KPI "Cost (time range)" | `cursor_member_daily_spend_usd` | F | `sum(sum_over_time(M{F}[W]))` @ `to`+`from` | deliberately NOT the cycle gross metric — windowed so it moves with the picker |
| KPI "Overage" | `cursor_member_spend_overage_usd` | F | `sum(last_over_time(M{F}[93600s]))` @ `to`+`from` | cycle-to-date snapshot; can't be windowed (Cursor `/teams/spend` has no date params) |
| KPI "Cycle Budget Used" | `cursor_member_spend_gross_usd`, `cursor_member_effective_limit_usd`, `cursor_billing_cycle_end_seconds` | F (cycle-end scalar unfiltered) | 3 snapshot windows | burn = gross/limit; days-remaining anchored on the window end, never `Date.now()` |
| Chart "Cost over time" — Cost by group | `cursor_member_daily_spend_usd` | by `group_name`; F | `sum by (group_name)(sum_over_time(M{F}[Bs]))` | band click adds a Billing-Group filter; series order pinned by harvested group list |
| Chart "Cost over time" — Requests by type | `cursor_requests_by_class_total` | by `billing_class`; F | `sum by (billing_class)(sum_over_time(M{F}[Bs]))` | bands: subscription_included / usage_based / api_key |
| "Cycle Budget (30 days)" bullet strip | `cursor_member_daily_spend_usd` (series) + `cursor_member_spend_gross_usd`, `cursor_member_effective_limit_usd`, `cursor_billing_cycle_start_seconds`, `cursor_billing_cycle_end_seconds` (windows) | F (cycle scalars unfiltered) | 1 series + 4 window/snapshot queries | pre-window ramp = `max(0, cycle gross − Σ window daily)` drawn from cycle start; amber ≥85 %, red >100 % |

## Most Expensive Conversations

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| Grid (top 20 by cost) | `cursor_event_cost_usd`, `cursor_event_tokens_total`, `cursor_event_request_units_total`, `cursor_events_total` | by `conversation_id, email` (+ `count by (conversation_id, model)`); F | 5 instants `sum by (conversation_id,email)(sum_over_time(M{F}[W]))` @ `to`, then `sum by (conversation_id)(sum_over_time(cursor_events_total{conversation_id=~"…"}[3600s]))` range step 1h for start times | `conversationsTable(limit 20)`; start times hour-aligned; row click opens the user drawer; Repos columns structurally empty (no Admin-API conversation↔repo link) |

## Models

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| Cumulative chart — Cost / Tokens toggle | `cursor_event_cost_usd` / `cursor_event_tokens_total` | by `model`; F | `sum by (model)(sum_over_time(M{F}[Bs]))`, accumulated client-side | top 6 + Other; no pre-window baseline (no cycle-to-date per-model metric exists) |
| Drawer "Window Tokens by Model" (chart click, Tokens mode) | `cursor_event_tokens_total` | by `model`; F | `sum by (model)(sum_over_time(M{F}[W]))` @ `to`+`from` | prefetched only while the toggle is on Tokens |
| Grid "Model Usage & Cost" — Cost / Requests / Tokens (+deltas, % of cost) | `cursor_event_cost_usd`, `cursor_events_total`, `cursor_event_tokens_total` | by `model`; F | 3 counter breakdowns @ `to`+`from` | % of Cost denominator = surviving rows only; row click adds a Model filter |
| Grid column "Users" (peak/day) | `cursor_model_distinct_users` | by `model`; **no filters at all** | `sum by (model)(sum_over_time(M[Bs]))`, max bucket taken | ⚠ metric carries only `model` — team-wide, no delta, and the scope-filter drop is undocumented |

## API Keys

All widgets are scoped by a two-step discovery: `sum by (service_account)(sum_over_time(cursor_event_cost_usd{F}[W]))` @ `to`+`from`, rows with the human sentinel `service_account="none"` dropped, survivors pinned as `service_account=~"a|b"`.

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| KPI "API Key Cost" | `cursor_event_cost_usd` | by `service_account`; F | the discovery breakdown itself — `current/previous = Σ rows` | — |
| Chart "Cost / Requests over Time" | `cursor_event_cost_usd` / `cursor_events_total` | `service_account=~"<accounts>"`; F | `sum(sum_over_time(M{service_account=~"…",F}[Bs]))` | short-circuits to empty when no accounts; ⚠ discovery breakdown re-issued per method (3× per load, no shareReplay) |
| Grid "Usage Breakdown" — User + Cost / Requests | `cursor_event_cost_usd` (limit 10), `cursor_events_total` | by `email`; `service_account=~"…"`; F | 2 breakdowns @ `to`+`from` | top 10 by cost (stated in tooltip); row click opens the user drawer |
| Grid column "Models" (chips) | `cursor_event_cost_usd` | by `model`; `[service_account=~"…", email="<row>"]`; F | one breakdown per surviving row (≤10) | chips ordered by cost |

# Impact tab

## Key Insights (one panel instance per tab)

All ~40 queries run in one forkJoin per window/filter change; rules read from the shared result set. `rosterSize` (from entities) caps the per-email breakdowns.

| Result → rules | Metric | Labels used | Query |
| --- | --- | --- | --- |
| spendByEmail → limit-breach, members-limited | `cursor_member_spend_gross_usd` | by `email`; F; limit rosterSize | `sum by (email)(last_over_time(M{F}[93600s]))` @ `to`+`from` |
| limitByEmail → limit-breach, members-limited | `cursor_member_effective_limit_usd` | by `email`; F | snapshot breakdown |
| overageByEmail → overage-running | `cursor_member_spend_overage_usd` | by `email`; F | snapshot breakdown |
| dailySpendSeries → daily-spike, z-score outlier | `cursor_member_daily_spend_usd` | by `email`; F | `sum by (email)(sum_over_time(M{F}[Bs]))` |
| costByEmail → spike/outlier/CPR-jump/power-user | `cursor_event_cost_usd` | by `email`; F; limit rosterSize | counter breakdown @ `to`+`from` |
| unitsByEmail → cost-per-request jump | `cursor_event_request_units_total` | by `email`; F | counter breakdown |
| costByModel + unitsByModel → expensive-model concentration, plan-cheap-implement-dear | `cursor_event_cost_usd`, `cursor_event_request_units_total` | by `model`; F | 2 counter breakdowns |
| planByModel + askByModel → plan-cheap-implement-dear | `cursor_user_plan_usage_total`, `cursor_user_ask_mode_usage_total` | by `model`; F | 2 counter breakdowns |
| totalCost / maxModeCost / totalEvents / maxModeEvents → max-mode premium | `cursor_event_cost_usd`, `cursor_events_total` | max-mode legs pin `max_mode="true"`; F | 4 window queries |
| listPrice → discount gap | `cursor_event_list_price_usd` | F | window query; discount = 1 − cost/list |
| tokensByType → cache under-used | `cursor_event_tokens_total` | by `token_type`; F | counter breakdown (reads input / cache_read / cache_write) |
| requestsByClass → outgrown subscription | `cursor_requests_by_class_total` | by `billing_class`; F | counter breakdown |
| bugbotReviewCost + totalMemberSpend → bugbot spend share | `cursor_bugbot_review_cost_usd`, `cursor_member_daily_spend_usd` | F | 2 window queries |
| bugbotReviewsTotal + dryRun → reviews unpublished | `cursor_bugbot_reviews_total` | 2nd leg pins `dry_run="true"`; F | 2 window queries |
| activeByEmail → idle seats | `cursor_member_active` | by `email`; F | `max by (email)(max_over_time(M{F}[W]))` |
| activeByVersion → stale clients | `cursor_member_active` | by `client_version`; F | flag breakdown; stale = ≥2 releases behind the harvested newest |
| agentSuggested/Accepted + tab pair → rejection churn, tab-beats-agent | `cursor_user_agent_diffs_suggested_total`, `…accepted_total`, `cursor_tab_suggestions_total`, `cursor_tab_accepts_total` | F | 4 window queries |
| complexity/guidance snapshot pairs → autonomous-complex-work | `cursor_conversation_snapshot` | `value="high"` / `dimension="complexity"` / `value="low"` / `dimension="guidanceLevels"`; F | 4 window queries |
| ktlo pair → KTLO share rising | `cursor_conversation_total` | `value="ktlo"` / `dimension="workTypes"`; F | 2 window queries (current + previous) |
| agentRequestsSeries → agent-requests spike | `cursor_requests_total` | by `email`; `surface="agent"`; F | `sum by (email)(sum_over_time(M{surface="agent",F}[Bs]))` |
| usageBased pair + cycle start → plan exhausted mid-cycle | `cursor_requests_by_class_total`, `cursor_billing_cycle_start_seconds` | by `email`; `billing_class="usage_based"`; F (cycle scalar unfiltered) | breakdown + series + snapshot window |
| thinking/max-mode cost by email → cost-driver notes | `cursor_event_cost_usd` | by `email`; `model="<thinking id>"` / `max_mode="true"`; F | 2 breakdowns (thinking skipped when no model id contains "thinking") |
| costByModelByEmail → "top model" notes | `cursor_event_cost_usd` | by `email`; one breakdown per catalog model, `model="<id>"`; F | N × breakdown — the panel's biggest fan-out |

Insight CTAs open the breakdown drawer with rows already computed above (no new query); the three cycle-snapshot drawers hide the delta column (previous ≈ current by construction).

## Work Type

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| Pie "Work Type Mix" | `cursor_conversation_total` | by `value`; `dimension="workTypes"`; F | `sum by (value)(sum_over_time(M{dimension="workTypes",F}[W]))` @ `to`+`from` | slice click → drawer from loaded rows |
| Chart "Work Type Over Time" | `cursor_conversation_total` | by `value`; `dimension="workTypes"`; F | grouped series | point click → drawer from loaded series |
| Pie "Task Complexity" | `cursor_conversation_total` | by `value`; `dimension="complexity"`; F | grouped breakdown | — |
| Chart "Task Guidance Over Time" | `cursor_conversation_total` | by `value`; `dimension="guidanceLevels"`; F | grouped series | substitution for Cursor's "Prompt Specificity" |

## AI Code (Code Impact)

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| KPI "Accepted Lines +/-" | `cursor_accepted_lines_added_total`, `cursor_accepted_lines_deleted_total` | F | 2 window queries | no-data only when both halves empty |
| KPI "Acceptance Rate" | `cursor_user_agent_diffs_accepted_total`, `…suggested_total` (+ `…rejected_total` fetched, tooltip-only) | F | 3 window queries | accepted ÷ suggested — never reconstructed |
| KPI "Commits" | `cursor_commits_total` | F | window query | click scrolls to Repositories |
| Chart "AI vs Human Lines" | `cursor_ai_code_lines_total` | two grouped series: by `surface` and by `direction`; F | `sum by (surface)(…[Bs])`  • `sum by (direction)(…[Bs])` | tab+composer folded into "AI"; added/deleted allocated per bucket by the direction margin (cross-tab approximated) |
| Chart click drawer | `cursor_ai_code_lines_total` | by `surface` and by `email` (limit 10); F | 2 breakdowns @ `to`+`from` (prefetched) | window-wide, not bucket-scoped |

## Git Repositories (shared repo-breakdown section)

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| Repo cost pie + bar ("Est. Cost") | `cursor_event_cost_usd`  • `cursor_ai_code_total_lines_added_total` (allocation), plus breakdowns of `cursor_ai_code_total_lines_added_total` and `cursor_commits_total` | allocation: by `email` and by `email, repo_name`; breakdowns by `repo_name`; F | `estimatedCostByRepo()` 4 instants + 2 repo breakdowns | derived allocation — billing events carry no repo; managed/unmanaged classification is client-side |
| Grid "Top Users on Unmanaged Repositories" | `cursor_ai_code_total_lines_added_total` (by `repo_name`), `estimatedCostByRepo`, `cursor_event_cost_usd` (window), `cursor_user_model_messages_total` (by `model`) | each with `email="<member>"` pinned; F | 4 engine calls × roster size, one forkJoin | top 5 by external est. cost; heaviest fan-out on the page |

## Bugbot

Bugbot metrics carry no `email` → User/Billing-Group filters are a deliberate no-op; scope filters still apply.

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| KPI "PRs Reviewed" (+ tooltip review count) | `cursor_bugbot_prs_reviewed`, `cursor_bugbot_pr_reviews_total` | F | 2 window queries | — |
| KPI "Findings" (+ resolved tooltip) | `cursor_bugbot_issues_snapshot` | `state="found"` / `state="resolved"`; F | 2 window queries | resolved ⊆ found — never summed |
| KPI "Bugbot Coverage" | `cursor_bugbot_repos` | by `enabled`; F | `sum by (enabled)(last_over_time(M{F}[93600s]))` | trendless by design; ⚠ metric has no team stamp → a Team filter zeroes it |
| Coverage drawer "PRs reviewed by repository" | `cursor_bugbot_prs_reviewed` | by `repo_name`; F; limit = harvested repo count | counter breakdown, zero-filled from the harvested repo list | KPI click only |
| Chart "Findings by Severity" | `cursor_bugbot_issues_total` | by `severity`; `state="found"` and `state="resolved"`; F | 2 breakdowns @ `to`+`from` | "Open" = found − resolved, severity-ordered |

# Drawers

## User drawer (email pinned into F on every call)

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| KPI "Cost" | `cursor_member_daily_spend_usd` | F + `email="<user>"` | window query | — |
| KPI "User Requests" | `cursor_requests_total` | F + email | window query | — |
| KPI "API Key Requests" | `cursor_requests_by_class_total` | `billing_class="api_key"`; F + email | window query | subset of User Requests |
| Chart "Cost / Tokens Over Time" (toggle) | `cursor_event_cost_usd` / `cursor_event_tokens_total` | by `model`; F + email | `sum by (model)(sum_over_time(M{email="…",F}[Bs]))` | top 6 + Other |
| Mini bars "Commands / MCP Servers / Skills" | `cursor_user_command_usage_total`, `cursor_user_mcp_usage_total`, `cursor_user_skill_usage_total` | by `command_name` / `mcp_server_name` / `skill_name`; F + email | 3 breakdowns | top 3 client-side |
| Pie "Work Type Mix" | `cursor_conversation_total` | by `value`; `dimension="workTypes"`; F + email | grouped breakdown | ⚠ real backend emits no `email` on this family → shows team-wide mix |
| Pie "Commit Types" | `cursor_commits_total` | by `branch_name`; F + email; no limit (unbounded label, folded client-side) | counter breakdown | classified into 6 work-nature buckets |
| KPIs "Accepted Lines +/- / Commits / Accept Rate" | `cursor_accepted_lines_added_total`, `…deleted_total`, `cursor_commits_total`, `cursor_accepts_total`, `cursor_applies_total` | F + email | 5 window queries | accept rate = accepts/applies |
| Bar "Git Repositories" + "Repo Type" pie | `cursor_ai_code_total_lines_added_total`, `cursor_commits_total` (by `repo_name`) + `estimatedCostByRepo` | by `repo_name`; F + email | 2 breakdowns + the 4-instant allocation | bar click opens the repo drawer |
| Grid "Conversations" (top 50) | usage-event family via `conversationsTable` | by `conversation_id`; F + email | 5 instants + firstTs range query, limit 50 | costliest-first |

## Repo drawer (`repo_name` pinned)

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| KPI "Est. Cost" | allocation pair (`cursor_event_cost_usd`  • `cursor_ai_code_total_lines_added_total`) | F | `estimatedCostByRepo()`, row picked by repo name | missing row → blank, not $0 |
| KPI "Commits" | `cursor_commits_total` | `repo_name="<repo>"`; F | window query | — |
| Bar "Est. Cost by Member" + KPI "Active Users" | `cursor_ai_code_total_lines_added_total` | `repo_name="<repo>"`  • `email="<member>"` — one window per roster member (~16) | N window queries | repo cost split ∝ member line share; Active Users = members with lines > 0 |
| KPI "AI Lines +/-" | `cursor_ai_code_total_lines_added_total`, `…deleted_total` | `repo_name="<repo>"`; F | 2 window queries | — |
| KPI "Code Committed by AI" | `cursor_ai_code_lines_total` | `[repo_name, surface="tab"|"composer", direction="added"]`; F | 2 window queries ÷ total added | 0 denominator → N/A |
| Bar "Commit Types" | `cursor_commits_total` | by `branch_name`; F with `email` replaced by the contributor list | counter breakdown | approximation: contributors' commits on ANY repo (breakdown can't be repo-scoped per label limits) |
| Bugbot KPIs + "Findings by Severity" | `cursor_bugbot_issues_total` | by `state`; by `severity` with `state="found"` / `"resolved"`; `repo_name` pinned; **no page filters** | 3 breakdowns | findings carry no user attribution |

## Breakdown drawer (generic)

Rows always come from the calling widget (no fetch of its own). For `rowsAreUsers` payloads it enriches once per open: `perUserTable(['cursor_member_daily_spend_usd','cursor_event_tokens_total'], {F})` → Group / Cost / Tokens columns joined by email; unmatched emails render "—".

# User tags ("Usage Patterns")

Computed once per window/filter change at the dashboard shell; quantiles taken over the whole in-view roster.

| Input | Metric | Labels used | Query | Feeds |
| --- | --- | --- | --- | --- |
| requests / spend / acceptRate | `cursor_requests_total`, `cursor_member_daily_spend_usd`, `cursor_accepts_total`, `cursor_applies_total` | by `email`; F | `perUserTable` (4 per-email window queries) | low-usage, over-budget, premium-model, cost-efficient, adoption score |
| daysActive | `cursor_member_active` | by `email`; F | grouped series step 1 bucket — flag counted per bucket | power-user, consistency term |
| maxModeShare | `cursor_events_total` | by `email`; numerator pins `max_mode="true"` | 2 breakdowns | deep-thinker, premium-model |
| contextPerRequest | `cursor_event_tokens_total` | by `email`; `token_type="cache_read"` | breakdown | long-sessions / short-sessions |

# Empty state

- Whole-dashboard gate: `checkHasData()` → `count(last_over_time(cursor_events_total[48h]))` @ window end — unfiltered by design (integration presence, not a filtered query). Renders optimistically; collapses to the docs empty state only on a confirmed `false`.

# Appendix — dead / duplicated queries worth cleaning up

1. ✅ **Fixed 2026-08-30** — API Keys service-account discovery breakdown was issued 3× per load (KPI, chart, grid); now computed once per window/filter change and shared via `shareReplay` in `#serviceAccountRows()`.
2. ✅ **Fixed 2026-08-30** — Conversations dead legs removed: `getPlanUsage`/`getAskUsage` no longer fetch `window()` totals (loading state now comes from the rendered breakdown); the guidance + complexity prefetch legs are deleted (`getConversationBreakdowns()` → `getIntentsBreakdown()`, intents only); the intent chart's `by (dimension)` reconstruction leg is deleted (provably dead — no intents value is cross-dimension ambiguous).
3. ✅ **Fixed 2026-08-30** — Code Impact no longer fetches `cursor_user_agent_diffs_rejected_total` (its tooltip is static copy; the Overview Agent-Edits KPI and key-insights fetches of rejected counts are untouched).
4. **Models grid "Users" column** queries `cursor_model_distinct_users` with no filters at all — the scope-filter drop is undocumented.
5. **Autonomy / High-Complexity KPI numerators** scope on `value` alone while `low/medium/high` exist in two dimensions — the shares merge counts across complexity and guidanceLevels.
6. **Adoption KPI clicks** re-issue the seat breakdown instead of reusing the loaded resource (entities + breakdown + drawer `perUserTable` per click).
7. **Repositories external-users grid** fans out 4 engine calls × roster size per load.