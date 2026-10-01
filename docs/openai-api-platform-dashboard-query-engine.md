# OpenAI API Platform Dashboard — Real Query Engine Map

Full map of **widget → metric → labels → PromQL** for the OpenAI **API Platform** dashboard's real query service (`PromOpenAiAdminQueriesService`, `libs/ai-center/code-agents/src/lib/openai-administration/prom-openai-admin-queries.service.ts`, `origin/master` @ `453efa632ca`, shipped in PR #23368). Sidebar row `api-platform` → route `openai/administration`, gated by the `ai-center-openai-usage` flag. Traced code-level on 2026-09-30: component → panel `rxResource` → query-service method → PromQL template, then an adversarial re-derivation of every row and a completeness pass over every template and call site. **Not run against live data**; claims marked ⚠ unverified are listed in the last section, "Unverified — live checks to run". Cursor counterpart: [Cursor Usage Dashboard — Real Query Engine Map](https://app.notion.com/p/Cursor-Usage-Dashboard-Real-Query-Engine-Map-3cbc048defad81019087e90326175a9b?pvs=21).

<aside>
⚙️

Every `openai_administration_usage_*` / `openai_administration_cost_*` sample is **one daily bucket** with a `date` label (YYYY-MM-DD, restated daily), so each calendar day is its own series. The service therefore only ever runs **instant** queries with `last_over_time(…[Nd])`, sums the day-series for window totals, and builds every time axis from the `date` label. It never uses `rate()` / `increase()` / `sum_over_time()`, and never a range query.

</aside>

# Conventions

- `F_usage` — `usageLabelFilter`: `model`, `project_id`, `user_id`, `api_key_id`, `service_tier` matchers; emits nothing when no dimension is set. One value → `label="v"`, several → `label=~"v1|v2"` (regex-escaped); an empty chip emits no matcher, never `label=""`.
- `F_cost` — `costLabelFilter`: cost has no `model` label, so the Model chip becomes `line_item=~"(<m1>|<m2>), .*"`; `project_id` / `user_id` / `api_key_id` as usual; `service_tier` dropped (no such label on cost).
- `F_project` — `projectOnlyFilter`: `project_id` only. Used by the directory (`*_info`) and spend-limit reads.
- `[W]` — `[Nd]`, N = max(1, ceil((to − from) / 1 day)). Always whole days: 24h → `[1d]`, 6h → `[1d]`, 7d+1h → `[8d]`. `lastDays` caps N (only the spike-mode API-key drawer passes 7 → `[W_7d]` = `[min(7, N)d]`).
- `[W_prev]` — `[Nd] offset Nd`: the adjacent previous window at the same instant, never `[2Nd]` (that would double-count the current window). Used only by the Cost change tile.
- Evaluation — every query is ONE instant query `GET /metrics/api/v1/query` at `time = floor(to/1000)`, 30 s timeout, no step. In quick mode `to` is re-read as `Date.now()` on each call, so parallel "identical" reads can land on different seconds.
- Time axis — the `date` label plotted at UTC midnight. A day with no series is absent: there is **no zero-fill** anywhere in the data layer.
- `*_info` series — constant 1, the labels carry the record → read raw with `last_over_time`, one entity per distinct label set; never summed, never deduplicated by id.
- Cost model = `costModel(line_item)` = text before the first ", " (`gpt-4o-2024-08-06, cached input` → `gpt-4o-2024-08-06`); a line item without ", " (`file search tool calls`) stays whole.
- Fail-soft — each leg catches its own HTTP / timeout / parse error as an empty result and every method has an outer `catchError`, so no method ever errors: an outage renders as "No data", "$0" or "0", never an error state.
- No caching — the service has no `shareReplay`; each panel and drawer `rxResource` issues its own calls, and every section is `@defer (on viewport)`, so a section fetches only once scrolled into view.

# Engine operation → PromQL shape

