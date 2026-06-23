#!/usr/bin/env bash
# Wait for Phase 3 collector soak, then emit a wake marker for agent MCP validation (E1–E5).
#
# Usage:
#   bash scripts/phase3-validate-after-soak.sh
#   PHASE3_DEPLOY=2026-06-22T19:45:29Z SOAK_MINUTES=30 bash scripts/phase3-validate-after-soak.sh
#   SOAK_MINUTES=120  # plan default 2h gate
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PHASE3_DEPLOY="${PHASE3_DEPLOY:-2026-06-22T19:45:29Z}"
SOAK_MINUTES="${SOAK_MINUTES:-30}"
LOG_DIR="$ROOT/.logs"
LOG="$LOG_DIR/phase3-validation.log"
REQUEST="$LOG_DIR/phase3-validation-request.json"

mkdir -p "$LOG_DIR"
exec > >(tee -a "$LOG") 2>&1

GATE_EPOCH="$(python3 - <<PY
from datetime import datetime, timedelta, timezone
deploy = datetime.fromisoformat("${PHASE3_DEPLOY}".replace("Z", "+00:00"))
gate = deploy + timedelta(minutes=int("${SOAK_MINUTES}"))
print(int(gate.timestamp()))
PY
)"

echo "=== phase3-validate-after-soak $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
echo "Phase 3 deploy: ${PHASE3_DEPLOY}"
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

cat >"$REQUEST" <<EOF
{
  "phase": 3,
  "deploy_start": "${PHASE3_DEPLOY}",
  "soak_minutes": ${SOAK_MINUTES},
  "validation_start": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "mcp_server": "user-ob-coralogix-server",
  "promql_window_start": "${PHASE3_DEPLOY}",
  "checks": [
    {"id": "E1", "query": "sum(github_copilot_org_cli_session_count{organization=\\"coralogix\\"})"},
    {"id": "E2", "query": "sum(github_copilot_org_user_initiated_interaction_count_by_model_feature{organization=\\"coralogix\\", feature=\\"copilot_cli\\"})"},
    {"id": "E3", "query": "sum(github_copilot_billing_net_amount{organization=\\"coralogix\\", sku=\\"copilot_enterprise\\"})"},
    {"id": "E4", "query": "count(github_copilot_user_cli_session_count{organization=\\"coralogix\\", user_email=\\"\\"} > 0)"},
    {"id": "E5", "note": "Span process.tags user.email matches collector user_email for users with email set"},
    {"id": "E3b", "query": "sum(github_copilot_billing_net_amount{organization=\\"coralogix\\", sku=\\"copilot_business\\"})"},
    {"id": "lang", "query": "topk(3, sum by (language) (github_copilot_org_loc_added_sum_by_language_feature{organization=\\"coralogix\\", feature=\\"copilot_cli\\"}))"},
    {"id": "neg", "query": "sum(github_copilot_org_user_initiated_interaction_count_by_model_feature{organization=\\"coralogix\\", feature=\\"code_completion\\"})", "expect": "no new increase post-deploy vs copilot_cli dominance"}
  ]
}
EOF

echo "Wrote ${REQUEST}"
echo "PHASE3_VALIDATION_READY soak_minutes=${SOAK_MINUTES} deploy=${PHASE3_DEPLOY}"
echo 'AGENT_LOOP_WAKE_phase3 {"prompt":"Phase 3 soak gate reached. Read .logs/phase3-validation-request.json and run E1–E5 (+ language SKU checks) via user-ob-coralogix-server MCP query_metrics_range. PromQL/span window starts at Phase 3 deploy 2026-06-22T19:45:29Z. Fill pass/fail table for user."}'
