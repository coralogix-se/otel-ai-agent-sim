#!/usr/bin/env bash
# Wait for post-deploy soak, re-run Tier 0 local checks, emit wake payload for tenant (Tier 2–3) validation.
#
# Usage:
#   DEPLOY_START=2026-06-24T15:47:00Z SOAK_MINUTES=30 bash scripts/post-deploy-validate-after-soak.sh
#   bash scripts/post-deploy-validate-after-soak.sh   # deploy_start=now, soak=30m
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DEPLOY_START="${DEPLOY_START:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
SOAK_MINUTES="${SOAK_MINUTES:-30}"
LOG_DIR="$ROOT/.logs"
LOG="$LOG_DIR/post-deploy-validation.log"
REQUEST="$LOG_DIR/post-deploy-validation-request.json"

mkdir -p "$LOG_DIR"
exec > >(tee -a "$LOG") 2>&1

GATE_EPOCH="$(python3 - <<PY
from datetime import datetime, timedelta, timezone
deploy = datetime.fromisoformat("${DEPLOY_START}".replace("Z", "+00:00"))
gate = deploy + timedelta(minutes=int("${SOAK_MINUTES}"))
print(int(gate.timestamp()))
PY
)"

echo "=== post-deploy-validate-after-soak $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
echo "Deploy start: ${DEPLOY_START}"
echo "Soak gate: ${SOAK_MINUTES}m -> $(date -u -r "$GATE_EPOCH" +%Y-%m-%dT%H:%M:%SZ)"

while true; do
  now=$(date +%s)
  if (( now >= GATE_EPOCH )); then
    echo "Soak gate reached."
    break
  fi
  remaining=$((GATE_EPOCH - now))
  echo "Waiting... ${remaining}s remaining (gate $(date -u -r "$GATE_EPOCH" +%H:%M:%SZ))"
  sleep "$(( remaining > 300 ? 300 : remaining ))"
done

echo "--- Tier 0 local (post-soak) ---"
if ! bash "$ROOT/scripts/post-deploy-validate-local.sh"; then
  echo "FAIL: Tier 0 local validation after soak" >&2
  exit 1
fi

VALIDATION_START="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
cat >"$REQUEST" <<EOF
{
  "deploy_start": "${DEPLOY_START}",
  "soak_minutes": ${SOAK_MINUTES},
  "validation_start": "${VALIDATION_START}",
  "mcp_server": "user-ob-coralogix-server",
  "doc": "docs/post-deploy-validation.md",
  "tier2_promql": [
    "sum(increase(claude_code_cost_usage_USD_total{job=\\"otel-ai-agent-sim\\"}[1h]))",
    "sum by (cx_subsystem_name) (increase(claude_code_cost_usage_USD_total{job=\\"otel-ai-agent-sim\\"}[1h]))",
    "sum by (repository_name) (max_over_time(claude_code_session_repo_info{job=\\"otel-ai-agent-sim\\"}[1h]))",
    "group by (user_email, repository_name) (max by (session_id, repository_name) (max_over_time(claude_code_session_repo_info{job=\\"otel-ai-agent-sim\\"}[1h])) * on(session_id) group_left(user_email) max by (session_id, user_email) (increase(claude_code_cost_usage_USD_total{job=\\"otel-ai-agent-sim\\"}[1h])))"
  ],
  "tier2_pass": [
    "2.1a cost increase > 0",
    "2.1b subsystems claude-code and claude-code-sessions",
    "2.2a join returns rows",
    "2.2b no repository_name=unknown",
    "2.2c coralogix/cxai-observability-demo-playground present"
  ],
  "tier3_promql": [
    "github_copilot_org_cli_session_count{organization=\\"coralogix\\"}",
    "github_copilot_org_user_initiated_interaction_count_by_model_feature{feature=\\"copilot_cli\\"}"
  ]
}
EOF

echo "Wrote ${REQUEST}"
echo "POST_DEPLOY_VALIDATION_READY soak_minutes=${SOAK_MINUTES} deploy=${DEPLOY_START}"
echo "AGENT_LOOP_WAKE_postdeploy {\"prompt\":\"Run Tier 2-3 obdev validation from .logs/post-deploy-validation-request.json per docs/post-deploy-validation.md. Use user-ob-coralogix-server MCP. Report pass/fail table; do not push until validated.\",\"validation_start\":\"${VALIDATION_START}\",\"deploy_start\":\"${DEPLOY_START}\"}"