| Op | Query shape | Evaluation |
| --- | --- | --- |
| `getUsageCompletions(F, {lastDays?})` | 15 legs, one per field: `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_<field>{F_usage}[W]))` — `<field>` ∈ `input_tokens`, `input_uncached_tokens`, `input_cached_tokens`, `input_cache_write_tokens`, `input_text_tokens`, `input_audio_tokens`, `input_image_tokens`, `input_cached_text_tokens`, `input_cached_audio_tokens`, `input_cached_image_tokens`, `output_tokens`, `output_text_tokens`, `output_audio_tokens`, `output_image_tokens`, `requests` | 15 instants @ to, forkJoin. Folded client-side into totals, byModel (sorted by input desc), overTime per `date`, byApiKey (+ per key×model split); cacheHitRate = cached ÷ input. Only input / cached / output / requests reach the screen |
| `getCost(F, {previousPeriod?})` | `sum by (line_item, project_id, project_name, date) (last_over_time(openai_administration_cost_usd{F_cost}[W]))` • `sum by (line_item, project_id, project_name, date, unit) (last_over_time(openai_administration_cost_quantity{F_cost}[W]))`; previous period swaps `[W]` → `[W_prev]` | 2 instants @ to. totalUsd = Σ all series; overTime per `date`; byModel per `costModel(line_item)`; byProject per `project_id`, named by the `project_name` label. The quantity leg is never consumed |
| `getDirectory(F)` | 7 legs: `last_over_time(openai_administration_org_user_info[W])`, `last_over_time(openai_administration_project_info{F_project}[W])`, `last_over_time(openai_administration_project_api_key_info{F_project}[W])`, `last_over_time(openai_administration_project_service_account_info{F_project}[W])`, `last_over_time(openai_administration_org_invite_info[W])`, `last_over_time(openai_administration_admin_key_info[W])`, `last_over_time(openai_administration_project_member_info{F_project}[W])` | 7 instants @ to. `unavailable` only when all 7 fail. createdAt / lastUsedAt / redactedSuffix are always null / null / "" on this plane |
| `getSpendLimitsAndAlerts(F)` | 5 legs `last_over_time(openai_administration_<m>{F_project}[W])` — `<m>` ∈ `spend_limit_configured`, `project_spend_limit_configured`, `project_spend_limit_enforcing`, `project_spend_limit_usd`, `spend_alert_threshold_usd` | 5 instants @ to. Org row = configured > 0 with `limitUsd: null`, `enforcing: false` hard-coded (org `spend_limit_usd` / `spend_limit_enforcing` are never queried). Project rows joined by `project_id`; `*_usd` read verbatim (no cents conversion) |
| `getUsageFamilies(F)` | 11 legs `sum(last_over_time(openai_administration_usage_<m>{F_usage}[W]))` — `<m>` ∈ `embeddings_tokens`, `embeddings_model_requests`, `audio_speeches_characters`, `audio_speeches_model_requests`, `audio_transcriptions_seconds`, `audio_transcriptions_model_requests`, `moderations_tokens`, `moderations_model_requests`, `web_search_requests`, `web_search_model_requests`, `file_search_requests` | 11 instants @ to, no grouping; an absent or failed leg reads 0. Images, vector stores and code interpreter are never queried |
| `getUsageFamilyApiKeyIds(family, F)` | `count by (api_key_id) (last_over_time(<K>{F_usage}[W]))` — `<K>` = `usage_embeddings_model_requests` / `usage_audio_speeches_model_requests` / `usage_audio_transcriptions_model_requests` / `usage_moderations_model_requests` / `usage_web_search_requests` / `usage_file_search_requests`; Images → no query | 1 instant @ to (0 for Images). Only the `api_key_id` label is read (count ignored), ids deduped and sorted lexicographically |
| `getDataRetentionAndCertificates` / `getRateLimitsAndPermissions` / `getRbac` | `*_info` / rate-limit / posture reads | Not called by API Platform — used by the API Platform Compliance page and the unwired governance / identity panels |
| `getAuditEvents` | `sum by (event_type) (last_over_time(openai_administration_audit_events_type_total[W]))` — filters ignored | No production caller (only the unwired audit panel) |
| Detection probe (parent Code Agents shell) | `count(max_over_time(openai_administration_usage_input_tokens[10d]))` | 1 of 16 PromQL probes, once per shell at `Date.now()`, fixed `[10d]` (ignores the picker); marks `CODEX:VENDOR_ADMIN_API` |

