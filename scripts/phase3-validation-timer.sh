#!/usr/bin/env bash
# Arm Phase 3 validation after soak (default 30m from deploy). Logs to .logs/phase3-validation-timer.log
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE3_DEPLOY="${PHASE3_DEPLOY:-2026-06-22T19:45:29Z}"
SOAK_MINUTES="${SOAK_MINUTES:-30}"
exec >>"$ROOT/.logs/phase3-validation-timer.log" 2>&1
echo "=== timer start $(date -u +%Y-%m-%dT%H:%M:%SZ) deploy=${PHASE3_DEPLOY} soak=${SOAK_MINUTES}m ==="
PHASE3_DEPLOY="$PHASE3_DEPLOY" SOAK_MINUTES="$SOAK_MINUTES" bash "$ROOT/scripts/phase3-validate-after-soak.sh"
