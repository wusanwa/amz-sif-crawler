#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ $# -lt 1 ]]; then
  echo "Usage: bash scripts/crawl.sh [--amazon-only|--sif-only] <amazon-url-or-asin> [more...]" >&2
  exit 1
fi

export PYTHONPATH="$ROOT_DIR/src"
MODE_ARGS=()
if [[ "${1:-}" == "--amazon-only" ]]; then
  MODE_ARGS=(--mode amazon)
  shift
elif [[ "${1:-}" == "--sif-only" ]]; then
  MODE_ARGS=(--mode sif)
  shift
fi

if [[ $# -lt 1 ]]; then
  echo "Usage: bash scripts/crawl.sh [--amazon-only|--sif-only] <amazon-url-or-asin> [more...]" >&2
  exit 1
fi

"$PYTHON_BIN" "$ROOT_DIR/crawl_once.py" "${MODE_ARGS[@]}" "$@"
