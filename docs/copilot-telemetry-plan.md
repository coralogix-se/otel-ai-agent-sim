# Copilot CLI telemetry alignment — tracked plan

Align sim Copilot telemetry with `docs/copilotrequirements.txt`, `docs/copilot-sim-data.json`, and cx498 dashboard queries extracted from HAR files. Validate on **obdev** after deploy.

## Sources

| Source | Path | Use |
|--------|------|-----|
| Requirements | [copilotrequirements.txt](./copilotrequirements.txt) | Span vs collector paths, join keys |
| Target shapes | [copilot-sim-data.json](./copilot-sim-data.json) | Example tags per span type |
| cxai-demo queries | [cxai-demo-copilot-queries.txt](./cxai-demo-copilot-queries.txt) | EU2-style DataPrime panels |
| Overview queries | [overview-dev-dashboard-queries.txt](./overview-dev-dashboard-queries.txt) | Code Agents overview (widgets 11, 14, 17) |
| cx498 Copilot dashboard HAR | `copilot-dev.app.cx498.coralogix.com.har` | Repo + session message queries (`chunk-MVEV33BH.js`, `chunk-X6M6SIGN.js`) |
| cx498 Copilot dashboard HAR (obdev) | `.logs/copiloteonlineboutique-dev.app.cx498.coralogix.com.har` | Main dashboard queries (`chunk-DPVBNZ5E.js`) + collector PromQL |

**HAR refresh:** After UI changes, capture a new HAR from `copilot-dev.app.cx498.coralogix.com` (Copilot CLI page, FF ON). Re-run the extraction script below and update query sections if chunk hashes change.

```bash
# Re-extract DataPrime query keys from obdev/cx498 HAR (adjust path)
python3 scripts/extract_copilot_har_queries.py .logs/copiloteonlineboutique-dev.app.cx498.coralogix.com.har
```

---

## Implementation phases

### Phase 0 — Prep (no code)

- [ ] **0.1** Confirm obdev deploy target: sim in `codeagentsim` → obdev tenant; filter sim with `job=otel-ai-agent-sim` (exclude AcmeCorp `job=claude-code` noise).
- [ ] **0.2** Set k8s env for cx498 shape before deploy:
  - `COPILOT_CX_APPLICATION_NAME=copilot-cli`
  - `COPILOT_CX_SUBSYSTEM_NAME=copilot-sessions`
  - `SIM_COPILOT_ENDUSER_PSEUDO_OPAQUE=true` (until code default flips)
- [ ] **0.3** Optional: add `scripts/extract_copilot_har_queries.py` + commit refreshed query extract under `docs/` (not required for implementation).

### Phase 1 — P0 span fixes (dashboard blockers)

**Files:** `sim/claude/repos.py`, `sim/copilot/cli.py`, `k8s/codeagentsim/sim-deployment.yaml`

- [ ] **1.1** `github.copilot.git.repository` → `org/repo` string (not `https://github.com/...git`).
- [ ] **1.2** Cache key on `invoke_agent` (+ logs): `gen_ai.usage.cache_read_input_tokens` (remove/w stop emitting `gen_ai.usage.cache_read.input_tokens` on spans).
- [ ] **1.3** Default cx labels: `copilot-cli` / `copilot-sessions` (env + code defaults).
- [ ] **1.4** `user.email` **only** on Resource / `process.tags`; remove from span `tags`.
- [ ] **1.5** `enduser.pseudo.id`: 32-char hex on **`invoke_agent` only**; omit on `chat` / `execute_tool`.
- [ ] **1.6** Git attrs (`github.copilot.git.*`, `github.copilot.github.org`) on **`invoke_agent` only**.

### Phase 2 — P1 span fidelity

**Files:** `sim/copilot/cli.py`, `sim/common/constants.py` (Copilot model pool)

