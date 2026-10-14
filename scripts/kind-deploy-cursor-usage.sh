#!/usr/bin/env bash
# Deploy Cursor Usage (incl. Annual Budget / pooled org gauges) onto kind → Coralogix US2.
# Reuses the kind collector from scripts/kind-deploy-openai-admin.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

KIND_CLUSTER="${KIND_CLUSTER:-kind-cluster}"
CTX="kind-${KIND_CLUSTER}"
IMAGE_REPO="${IMAGE_REPO:-otel-ai-agent-sim}"
IMAGE_TAG="${IMAGE_TAG:-kind-cursor-local-$(date +%Y%m%d%H%M%S)}"
IMAGE="${IMAGE:-${IMAGE_REPO}:${IMAGE_TAG}}"
SECRETS_ENV="${SECRETS_ENV:-$ROOT/k8s/codeagentsim/secrets.env}"

if [[ ! -f "$SECRETS_ENV" ]]; then
  echo "missing secrets file: $SECRETS_ENV" >&2
  exit 1
fi

set -a
# shellcheck source=/dev/null
source "$SECRETS_ENV"
set +a

if [[ -z "${CORALOGIX_PRIVATE_KEY_US2:-}" ]]; then
  echo "CORALOGIX_PRIVATE_KEY_US2 unset in $SECRETS_ENV" >&2
  exit 1
fi

if ! kind get clusters 2>/dev/null | grep -qx "$KIND_CLUSTER"; then
  echo "creating kind cluster: $KIND_CLUSTER"
  kind create cluster --name "$KIND_CLUSTER"
fi

kubectl config use-context "$CTX" >/dev/null

echo "building ${IMAGE}..."
docker build -t "$IMAGE" .
kind load docker-image "$IMAGE" --name "$KIND_CLUSTER"

echo "ensuring collector + cursor usage sim..."
kubectl apply -f "$ROOT/k8s/codeagentsim/namespace.yaml"
kubectl -n codeagentsim create secret generic coralogix-multi-export \
  --from-literal=private_key_us2="$CORALOGIX_PRIVATE_KEY_US2" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -f "$ROOT/k8s/kind-openai/otel-collector-configmap.yaml"
kubectl apply -f "$ROOT/k8s/kind-openai/otel-collector-deployment.yaml"
kubectl apply -f "$ROOT/k8s/codeagentsim/cursor-usage-sim-deployment.yaml"
kubectl -n codeagentsim set image deploy/otel-cursor-usage-sim "sim=${IMAGE}"
# Collector must reload scrape config (configmap change).
kubectl -n codeagentsim rollout restart deploy/otel-collector-codeagentsim

kubectl -n codeagentsim rollout status deploy/otel-collector-codeagentsim --timeout=180s
kubectl -n codeagentsim rollout status deploy/otel-cursor-usage-sim --timeout=180s

echo
kubectl -n codeagentsim get pods,svc -l 'app in (otel-cursor-usage-sim,otel-collector-codeagentsim)'
echo
echo "verify: cx metrics query -p dmb-sm 'sum(last_over_time(cursor_org_pool_limit_usd[93600s]))'"
echo "done."
