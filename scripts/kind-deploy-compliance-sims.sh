#!/usr/bin/env bash
# Deploy Codex Compliance + ChatGPT Enterprise sims onto kind, scraped by the
# Coralogix otel-integration Helm chart (US2).
#
# Required:
#   export CORALOGIX_PRIVATE_KEY='cxtp_...'   # Send-Your-Data key (us2)
#
# Optional:
#   KIND_CLUSTER=kind-cluster
#   IMAGE_TAG=kind-compliance-local-$(date +%Y%m%d%H%M%S)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

KIND_CLUSTER="${KIND_CLUSTER:-kind-cluster}"
CTX="kind-${KIND_CLUSTER}"
IMAGE_REPO="${IMAGE_REPO:-otel-ai-agent-sim}"
if [[ -z "${IMAGE_TAG:-}" ]]; then
  IMAGE_TAG="kind-compliance-local-$(date +%Y%m%d%H%M%S)"
fi
if [[ -z "${IMAGE:-}" ]]; then
  IMAGE="${IMAGE_REPO}:${IMAGE_TAG}"
fi
HELM_NS="${HELM_NS:-coralogix}"
HELM_RELEASE="${HELM_RELEASE:-otel-coralogix}"
CX_REGION="${CX_REGION:-us2}"

if [[ -z "${CORALOGIX_PRIVATE_KEY:-}" ]]; then
  echo "CORALOGIX_PRIVATE_KEY unset (Send-Your-Data key for ${CX_REGION})" >&2
  exit 1
fi

if ! kind get clusters 2>/dev/null | grep -qx "${KIND_CLUSTER}"; then
  echo "creating kind cluster: ${KIND_CLUSTER}"
  kind create cluster --name "${KIND_CLUSTER}"
fi

kubectl config use-context "${CTX}" >/dev/null

echo "building ${IMAGE}"
docker build -t "${IMAGE}" .

echo "loading image into kind"
kind load docker-image "${IMAGE}" --name "${KIND_CLUSTER}"

echo "ensuring namespaces"
kubectl --context "${CTX}" apply -f "${ROOT}/k8s/codeagentsim/namespace.yaml"
kubectl --context "${CTX}" create namespace "${HELM_NS}" --dry-run=client -o yaml | kubectl --context "${CTX}" apply -f -

echo "upserting Coralogix secret in ${HELM_NS}"
kubectl --context "${CTX}" -n "${HELM_NS}" create secret generic coralogix-keys \
  --from-literal=PRIVATE_KEY="${CORALOGIX_PRIVATE_KEY}" \
  --dry-run=client -o yaml | kubectl --context "${CTX}" apply -f -

echo "helm repo update (coralogix)"
helm repo add coralogix https://cgx.jfrog.io/artifactory/coralogix-charts-virtual 2>/dev/null || true
helm repo update coralogix >/dev/null

echo "installing/upgrading ${HELM_RELEASE} (${HELM_NS}) -> domain=${CX_REGION}.coralogix.com"
helm upgrade --install "${HELM_RELEASE}" coralogix/otel-integration \
  --kube-context "${CTX}" \
  --namespace "${HELM_NS}" \
  --values "${ROOT}/k8s/kind-compliance/coralogix-otel-values.yaml" \
  --set "global.domain=${CX_REGION}.coralogix.com" \
  --set "global.clusterName=${KIND_CLUSTER}" \
  --wait --timeout 5m

echo "deploying compliance sims"
kubectl --context "${CTX}" apply -f "${ROOT}/k8s/codeagentsim/codex-compliance-sim-deployment.yaml"
kubectl --context "${CTX}" apply -f "${ROOT}/k8s/codeagentsim/chatgpt-enterprise-sim-deployment.yaml"
kubectl --context "${CTX}" -n codeagentsim set image deploy/otel-codex-compliance-sim "sim=${IMAGE}"
kubectl --context "${CTX}" -n codeagentsim set image deploy/otel-chatgpt-enterprise-sim "sim=${IMAGE}"
kubectl --context "${CTX}" -n codeagentsim patch deploy/otel-codex-compliance-sim --type=json \
  -p='[{"op":"replace","path":"/spec/template/spec/containers/0/imagePullPolicy","value":"IfNotPresent"}]'
kubectl --context "${CTX}" -n codeagentsim patch deploy/otel-chatgpt-enterprise-sim --type=json \
  -p='[{"op":"replace","path":"/spec/template/spec/containers/0/imagePullPolicy","value":"IfNotPresent"}]'
# Kind scrapes via helm collector in another ns; drop wait-for-collector init.
kubectl --context "${CTX}" -n codeagentsim patch deploy/otel-codex-compliance-sim --type=json \
  -p='[{"op":"remove","path":"/spec/template/spec/initContainers"}]' 2>/dev/null || true
kubectl --context "${CTX}" -n codeagentsim patch deploy/otel-chatgpt-enterprise-sim --type=json \
  -p='[{"op":"remove","path":"/spec/template/spec/initContainers"}]' 2>/dev/null || true

echo "waiting for rollouts"
kubectl --context "${CTX}" -n "${HELM_NS}" rollout status deploy/coralogix-opentelemetry-collector --timeout=180s
kubectl --context "${CTX}" -n codeagentsim rollout status deploy/otel-codex-compliance-sim --timeout=180s
kubectl --context "${CTX}" -n codeagentsim rollout status deploy/otel-chatgpt-enterprise-sim --timeout=180s

echo
kubectl --context "${CTX}" -n "${HELM_NS}" get pods,svc
kubectl --context "${CTX}" -n codeagentsim get pods,svc | grep -E 'NAME|compliance|chatgpt-enterprise|collector' || true
echo
echo "key prefix: ${CORALOGIX_PRIVATE_KEY:0:12}...  region=${CX_REGION}  image=${IMAGE}"
echo "verify (us2 profile that owns this key), e.g.:"
echo "  cx metrics query -p dmb-sm 'count(last_over_time(openai_compliance_events_total{event_type=\"CODEX_LOG\"}[15m]))'"
echo "  cx metrics query -p dmb-sm 'count(last_over_time(openai_compliance_events_total{event_type=\"CONVERSATION_MESSAGE\"}[15m]))'"
echo "done."
