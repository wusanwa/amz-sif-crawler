#!/usr/bin/env bash
set -euo pipefail

NODE_TYPE="${NODE_TYPE:-both}"
PORT="${PORT:-8000}"
SEED_ROOT="${SEED_PROFILE_ROOT:-/app/profiles}"
ARCHIVE_ROOT="${PROFILE_ARCHIVE_ROOT:-/app/profile_bundles}"
RUNTIME_ROOT="${RUNTIME_PROFILE_ROOT:-/tmp/runtime-profiles}"
INSTANCE_ID="${HOSTNAME:-worker}-$(date +%s)-$$"
INSTANCE_ROOT="${RUNTIME_ROOT}/${INSTANCE_ID}"

AMZ_SEED="${SEED_ROOT}/amazon"
SIF_SEED="${SEED_ROOT}/sif"
AMZ_ARCHIVE="${ARCHIVE_ROOT}/amazon.tar.gz"
SIF_ARCHIVE="${ARCHIVE_ROOT}/sif.tar.gz"
AMZ_RUNTIME="${INSTANCE_ROOT}/amazon"
SIF_RUNTIME="${INSTANCE_ROOT}/sif"

copy_or_unpack_profile() {
  local name="$1"
  local src="$2"
  local archive="$3"
  local dst="$4"
  mkdir -p "$dst"

  if [[ -d "$src" ]] && [[ -n "$(ls -A "$src" 2>/dev/null || true)" ]]; then
    cp -a "$src"/. "$dst"/
    echo "[entrypoint] copied profile seed: $src -> $dst"
    return 0
  fi

  if [[ -f "$archive" ]]; then
    local unpack_root unpack_dir
    unpack_root="${INSTANCE_ROOT}/archive-unpack-${name}"
    unpack_dir="${unpack_root}/${name}"
    rm -rf "$unpack_root"
    mkdir -p "$unpack_root"
    tar -xzf "$archive" -C "$unpack_root"

    if [[ -d "$unpack_dir" ]] && [[ -n "$(ls -A "$unpack_dir" 2>/dev/null || true)" ]]; then
      cp -a "$unpack_dir"/. "$dst"/
      echo "[entrypoint] unpacked profile bundle: $archive -> $dst"
      return 0
    fi

    echo "[entrypoint] archive extracted but expected dir missing/empty: $unpack_dir"
  fi

  echo "[entrypoint] seed and archive both missing/empty (${src}, ${archive}); using empty runtime profile: $dst"
}

mkdir -p "$INSTANCE_ROOT"

case "$NODE_TYPE" in
  amazon)
    copy_or_unpack_profile "amazon" "$AMZ_SEED" "$AMZ_ARCHIVE" "$AMZ_RUNTIME"
    export AMAZON_PROFILE_DIR="$AMZ_RUNTIME"
    ;;
  sif)
    copy_or_unpack_profile "sif" "$SIF_SEED" "$SIF_ARCHIVE" "$SIF_RUNTIME"
    export SIF_PROFILE_DIR="$SIF_RUNTIME"
    ;;
  *)
    copy_or_unpack_profile "amazon" "$AMZ_SEED" "$AMZ_ARCHIVE" "$AMZ_RUNTIME"
    copy_or_unpack_profile "sif" "$SIF_SEED" "$SIF_ARCHIVE" "$SIF_RUNTIME"
    export AMAZON_PROFILE_DIR="$AMZ_RUNTIME"
    export SIF_PROFILE_DIR="$SIF_RUNTIME"
    ;;
esac

echo "[entrypoint] NODE_TYPE=${NODE_TYPE} AMAZON_PROFILE_DIR=${AMAZON_PROFILE_DIR:-N/A} SIF_PROFILE_DIR=${SIF_PROFILE_DIR:-N/A}"
exec python3 mcp_server.py --mode sse --port "$PORT"