# Filters → matchers

| Page filter | Matcher emitted | Condition / translation |
| --- | --- | --- |
| Time range | no label matcher — sets `[W]`, the evaluation time `to`, and `[W_prev]` | quick presets 24h / 2 / 3 / 5 / 7 / 14 days, default **last 14 days** (the week-over-week insight needs 14 daily buckets); custom ranges round up to whole days |
| Project (multi, "is") | `project_id="<id>"` / `project_id=~"a|b"` via `F_usage`, `F_cost` and `F_project` | options = unfiltered `getDirectory()` → `project_info` name (archived included). **Silently dropped** on `org_user_info`, `org_invite_info`, `admin_key_info` → admin keys keep showing under a project chip. Empties the org `spend_limit_configured` row by design (no `project_id` label). Not persisted to the URL |
| Model (multi, "is") | usage: `model="<m>"` / `model=~"…"`; cost: `line_item=~"(<m1>|<m2>), .*"` | options = unfiltered `getCost()` → `costModel(line_item)` sorted by spend (zero-spend models absent); also set by a Models-row click. Ignored by the directory and spend reads. Also hits every Platform-features family (⚠ unverified whether those series carry `model`). A non-model line item (`file search tool calls`) matches nothing on either plane |
| userId (drawer-pinned, not a chip) | usage: `user_id="<id>"` | set only by the user drawer; the directory ignores it. ⚠ unverified that `openai_administration_usage_*` carries `user_id` |
| apiKeyId / serviceTier | `api_key_id=` (usage + cost) / `service_tier=` (usage only) | on the filter type, never set by any code on the page |

# Entities harvest (filter dropdowns & rosters)

No label-values API calls — every dropdown and roster comes from data queries. Dropdown options are fetched once per chip, when the chip is created, and are not refetched when the time range changes.

| Feeds | Query |
| --- | --- |
| Project dropdown (label = name, value = `project_id`) | `getDirectory()` unfiltered — all 7 legs run, only `last_over_time(openai_administration_project_info[W])` is used |
| Model dropdown (label = value = cost model) | `getCost()` unfiltered — both legs run; options = `cost.byModel` line items sorted by spend desc |
| Directory roster — Projects, API keys & service accounts, all three drawers | the 7 `getDirectory` legs mapped by `toDirectory`; `F_project` on project-scoped families only; one entity per distinct label set, no dedupe |
| API-key owner → user (Owner columns, user-drawer link) | client-side join: `owner_email` → `org_user_info.user_id` → email; else `owner_email`, else `owner_name`. Service-account-owned keys: the lookup is keyed by SA `user_id`, so it misses and the raw `owner_name` shows |
| Project names everywhere | `project_info` `name` label, fallback `project_id` |

# Insights (above the accordion)

