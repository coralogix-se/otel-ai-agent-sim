# ChatGPT Dashboard — Real Query Engine Map

Full map of **widget → metric / log → labels → query** for the **ChatGPT dashboard** (`ChatGptDashboardComponent`, selector `cxai-chatgpt-compliance-dashboard`, folder `libs/ai-center/code-agents/src/lib/chatgpt-compliance/` — the folder name says "compliance" but this is the adoption & cost page; the posture page `chatgpt-compliance-posture/` is out of scope). Sidebar row "AI chat apps → ChatGPT" → route `ai-center/code-agents/openai/chatgpt`, guarded by `featureFlagRouteGuard('ai-center-openai-chatgpt', { fallback: ['../../overview'] })` under the `code-agents` flag + `aiCodeAgentsReadPermissionGuard`. Engines: `data/chatgpt-compliance-queries.service.ts` (wiring + caches), `data/chatgpt-compliance-promql.ts` (window, matchers, identity join), `data/queries/*.queries.ts` (PromQL maps) and `data/queries/raw-log/*.queries.ts` (DataPrime), plus `openai-shared/compliance/` (`promql-matchers`, `compliance-counters`, `compliance-raw-log`, `compliance-feed-freshness`, `compliance-collector.constants`). Transport is `AiCenterPromqlService` / `AiCenterDataprimeService` (`@cx/ai-center`); `prometheus-query.service.ts` and `promql-queries.ts` are **not** used by this page. Traced code-level on 2026-11-08 on `origin/master` @ `89abdb48f52` (created in #23369, reshaped in #27267, docs link fixed in #27325): component → panel `rxResource` → query-service method → query builder, plus a pass over every template and call site. **Not run against live data**; ⚠ marks claims listed in the last section. Sibling pages: [ChatGPT Compliance Dashboard — Real Query Engine Map](https://app.notion.com/p/ChatGPT-Compliance-Dashboard-Real-Query-Engine-Map-412c048defad8146a6b4ddf29e214cf2?pvs=21), [Codex Dashboard — Real Query Engine Map](https://app.notion.com/p/Codex-Dashboard-Real-Query-Engine-Map-6913910af3bc4fd6966b6b78fb64100a?pvs=21), [Codex Compliance Dashboard — Real Query Engine Map](https://app.notion.com/p/Codex-Compliance-Dashboard-Real-Query-Engine-Map-412c048defad8135918bfeb7f2850ed0?pvs=21), [OpenAI API Platform Dashboard — Real Query Engine Map](https://app.notion.com/p/OpenAI-API-Platform-Dashboard-Real-Query-Engine-Map-cc9525d6a3214210a45ad8fd10dab354?pvs=21).

<aside>
⚙️

Three planes feed this page. (1) **Costs ledger** `openai_compliance_costs_{credits,usd,tokens}` — hourly rows keyed by `date`/`hour` labels, organization-wide (no `workspace_id`, no `user_id`), Codex dropped by `{product!="codex"}`; read with `sum by (…)(last_over_time(…))` late enough to see the ledger's lag. Feeds Cost, Activity tokens, Models used, the spend insight and the Model drawer. (2) **Compliance metrics** — daily inventory snapshots (`openai_compliance_user_state`, `_invites_total`, `_gpts_total`, `_projects_total`, `_gpt_shared_users_total`, `_project_shared_users_total`, `_gpt_tools_total`, `_plugins_total`, `_agents_total`, `_skills_total`, `_plugin_mcp_servers_total`, `_agent_connectors_total`) read with `last_over_time` + keep-newest-`date`; event counters (`openai_compliance_events_total`, `_connector_calls_total`) read with the `CT()` expression; identity (`openai_compliance_user_identity`, fallback `openai_analytics_codex_credits`); health (`openai_compliance_users_total`, `_log_feed_lag_seconds`). All carry `workspace_id` except identity and health. (3) **Raw Compliance log** `default/ai.raw.openai.compliance_logs` (DataPrime, streams `conversation_message`, `app_auth`, `app`, `costs`, `auth`, `agents`, `skills`) — fills what no metric label carries (who wrote messages, who linked apps, per-member credits, item lists). Every raw read fails open to "not reported".

</aside>

# Conventions

- `F_ws` — Workspace chip → `labelMatcher('workspace_id', …)`: one value `workspace_id="…"`, several `workspace_id=~"a|b"` (regex-escaped), none → no matcher. `F_user` — same for `user_id`; only the Member drawer sets it. `selector(…)` drops empty clauses, so an unfiltered read is `metric[…]` with no braces.
- `W` = max(1, ceil((to − from) / 1 day)). `[S]` snapshot lookback = `[max(W,2)d]`. `[I]` identity lookback = `[max(W,2,30)d]`. `[R]` counter range = `[max(round((to − from)/1s), 60)s]`. `[L]` connector last-call lookback = `[max(W,90)d]`. `[H]` ledger lookback = `[max(1, ceil((T_c − from)/1h) + 1)h]`.
- Window: `resolveComplianceWindow(cxAiTimeRange(), Date.now())`, bounds floored to whole seconds; unusable URL bounds → **7-day fallback** ending now. Default picker choice is **Last 14 days** (set in the constructor so the week-over-week rule has 14 buckets). Quick presets: 24h, 2, 3, 5, 7, 14 days.
- Evaluation: every query is an **instant** (no `query_range` on this page). Default time = `to`. Ledger reads run at `T_c = min(now, to + 3d)` and select rows by `date`/`hour` labels inside `[from, to)`. `checkHasData` and feed freshness run at `now`. The Chatting Activity calendar ignores the picker: window = (today 00:00 UTC − 364 d) → now.
- Quick mode: `pinQuickModeAnchor(true)` while the page is mounted, so deferred panels resolve the same `from|to` and hit the service caches. `T_c` still moves with `now`, so a cost read keyed in a later minute re-runs.
- `CT(r)` = `counterTotals(r)` = `(sum_over_time(r) * (max_over_time(r) <= bool clamp_min(resets(r), 1)) + (max_over_time(r) + resets(r)) * (1 - (max_over_time(r) <= bool clamp_min(resets(r), 1))))` — per-series choice between per-event points (sum) and a running total (max + resets).
- `L(m, dims)` = `sum by (dims) (last_over_time(m{product!="codex"}[H]))` at `T_c`.
- DataPrime: `RL(stream, clauses)` = `source default/ai.raw.openai.compliance_logs` + newline + `| filter $d.stream == 'openai.compliance.<stream>' && <clauses>`. `DP_ws` = `$d.data.principal.id == '…'` (OR-group for several); `DP_user` = `$d.data.actor.user_id == '…'`. Archive tier (service default), `limit 5000` (`RAW_LOG_ROW_LIMIT`), 30 s timeout. A read that errors, times out, or (for conversations, grants, member costs, snapshots) returns ≥ 5000 rows yields `null` → widget shows "Not reported" / N/A or its column hides.
- Caching: `AiCenterPromqlService` only de-dups **in-flight** identical requests. `ChatGptComplianceQueriesService` keeps `shareReplay` caches (≤ 20 keys each, a failed read is evicted): cost (`from|to|evalMinute`), roster, conversations (`from|to|filters`), shared assets (`from|to|filters`), app grants (`to|filters`), custom agents / skills (`to|workspace`). Uncached: has-data, feed freshness, workspace ids, pending invites, tool calls, cost breakdown, messages, messages by day, member costs, app use, sign-ins.
- Identity: seat key = `workspace_id|user_id` (metrics) ↔ `$d.data.principal.id|$d.data.actor.user_id` (raw log) ⚠. Email/name = `openai_compliance_user_identity{email|user_email,name}` by `user_id`, else `openai_analytics_codex_credits{user_email}` by `user_id` (Codex users only) ⚠; display = name ?? email ?? user_id. Model = ledger `model` ↔ raw `$d.data.message.author.model` with `-wm` stripped and `UNKNOWN` dropped. Raw inventory ids are account-scoped `user-…__<workspace>`; the workspace is the suffix.
- Flag model: the only flag is the route guard / sidebar row `ai-center-openai-chatgpt` (snapshot). **No widget inside the dashboard reads a feature flag** — every row below is "not flag-gated".
- Fail-soft: a metrics method runs its map with `forkJoin`; any non-`optional` query failing fails the whole method → the panel's error state. `optional` (identity reads, feed freshness) → empty vector. `checkHasData` error → treated as "has data" (no banner). Raw-log reads never throw.

# Data contract for simulators

What a simulator must emit for every widget on this page to render, derived from the query code only. Numbers below are code thresholds or stated minimums, never observed values.

## Metric families to emit

| Family (metric names / dataset + stream) | Shape & cadence | Required labels (joins) | Values that render | Feeds (widgets) |
| --- | --- | --- | --- | --- |
| `openai_compliance_costs_credits`, `openai_compliance_costs_usd`, `openai_compliance_costs_tokens` | Gauges, one series per (labels, `date`, `hour`) = that usage hour's amount; re-emitting the same value is harmless (`last_over_time` keeps one). The query selects rows by the `date`/`hour` labels, not sample time, and looks back `[H]` from `T_c` = up to 3 days after `to`, so a row may be emitted up to 3 days after its hour. Never cumulative across hours. | all: `date` (`YYYY-MM-DD`), `hour` (`0`–`23`), `product`, `model`; credits also `surface`, `client`, `reasoning` (one series carries all of them — Cost groups the same metric two ways); tokens also `service_tier`, `token_type`. No `user_id`, no `workspace_id`. `model` joins the raw log's `message.author.model` | `product`: `ChatGPT` / `Work` (either case) get their own band, anything else → "Other"; never `codex` (dropped). `token_type`: `{text,image,audio}_{input,cached_input,output}_tokens` (others ignored). Credits and USD are separate series; the page never derives one from the other | Cost (all), Activity tokens (ChatGPT product only), Models used, spend insight, Model drawer |
| `openai_compliance_events_total{event_type="CONVERSATION_MESSAGE"}` | Counter. `CT()` reads either form; emit the cumulative form: one series per (labels, `date`), value = running day total | `workspace_id`, `date`, `client_id` (no `user_id`) | `client_id` `''` → "Not reported" | Messages over time, Messages by client, Chatting Activity calendar, sidebar detection (`sum_over_time(…[10d]) > 0`) |
| `openai_compliance_connector_calls_total` | Counter, same cumulative-per-(labels, `date`) form | `workspace_id`, `connector_name`, `action_name`, `call_phase` (`requested` / `completed`), `status` (on `completed`; `failed` counts as Failed), `requires_elicitation` (on `requested`; `false` = ran without confirmation), `date` (Last seen). `connector_name` joins raw `app_name` ⚠ | each call ticks `requested` once and `completed` once | Most used tools, Connected apps |
| `openai_compliance_user_state` | Daily snapshot gauge: one series per seat per `date`; value ignored (presence = seat). The newest `date` per workspace inside `[S]` is the roster | `workspace_id`, `user_id`, `role`, `status`, `is_scim_managed`, `date`. `user_id` joins identity and the raw log `actor.user_id`; `workspace_id` joins `principal.id` | `role`: `account-owner` / `account-admin` / `standard-user`; `status`: `active` / `inactive`; `is_scim_managed`: `true` / `false` (any case) | Users grid, Workspace options, idle-seat insight, Member drawer header, skill creators |
| `openai_compliance_invites_total` | Daily snapshot gauge (value = count), emitted on the **same `date`** as `user_state` | `workspace_id`, `role`, `date` | count > 0 | Users header "N pending invites" |
| `openai_compliance_user_identity` (SPEC_PENDING in code) | Gauge, any cadence within `[I]` (≥ 30 d) | `user_id`, `email` (or `user_email`), `name`; `service_name` ≠ `cx-codex-compliance-puller` | name / email text | every member display name; without it names fall back to `openai_analytics_codex_credits{user_id,user_email}`, then to the raw `user_id` |
| `openai_compliance_gpts_total`, `openai_compliance_projects_total` | Daily snapshot gauges (value = count), newest `date` per workspace | `workspace_id`, `visibility`, `can_read`, `can_view_config`, `can_write`, `date` | `visibility`: `invite-only`, `workspace-with-link`, `workspace`, `anyone-with-link`, `gpt-store` (others render raw); `can_*`: `true` / `false` | Shared GPTs, Projects |
| `openai_compliance_gpt_shared_users_total`, `openai_compliance_project_shared_users_total` | Daily snapshot, on the **same `date`** as the asset snapshot (else ignored) | `workspace_id`, `role`, `status`, `can_write`, `date` | shares > 0 | Shared with |
| `openai_compliance_gpt_tools_total` | Daily snapshot, same `date` as `gpts_total` | `workspace_id`, `tool_type`, `auth_type`, `date` | tools > 0 | GPT tools |
| `openai_compliance_plugins_total`, `openai_compliance_agents_total`, `openai_compliance_skills_total` | Daily snapshots | plugins: `workspace_id`, `status`, `installation_policy`, `discoverability`, `authentication_policy`, `date`; agents: `workspace_id`, `status`, `date`; skills: `workspace_id`, `original_product_surface`, `date` | count > 0 | Plugins; Custom agents / Skills fallback tables |
| `openai_compliance_plugin_mcp_servers_total`, `openai_compliance_agent_connectors_total` | Daily snapshots | MCP: `workspace_id`, `plugin_id`, `url_host`, `date`; grants: `workspace_id`, `connector_id`, `user_approvals_mode`, `date` | value > 0; `user_approvals_mode` `never` gets the warning chip | Connected apps (MCP rows), Agent connector grants |
| `openai_compliance_users_total`, `openai_compliance_log_feed_lag_seconds` | Health gauges. Any series of either in the last 10 days hides the setup banner. The lag metric's **sample time** is read (`timestamp()`), per `event_type` | lag: `event_type` ∈ `CONVERSATION_MESSAGE`, `AUDIT_LOG`, `AUTH_LOG`, `APP_LOG`, `APP_AUTH_LOG`, `CUSTOM_AGENTS_LOG`, `COSTS` | emit each type at least every 3 h, or the stall insight fires | Banner, "Compliance export stopped" insight |
| `default/ai.raw.openai.compliance_logs`, stream `openai.compliance.conversation_message` | One log per message | `$d.data.principal.id`, `$d.data.actor.user_id`, `$d.data.message.author.model` (empty on the member's own message, the model on an answer; `-wm` suffix stripped, `UNKNOWN` ignored) | < 5000 groups (workspace × user × model) per window, or every consumer reads "not reported" | Active members, Users / Top model for, idle-seat + concentration insights, Model drawer users, Member drawer activity |
| same dataset, streams `app_auth`  • `app` | One log per link/unlink and per app call | `app_auth`: `$d.data.app_id`, `principal.id`, `actor.user_id`, `actor.user_email`, `action` (`link` = connected); `app`: `$d.data.app_id`, `$d.data.app_name`, `principal.id`, `actor.user_id` | grants read 90 days back | Connected apps Owners / Connection, Users Connected apps column, Member drawer apps |
| same dataset, stream `costs` | One record per usage hour with `measures.billing[]` | `$d.data.payload.{product, identity.user_id, day, hour, surface, client, model, service_tier, reasoning}`, `measures.billing[].{sku, cost.value, estimated_cost_usd.value}` | `product` ≠ `codex` | Member drawer Consumption (the only per-user credits) |
| same dataset, streams `auth`, `agents`, `skills` | `auth` per event; `agents` / `skills` one daily snapshot run, all rows of a run sharing one timestamp | `auth`: `$d.data.action_data.action` (`login_success`); `agents`: `$d.data.{id,name,status,owner_email,owner_id,created_at,updated_at}`; `skills`: `$d.data.{id,name,original_product_surface,creator,created_at,updated_at}`; `owner_id` / `creator` = `user-…__<workspace>`; times in seconds | snapshot within the last 2 days | Member drawer Last sign-in / Shared assets; Custom agents and Skills lists |

## Simulator Q&A

| Question | Answer |
| --- | --- |
| It only outputs one sample a day? | Depends on the family. Ledger: one series per **hour** (`date`  • `hour` labels). Inventory snapshots and `user_state`: one series per `date` (any number of samples per day; the newest `date` wins). Event counters: one series per (labels, `date`), value = running day total. Raw log: one record per event, `agents` / `skills` one snapshot per day. Feed-lag health metric: at least every 3 h per `event_type`. |
| What sort of daily cost value? | The code asserts no magnitude. Credits (`_costs_credits`) and USD (`_costs_usd`) are separate hourly series and both render as-is; nothing converts one into the other. The only cost threshold is the spend insight (below). |
| How many users / models / projects? Limits enforced on some projects? | This page has **no projects and no spend limits** (those are API Platform concepts). Entities are workspaces (`workspace_id`), seats (`user_id`), models (ledger `model`), connectors, plugins, GPTs/projects as counts. Hard limit: a raw-log grouped read returning ≥ 5000 rows is discarded (widgets read "not reported"). Display caps: top 10 tools, 5 bars visible per breakdown. |
| Minimum entities to light every widget (derived from code thresholds, not observed data) | ≥ 1 workspace with a `user_state` snapshot and `invites_total` > 0; ≥ 14 complete UTC days of ledger rows for `product="ChatGPT"` and `"Work"` with ≥ 2 models, `surface` / `client` / `reasoning` / `service_tier` filled; `CONVERSATION_MESSAGE` counters with ≥ 1 `client_id`; connector calls with both phases, one `failed`, one `requires_elicitation="false"`; every inventory family > 0; raw-log streams all present; ≥ 11 writing members for the concentration rule; > 30 % of seats with no message for the idle rule. |
| Insights and how to trigger each | "Compliance export stopped": any of the 7 feed types last listed > 3 h ago and < 7 days ago → stop emitting `log_feed_lag_seconds{event_type=…}` for one type. "Idle ChatGPT seats": seats with no `conversation_message` row ÷ roster seats > 30 % → e.g. 10 seats, ≥ 4 without messages. "Credit use is climbing": ≥ 14 charted daily ledger points, previous 7 days > 0, last 7 days ≥ 1.3 × previous 7 → ramp ChatGPT credits ≥ 30 % in the second week; the newest day must have all 24 hours or it is dropped. "One member drives ChatGPT use": a member's own messages ≥ 10 × the average writing member → minimum by arithmetic: 11 writers, one with ≥ 100 messages and ten with 1. |
| Per-user data — expected that the dashboard shows little per user? | Yes. The ledger, `events_total`, connector calls and every inventory snapshot carry **no user**, so Cost, Activity tokens and messages, Models used tokens/credits, Shared GPTs & projects and Connectors are workspace- or organization-wide. Per-user surfaces: Users grid (`user_state.user_id`), Active members and both member insights (raw `actor.user_id`), Models used Users / Top model for and Model drawer users (raw answers), Users Connected apps (raw `app_auth`), Member drawer (roster + 8 raw-log reads; credits only from the raw `costs` stream). Without the raw log those surfaces read "Not reported". |
| Is there official doc from OpenAI? | Compliance API / Compliance Platform for ChatGPT Enterprise & Edu — [https://help.openai.com/en/articles/9261474](https://help.openai.com/en/articles/9261474) (full API reference needs a workspace login). Codex enterprise governance (Analytics dashboard, Analytics API `api.chatgpt.com/v1/analytics/codex`, daily UTC buckets, key scope `codex.enterprise.analytics.read`) — [https://developers.openai.com/codex/enterprise/governance](https://developers.openai.com/codex/enterprise/governance) . Codex Security — [https://developers.openai.com/codex/security](https://developers.openai.com/codex/security) . Admin API usage/costs (`GET /v1/organization/costs`, admin key) — [https://platform.openai.com/docs/api-reference/usage](https://platform.openai.com/docs/api-reference/usage) . Coralogix: [https://coralogix.com/docs/integrations/ai-observability/openai/](https://coralogix.com/docs/integrations/ai-observability/openai/) , …/openai/codex/ , …/openai/chatgpt/ . |
| Any live telemetry somewhere? | EU2 (euprod2) teams `knowledge-base` and `cxai-dev` have real Codex Analytics, compliance metrics and costs. The raw dataset `default/ai.raw.openai.compliance_logs` exists in EU2 from ~2026-10-29 and has longer history on staging team `test-ai-center`. Producer: the Coralogix cloud collector (`openai_integrations`); the old laptop puller (`service_name="cx-codex-compliance-puller"`) stopped 2026-09-16. |
| Counter form to emit? | Compliance counters (`openai_compliance_*_total`) were OTLP delta (0/1 points per event) up to 2026-10-28, mixed on 2026-10-29, and a cumulative running total per (series, `date`) from 2026-10-30. `CT()` handles both; emit the cumulative form: one series per (labels, `date`), value = running day total. |
| Codex Analytics cadence (email fallback only) | `openai_analytics_codex_*`: one daily value per `date` label, restated on each daily pull; threads/turns arrive 0 on the first pull and fill 1–3 days later; credits/tokens 95–98 % on day+1. This page reads only `openai_analytics_codex_credits{user_id,user_email}` for emails. |

# Engine operation → query shape

| Op | Query shape | Evaluation |
| --- | --- | --- |
| `getCost` (`buildCostQueries`) | `L(openai_compliance_costs_credits, date, hour, product, model)`  • `L(openai_compliance_costs_usd, date, hour, product, model)`  • `L(openai_compliance_costs_tokens, date, hour, product, model, service_tier, token_type)` | 3 instants at `T_c`; rows kept when `date`+`hour` ∈ `[from, to)`; one point per UTC day from the first reported hour to the newest; newest day `partial` when < 24 hours reported. Ignores every filter. 5 consumers (Insights, Cost, Activity, Models, Model drawer) |
| `getCost({}, {previousPeriod})` | same 3 queries for `[from − span, from)` | `T_c' = min(now, from + 3d)`; feeds the period-over-period badges of Cost, Activity, Models |
| `getCostBreakdown` | `L(openai_compliance_costs_credits, date, hour, surface, client, reasoning)` | 1 instant at `T_c`; credits per `surface` / `client` / `reasoning`; `''` → "Not reported" |
| `getMessages` | `sum by (date, client_id) (CT(openai_compliance_events_total{event_type="CONVERSATION_MESSAGE",F_ws}[R]))` | 1 instant at `to`; days dated before `from` dropped; series from first day with a message; returns `{}` under `F_user` |
| `getMessagesByDay` | `sum by (date) (CT(openai_compliance_events_total{event_type="CONVERSATION_MESSAGE",F_ws}[Ns]))`, N = seconds from calendar start to now | 1 instant at now; picker ignored |
| `getWorkspaceRoster` | stage 1: `count by (workspace_id, date) (last_over_time(openai_compliance_user_state{F_ws}[S]))`  • `max by (user_id, email, name, user_email) (last_over_time(openai_compliance_user_identity{service_name!="cx-codex-compliance-puller",F_user}[I]))` (optional) • `max by (user_id, user_email) (last_over_time(openai_analytics_codex_credits{F_user}[I]))` (optional); stage 2: `max by (workspace_id, user_id, role, status, is_scim_managed, date) (last_over_time(openai_compliance_user_state{F_ws,F_user,date="<d>"}[S]))` (`date=~"d1|d2"` when workspaces' newest days differ) | 3 + 1 instants at `to`; rows = each workspace's newest snapshot `date`; value ignored (presence = seat) |
| `getPendingInvites` | `count by (workspace_id, date) (last_over_time(openai_compliance_user_state{F_ws}[S]))`  • `sum by (workspace_id, role, date) (last_over_time(openai_compliance_invites_total{F_ws}[S]))` | 2 instants; invites read on the roster's newest day; `{}` under `F_user` |
| `getSharedAssets` | 13 instants — snapshots `X{F_ws}[S]`: `sum by (workspace_id, visibility, can_read, can_view_config, can_write, date) (last_over_time(openai_compliance_gpts_total…))`, same for `openai_compliance_projects_total`; `sum by (workspace_id, role, status, can_write, date) (last_over_time(openai_compliance_gpt_shared_users_total…))`, same for `openai_compliance_project_shared_users_total`; `sum by (workspace_id, tool_type, auth_type, date) (last_over_time(openai_compliance_gpt_tools_total…))`; `sum by (workspace_id, status, installation_policy, discoverability, authentication_policy, date) (last_over_time(openai_compliance_plugins_total…))`; `sum by (workspace_id, status, date) (last_over_time(openai_compliance_agents_total…))`; `sum by (workspace_id, original_product_surface, date) (last_over_time(openai_compliance_skills_total…))`; `max by (workspace_id, plugin_id, url_host, date) (last_over_time(openai_compliance_plugin_mcp_servers_total…))`; `max by (workspace_id, connector_id, user_approvals_mode, date) (last_over_time(openai_compliance_agent_connectors_total…))` — counters: `sum by (connector_name, action_name) (CT(openai_compliance_connector_calls_total{call_phase="requested",F_ws}[R]))`  • `sum by (connector_name, call_phase, status, requires_elicitation) (CT(openai_compliance_connector_calls_total{F_ws}[R]))`  • `max by (connector_name, date) (max_over_time(openai_compliance_connector_calls_total{call_phase="requested",F_ws}[L]))` | at `to`; snapshots keep each workspace's newest `date`; shared-users / tools read on their asset's newest date; `{}` under `F_user`; cached, shared by Assets + Connectors |
| `getToolCalls` | `sum by (connector_name, action_name) (CT(openai_compliance_connector_calls_total{call_phase="requested",F_ws}[R]))` | 1 instant; its own read so a failing asset query cannot error the chart |
| `getWorkspaceIds` | `count by (workspace_id) (last_over_time(openai_compliance_user_state[S]))` | 1 instant, unfiltered, lazy on first dropdown open |
| `checkHasData` | `count(last_over_time(openai_compliance_users_total[10d]) or last_over_time(openai_compliance_log_feed_lag_seconds[10d]))` | 1 instant at now; fixed `[10d]`; error → true |
| `getFeedFreshness` | `max by (event_type) (max_over_time(timestamp(openai_compliance_log_feed_lag_seconds{service_name!="cx-codex-compliance-puller"})[7d:10m]))` | 1 instant at now (subquery); optional |
| DataPrime `getConversationRead` | `RL(conversation_message, DP_ws, DP_user) | groupby $d.data.principal.id as workspace_id, $d.data.actor.user_id as user_id, $d.data.message.author.model as model aggregate count() as messages, min($m.timestamp) as first_ns, max($m.timestamp) as last_ns` | `[from, to]`; empty `model` row = the member's own message, non-empty = an answer; null when ≥ 5000 rows |
| DataPrime `getAppConnections` | `RL(app_auth, DP_ws, DP_user) | groupby $d.data.app_id as app_id, $d.data.principal.id as workspace_id, $d.data.actor.user_id as user_id, $d.data.actor.user_email as user_email, $d.data.action as action aggregate max($m.timestamp) as last_ns`  • `RL(app, DP_ws) | groupby $d.data.app_id as app_id, $d.data.app_name as app_name aggregate count() as events` | `[to − 90d, to]`; newest `link`/unlink per app × seat; null when grants ≥ 5000 rows |
| DataPrime `getMemberCosts` | `RL(costs, $d.data.payload.product != 'codex', $d.data.payload.identity.user_id == '<id>') | explode $d.data.payload.measures.billing into $d.b original preserve | groupby $d.data.payload.day as day, $d.data.payload.hour as hour, $d.data.payload.product as product, $d.data.payload.surface as surface, $d.data.payload.client as client, $d.data.payload.model as model, $d.data.payload.service_tier as tier, $d.data.payload.reasoning as reasoning, $d.b.sku as sku aggregate max($d.b.cost.value:number) as credits, max($d.b.estimated_cost_usd.value:number) as usd | groupby day, hour, product, model aggregate sum(credits) as credits, sum(usd) as usd` | `[from, T_c]`; hours kept in `[from, to)`; no workspace clause |
| DataPrime `getAppUse` | `RL(app, DP_ws, DP_user) | groupby $d.data.app_name as app_name aggregate count() as calls, max($m.timestamp) as last_ns` | `[from, to]` |
| DataPrime `getLastSignIn` | `RL(auth, DP_ws, DP_user) | groupby $d.data.action_data.action as action aggregate count() as events, max($m.timestamp) as last_ns` | `[min(from, to − 90d), to]`; newest `login_success` |
| DataPrime `getCustomAgents` | `RL(agents, (!$d.data.owner_id.contains('__') || $d.data.owner_id.endsWith('__<ws>'))) | groupby $d.data.id as agent_id, $d.data.name as agent_name, $d.data.status as agent_status, $d.data.owner_email as owner_email, $d.data.owner_id as owner_id aggregate min($d.data.created_at:number) as created_s, max($d.data.updated_at:number) as updated_s, max($m.timestamp) as snapshot_ns` | `[to − 2d, to]`; newest snapshot run per workspace (1 h tolerance), newest row per id; workspace clause only when a chip is set |
| DataPrime `getSkills` | `RL(skills, (!$d.data.creator.contains('__') || $d.data.creator.endsWith('__<ws>'))) | groupby $d.data.id as skill_id, $d.data.name as skill_name, $d.data.original_product_surface as surface, $d.data.creator as creator_id aggregate min($d.data.created_at:number) as created_s, max($d.data.updated_at:number) as updated_s, max($m.timestamp) as snapshot_ns` | same as agents |
| Detection (parent Code Agents shell) | `count(sum_over_time(openai_compliance_events_total{event_type="CONVERSATION_MESSAGE"}[10d]) > 0)` | marks `CHATGPT` / `VENDOR_COMPLIANCE_API`; only when the flag is ON (`codeAgentDashboardProducts`) |

# Filters → matchers

| Page filter | Matcher emitted | Condition / translation |
| --- | --- | --- |
| Time range | ledger `[H]` at `T_c`  • `date`/`hour` row filter; snapshots `[S]`; identity `[I]`; counters `[R]`; last call `[L]`; raw log = picker window (grants / sign-ins ≥ 90 d back, snapshots last 2 d) | default Last 14 days; fixed windows ignore it: has-data `[10d]`, feed freshness `[7d:10m]`, Chatting Activity 365 days |
| Workspace (chip key `workspace`, label "Workspace", operator "is" only) | `workspace_id="…"` / `=~"a|b"` on user_state, invites, inventory snapshots, events_total, connector_calls_total; raw `$d.data.principal.id == '…'`; raw inventory `owner_id` / `creator` suffix `__<ws>` | options = `workspace_id` values (ids, no names). **Not applied** to: the costs ledger (Cost, Activity tokens, Models used, Model drawer, spend insight), identity reads, Models used / Model drawer member columns (read the conversation log unfiltered), member credits, has-data, feed freshness. Grid row clicks do not add chips |
| User (no chip — Member drawer only, `userId`) | `user_id="…"` on user_state stage 2 + identity; raw `$d.data.actor.user_id == '…'`; member costs `$d.data.payload.identity.user_id == '…'` | under a user: messages, pending invites and shared assets return an empty map (no `user_id` label); the drawer also pins the clicked row's `workspaceId`, replacing the page chip |
| Search boxes (Models, Plugins, Connected apps, Agent grants, Users) | none | client-side over loaded rows; drive the section count chips (Models, Users) |

# Entities harvest (filter dropdowns & rosters)

No label-values API calls — the one dropdown and every roster come from data queries; the Workspace options load once, when the dropdown first opens.

| Feeds | Query |
| --- | --- |
| Workspace options | `count by (workspace_id) (last_over_time(openai_compliance_user_state[S]))`, unfiltered |
| Seat roster (Users grid, idle-seat rule, member lookups) | `getWorkspaceRoster` 2-stage `openai_compliance_user_state` read, newest `date` per workspace |
| Member email / name | `openai_compliance_user_identity{email|user_email, name}` by `user_id` ⚠, else `openai_analytics_codex_credits{user_email}` by `user_id` |
| Active members / writers / answers per model | raw `conversation_message` groupby workspace × user × model |
| App name for an `app_id` | raw `app` stream, the `app_name` seen most often per id |
| Skill creator email | roster `user_id` → email (bare id from `creator`) |
| No model / user dropdowns | — |

# Insights (above the sections)

One `rxResource` runs `getCost(filters)` (3) + `getFeedFreshness` (1) + `getWorkspaceRoster(filters)` (4) in one `forkJoin` with `getConversationActivity(filters)` (1 DataPrime) — **8 instant PromQL + 1 DataPrime**, current window only. A cost or roster failure fails every card (feeds are optional, raw-log reads never throw). Rules render in this order. CTAs: the two member rules open the insight-members drawer, the spend rule scrolls to Cost, the stall rule has none. No rule adds a filter chip.

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| Rule "Compliance export stopped" | `openai_compliance_log_feed_lag_seconds` | `event_type`, `service_name` | `max by (event_type) (max_over_time(timestamp(openai_compliance_log_feed_lag_seconds{service_name!="cx-codex-compliance-puller"})[7d:10m]))` at now | not flag-gated; fires when any of CONVERSATION_MESSAGE, AUDIT_LOG, AUTH_LOG, APP_LOG, APP_AUTH_LOG, CUSTOM_AGENTS_LOG, COSTS was last listed > 3 h ago (types unseen in 7 d ignored); no CTA; ignores Workspace |
| Rule "Idle ChatGPT seats" | `openai_compliance_user_state`  • raw `conversation_message` | seat `workspace_id|user_id` ↔ `principal.id|actor.user_id` | roster stage 1 + 2 • conversations DataPrime | not flag-gated; idle = seat with no message row (own or answer); fires when idle / seats > 30%; silent when the raw log is unread or cut at 5000 rows. ⚠ silently flags every seat if the raw-log ids don't match the metric ids. CTA "View idle seats" → Seats going idle drawer |
| Rule "Credit use is climbing" | `openai_compliance_costs_credits` | `date`, `hour`, `product`, `model` | `L(openai_compliance_costs_credits, date, hour, product, model)` | not flag-gated; needs ≥ 14 charted daily points (partial newest day dropped); last 7 ≥ 1.3 × previous 7; organization-wide. ⚠ on the default 14-day range it fires only once yesterday's 24 hours are in (ledger lag), and the first day is partial (starts at `from`'s hour), biasing toward "climbing". CTA "Review credits over time" → scroll to Cost |
| Rule "One member / A few members drive ChatGPT use" | raw `conversation_message` (own messages, `model` empty) | `actor.user_id` | conversations DataPrime | not flag-gated; member ≥ 10 × average writing member; email from roster else raw `user_id`. CTA "View heaviest members" → Heaviest members drawer |

# Sections

Page order: Cost → Activity → Models used → Shared GPTs & projects → Connectors → Users. Every section is `@defer (on viewport)`; the Chatting Activity calendar has its own defer.

## Cost

7 instants: `getCost` (3, shared) + previous-window `getCost` (3, shared) + `getCostBreakdown` (1); all ledger, **organization-wide** (Workspace chip ignored), Codex excluded; read-only, no count chip.

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| KPI "Credits used" + "\<USD> cost" + trend badge | `openai_compliance_costs_credits`, `openai_compliance_costs_usd` | `date`, `hour`, `product`, `model` | `getCost` (3 instants) + previous-window `getCost` (3) | not flag-gated; credits = sum of all daily points incl. the partial day; USD as the ledger reports it; badge = current vs previous window over comparable points (partial last day and the previous window's last day dropped), `lower-is-better`; "N/A" when no ledger row in range |
| Chart "Credits over time" | `openai_compliance_costs_credits` | `product` → bands ChatGPT / Work / Other | `L(openai_compliance_costs_credits, date, hour, product, model)` | not flag-gated; daily stacked area; `ChatGPT`/`chatgpt` merged; partial newest day left out (tooltip says so) |
| Hbar "Credits by surface" | `openai_compliance_costs_credits` | `surface` | `L(openai_compliance_costs_credits, date, hour, surface, client, reasoning)` | not flag-gated; top 5 bars visible; `''` → "Not reported" |
| Hbar "Credits by client" | same | `client` | same query | not flag-gated |
| Hbar "Credits by reasoning" | same | `reasoning` | same query | not flag-gated; usage with no reasoning effort → "Not reported" |

## Activity

Ledger (6, shared with Cost) + `getMessages` (1) + `getToolCalls` (1) + calendar `getMessagesByDay` (1, own defer) + conversations (1 DataPrime, shared with Insights); tokens organization-wide, messages and tools follow Workspace; read-only.

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| KPI "Input tokens" (+ trend) | `openai_compliance_costs_tokens` | `product="ChatGPT"` (client-side), `token_type` ending `_input_tokens` (cached included) | `L(openai_compliance_costs_tokens, date, hour, product, model, service_tier, token_type)` | not flag-gated; **ChatGPT product only** (Work excluded, unlike Cost); organization-wide; trend vs previous window, `lower-is-better` |
| KPI "Output tokens" (+ trend) | same | `token_type` ending `_output_tokens` | same | not flag-gated |
| KPI "Active members" | raw `conversation_message` | distinct `actor.user_id` | conversations DataPrime (`DP_ws`) | not flag-gated; follows Workspace; "N/A" when the log is unread or ≥ 5000 rows |
| Chart "ChatGPT tokens over time" | `openai_compliance_costs_tokens` | ChatGPT product; input / output | same ledger query | not flag-gated; stacked Input / Output; partial newest day dropped |
| Chart "ChatGPT messages over time" | `openai_compliance_events_total{event_type="CONVERSATION_MESSAGE"}` | `date` | `sum by (date, client_id) (CT(openai_compliance_events_total{event_type="CONVERSATION_MESSAGE",F_ws}[R]))` | not flag-gated; follows Workspace; daily by the event's `date` label, rounded |
| Calendar "Chatting Activity" | same counter | `date` | `sum by (date) (CT(openai_compliance_events_total{event_type="CONVERSATION_MESSAGE",F_ws}[Ns]))`, N ≈ 365 d | not flag-gated; ignores the picker; days before the first message are faded |
| Hbar "Messages by client" | same counter | `client_id` | same `getMessages` query | not flag-gated; follows Workspace; `''` → "Not reported" |
| Hbar "Most used tools" | `openai_compliance_connector_calls_total` | `call_phase="requested"`, `connector_name`, `action_name` | `sum by (connector_name, action_name) (CT(openai_compliance_connector_calls_total{call_phase="requested",F_ws}[R]))` | not flag-gated; top 10 `connector.action`; share of all calls |

## Models used

Ledger (6, shared) + conversations read **unfiltered** (1 DataPrime); one row per ledger `model`; count chip = filtered rows; row click → Model drawer.

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| Model | ledger `model` | `model` | `getCost` (shared) | not flag-gated; rows = models with tokens, credits or USD |
| Tokens (+ delta) | `openai_compliance_costs_tokens` | `model`, `token_type` (input + output) | `L(openai_compliance_costs_tokens, …)` | not flag-gated; all non-Codex products; delta vs previous window; default sort |
| Share | derived | — | — | not flag-gated; model tokens ÷ all models' tokens |
| Credits / Est. cost (USD) | `openai_compliance_costs_credits` / `_usd` | `model`, `product` | `L(openai_compliance_costs_credits, …)` / `L(openai_compliance_costs_usd, …)` | not flag-gated |
| Users / Top model for | raw `conversation_message` answers | `message.author.model` (−`-wm`), `actor.user_id` | conversations DataPrime, **no filters** | not flag-gated; columns hidden when the log is unread; ignores Workspace |
| Products | ledger `product` | `product` | `getCost` | not flag-gated; chips |

## Shared GPTs & projects

`getSharedAssets` (13, shared with Connectors) + `getWorkspaceRoster` (4, shared with Users) + custom agents and skills (2 DataPrime); rows = snapshot groups or raw-log items; follows Workspace; read-only, no count chip.

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| Table "Shared GPTs" — Visibility / GPTs / Can read / Can view config / Can write | `openai_compliance_gpts_total` | `visibility`, `can_read`, `can_view_config`, `can_write`, `date` | `sum by (workspace_id, visibility, can_read, can_view_config, can_write, date) (last_over_time(openai_compliance_gpts_total{F_ws}[S]))` | not flag-gated; newest `date` per workspace; header total = sum |
| Table "Projects" — same columns | `openai_compliance_projects_total` | same | same shape on `openai_compliance_projects_total` | not flag-gated |
| Table "Shared with" — Asset / Role / Status / Access / Shares | `openai_compliance_gpt_shared_users_total`, `openai_compliance_project_shared_users_total` | `role`, `status`, `can_write` | `sum by (workspace_id, role, status, can_write, date) (last_over_time(openai_compliance_gpt_shared_users_total{F_ws}[S]))` (+ project twin) | not flag-gated; read on the GPT / project snapshot's newest date; "No direct shares" empty state |
| Table "GPT tools" — Tool / Auth / Tools | `openai_compliance_gpt_tools_total` | `tool_type`, `auth_type` | `sum by (workspace_id, tool_type, auth_type, date) (last_over_time(openai_compliance_gpt_tools_total{F_ws}[S]))` | not flag-gated; on the GPT snapshot's newest date |
| Table "Custom agents" — Agent / Owner / Status / Created / Updated (fallback Status / Agents) | raw `agents` stream; fallback `openai_compliance_agents_total` | `owner_id` suffix; `status` | `getCustomAgents` DataPrime; fallback `sum by (workspace_id, status, date) (last_over_time(openai_compliance_agents_total{F_ws}[S]))` | not flag-gated; list when the raw snapshot is read whole, else counts by status; total = list length or metric total |
| Table "Skills" — Skill / Creator / Source / Created / Updated (fallback Source / Skills) | raw `skills` stream; fallback `openai_compliance_skills_total` | `creator`; `original_product_surface` | `getSkills` DataPrime; fallback `sum by (workspace_id, original_product_surface, date) (last_over_time(openai_compliance_skills_total{F_ws}[S]))` | not flag-gated; creator email from `getWorkspaceRoster(filters)` |

## Connectors

Section id `appConnectors`. `getSharedAssets` (13, shared) + app grants and names (2 DataPrime, shared with Users); rows = snapshot groups, connectors and MCP hosts; follows Workspace; read-only, no count chip.

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| Table "Plugins" — Status / Install policy / Discoverability / Auth policy / Plugins | `openai_compliance_plugins_total` | `status`, `installation_policy`, `discoverability`, `authentication_policy` | `sum by (workspace_id, status, installation_policy, discoverability, authentication_policy, date) (last_over_time(openai_compliance_plugins_total{F_ws}[S]))` | not flag-gated; newest date per workspace |
| Table "Connected apps" — App / Type / Calls / Failed / Ran without confirmation / Last seen / Heartbeat | `openai_compliance_connector_calls_total`, `openai_compliance_plugin_mcp_servers_total` | `connector_name`, `call_phase`, `status`, `requires_elicitation`, `date`; `url_host`, `plugin_id` | `sum by (connector_name, call_phase, status, requires_elicitation) (CT(openai_compliance_connector_calls_total{F_ws}[R]))`  • `max by (connector_name, date) (max_over_time(openai_compliance_connector_calls_total{call_phase="requested",F_ws}[L]))`  • `max by (workspace_id, plugin_id, url_host, date) (last_over_time(openai_compliance_plugin_mcp_servers_total{F_ws}[S]))` | not flag-gated; Calls = `requested`; Failed = `completed`  • `status="failed"`; unconfirmed = `requested`  • `requires_elicitation="false"`; Last seen bucket from newest day with a call (≤ 90 d); Inactive after 30 d; MCP hosts have no calls |
| Owners / Connection (optional columns) | raw `app_auth`  • `app` | `app_id` → `app_name`, `action`, `actor.user_email` | `getAppConnections` (2 DataPrime, 90 d) | not flag-gated; joined on metric `connector_name` = raw `app_name` ⚠; hidden when no row fills them |
| Table "Agent connector grants" — Connector / Approval mode / Grants | `openai_compliance_agent_connectors_total` | `connector_id`, `user_approvals_mode` | `max by (workspace_id, connector_id, user_approvals_mode, date) (last_over_time(openai_compliance_agent_connectors_total{F_ws}[S]))` | not flag-gated |

## Users

`getWorkspaceRoster` (4) + `getPendingInvites` (2) + app grants (2 DataPrime, shared); one row per seat of each workspace's newest snapshot; count chip = filtered rows; row click → Member drawer (focus `exposure`, row's workspace pinned).

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| Header "N pending invites" (+ tooltip by role) | `openai_compliance_invites_total` | `role`, `date` | `count by (workspace_id, date) (last_over_time(openai_compliance_user_state{F_ws}[S]))`  • `sum by (workspace_id, role, date) (last_over_time(openai_compliance_invites_total{F_ws}[S]))` | not flag-gated; read on the roster's newest day |
| Grid "Users" — Workspace / User / Role / Status / SCIM | `openai_compliance_user_state` (+ identity) | `workspace_id`, `user_id`, `role`, `status`, `is_scim_managed`, `date` | `getWorkspaceRoster(filters)` (4 instants) | not flag-gated; User = name ?? email ?? user_id |
| Connected apps (optional column) | raw `app_auth` | seat `principal.id|actor.user_id`, `action="link"` | `getAppConnections` (2 DataPrime) | not flag-gated; apps linked per seat; hidden when grants are unread |

# Drawers

## Member drawer (Users row, insight-drawer row)

`ChatGptComplianceMemberDrawerService`, provided once at the dashboard shell. Pins `userId` and the clicked row's `workspaceId` (replacing the page chip; the heavy-members drawer pins no workspace). Per open: `getWorkspaceRoster` (4 PromQL, user-narrowed) + 8 DataPrime (conversations, app grants, app names, member costs, app calls, sign-ins, custom agents, skills); agents / skills reuse the page cache only when the pinned workspace equals the page chip. Read-only: no grid row is clickable. Cards: Consumption, then Connectors (scrolled into view for focus `exposure`).

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| Header (name, email) | `openai_compliance_user_state`  • identity | `user_id`, `workspace_id` | `getWorkspaceRoster({…filters, userId, workspaceId})` | not flag-gated; name ?? email ?? user_id; email line only when it differs from the title |
| KPI "Credits" | raw `costs` stream | `payload.identity.user_id`, `measures.billing[].cost.value` | `getMemberCosts` DataPrime over `[from, T_c]` | not flag-gated; this member across all workspaces, Codex excluded; usage hours kept in `[from, to)`; hidden with "Credits not reported" when the read is null |
| KPI "List cost (USD)" | raw `costs` stream | `measures.billing[].estimated_cost_usd.value` | same | not flag-gated |
| Grid "Credits by model" — Model / Credits / List cost (USD) / Products / Top model | raw `costs` stream | `payload.model`, `payload.product` | same | not flag-gated; "Top model" chip on the highest-credit model; "No model usage" empty state |
| Grid "Credits by product" — Product / Credits / List cost (USD) / Share | raw `costs` stream | `payload.product` (`ChatGPT`/`Work` case-merged) | same | not flag-gated; share of the member's credits |
| KPI "Messages written" | raw `conversation_message` | own messages (`model` empty) | `getConversationRead` DataPrime with `DP_ws`, `DP_user` | not flag-gated; "Not reported" when the log is unread or ≥ 5000 rows; "No activity" when read but empty |
| KPI "Last message" | raw `conversation_message` | `max($m.timestamp)` of own messages | same | not flag-gated |
| KPI "Last sign-in" | raw `auth` | `action_data.action="login_success"` | `getLastSignIn` DataPrime over `[min(from, to − 90d), to]` | not flag-gated; can predate the range |
| Grid "Models used" — Model / Answers | raw `conversation_message` answers | `message.author.model` (−`-wm`) | `getConversationRead` | not flag-gated; answers in this member's conversations; "No answers" empty state |
| Grid "Shared assets" — Asset / Kind / Status / Created | raw `agents`, `skills` | `owner_id` / `owner_email`, `creator` | `getCustomAgents`  • `getSkills` DataPrime (`[to − 2d, to]`) | not flag-gated; agents owned by this `user_id` or email, skills created by this `user_id`; note when only one of the two reads failed |
| Grid "Apps used" — App / Calls / Last used | raw `app` | `app_name` | `getAppUse` DataPrime with `DP_ws`, `DP_user` | not flag-gated; "No app calls" / "Not reported" |
| Grid "Connected apps" — Connected app | raw `app_auth`  • `app` | `action="link"`, `app_id` → `app_name` | `getAppConnections` DataPrime (`[to − 90d, to]`) | not flag-gated; apps this member still has linked |

## Model drawer (Models used row)

`ChatGptComplianceModelDrawerService`, provided once at the dashboard shell. Pins nothing on the queries: `getCost()`, `getConversationActivity()` and `getWorkspaceRoster()` all run **unfiltered** (Workspace chip ignored) and are served from the page caches when warm (0 new requests). The selected model is matched client-side on the ledger `model`. Read-only. Error state on a cost failure; "No model activity" when the model has no tokens, credits or USD in range.

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| KPI "Input tokens" / "Output tokens" / "Total tokens" | `openai_compliance_costs_tokens` | `model`, `token_type` | `L(openai_compliance_costs_tokens, date, hour, product, model, service_tier, token_type)` | not flag-gated; all non-Codex products; organization-wide |
| KPI "Credits" / "Est. cost (USD)" | `openai_compliance_costs_credits`, `_usd` | `model` | `L(openai_compliance_costs_credits, date, hour, product, model)`  • `L(openai_compliance_costs_usd, date, hour, product, model)` | not flag-gated |
| KPI "Users" | raw `conversation_message` answers | `message.author.model`, `actor.user_id` | `getConversationRead` DataPrime, no filters | not flag-gated; members this model answered; "N/A" when the log is unread |
| Chart "Tokens over time by product" | `openai_compliance_costs_tokens` | `product` → bands ChatGPT / Work / Other | ledger tokens query | not flag-gated; daily stacked; partial newest day dropped |
| Grid "Users" — Member / Workspace / Answers / Top model | raw `conversation_message`  • roster | `actor.user_id`, `principal.id`, `message.author.model` | conversations DataPrime + `getWorkspaceRoster()` (4 PromQL, cached) | not flag-gated; email from roster else raw `user_id`; Workspace = workspaces the answers came from; "No members" empty state |
| Grid "Tokens by product" — Product / Tokens / Share of model | `openai_compliance_costs_tokens` | `product` | ledger tokens query | not flag-gated |
| Grid "Tokens by service tier" — Service tier / Tokens / Share of model | `openai_compliance_costs_tokens` | `service_tier` | ledger tokens query | not flag-gated; missing tier → "Not reported" |

## Seats going idle (idle-seats insight CTA)

`ChatGptComplianceInsightMembersDrawerService`, provided once at the dashboard shell. No query: rows are the seats the "Idle ChatGPT seats" rule already resolved (roster seats with no `conversation_message` row). Subtitle from the same rows. Row click → Member drawer with the seat's workspace pinned; this drawer stays open beneath it.

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| User | `openai_compliance_user_state`  • identity | `user_id` → email | none (rule rows) | not flag-gated; email else `user_id`; default sort |
| Role | `openai_compliance_user_state` | `role` | none | not flag-gated; sorts by access rank |
| Status | `openai_compliance_user_state` | `status` | none | not flag-gated |

## Heaviest members by messages (concentration insight CTA)

Same service. No query: rows are the members the concentration rule flagged from the raw `conversation_message` read. Title singular / plural to match the card. Row click → Member drawer with no workspace pinned (page chip applies).

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| User | raw `conversation_message`  • roster | `actor.user_id` → email | none (rule rows) | not flag-gated; email else `user_id` |
| Messages | raw `conversation_message` | own messages (`model` empty) | none | not flag-gated |
| Share of messages | derived | — | none | not flag-gated; member ÷ all members' own messages |
| × avg member | derived | — | none | not flag-gated; member ÷ average writing member (≥ 10 to appear) |

# Empty state

- Page gate: route flag `ai-center-openai-chatgpt` OFF → redirect to `../../overview`. Sidebar: the "AI chat apps → ChatGPT" row links to `openai/chatgpt` only with the flag ON **and** the `CHATGPT` mark (detection `count(sum_over_time(openai_compliance_events_total{event_type="CONVERSATION_MESSAGE"}[10d]) > 0)`); unmarked → "+" opening the docs. A team with no code agent connected and `CHATGPT` marked lands on this page (API Platform wins when it is marked too).
- In-page banner "No ChatGPT Enterprise usage data yet" when `checkHasData` returns false (`openai_compliance_users_total` or `openai_compliance_log_feed_lag_seconds` in the last 10 days, evaluated at now). Non-blocking — panels still render — and dismissible, persisted under `:announcements::chatgpt-integration-banner`. A probe error never shows it.
- The banner probe and the sidebar mark read different metrics: a tenant with a roster but no messages gets no banner yet no sidebar link (reachable by URL only).
- Per panel: a failed metrics query → that panel's error state; ledger with no rows in range → "N/A" tiles and empty charts; raw log unread → "Not reported" / hidden columns / metric fallbacks (agents, skills).

# Appendix — dead / duplicated queries

1. Fan-out per full page load (no chip, warm caches shared): 31 PromQL + 5 DataPrime — has-data 1, insights (cost 3, feeds 1, roster 4), cost (previous window 3, breakdown 1), activity (tool calls 1, messages 1, calendar 1), shared assets 13, invites 2; DataPrime conversations, agents, skills, app grants + names. Member drawer adds 4 PromQL + up to 8 DataPrime per open.
2. `connectorCalls` runs twice: inside the 13-query `getSharedAssets` map (its `toolCalls` field is read by no panel on this page) and again in `getToolCalls` for Most used tools.
3. `count by (workspace_id, date) (last_over_time(openai_compliance_user_state{F_ws}[S]))` runs twice (roster stage 1, pending invites; neither cached across the other). `getWorkspaceIds` is a third near-copy.
4. Shared GPTs & projects reads the full roster (4 PromQL) only to map skill creator ids to emails.
5. Cost cache key includes the evaluation minute while the window is still within the ledger lag, so a panel deferred into a later minute re-runs the 3 ledger queries (and the 3 previous-window ones).
6. Workspace chip is silently dropped on Models used's member columns and the whole Model drawer (conversation log read unfiltered) although the log carries `principal.id`; ledger-backed figures are organization-wide by design and say so.
7. Activity tokens are ChatGPT-product only while Cost and Models used count every non-Codex product (Work included): the two token totals disagree by design.
8. ⚠ `openai_compliance_user_identity` is marked SPEC_PENDING in code (no collector known to emit it); emails then come only from `openai_analytics_codex_credits`, so non-Codex members display as raw `user_id`.
9. ⚠ No `service_name!="cx-codex-compliance-puller"` matcher on the ledger, events, roster or inventory reads — only identity and feed freshness exclude the Codex puller.

# Unverified — live checks to run

These claims come from code only; each needs one query against a team with the OpenAI Compliance integration (for example knowledge-base on eu2).

| Claim | Query |
| --- | --- |
| Ledger `product` vocabulary; Codex spelled exactly `codex` | `count by (product) (last_over_time(openai_compliance_costs_credits[14d]))` |
| The Codex puller also emits these names (would double counts) | `count by (service_name) (last_over_time(openai_compliance_costs_credits[7d]))`; same on `openai_compliance_user_state` and `openai_compliance_events_total` |
| `openai_compliance_user_identity` exists and which email label it uses | `count(last_over_time(openai_compliance_user_identity{email!=""}[30d]))` vs `count(last_over_time(openai_compliance_user_identity{user_email!=""}[30d]))` |
| `openai_analytics_codex_credits` carries `user_id` (email fallback) | `count(last_over_time(openai_analytics_codex_credits{user_id!=""}[30d]))` |
| Raw-log `actor.user_id` / `principal.id` match metric `user_id` / `workspace_id` (idle-seat rule, Connected apps column) | `source default/ai.raw.openai.compliance_logs | filter $d.stream == 'openai.compliance.conversation_message' | groupby $d.data.principal.id, $d.data.actor.user_id aggregate count() | limit 20` vs `count by (workspace_id, user_id) (last_over_time(openai_compliance_user_state[2d]))` |
| Metric `connector_name` equals raw `app_name` (Owners / Connection join) | `count by (connector_name) (last_over_time(openai_compliance_connector_calls_total[30d]))` vs `source default/ai.raw.openai.compliance_logs | filter $d.stream == 'openai.compliance.app' | groupby $d.data.app_name aggregate count()` |
| `events_total` carries `date` and `client_id` | `count by (date, client_id) (last_over_time(openai_compliance_events_total{event_type="CONVERSATION_MESSAGE"}[3d]))` |
| Raw log exported on prod (code comment says staging only) | `source default/ai.raw.openai.compliance_logs | groupby $d.stream aggregate count()` |
| Spend-trend rule can fire on the default 14-day range | `count by (date) (last_over_time(openai_compliance_costs_credits{product!="codex"}[2d]))` — check whether yesterday has 24 `hour` series |