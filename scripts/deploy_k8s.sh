#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

NAMESPACE="amz-sif-crawler"
TAG="latest"
REGISTRY=""
WITH_PVC="true"
TIMEOUT="240s"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/deploy_k8s.sh [--namespace NAMESPACE] [--tag TAG] [--registry REGISTRY] [--no-pvc] [--timeout 240s]

Options:
  --namespace NAMESPACE   Kubernetes namespace, default: amz-sif-crawler
  --tag TAG               Image tag, default: latest
  --registry REGISTRY     Registry prefix, e.g. ghcr.io/your-org
  --no-pvc                Skip applying k8s/pvc-example.yaml
  --timeout DURATION      Rollout timeout, default: 240s
  -h, --help              Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --namespace)
      NAMESPACE="$2"
      shift 2
      ;;
    --tag)
      TAG="$2"
      shift 2
      ;;
    --registry)
      REGISTRY="$2"
      shift 2
      ;;
    --no-pvc)
      WITH_PVC="false"
      shift
      ;;
    --timeout)
      TIMEOUT="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[ERROR] Unknown argument: $1"
      usage
      exit 1
      ;;
  esac
done

if ! command -v kubectl >/dev/null 2>&1; then
  echo "[ERROR] kubectl not found."
  exit 1
fi

REGISTRY="${REGISTRY%/}"
PREFIX=""
if [[ -n "${REGISTRY}" ]]; then
  PREFIX="${REGISTRY}/"
fi

AMAZON_IMAGE="${PREFIX}amz-sif-crawler-amazon-worker:${TAG}"
SIF_IMAGE="${PREFIX}amz-sif-crawler-sif-worker:${TAG}"
GATEWAY_IMAGE="${PREFIX}amz-sif-crawler-mcp-gateway:${TAG}"

cd "${ROOT_DIR}"

echo "[INFO] Applying namespace/config..."
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmap.yaml

if [[ "${WITH_PVC}" == "true" ]]; then
  echo "[INFO] Applying PVC manifests..."
  kubectl apply -f k8s/pvc-example.yaml
fi

echo "[INFO] Applying deployments/services..."
kubectl apply -f k8s/amazon-worker-deployment.yaml
kubectl apply -f k8s/sif-worker-deployment.yaml
kubectl apply -f k8s/gateway-deployment.yaml
kubectl apply -f k8s/services.yaml

echo "[INFO] Updating deployment images..."
kubectl -n "${NAMESPACE}" set image deployment/amazon-worker amazon-worker="${AMAZON_IMAGE}"
kubectl -n "${NAMESPACE}" set image deployment/sif-worker sif-worker="${SIF_IMAGE}"
kubectl -n "${NAMESPACE}" set image deployment/mcp-gateway mcp-gateway="${GATEWAY_IMAGE}"

echo "[INFO] Waiting for rollouts..."
kubectl -n "${NAMESPACE}" rollout status deploy/amazon-worker --timeout="${TIMEOUT}"
kubectl -n "${NAMESPACE}" rollout status deploy/sif-worker --timeout="${TIMEOUT}"
kubectl -n "${NAMESPACE}" rollout status deploy/mcp-gateway --timeout="${TIMEOUT}"

echo "[INFO] Current resources:"
kubectl -n "${NAMESPACE}" get pods -o wide
kubectl -n "${NAMESPACE}" get svc

cat <<EOF
[DONE] Kubernetes deploy completed.
  Namespace: ${NAMESPACE}
  AMAZON_IMAGE=${AMAZON_IMAGE}
  SIF_IMAGE=${SIF_IMAGE}
  GATEWAY_IMAGE=${GATEWAY_IMAGE}
EOF