One `rxResource` runs `getCost` + `getUsageCompletions` + `getSpendLimitsAndAlerts` in one forkJoin — **22 instant queries** @ to over `[W]`, current window only (no previous-period read); the rules read 5 of them. The first 2 fired cards show; "View all insights (N)" opens a drawer from the same result (no query, writes `?insight=all`). CTAs scroll to a section, except the spike CTA, which opens the API-key drawer. No rule adds a filter chip.

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| Rule "Nearing the organization spend limit" | `openai_administration_spend_limit_configured` (org row), `openai_administration_cost_usd` | spend: no grouping, `F_project`; cost: `F_cost` | `last_over_time(openai_administration_spend_limit_configured{F_project}[W])` • `sum by (line_item, project_id, project_name, date) (last_over_time(openai_administration_cost_usd{F_cost}[W]))` | fires at cost ≥ 80 % of the org limit. ⚠ **Can never fire**: the org `limitUsd` is hard-coded null. CTA "Set project spend limits" → Projects |
| Rule "Spend is climbing week over week" | `openai_administration_cost_usd` (overTime) | folded per `date`; `F_cost` | `sum by (line_item, project_id, project_name, date) (last_over_time(openai_administration_cost_usd{F_cost}[W]))` | needs ≥ 14 date points; last 7 points vs the 7 before; previous week ≥ $100 and growth ≥ 30 %. "7 days" = 7 points (no zero-fill). CTA "See the keys behind the spike" → API-key drawer, spike mode |
| Rule "Projects have no spend limit" | `openai_administration_project_spend_limit_configured` | one series per `project_id`; `F_project` | `last_over_time(openai_administration_project_spend_limit_configured{F_project}[W])` | fires when ≥ 1 project reads ≤ 0; denominator = projects with a series, not directory projects. CTA → Projects |
| Rule "<model> drives most of the spend" | `openai_administration_cost_usd` (byModel) | `line_item` → `costModel` → `openAiModelAlias` (dated suffix stripped); `F_cost` | `sum by (line_item, project_id, project_name, date) (last_over_time(openai_administration_cost_usd{F_cost}[W]))` | top alias ≥ 60 % of spend, ≥ 2 aliases, tier "expensive" in the hand-kept `OPENAI_MODEL_TIERS`. A single-model chip always silences it. ⚠ alias total can differ from the per-snapshot Models rows. CTA "Review cost by model" → Cost |
| Rule "Context is re-sent, not cached" | `openai_administration_usage_input_tokens`, `openai_administration_usage_input_cached_tokens` | by (model, api_key_id, date) → window totals; `F_usage` | `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_input_tokens{F_usage}[W]))` • `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_input_cached_tokens{F_usage}[W]))` | cached ÷ input < 70 % with ≥ 100k input tokens. CTA "Review usage" → Usage, which does not show the rate |

# Accordion sections

## Cost

The same component renders on the API Platform Compliance page (row click disabled), so `getCost` and `getUsageCompletions` are shared by both pages.

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| KPI "Cost" | `openai_administration_cost_usd` | by (line_item, project_id, project_name, date); `F_cost` | `sum by (line_item, project_id, project_name, date) (last_over_time(openai_administration_cost_usd{F_cost}[W]))` @ to (+ the unused `cost_quantity` leg) | Σ every series, already dollars. Value click scrolls to Projects |
| KPI "Cost change" | `openai_administration_cost_usd` | same, both windows; `F_cost` | the Cost pair @ to + the same pair over `[W_prev]` = `[Nd] offset Nd` (4 instants) | Δ % and Δ $ vs the previous period; "—" when the previous total ≤ 0; spinner until both windows resolve |
| Chart "Cost over time" | `openai_administration_cost_usd` | folded per `date` | no extra query — same resource as Cost | one area series, each point = that day at UTC midnight (not cumulative); no zero-fill; empty when total ≤ 0; static |
| Grid "Models" — Model / Cost / % of Cost | `openai_administration_cost_usd` | `line_item` folded by `costModel`; `F_cost` | the Cost resource | rows = cost byModel LEFT-joined to usage byModel by exact model string; usage-only models appended with Cost "—"; % of Cost = row ÷ Σ rows (pre-search); default sort Cost desc; row click sets the Model chip |
| Grid "Models" — Input / Output / Cached Tokens, Requests | `openai_administration_usage_input_tokens`, `…_output_tokens`, `…_input_cached_tokens`, `…_requests` | by model (Σ over api_key_id, date); `F_usage` | 15 × `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_<field>{F_usage}[W]))` @ to — 4 rendered | cached ⊂ input, never added; cost-only line items show "—". ⚠ the join assumes the cost `line_item` prefix equals the usage `model` label |

## Usage

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| Chart "Tokens Over Time" — Input / Output / Cached areas | `openai_administration_usage_input_tokens`, `…_output_tokens`, `…_input_cached_tokens` | by (model, api_key_id, date) → Σ per `date`; `F_usage` | 15 × `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_<field>{F_usage}[W]))` @ to — 3 drawn | overlapping unstacked areas, x = `date` at UTC midnight; requests not drawn; the "Tokens" axis unit is set but dropped by the config builder; empty when every point ≤ 0; static, no drill |

