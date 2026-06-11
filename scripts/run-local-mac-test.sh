#!/usr/bin/env bash
# Local Mac smoke test: sim -> local OTel collector -> Coralogix US2.
# Does NOT deploy to Kubernetes.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

COLLECTOR_NAME="${COLLECTOR_NAME:-otel-local-cx}"
COLLECTOR_IMAGE="${COLLECTOR_IMAGE:-otel/opentelemetry-collector-contrib:0.115.1}"
COLLECTOR_OTLP_PORT="${COLLECTOR_OTLP_PORT:-14317}"
COLLECTOR_OTLP_HTTP_PORT="${COLLECTOR_OTLP_HTTP_PORT:-14318}"
TRACE_ITERATIONS="${TRACE_ITERATIONS:-8}"
TRACE_INTERVAL_SEC="${TRACE_INTERVAL_SEC:-3}"

if [[ -z "${CORALOGIX_PRIVATE_KEY:-}" ]]; then
  echo "Set CORALOGIX_PRIVATE_KEY (Send-Your-Data key) before running." >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required for the local collector." >&2
  exit 1
fi

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "Creating .venv and installing requirements..."
  python3 -m venv "$ROOT/.venv"
  "$ROOT/.venv/bin/pip" -q install -r "$ROOT/requirements.txt"
fi

docker rm -f "$COLLECTOR_NAME" >/dev/null 2>&1 || true
docker run -d --name "$COLLECTOR_NAME" \
  -p "${COLLECTOR_OTLP_PORT}:4317" -p "${COLLECTOR_OTLP_HTTP_PORT}:4318" \
  -e "CORALOGIX_PRIVATE_KEY=${CORALOGIX_PRIVATE_KEY}" \
  -v "$ROOT/config/otel-collector-local-coralogix.yaml:/etc/otelcol-contrib/config.yaml:ro" \
  "$COLLECTOR_IMAGE" \
  --config=/etc/otelcol-contrib/config.yaml

cleanup() {
  docker rm -f "$COLLECTOR_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "Waiting for collector on localhost:${COLLECTOR_OTLP_PORT}..."
for _ in $(seq 1 30); do
  if docker logs "$COLLECTOR_NAME" 2>&1 | grep -q "Everything is ready"; then
    break
  fi
  sleep 1
done

echo "Starting sim (${TRACE_ITERATIONS} iterations)..."
(
  unset CORALOGIX_PRIVATE_KEY
  export OTLP_ENDPOINT="localhost:${COLLECTOR_OTLP_PORT}"
  export OTLP_INSECURE=true
  export PROMETHEUS_METRICS_PORT=9090
  export PROMETHEUS_REMOTE_WRITE_ENABLED=false
  export SIM_COPILOT_COLLECTOR_METRICS=true
  export SIM_COPILOT_COLLECTOR_ORG=coralogix
  export TRACE_ITERATIONS="$TRACE_ITERATIONS"
  export TRACE_INTERVAL_SEC="$TRACE_INTERVAL_SEC"
  export LOG_LEVEL=INFO
  exec "$ROOT/.venv/bin/python" "$ROOT/app.py"
)

echo "Done."
