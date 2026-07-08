#!/usr/bin/env bash
# Deploy otel-ai-agent-sim to SigmaDev with a single Coralogix exporter.
#
# Prerequisites:
#   - SigmaDev cluster exists (bash scripts/create-sigmadev-cluster.sh)
#   - CORALOGIX_PRIVATE_KEY set (Send-Your-Data API key for the new CX team)
#
# Usage:
#   kubectl config use-context SigmaDev
#   CORALOGIX_PRIVATE_KEY='cxtp_...' bash scripts/deploy-sigmadev.sh
#
# Optional env:
#   CORALOGIX_INGRESS_HOST   default ingress.us2.coralogix.com
#   CORALOGIX_PRIVATE_KEY    required (or k8s/sigmadev/secrets.env with private key)
#   K8S_NAMESPACE            default codeagentsim
#   ECR_REGISTRY             default 827602716714.dkr.ecr.us-west-2.amazonaws.com
#   IMAGE_TAG                default latest
#   BUILD_IMAGE=1            build + push linux/amd64 image before apply
#   SKIP_VERIFY=1            skip scripts/verify-rollout.sh
#   DRY_RUN=1                print actions only
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
K8S_DIR="${ROOT}/k8s/sigmadev"
CLUSTER_NAME="${EKS_CLUSTER_NAME:-SigmaDev}"
REGION="${AWS_REGION:-us-west-2}"
NS="${K8S_NAMESPACE:-codeagentsim}"
DEPLOY="${K8S_DEPLOYMENT:-otel-ai-agent-sim}"
ECR_REGISTRY="${ECR_REGISTRY:-827602716714.dkr.ecr.us-west-2.amazonaws.com}"
IMAGE_NAME="${IMAGE_NAME:-otel-ai-agent-sim}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
IMAGE="${ECR_REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"
CORALOGIX_INGRESS_HOST="${CORALOGIX_INGRESS_HOST:-ingress.us2.coralogix.com}"

KUBECTL=(kubectl)
if [[ -n "${KUBECTL_CONTEXT:-}" ]]; then
  KUBECTL+=(--context "$KUBECTL_CONTEXT")
else
  KUBECTL+=(--context "$CLUSTER_NAME")
fi

run() {
  if [[ -n "${DRY_RUN:-}" ]]; then
    echo "[dry-run] $*"
  else
    "$@"
  fi
}

# Load optional secrets.env (gitignored).
SECRETS_ENV="${K8S_DIR}/secrets.env"
if [[ -f "$SECRETS_ENV" ]]; then
  # shellcheck disable=SC1090
  set -a
  source "$SECRETS_ENV"
  set +a
fi

if [[ -z "${CORALOGIX_PRIVATE_KEY:-}" || "$CORALOGIX_PRIVATE_KEY" == REPLACE* ]]; then
  echo "FAIL: set CORALOGIX_PRIVATE_KEY (Send-Your-Data key) or create ${SECRETS_ENV}" >&2
  echo "Example: CORALOGIX_PRIVATE_KEY='cxtp_...' bash scripts/deploy-sigmadev.sh" >&2
  exit 1
fi

echo "== deploy otel-ai-agent-sim to ${CLUSTER_NAME} (namespace ${NS}) =="
echo "Coralogix ingress: ${CORALOGIX_INGRESS_HOST}"
echo "Image: ${IMAGE}"

if ! "${KUBECTL[@]}" cluster-info >/dev/null 2>&1; then
  echo "FAIL: kubectl cannot reach cluster (context ${KUBECTL[*]})" >&2
  echo "Run: aws eks update-kubeconfig --name ${CLUSTER_NAME} --region ${REGION} --alias ${CLUSTER_NAME}" >&2
  exit 1
fi

if [[ -n "${BUILD_IMAGE:-}" ]]; then
  echo "Building and pushing ${IMAGE}..."
  aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$ECR_REGISTRY"
  docker buildx build --platform linux/amd64 -t "$IMAGE" --push "$ROOT"
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

# Substitute Coralogix ingress host into collector ConfigMap.
sed "s|CORALOGIX_INGRESS_HOST|${CORALOGIX_INGRESS_HOST}|g" \
  "${K8S_DIR}/otel-collector-configmap.yaml" >"${TMP_DIR}/otel-collector-configmap.yaml"

echo "Applying namespace..."
run "${KUBECTL[@]}" apply -f "${K8S_DIR}/namespace.yaml"

echo "Applying Coralogix secret..."
if [[ -n "${DRY_RUN:-}" ]]; then
  echo "[dry-run] create secret coralogix-otel -n ${NS}"
else
  "${KUBECTL[@]}" create secret generic coralogix-otel -n "$NS" \
    --from-literal=private_key="$CORALOGIX_PRIVATE_KEY" \
    --dry-run=client -o yaml | "${KUBECTL[@]}" apply -f -
fi

echo "Applying OTEL collector (single Coralogix exporter)..."
run "${KUBECTL[@]}" apply -f "${TMP_DIR}/otel-collector-configmap.yaml"
run "${KUBECTL[@]}" apply -f "${K8S_DIR}/otel-collector-deployment.yaml"

echo "Applying sim deployment..."
run "${KUBECTL[@]}" apply -f "${K8S_DIR}/sim-deployment.yaml"

if [[ -n "${DRY_RUN:-}" ]]; then
  echo "DRY_RUN=1: skipping rollout wait and verification."
  exit 0
fi

echo "Waiting for collector rollout..."
"${KUBECTL[@]}" rollout status deployment/otel-collector-codeagentsim -n "$NS" --timeout=180s

echo "Waiting for sim rollout..."
"${KUBECTL[@]}" rollout status "deployment/${DEPLOY}" -n "$NS" --timeout=180s

if [[ -z "${SKIP_VERIFY:-}" ]]; then
  echo "Running post-rollout verification..."
  K8S_NAMESPACE="$NS" K8S_DEPLOYMENT="$DEPLOY" KUBECTL_CONTEXT="${KUBECTL_CONTEXT:-$CLUSTER_NAME}" \
    bash "${ROOT}/scripts/verify-rollout.sh"
fi

echo "Done: sim deployed to ${CLUSTER_NAME}/${NS} -> ${CORALOGIX_INGRESS_HOST}"
