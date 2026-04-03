#!/usr/bin/env bash
set -euo pipefail

NODE_TYPE="${NODE_TYPE:-both}"
PORT="${PORT:-8000}"
SEED_ROOT="${SEED_PROFILE_ROOT:-/app/profiles}"
RUNTIME_ROOT="${RUNTIME_PROFILE_ROOT:-/tmp/runtime-profiles}"
INSTANCE_ID="${HOSTNAME:-worker}-$(date +%s)-$$"
INSTANCE_ROOT="${RUNTIME_ROOT}/${INSTANCE_ID}"

AMZ_SEED="${SEED_ROOT}/amazon"
SIF_SEED="${SEED_ROOT}/sif"
AMZ_RUNTIME="${INSTANCE_ROOT}/amazon"
SIF_RUNTIME="${INSTANCE_ROOT}/sif"

copy_profile() {
  local src="$1"
  local dst="$2"
  mkdir -p "$dst"
  if [[ -d "$src" ]] && [[ -n "$(ls -A "$src" 2>/dev/null || true)" ]]; then
    cp -a "$src"/. "$dst"/
    echo "[entrypoint] copied profile seed: $src -> $dst"
  else
    echo "[entrypoint] seed profile missing/empty: $src ; using empty runtime profile: $dst"
  fi
}

mkdir -p "$INSTANCE_ROOT"

case "$NODE_TYPE" in
  amazon)
    copy_profile "$AMZ_SEED" "$AMZ_RUNTIME"
    export AMAZON_PROFILE_DIR="$AMZ_RUNTIME"
    ;;
  sif)
    copy_profile "$SIF_SEED" "$SIF_RUNTIME"
    export SIF_PROFILE_DIR="$SIF_RUNTIME"
    ;;
  *)
    copy_profile "$AMZ_SEED" "$AMZ_RUNTIME"
    copy_profile "$SIF_SEED" "$SIF_RUNTIME"
    export AMAZON_PROFILE_DIR="$AMZ_RUNTIME"
    export SIF_PROFILE_DIR="$SIF_RUNTIME"
    ;;
esac

echo "[entrypoint] NODE_TYPE=${NODE_TYPE} AMAZON_PROFILE_DIR=${AMAZON_PROFILE_DIR:-N/A} SIF_PROFILE_DIR=${SIF_PROFILE_DIR:-N/A}"
exec python3 mcp_server.py --mode sse --port "$PORT"

