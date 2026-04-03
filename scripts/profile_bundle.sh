#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_ROOT="${APP_RUNTIME_ROOT:-$PROJECT_ROOT/runtime_data}"
PROFILE_ROOT="${PROFILE_ROOT_DIR:-$RUNTIME_ROOT/profiles}"
ARCHIVE_ROOT="${PROFILE_ARCHIVE_ROOT:-$PROJECT_ROOT/profile_bundles}"

AMZ_PROFILE_DIR="$PROFILE_ROOT/amazon"
SIF_PROFILE_DIR="$PROFILE_ROOT/sif"
AMZ_ARCHIVE="$ARCHIVE_ROOT/amazon.tar.gz"
SIF_ARCHIVE="$ARCHIVE_ROOT/sif.tar.gz"

usage() {
  cat <<USAGE
Usage:
  scripts/profile_bundle.sh pack [amazon|sif|all]
  scripts/profile_bundle.sh unpack [amazon|sif|all] [--force]
  scripts/profile_bundle.sh status

Env override:
  APP_RUNTIME_ROOT      default: $PROJECT_ROOT/runtime_data
  PROFILE_ROOT_DIR      default: <APP_RUNTIME_ROOT>/profiles
  PROFILE_ARCHIVE_ROOT  default: $PROJECT_ROOT/profile_bundles
USAGE
}

clean_profile_locks() {
  local dir="$1"
  [ -d "$dir" ] || return 0
  rm -f "$dir"/SingletonLock "$dir"/SingletonCookie "$dir"/SingletonSocket 2>/dev/null || true
  rm -f "$dir"/*.lock "$dir"/Singleton* 2>/dev/null || true
}

pack_one() {
  local name="$1"
  local src="$2"
  local archive="$3"

  if [ ! -d "$src" ] || [ -z "$(ls -A "$src" 2>/dev/null || true)" ]; then
    echo "[pack] skip $name: source profile missing/empty ($src)"
    return 0
  fi

  mkdir -p "$ARCHIVE_ROOT"
  clean_profile_locks "$src"

  local parent base tmp
  parent="$(dirname "$src")"
  base="$(basename "$src")"
  tmp="${archive}.tmp"

  tar -C "$parent" -czf "$tmp" \
    --exclude='SingletonLock' \
    --exclude='SingletonCookie' \
    --exclude='SingletonSocket' \
    --exclude='*.lock' \
    "$base"
  mv "$tmp" "$archive"
  echo "[pack] $name => $archive"
}

unpack_one() {
  local name="$1"
  local archive="$2"
  local dst="$3"
  local force="$4"

  if [ ! -f "$archive" ]; then
    echo "[unpack] skip $name: archive not found ($archive)"
    return 0
  fi

  if [ "$force" != "1" ] && [ -d "$dst" ] && [ -n "$(ls -A "$dst" 2>/dev/null || true)" ]; then
    echo "[unpack] skip $name: profile already exists ($dst)"
    return 0
  fi

  mkdir -p "$dst"
  rm -rf "$dst"/*

  local parent
  parent="$(dirname "$dst")"
  mkdir -p "$parent"
  tar -xzf "$archive" -C "$parent"
  echo "[unpack] $name <= $archive"
}

cmd_status() {
  mkdir -p "$PROFILE_ROOT" "$ARCHIVE_ROOT"
  echo "PROJECT_ROOT=$PROJECT_ROOT"
  echo "PROFILE_ROOT=$PROFILE_ROOT"
  echo "ARCHIVE_ROOT=$ARCHIVE_ROOT"
  echo "- amazon profile: $AMZ_PROFILE_DIR"
  echo "- sif profile   : $SIF_PROFILE_DIR"
  echo "- amazon archive: $AMZ_ARCHIVE"
  echo "- sif archive   : $SIF_ARCHIVE"
  [ -f "$AMZ_ARCHIVE" ] && ls -lh "$AMZ_ARCHIVE" || true
  [ -f "$SIF_ARCHIVE" ] && ls -lh "$SIF_ARCHIVE" || true
}

cmd_pack() {
  local target="${1:-all}"
  case "$target" in
    amazon) pack_one amazon "$AMZ_PROFILE_DIR" "$AMZ_ARCHIVE" ;;
    sif) pack_one sif "$SIF_PROFILE_DIR" "$SIF_ARCHIVE" ;;
    all)
      pack_one amazon "$AMZ_PROFILE_DIR" "$AMZ_ARCHIVE"
      pack_one sif "$SIF_PROFILE_DIR" "$SIF_ARCHIVE"
      ;;
    *) usage; exit 1 ;;
  esac
}

cmd_unpack() {
  local target="${1:-all}"
  shift || true
  local force=0
  for arg in "$@"; do
    case "$arg" in
      --force) force=1 ;;
      *) usage; exit 1 ;;
    esac
  done

  case "$target" in
    amazon) unpack_one amazon "$AMZ_ARCHIVE" "$AMZ_PROFILE_DIR" "$force" ;;
    sif) unpack_one sif "$SIF_ARCHIVE" "$SIF_PROFILE_DIR" "$force" ;;
    all)
      unpack_one amazon "$AMZ_ARCHIVE" "$AMZ_PROFILE_DIR" "$force"
      unpack_one sif "$SIF_ARCHIVE" "$SIF_PROFILE_DIR" "$force"
      ;;
    *) usage; exit 1 ;;
  esac
}

main() {
  local cmd="${1:-}"
  case "$cmd" in
    pack)
      shift
      cmd_pack "$@"
      ;;
    unpack)
      shift
      cmd_unpack "$@"
      ;;
    status)
      cmd_status
      ;;
    *)
      usage
      exit 1
      ;;
  esac
}

main "$@"
