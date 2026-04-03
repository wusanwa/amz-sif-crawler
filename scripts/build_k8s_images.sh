#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

TAG="latest"
REGISTRY=""
PUSH="false"
BASE_IMAGE_NAME="amz-sif-crawler-build"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/build_k8s_images.sh [--tag TAG] [--registry REGISTRY] [--push]

Options:
  --tag TAG             Image tag, default: latest
  --registry REGISTRY   Registry prefix, e.g. ghcr.io/your-org
  --push                Push images after build/tag
  -h, --help            Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag)
      TAG="$2"
      shift 2
      ;;
    --registry)
      REGISTRY="$2"
      shift 2
      ;;
    --push)
      PUSH="true"
      shift
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

if ! command -v docker >/dev/null 2>&1; then
  echo "[ERROR] docker not found."
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
BASE_IMAGE="${BASE_IMAGE_NAME}:${TAG}"

echo "[INFO] Building base image: ${BASE_IMAGE}"
docker build -t "${BASE_IMAGE}" "${ROOT_DIR}"

echo "[INFO] Tagging worker/gateway images..."
docker tag "${BASE_IMAGE}" "${AMAZON_IMAGE}"
docker tag "${BASE_IMAGE}" "${SIF_IMAGE}"
docker tag "${BASE_IMAGE}" "${GATEWAY_IMAGE}"

if [[ "${PUSH}" == "true" ]]; then
  echo "[INFO] Pushing images..."
  docker push "${AMAZON_IMAGE}"
  docker push "${SIF_IMAGE}"
  docker push "${GATEWAY_IMAGE}"
fi

cat <<EOF
[DONE] Image build completed.
  AMAZON_IMAGE=${AMAZON_IMAGE}
  SIF_IMAGE=${SIF_IMAGE}
  GATEWAY_IMAGE=${GATEWAY_IMAGE}

Next:
  ./scripts/deploy_k8s.sh --tag ${TAG} ${REGISTRY:+--registry ${REGISTRY}}
EOF