## Projects (section id `budget`)

4 `rxResource`s = **29 instant queries**: `getDirectory` (7) + `getUsageCompletions` (15) + `getCost` (2) + `getSpendLimitsAndAlerts` (5). One row per `project_info` series (archived included); every other source is a left join. Row click opens the API-key drawer in project mode. The Model chip narrows only the Models and Cost columns.

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| Project / Status | `openai_administration_project_info` | `project_id`, `name`, `status`; `F_project` | `last_over_time(openai_administration_project_info{F_project}[W])` | name falls back to `project_id`; status "archived" → Archived chip, else Active |
| Created | — | — | none | always "—" (createdAt hard-coded null) |
| Members | `openai_administration_project_member_info` | `project_id`; `F_project` | `last_over_time(openai_administration_project_member_info{F_project}[W])` | count of member SERIES for the project (not distinct users); archived → "—" |
| API keys | `openai_administration_project_api_key_info` | `project_id`; `F_project` | `last_over_time(openai_administration_project_api_key_info{F_project}[W])` | count of key series; archived → "—" |
| Models (chips) | usage input / cached / output × `project_api_key_info` | usage by (model, api_key_id); key → project from the directory | usage legs + directory | union of the project keys' models, ordered by global input tokens; top 3 + "+N"; completions only |
| Cost | `openai_administration_cost_usd` | `project_id`, `project_name`; `F_cost` | this panel's own `getCost` @ to | ⚠ joined to the row by project NAME (first match), not id — a rename or duplicate name mis-joins |
| Limit | `openai_administration_project_spend_limit_usd` (gated by `…_configured`) | `project_id`; `F_project` | `last_over_time(openai_administration_project_spend_limit_usd{F_project}[W])` | read verbatim as USD; no `_configured` series → "—" |
| Limit Enforcing | `openai_administration_project_spend_limit_enforcing` | `project_id`; `F_project` | `last_over_time(openai_administration_project_spend_limit_enforcing{F_project}[W])` | Enforcing / Not enforcing. ⚠ a missing enforcing series reads "Not enforcing", never "—" |
| Limit Configured | `openai_administration_project_spend_limit_configured` | `project_id`; `F_project` | `last_over_time(openai_administration_project_spend_limit_configured{F_project}[W])` | > 0 → Configured, else Not configured; no series → "—" |

## API keys & service accounts (section id `credentials`)

2 `rxResource`s = **22 instant queries**: `getDirectory` (7) + `getUsageCompletions` (15); rows are built once both resolve. Order: project API keys, then admin keys, then service accounts. Read-only: no row click, no drawer. `admin_key_info` is never project-filtered, so admin keys show under a Project chip as "Organization".

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| Name | `openai_administration_project_api_key_info`, `…_admin_key_info`, `…_project_service_account_info` | `name` | `last_over_time(openai_administration_project_api_key_info{F_project}[W])` • `last_over_time(openai_administration_admin_key_info[W])` • `last_over_time(openai_administration_project_service_account_info{F_project}[W])` | ⚠ keys render "<name> ()" — the suffix is always "" |
| Type | — | — | none | chip from the source family: API key / Admin key / Service account |
| Project | `openai_administration_project_info` | `project_id` → name; `F_project` | directory | admin keys → "Organization" |
| Owner | `…_project_api_key_info` (`owner_type`, `owner_email`, `owner_name`), `…_org_user_info`, `…_project_service_account_info` | client-side join | directory | user keys → email; SA keys → `owner_name` in practice; admin keys and SAs → "—" |
| Models | usage input / cached / output | by (model, api_key_id); `F_usage` | usage legs | top 3 + "+N"; an empty cell (not "—") when the key has no usage |
| Tokens | `…_usage_input_tokens` + `…_usage_output_tokens` | per `api_key_id`; `F_usage` | 2 usage legs | cached not added; no usage → "—" |
| Requests | `openai_administration_usage_requests` | per `api_key_id`; `F_usage` | 1 usage leg | also drives Status |
| List cost | usage input / cached / output per (api_key_id, model) | `F_usage` | 3 usage legs, priced client-side | Σ ((input − cached)·rate + cached·cached_rate + output·out_rate) ÷ 1e6 from hard-coded list prices (12 model ids); any unpriced model → "—". An estimate, never billed dollars (Costs API has no `api_key_id` grain) |
| Status | `openai_administration_usage_requests` | per `api_key_id` | usage legs | "Active" only when requests > 0 in the window; Idle / Inactive never appear (last-used is not measured) |

