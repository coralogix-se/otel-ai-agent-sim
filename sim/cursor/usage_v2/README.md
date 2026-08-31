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
| `SIM_CURSOR_USAGE_ROSTER_SIZE` | `24` | Members (4–64) |
| `SIM_CURSOR_USAGE_EMITS_PER_CYCLE` | `6` | Activity bursts per loop |
| `SIM_CURSOR_USAGE_VOLUME` | `1.0` | Scale factor for counts/cost |
