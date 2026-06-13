#!/usr/bin/env bash
# Verify the sim pod is actually healthy after ``kubectl rollout status`` (not just started).
# Exit 1 on crash loop, Python tracebacks in logs, or missing Prometheus metrics.
#
# Usage:
#   bash scripts/verify-rollout.sh
#   K8S_NAMESPACE=codeagentsim bash scripts/verify-rollout.sh
#   WARMUP_SEC=30 bash scripts/verify-rollout.sh
set -euo pipefail

KUBECTL=(kubectl)
if [[ -n "${KUBECTL_CONTEXT:-}" ]]; then
  KUBECTL+=(--context "$KUBECTL_CONTEXT")
fi

NS="${K8S_NAMESPACE:-codeagentsim}"
DEPLOY="${K8S_DEPLOYMENT:-otel-ai-agent-sim}"
CONTAINER="${SIM_CONTAINER:-sim}"
WARMUP_SEC="${WARMUP_SEC:-25}"
LABEL="${APP_LABEL:-app=otel-ai-agent-sim}"

echo "== verify rollout: ${DEPLOY} in ${NS} (warmup ${WARMUP_SEC}s) =="

POD="$("${KUBECTL[@]}" get pods -n "$NS" -l "$LABEL" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
if [[ -z "$POD" ]]; then
  echo "FAIL: no pod found for -l ${LABEL} in ${NS}" >&2
  exit 1
fi

phase="$("${KUBECTL[@]}" get pod -n "$NS" "$POD" -o jsonpath='{.status.phase}')"
ready="$("${KUBECTL[@]}" get pod -n "$NS" "$POD" -o jsonpath='{.status.containerStatuses[?(@.name=="'"$CONTAINER"'")].ready}')"
restarts="$("${KUBECTL[@]}" get pod -n "$NS" "$POD" -o jsonpath='{.status.containerStatuses[?(@.name=="'"$CONTAINER"'")].restartCount}')"
image="$("${KUBECTL[@]}" get pod -n "$NS" "$POD" -o jsonpath='{.status.containerStatuses[?(@.name=="'"$CONTAINER"'")].imageID}')"

echo "pod=${POD} phase=${phase} ready=${ready} restarts=${restarts}"
echo "image=${image}"

if [[ "$phase" != "Running" ]]; then
  echo "FAIL: pod phase is ${phase}, want Running" >&2
  "${KUBECTL[@]}" describe pod -n "$NS" "$POD" | tail -25 >&2
  exit 1
fi

if [[ "$ready" != "true" ]]; then
  echo "FAIL: container ${CONTAINER} not ready" >&2
  exit 1
fi

echo "waiting ${WARMUP_SEC}s to detect crash loop..."
sleep "$WARMUP_SEC"

restarts_after="$("${KUBECTL[@]}" get pod -n "$NS" "$POD" -o jsonpath='{.status.containerStatuses[?(@.name=="'"$CONTAINER"'")].restartCount}')"
ready_after="$("${KUBECTL[@]}" get pod -n "$NS" "$POD" -o jsonpath='{.status.containerStatuses[?(@.name=="'"$CONTAINER"'")].ready}')"
phase_after="$("${KUBECTL[@]}" get pod -n "$NS" "$POD" -o jsonpath='{.status.phase}')"

echo "after warmup: phase=${phase_after} ready=${ready_after} restarts=${restarts_after}"

if [[ "$phase_after" != "Running" || "$ready_after" != "true" ]]; then
  echo "FAIL: pod unhealthy after warmup" >&2
  "${KUBECTL[@]}" logs -n "$NS" "$POD" -c "$CONTAINER" --tail=60 >&2 || true
  exit 1
fi

if [[ "$restarts_after" != "$restarts" ]]; then
  echo "FAIL: restart count increased ${restarts} -> ${restarts_after} (likely crash after startup)" >&2
  "${KUBECTL[@]}" logs -n "$NS" "$POD" -c "$CONTAINER" --tail=80 >&2 || true
  exit 1
fi

LOGS="$("${KUBECTL[@]}" logs -n "$NS" "$POD" -c "$CONTAINER" 2>&1 || true)"
if echo "$LOGS" | grep -qE 'Traceback \(most recent call last\)|NameError:|ImportError:|ModuleNotFoundError:|SyntaxError:'; then
  echo "FAIL: Python error in container logs:" >&2
  echo "$LOGS" | grep -E 'Traceback|Error:|File "/app' | tail -20 >&2
  exit 1
fi

if ! echo "$LOGS" | grep -q 'AI Agent Simulation started'; then
  echo "FAIL: startup banner missing from logs" >&2
  "${KUBECTL[@]}" logs -n "$NS" "$POD" -c "$CONTAINER" --tail=40 >&2
  exit 1
fi

echo "probing :9090/metrics inside pod..."
"${KUBECTL[@]}" exec -n "$NS" "$POD" -c "$CONTAINER" -- python3 - <<'PY'
import sys
import urllib.request

url = "http://127.0.0.1:9090/metrics"
with urllib.request.urlopen(url, timeout=15) as resp:
    body = resp.read().decode("utf-8", errors="replace")
if resp.status != 200:
    sys.exit(f"metrics HTTP {resp.status}")
needles = ("claude_code_cost_usage_USD", "claude_code_session_count")
if not any(n in body for n in needles):
    sys.exit(f"metrics missing Claude counters (len={len(body)})")
print(f"metrics OK ({len(body)} bytes, claude counters present)")
PY

echo "OK: ${DEPLOY} verified in ${NS}"