- [ ] **2.1** Copilot model ids: dot form (`claude-haiku-4.5`, `gpt-5-mini`) not hyphen API ids.
- [ ] **2.2** `span.kind`: `client` on `invoke_agent` + `chat`, `internal` on `execute_tool`.
- [ ] **2.3** Provider/agent: `gen_ai.provider.name=github`, `gen_ai.agent.id=github.copilot.default`; drop `gen_ai.agent.name=copilotcli`, `gen_ai.system=azure.openai` from spans.
- [ ] **2.4** `invoke_agent` rollups: `github.copilot.turn_count`, `gen_ai.usage.cache_creation_input_tokens`, `gen_ai.usage.reasoning_output_tokens`, `gen_ai.response.finish_reasons` (JSON array string).
- [ ] **2.5** Messages on **`invoke_agent`**: `gen_ai.input.messages` / `gen_ai.output.messages` (GenAI JSON with `parts[].type` = `text` | `reasoning`). Required for `sessionsWithMessages` / `sessionMessages` (see validation §B).
- [ ] **2.6** `chat` spans: `gen_ai.response.model`, `github.copilot.server_duration`, `turn_id`, `interaction_id`, `service_request_id`, `gen_ai.response.id`, per-call cost/nano_aiu; no pseudo id / git tags.
- [ ] **2.7** Tools: real names (`view`, `grep`, `bash`) + `gen_ai.tool.call.id`, arguments, result, description on `execute_tool`.
- [ ] **2.8** Cost realism: sometimes `github.copilot.cost` null on invoke (match real CLI); keep `github.copilot.nano_aiu` populated.

### Phase 3 — Collector metrics (FF ON)

**Files:** `sim/copilot/collector_metrics.py`, `sim/common/identity.py`

- [ ] **3.1** Billing SKU labels: `copilot_enterprise`, `copilot_business` (not `copilot_premium_request`, etc.).
- [ ] **3.2** CLI session feature slug: `copilot_cli` on `*_by_model_feature` / `*_by_feature` for CLI-driven increments.
- [ ] **3.3** Language labels: `TypeScript`, `Python`, … (match docs casing).
- [ ] **3.4** User metrics: ~20–30% of users with `user_email=""`, login+name only (PromQL `label_join` fallback path).
- [ ] **3.5** Review daily-gauge vs per-session increment semantics (document if sim stays counter-based).

### Phase 4 — Deploy & soak

- [ ] **4.1** Commit on feature branch; build + deploy to obdev (`KUBECTL_CONTEXT=… bash scripts/redeploy.sh`).
- [ ] **4.2** Verify pod pools + sample span locally in pod (`python3 -c "from sim.copilot.cli import …"` / constants).
- [ ] **4.3** Soak **≥2 hours** (or ≥500 `invoke_agent` spans) before validation.

---

## Validation on obdev

**Tenant MCP:** `user-obdev-coralogix-server`  
**Sim filter (append to every query):**

```dataprime
| filter $d.resource.attributes['job']:string == 'otel-ai-agent-sim'
```

**Time window:** last 24h after soak (adjust `start_date` / `end_date`).

### A. Common base filter (all cx498 span queries)

From HAR `chunk-X6M6SIGN.js`:

```dataprime
source spans
| filter $l.serviceName == 'github-copilot' || tags['otel.scope.name'] == 'github.copilot'
```

**Pass criteria:** `hasData` count > 0; `availableAppSubsystems` shows `applicationName=copilot-cli`, `subsystemName=copilot-sessions`.

| ID | Query name (HAR) | Pass criteria |
|----|------------------|---------------|
| A1 | `hasData` | count ≥ 1 |
| A2 | `availableAppSubsystems` | includes `copilot-cli` / `copilot-sessions` |

### B. cx498 Copilot CLI dashboard — DataPrime (`chunk-DPVBNZ5E.js`)

All below use base filter + **invoke_agent** suffix unless noted:

`| filter tags['gen_ai.operation.name'] == 'invoke_agent'`

