#!/usr/bin/env bash
# Run the OpenAI API Platform sim locally and remote_write metrics to Coralogix.
# Target tenant for validation: cx profile ``dmb-sm`` (US2).
#
# Does NOT deploy to Kubernetes.
#
# Usage:
#   # Prefer the dmb-sm / US2 Send-Your-Data key (cxtp_hyz… in secrets.env):
#   set -a; source k8s/codeagentsim/secrets.env; set +a
#   export CORALOGIX_PRIVATE_KEY="$CORALOGIX_PRIVATE_KEY_US2"
#   bash scripts/run-openai-local-dmb-sm.sh
#
# Or export CORALOGIX_PRIVATE_KEY directly.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# If secrets.env is present and CORALOGIX_PRIVATE_KEY unset, use the US2 / dmb-sm key.
if [[ -z "${CORALOGIX_PRIVATE_KEY:-}" && -f "$ROOT/k8s/codeagentsim/secrets.env" ]]; then
  # shellcheck disable=SC1091
  set -a
  source "$ROOT/k8s/codeagentsim/secrets.env"
  set +a
  export CORALOGIX_PRIVATE_KEY="${CORALOGIX_PRIVATE_KEY_US2:-}"
fi

if [[ -z "${CORALOGIX_PRIVATE_KEY:-}" ]]; then
  echo "Set CORALOGIX_PRIVATE_KEY to the dmb-sm Send-Your-Data key (US2 / cxtp_hyz…)." >&2
  exit 1
fi

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "Creating .venv and installing requirements..."
  python3 -m venv "$ROOT/.venv"
  "$ROOT/.venv/bin/pip" -q install -r "$ROOT/requirements.txt"
fi

export CORALOGIX_REGION="${CORALOGIX_REGION:-us2}"
export PROMETHEUS_REMOTE_WRITE_ENABLED=true
export PROMETHEUS_METRICS_PORT="${PROMETHEUS_METRICS_PORT:-9091}"
export PROMETHEUS_RW_JOB="${PROMETHEUS_RW_JOB:-otel-openai-admin-sim}"
export PROMETHEUS_RW_INSTANCE="${PROMETHEUS_RW_INSTANCE:-localhost:${PROMETHEUS_METRICS_PORT}}"
export PROMETHEUS_RW_INTERVAL_SEC="${PROMETHEUS_RW_INTERVAL_SEC:-15}"
export SIM_OPENAI_ADMIN_INTERVAL_SEC="${SIM_OPENAI_ADMIN_INTERVAL_SEC:-30}"
export SIM_OPENAI_ADMIN_EMITS_PER_CYCLE="${SIM_OPENAI_ADMIN_EMITS_PER_CYCLE:-8}"
export OPENAI_ADMIN_CX_APPLICATION_NAME="${OPENAI_ADMIN_CX_APPLICATION_NAME:-OpenAI}"
export OPENAI_ADMIN_CX_SUBSYSTEM_NAME="${OPENAI_ADMIN_CX_SUBSYSTEM_NAME:-API Platform}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"
# Leave TRACE_ITERATIONS unset for continuous run; set e.g. 10 for a short smoke.

echo "Starting OpenAI Admin sim → remote_write ingress.${CORALOGIX_REGION}.coralogix.com"
echo "  job=${PROMETHEUS_RW_JOB}  scrape :${PROMETHEUS_METRICS_PORT}"
echo "  Validate with: cx metrics query -p dmb-sm 'count(max_over_time(openai_administration_usage_input_tokens{job=\"${PROMETHEUS_RW_JOB}\"}[10d]))'"
exec "$ROOT/.venv/bin/python" -m sim.openai