## Platform features

Backed only by `getUsageFamilies` — **11 instant queries**, each a bare `sum(…)` with no grouping. 7 fixed rows; the grid paginates at 6, so File search always sits on page 2. Row click opens the feature-keys drawer with the page filters (no filter added).

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| Row "Embeddings" — Tokens / Model requests | `…_usage_embeddings_tokens`, `…_usage_embeddings_model_requests` | no grouping; `F_usage` | `sum(last_over_time(openai_administration_usage_embeddings_tokens{F_usage}[W]))` • `sum(last_over_time(openai_administration_usage_embeddings_model_requests{F_usage}[W]))` | absent or failed metric prints "0", not "—" |
| Row "Images" | — | — | no query (`images: []` hard-coded) | always "—"; the inventory lists `usage_images` / `usage_images_model_requests` (⚠ unverified) |
| Row "Speech" — Model requests | `…_usage_audio_speeches_model_requests` | no grouping; `F_usage` | `sum(last_over_time(openai_administration_usage_audio_speeches_model_requests{F_usage}[W]))` | Tokens "—"; `…_audio_speeches_characters` is fetched and never rendered |
| Row "Transcriptions" — Model requests | `…_usage_audio_transcriptions_model_requests` | no grouping; `F_usage` | `sum(last_over_time(openai_administration_usage_audio_transcriptions_model_requests{F_usage}[W]))` | Tokens "—"; `…_audio_transcriptions_seconds` is fetched and never rendered |
| Row "Moderations" — Tokens / Model requests | `…_usage_moderations_tokens`, `…_usage_moderations_model_requests` | no grouping; `F_usage` | `sum(last_over_time(openai_administration_usage_moderations_tokens{F_usage}[W]))` • `sum(last_over_time(openai_administration_usage_moderations_model_requests{F_usage}[W]))` | absent prints "0" |
| Row "Web search" — Model requests | `…_usage_web_search_requests` | no grouping; `F_usage` | `sum(last_over_time(openai_administration_usage_web_search_requests{F_usage}[W]))` | 0 → "—"; `…_web_search_model_requests` is fetched and deliberately not shown |
| Row "File search" — Model requests | `…_usage_file_search_requests` | no grouping; `F_usage` | `sum(last_over_time(openai_administration_usage_file_search_requests{F_usage}[W]))` | 0 → "—"; one aggregate row (no per-store grain) |

# Drawers

## API-key drawer — spike mode (Insights "See the keys behind the spike")

Singleton `OpenAiApiKeyDrawerService` (provided in the shell). Re-issues everything: 15 usage legs over `[W_7d]` = `[min(7, N)d]` + the 7 `getDirectory` legs over the full `[W]` = **22 instants** @ to. The summary paragraph is the insight's own sentence (no query). Ranked by tokens, never dollars.

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| Grid rows | usage byApiKey | by (model, api_key_id, date); `F_usage` | 15 × `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_<field>{F_usage}[W_7d]))` | rows = keys with an input / output / requests series in the 7-day window; default rank input + output desc |
| Name | `…_project_api_key_info` | `name`; `F_project` | `last_over_time(openai_administration_project_api_key_info{F_project}[W])` | `apiKeyLabel` (no "()"); falls back to the raw `api_key_id` |
| Owner | `…_project_api_key_info`, `…_org_user_info`, `…_project_service_account_info` | client-side join | directory | row click opens the user drawer on top, only when the owner resolves to a directory user |
| Project | `…_project_info` | `project_id` → name | directory | spike mode only |
| Models | usage input / cached / output | per (api_key_id, model) | usage legs over `[W_7d]` | top 3 + "+N" |
| Input tokens / Output tokens / Requests | `…_usage_input_tokens` / `…_usage_output_tokens` / `…_usage_requests` | Σ per `api_key_id`; `F_usage` | `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_input_tokens{F_usage}[W_7d]))` (same for output_tokens, requests) | the key's total for the spike week |

