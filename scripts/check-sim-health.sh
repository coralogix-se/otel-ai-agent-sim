#!/usr/bin/env bash
# Ongoing health check for the sim pod: restarts, OOM, Python crashes, memory pressure, metrics.
# Persists state in .logs/sim-health-state.json to detect restart deltas between runs.
#
# Exit 0 = healthy, 1 = warning (memory pressure), 2 = alert (restarts / crash / not ready).
#
# Usage:
#   bash scripts/check-sim-health.sh
#   KUBECTL_CONTEXT=coralogixDemo bash scripts/check-sim-health.sh
#   MEMORY_WARN_PCT=80 bash scripts/check-sim-health.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${ROOT}/.logs"
STATE_FILE="${LOG_DIR}/sim-health-state.json"
mkdir -p "$LOG_DIR"

KUBECTL=(kubectl)
if [[ -n "${KUBECTL_CONTEXT:-}" ]]; then
  KUBECTL+=(--context "$KUBECTL_CONTEXT")
fi

NS="${K8S_NAMESPACE:-codeagentsim}"
DEPLOY="${K8S_DEPLOYMENT:-otel-ai-agent-sim}"
CONTAINER="${SIM_CONTAINER:-sim}"
LABEL="${APP_LABEL:-app=otel-ai-agent-sim}"
COLLECTOR_LABEL="${COLLECTOR_LABEL:-app=otel-collector-codeagentsim}"
MEMORY_WARN_PCT="${MEMORY_WARN_PCT:-80}"

ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
status=0
issues=()

emit() { echo "[$ts] $*"; }

fail_alert() {
  issues+=("$1")
  status=2
}

fail_warn() {
  issues+=("$1")
  if [[ "$status" -lt 1 ]]; then
    status=1
  fi
}

parse_mem_bytes() {
  local raw="${1:-}"
  if [[ -z "$raw" ]]; then
    echo 0
    return
  fi
  if [[ "$raw" =~ ^[0-9]+$ ]]; then
    echo "$raw"
    return
  fi
  if [[ "$raw" =~ ^([0-9]+)Ki$ ]]; then
    echo $(( ${BASH_REMATCH[1]} * 1024 ))
    return
  fi
  if [[ "$raw" =~ ^([0-9]+)Mi$ ]]; then
    echo $(( ${BASH_REMATCH[1]} * 1024 * 1024 ))
    return
  fi
  if [[ "$raw" =~ ^([0-9]+)Gi$ ]]; then
    echo $(( ${BASH_REMATCH[1]} * 1024 * 1024 * 1024 ))
    return
  fi
  echo 0
}

read_prev_state() {
  local pod="${1:-}" restarts="${2:-0}"
  if [[ ! -f "$STATE_FILE" ]]; then
    echo "${pod}|${restarts}"
    return
  fi
  python3 - "$STATE_FILE" "$pod" "$restarts" <<'PY'
import json, sys
path, pod, restarts = sys.argv[1], sys.argv[2], int(sys.argv[3])
try:
    with open(path) as f:
        data = json.load(f)
except Exception:
    print(f"{pod}|{restarts}")
    raise SystemExit
prev_pod = data.get("pod", "")
prev_restarts = int(data.get("restarts", 0))
if prev_pod != pod:
    print(f"{pod}|{restarts}")
else:
    print(f"{prev_pod}|{prev_restarts}")
PY
}

