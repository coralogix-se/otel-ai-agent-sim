#!/usr/bin/env bash
# Deploy OpenAI Admin sim + US2 OTEL collector onto a local kind cluster
# (scrape → prometheusremotewrite, same model as EKS codeagentsim).
#
# Usage:
#   ./scripts/kind-deploy-openai-admin.sh
#   KIND_CLUSTER=kind-cluster ./scripts/kind-deploy-openai-admin.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

KIND_CLUSTER="${KIND_CLUSTER:-kind-cluster}"
CTX="kind-${KIND_CLUSTER}"
IMAGE_REPO="${IMAGE_REPO:-otel-ai-agent-sim}"
# Unique tag each run so kind nodes pick up code changes (IfNotPresent + stable tag is sticky).
IMAGE_TAG="${IMAGE_TAG:-kind-openai-local-$(date +%Y%m%d%H%M%S)}"
IMAGE="${IMAGE:-${IMAGE_REPO}:${IMAGE_TAG}}"
SECRETS_ENV="${SECRETS_ENV:-$ROOT/k8s/codeagentsim/secrets.env}"

if [[ ! -f "$SECRETS_ENV" ]]; then
  echo "missing secrets file: $SECRETS_ENV" >&2
  exit 1
fi

# shellcheck disable=SC1090
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

echo "stopping any local python -m sim.openai (direct RW)…"
pkill -f 'python -m sim.openai' 2>/dev/null || true

echo "building $IMAGE (native platform for kind)…"
docker build -t "$IMAGE" .

echo "loading image into kind…"
kind load docker-image "$IMAGE" --name "$KIND_CLUSTER"

echo "applying namespace + US2 secret + collector + openai sim…"
kubectl apply -f "$ROOT/k8s/codeagentsim/namespace.yaml"

kubectl -n codeagentsim create secret generic coralogix-multi-export \
  --from-literal=private_key_us2="$CORALOGIX_PRIVATE_KEY_US2" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -f "$ROOT/k8s/kind-openai/otel-collector-configmap.yaml"
kubectl apply -f "$ROOT/k8s/kind-openai/otel-collector-deployment.yaml"
kubectl apply -f "$ROOT/k8s/codeagentsim/openai-admin-sim-deployment.yaml"
# Point the Deployment at the freshly loaded tag (manifest default is a sticky local tag).
kubectl -n codeagentsim set image deploy/otel-openai-admin-sim "sim=${IMAGE}"

echo "waiting for collector + sim…"
kubectl -n codeagentsim rollout status deploy/otel-collector-codeagentsim --timeout=180s
kubectl -n codeagentsim rollout status deploy/otel-openai-admin-sim --timeout=180s

echo
kubectl -n codeagentsim get pods,svc
echo
echo "key prefix: ${CORALOGIX_PRIVATE_KEY_US2:0:12}…  region=us2  job=otel-openai-admin-sim"
echo "verify: cx metrics search -p dmb-sm --name '*openai_administration*' "
echo "done."
