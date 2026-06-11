#!/usr/bin/env bash
# Exercise all five refactored CLI simulators locally (no K8s deploy).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

COLLECTOR_NAME="${COLLECTOR_NAME:-otel-local-cx}"
COLLECTOR_OTLP_PORT="${COLLECTOR_OTLP_PORT:-14317}"
PY="${ROOT}/.venv/bin/python"

AGENTS=(claude_code gemini_cli codex cursor copilot_cli)

if [[ -z "${CORALOGIX_PRIVATE_KEY:-}" ]]; then
  echo "Set CORALOGIX_PRIVATE_KEY before running." >&2
  exit 1
fi

if [[ ! -x "$PY" ]]; then
  python3 -m venv "$ROOT/.venv"
  "$PY" -m pip -q install -r "$ROOT/requirements.txt"
fi

echo "=== compile / import check ==="
"$PY" -m compileall -q sim app.py
"$PY" <<'PY'
from sim.claude.dashboard import emit_claude_code_dashboard
from sim.gemini.agent import emit_gemini_cli_user_prompt_span
from sim.codex.agent import _codex_model_for_turn
from sim.cursor.agent import emit_cursor_composer_session
from sim.copilot.cli import emit_copilot_cli_session
from sim.copilot.collector_metrics import copilot_collector_enabled
from sim.generic.agent import emit_generic_agent_workflow
print("sim package imports: ok")
PY

if ! docker ps --format '{{.Names}}' | grep -qx "$COLLECTOR_NAME"; then
  echo "Starting collector container ${COLLECTOR_NAME}..."
  docker rm -f "$COLLECTOR_NAME" >/dev/null 2>&1 || true
  docker run -d --name "$COLLECTOR_NAME" \
    -p "${COLLECTOR_OTLP_PORT}:4317" -p "${COLLECTOR_OTLP_HTTP_PORT:-14318}:4318" \
    -e "CORALOGIX_PRIVATE_KEY=${CORALOGIX_PRIVATE_KEY}" \
    -v "$ROOT/config/otel-collector-local-coralogix.yaml:/etc/otelcol-contrib/config.yaml:ro" \
    otel/opentelemetry-collector-contrib:0.115.1 \
    --config=/etc/otelcol-contrib/config.yaml
  for _ in $(seq 1 30); do
    docker logs "$COLLECTOR_NAME" 2>&1 | grep -q "Everything is ready" && break
    sleep 1
  done
fi

run_agent() {
  local agent="$1"
  echo "=== sim force=${agent} ==="
  (
    unset CORALOGIX_PRIVATE_KEY
    export OTLP_ENDPOINT="localhost:${COLLECTOR_OTLP_PORT}"
    export OTLP_INSECURE=true
    export SIM_CLI_AGENTS_ONLY=true
    export SIM_FORCE_AGENT="$agent"
    export TRACE_ITERATIONS=2
    export TRACE_INTERVAL_SEC=1
    export PROMETHEUS_METRICS_PORT=9090
    export PROMETHEUS_REMOTE_WRITE_ENABLED=false
    export SIM_COPILOT_COLLECTOR_METRICS=true
    export SIM_COPILOT_COLLECTOR_ORG=coralogix
    export SIM_CLAUDE_OTLP_TRACES_ENABLED=true
    export LOG_LEVEL=WARNING
    exec "$PY" "$ROOT/app.py"
  ) 2>&1 | tail -3
}

for agent in "${AGENTS[@]}"; do
  run_agent "$agent"
done

echo "=== local emit complete; waiting for collector flush ==="
sleep 8
docker logs "$COLLECTOR_NAME" 2>&1 | tail -8

echo "OK: forced all agents: ${AGENTS[*]}"
