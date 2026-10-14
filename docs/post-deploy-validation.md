# Post-deploy validation (reusable)

Run this checklist after **every major sim update** (image push + rollout to `codeagentsim` on `coralogixDemo` → **obdev** tenant).

**Related docs:** [copilot-telemetry-plan.md](./copilot-telemetry-plan.md) (Copilot phase detail), [har-mcp-run-summary.json](./har-mcp-run-summary.json) (metric naming gotchas).

---

## Deploy record (fill in each run)

| Field | Value |
|-------|-------|
| Date (UTC) | |
| Git commit | |
| Image digest | |
| Branch | |
| Deployer | |
| Soak start (UTC) | |
| Validation start (UTC) | |
| MCP tenant | `user-ob-coralogix-server` (obdev) |
| K8s context | `arn:aws:eks:us-west-2:827602716714:cluster/coralogixDemo` |
| Namespace | `codeagentsim` |

**Scope of this deploy** (check what changed — skip unrelated tiers):

- [ ] Claude Code (metrics / logs / repos)
- [ ] Copilot CLI (spans / collector metrics)
- [ ] Gemini / Codex / Cursor
- [ ] K8s env only (no code)
- [ ] Other: _______________

---

## Global filters (use on every tenant query)

Sim traffic is isolated from real CLI exports with these filters.

**Prometheus** — append to every PromQL query:

```promql
{job="otel-ai-agent-sim"}
```

**DataPrime spans** — append after `source spans`:

```dataprime
| filter $d.resource.attributes['job']:string == 'otel-ai-agent-sim'
```

**DataPrime logs** — prefer application/subsystem over legacy HAR `service_name` paths (see [har-mcp-run-summary.json](./har-mcp-run-summary.json)).

**Time window:**

| Tier | Window | Notes |
|------|--------|-------|
| Quick smoke | `[15m]` or `[30m]` | Right after rollout + 5 min warmup |
| Standard | `[1h]` | Default for dashboard joins |
| Full soak | from `deploy_start` | Use after Tier 1 gate (≥30–120 min) |

**Metric name reminder:** Coralogix may expose Claude token counters as `claude_code_token_usage__token__total` (double underscore), not HAR `claude_code_token_usage_tokens_total`. Search metrics catalog before failing a query.

---

## Tier 0 — Immediate (0–10 min after rollout)

**Goal:** Pod healthy, code imports, local shape tests pass.

### 0.1 Cluster rollout

```bash
KUBECTL_CONTEXT=arn:aws:eks:us-west-2:827602716714:cluster/coralogixDemo \
  K8S_NAMESPACE=codeagentsim \
  bash scripts/verify-rollout.sh
```

| ID | Pass | Notes |
|----|------|-------|
| 0.1a | Pod `Running`, `ready=true`, restarts stable after warmup | |
| 0.1b | Log line `AI Agent Simulation started` | |
| 0.1c | `:9090/metrics` contains `claude_code_cost_usage_USD` and `claude_code_session_count` | |
| 0.1d | Image digest matches expected ECR push | |

### 0.2 Local shape validation (no tenant required)

```bash
.venv/bin/python scripts/validate_claude_dmbsm_shapes.py
bash scripts/post-deploy-validate-local.sh
bash scripts/run-dashboard-regression.sh --catalog-only
```

| ID | Pass | Notes |
|----|------|-------|
| 0.2a | `validate_claude_dmbsm_shapes.py` exits 0 | flat/dotted logs, api_response_body, repo names |
| 0.2b | `post-deploy-validate-local.sh` exits 0 | imports, prompt/reply pairs, no `unknown` repos |
| 0.2c | Dashboard regression catalogs load | `tests/dashboard_regression/catalogs/*.yaml` |

### 0.3 Record soak start

Note `deploy_start` UTC — Tier 1+ queries should use **`start_date >= deploy_start`** so pre-deploy traffic does not pollute results.

---

## Tier 1 — Soak gate

**Goal:** Enough fresh sim volume before dashboard validation.

| ID | Gate | Pass |
|----|------|------|
| 1.1 | Wall time ≥ **30 min** since rollout (quick) or ≥ **120 min** (full Copilot checklist) | |
| 1.2 | Copilot: ≥ **100** `invoke_agent` spans with sim filter in window | |
| 1.3 | Claude: `increase(claude_code_cost_usage_USD_total{job="otel-ai-agent-sim"}[1h])` > 0 | |
| 1.4 | Claude: `claude_code_session_repo_info` series present in metrics catalog | |

Optional timer script:

```bash
DEPLOY_START=2026-06-08T12:00:00Z SOAK_MINUTES=30 bash scripts/phase4-validate-after-soak.sh
```

---

## Tier 2 — Claude Code

**Goal:** Metrics, logs, repo join, session messages, subsystem routing.

### 2.1 Prometheus — counters & cost

```promql
sum(increase(claude_code_cost_usage_USD_total{job="otel-ai-agent-sim"}[1h]))
```

```promql
sum by (cx_subsystem_name) (increase(claude_code_cost_usage_USD_total{job="otel-ai-agent-sim"}[1h]))
```

