# Cursor Usage v2 (`cursor_*` Prometheus family)

New emit path for the Cursor Usage dashboard. Sibling of the **legacy** Composer
OTLP span simulator (`sim/cursor/agent.py`).

- Spec / PromQL map: `docs/cursor-usage-dashboard-query-engine-map.md`
- Dual-path plan + gap analysis: `docs/cursor-usage-v2-plan.md`
- Real labels reference: cxai-dev (`user-cxaidev-coralogix-server`)

## Gate

`SIM_CURSOR_USAGE_METRICS_ENABLED` (default `false`). When enabled, `app.py`
registers `CursorUsageCollector` and calls `emit_cursor_usage_metrics_cycle()`
each main-loop iteration. Legacy Composer spans are unchanged.

## Semantics

Every sample is a **per-bucket delta** (cleared on scrape) or a **snapshot**
(restated for `last_over_time`). Dashboard PromQL uses only `sum_over_time` /
`max_over_time` / `last_over_time` — never `rate` / `increase`.

## Optional knobs

| Env | Default | Purpose |
|-----|---------|---------|
| `SIM_CURSOR_USAGE_TEAM_ID` | `3405693` | Match cxai team label |
| `SIM_CURSOR_USAGE_CX_APPLICATION_NAME` | `Cursor` | `cx_application_name` |
| `SIM_CURSOR_USAGE_CX_SUBSYSTEM_NAME` | `Admin APIs` | `cx_subsystem_name` |
| `SIM_CURSOR_USAGE_ROSTER_SIZE` | `48` | Members (4–64); prefer growing users over series |
| `SIM_CURSOR_USAGE_EMITS_PER_CYCLE` | `2` | Max events emitted per main-loop tick |
| `SIM_CURSOR_USAGE_CONVERSATIONS_PER_DAY` | `400` | New `conversation_id` budget per UTC day (~300–500) |
| `SIM_CURSOR_USAGE_EVENTS_PER_CONV_MIN` | `20` | Min events reusing one conversation |
| `SIM_CURSOR_USAGE_EVENTS_PER_CONV_MAX` | `40` | Max events reusing one conversation |
| `SIM_CURSOR_USAGE_EVENTS_PER_USER_DAY` | `350` | Target events per active user / day (~200–500) |
| `SIM_CURSOR_USAGE_EVENTS_PER_USER_DAY_MAX` | `500` | Hard cap per user / day |
| `SIM_CURSOR_USAGE_VOLUME` | `1.0` | Scale factor for counts/cost |

### Cardinality note

`conversation_id` is the only high-cardinality event label. The sim **reuses** each
id across 20–40 events and caps new conversations to ~400/day so a 7-day
`sum_over_time(cursor_event_tokens_total[…])` stays under the metrics series limit
(~300k). Do not add other per-event unique labels.

## Topic Mix

`cursor_conversation_subcategory_snapshot` is a **delta** (despite the name) so Topic Mix
can use `sum by (subcategory)(sum_over_time(…[W]))`. Emitted once per **new**
conversation for Ask / Plan / Write Code intents:

| Intent | `mode` | `subcategory` values |
|--------|--------|----------------------|
| Ask | `askMode` | `error_fix`, `explanation` |
| Plan | `planMode` | `implementation` |
| Write Code | `writeCode` | `feature`, `refactor` |

`email` is the conversation member (User filter works). Task Automation skips this metric.
