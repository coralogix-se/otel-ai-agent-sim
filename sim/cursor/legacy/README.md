# Cursor legacy (span / hook) telemetry — backup

Snapshot of the **pre–Usage-dashboard** Cursor simulator:

| Signal | Shape |
|--------|--------|
| Traces | OTLP spans, `service.name=cursor-agent`, library `cursor-coralogix` |
| Attributes | Flat `cursor.*` + `gen_ai.*` (Composer sessions) |
| Metrics | None of the Admin/Usage `cursor_*` PromQL family |

**Live entrypoint (unchanged):** `sim.cursor.agent.emit_cursor_composer_session`  
**This directory:** byte-copy of that module as of the Usage-v2 kickoff, for rollback / side-by-side.

Regression catalog copy: `docs/cursor-legacy/cursor-spans-catalog.yaml`  
Overview DataPrime queries: `docs/cursor-legacy/overview-dev-dashboard-queries.txt`