| ID | Pass criteria | Notes |
|----|---------------|-------|
| 2.1a | Total cost increase > 0 in window | |
| 2.1b | Cost split across `claude-code` **and** `claude-code-sessions` (~50/50 when `SIM_CLAUDE_TELEMETRY_PROFILE=both`) | |
| 2.1c | **No** session_id appears with cost on **both** subsystems (dedupe check below) | Per-session routing |

**Dedupe check** (same session must not exist on both subsystems):

```promql
count by (session_id) (
  count by (session_id, cx_subsystem_name) (
    max_over_time(claude_code_cost_usage_USD_total{job="otel-ai-agent-sim"}[1h])
  )
) > 1
```

| ID | Pass |
|----|------|
| 2.1d | Query returns **empty** (no session_id counted on 2+ subsystems) | |

### 2.2 Prometheus — user ↔ repo join (AI Center)

Engineer join pattern (adjust `${range}`):

```promql
group by (user_email, repository_name) (
  max by (session_id, repository_name) (
    max_over_time(claude_code_session_repo_info{job="otel-ai-agent-sim"}[1h])
  )
  * on(session_id) group_left(user_email)
  max by (session_id, user_email) (
    increase(claude_code_cost_usage_USD_total{job="otel-ai-agent-sim"}[1h])
  )
)
```

| ID | Pass criteria | Notes |
|----|---------------|-------|
| 2.2a | Join returns rows with non-empty `repository_name` | |
| 2.2b | **No** `repository_name="unknown"` in window | Removed in sim — legacy may linger until aged out |
| 2.2c | Managed repo `coralogix/cxai-observability-demo-playground` appears in results | Default `SIM_CLAUDE_ORG_REPOS` |
| 2.2d | Unmanaged fictional repos appear (e.g. `jchen/dotfiles`) at low rate (~10%) | |
| 2.2e | Users with cost have ≥1 repo row (no orphan cost-only users for **sim** `user_email`) | Real non-sim users won't have repo gauge |

**Repo distribution:**

```promql
sum by (repository_name) (
  max_over_time(claude_code_session_repo_info{job="otel-ai-agent-sim"}[1h])
)
```

### 2.3 Logs — prompt & response alignment

Flat subsystem:

```dataprime
source logs
| filter $l.applicationname == 'claude-code'
| filter $l.subsystemname == 'claude-code'
| filter $m.text ~ 'user_prompt|api_response_body'
| limit 50
```

| ID | Pass criteria | Notes |
|----|---------------|-------|
| 2.3a | `user_prompt` events include full `prompt` text (not length only) | |
| 2.3b | Same `session.id` has `api_response_body` whose JSON `content[].text` matches prompt theme | Semantic pair from same turn |
| 2.3c | Dotted pipeline: rows in `claude-code-sessions` with `event.name` | ~50% of sessions |

### 2.4 Personal-repo violator (if enabled)

| User | Agent | Expected repo pattern |
|------|-------|---------------------|
| `quinn.bernstein@coralogix.com` | Claude Code | `<github-user>/Coralogix-log-explore` (personal) |
| `quinn.bernstein2@coralogix.com` | Copilot CLI | same pattern |

```promql
max by (user_email, repository_name) (
  max_over_time(claude_code_session_repo_info{job="otel-ai-agent-sim", user_email="quinn.bernstein@coralogix.com"}[24h])
)
```

| ID | Pass |
|----|------|
| 2.4a | Violator user shows personal repo, not `coralogix/*` | |

---

## Tier 3 — Copilot CLI