write_state() {
  local pod="$1" restarts="$2" memory_bytes="$3" memory_limit_bytes="$4" exit_status="$5"
  python3 - "$STATE_FILE" "$ts" "$pod" "$restarts" "$memory_bytes" "$memory_limit_bytes" "$exit_status" <<'PY'
import json, sys
path, ts, pod, restarts, mem, mem_lim, exit_status = sys.argv[1:8]
data = {
    "ts": ts,
    "pod": pod,
    "restarts": int(restarts),
    "memory_bytes": int(mem),
    "memory_limit_bytes": int(mem_lim),
    "last_status": int(exit_status),
}
with open(path, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PY
}

dump_diagnostics() {
  local pod="$1"
  emit "DIAG pod=${pod}"
  "${KUBECTL[@]}" get pod -n "$NS" "$pod" -o wide 2>&1 | sed 's/^/  /' || true
  emit "DIAG describe (tail)"
  "${KUBECTL[@]}" describe pod -n "$NS" "$pod" 2>&1 | tail -35 | sed 's/^/  /' || true
  emit "DIAG current logs (tail 40)"
  "${KUBECTL[@]}" logs -n "$NS" "$pod" -c "$CONTAINER" --tail=40 2>&1 | sed 's/^/  /' || true
  if "${KUBECTL[@]}" logs -n "$NS" "$pod" -c "$CONTAINER" --previous --tail=5 >/dev/null 2>&1; then
    emit "DIAG previous container logs (likely crash cause)"
    "${KUBECTL[@]}" logs -n "$NS" "$pod" -c "$CONTAINER" --previous --tail=60 2>&1 | sed 's/^/  /' || true
  fi
}

emit "== sim health: ${DEPLOY} in ${NS} =="

POD="$("${KUBECTL[@]}" get pods -n "$NS" -l "$LABEL" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
if [[ -z "$POD" ]]; then
  fail_alert "no pod for -l ${LABEL}"
  emit "ALERT ${issues[*]}"
  exit 2
fi

phase="$("${KUBECTL[@]}" get pod -n "$NS" "$POD" -o jsonpath='{.status.phase}')"
ready="$("${KUBECTL[@]}" get pod -n "$NS" "$POD" -o jsonpath='{.status.containerStatuses[?(@.name=="'"$CONTAINER"'")].ready}')"
restarts="$("${KUBECTL[@]}" get pod -n "$NS" "$POD" -o jsonpath='{.status.containerStatuses[?(@.name=="'"$CONTAINER"'")].restartCount}')"
image="$("${KUBECTL[@]}" get pod -n "$NS" "$POD" -o jsonpath='{.status.containerStatuses[?(@.name=="'"$CONTAINER"'")].imageID}')"
last_reason="$("${KUBECTL[@]}" get pod -n "$NS" "$POD" -o jsonpath='{.status.containerStatuses[?(@.name=="'"$CONTAINER"'")].lastState.terminated.reason}')"
last_exit="$("${KUBECTL[@]}" get pod -n "$NS" "$POD" -o jsonpath='{.status.containerStatuses[?(@.name=="'"$CONTAINER"'")].lastState.terminated.exitCode}')"
mem_limit_raw="$("${KUBECTL[@]}" get pod -n "$NS" "$POD" -o jsonpath='{.spec.containers[?(@.name=="'"$CONTAINER"'")].resources.limits.memory}')"
mem_limit_bytes="$(parse_mem_bytes "$mem_limit_raw")"
mem_bytes="$("${KUBECTL[@]}" exec -n "$NS" "$POD" -c "$CONTAINER" -- sh -c 'cat /sys/fs/cgroup/memory.current 2>/dev/null || cat /sys/fs/cgroup/memory/memory.usage_in_bytes 2>/dev/null || echo 0' 2>/dev/null || echo 0)"
mem_bytes="${mem_bytes//$'\r'/}"
mem_bytes="${mem_bytes//$'\n'/}"

prev="$(read_prev_state "$POD" "$restarts")"
prev_pod="${prev%%|*}"
prev_restarts="${prev##*|}"

emit "pod=${POD} phase=${phase} ready=${ready} restarts=${restarts} image=${image##*@}"
if [[ -n "$last_reason" ]]; then
  emit "last_terminated reason=${last_reason} exit=${last_exit}"
fi
if [[ "$mem_limit_bytes" -gt 0 ]]; then
  mem_pct=$(( mem_bytes * 100 / mem_limit_bytes ))
  emit "memory=${mem_bytes}B (${mem_pct}% of ${mem_limit_raw})"
else
  mem_pct=0
  emit "memory=${mem_bytes}B (limit unknown)"
fi

if [[ "$phase" != "Running" || "$ready" != "true" ]]; then
  fail_alert "pod not healthy (phase=${phase} ready=${ready})"
fi

if [[ "$restarts" -gt 0 ]]; then
  if [[ "$POD" == "$prev_pod" && "$restarts" -gt "$prev_restarts" ]]; then
    delta=$(( restarts - prev_restarts ))
    fail_alert "restart count increased ${prev_restarts} -> ${restarts} (+${delta})"
  elif [[ "$POD" != "$prev_pod" && "$restarts" -gt 0 ]]; then
    fail_warn "new pod already has ${restarts} restart(s) — investigate"
  fi
fi

if [[ "$last_reason" == "OOMKilled" ]]; then
  fail_alert "last container exit was OOMKilled — bump sim memory limit in k8s/codeagentsim/sim-deployment.yaml"
fi

if [[ "$mem_limit_bytes" -gt 0 && "$mem_pct" -ge "$MEMORY_WARN_PCT" ]]; then
  fail_warn "memory at ${mem_pct}% of ${mem_limit_raw} (threshold ${MEMORY_WARN_PCT}%)"
fi

LOGS="$("${KUBECTL[@]}" logs -n "$NS" "$POD" -c "$CONTAINER" --tail=200 2>&1 || true)"
if echo "$LOGS" | grep -qE 'Traceback \(most recent call last\)|NameError:|ImportError:|ModuleNotFoundError:|SyntaxError:|MemoryError:'; then
  fail_alert "Python error in current logs"
fi

if ! echo "$LOGS" | grep -q 'AI Agent Simulation started'; then
  fail_alert "startup banner missing — process may not have initialized"
fi

if [[ "$ready" == "true" ]]; then
  if ! "${KUBECTL[@]}" exec -n "$NS" "$POD" -c "$CONTAINER" -- python3 - <<'PY' >/dev/null 2>&1
import urllib.request
with urllib.request.urlopen("http://127.0.0.1:9090/metrics", timeout=10) as resp:
    body = resp.read().decode("utf-8", errors="replace")
if "claude_code_cost_usage_USD" not in body:
    raise SystemExit("missing claude metrics")
PY
  then
    fail_warn "metrics probe failed or Claude counters missing"
  fi
fi

# Collector sanity (shared pipeline — collector crash can look like sim issues).
COL_POD="$("${KUBECTL[@]}" get pods -n "$NS" -l "$COLLECTOR_LABEL" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
if [[ -n "$COL_POD" ]]; then
  col_restarts="$("${KUBECTL[@]}" get pod -n "$NS" "$COL_POD" -o jsonpath='{.status.containerStatuses[0].restartCount}')"
  col_ready="$("${KUBECTL[@]}" get pod -n "$NS" "$COL_POD" -o jsonpath='{.status.containerStatuses[0].ready}')"
  emit "collector=${COL_POD} ready=${col_ready} restarts=${col_restarts}"
  if [[ "$col_ready" != "true" ]]; then
    fail_alert "otel collector not ready"
  elif [[ "$col_restarts" -gt 0 ]]; then
    col_last="$("${KUBECTL[@]}" get pod -n "$NS" "$COL_POD" -o jsonpath='{.status.containerStatuses[0].lastState.terminated.reason}')"
    if [[ "$col_last" == "OOMKilled" ]]; then
      fail_warn "collector had OOMKilled restarts (${col_restarts}) — check collector memory limits"
    fi
  fi
fi

write_state "$POD" "$restarts" "$mem_bytes" "$mem_limit_bytes" "$status"

if [[ "$status" -eq 0 ]]; then
  emit "OK healthy"
  exit 0
fi

emit "ISSUES: ${issues[*]}"
if [[ "$status" -ge 2 ]]; then
  dump_diagnostics "$POD"
  emit "FIX hints: OOM -> raise memory limit; Python traceback -> fix code and redeploy; crash loop -> bash scripts/verify-rollout.sh after fix"
  exit 2
fi

emit "WARN memory or secondary issue — monitor"
exit 1
