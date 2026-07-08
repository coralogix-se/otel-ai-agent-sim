#!/usr/bin/env bash
# Create SigmaDev EKS cluster (3 x t3.large nodes, ~30 pod capacity).
#
# Usage:
#   bash scripts/create-sigmadev-cluster.sh
#   AWS_REGION=eu-west-1 bash scripts/create-sigmadev-cluster.sh   # if you relocate the cluster
#
# Does NOT deploy the sim — run scripts/deploy-sigmadev.sh after you have a Coralogix API key.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLUSTER_NAME="${EKS_CLUSTER_NAME:-SigmaDev}"
REGION="${AWS_REGION:-us-west-2}"
CONFIG="${ROOT}/infra/eks/sigmadev-cluster.yaml"

if ! command -v eksctl >/dev/null 2>&1; then
  echo "FAIL: eksctl not found. Install: https://eksctl.io/installation/" >&2
  exit 1
fi

if aws eks describe-cluster --name "$CLUSTER_NAME" --region "$REGION" >/dev/null 2>&1; then
  echo "Cluster ${CLUSTER_NAME} already exists in ${REGION}."
else
  echo "Creating EKS cluster ${CLUSTER_NAME} in ${REGION} (typically 15–20 minutes)..."
  eksctl create cluster -f "$CONFIG"
fi

echo "Updating kubeconfig for ${CLUSTER_NAME}..."
aws eks update-kubeconfig --name "$CLUSTER_NAME" --region "$REGION" --alias "$CLUSTER_NAME"

echo "Ensuring upgrade policy is STANDARD (not EXTENDED)..."
current_policy="$(aws eks describe-cluster --name "$CLUSTER_NAME" --region "$REGION" \
  --query 'cluster.upgradePolicy.supportType' --output text)"
if [[ "$current_policy" != "STANDARD" ]]; then
  aws eks update-cluster-config --name "$CLUSTER_NAME" --region "$REGION" \
    --upgrade-policy supportType=STANDARD
  echo "Upgrade policy changed: ${current_policy} -> STANDARD"
else
  echo "Upgrade policy already STANDARD"
fi

echo "Waiting for 3 nodes Ready..."
kubectl --context "$CLUSTER_NAME" wait --for=condition=Ready nodes --all --timeout=600s

echo ""
echo "SigmaDev cluster ready."
kubectl --context "$CLUSTER_NAME" get nodes -o wide
echo ""
echo "Context: kubectl config use-context ${CLUSTER_NAME}"
echo "Next: obtain Coralogix Send-Your-Data key, then:"
echo "  CORALOGIX_PRIVATE_KEY='...' bash scripts/deploy-sigmadev.sh"