**Goal:** cx498 / obdev Copilot dashboard queries. Full query text: [copilot-telemetry-plan.md § Validation](./copilot-telemetry-plan.md#validation-on-obdev).

**Base span filter:**

```dataprime
source spans
| filter $l.serviceName == 'github-copilot' || tags['otel.scope.name'] == 'github.copilot'
| filter $d.resource.attributes['job']:string == 'otel-ai-agent-sim'
```

### 3.1 Presence & labels

| ID | Check | Pass |
|----|-------|------|
| 3.1a | `hasData` count > 0 | |
| 3.1b | Subsystems include `copilot-cli` / `copilot-sessions` | |
| 3.1c | `invoke_agent` spans have `gen_ai.conversation.id` | |

### 3.2 Repo panel (`sessionRepoUserInfo`)

| ID | Pass criteria |
|----|---------------|
| 3.2a | `github.copilot.git.repository` is `org/repo` (no `https://`) |
| 3.2b | Managed repo `coralogix/cxai-observability-demo-playground` present |
| 3.2c | `user.email` from `$d.process.tags`, not span tags |
| 3.2d | Git attrs on `invoke_agent` only (not on `chat` / `execute_tool`) |

### 3.3 Session messages

| ID | Pass criteria |
|----|---------------|
| 3.3a | `gen_ai.input.messages` / `gen_ai.output.messages` on **`invoke_agent`** |
| 3.3b | Prompt and reply text semantically aligned (same conversation turn) |
| 3.3c | `sessionsWithMessages` / `sessionMessages` queries return data |

Spot-check DataPrime:

```dataprime
source spans
| filter tags['gen_ai.operation.name'] == 'invoke_agent'
| filter $d.resource.attributes['job']:string == 'otel-ai-agent-sim'
| create sid from tags['gen_ai.conversation.id']
| create input from tags['gen_ai.input.messages']
| create output from tags['gen_ai.output.messages']
| filter input != null && output != null
| limit 20
```

### 3.4 Span fidelity (sample)

| ID | Field | Pass |
|----|-------|------|
| 3.4a | `gen_ai.usage.cache_read_input_tokens` on invoke (not dotted legacy key) | |
| 3.4b | `enduser.pseudo.id` 32-char hex on invoke only | |
| 3.4c | `gen_ai.response.model` on `chat`, null on `invoke_agent` | |
| 3.4d | Tool names: `view`, `grep`, `bash`, … on `execute_tool` | |

### 3.5 Collector metrics (`SIM_COPILOT_COLLECTOR_METRICS=true`)

```promql
github_copilot_org_cli_session_count{organization="coralogix"}
github_copilot_org_user_initiated_interaction_count_by_model_feature{feature="copilot_cli"}
github_copilot_billing_net_amount{sku="copilot_enterprise"}
github_copilot_user_cli_session_count{user_email=""}
```

| ID | Pass |
|----|------|
| 3.5a | Org CLI session count > 0 over 24h | |
| 3.5b | `feature="copilot_cli"` series exists | |
| 3.5c | Some users with empty `user_email` (~20–30%) | |

---

## Tier 4 — Other CLI agents (smoke)

Skip if deploy did not touch these agents.

| Agent | Quick check | Pass |
|-------|-------------|------|
| Gemini | Spans: `$l.applicationname == 'gemini-cli'`, ops `user_prompt` + `llm_call` | |
| Codex | Spans: `$l.applicationname == 'codex'`, `run_turn` → `user_prompt` | |
| Cursor | Spans with `agent.product=cursor`, composer session ids | |

Local reference: `bash scripts/validate_refactored_cli_agents.sh` (requires local collector + `CORALOGIX_PRIVATE_KEY`).

---

## Tier 5 — UI smoke (manual)

| Page | Pass |
|------|------|
| cx498 obdev — Copilot CLI dashboard | Repo table, sessions, model breakdown, top tools |
| AI Center — Claude Code | User cost ↔ repo join, managed/unmanaged split |
| Session message / AI Analysis | Prompt + reply readable and aligned |

### Automated dashboard query regression (preferred)

Runs the catalogued AI Center queries per sim via `cx` and asserts each returns data. This is what catches Session Analyze source renames (`ai.sessions.claude` vs `ai_sessions_claude`).

```bash
# All sims (default cx profile = obdev)
bash scripts/run-dashboard-regression.sh

# Claude only
bash scripts/run-dashboard-regression.sh --sim claude
```

| ID | Pass |
|----|------|
| 5.a | All selected live checks green (see `tests/dashboard_regression/README.md`) |

---

## Tier 6 — Regression (must NOT appear on sim traffic)

| ID | Check | Expected |
|----|-------|----------|
| 6.1 | `repository_name="unknown"` on `claude_code_session_repo_info` | Absent in post-deploy window |
| 6.2 | Same `session_id` on both `claude-code` and `claude-code-sessions` for cost/repo | Absent |
| 6.3 | `enduser.pseudo.id` on Copilot `chat` / `execute_tool` | Null / omitted |
| 6.4 | `github.copilot.git.repository` URL format | Absent |
| 6.5 | Sim spans with wrong `applicationName` (e.g. legacy `github-copilot` resource routing) | Absent after label fixes |

---

## Results log (copy per deploy)

| Tier | ID | Status | Notes | Validator |
|------|-----|--------|-------|-----------|
| 0 | 0.1a–0.2b | | | |
| 1 | 1.1–1.4 | | | |
| 2 | 2.1a–2.4a | | | |
| 3 | 3.1a–3.5c | | | |
| 4 | smoke | | | |
| 5 | UI | | | |
| 6 | 6.1–6.5 | | | |

**Overall:** PASS / FAIL / PASS WITH WARNINGS

**Follow-ups:**

- 

---

## Quick command reference

```bash
# Deploy
KUBECTL_CONTEXT=arn:aws:eks:us-west-2:827602716714:cluster/coralogixDemo \
  K8S_NAMESPACE=codeagentsim \
  bash scripts/redeploy.sh

# Tier 0 local + pod
bash scripts/post-deploy-validate-local.sh
KUBECTL_CONTEXT=arn:aws:eks:us-west-2:827602716714:cluster/coralogixDemo \
  K8S_NAMESPACE=codeagentsim \
  bash scripts/verify-rollout.sh

# Soak timer → writes .logs/phase4-validation-request.json
DEPLOY_START=$(date -u +%Y-%m-%dT%H:%M:%SZ) SOAK_MINUTES=30 \
  bash scripts/phase4-validate-after-soak.sh
```

---

## Changelog (this document)

| Date | Change |
|------|--------|
| 2026-06-08 | Initial reusable plan: Claude repo join, subsystem dedupe, prompt/reply alignment, Copilot tiers |
