# Dashboard regression tests

Live checks that AI Center **dashboard queries still return data**, grouped by sim.

These catch product/tenant drift such as Session Analyze switching from
`source ai_sessions_claude` to `source ai.sessions.claude` while TCO still
routes prompts into the old dataset (see `docs/nomessages*.har`).

## Layout

| Path | Purpose |
|------|---------|
| `catalogs/<sim>.yaml` | Dashboard queries + assertions per sim |
| `test_dashboard_queries.py` | pytest runner (`catalog` + `live`) |
| `cx_client.py` | `cx dataprime` / `cx metrics` wrapper |

Sims covered: **claude**, **copilot**, **gemini**, **codex**, **cursor**.

## Run

```bash
python3 -m pip install -r requirements-dev.txt

# Catalog schema only (no Coralogix)
bash scripts/run-dashboard-regression.sh --catalog-only

# Live against cx default profile (obdev)
bash scripts/run-dashboard-regression.sh

# One sim
bash scripts/run-dashboard-regression.sh --sim claude

# Explicit profile
CX_PROFILE=default bash scripts/run-dashboard-regression.sh --sim claude --sim copilot
```

Pass criteria for each check: query succeeds and returns rows (DataPrime) or a
non-zero PromQL sample. A missing DataPrime source fails hard (the Aug 2026
Claude “No messages” case).

## Adding a check

Edit `catalogs/<sim>.yaml`:

```yaml
- id: claude.example
  title: Short human title
  kind: dataprime   # or promql
  expect: has_rows  # has_rows | has_nonzero | source_exists
  window: 24h       # dataprime only → now-<window>
  tier: frequent    # optional: frequent | archive
  require_tags:     # optional: assert keys on returned span/log JSON
    - gen_ai.conversation.id
  query: |
    source spans
    | filter $l.applicationName == 'copilot-cli'
    | limit 5
```

`require_tags` inspects `userData.tags` / `attributes` client-side. Use it when
DataPrime `tags['…']` filters are unreliable on the tenant but raw rows still
carry the fields the dashboard needs.
