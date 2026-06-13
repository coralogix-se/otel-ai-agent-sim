#!/usr/bin/env bash
# Build linux/amd64 image, push to ECR, restart the sim Deployment on EKS.
# Override: ECR_REGISTRY, AWS_REGION, K8S_NAMESPACE, K8S_DEPLOYMENT, IMAGE_NAME, IMAGE_TAG
#
# If docker build is too slow or you already built and pushed the image elsewhere:
#   SKIP_DOCKER=1 bash scripts/redeploy.sh
#   (applies k8s manifest, then rollout restart + status — cluster pulls :latest with imagePullPolicy: Always)
# If buildx still misbehaves: USE_LEGACY_BUILD=1 bash scripts/redeploy.sh
# If you need a local image: BUILDX_LOAD=1 bash scripts/redeploy.sh (uses --load + docker push; can EOF on some Macs)
# If you already ran ``docker login`` to ECR: SKIP_ECR_LOGIN=1 bash scripts/redeploy.sh
set -euo pipefail

KUBECTL=(kubectl)
if [[ -n "${KUBECTL_CONTEXT:-}" ]]; then
  KUBECTL+=(--context "$KUBECTL_CONTEXT")
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ECR_REGISTRY="${ECR_REGISTRY:-827602716714.dkr.ecr.us-west-2.amazonaws.com}"
IMAGE_NAME="${IMAGE_NAME:-otel-ai-agent-sim}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
IMAGE="${ECR_REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"
REGION="${AWS_REGION:-us-west-2}"
NS="${K8S_NAMESPACE:-codeagentsim}"
DEPLOY="${K8S_DEPLOYMENT:-otel-ai-agent-sim}"

cd "$ROOT"
DK8S="${ROOT}/k8s/codeagentsim/sim-deployment.yaml"
if [[ ! -f "$DK8S" ]]; then
  DK8S="${ROOT}/k8s/deployment.yaml"
fi
if [[ -f "$DK8S" ]]; then
  echo "Applying ${DK8S} (env, labels, image pull policy)..."
  "${KUBECTL[@]}" apply -f "$DK8S"
fi

if [[ -n "${SKIP_DOCKER:-}" ]]; then
  echo "SKIP_DOCKER=1: skipping docker build, ECR login, and push (use after you built and pushed the image, or to force rollout only)."
else
  if [[ -n "${SKIP_ECR_LOGIN:-}" ]]; then
    echo "SKIP_ECR_LOGIN=1: skipping aws ecr get-login-password | docker login (use when already authenticated to ${ECR_REGISTRY})."
  else
    echo "Logging in to ${ECR_REGISTRY}..."
    aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$ECR_REGISTRY"
  fi

  echo "Building ${IMAGE} (linux/amd64)..."
  # Default: ``buildx build --push`` — streams the image to ECR without ``--load``. On Docker Desktop the
  # ``docker-container`` buildx driver often returns ``EOF`` when combined with ``--load`` (nothing to export locally).
  if [[ -n "${USE_LEGACY_BUILD:-}" ]]; then
    echo "USE_LEGACY_BUILD=1: legacy builder (no BuildKit), then docker push."
    if [[ -n "${NO_CACHE:-}" ]]; then
      DOCKER_BUILDKIT=0 docker build --platform linux/amd64 --progress=plain --no-cache -t "$IMAGE" .
    else
      DOCKER_BUILDKIT=0 docker build --platform linux/amd64 --progress=plain -t "$IMAGE" .
    fi
    echo "Pushing ${IMAGE}..."
    docker push "$IMAGE"
  elif [[ -n "${BUILDX_LOAD:-}" ]]; then
    echo "BUILDX_LOAD=1: buildx --load then docker push (may fail with EOF on some setups)."
    if [[ -n "${NO_CACHE:-}" ]]; then
      docker buildx build --no-cache --progress=plain --platform linux/amd64 -t "$IMAGE" --load .
    else
      docker buildx build --progress=plain --platform linux/amd64 -t "$IMAGE" --load .
    fi
    docker push "$IMAGE"
  elif [[ -n "${NO_CACHE:-}" ]]; then
    docker buildx build --no-cache --progress=plain --platform linux/amd64 -t "$IMAGE" --push .
  else
    docker buildx build --progress=plain --platform linux/amd64 -t "$IMAGE" --push .
  fi
fi

echo "Restarting deployment/${DEPLOY} in namespace ${NS}..."
"${KUBECTL[@]}" rollout restart "deployment/${DEPLOY}" -n "$NS"
"${KUBECTL[@]}" rollout status "deployment/${DEPLOY}" -n "$NS" --timeout=180s

if [[ -z "${SKIP_VERIFY:-}" ]]; then
  echo "Running post-rollout verification..."
  K8S_NAMESPACE="$NS" K8S_DEPLOYMENT="$DEPLOY" KUBECTL_CONTEXT="${KUBECTL_CONTEXT:-}" bash "${ROOT}/scripts/verify-rollout.sh"
else
  echo "SKIP_VERIFY=1: skipping scripts/verify-rollout.sh"
fi

echo "Done: ${IMAGE} rolled out and verified in ${NS}."
