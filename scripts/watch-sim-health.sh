#!/usr/bin/env bash
# Background health watcher for otel-ai-agent-sim. Logs to .logs/sim-health-watch.log.
# Emits AGENT_LOOP_TICK lines so Cursor can wake the agent on each interval.
#
# Usage:
#   bash scripts/watch-sim-health.sh          # foreground (Ctrl+C to stop)
#   bash scripts/watch-sim-health.sh &        # background
#   INTERVAL_SEC=300 KUBECTL_CONTEXT=coralogixDemo bash scripts/watch-sim-health.sh
#
# Stop: kill $(cat .logs/sim-health-watch.pid)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${ROOT}/.logs"
LOG_FILE="${LOG_DIR}/sim-health-watch.log"
PID_FILE="${LOG_DIR}/sim-health-watch.pid"
INTERVAL_SEC="${INTERVAL_SEC:-300}"
CHECK="${ROOT}/scripts/check-sim-health.sh"

mkdir -p "$LOG_DIR"
echo $$ >"$PID_FILE"

exec >>"$LOG_FILE" 2>&1
echo "=== watch start $(date -u +%Y-%m-%dT%H:%M:%SZ) interval=${INTERVAL_SEC}s pid=$$ ==="

while true; do
  sleep "$INTERVAL_SEC"
  echo "AGENT_LOOP_TICK_sim_health {\"prompt\":\"Run bash scripts/check-sim-health.sh on coralogixDemo/codeagentsim. If restarts increased or pod unhealthy, diagnose from logs/describe and fix the root cause (OOM, Python crash, etc.), then redeploy.\"}"
  KUBECTL_CONTEXT="${KUBECTL_CONTEXT:-coralogixDemo}" bash "$CHECK" || true
done