## API-key drawer — project mode (Projects row click)

`projectId` is replaced by the clicked project: usage selector `{<model chips>, project_id="<clicked>"}`, directory `project_id="<clicked>"`. **22 instants** over the full `[W]`. Rows = every directory key of the project, left-joined to usage (unused keys read 0 / 0 / 0). Read-only.

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| API Keys | `…_project_api_key_info` | `project_id="<clicked>"` | `last_over_time(openai_administration_project_api_key_info{project_id="<clicked>"}[W])` | `apiKeyLabel` (name only) |
| Owner | `…_project_api_key_info`, `…_org_user_info`, `…_project_service_account_info` | client-side join | directory | rows are never clickable in this mode |
| Status | — | — | none | always empty — the verdict needs last-used, which is not measured |
| Models / Input tokens / Output tokens / Requests | usage input / cached / output / requests | by (model, api_key_id, date); `{<model chips>, project_id="<clicked>"}` | `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_input_tokens{<model chips>, project_id="<clicked>"}[W]))` (same for the others) | sort input + output desc; a Model chip narrows usage but not the roster |

## Feature-keys drawer (Platform features row click)

Provided in the Platform features panel. **8 instants** @ to over `[W]` (1 membership + 7 directory); Images sends 7 and always shows "No Data". No usage or cost columns — no plane has a per-feature, per-key measure.

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| API Keys | the family key metric + `…_project_api_key_info` | `count by (api_key_id)`; `F_usage` | `count by (api_key_id) (last_over_time(<K>{F_usage}[W]))` joined to `last_over_time(openai_administration_project_api_key_info{F_project}[W])` | ids sorted lexicographically; unknown id → raw id with Owner "—"; rows read-only |
| Owner | `…_project_api_key_info`, `…_org_user_info`, `…_project_service_account_info` | client-side join | directory | same owner resolution as the API-key drawer |

## User drawer (`user_id` pinned into F_usage)