| ID | Query | Fields / behavior required | Sim gap today |
|----|-------|---------------------------|---------------|
| B1 | `totalSessions` | `gen_ai.conversation.id` | OK |
| B2 | `uniqueUsers` | `enduser.pseudo.id` (hex, invoke only) | email default; on child spans |
| B3 | `totalTokens` | input/output on invoke | OK |
| B4 | `totalCost` | sum `github.copilot.cost` | OK (may need null cases) |
| B5 | `costByUser` | pseudo id + cost | same as B2 |
| B6 | `costByModel` | `gen_ai.request.model` on invoke | model id format |
| B7 | `costOverTime` | cost + timestamp | OK |
| B8 | `cacheTokens` | **`gen_ai.usage.cache_read.input_tokens`** OR fallback `cache_read_input_tokens` | **wrong key emitted** |
| B9 | `sessionsByUser` | pseudo id + conversation id | B2 |
| B10 | `tokensByUser` | pseudo id + usage | B2 |
| B11 | `userDetails` | cost, tokens, cacheRead, duration | cache key + cacheCreation=0 in UI |
| B12 | `userModelBreakdown` | model + cost on invoke | model format |
| B13 | `tokensOverTime` | tokens by time | OK |
| B14 | `totalChatDurationMs` | **`chat` spans**, `$m.duration` | OK if chat spans exist |
| B15 | `modelsByTokens` | model + tokens on invoke | model format |
| B16 | `topUsedTools` | **`execute_tool`**, `gen_ai.tool.name` | wrong tool names |

**Repo panel** (`chunk-MVEV33BH.js` — `sessionRepoUserInfo`):

```dataprime
| filter tags['gen_ai.operation.name'] == 'invoke_agent'
| create sessionId from tags['gen_ai.conversation.id']
| create repo from tags['github.copilot.git.repository']
| create org from tags['github.copilot.github.org']
| create user from $d.process.tags['user.email']
| filter sessionId != null
| groupby sessionId, repo, org, user aggregate count() as turns
```

| ID | Pass criteria | Sim gap |
|----|---------------|---------|
| B17 | `repo` like `coralogix/*`, not `https://` | **URL format** |
| B18 | `user` from process.tags, not span tags | user on span tags |
| B19 | Multiple rows per sessionId when multi-repo | partial (format wrong) |

**Session messages** (`sessionMessages`, `sessionsWithMessages`):

```dataprime
| create sid from tags['gen_ai.conversation.id']
| create input from tags['gen_ai.input.messages']
| create output from tags['gen_ai.output.messages']
```

| ID | Pass criteria | Sim gap |
|----|---------------|---------|
| B20 | `sessionsWithMessages` returns sessionIds with turns > 0 | messages on **chat only**, not invoke |
| B21 | `sessionMessages` returns input/output JSON for a trace | same |

**AIU cost path** (HAR `ve` template — some panels):

```dataprime
| create cost from tags['github.copilot.nano_aiu']:number / 1000000000
```

| ID | Pass criteria |
|----|---------------|
| B22 | nano_aiu present on invoke; AIU > 0 when summed |

### C. Code Agents overview (`overview-dev-dashboard-queries.txt`)

| ID | Widget | Pass criteria |
|----|--------|---------------|
| C1 | Active users (11) | pseudo id + conversation id → sessions per user |
| C2 | Usage by model (14) | distinct conversations per `gen_ai.request.model` |
| C3 | Sessions over time (17) | daily distinct `gen_ai.conversation.id` |

### D. cxai-demo panels (`cxai-demo-copilot-queries.txt`)

Run all 9 pipelines (presence, app×subsystem, tokens, chat duration, sessions, users, models, cost, cache). Update cache query in that file after Phase 1 to prefer `cache_read_input_tokens`.

### E. Collector PromQL (FF ON — `SIM_COPILOT_COLLECTOR_METRICS=true`)

From HAR `chunk-DPVBNZ5E.js` (41 metric names). Sample checks on obdev:

```promql
github_copilot_org_cli_session_count{organization="coralogix"}
github_copilot_org_user_initiated_interaction_count_by_model_feature{feature="copilot_cli"}
github_copilot_billing_net_amount{sku="copilot_enterprise"}
github_copilot_user_cli_session_count{user_email=~".+@coralogix.com"}
github_copilot_user_cli_session_count{user_email=""}
```

