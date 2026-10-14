#!/usr/bin/env bash
# Wait for Phase 1 soak gate, commit Phase 2, deploy, log validation hints.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
DEPLOY_START="2026-06-22T15:57:53Z"
GATE_EPOCH=$(python3 - <<'PY'
from datetime import datetime, timedelta, timezone
start = datetime(2026, 6, 22, 15, 57, 53, tzinfo=timezone.utc)
print(int((start + timedelta(hours=2)).timestamp()))
PY
)
LOG="$ROOT/.logs/phase2-rollout.log"
mkdir -p "$ROOT/.logs"
exec > >(tee -a "$LOG") 2>&1
echo "=== phase2-rollout $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

while true; now=$(date +%s); do
  if (( now >= GATE_EPOCH )); then
    echo "Time gate reached (2h since Phase 1 deploy)"
    break
  fi
  echo "Waiting for soak gate... now=$(date -u +%H:%M:%SZ) gate=$(date -u -r "$GATE_EPOCH" +%H:%M:%SZ)"
  sleep 300
done

ssh-add --apple-load-keychain 2>/dev/null || true
ssh-add "$HOME/.ssh/git1" 2>/dev/null || true

git add sim/copilot/cli.py sim/common/model_pricing.py
if ! git diff --cached --quiet; then
  git commit -m "$(cat <<'EOF'
Add Copilot Phase 2 span fidelity for cx498 dashboards.

Dot model ids, client/internal span kinds, invoke messages for AI Analysis, chat/tool attrs, and invoke rollups.
EOF
)"
  git push coralogix-se HEAD
fi

KUBECTL_CONTEXT=arn:aws:eks:us-west-2:827602716714:cluster/coralogixDemo \
  K8S_NAMESPACE=codeagentsim \
  bash scripts/redeploy.sh

echo "Phase 2 deploy complete at $(date -u +%Y-%m-%dT%H:%M:%SZ). Run MCP validation queries after 5m soak."
