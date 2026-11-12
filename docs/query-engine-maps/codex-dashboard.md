# Codex Dashboard — Real Query Engine Map

Full map of **widget → metric / log → labels → query** for the **new Codex dashboard** (`CodexDashboardComponent`, selector `cxai-codex-dashboard`). Route `ai-center/code-agents/openai/codex` (legacy `code-agents/codex` redirects there), under the `code-agents` flag guard + `aiCodeAgentsReadPermissionGuard`; the route itself is **not** flag-gated. `CodexDashboardRouteComponent` snapshots **`ai-center-openai-codex`** once: ON renders this dashboard (lazy `@defer`), OFF renders the legacy CLI-telemetry dashboard (out of scope). Engines (all under `libs/ai-center/code-agents/src/lib/code-agents-codex/`): `data/analytics/prom-codex-analytics-queries.service.ts`, `data/compliance/codex-compliance-queries.service.ts`, `utils/codex-no-user-usage.ts`, `codex-cost/codex-cost.service.ts`, `codex-repos/codex-repos.service.ts` + `codex-repo-hook.ts`, `codex-usage/codex-client-models.ts`, `data/codex.service.ts` (only `getUsersTable`, itself Analytics-only); transport `../prometheus-query.service.ts` + `AiCenterDataprimeService`. `codex-dataprime-queries.ts` is reachable only on a flag-OFF fallback. Traced code-level on 2026-11-08 on `origin/master` @ `89abdb48f52` (shape set by #24772, #27136, #27250, #27325), every template and every call into the query services enumerated. **Not run against live data**; ⚠ marks claims listed in the last section. Sibling pages: [Codex Compliance Dashboard — Real Query Engine Map](https://app.notion.com/p/Codex-Compliance-Dashboard-Real-Query-Engine-Map-412c048defad8135918bfeb7f2850ed0?pvs=21), [ChatGPT Dashboard — Real Query Engine Map](https://app.notion.com/p/ChatGPT-Dashboard-Real-Query-Engine-Map-412c048defad817d935eecedde68876f?pvs=21), [ChatGPT Compliance Dashboard — Real Query Engine Map](https://app.notion.com/p/ChatGPT-Compliance-Dashboard-Real-Query-Engine-Map-412c048defad8146a6b4ddf29e214cf2?pvs=21), [OpenAI API Platform Dashboard — Real Query Engine Map](https://app.notion.com/p/OpenAI-API-Platform-Dashboard-Real-Query-Engine-Map-cc9525d6a3214210a45ad8fd10dab354?pvs=21), [OpenAI API Platform Compliance Dashboard — Real Query Engine Map](https://app.notion.com/p/OpenAI-API-Platform-Compliance-Dashboard-Real-Query-Engine-Map-c801c01a10174855b4249100e5500970?pvs=21), [Cursor Usage Dashboard — Real Query Engine Map](https://app.notion.com/p/Cursor-Usage-Dashboard-Real-Query-Engine-Map-3cbc048defad81019087e90326175a9b?pvs=21).

<aside>
⚙️

Five planes feed this page and **no Codex CLI/OTel log is read** while the flag is ON: (1) **Codex Analytics** `openai_analytics_codex_*` — one series per usage row and UTC `date` label, read as the newest sample per series at "now" (`L()` below), never `rate()` / `increase()` / `query_range`; (2) **Analytics raw log, no-user rows** `default/ai.raw.openai.codex.analytics_logs` (GitHub-app code review, which the metrics drop), DataPrime, added on top of (1); (3) **Compliance collector** `openai_compliance_codex_session_tokens_total`, `openai_compliance_codex_session_turns_total`, `openai_compliance_codex_tokens_total`, `openai_compliance_codex_tool_calls_total`, `openai_compliance_user_identity` — `date`-labelled counters; (4) **Raw compliance log** `default/ai.raw.openai.compliance_logs` — tool-call threads / last use only; (5) **Repo-tracker hook** `codex_session_repo_info`, `codex_session_commits|prs_opened|lines_added|lines_removed`. Planes 1–4 are whole-workspace; 5 only sees machines with the hook.

</aside>

# Conventions

- **`D`** — the `date` matcher over the read's UTC dates: `date="YYYY-MM-DD"` for one, `date=~"d1|d2|…"` for several. **`[H]`** — `[Nh]`, N = ceil((eval − first date 00:00 UTC) / 1 h) + 1.
- **Dates (`resolveDays`)** — closed UTC days only (a day counts closed 26 h after it starts; today is never read). Quick mode: the latest N closed days, N = max(1, round(span / 1 day)). Custom: the dates the picker covers, up to the newest closed day. No range in the URL: 30 dates. Capped at 400 dates. **`rangeDays`** = same but through today (hook + repo rollup + hook probe only).
- **`L(m{F})`** = `max without (organization) (last_over_time(m{F,D}[H]))` — newest sample of every (row, date) series. Daily charts are **instant** `sum by (date) (L(…))` reads, one point per date at UTC midnight, from the first date any read reports through the last date (no point before data starts, no extra leading point).
- **`F_analytics`** = `labelFilter({model, user, workspace})`: `{model=…,user_email=…,workspace_id=…}` in that order, set ones only; one value `=`, several `=~"a|b"` (regex-escaped). Under a Model chip, `MF(m)` swaps `openai_analytics_codex_{credits,cost_usd,uncached_text_input_tokens,cached_text_input_tokens,text_output_tokens}` to their `openai_analytics_codex_model_*` sibling; every other family keeps the `model` matcher unchanged. `F_lines` = `F_analytics` without `model`. `F_ws` = workspace only. `NU` = `user_email!=""` (people reads). `withLabel` appends a pinned matcher, e.g. `{F_lines,change="added"}`.
- **Evaluation** — every read is an instant at `time = floor(eval/1000)`, eval = now floored to the minute (not the range end), except Most Expensive Sessions' "Last active" (a range read). `PrometheusQueryService` caches per (query, second) with `shareReplay`, so identical reads in one minute share one request. The no-user read is cached per (dates, Workspace chip) per eval minute.
- **`{NoUser}`** — no-user rows (plane 2): two PromQL checks `count by (date) (L(openai_analytics_codex_credits))` and `count by (date, workspace_id) (L(openai_analytics_codex_credits{user_id=""}))`, then the DataPrime query below over only the dates that hold user rows, minus each (date, workspace) the collector already wrote; newest copy per (workspace, day) wins; limit 5,000 rows (re-read in halves when full), 30 s timeout. **Empty under a User chip**, without DataPrime, or on any failure (silent). Added to: usage totals, credits/cost and tokens daily series, model and client breakdowns, cost posture, credits-by-client chart, workspace and model options.

```
source default/ai.raw.openai.codex.analytics_logs
| filter $d.stream == 'openai.analytics.codex_usage' && $d.data.user_id == null
| filter $d.data.start_time >= \<first date, unix s> && $d.data.start_time \< \<last date + 1 day, unix s>
| filter $d.resource.attributes.workspace_id == '\<id>'        -- Workspace chip only; several ids → (… || …)
| orderby $m.timestamp desc
| choose $m.timestamp as ts, $d.resource.attributes.workspace_id as workspace_id, $d.data.start_time as start_time, $d.data.credits as credits, $d.data.estimated_cost_usd as estimated_cost_usd, $d.data.uncached_text_input_tokens as uncached_text_input_tokens, $d.data.cached_text_input_tokens as cached_text_input_tokens, $d.data.text_output_tokens as text_output_tokens, $d.data.clients as clients, $d.data.models as models
```

- **Compliance window** `F_c` = `{workspace_id=…,D}` + `[H]` over the same closed dates (`complianceWindow`). **`TC(r)`** (token counters) = `(sum_over_time(r) * (resets(r) > bool 0) + max_over_time(r) * (resets(r) == bool 0))`. **`EC(r)`** (event counters: turns, tool calls) = `(sum_over_time(r) * P + (max_over_time(r) + resets(r)) * (1 - P))`, `P = (max_over_time(r) <= bool clamp_min(resets(r), 1))`. Both read the delta and the cumulative form.
- **`{ID_c}`** = `max by (user_id, email, name) (last_over_time(openai_compliance_user_identity[H]))`. **`{ID_a}`** = `max by (user_id, user_email) (last_over_time(openai_analytics_codex_credits{user_id!="",user_email!="",workspace_id=…}[30d]))` (`[H]` when the window is longer than 30 days).
- **`{Q_sessions}`** = `getCodexLogSessions()`: 3 instants — `sum by (session_id, user_id, model, token_type, service_tier, reasoning_effort, client_id) (TC(openai_compliance_codex_session_tokens_total{F_c}[H]))`, `sum by (session_id) (EC(openai_compliance_codex_session_turns_total{F_c}[H]))`, `{ID_c}`. Session tokens = input + output (cached ⊂ input, reasoning ⊂ output); email = `{ID_c}` `email` (smallest when several); `turns[]`, `activeMs`, `users[]` stay empty. Workspace chip narrows; User / Model are re-applied client-side. A failed read errors the caller.
- **Identity** — email is the cross-plane key. Analytics `user_email` is compared exactly (two casings = two rows); compliance → email goes through `{ID_c}` / `{ID_a}`, case-insensitive in tool-call and active-time lookups; Activity insights lowercase both sides, Most Expensive Sessions compares exactly. Session = compliance `session_id` = hook `session_id` (⚠). Workspace = Analytics `workspace_id` = compliance `workspace_id` = raw-log `resource.attributes.workspace_id` (no workspace names exist on any plane).
- **Flag model** — inside the dashboard, 11 call sites re-read `ai-center-openai-codex` live (Tool Call Breakdown, Users Active Time, Most Expensive Sessions, `CodexCostService`, Cost insights, Activity insights, Tokens compliance cards, user drawer + dialog, client drawer, `CodexReposService`); OFF removes those widgets (not empty-stated) and skips their reads. Reachable OFF only on a mid-session flip, since the route mounts this dashboard only when ON. The user drawer's Run Time tile and Tools Used card also open under `ai-center-openai-administration-usage` (OR). `ai-center-managed-repos-settings` (snapshot) only adds the "Configure repo settings" header link — no query gate.
- **Fail-soft** — overview, posture and review-funnel reads use `queryInstantOrEmpty` (fail to 0 + a `failed` flag → that widget's error state); `#byLabel`, `#daily`, `#instant`, every compliance read and every hook read throw → the widget's error state. Insights panels `forkJoin` many reads, so one failed read blanks the whole panel. No-user and raw-compliance-log reads fail silent (nothing added / N/A).

# Data contract for simulators

What a demo-data simulator must emit (into ob / ob-dev) for every widget on this page to render, derived **from the query code only**. Numbers below are code thresholds or code constants, labelled as such — none is an observed value.

## Metric families to emit

| Family | Shape & cadence | Required labels | Values that render | Feeds |
| --- | --- | --- | --- | --- |
| `openai_analytics_codex_{credits,cost_usd,uncached_text_input_tokens,cached_text_input_tokens,text_output_tokens,threads,turns}` | Gauge, one value per (usage row, `date`); a usage row = one person in one workspace on one UTC day. Re-emit the same `date` with the restated value: the reader takes the newest sample of each (labels, `date`) series between that date's 00:00 UTC and now (`L()`), so sample frequency does not matter, only that the last sample is the day's value. Only **closed** dates are read (a date is read from 26 h after it starts) | `date` (`YYYY-MM-DD`), `user_email`, `user_id`, `workspace_id`; optional `organization` (dropped by the reader). **No `model`, no `client_id`.** `user_id`  • `user_email` on `credits` is the Analytics identity (`{ID_a}`) that maps compliance `user_id` → email | credits and USD as floats; tokens, threads, turns as counts. Users List Cost falls back to credits × 0.04 when `cost_usd` is absent (code constant) | totals and tiles, daily charts, Users table, seat roster, workspace options, cost posture, unused-seats / long-threads / climbing / concentration / cache insights |
| `openai_analytics_codex_model_{credits,cost_usd,uncached_text_input_tokens,cached_text_input_tokens,text_output_tokens}` | same as above | same + `model`, `speed` (`fast`, anything else reads "standard") | as above | Model usage grid, Model options, Users Models, Tokens by Serving Speed, fast-tier and full-size insights, every Analytics read under a Model chip, per-model $/token rates for session and repo cost |
| `openai_analytics_codex_client_{credits,cost_usd,uncached_text_input_tokens,cached_text_input_tokens,text_output_tokens}` | same as above | same + `client_id` (series without it are dropped) | as above | Client usage grid, Credits Over Time per-client split, client drawer |
| `openai_analytics_codex_lines` | same as above | `date`, `user_email`, `workspace_id`, `change="added"|"removed"`; no `model` | line counts | Lines +/- (Users), Lines of code, Coding Activity (trailing 364 days), Cost by Workspace lines, user drawer Lines, churn insight |
| `openai_analytics_codex_pr_reviews`, `_review_comments`, `_review_comments_by_priority{priority="p0|p1|p2"}`, `_review_response_pr_reviews`, `_review_response_comments`, `_review_replies`, `_review_reactions`, `_review_reactions_by_type{reaction="upvote|downvote|other"}`, `_review_comments_engaged`, `_review_comments_replied`, `_review_comments_reacted`, `_review_comments_reacted_by_type{reaction=…}` | same daily gauge shape | `date`, `workspace_id` only (workspace grain; no user, no model) | counts | Codex Code Review tiles + pie, the four review insights |
| Dataset `default/ai.raw.openai.codex.analytics_logs`, stream `openai.analytics.codex_usage` | One log per workspace × day with `$d.data.user_id == null`; re-written copies allowed (newest `$m.timestamp` wins). Read only on dates that also hold user rows in the metrics | `$d.resource.attributes.workspace_id`, `$d.data.start_time` (unix s of the UTC day) | `$d.data.{credits,estimated_cost_usd,uncached_text_input_tokens,cached_text_input_tokens,text_output_tokens}`, `$d.data.clients[]` (`client_id`  • amounts), `$d.data.models[]` (`model`, `speed`  • amounts) | added into totals, daily charts, model / client grids, posture, client drawer "Codex code review (no user)" row |
| `openai_compliance_codex_session_tokens_total` | Counter. Emit the cumulative form: one series per (labels, `date`), value = running day total (`TC()` also reads the older delta form). Emit a sample only when the value moves | `session_id`, `user_id`, `model`, `token_type` ∈ `input`, `cached_input`, `output`, `reasoning_output`, `service_tier`, `reasoning_effort`, `client_id`, `workspace_id`, `date`. `session_id` joins to the hook; `user_id` joins to `openai_compliance_user_identity` | token counts; session total = input + output | Most Expensive Sessions, session-cost insight, Longest threads drawer, Git Repos token join, client drawer Threads, "Last active" |
| `openai_compliance_codex_session_turns_total` | Counter, cumulative per (`session_id`, `date`); read with `EC()` | `session_id`, `workspace_id`, `date` | turns per session | Longest threads drawer Turns |
| `openai_compliance_codex_tokens_total` | Counter, cumulative per (labels, `date`). Active time counts every 10-minute window holding a sample, so emit a sample **only** in windows where the person generated tokens | `user_id`, `model`, `token_type` (as above), `reasoning_effort`, `client_id`, `workspace_id`, `date` | token counts | Tokens by reasoning effort, reasoning-share insight, Users Active Time, user drawer Run Time, client drawer Models |
| `openai_compliance_codex_tool_calls_total` | Counter, cumulative per (labels, `date`); read with `EC()`, which reads a day total of 1 as per-event samples and sums them — emit only on change (⚠ derived from the rule) | `tool_name`, `tool_type` (may be empty), `status` (`completed` / `failed`; other values count in Calls only), `user_id`, `workspace_id`, `date` | call counts | Tool Call Breakdown, Tool drawer, user drawer Tools Used |
| `openai_compliance_user_identity` | Gauge, any value; re-emit within the range | `user_id`, `email`, `name` | — (labels only) | every compliance → email join |
| Dataset `default/ai.raw.openai.compliance_logs`, stream `openai.compliance.codex` | One log per tool-call event | `$d.data.event_details.{tool_name,tool_type,session_id}`, `$d.data.actor.user_id`, `$d.data.workspace_id` | distinct sessions, last `$m.timestamp` | Threads / Last Used columns of the Tool drawer and Tools Used (N/A without it) |
| `codex_session_repo_info` | Gauge, any value, per session × repo while the session runs; no `date` label | `session_id`, `repository_name` (remote URL or `owner/repo`, parsed by `parseRepoRemote`) | — (labels only) | Git Repos (all three widgets), repo drawer, user drawer Git Repositories / Repo Type Split, Most Expensive Sessions repos, the hook probe |
| `codex_session_commits`, `codex_session_prs_opened`, `codex_session_lines_added`, `codex_session_lines_removed` | Gauge, cumulative per (`session_id`, `repository_name`), read with `max_over_time`; emit 0 too — absence means "hook too old to count" | `session_id`, `repository_name` | counts | repo drawer Lines / Commits / PRs, user drawer Commits / Pull Requests |
| `openai_compliance_events_total{event_type="CODEX_LOG"|"CODEX_SECURITY_LOG"}`, `codex_turn_e2e_duration_ms_count` | detection only, fixed `[10d]` | `event_type` | > 0 | shell detection (any one of these or `openai_analytics_codex_credits` marks Codex) |

## Simulator Q&A

| Question | Answer |
| --- | --- |
| It only outputs one sample a day? | Analytics families: one **value** per (labels, `date`), restated as often as you like; the reader keeps the newest sample of each date series and only reads closed dates (26 h after the date starts). Compliance counters: per-event growth within a `date`, emitted as a cumulative running total per (labels, `date`). Hook: live samples while a session runs, no `date` label. Real behaviour (verified outside code): Analytics is restated on each daily pull, threads / turns arrive 0 on the first pull and fill 1–3 days later, credits / tokens are 95–98 % complete on day+1; compliance counters were OTLP delta (0/1 per event) up to 2026-10-28, mixed on 2026-10-29, cumulative from 2026-10-30. |
| What sort of daily cost value? | `openai_analytics_codex_cost_usd` (+ `_model_` / `_client_` siblings): list-rate USD per usage row per day, a float. The code enforces no range. A workspace counts as "reporting cost" when any `cost_usd` series exists for it; without one, List Cost reads N/A and Most Expensive Sessions is empty. Code constant: Users List Cost fallback = credits × 0.04. The OpenAI costs ledger (`openai_compliance_costs_*`, hourly rows with `date` / `hour` labels, organization-wide, no user and no workspace) is **not** read by this dashboard. |
| How many users / models / projects? Limits enforced on some projects? | Codex has no projects and no spend limits on this page. Entities are workspaces (`workspace_id`, shown as ids), people (`user_email` / `user_id`), models, clients (`client_id`), sessions and repos. Volume: see "Minimum entities". |
| Minimum entities to light every widget (derived from code thresholds and render conditions, not observed data) | ≥ 1 `workspace_id` with a `cost_usd` series (List Cost, Most Expensive Sessions); ≥ 10 people with credits > 0 (concentration rule); ≥ 1 person on the 30-date roster under 500 credits in the last 21 dates (unused seats); ≥ 1 model listed as expensive in `OPENAI_MODEL_TIERS` (e.g. `gpt-5-codex`) carrying > 50 % of tokens (full-size rule); `speed="fast"` rows carrying > 40 % of credits (fast-tier rule); ≥ 1 `client_id`; ≥ 10 compliance sessions whose models have Analytics rates (session-cost rule); ≥ 50 Analytics threads; ≥ 14 closed dates of history, read with the 14-day preset (climbing and engagement rules); hook sessions whose `session_id`s also exist in `openai_compliance_codex_session_tokens_total`, with repo prefixes configured in settings (managed / unmanaged split, unmanaged rule); tool calls carrying `user_id`; raw compliance log events for Threads / Last Used. |
| What insights would you like to trigger? / Insights and how to trigger each | **Activity:** "Unused Codex seats" — total credits ≥ 1,000 and ≥ 1 roster seat < 500 credits over the last 21 dates (no User / Model chip). "Threads are running long" — threads ≥ 50 and turns ÷ threads ≥ 15. "Turns wasted after a failure" — cannot fire (per-turn status is never read). **Cost:** "Fast tier is driving credits" — model credits ≥ 1,000 with `speed="fast"` > 40 %. "Prompt cache is barely used" — cached + uncached input ≥ 100k and cached ÷ input < 70 %. "Reasoning tokens are high" — compliance input + output ≥ 100k and `reasoning_output` ÷ `output` > 20 %. "Full-size models carry most traffic" — model tokens ≥ 100k with > 50 % on expensive-tier models. "Credit use is climbing fast" — ≥ 14 daily points, each week ≥ 500 credits, last 7 days ≥ 1.3 × the 7 before. "A few users drive most credits" — ≥ 10 active users, ≥ 1,000 credits, top 3 combined ≥ 10 × the per-user average. "One session cost far more" — ≥ 10 priced sessions, top session > 10 × the mean (needs `cost_usd` and model rates). **Impact:** "Codex removes as much as it adds" — added + removed ≥ 500 and removed ≥ 50 %. "PR reviews come back empty" — ≥ 10 reviews and < 1 comment per review. "Codex review comments go unanswered" — response comments ≥ 50 and engaged < 2/3. "Review engagement is dropping" — ≥ 14 daily points, each half ≥ 50 comments, recent engaged rate < 0.75 × earlier. "Work in unmanaged repositories" — prefixes configured and unmanaged repo tokens > 10 % of repo tokens. |
| The telemetry has users in it but the dashboard does not show per-user data — expected? / Per-user data | Per-user, by `user_email`: Users table (all columns), user drawer, unused-seats / concentration drawers, client drawer rows (`client_*` families). Per-user, by compliance `user_id` → email (`{ID_a}` / `{ID_c}`): Active Time, Run Time, Tool Call Breakdown Users, Tool drawer, Tools Used, Most Expensive Sessions / Longest threads owners. **No user at all:** the review families (workspace grain), the no-user raw rows, the hook (`codex_session_repo_info` carries none; users come only through the compliance session join). Workspace-level tiles (totals, charts, Cost by Workspace) never break out users. Per-user rows need `user_email` on the Analytics series; compliance per-user cells need an identity series naming the `user_id`. |
| Is there official doc from OpenAI? / Official docs | Compliance API / Compliance Platform for ChatGPT Enterprise & Edu — [https://help.openai.com/en/articles/9261474](https://help.openai.com/en/articles/9261474) (full API reference needs a workspace login). Codex enterprise governance (Analytics dashboard, Analytics API `api.chatgpt.com/v1/analytics/codex`, daily UTC buckets, key scope `codex.enterprise.analytics.read`) — [https://developers.openai.com/codex/enterprise/governance](https://developers.openai.com/codex/enterprise/governance) . Codex Security — [https://developers.openai.com/codex/security](https://developers.openai.com/codex/security) . Admin API usage / costs (`GET /v1/organization/costs`, admin key) — [https://platform.openai.com/docs/api-reference/usage](https://platform.openai.com/docs/api-reference/usage) . Coralogix: [https://coralogix.com/docs/integrations/ai-observability/openai/](https://coralogix.com/docs/integrations/ai-observability/openai/) , …/openai/codex/ , …/openai/chatgpt/ . |
| Any live telemetry somewhere? / Live telemetry | EU2 (euprod2) teams `knowledge-base` and `cxai-dev` have real Codex Analytics, compliance metrics and costs. The raw dataset `default/ai.raw.openai.compliance_logs` exists in EU2 from ~2026-10-29 and has longer history on staging team `test-ai-center`. Producer: the Coralogix cloud collector (`openai_integrations`); the old laptop puller (`service_name="cx-codex-compliance-puller"`) stopped 2026-09-16. |

# Engine operation → query shape

| Op | Query shape | Evaluation |
| --- | --- | --- |
| Analytics `getUsageOverview` | 7 × `sum by (date) (L(MF(openai_analytics_codex_<m>){F_analytics}))` — `<m>` ∈ `credits`, `cost_usd`, `uncached_text_input_tokens`, `cached_text_input_tokens`, `text_output_tokens`, `threads`, `turns` — + `{NoUser}` | 7 instants (OrEmpty), dates summed; cache hit = cached ÷ (cached + uncached). 5 consumers (Activity insights, Total Threads, Cost insights, Credits Consumed, Tokens tiles) |
| Analytics `getModelBreakdown` | 5 × `sum by (model, speed) (L(openai_analytics_codex_model_<m>{F_analytics}))` — `<m>` ∈ `credits`, `cost_usd`, `uncached_text_input_tokens`, `cached_text_input_tokens`, `text_output_tokens` — + `{NoUser}` model parts | 5 instants (throw); empty model → "unknown"; speed "fast" else "standard". 6 consumers (Model usage, Tokens by Serving Speed, Cost insights, session cost, repo rates, user drawer Models) |
| Analytics `getCostPosture` | `sum by (workspace_id) (L(MF(credits){F_analytics}))`  • `sum by (workspace_id) (L(MF(cost_usd){F_analytics}))`  • `count by (workspace_id) (count by (workspace_id, user_email) (L(MF(credits){F_analytics,NU})))`  • `count by (workspace_id) (count by (workspace_id, date) (L(MF(credits){F_analytics})))`  • `sum by (workspace_id) (L(openai_analytics_codex_lines{F_lines,change="added"}))` (+ `removed`) • `count by (workspace_id) (L(openai_analytics_codex_lines{F_lines,change="added"}))`  • `count by (workspace_id) (L(openai_analytics_codex_credits{F_lines}))` — + `{NoUser}` | 8 instants (OrEmpty); rows = workspaces with credits, cost_usd or no-user amounts; cost reported when a `cost_usd` series exists (or the row is no-user only) |
| Analytics per-user reads (`#byLabel`) | `sum by (user_email) (L(MF(<m>){F_analytics}))` for tokens ×3, `cost_usd`, `threads`, `turns`; credits `sum by (user_email) (L(MF(credits){F_analytics,NU}))`; models `sum by (user_email, model) (L(openai_analytics_codex_model_<tok>{F_analytics}))` ×3; lines `sum by (user_email) (L(openai_analytics_codex_lines{F_lines,change="added|removed"}))` | instants (throw); rows with an empty label skipped; joined on email |
| Analytics daily series (`#dailySeries`) | `sum by (date) (L(MF(<m>){F_analytics}))` per field — credits + cost_usd, tokens ×3 (both + `{NoUser}`), threads + turns, review response comments + engaged (`F_ws`) | instants (throw), zipped by `date` |
| Analytics client reads | 5 × `sum by (client_id) (L(openai_analytics_codex_client_<m>{F_analytics}))` (+ `{NoUser}` clients, none under a Model chip); chart `sum by (client_id, date) (L(openai_analytics_codex_client_credits{F_analytics}))`; drawer 4 × `sum by (user_email) (L(openai_analytics_codex_client_<m>{F_analytics,client_id="<id>"}))` | instants (throw); `client_id`-less rows dropped |
| Analytics review funnels | `sum(L(openai_analytics_codex_pr_reviews{F_ws}))`, `sum(L(openai_analytics_codex_review_comments{F_ws}))`, `sum(L(openai_analytics_codex_review_comments_by_priority{F_ws,priority="p0|p1|p2"}))` (3); response funnel: 13 × `sum(L(<m>{F_ws}))` over `review_response_pr_reviews`, `review_response_comments`, `review_replies`, `review_reactions`, `review_reactions_by_type{reaction=…}` ×3, `review_comments_engaged`, `review_comments_replied`, `review_comments_reacted`, `review_comments_reacted_by_type{reaction=…}` ×3 | workspace grain only — User and Model dropped. Funnel 5 = OrEmpty; response 13 = throw |
| Analytics rosters / options | workspaces `count by (workspace_id) (L(openai_analytics_codex_credits))` ∪ `{NoUser}` workspaces; models `count by (model) (max without (organization) (last_over_time({__name__=~"openai_analytics_codex_model_(credits|cost_usd|uncached_text_input_tokens|cached_text_input_tokens|text_output_tokens)",D}[H])))` ∪ `{NoUser}` models; seats `count by (user_email) (L(openai_analytics_codex_credits{NU}))` over the **30 dates** ending at the range's last date; last-N per-user credits `sum by (user_email) (L(MF(credits){F_analytics,NU}))` over the last N dates (N = 21 for unused seats, half-range for the Users delta) | options / seats unfiltered; a seat with zero credits in 30 days cannot appear |
| Analytics `getLinesByDay` | chunks of 30 dates, 3 concurrent: `sum by (date) (L(openai_analytics_codex_lines{F_lines}))` | trailing 364 days through the newest closed day; failed chunk → `failed`, rest kept |
| Compliance `getCodexLogSessions` | `{Q_sessions}` | 3 instants (throw) |
| Compliance `getCodexSessionsLastActive` | `max by (session_id) (max_over_time(openai_compliance_codex_session_tokens_total{session_id=~"<ids>",D}[<step>]))`, `query_range` from first date 00:00 UTC to eval, step = max(10, ceil(minutes / 10,000)) m | only the ≤ 20 shown sessions (or one, on an insight click); failure → no "Last active" |
| Compliance `getCodexServiceTiers` | delegates to `getModelBreakdown({model, workspace})`; tokens = 3 buckets summed per `speed` | 5 instants |
| Compliance `getCodexReasoningEffort` | `sum by (reasoning_effort) (TC(openai_compliance_codex_tokens_total{token_type=~"input|output",model=…,workspace_id=…,D}[H]))` | 1 instant (throw); series without `reasoning_effort` skipped |
| Compliance `getCodexTokenTypeTotals` | `sum by (token_type) (TC(openai_compliance_codex_tokens_total{model=…,workspace_id=…,D}[H]))` | 1 instant (throw); total = input + output |
| Compliance active time | `sum by (user_id) (count_over_time((sum by (user_id) (sum_over_time(openai_compliance_codex_tokens_total{workspace_id=…,model=…,user_id=~…,D}[10m])) > 0)[<N>h:10m]))` × 600 s + `{ID_a}`  • `{ID_c}` | 3 instants (throw); per user (table) or per one user's `user_id`s (drawer) |
| Compliance tool calls | `sum by (tool_name, tool_type, status) (EC(openai_compliance_codex_tool_calls_total{user_id=~…,workspace_id=…,D}[H]))`  • people `count by (tool_name, tool_type, user_id) (sum_over_time(openai_compliance_codex_tool_calls_total{user_id!="",workspace_id=…,D}[H]) > 0)`  • `{ID_c}`; under a User chip `user_id=~"<ids from {ID_a} ∪ {ID_c}>"` | calls throw, names OrEmpty. Refused (no read) under a Model chip, or a User chip on a range starting before 2026-10-13 |
| Compliance tool → users (drawer) | `sum by (user_id, status) (EC(openai_compliance_codex_tool_calls_total{tool_name="<t>",tool_type="<ty>"|tool_type="",user_id!=""|user_id=~…,workspace_id=…,D}[H]))`  • `{ID_c}`  • raw-log tool sessions | metric rows first, threads re-emitted when the log answers |
| Raw compliance log tool sessions | `source default/ai.raw.openai.compliance_logs | filter $d.stream == 'openai.compliance.codex' && $d.data.event_details.tool_name != null [&& tool_name / tool_type equality] [&& $d.data.actor.user_id OR-list] [&& $d.data.workspace_id OR-list] | groupby $d.data.actor.user_id as user_id` (or `tool_name, tool_type`) `aggregate distinct_count($d.data.event_details.session_id) as threads, max($m.timestamp) as last_used` | DataPrime, closed-date window, limit 5,000, 30 s timeout; failure → N/A |
| Client drawer compliance | models `sum by (user_id, model) (TC(openai_compliance_codex_tokens_total{client_id="<id>",token_type=~"input|output",workspace_id=…,model=…,D}[H]))`  • threads `count by (user_id) (sum by (user_id, session_id) (sum_over_time(openai_compliance_codex_session_tokens_total{client_id="<id>",workspace_id=…,model=…,D}[H])) > 0)`  • `{ID_a}` | 3 instants (OrEmpty); flag-gated |
| Repos (`CodexReposService`) | over `rangeDays`: `max by (session_id, repository_name) (last_over_time(codex_session_repo_info[H]))`  • 4 × `max by (session_id, repository_name) (max_over_time(codex_session_<c>[H]))` (`commits`, `prs_opened`, `lines_added`, `lines_removed`) + `sum by (session_id, user_id, model, token_type, service_tier, reasoning_effort, client_id) (TC(openai_compliance_codex_session_tokens_total{workspace_id=…,D}[H]))`  • `{ID_c}`; rates `getModelBreakdown({})` | 7 instants (throw) + 5 rate instants (failure → $0 rates); sessions = hook ∩ rollup, tokens = input + output split evenly across a session's repos; no flag check on the ON path |
| Repo hook probe (`hasCodexRepoHook`) | `count(max_over_time(codex_session_repo_info[H]))` over `rangeDays` | 1 instant; failure → treated as present |
| Session cost (`CodexCostService`) | `{Q_sessions}`  • `getModelBreakdown` (Model chip removed) + `getCostPosture`; Most Expensive Sessions adds `max by (session_id, repository_name) (last_over_time(codex_session_repo_info[H]))` (closed-date window) + last-active range | 16 instants (+ 2 for MES); $/token per model = model cost_usd ÷ model tokens (blended fallback); priced only when some workspace reports cost; posture `cost_usd` failure errors |
| `creditsToUsd` | `round(credits × 0.04, 2)` ($40 per 1,000 credits) | only the Users-table List Cost fallback |
| Detection (parent Code Agents shell) | `count(max_over_time(codex_turn_e2e_duration_ms_count[10d]) > 0)`  • `count(max_over_time(openai_analytics_codex_credits[10d]))`  • `count(sum_over_time(openai_compliance_events_total{event_type=~"CODEX_LOG|CODEX_SECURITY_LOG"}[10d]) > 0)` | once per shell, fixed `[10d]`; any hit marks Codex |

# Filters → matchers

| Page filter | Matcher emitted | Condition / translation |
| --- | --- | --- |
| Time range | Analytics + compliance: `D`  • `[H]` over closed days; hook / repo rollup / hook probe: `rangeDays` (today included); DataPrime: first date 00:00 UTC → eval (no-user: by `start_time`) | default last 7 days; presets 24 h / 2 / 3 / 5 / 7 / 14 days; minimum 1 h (Codex overrides the 1-day floor). Fixed windows ignore the picker's length: seats 30 dates, unused seats 21 dates, identity ≥ 30 d, Coding Activity trailing 364 days |
| Model | Analytics `model=…`  • `MF()` swap to `_model_` siblings; compliance `model=…` on `openai_compliance_codex_tokens_total` and client-drawer reads; sessions and repos filtered client-side | options = `getModelNames` (no "(unknown)" option). Dropped on lines, Lines of code, review funnels, Credits Over Time split, session-cost rates, user drawer threads / models. Coding Activity and Tool Call Breakdown show an "unfilterable" state. ⚠ still forwarded to `threads`, `turns` and `client_*`, which have no `_model_` sibling |
| User | Analytics `user_email=…` (exact case); `{NoUser}` skipped; tool calls → `user_id=~` via `{ID_a}` ∪ `{ID_c}` (case-insensitive); sessions and repos client-side | options = 30-date seat roster. Dropped on review funnels, workspace / model options, Tokens by Serving Speed, Tokens by reasoning effort, active-time read (looked up by row email) |
| Workspace (chip label "Workspace", key `team`) | Analytics `workspace_id=…`; compliance `workspace_id=…` on every read except `{ID_c}`; no-user DataPrime `$d.resource.attributes.workspace_id`; raw compliance log `$d.data.workspace_id` | options = `getWorkspaceIds` (ids only). Dropped on the seat roster, repo rates (`getModelBreakdown({})`); the hook carries no workspace and is narrowed only through the compliance rollup join |
| Application / Subsystem | none | no chip: `CodexFilterConfigService` defines Model, User and Workspace only |

# Entities harvest (filter dropdowns & rosters)

No label-values API calls — every dropdown comes from the three `CodexFilterConfigService` data reads (models, users, teams), and every roster or name join from data queries.

| Feeds | Query |
| --- | --- |
| Workspace options | `count by (workspace_id) (L(openai_analytics_codex_credits))` (unfiltered) ∪ `{NoUser}` workspace ids; empty ids dropped, sorted |
| User options + seat roster | `count by (user_email) (L(openai_analytics_codex_credits{user_email!=""}))` over the 30 dates ending at the range's last date (unfiltered), sorted |
| Model options | `count by (model) (max without (organization) (last_over_time({__name__=~"openai_analytics_codex_model_(credits|cost_usd|uncached_text_input_tokens|cached_text_input_tokens|text_output_tokens)",D}[H])))` ∪ `{NoUser}` models |
| Session owner | compliance `user_id` → `{ID_c}` `email`; unmatched → "" (not clickable, dropped under a User chip) |
| Tool-call people | `user_id` → `{ID_c}` `name` (else email, else raw id) |
| Active-time owner | `user_id` → `{ID_a}` `user_email` (all emails of that id), else `{ID_c}` `email` re-spelled to the Analytics casing |
| Session ↔ repo | `codex_session_repo_info{session_id, repository_name}` joined on `session_id` to the compliance rollup; repo name parsed with `parseRepoRemote` |
| Managed / unmanaged repos | settings `RepositorySettingsApiService.state$` (managed / unmanaged prefixes; not telemetry); failure → empty sets = "not configured" |

# Activity tab

## Insights (tab intro)

One `rxResource` (`CodexOptimizationService.getInsights`): `getUsageOverview` (7 + `{NoUser}`) + 21-date per-user credits (1) + seat roster (1) + `{Q_sessions}` (3, flag ON). Any failed credits / threads / turns total, per-user or session read blanks the panel. First cards show; "View all insights (N)" opens a drawer from the same result. CTAs open the Seats going unused or Longest threads drawers; none adds a chip.

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| Rule "Unused Codex seats" | `openai_analytics_codex_credits` | gate `F_analytics`; per-user 21 dates; roster unfiltered | `sum by (date) (L(openai_analytics_codex_credits{F_analytics}))` (+ `{NoUser}`) • `sum by (user_email) (L(MF(credits){F_analytics,NU}))` over the last 21 dates • `count by (user_email) (L(openai_analytics_codex_credits{NU}))` over 30 dates | total ≥ 1,000 credits and ≥ 1 roster seat < 500 credits in 21 days; silent under a User or Model chip. ⚠ roster ignores the Workspace chip, so seats of other workspaces count as unused. CTA → Seats going unused drawer. Not flag-gated |
| Rule "Threads are running long" | `…_threads`, `…_turns` (+ `{Q_sessions}` for the drawer) | `F_analytics` | `sum by (date) (L(openai_analytics_codex_threads{F_analytics}))`  • `sum by (date) (L(openai_analytics_codex_turns{F_analytics}))` | ≥ 50 threads and ≥ 15 turns per thread; CTA (only when sessions exist) → Longest threads drawer (top 50 by turns). Flag-gated. ⚠ a Model chip zeroes it (threads carry no model) |
| Rule "Turns wasted after a failure" | `{Q_sessions}` | client-side User / Model | `{Q_sessions}` | ≥ 200 turns and > 15% after a failed turn. ⚠ can never fire — `turns[]` is never filled. Flag-gated |

## Usage

≈ 30 reads: overview 7 (shared with the insights), threads/turns daily 2, Coding Activity 13 chunks, tool calls 2–3 (flag), client breakdown 5 + `{NoUser}`. Tool Call Breakdown rows open the Tool drawer; Client usage rows open the Client drawer.

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| KPI "Total Threads" (+ trend badge) | `openai_analytics_codex_threads` | `F_analytics` | `sum by (date) (L(openai_analytics_codex_threads{F_analytics}))` (dates summed, `getUsageOverview`); badge = last ⌊n/2⌋ chart points vs first ⌊n/2⌋ | badge needs ≥ 4 points (middle point left out on odd counts). Not flag-gated |
| Chart "Threads Over Time" | `…_threads` (+ `…_turns` fetched, not plotted) | `F_analytics` | `sum by (date) (L(openai_analytics_codex_threads{F_analytics}))`  • `sum by (date) (L(openai_analytics_codex_turns{F_analytics}))` | one line, one point per date (UTC). Not flag-gated |
| Calendar "Coding Activity" | `openai_analytics_codex_lines` | `F_lines` (User + Workspace) | 13 chunks × `sum by (date) (L(openai_analytics_codex_lines{F_lines}))` (30 dates each) | added + removed per day, trailing 364 days, own `@defer`; ignores the picker. Model chip → "Can't filter by model" state, no read. Not flag-gated |
| Grid "Tool Call Breakdown" — Tool / Type / Users / Calls / Success Rate / Fail Rate | `openai_compliance_codex_tool_calls_total` (+ `openai_compliance_user_identity`) | `tool_name`, `tool_type`, `status`, `user_id`; Workspace; User via ids | tool-call reads (see engine table) | flag-gated; Users names only calls since 2026-10-13; Model chip, or User chip on an earlier range → "can't be narrowed" state. Row click → Tool drawer |
| Grid "Client usage" — Client / Credits / Input / Output / Cached Tokens | `openai_analytics_codex_client_{credits,uncached_text_input_tokens,cached_text_input_tokens,text_output_tokens}` (+ `client_cost_usd` read, not shown) | `client_id`; `F_analytics` | `sum by (client_id) (L(openai_analytics_codex_client_credits{F_analytics}))` (same shape for the others) + `{NoUser}` clients | Input = uncached + cached; rows without `client_id` dropped; ⚠ a Model chip may zero it. Row click → Client drawer. Not flag-gated |

## Users

One grid; row set = every email the Analytics tokens, credits or threads/turns reads name (no CLI rows). 13–14 Analytics PromQL (+3 compliance, flag ON). A failure of all three identity reads errors the grid; any single failed read shows N/A in its column. Row click → User drawer.

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| User | row identity | `user_email` | — | exact-case email; two casings = two rows |
| Tokens | `…_uncached_text_input_tokens`  • `…_cached_text_input_tokens`  • `…_text_output_tokens` | `user_email`; `F_analytics` | `sum by (user_email) (L(MF(openai_analytics_codex_uncached_text_input_tokens){F_analytics}))` (+ 2 twins) | default sort; input (cached included) + output |
| Lines +/- | `openai_analytics_codex_lines` | `user_email`, `change="added|removed"`; `F_lines` | `sum by (user_email) (L(openai_analytics_codex_lines{F_lines,change="added"}))` (+ `removed`) | "N/A" when unattributed; not read under a Model chip (cell tooltip says why) |
| Credits | `openai_analytics_codex_credits` | `user_email`; `F_analytics` | `sum by (user_email) (L(MF(openai_analytics_codex_credits){F_analytics,user_email!=""}))` | "N/A" when absent |
| List Cost (+ delta) | `…_cost_usd`, else credits × 0.04 | `user_email`; `F_analytics` | `sum by (user_email) (L(MF(openai_analytics_codex_cost_usd){F_analytics}))`; delta: the same credits read over the last ⌊n/2⌋ dates (and ⌊n/2⌋+1 on odd n) vs the rest | no delta under 4 dates; middle date left out; a failed cost read shows N/A, not the fallback |
| Active Time | `openai_compliance_codex_tokens_total` active 10-min windows | `user_id` → email; Workspace + Model | compliance active time (3 instants) | flag-gated column; "N/A" when 0 |
| Models | `openai_analytics_codex_model_{uncached_text_input,cached_text_input,text_output}_tokens` | `user_email` × `model`; `F_analytics` | `sum by (user_email, model) (L(openai_analytics_codex_model_uncached_text_input_tokens{F_analytics}))` (+ 2 twins) | ranked by tokens |

# Cost tab

## Insights (tab intro)

One `forkJoin` (`CodexCostInsightsService.getInsights`): overview (7) + model breakdown (5) + users by credits (1) + credits/cost daily (2) + token types (1, flag) + session estimates (16, flag) (+ `{NoUser}`). Every read except the overview's non-input totals throws, so one failure blanks every Cost insight. CTAs scroll to Cost / Tokens cards or open the Heaviest credit users or User drawers.

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| Rule "Fast tier is driving credits" | `…_model_credits` | `model`, `speed`; `F_analytics` | `sum by (model, speed) (L(openai_analytics_codex_model_credits{F_analytics}))` | total ≥ 1,000 credits and fast share > 40%. CTA → Tokens › Model usage |
| Rule "Prompt cache is barely used" | `…_cached_text_input_tokens`, `…_uncached_text_input_tokens` | `F_analytics` | `sum by (date) (L(MF(openai_analytics_codex_cached_text_input_tokens){F_analytics}))`  • `sum by (date) (L(MF(openai_analytics_codex_uncached_text_input_tokens){F_analytics}))` | input ≥ 100k and hit rate < 70%. ⚠ fires a false "0%" if the cached series is missing; a failed read errors the panel. CTA → Cost section |
| Rule "Reasoning tokens are high" | `openai_compliance_codex_tokens_total` | `token_type`; Model + Workspace | `sum by (token_type) (TC(openai_compliance_codex_tokens_total{model=…,workspace_id=…,D}[H]))` | input + output ≥ 100k and `reasoning_output` ÷ `output` > 20%; silent when `reasoning_output` is absent or 0. Flag-gated. CTA → Tokens › reasoning effort |
| Rule "Full-size models carry most traffic" | `…_model_{uncached_text_input,cached_text_input,text_output}_tokens` | `model`; `F_analytics` | `sum by (model, speed) (L(openai_analytics_codex_model_text_output_tokens{F_analytics}))` (+ 2 twins) | tokens ≥ 100k and > 50% on models tagged expensive in `OPENAI_MODEL_TIERS`; skipped under a Model chip. CTA → Tokens › Model usage |
| Rule "Credit use is climbing fast" | `openai_analytics_codex_credits` | `F_analytics` | `sum by (date) (L(MF(openai_analytics_codex_credits){F_analytics}))` (+ `{NoUser}`) | ≥ 14 daily points, both weeks ≥ 500 credits, last week ≥ 1.3 × previous. Never fires on ranges under 14 days (default 7). CTA → Cost section |
| Rule "A few users drive most credits" | `openai_analytics_codex_credits` | `user_email`; `F_analytics` | `sum by (user_email) (L(MF(openai_analytics_codex_credits){F_analytics,user_email!=""}))` | ≥ 10 active users, total ≥ 1,000; top 3 combined ≥ 10 × the per-user average. CTA → Heaviest credit users drawer |
| Rule "One session cost far more" | `{Q_sessions}` × per-model rate, gated by posture | client-side User (exact case) / Model; Workspace | `{Q_sessions}`  • `getModelBreakdown`  • `getCostPosture` | ≥ 10 priced sessions and top session > 10 × the mean. Flag-gated. CTA → reads last-active for that session, then User drawer with the session card |

## Cost

≈ 37 reads: overview 7 (shared), credits + cost daily 2, credits by client 1 (+ the shared total), cost posture 8, Most Expensive Sessions 18 (flag), + `{NoUser}`. Rows: workspaces from the posture, sessions from `{Q_sessions}`. Workspace row click adds a Workspace chip; session row click opens the User drawer.

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| KPI "Credits Consumed" (+ trend) | `openai_analytics_codex_credits` | `F_analytics` | `sum by (date) (L(MF(openai_analytics_codex_credits){F_analytics}))` (+ `{NoUser}`), dates summed; trend from the credits daily series | lower-is-better badge; a failed read shows the error state. Not flag-gated |
| Chart "Credits Over Time" | `…_client_credits` (split) + `…_credits` (total) | `client_id`; User + Workspace only | `sum by (client_id, date) (L(openai_analytics_codex_client_credits{user_email=…,workspace_id=…}))`  • `sum by (date) (L(openai_analytics_codex_credits{user_email=…,workspace_id=…}))` (+ `{NoUser}`) | stacked per client + grey "Unattributed" = total − clients; single "Credits" area (`getCreditsCostOverTime`, Model applied) under a Model chip. Not flag-gated |
| KPI "List Cost" (+ trend) | `openai_analytics_codex_cost_usd` | `workspace_id`; `F_analytics` | `sum by (workspace_id) (L(MF(openai_analytics_codex_cost_usd){F_analytics}))` summed (+ `{NoUser}` cost); trend from `sum by (date) (L(MF(openai_analytics_codex_cost_usd){F_analytics}))` | "N/A" when no workspace reports cost; tooltip adds "r of t workspaces report cost". Not flag-gated |
| Grid "Cost by Workspace" — Workspace / Cost Reporting / Credits / List Cost / Users / Workspace lines +/- | `…_credits`, `…_cost_usd`, `…_lines` | `workspace_id` | `getCostPosture` (8 instants + `{NoUser}`) | Reporting chip = a `cost_usd` series exists; lines "Not reported" when no attributed row; any failed posture read errors the grid; row click adds a Workspace chip. Not flag-gated |
| Grid "Most Expensive Sessions" — # / Last active / User / List Cost / Git Repositories / Repo type / Model / Session ID | `{Q_sessions}`  • `…_model_*` rates + posture + `codex_session_repo_info`  • last-active range | `session_id`; client-side User (exact case) / Model; Workspace | 18 reads (3 sessions + 5 rates + 8 posture + 1 repo + 1 range) | flag-gated; top 20 priced sessions; empty when no workspace reports cost; repo "N/A" without the hook. Row click → User drawer with the session card |

## Tokens

≈ 16 reads: overview 7 (shared), tokens daily 3, model breakdown 5 (+ `{NoUser}`; Tokens by Serving Speed reuses it), reasoning effort 1 (flag). Model usage rows come from `getModelBreakdown`; a row click adds a Model chip.

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| KPIs "Input" / "Output" / "Cached" / "Cache Hit Rate" | `…_uncached_text_input_tokens`, `…_cached_text_input_tokens`, `…_text_output_tokens` | `F_analytics` | `sum by (date) (L(MF(openai_analytics_codex_uncached_text_input_tokens){F_analytics}))` (+ twins, + `{NoUser}`) | Input = uncached + cached; hit rate = cached ÷ input; Input / Output carry a half-window badge. Not flag-gated |
| Chart "Tokens Over Time" | same three | `F_analytics` | `sum by (date) (L(MF(openai_analytics_codex_uncached_text_input_tokens){F_analytics}))` (+ twins, + `{NoUser}`) | Input / Output / Cached areas, not stacked. Not flag-gated |
| Grid "Model usage" — Model / Speed / Credits / Input Tokens / Output Tokens / Cache hit rate | `openai_analytics_codex_model_*` | `model`, `speed`; `F_analytics` | `sum by (model, speed) (L(openai_analytics_codex_model_credits{F_analytics}))` (+ 4 twins, + `{NoUser}` models) | row click adds a Model chip. Not flag-gated |
| Chart "Tokens by Serving Speed" | `…_model_*` tokens | `speed`; Model + Workspace | `getModelBreakdown({model, workspace})` | flag-gated; User not applied |
| Chart "Tokens by reasoning effort" | `openai_compliance_codex_tokens_total` | `reasoning_effort`; Model + Workspace | `sum by (reasoning_effort) (TC(openai_compliance_codex_tokens_total{token_type=~"input|output",model=…,workspace_id=…,D}[H]))` | flag-gated; User not applied; empty state when no effort is reported |

# Impact tab

## Insights (tab intro)

One `combineLatest` (`CodexImpactInsightsService.getInsights`): code impact (3) + review funnel (5) + response funnel (13) + response daily (2) + repo activity (12, failure → silent). A failed lines, review or response read blanks the panel. CTAs scroll to Codex Code Review or open the Repo drawer on the unmanaged slice.

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| Rule "Codex removes as much as it adds" | `openai_analytics_codex_lines` | `change`; `F_lines` | `sum by (date) (L(openai_analytics_codex_lines{F_lines,change="added"}))`  • removed twin (+ `sum by (date) (L(openai_analytics_codex_credits{F_lines}))`, unused) | added + removed ≥ 500 and removed ≥ 50%. CTA → Codex Code Review |
| Rule "PR reviews come back empty" | `…_pr_reviews`, `…_review_comments` | `F_ws` | `sum(L(openai_analytics_codex_pr_reviews{F_ws}))`  • `sum(L(openai_analytics_codex_review_comments{F_ws}))` | ≥ 10 reviews and < 1 comment per review. CTA → Codex Code Review |
| Rule "Codex review comments go unanswered" | `…_review_response_comments`, `…_review_comments_engaged` | `F_ws` | `sum(L(openai_analytics_codex_review_response_comments{F_ws}))`  • `sum(L(openai_analytics_codex_review_comments_engaged{F_ws}))` | ≥ 50 comments and engaged < 2/3 |
| Rule "Review engagement is dropping" | same two, daily | `F_ws` | `sum by (date) (L(openai_analytics_codex_review_response_comments{F_ws}))` (+ engaged) | ≥ 14 day-points, each half ≥ 50 comments, recent rate < 0.75 × earlier |
| Rule "Work in unmanaged repositories" | repo hook + session tokens | repo classification (managed + unmanaged prefixes) | Git Repos queries | unmanaged tokens > 10% of repo tokens; needs prefixes configured. CTA → Repo drawer (unmanaged slice) |

## Git Repos

1 hook probe `count(max_over_time(codex_session_repo_info[H]))` (`rangeDays`), then 12 reads (5 hook + 2 compliance rollup + 5 rates). No hook data → "Install the hook" setup state (#27325), nothing else queried. Rows = sessions seen by both the hook and the compliance rollup; bar, slice and user-row clicks open the Repo or User drawers.

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| Chart "Repository Token Distribution" | `codex_session_repo_info`  • `codex_session_{commits,prs_opened,lines_added,lines_removed}`  • session tokens | `session_id`, `repository_name` | `max by (session_id, repository_name) (last_over_time(codex_session_repo_info[H]))`  • compliance rollup (see engine table) | tokens per repo (session tokens split across its repos); Workspace narrows via the rollup; a failed read errors the card; not flag-gated; bar click → Repo drawer |
| Pie "Managed vs Unmanaged" | same rows + repo prefix settings | classification | same | shown only when prefixes are configured; a failed settings read reads as "not configured" |
| Grid "Top Users on Unmanaged Repositories" | same rows | `user_email` | same | top 5 by unmanaged tokens; ⚠ classifies with managed prefixes only (ignores unmanaged carve-out prefixes) |

## Codex Code Review

8 reads: review funnel 5 (PR reviews tile + priority pie) + code impact 3 (Lines of code tile). Workspace grain for reviews; no rows, no click.

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| KPI "Codex PR reviews" | `openai_analytics_codex_pr_reviews` | `F_ws` | `sum(L(openai_analytics_codex_pr_reviews{F_ws}))` | workspace grain. Not flag-gated |
| KPI "Lines of code" | `openai_analytics_codex_lines` | `F_lines` | `sum by (date) (L(openai_analytics_codex_lines{F_lines,change="added"}))`  • removed (+ credits, unused) | daily points summed; a floor (attributed rows only). Not flag-gated |
| Pie "PR comments by priority" | `…_review_comments`, `…_review_comments_by_priority` | `priority="p0|p1|p2"`; `F_ws` | `sum(L(openai_analytics_codex_review_comments_by_priority{F_ws,priority="p0"}))` (p1, p2) | header = total comments. Not flag-gated |

# Drawers

## User drawer (Users row, Most Expensive Sessions row, insight CTAs, other drawers' user rows)

Singleton `CodexUserDrawerService` (provided once in the dashboard shell). Pins `user_email="<email>"` over a snapshot of the page filters taken at open; a newer open cancels the previous load. ≈ 28 PromQL + 1 DataPrime per open (roster 1, tokens 3, tokens daily 3, threads/turns 2, model breakdown 5, repos 7, hook probe 1, run time 3, lines 2, tools 1 + raw log). Read-only; repo bars open the Repo drawer and Tools Used rows the Tool drawer.

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| Header "Username" / session id | — | — | none | session id instead of the name when opened from a session |
| Session card — Username / List Cost | the clicked session row | — | none | session mode only; List Cost = the row's estimate, N/A when ≤ 0 |
| KPI "Input Tokens" | `…_uncached_text_input_tokens`  • `…_cached_text_input_tokens` | `user_email="<e>"`, Model, Workspace | `sum by (user_email) (L(MF(openai_analytics_codex_uncached_text_input_tokens){model=…,user_email="<e>",workspace_id=…}))` (+ cached twin) + roster `count by (user_email) (L(openai_analytics_codex_credits{NU}))` (30 dates) | N/A when the read fails or the email is off the roster; 0 when on the roster with no usage |
| KPI "Output Tokens" | `…_text_output_tokens` | same | `sum by (user_email) (L(MF(openai_analytics_codex_text_output_tokens){model=…,user_email="<e>",workspace_id=…}))` | same N/A rule |
| KPI "Total Threads" | `…_threads` (+ `…_turns` read, not shown) | `user_email="<e>"`, Workspace; no Model | `sum by (user_email) (L(openai_analytics_codex_threads{user_email="<e>",workspace_id=…}))` (+ turns twin) | same N/A rule |
| KPI "Total Codex Run Time" | `openai_compliance_codex_tokens_total` active 10-min windows | the person's `user_id`s; Workspace + Model | `{ID_a}`  • `{ID_c}` → `sum by (user_id) (count_over_time((sum by (user_id) (sum_over_time(openai_compliance_codex_tokens_total{workspace_id=…,model=…,user_id=~"<ids>",D}[10m])) > 0)[<N>h:10m]))` × 600 s | shown under `ai-center-openai-codex` OR `ai-center-openai-administration-usage`; N/A when 0 or failed |
| Chart "Tokens Over Time" | the three token families | `user_email="<e>"`, Model, Workspace | 3 × `sum by (date) (L(MF(<tokens>){model=…,user_email="<e>",workspace_id=…}))` | Input / Output / Cached areas; error state on failure. A second, Model-free copy is read for an unrendered cost trend |
| Chart "Git Repositories" | hook + compliance rollup | `session_id`, `repository_name`; sessions filtered to the email | Repos reads (7, `rangeDays`) + hook probe | tokens per repo; bar click → Repo drawer; hook-setup state when the probe is empty |
| Chart "Repo Type Split" | same rows + repo prefix settings | classification (managed / unmanaged / unknown) | same | configure state when no prefixes are set |
| Chart "Models Used" | `openai_analytics_codex_model_{uncached_text_input,cached_text_input,text_output}_tokens` (+ `_credits`, `_cost_usd` read) | `user_email="<e>"`, Workspace; no Model | 5 × `sum by (model, speed) (L(openai_analytics_codex_model_<m>{user_email="<e>",workspace_id=…}))` | share of this person's tokens per model; error state on failure |
| KPIs "Lines Added" / "Lines Removed" | `openai_analytics_codex_lines` | `user_email="<e>"`, Workspace | `sum by (user_email) (L(openai_analytics_codex_lines{user_email="<e>",workspace_id=…,change="added"}))` (+ removed) | not-reported marker when unattributed; ⚠ a failed read may break the drawer (appendix 6) |
| KPIs "Commits" / "Pull Requests" | `codex_session_commits`, `codex_session_prs_opened` | the person's repo sessions | `max by (session_id, repository_name) (max_over_time(codex_session_commits[H]))` (+ prs) via the Repos reads | summed over the person's (session, repo) pairs; not-reported when no pair was measured |
| Grid "Tools Used" — Tool / Type / Calls / Failed / Success Rate / Fail Rate / Threads / Last Used | `openai_compliance_codex_tool_calls_total`  • raw compliance log | the person's `user_id`s; Workspace | `{ID_a}`  • `{ID_c}` → `sum by (tool_name, tool_type, status) (EC(openai_compliance_codex_tool_calls_total{user_id=~"<ids>",workspace_id=…,D}[H]))`  • raw-log sessions grouped by tool | shown under either flag; Model chip → "unfilterable"; Threads / Last Used arrive later from the raw log (N/A without it); row click → Tool drawer (person grain) |

## Client drawer (Client usage row)

`CodexClientDrawerService`, provided by the Usage panel. Pins `client_id="<id>"` over the live page filters. 4 PromQL + `{NoUser}` + 3 compliance (flag ON). Rows = people from the client reads plus one no-user row; row click → User drawer.

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| User | row identity | `user_email` | — | plus a "Codex code review (no user)" row from `{NoUser}` (none under a Model chip) |
| Credits | `openai_analytics_codex_client_credits` | `user_email`; `{F_analytics,client_id="<id>"}` | `sum by (user_email) (L(openai_analytics_codex_client_credits{F_analytics,client_id="<id>"}))` | default sort |
| Input Tokens | `…_client_uncached_text_input_tokens`  • `…_client_cached_text_input_tokens` | same | `sum by (user_email) (L(openai_analytics_codex_client_uncached_text_input_tokens{F_analytics,client_id="<id>"}))` (+ cached) | uncached + cached |
| Output Tokens | `…_client_text_output_tokens` | same | `sum by (user_email) (L(openai_analytics_codex_client_text_output_tokens{F_analytics,client_id="<id>"}))` | — |
| Cached Tokens | `…_client_cached_text_input_tokens` | same | as above | — |
| Threads | `openai_compliance_codex_session_tokens_total` | `user_id` → `{ID_a}` email; Workspace + Model | `count by (user_id) (sum by (user_id, session_id) (sum_over_time(openai_compliance_codex_session_tokens_total{client_id="<id>",workspace_id=…,model=…,D}[H])) > 0)` | flag-gated column; sessions stand in for threads; N/A on failure |
| Models | `openai_compliance_codex_tokens_total` | `user_id` → `{ID_a}` email; Workspace + Model | `sum by (user_id, model) (TC(openai_compliance_codex_tokens_total{client_id="<id>",token_type=~"input|output",workspace_id=…,model=…,D}[H]))` | flag-gated column; ordered by tokens |

## Tool drawer (Tool Call Breakdown row or Tools Used row)

Singleton `CodexToolDrawerService` (shell). Pins `tool_name` + `tool_type`; Workspace and User chips apply. 1 tool-call read + `{ID_c}` (+ 2 identity reads under a User chip) + 1 DataPrime raw-log read. Rows = people with attributable calls; row click → User drawer (opened above); a failed read shows the error state.

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| Header figures — Calls / Calls naming a user / Type | the clicked row + the per-user read | — | none extra | workspace grain: Calls = the row's total, "Calls naming a user" only when smaller; person grain: Calls = attributed sum |
| User | `openai_compliance_user_identity` | `user_id` → `email` | `{ID_c}` | raw `user_id` when no email resolves |
| Calls / Succeeded / Failed / Success Rate / Fail Rate | `openai_compliance_codex_tool_calls_total` | `user_id`, `status`; `tool_name`, `tool_type` pinned | `sum by (user_id, status) (EC(openai_compliance_codex_tool_calls_total{tool_name="<t>",tool_type="<ty>",user_id!="",workspace_id=…,D}[H]))` (`tool_type=""` for untyped, `user_id=~"<ids>"` under a User chip) | Succeeded = `status="completed"`; no rows under a Model chip or a pre-2026-10-13 User chip |
| Threads | raw compliance log | `$d.data.actor.user_id` | `source default/ai.raw.openai.compliance_logs | filter … tool_name == '<t>' && tool_type … | groupby $d.data.actor.user_id as user_id aggregate distinct_count($d.data.event_details.session_id) as threads, max($m.timestamp) as last_used` | pending marker until the log answers; N/A without it |

## Repo drawer (Git Repos bars / slices, Impact insight CTA, user-drawer repo bars)

Singleton `CodexRepoDrawerService` (shell), built on the shared `createRepoDrawer` in cost mode. Pins one repo or one slice (managed / unmanaged / unknown) over the live page filters. Reads `getRepoCostBreakdown` + `getTopUsersForRepo` = the 12 Repos reads (shared per-minute cache). Every money figure is tokens × the unfiltered per-model rate. Row click → User drawer (opened above).

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| KPI "List Cost" | session tokens × `getModelBreakdown({})` rate | `session_id`, `repository_name`, `model` | Repos reads + 5 × `sum by (model, speed) (L(openai_analytics_codex_model_<m>))` | estimate; sums the matching repos |
| KPI "Threads" | `codex_session_repo_info` ∩ rollup | `session_id` | Repos reads | distinct sessions per repo, summed |
| KPI "Users" | rollup owner emails | `user_id` → `{ID_c}` email | Repos reads | count of grid rows; "—" when none |
| KPIs "Lines of Code" / "Commits" / "Pull Requests" | `codex_session_lines_added`  • `_lines_removed`, `_commits`, `_prs_opened` | `session_id`, `repository_name` | `max by (session_id, repository_name) (max_over_time(codex_session_<c>[H]))` | counted once per (session, repo); unmeasured counts read 0 |
| Grid — User / List Cost / Threads / Repos / Models / % of est. spend | same session rows | per owner email | Repos reads | ranked by estimated cost; Repos lists the person's unmanaged repos |

## Seats going unused ("Unused Codex seats" CTA)

`CodexUnusedSeatsDrawerService`, provided by the Activity insights panel. No query — rows are handed in by the rule. Row click → User drawer.

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| User | seat roster | `user_email` | the rule's `count by (user_email) (L(openai_analytics_codex_credits{NU}))` (30 dates) | seats under 500 credits |
| Credits (last 21d) | `openai_analytics_codex_credits` | `user_email` | the rule's `sum by (user_email) (L(MF(credits){F_analytics,NU}))` over 21 dates | 0 for roster seats absent from the read |

## Longest threads ("Threads are running long" / "Turns wasted after a failure" CTA)

`CodexLongThreadsDrawerService`, provided by the Activity insights panel. No query — up to 50 `{Q_sessions}` rows handed in by the rule. Row click → User drawer, only when the owner email is known.

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| Thread | `openai_compliance_codex_session_tokens_total` | `session_id` | `{Q_sessions}` | — |
| User | `openai_compliance_user_identity` | `user_id` → `email` | `{Q_sessions}` | empty when unmatched |
| Turns | `openai_compliance_codex_session_turns_total` | `session_id` | `{Q_sessions}` | sort key |
| Final turn / Failed turns | — | — | none | ⚠ always empty / "—" (`turns[]` is never filled) |
| Tokens in / Tokens out | `openai_compliance_codex_session_tokens_total` | `token_type="input"` / `"output"` | `{Q_sessions}` | input includes cached, output includes reasoning |

## Heaviest credit users ("A few users drive most credits" CTA)

`CodexCreditConcentrationDrawerService`, provided by the Cost insights panel. No query — the top 3 rows are handed in by the rule. Row click → User drawer.

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| User | `openai_analytics_codex_credits` | `user_email` | the rule's `sum by (user_email) (L(MF(credits){F_analytics,NU}))` | — |
| Credits | same | same | same | — |
| x avg per active user | same | same | same | credits ÷ mean over users with credits > 0 |
| % of credits | same | same | same | credits ÷ total over users with credits > 0 |

# Empty state

- Whole-page gate lives in the Code Agents shell: Codex is detected by any of the three `[10d]` probes (native OTLP, Analytics credits, compliance CODEX events). A tenant with only another agent integrated can open `openai/codex` and see every widget empty — the dashboard has no "not integrated" state.
- Git Repos shows the repo-tracking hook setup state when `codex_session_repo_info` has no series in the range (probe fails open).
- Coding Activity and Tool Call Breakdown show "can't filter" states under chips they cannot honour; Users Lines +/- shows N/A under a Model chip.
- Most Expensive Sessions shows its empty state whenever no workspace reports cost (all estimates null).
- Analytics totals / posture / review-funnel failures show the widget's error state; every other failed read (per-user, daily, compliance, hook) errors its widget or blanks its insights panel; no-user and raw-compliance-log failures are silent.

# Appendix — dead / duplicated queries worth cleaning up

1. **Fan-out per tab** (flag ON, no chips, unique requests after the per-minute cache): Activity ≈ 50 PromQL + 1 DataPrime (13 of them the Coding Activity chunks), Cost ≈ 36 PromQL + 1 DataPrime, Impact ≈ 36 PromQL; user drawer ≈ 28 PromQL + 1 DataPrime per open.
2. ⚠ Two rules and two cells can never show data: "Turns wasted after a failure", Longest threads' Final turn / Failed turns (`turns[]` is never filled).
3. ⚠ Bug — the Model chip is forwarded to `threads`, `turns` and `client_*`, which have no `_model_` sibling: Total Threads, Threads Over Time, Client usage, the Users row set's threads source and "Threads are running long" would read 0 under a Model chip.
4. ⚠ Bug — the unused-seats roster ignores the Workspace chip; every seat in another workspace counts as unused.
5. ⚠ Bug — "Prompt cache is barely used" fires a false "0%" when the cached-tokens series is missing.
6. ⚠ Bug — user drawer: a failed `getCodeImpactByUser` read is caught to `null`, then `codeImpact.find(…)` runs on it; the drawer's switchMapped load pipeline would error and stop loading for every later open (lib is not strict-null).
7. User drawer computes `estCost`, `estCostTrend`, `requests` and `tokens` but renders none of them (only the session card's List Cost); the `costTrendSeries` read (3 queries, duplicated only under a Model chip) feeds nothing visible.
8. Session filtering compares emails lowercased in Activity insights but exactly in `CodexCostService` (Most Expensive Sessions, "One session cost far more").
9. `getCodeImpact` always reads `sum by (date) (L(openai_analytics_codex_credits{F_lines}))`; neither the Lines of code tile nor the churn rule uses it.
10. Dead code: `getActiveDaysByUser`, `getTopUsersByCredits`, 13 of 14 public `CodexService` methods, every `codexDataprimeQueries` builder except `repoSessionRollup` (flag-OFF fallback only), the fading-seats drawer (provided, never opened).

# Unverified — live checks to run

These claims come from code only; each needs one query against a team with Codex Analytics / Compliance data (for example knowledge-base on eu2).

| Claim | Query |
| --- | --- |
| Per-user families carry no `model` (Model chip behaviour on threads / clients) | `count by (model) (last_over_time(openai_analytics_codex_threads[7d]))` and the same on `openai_analytics_codex_client_credits` |
| Every Analytics series carries `date`, re-reads stamp the same date | `count by (date) (last_over_time(openai_analytics_codex_credits[3d]))` vs `count(last_over_time(openai_analytics_codex_credits{date=""}[3d]))` |
| `organization` is sometimes missing (why `max without (organization)`) | `count by (organization) (last_over_time(openai_analytics_codex_credits[7d]))` |
| No-user rows are in the raw log and not in the metrics | `source default/ai.raw.openai.codex.analytics_logs | filter $d.stream == 'openai.analytics.codex_usage' && $d.data.user_id == null | count` vs `count(last_over_time(openai_analytics_codex_credits{user_id=""}[7d]))` |
| Compliance `token_type` vocabulary (input, cached_input, output, reasoning_output) | `count by (token_type) (last_over_time(openai_compliance_codex_tokens_total[7d]))` |
| Session ids match across compliance and the repo hook | `count(max by (session_id) (last_over_time(codex_session_repo_info[7d])) and on (session_id) max by (session_id) (last_over_time(openai_compliance_codex_session_tokens_total[7d])))` |
| Tool-call `user_id` present since 2026-10-13 | `count(last_over_time(openai_compliance_codex_tool_calls_total{user_id!=""}[30d]))` |
| Raw compliance log carries Codex tool events outside staging | `source default/ai.raw.openai.compliance_logs | filter $d.stream == 'openai.compliance.codex' && $d.data.event_details.tool_name != null | count` |
| Series without `user_email` / `workspace_id` | `count(last_over_time(openai_analytics_codex_credits{user_email=""}[7d]))` |
| `EC()` over-counts a cumulative day total of 1 re-emitted every scrape (simulator caveat) | `sum by (tool_name) (sum_over_time(openai_compliance_codex_tool_calls_total[1d]))` vs `max by (tool_name) (max_over_time(openai_compliance_codex_tool_calls_total[1d]))` on a quiet tool |
| User drawer stops loading after a failed lines read (appendix 6) | not a live query — reproduce by making `getCodeImpactByUser` throw in a `CodexUserDrawerService` unit test and opening twice |