| ID | Pass criteria | Sim gap |
|----|---------------|---------|
| E1 | Org CLI session count > 0 over 24h | OK if collector enabled |
| E2 | `feature="copilot_cli"` series exists | uses `agent`, `code_completion`, … |
| E3 | Billing sku `copilot_enterprise` | wrong sku names |
| E4 | User series with empty `user_email` | always set email |
| E5 | FF-ON users table: span `process.tags.user.email` matches collector `user_email` | join key |

### F. Regression — fields that must NOT appear

| Check | Expected |
|-------|----------|
| F1 | `gen_ai.response.model` null on `invoke_agent` |
| F2 | `enduser.pseudo.id` null on `chat` / `execute_tool` |
| F3 | `github.copilot.git.repository` null on child spans |
| F4 | No sim traffic with `applicationName=github-copilot` after Phase 1 |

---

## Validation workflow (checklist)

1. [ ] Deploy Phase 1+2 (+3 if FF ON) to obdev.
2. [ ] Wait for soak (Phase 4.3).
3. [ ] Run **A1–A2** — base presence and labels.
4. [ ] Run **B1–B22** — Copilot CLI dashboard queries via MCP `query_dataprime` on `user-obdev-coralogix-server`.
5. [ ] Run **C1–C3** — overview widgets.
6. [ ] Run **D** — cxai-demo query file (optional if obdev ≠ eu2).
7. [ ] Run **E1–E5** — PromQL via MCP `query_metrics_range`.
8. [ ] Run **F1–F4** — negative checks.
9. [ ] Manual UI smoke: Copilot CLI page on cx498 obdev — repo table, session list, model breakdown, top tools, users table (FF ON).
10. [ ] Record results in table below; file issues for any FAIL.

### Results log (fill after obdev validation)

| ID | Status | Notes | Date |
|----|--------|-------|------|
| A1 | | | |
| A2 | | | |
| B1–B22 | | | |
| C1–C3 | | | |
| E1–E5 | | | |
| F1–F4 | | | |
| UI smoke | | | |

---

## HAR query reference (extracted)

### Base filters (`chunk-X6M6SIGN.js`)

```
source spans
| filter $l.serviceName == 'github-copilot' || tags['otel.scope.name'] == 'github.copilot'

| filter tags['gen_ai.operation.name'] == 'invoke_agent'   # invoke
| filter tags['gen_ai.operation.name'] == 'chat'             # chat

$d.process.tags['user.email']                               # user join
tags['github.copilot.nano_aiu']:number / 1000000000        # AIU cost
```

### Main dashboard queries (`chunk-DPVBNZ5E.js` — 19 keys)

`hasData`, `availableAppSubsystems`, `totalCost`, `costByUser`, `costByModel`, `costOverTime`, `cacheTokens`, `totalSessions`, `uniqueUsers`, `sessionsOverTime`, `sessionsByUser`, `tokensByUser`, `userDetails`, `userModelBreakdown`, `totalTokens`, `tokensOverTime`, `totalChatDurationMs`, `modelsByTokens`, `topUsedTools`

Full pipeline text: see [cxai-demo-copilot-queries.txt](./cxai-demo-copilot-queries.txt) (overlap) or re-extract from obdev HAR.

### Repo + messages (`copilot-dev.app.cx498.coralogix.com.har`)

`sessionRepoUserInfo`, `tokensBySession`, `tokensByRepo`, `modelsBySession`, `sessionMeta`, `sessionMessages`, `sessionsWithMessages`

---

## Out of scope (for now)

- Replacing `copilot_cli_session_repo_info` Prometheus gauge with span-only repo panel (gauge uses different label schema).
- Matching every MCP/skills/custom_agent_names value literally (simulate plausible JSON arrays only).
- EU2 cxai-demo tenant validation (obdev cx498 is primary).

---

## Owners & branch

- **Branch:** continue on `feature/copilot-cx498-repo-spans` (or split PR: spans P0/P1 vs collector P3).
- **Deploy target:** EKS → obdev Coralogix tenant.
- **Do not implement** in this doc — implementation tracked by checkboxes above.
