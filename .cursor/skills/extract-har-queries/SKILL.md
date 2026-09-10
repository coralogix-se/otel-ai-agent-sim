---
name: extract-har-queries
description: >-
  Extract PromQL and related metrics queries from Chrome HAR captures of
  Coralogix AI Center / Usage dashboards (Cursor, Claude Products, etc.).
  Use when the user provides a .har file, asks to reverse-engineer dashboard
  queries, document a widget→PromQL contract, debug 422 series-limit errors,
  or build sim emitters from live network traffic.
---

# Extract queries from HAR files

## When to use

- New `docs/*.har` (or any Chrome HAR) of cx498 / cxai-dev / ob Usage pages
- “What does this dashboard query?” / “extract PromQL from the HAR”
- Appendix slides or `docs/*-query-engine-map.md` updates
- Cardinality / 422 investigations (`ViolationTypeTotalSeriesAnalyzed`)

## Workflow

1. **Run the extractor** (do not hand-roll JSON parsing on 100MB+ HARs):

```bash
python3 .cursor/skills/extract-har-queries/scripts/extract_har_queries.py \
  path/to/file.har \
  --format markdown \
  --dedupe promql+status
```

Useful flags:

| Flag | Purpose |
|------|---------|
| `--dedupe promql+status` | Default. Keeps both **200** and **422** for the same PromQL (dual `to`/`from` windows). |
| `--dedupe promql` | One row per PromQL text |
| `--dedupe collapsed` | Merge date=/client_version= dual-window twins |
| `--status 422` | Failures only |
| `--metric cursor_event_tokens_total` | Filter by metric name |
| `--host cx498` | Filter API host |
| `--snippet-422` | Append a **literal** HAR byte snippet around the first series-limit 422 |
| `--format list\|json` | Alternate outputs |
| `-o out.md` | Write file |

2. **Call out the dual-window pattern** when the same PromQL is **200 at page `to`** and **422 at page `from`** (previous window). That is expected after a cardinality fix until old series age out.

3. **For demos / slides**: show a few **literal** HAR lines first (URL-encoded `query=sum%20by%20…`, escaped error JSON)—then the cleaned extracted list. Prefer `--snippet-422` or a short raw cut from the script output; do not pretty-print away the mess.

4. **Map into the sim** only after listing the contract: metric name, labels (`token_type`, `email`, `conversation_id`, …), range, and aggregation. Point at emitters under `sim/` and validate with `cx metrics query` / `query-range` before changing volume knobs.

## Output expectations

Default deliverable is a markdown table:

| Status | Eval / window | Endpoint | PromQL |
|--------|---------------|----------|--------|

Plus a short “Non-2xx samples” section when failures exist.

When appending to docs (e.g. `docs/cursor-usage-dashboard-query-engine-map.md`), use:

1. Source / capture time / page window
2. Literal HAR snippet (trimmed)
3. Extracted query table
4. Optional `cx metrics query … --time …` repro
5. HAR finding → sim change mapping

## Notes

- HARs in this repo are often **>100MB** and may be one giant line—always use the script (byte scan), not `json.load` of the whole file.
- Targets Coralogix `/metrics/api/v1/query` and `/query_range` URLs. Other XHRs (entities, Dataprime) are out of scope unless the user asks.
- Script path is relative to the **otel-ai-agent-sim** repo root.
