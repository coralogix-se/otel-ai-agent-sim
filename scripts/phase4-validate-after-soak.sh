#!/usr/bin/env bash
# Phase 4 gate: wait for soak, then emit validation request for full obdev checklist (A–F + personal repos).
#
# Usage:
#   bash scripts/phase4-validate-after-soak.sh
#   PHASE4_DEPLOY=2026-06-23T03:10:00Z SOAK_MINUTES=120 bash scripts/phase4-validate-after-soak.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PHASE4_DEPLOY="${PHASE4_DEPLOY:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
SOAK_MINUTES="${SOAK_MINUTES:-120}"
LOG_DIR="$ROOT/.logs"
LOG="$LOG_DIR/phase4-validation.log"
REQUEST="$LOG_DIR/phase4-validation-request.json"

mkdir -p "$LOG_DIR"
exec > >(tee -a "$LOG") 2>&1

GATE_EPOCH="$(python3 - <<PY
from datetime import datetime, timedelta, timezone
deploy = datetime.fromisoformat("${PHASE4_DEPLOY}".replace("Z", "+00:00"))
gate = deploy + timedelta(minutes=int("${SOAK_MINUTES}"))
print(int(gate.timestamp()))
PY
)"

echo "=== phase4-validate-after-soak $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
echo "Phase 4 deploy: ${PHASE4_DEPLOY}"
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
  "phase": 4,
  "deploy_start": "${PHASE4_DEPLOY}",
  "soak_minutes": ${SOAK_MINUTES},
  "validation_start": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "mcp_server": "user-ob-coralogix-server",
  "span_window_start": "${PHASE4_DEPLOY}",
  "personal_repos": [
    {"agent": "claude_code", "user": "quinn.nguyen@coralogix.com", "repo": "quinn-nguyen/Coralogix-log-explore"},
    {"agent": "copilot_cli", "user": "taylor.lee@coralogix.com", "repo": "taylor-lee/Coralogix-log-explore"}
  ],
  "checks": [
    {"id": "A1", "type": "dataprime", "note": "hasData count > 0"},
    {"id": "A2", "type": "dataprime", "note": "copilot-cli / copilot-sessions subsystems"},
    {"id": "B1-B22", "type": "dataprime", "doc": "docs/copilot-telemetry-plan.md"},
    {"id": "C1-C3", "type": "dataprime", "doc": "docs/overview-dev-dashboard-queries.txt"},
    {"id": "E1-E5", "type": "promql"},
    {"id": "F1-F4", "type": "dataprime", "note": "negative regression checks"},
    {"id": "P1", "type": "dataprime", "note": "personal-repo violator spans only for pinned users"},
    {"id": "UI", "type": "manual", "note": "cx498 obdev Copilot CLI page smoke"}
  ]
}
EOF

echo "Wrote ${REQUEST}"
echo "PHASE4_VALIDATION_READY soak_minutes=${SOAK_MINUTES} deploy=${PHASE4_DEPLOY}"