Only opener on this page: a spike-mode API-key-drawer row whose owner is a directory user. Filters = page filters + `userId`. The 7 `getDirectory` legs + 15 usage legs = **22 instants** over the FULL `[W]` (not the 7-day spike window, and across all the user's keys). The directory ignores `userId`, so Credentials is filtered client-side (owner type user and owner id = userId).

| Widget | Metric | Labels used | Query | Notes |
| --- | --- | --- | --- | --- |
| Header (name, email) | `…_org_user_info` | find `user_id` = userId | `last_over_time(openai_administration_org_user_info[W])` | title blank when the user has no `name` label |
| Grid "Credentials" — Name / Type / Project | `…_project_api_key_info`, `…_project_info` | `F_project` | directory | ⚠ Name renders "<name> ()"; Type is always "API key"; the only surface that shows an error state (reads `directory.unavailable`) |
| Grid "Credentials" — Created / Last used / Status | — | — | none | always "—" / "—" / empty (not measured) |
| KPIs "Requests" / "Input tokens" / "Output tokens" | `…_usage_requests`, `…_usage_input_tokens`, `…_usage_output_tokens` | Σ totals; `F_usage` + `user_id="<id>"` | `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_requests{F_usage, user_id="<id>"}[W]))` (same for input_tokens, output_tokens) | ⚠ read 0 if usage series carry no `user_id` (unverified); a failure also renders 0 (no error branch) |

# Empty state

- No whole-page has-data query. The page renders inside the Code Agents shell, whose outlet shows only after detection marks an agent; the OpenAI probe is `count(max_over_time(openai_administration_usage_input_tokens[10d]))`, run once per shell next to 15 other PromQL probes, fixed `[10d]`, ignoring the picker.
- Error UI is unreachable: every leg is fail-soft, so an outage or a 30 s timeout renders "No data", "$0", "0" or empty tables. Only the user drawer's Credentials reads `directory.unavailable`.
- Not-measured fields render "—": Created, Last used, the key suffix, the org spend-limit amount and the whole Images row; key Status never reaches Idle / Inactive.
- Scalar feature families (Embeddings, Speech, Transcriptions, Moderations) print "0" when the metric is absent or the query failed; only Images, Web search and File search dash.

# Appendix — dead / duplicated queries worth cleaning up

1. **118 instant queries per full page load, 42 unique.** `getUsageCompletions` runs 5× (Usage, Cost, Projects, API keys & service accounts, Insights) = 75, `getCost` 3× (+ the previous window), `getDirectory` 2×, `getSpendLimitsAndAlerts` 2×. The service has no `shareReplay`, and the page provides its own uncached `AiCenterPromqlService` instead of the parent shell's cached `PrometheusQueryService`. Drawers add 22 / 22 / 8 per open.
2. **11 of the 15 usage legs never reach the screen** (`getUsageCompletions`): `input_uncached_tokens`, `input_cache_write_tokens` and `input_cached_{text,audio,image}_tokens` are never read at all; the six text / audio / image splits are copied onto Models rows that have no column for them. That is 55 wasted queries per load.
3. `openai_administration_cost_quantity` is fetched on every `getCost` call and never consumed (4 per load, +1 per Model-dropdown fetch).
4. Platform features fetches `usage_audio_speeches_characters`, `usage_audio_transcriptions_seconds` and `usage_web_search_model_requests` and never renders them.
5. `spend_alert_threshold_usd` (alerts) and `org_invite_info` (invites) are fetched and never read on this page; the drawers also fetch directory families they never read (invites, admin keys, project members).
6. ⚠ Bug — the "Nearing the organization spend limit" insight can never fire: the org `limitUsd` is hard-coded null, and `openai_administration_spend_limit_usd` / `_enforcing` (listed in the metric inventory) are never queried.
7. ⚠ Bug — API key names render "<name> ()" in API keys & service accounts and in the user drawer (`redactedSuffix` is always ""); the shared `apiKeyLabel()` helper is not used there.
8. ⚠ Bug — a Model chip set to a non-model line item (`file search tool calls`, via the dropdown or a Models-row click) zeroes Cost, Usage and Platform features: `line_item=~"(file search tool calls), .*"` cannot match its own value, and `model="file search tool calls"` matches nothing.
9. ⚠ Bug — every grid paginates at 6 rows, so Platform features' 7th row (File search) is always on page 2 while the count badge reads 7.
10. Projects Cost is joined to the row by project NAME, not `project_id` — renamed or same-named projects mis-join.
11. The same key reads "Active" in API keys & service accounts (usage fallback) and shows no Status chip in the project-mode drawer and the user drawer.
12. `getDataRetentionAndCertificates`, `getRateLimitsAndPermissions` and `getRbac` have no caller on API Platform (Compliance page only); `getAuditEvents` has no production caller anywhere.

# Unverified — live checks to run

These claims come from code and the metric inventory only; each needs one PromQL check against a team that runs the OpenAI admin collector.

| Claim | Query |
| --- | --- |
| Usage series carry `user_id` (user drawer KPIs) | `count by (user_id) (last_over_time(openai_administration_usage_requests[14d]))` |
| Images usage is published (Images row + drawer hard-wired empty) | `count(last_over_time(openai_administration_usage_images_model_requests[14d]))` |
| Org spend-limit amount exists (dead org-limit insight) | `count(last_over_time(openai_administration_spend_limit_usd[14d]))` |
| Family metrics carry `model` (Model chip on Platform features) | `count by (model) (last_over_time(openai_administration_usage_web_search_requests[14d]))` |
| Cost `line_item` prefix equals usage `model` (Models join, Model chip) | `count by (line_item) (last_over_time(openai_administration_cost_usd[14d]))` vs `count by (model) (last_over_time(openai_administration_usage_input_tokens[14d]))` |
| Info series carry no `date` label (directory duplicates) | `count by (date) (last_over_time(openai_administration_project_info[14d]))` |