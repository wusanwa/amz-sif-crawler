#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LEGACY_DATA_DIR="${1:-$PROJECT_ROOT/data}"
RUNTIME_ROOT="${APP_RUNTIME_ROOT:-$PROJECT_ROOT/runtime_data}"
PROFILE_ROOT="${PROFILE_ROOT_DIR:-$RUNTIME_ROOT/profiles}"
CACHE_ROOT="${CACHE_ROOT_DIR:-$RUNTIME_ROOT/cache_db}"
CURRENT_UID="$(id -u)"
CURRENT_GID="$(id -g)"

SUDO=""
if sudo -n true 2>/dev/null; then
  SUDO="sudo -n"
fi

mkdir -p "$PROFILE_ROOT/amazon" "$PROFILE_ROOT/sif" "$CACHE_ROOT/amazon" "$CACHE_ROOT/sif"

copy_merge() {
  local src="$1"
  local dst="$2"
  local label="$3"

  if [[ ! -d "$src" ]]; then
    echo "[skip] $label: source not found ($src)"
    return 0
  fi

  if [[ -z "$(ls -A "$src" 2>/dev/null || true)" ]]; then
    echo "[skip] $label: source empty ($src)"
    return 0
  fi

  mkdir -p "$dst"
  local rc=0
  $SUDO rsync -a --ignore-errors --chown="${CURRENT_UID}:${CURRENT_GID}" \
    --exclude='SingletonLock' \
    --exclude='SingletonCookie' \
    --exclude='SingletonSocket' \
    --exclude='*.lock' \
    "$src"/ "$dst"/ || rc=$?
  if [[ "$rc" -ne 0 && "$rc" -ne 23 && "$rc" -ne 24 ]]; then
    echo "[err] $label: rsync failed with code $rc"
    return "$rc"
  fi
  echo "[ok] $label: $src -> $dst"
}

# 1) 迁移 profile（非 cache 的核心内容）
copy_merge "$LEGACY_DATA_DIR/amazon/profiles/amazon" "$PROFILE_ROOT/amazon" "amazon profile"
copy_merge "$LEGACY_DATA_DIR/sif/profiles/amazon" "$PROFILE_ROOT/amazon" "amazon profile (fallback)"
copy_merge "$LEGACY_DATA_DIR/sif/profiles/sif" "$PROFILE_ROOT/sif" "sif profile"
copy_merge "$LEGACY_DATA_DIR/amazon/profiles/sif" "$PROFILE_ROOT/sif" "sif profile (fallback)"

# 2) 迁移 cache（单独处理）
copy_merge "$LEGACY_DATA_DIR/amazon/cache_db" "$CACHE_ROOT/amazon" "amazon cache"
copy_merge "$LEGACY_DATA_DIR/sif/cache_db" "$CACHE_ROOT/sif" "sif cache"

# 3) 打包 profile，便于提交到 git
if [[ -x "$PROJECT_ROOT/scripts/profile_bundle.sh" ]]; then
  bash "$PROJECT_ROOT/scripts/profile_bundle.sh" pack all
fi

# 4) 一次性迁移后，清理 data 下非 cache 目录（保留 cache）
for p in \
  "$LEGACY_DATA_DIR/amazon/profiles" \
  "$LEGACY_DATA_DIR/sif/profiles"; do
  if [[ -d "$p" ]]; then
    $SUDO rm -rf "$p" || true
    echo "[cleanup] removed legacy profile dir: $p"
  fi
done

echo "Done. Legacy data has been copied into runtime_data and profile_bundles."
